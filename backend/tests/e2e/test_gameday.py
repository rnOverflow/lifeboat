import pytest

pytestmark = pytest.mark.e2e
AWS, AZ = "aws-us-east-1", "azure-eastus"


def _run_gameday(api, scenario, region=None, auto_approve=True, ticks=40):
    hdr = api.as_("admin")
    api.tick(5)
    r = api.post("/api/v1/gamedays", json={"name": f"GD {scenario}", "scenario": scenario, "target_region": region,
                                           "auto_approve": auto_approve}, headers=hdr)
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    for _ in range(ticks):
        api.tick(1)
        g = api.get(f"/api/v1/gamedays/{gid}", headers=hdr).json()
        if g["status"] != "running":
            return g
    raise AssertionError(f"game day stuck in phase {g['phase']}")


def test_gameday_cloud_outage_full_cycle(api):
    g = _run_gameday(api, "cloud_outage")
    rep = g["report"]
    assert g["status"] == "completed" and rep["passed"], rep["findings"]
    assert rep["detection_s"] == 3.0  # fault injection -> 3rd failed probe at 1s interval
    assert rep["failover"]["status"] == "completed" and rep["failback"]["status"] == "completed"
    assert rep["failover"]["data_loss_txns"] == rep["failback"]["divergent_discarded"]
    assert api.get("/api/v1/status", headers=api.as_("viewer")).json()["active_region"] == AWS
    phases = [e["phase"] for e in g["timeline"]]
    assert phases[0] == "baseline" and "failback" in phases and phases[-1] == "done"


def test_gameday_database_failure(api):
    g = _run_gameday(api, "database_failure")
    assert g["report"]["passed"] and g["report"]["failover"]["status"] == "completed"


def test_gameday_network_failure(api):
    g = _run_gameday(api, "network_failure")
    assert g["report"]["passed"] and g["report"]["failback"]["status"] == "completed"


def test_gameday_service_failure_self_heals(api):
    g = _run_gameday(api, "service_failure")
    assert g["report"]["passed"] and g["report"]["failover"] is None
    assert any("self-healed" in e["detail"] for e in g["timeline"])


def test_gameday_replication_failure_detected_no_failover(api):
    g = _run_gameday(api, "replication_failure", region=AWS, ticks=60)
    rep = g["report"]
    assert rep["passed"] and rep["failover"] is None and rep["detection_s"] is not None


def test_gameday_without_preapproval_waits_for_human(api):
    hdr, ap = api.as_("operator"), api.as_("approver")
    api.tick(5)
    assert api.post("/api/v1/gamedays", json={"name": "preapproved", "scenario": "cloud_outage", "auto_approve": True},
                    headers=hdr).status_code == 403
    gid = api.post("/api/v1/gamedays", json={"name": "manual approval", "scenario": "cloud_outage"},
                   headers=hdr).json()["id"]
    api.tick(5)
    g = api.get(f"/api/v1/gamedays/{gid}", headers=hdr).json()
    assert g["phase"] == "respond"
    run = api.get(f"/api/v1/runs/{g['failover_run_id']}", headers=hdr).json()
    assert run["status"] == "awaiting_approval"
    api.post(f"/api/v1/runs/{run['id']}/approve", json={}, headers=ap)
    for _ in range(10):
        api.tick(1)
        g = api.get(f"/api/v1/gamedays/{gid}", headers=hdr).json()
        if g["phase"] == "failback":
            break
    fb = api.get(f"/api/v1/runs/{g['failback_run_id']}", headers=hdr).json()
    assert fb["status"] == "awaiting_approval"
    # operator started the game day so they cannot approve; approver can
    assert api.post(f"/api/v1/runs/{fb['id']}/approve", json={}, headers=hdr).status_code == 403
    api.post(f"/api/v1/runs/{fb['id']}/approve", json={}, headers=ap)
    for _ in range(5):
        api.tick(1)
    g = api.get(f"/api/v1/gamedays/{gid}", headers=hdr).json()
    assert g["status"] == "completed" and g["report"]["passed"]


def test_gameday_abort_clears_faults(api):
    hdr = api.as_("admin")
    api.tick(3)
    gid = api.post("/api/v1/gamedays", json={"name": "abort me", "scenario": "network_failure",
                                             "target_region": AZ}, headers=hdr).json()["id"]
    api.tick(1)
    g = api.post(f"/api/v1/gamedays/{gid}/abort", headers=hdr).json()
    assert g["status"] == "aborted"
    assert api.get("/api/v1/simulator/faults?active_only=true", headers=hdr).json() == []
