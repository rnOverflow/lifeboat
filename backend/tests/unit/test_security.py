import jwt
import pytest

from app.models import Role
from app.security import (
    LoginRateLimiter,
    Perm,
    decode_token,
    has_perm,
    hash_password,
    issue_token,
    verify_password,
)


def test_password_hashing():
    h = hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-hash")


def test_rbac_matrix():
    assert has_perm(Role.VIEWER, Perm.READ) and not has_perm(Role.VIEWER, Perm.INJECT_FAULT)
    assert has_perm(Role.OPERATOR, Perm.INITIATE_RUN) and not has_perm(Role.OPERATOR, Perm.APPROVE_RUN)
    assert has_perm(Role.APPROVER, Perm.APPROVE_RUN) and not has_perm(Role.APPROVER, Perm.INJECT_FAULT)
    assert all(has_perm(Role.ADMIN, p) for p in Perm)


def test_jwt_roundtrip_and_tamper():
    tok, _ = issue_token("s" * 40, "alice", Role.OPERATOR, 5)
    claims = decode_token("s" * 40, tok)
    assert claims["sub"] == "alice" and claims["role"] == "operator"
    with pytest.raises(jwt.PyJWTError):
        decode_token("t" * 40, tok)
    expired, _ = issue_token("s" * 40, "alice", Role.OPERATOR, -1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token("s" * 40, expired)


def test_rate_limiter():
    rl = LoginRateLimiter(max_failures=3, window_s=60)
    for _ in range(3):
        assert not rl.blocked("u")
        rl.fail("u")
    assert rl.blocked("u")
    rl.reset("u")
    assert not rl.blocked("u")
