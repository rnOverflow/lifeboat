from __future__ import annotations

from datetime import datetime
from typing import Any

from app.clock import aware
from app.models import AuditLog, Backup, GameDay, Incident, RecoveryRun, Region, SimFault


def iso(dt: datetime | None) -> str | None:
    return aware(dt).isoformat() if dt else None


def region(r: Region) -> dict[str, Any]:
    return {
        "name": r.name,
        "cloud": r.cloud,
        "display_name": r.display_name,
        "designated_role": r.designated_role,
        "status": r.status.value,
        "db_role": r.db_role,
        "replication_lag_s": r.replication_lag_s,
        "last_applied_seq": r.last_applied_seq,
        "consecutive_failures": r.consecutive_failures,
        "last_probe_at": iso(r.last_probe_at),
        "last_healthy_at": iso(r.last_healthy_at),
        "first_failure_at": iso(r.first_failure_at),
        "probe": r.last_probe,
    }


def incident(i: Incident) -> dict[str, Any]:
    return {
        "id": i.id,
        "region": i.region,
        "kind": i.kind,
        "severity": i.severity,
        "status": i.status,
        "summary": i.summary,
        "started_at": iso(i.started_at),
        "detected_at": iso(i.detected_at),
        "resolved_at": iso(i.resolved_at),
        "details": i.details,
    }


def run(r: RecoveryRun, full: bool = True) -> dict[str, Any]:
    d = {
        "id": r.id,
        "kind": r.kind.value,
        "status": r.status.value,
        "source_region": r.source_region,
        "target_region": r.target_region,
        "reason": r.reason,
        "incident_id": r.incident_id,
        "game_day_id": r.game_day_id,
        "initiated_by": r.initiated_by,
        "approved_by": r.approved_by,
        "created_at": iso(r.created_at),
        "approved_at": iso(r.approved_at),
        "completed_at": iso(r.completed_at),
        "outage_started_at": iso(r.outage_started_at),
        "detected_at": iso(r.detected_at),
        "service_restored_at": iso(r.service_restored_at),
        "metrics": r.metrics,
        "error": r.error,
        "steps": [
            {
                "position": s.position,
                "name": s.name,
                "title": s.title,
                "status": s.status.value,
                "attempts": s.attempts,
                "started_at": iso(s.started_at),
                "finished_at": iso(s.finished_at),
                "output": s.output,
            }
            for s in r.steps
        ],
    }
    if full:
        d["plan"] = r.plan
        d["report"] = r.report
    return d


def backup(b: Backup) -> dict[str, Any]:
    return {
        "id": b.id,
        "region": b.region,
        "created_at": iso(b.created_at),
        "created_by": b.created_by,
        "size_bytes": b.size_bytes,
        "record_count": b.record_count,
        "max_seq": b.max_seq,
        "checksum_sha256": b.checksum_sha256,
        "status": b.status,
        "verified_at": iso(b.verified_at),
        "verification": b.verification,
        "object": b.location.rsplit("/", 1)[-1],
    }


def fault(f: SimFault) -> dict[str, Any]:
    return {
        "id": f.id,
        "region": f.region,
        "fault_type": f.fault_type.value,
        "active": f.active,
        "injected_at": iso(f.injected_at),
        "injected_by": f.injected_by,
        "cleared_at": iso(f.cleared_at),
        "cleared_by": f.cleared_by,
        "game_day_id": f.game_day_id,
    }


def gameday(g: GameDay) -> dict[str, Any]:
    return {
        "id": g.id,
        "name": g.name,
        "scenario": g.scenario,
        "target_region": g.target_region,
        "status": g.status,
        "phase": g.phase,
        "auto_approve": g.auto_approve,
        "include_failback": g.include_failback,
        "created_by": g.created_by,
        "started_at": iso(g.started_at),
        "finished_at": iso(g.finished_at),
        "failover_run_id": g.failover_run_id,
        "failback_run_id": g.failback_run_id,
        "timeline": g.timeline,
        "report": g.report,
    }


def audit_entry(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "ts": iso(a.ts),
        "actor": a.actor,
        "action": a.action,
        "target": a.target,
        "outcome": a.outcome,
        "details": a.details,
        "ip": a.ip,
        "hash": a.hash,
    }
