"""Automated recovery-plan generation from observed state (no access to injected-fault ground truth)."""

from __future__ import annotations

from statistics import mean
from typing import Any

from app.engine.snapshot import Snapshot

# Typical step durations used when no history exists (seconds)
DEFAULT_STEP_S = {
    "detect": 3,
    "assess": 1,
    "validate": 1,
    "approve": 60,
    "promote": 1,
    "switch": 1,
    "verify": 1,
    "resync": 2,
    "restart": 5,
}


def _step(order: int, title: str, action: str, owner: str, est_s: float, automated: bool = True) -> dict[str, Any]:
    return {"order": order, "title": title, "action": action, "owner": owner, "est_s": est_s, "automated": automated}


def _preconditions(snap: Snapshot, target: str) -> list[dict[str, Any]]:
    t = snap.regions[target]
    lag = snap.rpo_exposure_s
    return [
        {
            "check": f"{t.display_name} healthy",
            "ok": t.status == "up",
            "detail": t.status if t.status != "up" else "all probes passing",
        },
        {"check": f"{t.display_name} database is replica", "ok": t.db_role == "replica", "detail": str(t.db_role)},
        {
            "check": "RPO exposure within target",
            "ok": lag is not None and lag <= snap.policy.rpo_target_s,
            "detail": "unknown" if lag is None else f"{lag:.1f}s vs {snap.policy.rpo_target_s}s",
        },
        {
            "check": "No recovery run in progress",
            "ok": snap.active_run is None,
            "detail": "none" if snap.active_run is None else f"run #{snap.active_run['id']}",
        },
    ]


