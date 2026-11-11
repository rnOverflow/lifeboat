"""Backups, failure simulator, remediation and game days."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as ser
from app.api.deps import Principal, get_rt, get_session, require
from app.engine import backups as backup_svc
from app.engine.gameday import GameDayError
from app.engine.runtime import Runtime
from app.models import Backup, FaultType, GameDay, Role, SimFault
from app.providers.base import ProviderError
from app.security import Perm

router = APIRouter(tags=["operations"])


# ------------------------------------------------------------------ backups
class BackupIn(BaseModel):
    region: str | None = None


@router.get("/backups")
def list_backups(
    limit: int = Query(50, le=200), s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> list[dict]:
    return [ser.backup(b) for b in s.scalars(select(Backup).order_by(Backup.id.desc()).limit(limit))]


@router.post("/backups", status_code=201)
def create_backup(body: BackupIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.BACKUP))) -> dict:
    if body.region and body.region not in rt.regions:
        raise HTTPException(404, "unknown region")
    with rt.lock, rt.db.session() as s:
        try:
            b = backup_svc.create_backup(rt, s, body.region, p.username, p.ip)
        except ProviderError as e:
            audit.record(
                s,
                ts=rt.clock.now(),
                actor=p.username,
                action="backup.create",
                outcome="failure",
                details={"error": str(e)},
                ip=p.ip,
            )
            raise HTTPException(409, str(e)) from e
        return ser.backup(b)


@router.post("/backups/{backup_id}/verify")
def verify_backup(backup_id: int, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.BACKUP))) -> dict:
    with rt.lock, rt.db.session() as s:
        b = s.get(Backup, backup_id)
        if b is None:
            raise HTTPException(404, "backup not found")
        return ser.backup(backup_svc.verify_backup(rt, s, b, p.username, p.ip))


# ------------------------------------------------------------------ simulator
def _sim(rt: Runtime):  # noqa: ANN202
    if rt.sim is None:
        raise HTTPException(400, "failure simulator only available in local provider mode")
    return rt.sim


class FaultIn(BaseModel):
    region: str
    fault_type: FaultType


class ClearIn(BaseModel):
    region: str
    fault_type: FaultType | None = None


@router.get("/simulator/faults")
def list_faults(
    active_only: bool = False, s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> list[dict]:
    q = select(SimFault).order_by(SimFault.id.desc()).limit(100)
    if active_only:
        q = q.where(SimFault.active.is_(True))
    return [ser.fault(f) for f in s.scalars(q)]


@router.post("/simulator/faults", status_code=201)
def inject_fault(
    body: FaultIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.INJECT_FAULT))
) -> dict:
    sim = _sim(rt)
    if body.region not in rt.regions:
        raise HTTPException(404, "unknown region")
    with rt.lock:
        f = sim.inject(body.region, body.fault_type, p.username)
        with rt.db.session() as s:
            audit.record(
                s,
                ts=rt.clock.now(),
                actor=p.username,
                action="fault.inject",
                target=body.region,
                details={"fault": body.fault_type.value, "fault_id": f.id},
                ip=p.ip,
            )
        return ser.fault(f)


@router.post("/simulator/clear")
def clear_faults(
    body: ClearIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.INJECT_FAULT))
) -> dict:
    sim = _sim(rt)
    if body.region not in rt.regions:
        raise HTTPException(404, "unknown region")
    with rt.lock:
        n = sim.clear(body.region, p.username, body.fault_type)
        with rt.db.session() as s:
            audit.record(
                s,
                ts=rt.clock.now(),
                actor=p.username,
                action="fault.clear",
                target=body.region,
                details={"fault": body.fault_type.value if body.fault_type else "all", "cleared": n},
                ip=p.ip,
            )
        return {"cleared": n}


@router.post("/simulator/backups/{backup_id}/corrupt")
def corrupt_backup(
    backup_id: int, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.INJECT_FAULT))
) -> dict:
    _sim(rt)
    with rt.lock, rt.db.session() as s:
        b = s.get(Backup, backup_id)
        if b is None:
            raise HTTPException(404, "backup not found")
        path = Path(b.location)
        if not path.exists():
            raise HTTPException(409, "backup object missing")
        raw = bytearray(path.read_bytes())
        mid = len(raw) // 2
        raw[mid : mid + 16] = bytes((x ^ 0xFF) for x in raw[mid : mid + 16])  # flip bits: simulated bit-rot
        path.write_bytes(bytes(raw))
        audit.record(
            s, ts=rt.clock.now(), actor=p.username, action="fault.corrupt_backup", target=f"backup:{b.id}", ip=p.ip
        )
        return {"corrupted": True, "backup_id": b.id}


@router.post("/regions/{name}/restart")
def restart_service(name: str, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.REMEDIATE))) -> dict:
    if name not in rt.regions:
        raise HTTPException(404, "unknown region")
    with rt.lock, rt.db.session() as s:
        try:
            res = rt.regions[name].restart_service()
        except ProviderError as e:
            raise HTTPException(409, str(e)) from e
        audit.record(
            s, ts=rt.clock.now(), actor=p.username, action="service.restart", target=name, details=res, ip=p.ip
        )
        return res


# ------------------------------------------------------------------ game days
class GameDayIn(BaseModel):
    name: str = Field(min_length=3, max_length=128)
    scenario: FaultType
    target_region: str | None = None
    auto_approve: bool = False
    include_failback: bool = True


@router.get("/gamedays")
def list_gamedays(s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))) -> list[dict]:
    return [ser.gameday(g) for g in s.scalars(select(GameDay).order_by(GameDay.id.desc()).limit(50))]


@router.get("/gamedays/{gd_id}")
def get_gameday(gd_id: int, s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))) -> dict:
    g = s.get(GameDay, gd_id)
    if g is None:
        raise HTTPException(404, "game day not found")
    return ser.gameday(g)


@router.post("/gamedays", status_code=201)
def start_gameday(
    body: GameDayIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.GAMEDAY))
) -> dict:
    if body.auto_approve and p.role not in (Role.APPROVER, Role.ADMIN):
        raise HTTPException(403, "pre-approving a game day requires approver or admin role")
    with rt.lock, rt.db.session() as s:
        try:
            g = rt.gamedays.start(
                s,
                body.name,
                body.scenario,
                body.target_region,
                body.auto_approve,
                body.include_failback,
                p.username,
                p.ip,
            )
        except GameDayError as e:
            raise HTTPException(409, str(e)) from e
        return ser.gameday(g)


@router.post("/gamedays/{gd_id}/abort")
def abort_gameday(gd_id: int, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.GAMEDAY))) -> dict:
    with rt.lock, rt.db.session() as s:
        g = s.get(GameDay, gd_id)
        if g is None:
            raise HTTPException(404, "game day not found")
        try:
            rt.gamedays.abort(s, g, p.username, p.ip)
        except GameDayError as e:
            raise HTTPException(409, str(e)) from e
        return ser.gameday(g)
