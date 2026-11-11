"""The required end-to-end demo, driven entirely through the HTTP API.

Healthy AWS -> simulate AWS outage -> detect -> check RTO/RPO -> validate Azure -> approve -> promote ->
switch traffic -> verify -> RTO/RPO -> report -> recover AWS -> failback -> verify
"""
import pytest

pytestmark = pytest.mark.e2e
AWS, AZ = "aws-us-east-1", "azure-eastus"


def regions(api, hdr):
    st = api.get("/api/v1/status", headers=hdr).json()
    return st, {r["name"]: r for r in st["regions"]}


def test_full_dr_lifecycle(api):
    op, ap, viewer = api.as_("operator"), api.as_("approver"), api.as_("viewer")

    # 1. Healthy AWS serving traffic, Azure replicating; verified backup exists
    api.tick(10)
    b = api.post("/api/v1/backups", json={}, headers=op).json()
    api.post(f"/api/v1/backups/{b['id']}/verify", headers=op)
    st, r = regions(api, viewer)
    assert st["active_region"] == AWS and r[AWS]["status"] == "up" and r[AWS]["db_role"] == "primary"
    assert r[AZ]["db_role"] == "replica" and r[AZ]["replication_lag_s"] <= 15
    ready_before = st["readiness"]["score"]

    # 2. Simulate AWS outage
    assert api.post("/api/v1/simulator/faults", json={"region": AWS, "fault_type": "cloud_outage"},
                    headers=op).status_code == 201

    # 3. Detect failure (3 consecutive failed probes) -> incident + auto-initiated failover run
    api.tick(2)
    _, r = regions(api, viewer)
    assert r[AWS]["status"] == "degraded"
    api.tick(1)
    st, r = regions(api, viewer)
    assert r[AWS]["status"] == "down"
    inc = api.get("/api/v1/incidents?status=open", headers=viewer).json()[0]
    assert inc["region"] == AWS and inc["kind"] == "region_outage" and inc["severity"] == "critical"
    run = api.get("/api/v1/runs", headers=viewer).json()[0]
    assert run["kind"] == "failover" and run["status"] == "awaiting_approval"
    steps = {s["name"]: s for s in api.get(f"/api/v1/runs/{run['id']}", headers=viewer).json()["steps"]}

    # 4. Check RTO/RPO budget and 5. Validate Azure happened before the approval gate
    assert steps["detect"]["status"] == "succeeded" and steps["detect"]["output"]["confirmed"]
    assess = steps["assess"]["output"]
    assert assess["rto_target_s"] == 300 and assess["rpo_exposure_s"] is not None and not assess["rpo_at_risk"]
    assert steps["validate"]["status"] == "succeeded" and all(steps["validate"]["output"]["checks"].values())
    assert steps["approve"]["status"] == "waiting"
    plan = api.get("/api/v1/plan", headers=viewer).json()
    assert plan["recommendation"] == "failover" and plan["target_region"] == AZ

    # Traffic still on dead AWS: customer writes failing
    assert api.get("/api/v1/status", headers=viewer).json()["workload"]["last_error"].startswith(AWS)

    # 6. Approve failover (operator cannot; approver can) -> 7. promote -> 8. switch -> 9. verify
    assert api.post(f"/api/v1/runs/{run['id']}/approve", json={}, headers=op).status_code == 403
    api.tick(2)  # time passes while the approver is paged
    res = api.post(f"/api/v1/runs/{run['id']}/approve", json={"comment": "confirmed AWS outage"}, headers=ap)
    assert res.status_code == 200
    done = res.json()
    assert done["status"] == "completed", done["error"]
    steps = {s["name"]: s for s in done["steps"]}
    assert all(s["status"] == "succeeded" for s in steps.values())
    assert steps["promote"]["output"]["fence"]["role"] == "fenced"
    assert steps["switch"]["output"]["to"] == AZ
    assert steps["verify"]["output"]["synthetic_ok"]
    st, r = regions(api, viewer)
    assert st["active_region"] == AZ and r[AZ]["db_role"] == "primary"
    api.tick(2)
    wl = api.get("/api/v1/status", headers=viewer).json()["workload"]
    assert wl["last_acked_region"] == AZ and wl["last_error"] is None  # customers served from Azure

    # 10. RTO/RPO measured and 11. report generated
    m = done["metrics"]
    assert m["rto_s"] == pytest.approx(4.0) and m["rto_met"]  # first failed probe t=11 -> verified t=15
    assert m["time_to_detect_s"] == 2.0 and m["time_to_approve_s"] == 2.0
    assert m["rpo_s"] == 2.0 and m["data_loss_txns"] == 2 and m["rpo_met"]
    md = api.get(f"/api/v1/runs/{run['id']}/report.md", headers=viewer)
    assert md.status_code == 200 and "## Objectives" in md.text and "PASS" in md.text
    assert done["report"]["data_loss"]["transactions"] == 2

    # Readiness reflects lost protection while AWS is down/fenced
    assert api.get("/api/v1/readiness", headers=viewer).json()["score"] < ready_before

    # 12. Recover AWS: it returns FENCED (split-brain guard), not as a second primary
    api.post("/api/v1/simulator/clear", json={"region": AWS}, headers=op)
    api.tick(3)
    st, r = regions(api, viewer)
    assert r[AWS]["status"] == "up" and r[AWS]["db_role"] == "fenced"
    assert st["active_region"] == AZ
    assert api.get("/api/v1/plan", headers=viewer).json()["recommendation"] == "failback"

    # 13. Failback: resync (discard the 2 divergent writes) -> validate lag -> approve -> drain/swap -> switch -> verify
    fb = api.post("/api/v1/runs/failback", json={"reason": "AWS recovered"}, headers=op)
    assert fb.status_code == 201
    fb = fb.json()
    fsteps = {s["name"]: s for s in fb["steps"]}
    assert fsteps["resync"]["output"]["divergent_discarded"] == 2  # matches measured data loss exactly
    assert fb["status"] == "awaiting_approval"
    fb = api.post(f"/api/v1/runs/{fb['id']}/approve", json={}, headers=ap).json()
    assert fb["status"] == "completed", fb["error"]
    assert fb["metrics"]["data_loss_txns"] == 0 and fb["metrics"]["graceful"] and fb["metrics"]["rpo_s"] == 0

    # 14. Verify: back on AWS, Azure is a healthy replica again, replication flowing, readiness restored
    api.tick(4)
    st, r = regions(api, viewer)
    assert st["active_region"] == AWS and r[AWS]["db_role"] == "primary" and r[AZ]["db_role"] == "replica"
    assert r[AZ]["replication_lag_s"] <= 15
    assert st["workload"]["last_acked_region"] == AWS
    assert api.get("/api/v1/plan", headers=viewer).json()["recommendation"] == "none"
    assert api.get("/api/v1/readiness", headers=viewer).json()["score"] >= ready_before
    assert all(i["status"] == "resolved" for i in api.get("/api/v1/incidents", headers=viewer).json())

    # Every step is in a valid tamper-evident audit trail
    audit = api.get("/api/v1/audit?limit=500", headers=ap).json()
    actions = {a["action"] for a in audit}
    assert {"fault.inject", "incident.open", "failover.initiate", "failover.approve", "failover.promote",
            "traffic.switch", "failover.completed", "fault.clear", "failback.initiate", "failback.resync",
            "failback.approve", "failback.completed"} <= actions
    assert api.get("/api/v1/audit/verify", headers=ap).json()["valid"]