def generate(snap: Snapshot) -> dict[str, Any]:
    a, sb, p = snap.active, snap.standby, snap.policy
    hist = mean(snap.measured_rto_s[-5:]) if snap.measured_rto_s else None
    approve_s = 0 if not p.require_approval else DEFAULT_STEP_S["approve"]
    risks: list[str] = []

    if a.status == "down" and a.failing_components == ["app"]:
        steps = [
            _step(
                1,
                f"Restart application tier in {a.display_name}",
                "restart_service",
                "system",
                DEFAULT_STEP_S["restart"],
            ),
            _step(2, "Confirm region recovers (2 healthy probes)", "observe", "system", 2),
            _step(
                3,
                f"If not recovered, fail over to {sb.display_name}",
                "failover",
                "operator",
                sum(DEFAULT_STEP_S[k] for k in ("validate", "promote", "switch", "verify")) + approve_s,
                False,
            ),
        ]
        return _plan(
            snap,
            "service_failure",
            "remediate",
            f"Only the application tier in {a.display_name} is failing; database and network are healthy. "
            "Self-heal by restarting the service before considering a regional failover (avoids data loss).",
            steps,
            est_rto=DEFAULT_STEP_S["detect"] + DEFAULT_STEP_S["restart"] + 2,
            est_rpo=0.0,
            risks=["Restart loop if the fault is in the release itself - roll back the deployment"],
            target=sb.name,
        )

    if a.status in ("down", "degraded") and sb.status == "up" and sb.db_role == "replica":
        kind = (
            "region_outage"
            if len(a.failing_components) >= 2
            else f"{a.failing_components[0]}_failure"
            if a.failing_components
            else "degraded"
        )
        est_exec = sum(DEFAULT_STEP_S[k] for k in ("validate", "promote", "switch", "verify"))
        est_rto = hist or (DEFAULT_STEP_S["detect"] + est_exec + approve_s)
        exposure = snap.rpo_exposure_s
        if exposure is not None and exposure > p.rpo_target_s:
            risks.append(f"Expected data loss {exposure:.1f}s exceeds RPO target {p.rpo_target_s}s")
        if a.status == "degraded":
            risks.append("Primary only degraded - failure not yet confirmed; consider waiting for detection")
        risks.append(f"Unreplicated writes on {a.display_name} will be discarded when it is resynced")
        steps = [
            _step(
                1,
                f"Confirm {a.display_name} failure ({p.rto_target_s}s RTO budget starts at first failed probe)",
                "detect",
                "system",
                DEFAULT_STEP_S["detect"],
            ),
            _step(2, "Check RTO/RPO budget against policy", "assess", "system", DEFAULT_STEP_S["assess"]),
            _step(
                3,
                f"Validate {sb.display_name}: health, replica role, lag",
                "validate",
                "system",
                DEFAULT_STEP_S["validate"],
            ),
            _step(
                4,
                "Approval gate (two-person rule)" if p.two_person_rule else "Approval gate",
                "approve",
                "approver",
                approve_s,
                automated=not p.require_approval,
            ),
            _step(
                5,
                f"Fence {a.display_name} and promote {sb.display_name} database",
                "promote",
                "system",
                DEFAULT_STEP_S["promote"],
            ),
            _step(6, f"Switch global traffic to {sb.display_name}", "switch", "system", DEFAULT_STEP_S["switch"]),
            _step(
                7, "Verify with synthetic transactions; record RTO/RPO", "verify", "system", DEFAULT_STEP_S["verify"]
            ),
        ]
        return _plan(
            snap,
            kind,
            "failover",
            f"{a.display_name} is {a.status} ({', '.join(a.failing_components) or 'unknown'} failing) and "
            f"{sb.display_name} is a healthy replica. Regional failover restores service.",
            steps,
            est_rto=est_rto,
            est_rpo=exposure,
            risks=risks,
            target=sb.name,
        )

    if a.status == "down" and sb.status != "up":
        lb_age = snap.latest_verified_backup_age_s
        return _plan(
            snap,
            "dual_region_failure",
            "restore_from_backup",
            "Both regions are impaired. Recover the first region to return and restore from the newest "
            "verified backup.",
            [
                _step(
                    1,
                    "Declare major incident; page cloud provider support",
                    "escalate",
                    "incident commander",
                    300,
                    False,
                ),
                _step(
                    2, "Restore newest verified backup into first recovered region", "restore", "operator", 900, False
                ),
                _step(3, "Point traffic at restored region and verify", "switch", "operator", 60, False),
            ],
            est_rto=1260,
            est_rpo=lb_age,
            risks=["RPO equals backup age" if lb_age is not None else "No verified backup exists!"],
            target=None,
        )

    if snap.failed_over and snap.regions[snap.designated_primary].status == "up":
        prim = snap.regions[snap.designated_primary]
        steps = [
            _step(1, f"Confirm {prim.display_name} recovered", "precheck", "system", 1),
            _step(
                2,
                f"Resync {prim.display_name} from {a.display_name} (discard divergent writes)",
                "resync",
                "system",
                DEFAULT_STEP_S["resync"],
            ),
            _step(3, "Wait until replication lag within budget", "validate", "system", 3),
            _step(4, "Approval gate", "approve", "approver", approve_s, automated=not p.require_approval),
            _step(
                5,
                f"Drain writes, promote {prim.display_name}, demote {a.display_name} to replica",
                "promote",
                "system",
                2,
            ),
            _step(6, f"Switch traffic back to {prim.display_name}", "switch", "system", 1),
            _step(7, "Verify recovery", "verify", "system", 1),
        ]
        return _plan(
            snap,
            "failback_ready",
            "failback",
            f"Running on {a.display_name} after failover; {prim.display_name} is healthy again. "
            "Planned failback is a graceful switchover with zero data loss.",
            steps,
            est_rto=sum(s["est_s"] for s in steps),
            est_rpo=0.0,
            risks=["Schedule during low traffic; writes pause for the drain window"],
            target=prim.name,
        )

    if sb.status != "up" or sb.db_role != "replica":
        fix = "resync" if sb.status == "up" else "repair"
        return _plan(
            snap,
            "standby_impaired",
            "repair_standby",
            f"Production is healthy on {a.display_name} but the standby {sb.display_name} cannot take over "
            f"({sb.status}, db {sb.db_role}). DR protection is lost until it is repaired.",
            [
                _step(
                    1,
                    f"Investigate {sb.display_name} ({', '.join(sb.failing_components) or sb.db_role})",
                    "investigate",
                    "operator",
                    300,
                    False,
                ),
                _step(2, f"{fix.title()} standby database from {a.display_name}", fix, "operator", 120, False),
                _step(3, "Verify replication lag back within budget", "observe", "system", 30),
            ],
            est_rto=None,
            est_rpo=None,
            risks=["A failure of the active region now would cause an extended outage"],
            target=sb.name,
        )

    if snap.rpo_exposure_s is not None and snap.rpo_exposure_s > p.max_replication_lag_s:
        return _plan(
            snap,
            "replication_degraded",
            "repair_replication",
            f"Replication to {sb.display_name} is {snap.rpo_exposure_s:.1f}s behind "
            f"(budget {p.max_replication_lag_s}s). Failing over now would lose that much data.",
            [
                _step(1, "Check replication link / network path between clouds", "investigate", "operator", 120, False),
                _step(2, "Restore replication; monitor lag until within budget", "observe", "system", 60),
            ],
            est_rto=None,
            est_rpo=snap.rpo_exposure_s,
            risks=["RPO target at risk while lag persists"],
            target=sb.name,
        )

    return _plan(
        snap,
        "steady_state",
        "none",
        f"{a.display_name} is serving traffic and {sb.display_name} is a healthy in-sync replica. "
        "No recovery action required.",
        [
            _step(1, "Keep verified backups fresh", "backup", "system", 0),
            _step(2, "Run a game day at least monthly", "gameday", "operator", 0, False),
        ],
        est_rto=hist,
        est_rpo=snap.rpo_exposure_s,
        risks=[],
        target=sb.name,
    )


def _plan(
    snap: Snapshot,
    scenario: str,
    recommendation: str,
    rationale: str,
    steps: list[dict[str, Any]],
    est_rto: float | None,
    est_rpo: float | None,
    risks: list[str],
    target: str | None,
) -> dict[str, Any]:
    p = snap.policy
    return {
        "scenario": scenario,
        "recommendation": recommendation,
        "rationale": rationale,
        "source_region": snap.active_region,
        "target_region": target,
        "steps": steps,
        "estimated_rto_s": None if est_rto is None else round(est_rto, 1),
        "estimated_rpo_s": None if est_rpo is None else round(est_rpo, 1),
        "rto_target_s": p.rto_target_s,
        "rpo_target_s": p.rpo_target_s,
        "within_rto": None if est_rto is None else est_rto <= p.rto_target_s,
        "within_rpo": None if est_rpo is None else est_rpo <= p.rpo_target_s,
        "preconditions": _preconditions(snap, target) if target and recommendation in ("failover", "failback") else [],
        "risks": risks,
        "generated_at": snap.now.isoformat(),
    }
