"""Health monitoring, failure detection (with hysteresis) and replication-lag monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app.models import HealthSample, Incident, Region, RegionStatus
from app.observability import PROBE_LATENCY, REGION_STATUS, REGION_UP, REPLICATION_LAG

if TYPE_CHECKING:
    from app.engine.runtime import Runtime

AVAILABILITY_KINDS = ("region_outage", "database_failure", "network_failure", "service_failure", "degraded")
STATUS_CODE = {RegionStatus.UP: 0, RegionStatus.DEGRADED: 1, RegionStatus.DOWN: 2}
LAG_BREACH_TICKS = 3


@dataclass
class MonitorEvent:
    kind: str  # region_down | region_up | lag_breach | lag_recovered
    region: str
    incident_id: int | None = None
    detail: str = ""


def classify(failing: list[str]) -> str:
    if len(failing) >= 2 or "network" in failing:
        return "region_outage" if len(failing) >= 2 else "network_failure"
    if failing == ["database"]:
        return "database_failure"
    if failing == ["app"]:
        return "service_failure"
    return "degraded"


class HealthMonitor:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self._ticks = 0

    def run(self, s: Session) -> list[MonitorEvent]:
        events: list[MonitorEvent] = []
        now = self.rt.clock.now()
        settings = self.rt.settings
        policy = self.rt.policy(s)
        statuses: dict[str, Any] = {}
        rows = {r.name: r for r in s.scalars(select(Region))}

        for name, prov in self.rt.regions.items():
            region = rows[name]
            probe = prov.probe()
            dbs = prov.db_status() if probe.network.ok else None
            statuses[name] = dbs
            failing = probe.failing_components()
            region.last_probe_at = now
            region.last_probe = {
                **probe.to_dict(),
                "failing": failing,
                "db": None
                if dbs is None
                else {
                    "reachable": dbs.reachable,
                    "role": dbs.role,
                    "last_seq": dbs.last_seq,
                    "last_committed_at": dbs.last_committed_at.isoformat() if dbs.last_committed_at else None,
                },
                "lag_breaches": (region.last_probe or {}).get("lag_breaches", 0),
            }
            if dbs is not None and dbs.reachable:
                region.db_role = dbs.role
                region.last_applied_seq = dbs.last_seq
            s.add(
                HealthSample(
                    ts=now,
                    region=name,
                    healthy=probe.healthy,
                    app_ok=probe.app.ok,
                    db_ok=probe.database.ok,
                    network_ok=probe.network.ok,
                    latency_ms=probe.latency_ms,
                    replication_lag_s=region.replication_lag_s,
                )
            )
            PROBE_LATENCY.labels(name).observe(probe.latency_ms)

            if probe.healthy:
                region.consecutive_failures = 0
                region.consecutive_successes += 1
                region.last_healthy_at = now
                recovered = region.status == RegionStatus.DEGRADED or (
                    region.status == RegionStatus.DOWN and region.consecutive_successes >= settings.recovery_threshold
                )
                if recovered:
                    was_down = region.status == RegionStatus.DOWN
                    region.status = RegionStatus.UP
                    region.first_failure_at = None
                    if was_down:
                        for inc in s.scalars(
                            select(Incident).where(
                                Incident.region == name,
                                Incident.status.in_(("open", "mitigated")),
                                Incident.kind.in_(AVAILABILITY_KINDS),
                            )
                        ):
                            inc.status, inc.resolved_at = "resolved", now
                            audit.record(
                                s,
                                ts=now,
                                actor="system:monitor",
                                action="incident.resolve",
                                target=f"incident:{inc.id}",
                                details={"region": name},
                            )
                        events.append(MonitorEvent("region_up", name))
            else:
                region.consecutive_successes = 0
                region.consecutive_failures += 1
                if region.consecutive_failures == 1:
                    region.first_failure_at = now
                if region.consecutive_failures >= settings.detection_threshold and region.status != RegionStatus.DOWN:
                    region.status = RegionStatus.DOWN
                    active = self.rt.traffic.active_region()
                    kind = classify(failing)
                    inc = Incident(
                        region=name,
                        kind=kind,
                        severity="critical" if name == active else "major",
                        status="open",
                        started_at=region.first_failure_at or now,
                        detected_at=now,
                        summary=f"{region.display_name}: {kind.replace('_', ' ')} "
                        f"({', '.join(failing)} failing for {region.consecutive_failures} probes)",
                        details={"failing": failing, "serving_traffic": name == active, "probe": region.last_probe},
                    )
                    s.add(inc)
                    s.flush()
                    audit.record(
                        s,
                        ts=now,
                        actor="system:monitor",
                        action="incident.open",
                        target=f"incident:{inc.id}",
                        details={"region": name, "kind": kind},
                    )
                    events.append(MonitorEvent("region_down", name, inc.id, kind))
                elif region.status == RegionStatus.UP:
                    region.status = RegionStatus.DEGRADED
            REGION_UP.labels(name, prov.cloud).set(1 if region.status == RegionStatus.UP else 0)
            REGION_STATUS.labels(name).set(STATUS_CODE[region.status])

        # ---- replication lag (only measurable when both ends are reachable)
        primary = next((n for n, d in statuses.items() if d and d.reachable and d.role == "primary"), None)
        for name, dbs in statuses.items():
            region = rows[name]
            if name == primary:
                region.replication_lag_s = 0.0
                continue
            p = statuses.get(primary) if primary else None
            if not (dbs and dbs.reachable and dbs.role == "replica" and p and p.last_committed_at):
                region.replication_lag_s = None
                continue
            if (dbs.last_seq or 0) >= (p.last_seq or 0):
                lag = 0.0
            elif dbs.last_committed_at is None:
                lag = (now - p.last_committed_at).total_seconds()
            else:
                lag = max(0.0, (p.last_committed_at - dbs.last_committed_at).total_seconds())
            region.replication_lag_s = round(lag, 3)
            REPLICATION_LAG.labels(name).set(lag)
            breaches = (region.last_probe or {}).get("lag_breaches", 0)
            breaches = breaches + 1 if lag > policy.max_replication_lag_s else 0
            region.last_probe = {**region.last_probe, "lag_breaches": breaches}
            open_lag = s.scalar(
                select(Incident).where(
                    Incident.region == name, Incident.kind == "replication_lag", Incident.status == "open"
                )
            )
            if breaches >= LAG_BREACH_TICKS and open_lag is None:
                inc = Incident(
                    region=name,
                    kind="replication_lag",
                    severity="warning",
                    status="open",
                    started_at=now - timedelta(seconds=self.rt.settings.tick_interval_s * breaches),
                    detected_at=now,
                    summary=f"Replication to {region.display_name} lagging {lag:.1f}s "
                    f"(budget {policy.max_replication_lag_s}s) - RPO at risk",
                    details={"lag_s": lag},
                )
                s.add(inc)
                s.flush()
                audit.record(
                    s,
                    ts=now,
                    actor="system:monitor",
                    action="incident.open",
                    target=f"incident:{inc.id}",
                    details={"region": name, "kind": "replication_lag", "lag_s": lag},
                )
                events.append(MonitorEvent("lag_breach", name, inc.id))
            elif breaches == 0 and open_lag is not None:
                open_lag.status, open_lag.resolved_at = "resolved", now
                audit.record(
                    s,
                    ts=now,
                    actor="system:monitor",
                    action="incident.resolve",
                    target=f"incident:{open_lag.id}",
                    details={"region": name},
                )
                events.append(MonitorEvent("lag_recovered", name, open_lag.id))

        self._ticks += 1
        if self._ticks % 120 == 0:
            s.execute(delete(HealthSample).where(HealthSample.ts < now - timedelta(hours=2)))
        return events
