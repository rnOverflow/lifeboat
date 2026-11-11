"""Backup creation and integrity validation (checksum + test restore)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app import audit
from app.models import Backup

if TYPE_CHECKING:
    from app.engine.runtime import Runtime


def create_backup(rt: Runtime, s: Session, region: str | None, user: str, ip: str | None = None) -> Backup:
    region = region or rt.traffic.active_region()
    art = rt.regions[region].create_backup()
    now = rt.clock.now()
    b = Backup(
        region=region,
        created_at=now,
        created_by=user,
        location=art.location,
        size_bytes=art.size_bytes,
        record_count=art.record_count,
        max_seq=art.max_seq,
        checksum_sha256=art.checksum_sha256,
        status="created",
        verification={},
    )
    s.add(b)
    s.flush()
    audit.record(
        s,
        ts=now,
        actor=user,
        action="backup.create",
        target=f"backup:{b.id}",
        details={"region": region, "records": art.record_count, "sha256": art.checksum_sha256},
        ip=ip,
    )
    return b


def verify_backup(rt: Runtime, s: Session, b: Backup, user: str, ip: str | None = None) -> Backup:
    res = rt.regions[b.region].verify_backup(b.location, b.checksum_sha256)
    if res.ok and res.record_count != b.record_count:
        res.ok = False
        res.issues.append(f"record count {res.record_count} != catalogued {b.record_count}")
    now = rt.clock.now()
    b.status = "verified" if res.ok else "corrupt"
    b.verified_at = now
    b.verification = res.to_dict()
    audit.record(
        s,
        ts=now,
        actor=user,
        action="backup.verify",
        target=f"backup:{b.id}",
        outcome="success" if res.ok else "failure",
        details=res.to_dict(),
        ip=ip,
    )
    return b
