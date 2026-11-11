from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as ser
from app.api.deps import Principal, get_rt, get_session, require
from app.engine.runtime import Runtime
from app.models import AuditLog, Role, User
from app.security import Perm, hash_password

router = APIRouter(tags=["admin"])


@router.get("/audit")
def list_audit(
    limit: int = Query(100, le=1000),
    actor: str | None = None,
    action: str | None = None,
    s: Session = Depends(get_session),
    _: Principal = Depends(require(Perm.VIEW_AUDIT)),
) -> list[dict]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    return [ser.audit_entry(a) for a in s.scalars(q)]


@router.get("/audit/verify")
def verify_audit(s: Session = Depends(get_session), _: Principal = Depends(require(Perm.VIEW_AUDIT))) -> dict:
    return audit.verify_chain(s)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Role


class UserPatch(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


@router.get("/users")
def list_users(s: Session = Depends(get_session), _: Principal = Depends(require(Perm.MANAGE_USERS))) -> list[dict]:
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role.value,
            "is_active": u.is_active,
            "created_at": ser.iso(u.created_at),
        }
        for u in s.scalars(select(User).order_by(User.id))
    ]


@router.post("/users", status_code=201)
def create_user(
    body: UserIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.MANAGE_USERS))
) -> dict:
    with rt.db.session() as s:
        if s.scalar(select(User).where(User.username == body.username)):
            raise HTTPException(409, "username taken")
        u = User(
            username=body.username,
            password_hash=hash_password(body.password),
            role=body.role,
            is_active=True,
            created_at=rt.clock.now(),
        )
        s.add(u)
        s.flush()
        audit.record(
            s,
            ts=rt.clock.now(),
            actor=p.username,
            action="user.create",
            target=f"user:{u.username}",
            details={"role": body.role.value},
            ip=p.ip,
        )
        return {"id": u.id, "username": u.username, "role": u.role.value, "is_active": True}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int, body: UserPatch, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.MANAGE_USERS))
) -> dict:
    with rt.db.session() as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(404, "user not found")
        if u.username == p.username:
            raise HTTPException(409, "admins cannot change their own role or status")
        if body.role is not None:
            u.role = body.role
        if body.is_active is not None:
            u.is_active = body.is_active
        audit.record(
            s,
            ts=rt.clock.now(),
            actor=p.username,
            action="user.update",
            target=f"user:{u.username}",
            details=body.model_dump(exclude_none=True),
            ip=p.ip,
        )
        return {"id": u.id, "username": u.username, "role": u.role.value, "is_active": u.is_active}
