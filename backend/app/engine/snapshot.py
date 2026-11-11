"""Immutable view of the DR state. Readiness, planning and the copilot are pure functions over it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RegionView:
    name: str
    cloud: str
    display_name: str
    designated_role: str
    status: str
    db_role: str | None
    replication_lag_s: float | None
    last_applied_seq: int | None
    failing_components: list[str]
    first_failure_at: datetime | None
    last_healthy_at: datetime | None
    latency_ms: float | None


@dataclass
class PolicyView:
    rto_target_s: int
    rpo_target_s: int
    max_replication_lag_s: int
    backup_max_age_s: int
    auto_initiate_failover: bool
    require_approval: bool
    two_person_rule: bool


@dataclass
class Snapshot:
    now: datetime
    policy: PolicyView
    regions: dict[str, RegionView]
    active_region: str
    designated_primary: str
    designated_secondary: str
    rpo_exposure_s: float | None
    workload: dict[str, Any]
    latest_backup: dict[str, Any] | None
    latest_verified_backup_age_s: float | None
    last_successful_drill_at: datetime | None
    measured_rto_s: list[float]
    open_incidents: list[dict[str, Any]] = field(default_factory=list)
    active_run: dict[str, Any] | None = None

    @property
    def standby_region(self) -> str:
        return next(n for n in self.regions if n != self.active_region)

    @property
    def active(self) -> RegionView:
        return self.regions[self.active_region]

    @property
    def standby(self) -> RegionView:
        return self.regions[self.standby_region]

    @property
    def failed_over(self) -> bool:
        return self.active_region != self.designated_primary

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["standby_region"] = self.standby_region
        d["failed_over"] = self.failed_over
        return d
