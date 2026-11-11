"""Read-mostly DR state: status, regions, readiness, plan, incidents, replication, policy."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as ser
from app.api.deps import Principal, get_rt, get_session, require
from app.engine import planner, readiness
from app.engine.runtime import Runtime
from app.models import HealthSample, Incident, Region
from app.security import Perm

router = APIRouter(tags=["dr"])


@router.get("/status")
def status(
    rt: Runtime = Depends(get_rt), s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> dict:
    snap = rt.snapshot(s)
    regions = [ser.region(r) for r in s.scalars(select(Region).order_by(Region.id))]
    return {
        "mode": rt.settings.provider_mode,
        "active_region": snap.active_region,
        "standby_region": snap.standby_region,
        "designated_primary": snap.designated_primary,
        "failed_over": snap.failed_over,
        "regions": regions,
        "rpo_exposure_s": snap.rpo_exposure_s,
        "policy": snap.to_dict()["policy"],
        "readiness": readiness.compute(snap),
        "workload": snap.workload,
        "open_incidents": snap.open_incidents,
        "active_run": snap.active_run,
        "latest_backup": snap.latest_backup,
        "measured_rto_s": snap.measured_rto_s[-10:],
        "engine": {
            "last_tick_at": ser.iso(rt.last_tick_at),
            "tick_errors": rt.tick_errors,
            "scheduler_enabled": rt.settings.scheduler_enabled,
        },
        "server_time": snap.now.isoformat(),
    }


@router.get("/readiness")
def get_readiness(
    rt: Runtime = Depends(get_rt), s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> dict:
    return readiness.compute(rt.snapshot(s))


@router.get("/plan")
def get_plan(
    rt: Runtime = Depends(get_rt), s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> dict:
    return planner.generate(rt.snapshot(s))


@router.get("/regions/{name}/history")
def region_history(
    name: str,
    minutes: int = Query(10, ge=1, le=120),
    rt: Runtime = Depends(get_rt),
    s: Session = Depends(get_session),
    _: Principal = Depends(require(Perm.READ)),
) -> list[dict]:
    since = rt.clock.now() - timedelta(minutes=minutes)
    rows = s.scalars(
        select(HealthSample).where(HealthSample.region == name, HealthSample.ts >= since).order_by(HealthSample.ts)
    )
    return [
        {
            "ts": ser.iso(h.ts),
            "healthy": h.healthy,
            "app": h.app_ok,
            "db": h.db_ok,
            "network": h.network_ok,
            "latency_ms": h.latency_ms,
            "replication_lag_s": h.replication_lag_s,
        }
        for h in rows
    ]


@router.get("/incidents")
def incidents(
    status: str | None = None,
    limit: int = Query(50, le=500),
    s: Session = Depends(get_session),
    _: Principal = Depends(require(Perm.READ)),
) -> list[dict]:
    q = select(Incident).order_by(Incident.id.desc()).limit(limit)
    if status:
        q = q.where(Incident.status == status)
    return [ser.incident(i) for i in s.scalars(q)]


class PolicyIn(BaseModel):
    rto_target_s: int = Field(ge=10, le=86400)
    rpo_target_s: int = Field(ge=0, le=86400)
    max_replication_lag_s: int = Field(ge=1, le=3600)
    backup_max_age_s: int = Field(ge=60, le=7 * 86400)
    auto_initiate_failover: bool
    require_approval: bool
    two_person_rule: bool


@router.get("/policy")
def get_policy(
    rt: Runtime = Depends(get_rt), s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> dict:
    p = rt.policy(s)
    return {
        **PolicyIn.model_validate(p, from_attributes=True).model_dump(),
        "updated_at": ser.iso(p.updated_at),
        "updated_by": p.updated_by,
    }


@router.put("/policy")
def put_policy(
    body: PolicyIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.MANAGE_POLICY))
) -> dict:
    with rt.lock, rt.db.session() as s:
        pol = rt.policy(s)
        before = PolicyIn.model_validate(pol, from_attributes=True).model_dump()
        for k, v in body.model_dump().items():
            setattr(pol, k, v)
        pol.updated_at, pol.updated_by = rt.clock.now(), p.username
        audit.record(
            s,
            ts=rt.clock.now(),
            actor=p.username,
            action="policy.update",
            target="policy:default",
            details={"before": before, "after": body.model_dump()},
            ip=p.ip,
        )
        return body.model_dump()
