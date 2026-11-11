from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import audit
from app.api.deps import Principal, client_ip, current_user, get_rt
from app.engine.runtime import Runtime
from app.models import User
from app.security import ROLE_PERMS, dummy_verify, issue_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: LoginIn, request: Request, rt: Runtime = Depends(get_rt)) -> dict:
    ip = client_ip(request) or "unknown"
    limiter = request.app.state.login_limiter
    keys = (f"user:{body.username}", f"ip:{ip}")
    if limiter.blocked(*keys):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts, try later")
    with rt.db.session() as s:
        user = s.scalar(select(User).where(User.username == body.username))
        if user is None:
            dummy_verify()
        ok = user is not None and user.is_active and verify_password(body.password, user.password_hash)
        audit.record(
            s,
            ts=rt.clock.now(),
            actor=body.username,
            action="auth.login",
            outcome="success" if ok else "failure",
            ip=ip,
        )
        if not ok or user is None:
            limiter.fail(*keys)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        limiter.reset(*keys)
        token, exp = issue_token(
            rt.settings.effective_jwt_secret(), user.username, user.role, rt.settings.jwt_ttl_minutes
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": exp.isoformat(),
            "user": {"username": user.username, "role": user.role.value},
        }


@router.get("/me")
def me(p: Principal = Depends(current_user)) -> dict:
    return {"username": p.username, "role": p.role.value, "permissions": sorted(x.value for x in ROLE_PERMS[p.role])}
