"""Controlled failover / failback state machine.

Failover: detect -> assess (RTO/RPO) -> validate secondary -> approve -> promote -> switch traffic -> verify
Failback: precheck -> resync -> validate replication -> approve -> drain+role swap -> switch traffic -> verify

Each step handler returns ("ok" | "wait" | "fail", output). `advance()` runs as many steps as it can and
parks the run on the first step that has to wait (approval, replication catch-up, verification retry).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.clock import aware
from app.engine import report as reporting
from app.models import (
    DrPolicy,
    Incident,
    RecoveryRun,
    Region,
    RegionStatus,
    Role,
    RunKind,
    RunStatus,
    RunStep,
    StepStatus,
)
from app.observability import LAST_RPO, LAST_RTO, RUNS, tracer
from app.providers.base import ProviderError

if TYPE_CHECKING:
    from app.engine.runtime import Runtime

log = logging.getLogger(__name__)

STEPS: dict[RunKind, list[tuple[str, str]]] = {
    RunKind.FAILOVER: [
        ("detect", "Detect & confirm failure"),
        ("assess", "Check RTO/RPO budget"),
        ("validate", "Validate secondary region"),
        ("approve", "Approval gate"),
        ("promote", "Fence primary & promote secondary"),
        ("switch", "Switch global traffic"),
        ("verify", "Verify recovery"),
    ],
    RunKind.FAILBACK: [
        ("precheck", "Confirm primary recovered"),
        ("resync", "Resync recovered primary"),
        ("validate", "Validate replication caught up"),
        ("approve", "Approval gate"),
        ("promote", "Drain writes & swap roles"),
        ("switch", "Switch global traffic"),
        ("verify", "Verify recovery"),
    ],
}
ACTIVE = (RunStatus.PENDING, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
MAX_ATTEMPTS = {"detect": 60, "validate": 120, "verify": 10, "precheck": 60}
APPROVER_ROLES = {Role.APPROVER, Role.ADMIN}


class RunError(Exception):
    def __init__(self, msg: str, status: int = 409) -> None:
        super().__init__(msg)
        self.status = status


class Orchestrator:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    # ----------------------------------------------------------------- lifecycle
    def active_run(self, s: Session) -> RecoveryRun | None:
        return s.scalar(select(RecoveryRun).where(RecoveryRun.status.in_(ACTIVE)).order_by(RecoveryRun.id.desc()))

    def create(
        self,
        s: Session,
        kind: RunKind,
        reason: str,
        initiated_by: str,
        incident_id: int | None = None,
        game_day_id: int | None = None,
        ip: str | None = None,
    ) -> RecoveryRun:
        if (cur := self.active_run(s)) is not None:
            raise RunError(f"{cur.kind.value} run #{cur.id} is already in progress")
        active = self.rt.traffic.active_region()
        if kind == RunKind.FAILOVER:
            source, target = active, next(n for n in self.rt.regions if n != active)
        else:
            if active == self.rt.settings.primary_region:
                raise RunError("traffic already on the designated primary; nothing to fail back")
            source, target = active, self.rt.settings.primary_region
        now = self.rt.clock.now()
        snap = self.rt.snapshot(s)
        from app.engine import planner

        run = RecoveryRun(
            kind=kind,
            status=RunStatus.PENDING,
            source_region=source,
            target_region=target,
            reason=reason,
            incident_id=incident_id,
            game_day_id=game_day_id,
            initiated_by=initiated_by,
            created_at=now,
            plan=planner.generate(snap),
            metrics={},
        )
        run.steps = [
            RunStep(position=i, name=n, title=t, status=StepStatus.PENDING, output={})
            for i, (n, t) in enumerate(STEPS[kind])
        ]
        s.add(run)
        s.flush()
        audit.record(
            s,
            ts=now,
            actor=initiated_by,
            action=f"{kind.value}.initiate",
            target=f"run:{run.id}",
            details={"source": source, "target": target, "reason": reason},
            ip=ip,
        )
        self.advance(s, run)
        return run

    def approve(
        self, s: Session, run: RecoveryRun, user: str, role: Role, comment: str = "", ip: str | None = None
    ) -> RecoveryRun:
        policy = self.rt.policy(s)
        now = self.rt.clock.now()
        if run.status != RunStatus.AWAITING_APPROVAL:
            raise RunError(f"run is {run.status.value}, not awaiting approval")
        if role not in APPROVER_ROLES:
            audit.record(
                s,
                ts=now,
                actor=user,
                action=f"{run.kind.value}.approve",
                target=f"run:{run.id}",
                outcome="denied",
                details={"reason": "role"},
                ip=ip,
            )
            raise RunError("approver or admin role required", 403)
        if policy.two_person_rule and user == run.initiated_by:
            audit.record(
                s,
                ts=now,
                actor=user,
                action=f"{run.kind.value}.approve",
                target=f"run:{run.id}",
                outcome="denied",
                details={"reason": "two-person rule"},
                ip=ip,
            )
            raise RunError("two-person rule: the initiator cannot approve their own run", 403)
        run.approved_by = user
        run.approved_at = now
        run.status = RunStatus.RUNNING
        audit.record(
            s,
            ts=now,
            actor=user,
            action=f"{run.kind.value}.approve",
            target=f"run:{run.id}",
            details={"comment": comment},
            ip=ip,
        )
        self.advance(s, run)
        return run

    def reject(
        self, s: Session, run: RecoveryRun, user: str, role: Role, comment: str, ip: str | None = None
    ) -> RecoveryRun:
        if run.status != RunStatus.AWAITING_APPROVAL:
            raise RunError(f"run is {run.status.value}, not awaiting approval")
        if role not in APPROVER_ROLES:
            raise RunError("approver or admin role required", 403)
        now = self.rt.clock.now()
        for st in run.steps:
            if st.status in (StepStatus.WAITING, StepStatus.RUNNING):
                st.status, st.finished_at = StepStatus.FAILED, now
                st.output = {**st.output, "rejected_by": user, "comment": comment}
            elif st.status == StepStatus.PENDING:
                st.status = StepStatus.SKIPPED
        run.status, run.completed_at, run.error = RunStatus.REJECTED, now, f"rejected by {user}: {comment}"
        audit.record(
            s,
            ts=now,
            actor=user,
            action=f"{run.kind.value}.reject",
            target=f"run:{run.id}",
            details={"comment": comment},
            ip=ip,
        )
        RUNS.labels(run.kind.value, run.status.value).inc()
        return run

    def cancel(self, s: Session, run: RecoveryRun, user: str, ip: str | None = None) -> RecoveryRun:
        if run.status not in ACTIVE:
            raise RunError(f"run is {run.status.value}")
        if any(st.name == "promote" and st.status != StepStatus.PENDING for st in run.steps):
            raise RunError("cannot cancel after promotion started - complete or fail back instead")
        now = self.rt.clock.now()
        for st in run.steps:
            if st.status in (StepStatus.PENDING, StepStatus.WAITING, StepStatus.RUNNING):
                st.status = StepStatus.SKIPPED
        run.status, run.completed_at, run.error = RunStatus.CANCELLED, now, f"cancelled by {user}"
        audit.record(s, ts=now, actor=user, action=f"{run.kind.value}.cancel", target=f"run:{run.id}", ip=ip)
        RUNS.labels(run.kind.value, run.status.value).inc()
        return run

    # ----------------------------------------------------------------- engine
    def advance_active(self, s: Session) -> None:
        run = s.scalar(select(RecoveryRun).where(RecoveryRun.status.in_(ACTIVE)).with_for_update())
        if run is not None:
            self.advance(s, run)

    def advance(self, s: Session, run: RecoveryRun) -> None:
        if run.status not in ACTIVE:
            return
        with tracer().start_as_current_span(f"{run.kind.value}.advance") as span:
            span.set_attribute("run.id", run.id)
            for st in run.steps:
                if st.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
                    continue
                now = self.rt.clock.now()
                if st.started_at is None:
                    st.started_at = now
                st.status = StepStatus.RUNNING
                st.attempts += 1
                if run.status == RunStatus.PENDING and st.name != "approve":
                    run.status = RunStatus.RUNNING
                with tracer().start_as_current_span(f"step.{st.name}"):
                    try:
                        verdict, out = getattr(
                            self, f"_{run.kind.value}_{st.name}", getattr(self, f"_step_{st.name}", None)
                        )(s, run, st)
                    except ProviderError as e:
                        verdict, out = "fail", {"error": str(e)}
                    except Exception as e:  # noqa: BLE001 - a crashing step must fail the run, not the engine
                        log.exception("step %s crashed", st.name)
                        verdict, out = "fail", {"error": f"{type(e).__name__}: {e}"}
                st.output = {**(st.output or {}), **out}
                if verdict == "ok":
                    st.status, st.finished_at = StepStatus.SUCCEEDED, self.rt.clock.now()
                    continue
                if verdict == "wait" and st.attempts < MAX_ATTEMPTS.get(st.name, 10**9):
                    st.status = StepStatus.WAITING
                    return
                if verdict == "wait":
                    out = {"error": f"timed out after {st.attempts} attempts"}
                    st.output = {**st.output, **out}
                self._fail(s, run, st, out.get("error", "step failed"))
                return
            self._complete(s, run)

    def _fail(self, s: Session, run: RecoveryRun, st: RunStep, error: str) -> None:
        now = self.rt.clock.now()
        st.status, st.finished_at = StepStatus.FAILED, now
        for other in run.steps:
            if other.status == StepStatus.PENDING:
                other.status = StepStatus.SKIPPED
        run.status, run.completed_at, run.error = RunStatus.FAILED, now, f"{st.name}: {error}"
        policy = self.rt.policy(s)
        run.metrics = reporting.compute_metrics(run, policy.rto_target_s, policy.rpo_target_s)
        run.report = reporting.build_report(run, run.metrics, self.rt.workload_state())
        audit.record(
            s,
            ts=now,
            actor="system",
            action=f"{run.kind.value}.failed",
            target=f"run:{run.id}",
            outcome="failure",
            details={"step": st.name, "error": error},
        )
        RUNS.labels(run.kind.value, run.status.value).inc()

    def _complete(self, s: Session, run: RecoveryRun) -> None:
        now = self.rt.clock.now()
        run.status, run.completed_at = RunStatus.COMPLETED, now
        policy = self.rt.policy(s)
        run.metrics = reporting.compute_metrics(run, policy.rto_target_s, policy.rpo_target_s)
        run.report = reporting.build_report(run, run.metrics, self.rt.workload_state())
        if run.incident_id and (inc := s.get(Incident, run.incident_id)) and inc.status == "open":
            inc.status = "mitigated"
        RUNS.labels(run.kind.value, "completed").inc()
        if run.metrics.get("rto_s") is not None:
            LAST_RTO.labels(run.kind.value).set(run.metrics["rto_s"])
        if run.metrics.get("rpo_s") is not None:
            LAST_RPO.labels(run.kind.value).set(run.metrics["rpo_s"])
        audit.record(
            s,
            ts=now,
            actor="system",
            action=f"{run.kind.value}.completed",
            target=f"run:{run.id}",
            details={
                "rto_s": run.metrics.get("rto_s"),
                "rpo_s": run.metrics.get("rpo_s"),
                "data_loss_txns": run.metrics.get("data_loss_txns"),
            },
        )

    # ----------------------------------------------------------------- shared helpers
    def _region(self, s: Session, name: str) -> Region:
        r = s.scalar(select(Region).where(Region.name == name))
        assert r is not None
        return r

    def _metrics(self, run: RecoveryRun, **kw: Any) -> None:
        run.metrics = {**(run.metrics or {}), **kw}

    # ----------------------------------------------------------------- failover steps
    def _failover_detect(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        src = self._region(s, run.source_region)
        if src.status == RegionStatus.DOWN:
            inc = (
                s.get(Incident, run.incident_id)
                if run.incident_id
                else s.scalar(
                    select(Incident)
                    .where(Incident.region == src.name, Incident.status == "open")
                    .order_by(Incident.id.desc())
                )
            )
            if inc is not None:
                run.incident_id = inc.id
            run.outage_started_at = (inc.started_at if inc else None) or src.first_failure_at
            run.detected_at = (inc.detected_at if inc else None) or self.rt.clock.now()
            self._metrics(run, planned=False)
            return "ok", {
                "confirmed": True,
                "source_status": "down",
                "failing": (src.last_probe or {}).get("failing", []),
                "incident_id": run.incident_id,
                "outage_started_at": aware(run.outage_started_at).isoformat() if run.outage_started_at else None,
            }
        if src.status == RegionStatus.DEGRADED:
            return "wait", {"source_status": "degraded", "note": "waiting for failure confirmation threshold"}
        self._metrics(run, planned=True)
        run.detected_at = self.rt.clock.now()
        return "ok", {
            "confirmed": False,
            "planned": True,
            "source_status": "up",
            "note": "source healthy - executing a planned switchover (graceful, zero data loss expected)",
        }

    def _step_assess(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        policy = self.rt.policy(s)
        snap = self.rt.snapshot(s)
        now = self.rt.clock.now()
        elapsed = (now - aware(run.outage_started_at)).total_seconds() if run.outage_started_at else 0.0
        exposure = snap.rpo_exposure_s
        planned = (run.metrics or {}).get("planned", False)
        est_remaining = 5.0 + (0 if not policy.require_approval else 60.0)
        out = {
            "rto_target_s": policy.rto_target_s,
            "rpo_target_s": policy.rpo_target_s,
            "outage_elapsed_s": round(elapsed, 2),
            "rto_remaining_s": round(policy.rto_target_s - elapsed, 2),
            "estimated_time_to_recover_s": est_remaining,
            "rto_at_risk": elapsed + est_remaining > policy.rto_target_s,
            "rpo_exposure_s": None if planned or exposure is None else round(exposure, 3),
            "rpo_at_risk": (not planned) and exposure is not None and exposure > policy.rpo_target_s,
        }
        self._metrics(run, assessment=out)
        return "ok", out

    def _step_validate(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        tgt = self.rt.regions[run.target_region]
        probe = tgt.probe()
        dbs = tgt.db_status()
        policy = self.rt.policy(s)
        checks = {
            "target_healthy": probe.healthy,
            "database_reachable": dbs.reachable,
            "database_is_replica": dbs.role == "replica",
        }
        out: dict[str, Any] = {
            "checks": checks,
            "probe": probe.to_dict(),
            "db_role": dbs.role,
            "last_seq": dbs.last_seq,
        }
        if run.kind == RunKind.FAILBACK:
            src = self.rt.regions[run.source_region].db_status()
            lag = None
            if src.reachable and dbs.reachable and src.last_committed_at and dbs.last_committed_at:
                lag = (
                    0.0
                    if (dbs.last_seq or 0) >= (src.last_seq or 0)
                    else max(0.0, (src.last_committed_at - dbs.last_committed_at).total_seconds())
                )
            checks["replication_within_budget"] = lag is not None and lag <= policy.max_replication_lag_s
            out["replication_lag_s"] = lag
            if not all(checks.values()):
                return "wait", out
            return "ok", out
        if not all(checks.values()):
            failed = [k for k, v in checks.items() if not v]
            return "fail", {**out, "error": f"secondary not ready: {', '.join(failed)}"}
        return "ok", out

    def _step_approve(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        policy = self.rt.policy(s)
        if not policy.require_approval and not run.approved_by:
            run.approved_by, run.approved_at = "policy:auto-approve", self.rt.clock.now()
            run.status = RunStatus.RUNNING
        if run.approved_by:
            run.status = RunStatus.RUNNING
            return "ok", {
                "approved_by": run.approved_by,
                "approved_at": aware(run.approved_at).isoformat() if run.approved_at else None,
            }
        run.status = RunStatus.AWAITING_APPROVAL
        return "wait", {
            "note": "awaiting approval from an approver"
            + (" other than the initiator" if policy.two_person_rule else "")
        }

    def _step_promote(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        src_p, tgt_p = self.rt.regions[run.source_region], self.rt.regions[run.target_region]
        src_db = src_p.db_status()
        wl_before = self.rt.workload_state()
        fence = src_p.fence()
        graceful = src_db.reachable
        out: dict[str, Any] = {"fence": fence, "graceful": graceful}
        if graceful:
            out["drain"] = tgt_p.catch_up_from(src_p)
        tgt_db_before = tgt_p.db_status()
        out["promote"] = tgt_p.promote()
        if graceful:
            out["demote_source"] = src_p.demote_to_replica(tgt_p)
        # RPO measurement: client-acknowledged writes vs what the promoted database actually holds.
        acked_seq, acked_at = wl_before.get("last_acked_seq"), wl_before.get("last_acked_at")
        lost, rpo = 0, 0.0
        if wl_before.get("last_acked_region") == run.source_region and acked_seq is not None:
            lost = max(0, acked_seq - (tgt_db_before.last_seq or 0))
            if lost and tgt_db_before.last_committed_at is not None and acked_at is not None:
                rpo = max(0.0, acked_at - tgt_db_before.last_committed_at.timestamp())
        elif not wl_before:
            # Cloud mode without the synthetic workload: use the last observed replication lag.
            rpo = float(self._region(s, run.target_region).replication_lag_s or 0.0)
        self._metrics(
            run, data_loss_txns=lost, rpo_s=round(rpo, 3), graceful=graceful, promoted_at_seq=tgt_db_before.last_seq
        )
        out.update(data_loss_txns=lost, rpo_s=round(rpo, 3))
        # Reflect the new roles immediately instead of waiting for the next monitor probe.
        for name, prov in ((run.target_region, tgt_p), (run.source_region, src_p)):
            st_now = prov.db_status()
            row = self._region(s, name)
            if st_now.reachable:
                row.db_role, row.last_applied_seq = st_now.role, st_now.last_seq
            elif name == run.source_region and fence.get("role"):
                row.db_role = fence["role"]
        audit.record(
            s,
            ts=self.rt.clock.now(),
            actor="system",
            action=f"{run.kind.value}.promote",
            target=run.target_region,
            details={"run": run.id, "graceful": graceful, "lost": lost},
        )
        return "ok", out

    def _step_switch(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        res = self.rt.traffic.switch_to(run.target_region)
        audit.record(
            s,
            ts=self.rt.clock.now(),
            actor="system",
            action="traffic.switch",
            target=run.target_region,
            details={"run": run.id, **res},
        )
        return "ok", res

    def _step_verify(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        tgt = self.rt.regions[run.target_region]
        active = self.rt.traffic.active_region()
        probe = tgt.probe()
        synth = tgt.synthetic_transaction() if active == run.target_region else None
        out = {
            "traffic_on_target": active == run.target_region,
            "probe_healthy": probe.healthy,
            "synthetic_ok": bool(synth and synth.ok),
            "synthetic_latency_ms": synth.latency_ms if synth else None,
            "synthetic_detail": synth.detail if synth else "traffic not on target",
        }
        if out["traffic_on_target"] and probe.healthy and out["synthetic_ok"]:
            run.service_restored_at = self.rt.clock.now()
            return "ok", out
        return "wait", out

    # ----------------------------------------------------------------- failback steps
    def _failback_precheck(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        tgt = self._region(s, run.target_region)
        run.detected_at = run.detected_at or self.rt.clock.now()
        self._metrics(run, planned=True)
        if tgt.status != RegionStatus.UP:
            return "wait", {"target_status": tgt.status.value, "note": "waiting for primary to report healthy"}
        return "ok", {"target_status": "up"}

    def _failback_resync(self, s: Session, run: RecoveryRun, st: RunStep) -> tuple[str, dict[str, Any]]:
        tgt = self.rt.regions[run.target_region]
        if tgt.db_status().role == "replica":
            return "ok", {"note": "already a replica - no resync needed", "divergent_discarded": 0}
        res = tgt.resync_from(self.rt.regions[run.source_region])
        self._metrics(run, divergent_discarded=res.divergent_discarded)
        audit.record(
            s,
            ts=self.rt.clock.now(),
            actor="system",
            action="failback.resync",
            target=run.target_region,
            details={"run": run.id, "copied": res.copied, "divergent": res.divergent_discarded},
        )
        return "ok", {
            "copied": res.copied,
            "divergent_discarded": res.divergent_discarded,
            "divergent_seqs": res.divergent_seqs[:20],
            "last_seq": res.resulting_last_seq,
        }


def latest_policy(s: Session) -> DrPolicy:
    p = s.scalar(select(DrPolicy).where(DrPolicy.name == "default"))
    assert p is not None, "policy not bootstrapped"
    return p
