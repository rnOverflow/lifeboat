from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.engine.snapshot import PolicyView, RegionView, Snapshot

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def region(
    name: str,
    status: str = "up",
    db_role: str = "replica",
    lag: float | None = 1.0,
    failing: list[str] | None = None,
    designated: str = "secondary",
) -> RegionView:
    return RegionView(
        name=name,
        cloud="aws" if name.startswith("aws") else "azure",
        display_name=name,
        designated_role=designated,
        status=status,
        db_role=db_role,
        replication_lag_s=lag,
        last_applied_seq=100,
        failing_components=failing or [],
        first_failure_at=None,
        last_healthy_at=NOW,
        latency_ms=10.0,
    )


def snapshot(**kw) -> Snapshot:
    policy = PolicyView(
        rto_target_s=300,
        rpo_target_s=30,
        max_replication_lag_s=15,
        backup_max_age_s=3600,
        auto_initiate_failover=True,
        require_approval=True,
        two_person_rule=True,
    )
    regions = kw.pop("regions", None) or {
        "aws-us-east-1": region("aws-us-east-1", db_role="primary", lag=0.0, designated="primary"),
        "azure-eastus": region("azure-eastus"),
    }
    base = dict(
        now=NOW,
        policy=policy,
        regions=regions,
        active_region="aws-us-east-1",
        designated_primary="aws-us-east-1",
        designated_secondary="azure-eastus",
        rpo_exposure_s=2.0,
        workload={},
        latest_backup={"id": 1, "status": "verified", "age_s": 60},
        latest_verified_backup_age_s=60.0,
        last_successful_drill_at=NOW - timedelta(days=1),
        measured_rto_s=[42.0],
        open_incidents=[],
        active_run=None,
    )
    base.update(kw)
    return Snapshot(**base)
