"""Persistence model.

Two groups of tables:
* Control plane (users, audit, regions, incidents, runs, backups, policies, game days) - what the DR
  platform knows and decides.
* Simulation data plane (sim_*) - only used by the Local provider. It holds the "real" state of the
  simulated AWS and Azure regions: faults, database role and the replicated transaction log.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Role(str, enum.Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    APPROVER = "approver"
    ADMIN = "admin"


class RegionStatus(str, enum.Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class DbRole(str, enum.Enum):
    PRIMARY = "primary"
    REPLICA = "replica"
    FENCED = "fenced"  # old primary after failover: must be resynced before it can serve again


class FaultType(str, enum.Enum):
    CLOUD_OUTAGE = "cloud_outage"
    DATABASE_FAILURE = "database_failure"
    NETWORK_FAILURE = "network_failure"
    SERVICE_FAILURE = "service_failure"
    REPLICATION_FAILURE = "replication_failure"


class RunKind(str, enum.Enum):
    FAILOVER = "failover"
    FAILBACK = "failback"


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING = "waiting"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(_enum(Role))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Append-only, hash-chained audit trail (tamper evident)."""

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class Region(Base):
    """Control-plane view of each region, as observed by the health monitor."""

    __tablename__ = "regions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    cloud: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(64))
    designated_role: Mapped[str] = mapped_column(String(16))  # primary/secondary per DR design
    status: Mapped[RegionStatus] = mapped_column(_enum(RegionStatus), default=RegionStatus.UP)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_healthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_probe: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    db_role: Mapped[str | None] = mapped_column(String(16))
    replication_lag_s: Mapped[float | None] = mapped_column(Float)
    last_applied_seq: Mapped[int | None] = mapped_column(Integer)


class HealthSample(Base):
    __tablename__ = "health_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    healthy: Mapped[bool] = mapped_column(Boolean)
    app_ok: Mapped[bool] = mapped_column(Boolean)
    db_ok: Mapped[bool] = mapped_column(Boolean)
    network_ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[float] = mapped_column(Float)
    replication_lag_s: Mapped[float | None] = mapped_column(Float)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/mitigated/resolved
    summary: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # first failed probe
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # threshold crossed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DrPolicy(Base):
    __tablename__ = "dr_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    rto_target_s: Mapped[int] = mapped_column(Integer)
    rpo_target_s: Mapped[int] = mapped_column(Integer)
    max_replication_lag_s: Mapped[int] = mapped_column(Integer)
    backup_max_age_s: Mapped[int] = mapped_column(Integer)
    auto_initiate_failover: Mapped[bool] = mapped_column(Boolean, default=True)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    two_person_rule: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str] = mapped_column(String(64))


class Backup(Base):
    __tablename__ = "backups"
    id: Mapped[int] = mapped_column(primary_key=True)
    region: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer)
    record_count: Mapped[int] = mapped_column(Integer)
    max_seq: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="created")  # created/verified/corrupt
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RecoveryRun(Base):
    __tablename__ = "recovery_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[RunKind] = mapped_column(_enum(RunKind))
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus), index=True)
    source_region: Mapped[str] = mapped_column(String(64))
    target_region: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    game_day_id: Mapped[int | None] = mapped_column(Integer)
    initiated_by: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", order_by="RunStep.position", cascade="all, delete-orphan"
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recovery_runs.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(128))
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus), default=StepStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run: Mapped[RecoveryRun] = relationship(back_populates="steps")


class GameDay(Base):
    __tablename__ = "game_days"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    scenario: Mapped[str] = mapped_column(String(32))
    target_region: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # running/completed/failed/aborted
    phase: Mapped[str] = mapped_column(String(32))
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    include_failback: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failover_run_id: Mapped[int | None] = mapped_column(Integer)
    failback_run_id: Mapped[int | None] = mapped_column(Integer)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class KeyValue(Base):
    """Small durable state: active traffic target, workload ack watermark, etc."""

    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


# ---------------------------------------------------------------- simulation data plane


class SimRegion(Base):
    __tablename__ = "sim_regions"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cloud: Mapped[str] = mapped_column(String(16))
    db_role: Mapped[DbRole] = mapped_column(_enum(DbRole))
    service_restarts: Mapped[int] = mapped_column(Integer, default=0)


class SimFault(Base):
    __tablename__ = "sim_faults"
    id: Mapped[int] = mapped_column(primary_key=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    fault_type: Mapped[FaultType] = mapped_column(_enum(FaultType))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    injected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    injected_by: Mapped[str] = mapped_column(String(64))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_by: Mapped[str | None] = mapped_column(String(64))
    game_day_id: Mapped[int | None] = mapped_column(Integer)


class SimTxn(Base):
    """A business transaction stored in a region's (simulated) database.

    The same `seq` exists once per region once it has been replicated.
    """

    __tablename__ = "sim_txns"
    __table_args__ = (UniqueConstraint("region", "seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # on the writing primary
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # in this region
    origin_region: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
