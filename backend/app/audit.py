"""Tamper-evident audit trail: every entry stores SHA-256(prev_hash || canonical entry)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.clock import aware
from app.models import AuditLog

GENESIS = "0" * 64


def _digest(
    prev: str, ts: datetime, actor: str, action: str, target: str | None, outcome: str, details: dict[str, Any]
) -> str:
    body = json.dumps(
        {
            "ts": aware(ts).astimezone(UTC).isoformat(),
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "details": details,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256((prev + body).encode()).hexdigest()


def record(
    s: Session,
    *,
    ts: datetime,
    actor: str,
    action: str,
    target: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    details = json.loads(json.dumps(details or {}, default=str))
    if s.bind is not None and s.bind.dialect.name == "postgresql":
        # Serialise chain appends across API replicas.
        s.execute(text("SELECT pg_advisory_xact_lock(727001)"))
    prev = s.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)) or GENESIS
    entry = AuditLog(
        ts=ts,
        actor=actor,
        action=action,
        target=target,
        outcome=outcome,
        details=details,
        ip=ip,
        prev_hash=prev,
        hash=_digest(prev, ts, actor, action, target, outcome, details),
    )
    s.add(entry)
    s.flush()
    return entry


def verify_chain(s: Session) -> dict[str, Any]:
    prev = GENESIS
    n = 0
    for e in s.scalars(select(AuditLog).order_by(AuditLog.id)):
        ts = e.ts
        expected = _digest(prev, ts, e.actor, e.action, e.target, e.outcome, e.details)
        if e.prev_hash != prev or e.hash != expected:
            return {"valid": False, "entries_checked": n, "broken_at_id": e.id}
        prev = e.hash
        n += 1
    return {"valid": True, "entries_checked": n, "head": prev}
