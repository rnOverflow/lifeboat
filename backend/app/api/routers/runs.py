"""Controlled failover / failback runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as ser
from app.api.deps import Principal, get_rt, get_session, require
from app.engine import report as reporting
from app.engine.orchestrator import RunError
from app.engine.runtime import Runtime
from app.models import RecoveryRun, RunKind
from app.security import Perm

router = APIRouter(prefix="/runs", tags=["runs"])


class InitiateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DecisionIn(BaseModel):
    comment: str = Field(default="", max_length=500)


def _err(e: RunError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.get("")
def list_runs(
    limit: int = Query(50, le=200), s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))
) -> list[dict]:
    return [ser.run(r, full=False) for r in s.scalars(select(RecoveryRun).order_by(RecoveryRun.id.desc()).limit(limit))]


@router.get("/{run_id}")
def get_run(run_id: int, s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))) -> dict:
    r = s.get(RecoveryRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return ser.run(r)


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
def run_report_md(run_id: int, s: Session = Depends(get_session), _: Principal = Depends(require(Perm.READ))) -> str:
    r = s.get(RecoveryRun, run_id)
    if r is None or not r.report:
        raise HTTPException(404, "report not available")
    return reporting.to_markdown(r.report)


def _initiate(kind: RunKind, body: InitiateIn, rt: Runtime, p: Principal) -> dict:
    with rt.lock, rt.db.session() as s:
        try:
            r = rt.orchestrator.create(s, kind, body.reason, p.username, ip=p.ip)
        except RunError as e:
            raise _err(e) from e
        return ser.run(r)


@router.post("/failover", status_code=201)
def initiate_failover(
    body: InitiateIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.INITIATE_RUN))
) -> dict:
    return _initiate(RunKind.FAILOVER, body, rt, p)


@router.post("/failback", status_code=201)
def initiate_failback(
    body: InitiateIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.INITIATE_RUN))
) -> dict:
    return _initiate(RunKind.FAILBACK, body, rt, p)


def _with_run(run_id: int, rt: Runtime, fn) -> dict:  # noqa: ANN001
    with rt.lock, rt.db.session() as s:
        r = s.scalar(select(RecoveryRun).where(RecoveryRun.id == run_id).with_for_update())
        if r is None:
            raise HTTPException(404, "run not found")
        try:
            fn(s, r)
        except RunError as e:
            raise _err(e) from e
        return ser.run(r)


@router.post("/{run_id}/approve")
def approve(
    run_id: int, body: DecisionIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.APPROVE_RUN))
) -> dict:
    return _with_run(run_id, rt, lambda s, r: rt.orchestrator.approve(s, r, p.username, p.role, body.comment, p.ip))


@router.post("/{run_id}/reject")
def reject(
    run_id: int, body: DecisionIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.APPROVE_RUN))
) -> dict:
    return _with_run(run_id, rt, lambda s, r: rt.orchestrator.reject(s, r, p.username, p.role, body.comment, p.ip))


@router.post("/{run_id}/cancel")
def cancel(run_id: int, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.INITIATE_RUN))) -> dict:
    return _with_run(run_id, rt, lambda s, r: rt.orchestrator.cancel(s, r, p.username, p.ip))
