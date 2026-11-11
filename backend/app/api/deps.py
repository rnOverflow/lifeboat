from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.engine.runtime import Runtime
from app.models import Role, User
from app.security import Perm, decode_token, has_perm

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    username: str
    role: Role
    ip: str | None


def get_rt(request: Request) -> Runtime:
    return request.app.state.runtime


def get_session(rt: Runtime = Depends(get_rt)) -> Iterator[Session]:
    with rt.db.session() as s:
        yield s


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer), rt: Runtime = Depends(get_rt)
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated", {"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_token(rt.settings.effective_jwt_secret(), creds.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or expired token", {"WWW-Authenticate": "Bearer"}
        ) from e
    with rt.db.session() as s:
        user = s.scalar(select(User).where(User.username == claims["sub"]))
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user disabled")
        # role is read from the DB, not trusted from the token, so demotions take effect immediately
        return Principal(user.username, user.role, client_ip(request))


def require(perm: Perm) -> Callable[..., Principal]:
    def dep(p: Principal = Depends(current_user), rt: Runtime = Depends(get_rt)) -> Principal:
        if not has_perm(p.role, perm):
            with rt.db.session() as s:
                audit.record(
                    s,
                    ts=rt.clock.now(),
                    actor=p.username,
                    action="authz.denied",
                    target=perm.value,
                    outcome="denied",
                    details={"role": p.role.value},
                    ip=p.ip,
                )
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"permission '{perm.value}' required")
        return p

    return dep
