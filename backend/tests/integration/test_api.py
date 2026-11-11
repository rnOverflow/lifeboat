import pytest

pytestmark = pytest.mark.integration


def test_health_endpoints_and_metrics(api):
    assert api.get("/healthz").json() == {"status": "ok"}
    assert api.get("/readyz").status_code == 200
    api.tick(3)
    m = api.get("/metrics").text
    assert "lifeboat_region_up" in m and "lifeboat_readiness_score" in m and "lifeboat_replication_lag_seconds" in m


def test_auth_required_and_bad_credentials(api):
    assert api.get("/api/v1/status").status_code == 401
    assert api.get("/api/v1/status", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert api.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert api.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope"}).status_code == 401


def test_login_rate_limit(api):
    for _ in range(5):
        api.post("/api/v1/auth/login", json={"username": "viewer", "password": "wrong"})
    r = api.post("/api/v1/auth/login", json={"username": "viewer", "password": "wrong"})
    assert r.status_code == 429


def test_me_and_security_headers(api):
    r = api.get("/api/v1/auth/me", headers=api.as_("operator"))
    assert r.json()["role"] == "operator" and "inject_fault" in r.json()["permissions"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("user,method,path,body,expected", [
    ("viewer", "post", "/api/v1/simulator/faults", {"region": "aws-us-east-1", "fault_type": "cloud_outage"}, 403),
    ("viewer", "post", "/api/v1/backups", {}, 403),
    ("viewer", "post", "/api/v1/runs/failover", {"reason": "nope"}, 403),
    ("viewer", "get", "/api/v1/audit", None, 403),
    ("operator", "post", "/api/v1/runs/1/approve", {"comment": ""}, 403),
    ("operator", "put", "/api/v1/policy", {"rto_target_s": 60, "rpo_target_s": 5, "max_replication_lag_s": 5,
                                            "backup_max_age_s": 600, "auto_initiate_failover": True,
                                            "require_approval": False, "two_person_rule": False}, 403),
    ("approver", "post", "/api/v1/simulator/faults", {"region": "aws-us-east-1", "fault_type": "cloud_outage"}, 403),
    ("operator", "get", "/api/v1/users", None, 403),
])
def test_rbac_denials_are_enforced_and_audited(api, user, method, path, body, expected):
    kw = {"headers": api.as_(user)}
    if body is not None:
        kw["json"] = body
    assert getattr(api, method)(path, **kw).status_code == expected
    audit = api.get("/api/v1/audit?action=authz.denied", headers=api.as_("admin")).json()
    assert audit and audit[0]["actor"] == user and audit[0]["outcome"] == "denied"


def test_status_readiness_plan(api):
    api.tick(5)
    st = api.get("/api/v1/status", headers=api.as_("viewer")).json()
    assert st["mode"] == "local" and st["active_region"] == "aws-us-east-1"
    assert {r["name"]: r["status"] for r in st["regions"]} == {"aws-us-east-1": "up", "azure-eastus": "up"}
    assert st["workload"]["ok"] == 5
    rd = api.get("/api/v1/readiness", headers=api.as_("viewer")).json()
    assert 0 <= rd["score"] <= 100 and len(rd["components"]) == 5
    assert api.get("/api/v1/plan", headers=api.as_("viewer")).json()["recommendation"] == "none"
    hist = api.get("/api/v1/regions/aws-us-east-1/history", headers=api.as_("viewer")).json()
    assert len(hist) == 5 and all(h["healthy"] for h in hist)


def test_backup_lifecycle_and_corruption(api):
    api.tick(4)
    op = api.as_("operator")
    b = api.post("/api/v1/backups", json={}, headers=op).json()
    assert b["status"] == "created" and b["record_count"] == 4
    v = api.post(f"/api/v1/backups/{b['id']}/verify", headers=op).json()
    assert v["status"] == "verified" and v["verification"]["restorable"]
    assert api.post(f"/api/v1/simulator/backups/{b['id']}/corrupt", headers=op).status_code == 200
    v2 = api.post(f"/api/v1/backups/{b['id']}/verify", headers=op).json()
    assert v2["status"] == "corrupt" and not v2["verification"]["checksum_match"]
    rd = api.get("/api/v1/readiness", headers=op).json()
    assert {c["key"]: c for c in rd["components"]}["backups"]["score"] == 2
    # backup from a dead region fails cleanly
    api.post("/api/v1/simulator/faults", json={"region": "azure-eastus", "fault_type": "database_failure"}, headers=op)
    assert api.post("/api/v1/backups", json={"region": "azure-eastus"}, headers=op).status_code == 409


def test_fault_injection_changes_state(api):
    op = api.as_("operator")
    r = api.post("/api/v1/simulator/faults", json={"region": "azure-eastus", "fault_type": "network_failure"},
                 headers=op)
    assert r.status_code == 201
    api.tick(3)
    st = api.get("/api/v1/status", headers=op).json()
    assert {x["name"]: x["status"] for x in st["regions"]}["azure-eastus"] == "down"
    assert st["readiness"]["status"] != "ready"
    assert api.get("/api/v1/plan", headers=op).json()["recommendation"] == "repair_standby"
    assert api.post("/api/v1/simulator/clear", json={"region": "azure-eastus"}, headers=op).json()["cleared"] == 1
    api.tick(2)
    assert {x["name"]: x["status"] for x in api.get("/api/v1/status", headers=op).json()["regions"]}[
        "azure-eastus"] == "up"


def test_policy_update_and_validation(api):
    admin = api.as_("admin")
    body = {"rto_target_s": 120, "rpo_target_s": 10, "max_replication_lag_s": 5, "backup_max_age_s": 600,
            "auto_initiate_failover": False, "require_approval": True, "two_person_rule": True}
    assert api.put("/api/v1/policy", json=body, headers=admin).status_code == 200
    assert api.get("/api/v1/policy", headers=admin).json()["rto_target_s"] == 120
    assert api.put("/api/v1/policy", json={**body, "rto_target_s": 1}, headers=admin).status_code == 422


def test_user_management(api):
    admin = api.as_("admin")
    r = api.post("/api/v1/users", json={"username": "sre.jane", "password": "a-long-enough-pass", "role": "operator"},
                 headers=admin)
    assert r.status_code == 201
    assert api.post("/api/v1/users", json={"username": "x1y", "password": "short", "role": "viewer"},
                    headers=admin).status_code == 422
    jane = api.post("/api/v1/auth/login", json={"username": "sre.jane", "password": "a-long-enough-pass"}).json()
    hdr = {"Authorization": f"Bearer {jane['access_token']}"}
    assert api.get("/api/v1/status", headers=hdr).status_code == 200
    api.patch(f"/api/v1/users/{r.json()['id']}", json={"is_active": False}, headers=admin)
    assert api.get("/api/v1/status", headers=hdr).status_code == 401  # disabled immediately


def test_copilot_endpoint(api):
    api.tick(3)
    r = api.post("/api/v1/copilot/chat", json={"message": "are we ready for a failover?"}, headers=api.as_("viewer"))
    assert r.status_code == 200 and r.json()["engine"] == "rules" and "Readiness" in r.json()["answer"]
    assert api.get("/api/v1/audit?action=copilot", headers=api.as_("admin")).json()[0]["actor"] == "viewer"


def test_audit_chain_verifies(api):
    api.tick(2)
    api.post("/api/v1/backups", json={}, headers=api.as_("operator"))
    v = api.get("/api/v1/audit/verify", headers=api.as_("approver")).json()
    assert v["valid"] and v["entries_checked"] >= 3
