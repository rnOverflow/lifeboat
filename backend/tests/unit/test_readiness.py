from app.engine import readiness
from tests.factories import region, snapshot


def test_fully_ready_scores_100():
    r = readiness.compute(snapshot())
    assert r["score"] == 100 and r["grade"] == "A" and r["status"] == "ready" and r["blockers"] == []


def test_standby_down_is_blocker():
    snap = snapshot(
        regions={
            "aws-us-east-1": region("aws-us-east-1", db_role="primary", designated="primary"),
            "azure-eastus": region("azure-eastus", status="down", lag=None),
        }
    )
    r = readiness.compute(snap)
    assert r["status"] != "ready"
    assert any("No healthy standby" in b for b in r["blockers"])
    comp = {c["key"]: c for c in r["components"]}
    assert comp["standby_health"]["score"] == 0


def test_rpo_breach_zeroes_replication_component():
    r = readiness.compute(snapshot(rpo_exposure_s=120.0))
    comp = {c["key"]: c for c in r["components"]}
    assert comp["replication"]["score"] == 0
    assert any("BREACHES" in b for b in r["blockers"])


def test_lag_between_budget_and_rpo_partial_credit():
    comp = {c["key"]: c for c in readiness.compute(snapshot(rpo_exposure_s=20.0))["components"]}
    assert comp["replication"]["score"] == 15


def test_backups_and_testing_affect_score():
    r = readiness.compute(
        snapshot(
            latest_backup=None, latest_verified_backup_age_s=None, last_successful_drill_at=None, measured_rto_s=[]
        )
    )
    comp = {c["key"]: c for c in r["components"]}
    assert comp["backups"]["score"] == 0 and comp["testing"]["score"] == 0 and comp["rto"]["score"] == 7
    corrupt = readiness.compute(snapshot(latest_backup={"id": 2, "status": "corrupt", "age_s": 1}))
    assert {c["key"]: c for c in corrupt["components"]}["backups"]["score"] == 2


def test_after_failover_protects_new_active_and_flags_fenced_standby():
    snap = snapshot(
        active_region="azure-eastus",
        regions={
            "aws-us-east-1": region("aws-us-east-1", db_role="fenced", lag=None, designated="primary"),
            "azure-eastus": region("azure-eastus", db_role="primary", lag=0.0),
        },
        rpo_exposure_s=None,
    )
    r = readiness.compute(snap)
    assert r["protected_region"] == "azure-eastus" and r["standby_region"] == "aws-us-east-1"
    assert any("resync" in b for b in r["blockers"])


def test_rto_unproven_and_slow():
    comp = {c["key"]: c for c in readiness.compute(snapshot(measured_rto_s=[900.0]))["components"]}
    assert comp["rto"]["score"] == 0


def test_active_region_down_is_blocker():
    snap = snapshot(
        regions={
            "aws-us-east-1": region("aws-us-east-1", status="down", db_role="primary", designated="primary"),
            "azure-eastus": region("azure-eastus"),
        }
    )
    r = readiness.compute(snap)
    assert r["status"] != "ready" and any("is DOWN" in b for b in r["blockers"])
