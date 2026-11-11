"""Authentication (bcrypt + JWT) and role-based access control."""

from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from enum import Enum

import bcrypt
import jwt

from app.models import Role

# Cost factor; lowered only in the test suite via env to keep tests fast.
BCRYPT_ROUNDS = max(4, int(os.environ.get("LIFEBOAT_BCRYPT_ROUNDS", "12")))


class Perm(str, Enum):
    READ = "read"
    INJECT_FAULT = "inject_fault"
    BACKUP = "backup"
    INITIATE_RUN = "initiate_run"
    APPROVE_RUN = "approve_run"
    GAMEDAY = "gameday"
    REMEDIATE = "remediate"
    VIEW_AUDIT = "view_audit"
    MANAGE_POLICY = "manage_policy"
    MANAGE_USERS = "manage_users"
    COPILOT = "copilot"


ROLE_PERMS: dict[Role, set[Perm]] = {
    Role.VIEWER: {Perm.READ, Perm.COPILOT},
    Role.OPERATOR: {
        Perm.READ,
        Perm.COPILOT,
        Perm.INJECT_FAULT,
        Perm.BACKUP,
        Perm.INITIATE_RUN,
        Perm.GAMEDAY,
        Perm.REMEDIATE,
    },
    Role.APPROVER: {Perm.READ, Perm.COPILOT, Perm.APPROVE_RUN, Perm.VIEW_AUDIT, Perm.INITIATE_RUN},
    Role.ADMIN: set(Perm),
}


def has_perm(role: Role, perm: Perm) -> bool:
    return perm in ROLE_PERMS[role]


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


# Constant-time-ish dummy hash so unknown usernames cost the same as wrong passwords.
_DUMMY_HASH = bcrypt.hashpw(b"lifeboat-dummy", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def dummy_verify() -> None:
    verify_password("x", _DUMMY_HASH)


def issue_token(secret: str, username: str, role: Role, ttl_minutes: int) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=ttl_minutes)
    token = jwt.encode(
        {"sub": username, "role": role.value, "iat": now, "exp": exp, "jti": uuid.uuid4().hex, "iss": "lifeboat"},
        secret,
        algorithm="HS256",
    )
    return token, exp


def decode_token(secret: str, token: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"], issuer="lifeboat", options={"require": ["exp", "sub"]})


class LoginRateLimiter:
    """Sliding-window limiter for failed logins (per username and per client IP)."""

    def __init__(self, max_failures: int = 5, window_s: int = 300) -> None:
        self.max, self.window = max_failures, window_s
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q = self.failures[key]
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, *keys: str) -> bool:
        return any(len(self._prune(k)) >= self.max for k in keys)

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._prune(k).append(time.monotonic())

    def reset(self, *keys: str) -> None:
        for k in keys:
            self.failures.pop(k, None)
