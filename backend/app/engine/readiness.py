"""Recovery-readiness score: can we survive losing the *currently active* region right now?"""

from __future__ import annotations

from statistics import mean
from typing import Any

from app.engine.snapshot import Snapshot

DRILL_FRESH_S = 30 * 24 * 3600


def _component(key: str, label: str, weight: int, earned: float, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "weight": weight, "score": round(earned, 1), "detail": detail}


def compute(snap: Snapshot) -> dict[str, Any]:
    p = snap.policy
    sb = snap.standby
    comps: list[dict[str, Any]] = []
    blockers: list[str] = []

    # 1. Standby health (25)
    if sb.status == "up" and sb.db_role == "replica":
        comps.append(
            _component(
                "standby_health", "Standby region health", 25, 25, f"{sb.display_name} healthy, database is a replica"
            )
        )
    elif sb.status == "up":
        comps.append(
            _component(
                "standby_health",
                "Standby region health",
                25,
                8,
                f"{sb.display_name} up but database role is '{sb.db_role}' (needs resync)",
            )
        )
        blockers.append(f"{sb.display_name} database is {sb.db_role}; resync before it can take over")
    elif sb.status == "degraded":
        comps.append(
            _component(
                "standby_health",
                "Standby region health",
                25,
                10,
                f"{sb.display_name} degraded: {', '.join(sb.failing_components) or 'probing'}",
            )
        )
    else:
        comps.append(_component("standby_health", "Standby region health", 25, 0, f"{sb.display_name} is DOWN"))
        blockers.append(f"No healthy standby: {sb.display_name} is down")

    # 2. Replication / RPO exposure (25)
    exposure = snap.rpo_exposure_s
    lag = sb.replication_lag_s
    if sb.db_role != "replica":
        comps.append(_component("replication", "Replication & RPO exposure", 25, 0, "standby not replicating"))
    elif exposure is None and lag is None:
        comps.append(_component("replication", "Replication & RPO exposure", 25, 5, "replication lag unknown"))
    else:
        val = exposure if exposure is not None else lag or 0.0
        if val <= p.max_replication_lag_s:
            earned, d = 25, f"RPO exposure {val:.1f}s within lag budget {p.max_replication_lag_s}s"
        elif val <= p.rpo_target_s:
            earned, d = 15, f"RPO exposure {val:.1f}s above lag budget but within RPO {p.rpo_target_s}s"
        else:
            earned, d = 0, f"RPO exposure {val:.1f}s BREACHES RPO target {p.rpo_target_s}s"
            blockers.append(d)
        comps.append(_component("replication", "Replication & RPO exposure", 25, earned, d))

    # 3. Backups (20)
    lb = snap.latest_backup
    age = snap.latest_verified_backup_age_s
    if lb is None:
        comps.append(_component("backups", "Backup integrity", 20, 0, "no backups taken"))
    elif lb.get("status") == "corrupt":
        comps.append(_component("backups", "Backup integrity", 20, 2, "latest backup FAILED integrity check"))
    elif age is None:
        comps.append(_component("backups", "Backup integrity", 20, 8, "backups exist but none verified"))
    elif age <= p.backup_max_age_s:
        comps.append(
            _component(
                "backups", "Backup integrity", 20, 20, f"verified backup {age:.0f}s old (max {p.backup_max_age_s}s)"
            )
        )
    else:
        comps.append(
            _component(
                "backups",
                "Backup integrity",
                20,
                6,
                f"newest verified backup is stale ({age:.0f}s > {p.backup_max_age_s}s)",
            )
        )

    # 4. DR testing cadence (15)
    if snap.last_successful_drill_at is None:
        comps.append(_component("testing", "DR test cadence", 15, 0, "failover never exercised"))
    else:
        since = (snap.now - snap.last_successful_drill_at).total_seconds()
        if since <= DRILL_FRESH_S:
            comps.append(
                _component("testing", "DR test cadence", 15, 15, f"last successful drill {since / 3600:.1f}h ago")
            )
        else:
            comps.append(_component("testing", "DR test cadence", 15, 7, f"last drill {since / 86400:.0f} days ago"))

    # 5. Proven RTO (15)
    if not snap.measured_rto_s:
        comps.append(_component("rto", "Proven RTO", 15, 7, "no measured failover yet (RTO unproven)"))
    else:
        avg = mean(snap.measured_rto_s[-5:])
        if avg <= p.rto_target_s:
            comps.append(
                _component("rto", "Proven RTO", 15, 15, f"avg measured RTO {avg:.1f}s <= target {p.rto_target_s}s")
            )
        else:
            comps.append(
                _component("rto", "Proven RTO", 15, 0, f"avg measured RTO {avg:.1f}s > target {p.rto_target_s}s")
            )

    if snap.active.status == "down":
        blockers.append(f"Active region {snap.active.display_name} is DOWN - fail over to restore service")
    if snap.active_run:
        blockers.append(f"{snap.active_run['kind']} run #{snap.active_run['id']} in progress")

    score = round(sum(c["score"] for c in comps), 1)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    status = "ready" if score >= 75 and not blockers else "at_risk" if score >= 50 else "not_ready"
    return {
        "score": score,
        "grade": grade,
        "status": status,
        "components": comps,
        "blockers": blockers,
        "protected_region": snap.active_region,
        "standby_region": snap.standby_region,
        "computed_at": snap.now.isoformat(),
    }
