"""Composition root for the DR engine: providers + monitor + orchestrator + game days + the tick loop."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import audit
from app.clock import Clock, aware
from app.config import Settings
from app.db import Database
from app.engine import readiness
from app.engine.gameday import GameDayEngine
from app.engine.monitor import HealthMonitor, MonitorEvent
from app.engine.orchestrator import Orchestrator, RunError
from app.engine.snapshot import PolicyView, RegionView, Snapshot
from app.models import (
    Backup,
    DbRole,
    DrPolicy,
    GameDay,
    Incident,
    RecoveryRun,
    Region,
    RegionStatus,
    RunKind,
    RunStatus,
)
from app.observability import (
    ACTIVE_REGION,
    BACKUP_AGE,
    INCIDENTS_OPEN,
    READINESS,
    RPO_EXPOSURE,
    TICK_DURATION,
    WORKLOAD_WRITES,
)
from app.providers.base import RegionProvider, TrafficManager

log = logging.getLogger(__name__)
REMEDIATION_GRACE_S = 15


class Runtime:
    def __init__(self, settings: Settings, db: Database, clock: Clock) -> None:
        self.settings = settings
        self.db = db
        self.clock = clock
        self.lock = threading.RLock()
        self.sim = None
        self.regions: dict[str, RegionProvider] = {}
        self.traffic: TrafficManager
        self._build_providers()
        self.monitor = HealthMonitor(self)
        self.orchestrator = Orchestrator(self)
        self.gamedays = GameDayEngine(self)
        self._task: asyncio.Task | None = None
        self._leader_conn = None
        self.last_tick_at = None
        self.tick_errors = 0

    # ------------------------------------------------------------------ wiring
    def _build_providers(self) -> None:
        st = self.settings
        if st.provider_mode == "local":
            from app.providers.local import LocalRegion, LocalSimulation, LocalTrafficManager, RegionSpec

            specs = [
                RegionSpec(st.primary_region, "aws", "AWS us-east-1", DbRole.PRIMARY),
                RegionSpec(st.secondary_region, "azure", "Azure East US", DbRole.REPLICA),
            ]
            self.sim = LocalSimulation(
                self.db, self.clock, specs, st.sim_replication_delay_s, st.sim_write_interval_s, st.backup_dir
            )
            self.regions = {sp.name: LocalRegion(self.sim, sp) for sp in specs}
            self.traffic = LocalTrafficManager(self.sim)
        else:
            from app.providers.cloud import build_cloud_providers

            self.regions, self.traffic = build_cloud_providers(st)

    def bootstrap(self) -> None:
        now = self.clock.now()
        with self.db.session() as s:
            for name, prov in self.regions.items():
                if not s.scalar(select(Region).where(Region.name == name)):
                    s.add(
                        Region(
                            name=name,
                            cloud=prov.cloud,
                            display_name=prov.display_name,
                            designated_role="primary" if name == self.settings.primary_region else "secondary",
                            status=RegionStatus.UP,
                            consecutive_failures=0,
                            consecutive_successes=0,
                            last_probe={},
                        )
                    )
            if not s.scalar(select(DrPolicy).where(DrPolicy.name == "default")):
                s.add(
                    DrPolicy(
                        name="default",
                        rto_target_s=300,
                        rpo_target_s=30,
                        max_replication_lag_s=15,
                        backup_max_age_s=3600,
                        auto_initiate_failover=True,
                        require_approval=True,
                        two_person_rule=True,
                        updated_at=now,
                        updated_by="system",
                    )
                )
        if self.sim:
            self.sim.bootstrap(self.settings.primary_region)

    # ------------------------------------------------------------------ queries
    def policy(self, s: Session) -> DrPolicy:
        p = s.scalar(select(DrPolicy).where(DrPolicy.name == "default"))
        assert p is not None
        return p

    def workload_state(self) -> dict[str, Any]:
        return self.sim.workload_state() if self.sim else {}

    def snapshot(self, s: Session) -> Snapshot:
        now = self.clock.now()
        p = self.policy(s)
        regions: dict[str, RegionView] = {}
        last_commit: dict[str, float | None] = {}
        for r in s.scalars(select(Region).order_by(Region.id)):
            lp = r.last_probe or {}
            db = lp.get("db") or {}
            last_commit[r.name] = (
                aware(datetime.fromisoformat(db["last_committed_at"])).timestamp()
                if db.get("last_committed_at")
                else None
            )
            regions[r.name] = RegionView(
                name=r.name,
                cloud=r.cloud,
                display_name=r.display_name,
                designated_role=r.designated_role,
                status=r.status.value,
                db_role=r.db_role,
                replication_lag_s=r.replication_lag_s,
                last_applied_seq=r.last_applied_seq,
                failing_components=lp.get("failing", []),
                first_failure_at=aware(r.first_failure_at),
                last_healthy_at=aware(r.last_healthy_at),
                latency_ms=lp.get("latency_ms"),
            )
        active = self.traffic.active_region()
        standby = next(n for n in regions if n != active)
        wl = self.workload_state()
        exposure: float | None
        if wl.get("last_acked_at") and wl.get("last_acked_region") == active and last_commit.get(standby):
            sb_seq = regions[standby].last_applied_seq or 0
            exposure = (
                0.0
                if sb_seq >= (wl.get("last_acked_seq") or 0)
                else max(0.0, wl["last_acked_at"] - last_commit[standby])
            )
        else:
            exposure = regions[standby].replication_lag_s
        if regions[standby].db_role != "replica":
            exposure = None
        latest = s.scalar(select(Backup).order_by(Backup.id.desc()))
        verified = s.scalar(select(Backup).where(Backup.status == "verified").order_by(Backup.id.desc()))
        drills = list(
            s.scalars(
                select(RecoveryRun)
                .where(RecoveryRun.status == RunStatus.COMPLETED, RecoveryRun.kind == RunKind.FAILOVER)
                .order_by(RecoveryRun.id)
            )
        )
        last_gd = s.scalar(select(GameDay).where(GameDay.status == "completed").order_by(GameDay.id.desc()))
        drill_times = [aware(r.completed_at) for r in drills if r.completed_at]
        if last_gd and last_gd.finished_at:
            drill_times.append(aware(last_gd.finished_at))
        incidents = [
            {
                "id": i.id,
                "region": i.region,
                "kind": i.kind,
                "severity": i.severity,
                "status": i.status,
                "summary": i.summary,
                "detected_at": aware(i.detected_at).isoformat(),
            }
            for i in s.scalars(
                select(Incident).where(Incident.status.in_(("open", "mitigated"))).order_by(Incident.id.desc())
            )
        ]
        run = self.orchestrator.active_run(s)
        return Snapshot(
            now=now,
            policy=PolicyView(
                p.rto_target_s,
                p.rpo_target_s,
                p.max_replication_lag_s,
                p.backup_max_age_s,
                p.auto_initiate_failover,
                p.require_approval,
                p.two_person_rule,
            ),
            regions=regions,
            active_region=active,
            designated_primary=self.settings.primary_region,
            designated_secondary=self.settings.secondary_region,
            rpo_exposure_s=exposure,
            workload=wl,
            latest_backup=None
            if latest is None
            else {
                "id": latest.id,
                "status": latest.status,
                "region": latest.region,
                "age_s": (now - aware(latest.created_at)).total_seconds(),
            },
            latest_verified_backup_age_s=None
            if verified is None
            else (now - aware(verified.created_at)).total_seconds(),
            last_successful_drill_at=max(drill_times) if drill_times else None,
            measured_rto_s=[r.metrics["rto_s"] for r in drills if (r.metrics or {}).get("rto_s") is not None],
            open_incidents=incidents,
            active_run=None if run is None else {"id": run.id, "kind": run.kind.value, "status": run.status.value},
        )

    # ------------------------------------------------------------------ the tick
    def tick(self) -> list[MonitorEvent]:
        t0 = time.perf_counter()
        with self.lock:
            if self.sim:
                self.sim.workload_tick()
                self.sim.replicate_tick()
            with self.db.session() as s:
                events = self.monitor.run(s)
            with self.db.session() as s:
                self._react(s, events)
            with self.db.session() as s:
                self.orchestrator.advance_active(s)
            with self.db.session() as s:
                self.gamedays.advance_running(s)
            with self.db.session() as s:
                self._export_metrics(s)
        self.last_tick_at = self.clock.now()
        TICK_DURATION.observe(time.perf_counter() - t0)
        return events

    def _react(self, s: Session, events: list[MonitorEvent]) -> None:
        """Automatic responses: self-heal service failures, auto-initiate failover for regional failures."""
        policy = self.policy(s)
        active = self.traffic.active_region()
        now = self.clock.now()
        for ev in events:
            if ev.kind != "region_down" or ev.region != active:
                continue
            inc = s.get(Incident, ev.incident_id)
            assert inc is not None
            if inc.kind == "service_failure":
                try:
                    res = self.regions[ev.region].restart_service()
                    inc.details = {
                        **inc.details,
                        "auto_remediation": {"action": "restart_service", "at": now.isoformat(), "result": res},
                    }
                    audit.record(
                        s,
                        ts=now,
                        actor="system:self-heal",
                        action="service.restart",
                        target=ev.region,
                        details={"incident": inc.id, **res},
                    )
                except Exception as e:  # noqa: BLE001
                    inc.details = {**inc.details, "auto_remediation": {"error": str(e), "at": now.isoformat()}}
                continue
            self._maybe_auto_failover(s, inc, policy)

        # Escalate service failures that self-healing did not fix.
        for inc in s.scalars(
            select(Incident).where(
                Incident.status == "open", Incident.kind == "service_failure", Incident.region == active
            )
        ):
            rem = (inc.details or {}).get("auto_remediation")
            if (
                rem
                and "at" in rem
                and not (inc.details or {}).get("escalated")
                and (now - datetime.fromisoformat(rem["at"])).total_seconds() >= REMEDIATION_GRACE_S
            ):
                inc.details = {**inc.details, "escalated": True}
                self._maybe_auto_failover(s, inc, policy)

    def _maybe_auto_failover(self, s: Session, inc: Incident, policy: DrPolicy) -> None:
        if not policy.auto_initiate_failover:
            return
        standby = next(n for n in self.regions if n != inc.region)
        sb = s.scalar(select(Region).where(Region.name == standby))
        if sb is None or sb.status != RegionStatus.UP:
            inc.details = {**inc.details, "failover_blocked": f"standby {standby} not healthy"}
            return
        try:
            run = self.orchestrator.create(
                s, RunKind.FAILOVER, f"auto: {inc.summary}", "system:monitor", incident_id=inc.id
            )
            inc.details = {**inc.details, "failover_run_id": run.id}
        except RunError as e:
            inc.details = {**inc.details, "failover_blocked": str(e)}

    def _export_metrics(self, s: Session) -> None:
        snap = self.snapshot(s)
        rd = readiness.compute(snap)
        READINESS.set(rd["score"])
        if snap.rpo_exposure_s is not None:
            RPO_EXPOSURE.set(snap.rpo_exposure_s)
        for n in self.regions:
            ACTIVE_REGION.labels(n).set(1 if n == snap.active_region else 0)
        if snap.latest_verified_backup_age_s is not None:
            BACKUP_AGE.set(snap.latest_verified_backup_age_s)
        INCIDENTS_OPEN.set(len([i for i in snap.open_incidents if i["status"] == "open"]))
        wl = snap.workload or {}
        WORKLOAD_WRITES.labels("ok").set(wl.get("ok", 0))
        WORKLOAD_WRITES.labels("failed").set(wl.get("failed", 0))

    # ------------------------------------------------------------------ background loop with leader election
    def _try_become_leader(self) -> bool:
        if self.db.engine.dialect.name != "postgresql":
            return True
        if self._leader_conn is None:
            self._leader_conn = self.db.engine.connect()
        got = self._leader_conn.execute(text("SELECT pg_try_advisory_lock(727000)")).scalar()
        self._leader_conn.commit()
        return bool(got)

    async def run_forever(self) -> None:
        leader = False
        while True:
            try:
                if not leader:
                    leader = await asyncio.to_thread(self._try_become_leader)
                    if leader:
                        log.info("engine leader lock acquired - this replica runs the DR engine")
                if leader:
                    await asyncio.to_thread(self.tick)
            except Exception:  # noqa: BLE001 - keep the loop alive; surface via metrics/logs
                self.tick_errors += 1
                log.exception("engine tick failed")
                if self._leader_conn is not None:
                    try:
                        self._leader_conn.close()
                    finally:
                        self._leader_conn, leader = None, False
            await asyncio.sleep(self.settings.tick_interval_s)

    def start(self) -> None:
        if self.settings.scheduler_enabled and self._task is None:
            self._task = asyncio.get_event_loop().create_task(self.run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._leader_conn is not None:
            self._leader_conn.close()
            self._leader_conn = None

    def is_stale(self) -> bool:
        if not self.settings.scheduler_enabled or self.last_tick_at is None:
            return False
        return self.clock.now() - self.last_tick_at > timedelta(seconds=self.settings.tick_interval_s * 10)
