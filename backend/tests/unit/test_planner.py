from app.engine import planner
from tests.factories import region, snapshot


def test_steady_state():
    p = planner.generate(snapshot())
    assert p["recommendation"] == "none" and p["scenario"] == "steady_state"


def test_region_outage_recommends_failover_with_ordered_steps():
    snap = snapshot(
        regions={
            "aws-us-east-1": region(
                "aws-us-east-1",
                status="down",
                db_role="primary",
                failing=["app", "database", "network"],
                designated="primary",
            ),
            "azure-eastus": region("azure-eastus"),
        }
    )
    p = planner.generate(snap)
    assert p["recommendation"] == "failover" and p["scenario"] == "region_outage"
    assert [s["action"] for s in p["steps"]] == [
        "detect",
        "assess",
        "validate",
        "approve",
        "promote",
        "switch",
        "verify",
    ]
    assert p["target_region"] == "azure-eastus" and p["within_rto"] is True
    assert all(pc["ok"] for pc in p["preconditions"])


def test_service_failure_recommends_self_heal_first():
    snap = snapshot(
        regions={
            "aws-us-east-1": region(
                "aws-us-east-1", status="down", db_role="primary", failing=["app"], designated="primary"
            ),
            "azure-eastus": region("azure-eastus"),
        }
    )
    p = planner.generate(snap)
    assert p["recommendation"] == "remediate" and p["steps"][0]["action"] == "restart_service"
    assert p["estimated_rpo_s"] == 0


def test_rpo_risk_flagged():
    snap = snapshot(
        rpo_exposure_s=90.0,
        regions={
            "aws-us-east-1": region(
                "aws-us-east-1", status="down", db_role="primary", failing=["database"], designated="primary"
            ),
            "azure-eastus": region("azure-eastus"),
        },
    )
    p = planner.generate(snap)
    assert p["scenario"] == "database_failure" and p["within_rpo"] is False
    assert any("exceeds RPO" in r for r in p["risks"])


def test_dual_failure_restores_from_backup():
    snap = snapshot(
        regions={
            "aws-us-east-1": region(
                "aws-us-east-1", status="down", db_role="primary", failing=["network"], designated="primary"
            ),
            "azure-eastus": region("azure-eastus", status="down"),
        }
    )
    assert planner.generate(snap)["recommendation"] == "restore_from_backup"


def test_failback_when_primary_recovered():
    snap = snapshot(
        active_region="azure-eastus",
        rpo_exposure_s=None,
        regions={
            "aws-us-east-1": region("aws-us-east-1", db_role="fenced", designated="primary"),
            "azure-eastus": region("azure-eastus", db_role="primary", lag=0.0),
        },
    )
    p = planner.generate(snap)
    assert p["recommendation"] == "failback" and p["target_region"] == "aws-us-east-1" and p["estimated_rpo_s"] == 0


def test_standby_impaired_and_replication_degraded():
    down = snapshot(
        regions={
            "aws-us-east-1": region("aws-us-east-1", db_role="primary", designated="primary"),
            "azure-eastus": region("azure-eastus", status="down", failing=["database"]),
        }
    )
    assert planner.generate(down)["recommendation"] == "repair_standby"
    assert planner.generate(snapshot(rpo_exposure_s=25.0))["recommendation"] == "repair_replication"
