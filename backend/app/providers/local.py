"""Local simulation of a two-cloud deployment.

Nothing here is a mock: every region has a real (simulated) database made of rows in `sim_txns`,
a role (primary / replica / fenced), and a set of injected faults. A synthetic workload writes
through the global traffic manager, an asynchronous replication process ships transactions to
the replica with a configurable delay, and failover really promotes/fences databases. Data loss
during an unplanned failover is therefore *measured*, not invented.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.clock import Clock, aware
from app.db import Database
from app.models import DbRole, FaultType, KeyValue, SimFault, SimRegion, SimTxn
from app.providers.base import (
    BackupArtifact,
    BackupVerification,
    ComponentHealth,
    DbStatus,
    ProviderError,
    RegionProbe,
    RegionProvider,
    ResyncResult,
    SyntheticResult,
    TrafficManager,
)

# What each fault breaks inside a region.
FAULT_EFFECTS: dict[FaultType, set[str]] = {
    FaultType.CLOUD_OUTAGE: {"app", "database", "network", "replication"},
    FaultType.DATABASE_FAILURE: {"database", "replication"},
    FaultType.NETWORK_FAILURE: {"network", "replication"},
    FaultType.SERVICE_FAILURE: {"app"},
    FaultType.REPLICATION_FAILURE: {"replication"},
}

BASE_LATENCY_MS = {"aws": 18.0, "azure": 24.0, "local": 5.0}


def _get_kv(s: Session, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = s.get(KeyValue, key)
    return dict(row.value) if row else dict(default)


def _set_kv(s: Session, key: str, value: dict[str, Any]) -> None:
    row = s.get(KeyValue, key)
    if row:
        row.value = value
    else:
        s.add(KeyValue(key=key, value=value))


@dataclass
class RegionSpec:
    name: str
    cloud: str
    display_name: str
    initial_role: DbRole


class LocalSimulation:
    """Shared simulated world: regions, faults, replication, workload, traffic."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        specs: list[RegionSpec],
        replication_delay_s: float,
        write_interval_s: float,
        backup_dir: str,
    ) -> None:
        self.db = db
        self.clock = clock
        self.specs = {s.name: s for s in specs}
        self.replication_delay_s = replication_delay_s
        self.write_interval_s = write_interval_s
        self.backup_dir = Path(backup_dir)
        self.rng = random.Random()  # noqa: S311 - jitter only

    # ------------------------------------------------------------------ bootstrap
    def bootstrap(self, primary: str) -> None:
        with self.db.session() as s:
            for spec in self.specs.values():
                if not s.get(SimRegion, spec.name):
                    s.add(SimRegion(name=spec.name, cloud=spec.cloud, db_role=spec.initial_role))
            if not s.get(KeyValue, "traffic"):
                _set_kv(s, "traffic", {"active_region": primary, "switched_at": None, "history": []})

    # ------------------------------------------------------------------ faults
    def active_faults(self, s: Session, region: str | None = None) -> list[SimFault]:
        q = select(SimFault).where(SimFault.active.is_(True))
        if region:
            q = q.where(SimFault.region == region)
        return list(s.scalars(q))

    def broken(self, s: Session, region: str) -> set[str]:
        out: set[str] = set()
        for f in self.active_faults(s, region):
            out |= FAULT_EFFECTS[f.fault_type]
        return out

    def inject(self, region: str, fault: FaultType, actor: str, game_day_id: int | None = None) -> SimFault:
        if region not in self.specs:
            raise ProviderError(f"unknown region {region}")
        with self.db.session() as s:
            existing = s.scalar(
                select(SimFault).where(
                    SimFault.region == region, SimFault.fault_type == fault, SimFault.active.is_(True)
                )
            )
            if existing:
                return existing
            f = SimFault(
                region=region,
                fault_type=fault,
                injected_at=self.clock.now(),
                injected_by=actor,
                game_day_id=game_day_id,
                active=True,
            )
            s.add(f)
            s.flush()
            return f

    def clear(self, region: str, actor: str, fault: FaultType | None = None) -> int:
        with self.db.session() as s:
            faults = self.active_faults(s, region)
            n = 0
            for f in faults:
                if fault is None or f.fault_type == fault:
                    f.active = False
                    f.cleared_at = self.clock.now()
                    f.cleared_by = actor
                    n += 1
            return n

    # ------------------------------------------------------------------ data plane helpers
    def _role(self, s: Session, region: str) -> DbRole:
        r = s.get(SimRegion, region)
        if r is None:
            raise ProviderError(f"unknown region {region}")
        return r.db_role

    def _last(self, s: Session, region: str) -> tuple[int | None, Any]:
        row = s.execute(
            select(SimTxn.seq, SimTxn.committed_at).where(SimTxn.region == region).order_by(SimTxn.seq.desc()).limit(1)
        ).first()
        return (row[0], aware(row[1])) if row else (None, None)

    def _writable(self, s: Session, region: str) -> tuple[bool, str]:
        broken = self.broken(s, region)
        if "network" in broken:
            return False, "region unreachable"
        if "app" in broken:
            return False, "application tier down"
        if "database" in broken:
            return False, "database down"
        if self._role(s, region) != DbRole.PRIMARY:
            return False, f"database is {self._role(s, region).value}, not writable"
        return True, "ok"

    def _write(self, s: Session, region: str, payload: dict[str, Any]) -> int:
        last_seq, _ = self._last(s, region)
        seq = (last_seq or 0) + 1
        now = self.clock.now()
        s.add(SimTxn(region=region, seq=seq, committed_at=now, applied_at=now, origin_region=region, payload=payload))
        s.flush()
        return seq

    # ------------------------------------------------------------------ workload
    def workload_tick(self) -> dict[str, Any]:
        """The synthetic customer: one write through the global endpoint per interval."""
        now = self.clock.now()
        with self.db.session() as s:
            wl = _get_kv(
                s,
                "workload",
                {
                    "ok": 0,
                    "failed": 0,
                    "last_acked_seq": None,
                    "last_acked_at": None,
                    "last_attempt_at": None,
                    "outage_started_at": None,
                    "last_outage": None,
                    "last_error": None,
                    "last_acked_region": None,
                },
            )
            last_attempt = wl.get("last_attempt_at")
            if last_attempt and (now.timestamp() - last_attempt) < self.write_interval_s - 1e-6:
                return wl
            wl["last_attempt_at"] = now.timestamp()
            region = _get_kv(s, "traffic", {})["active_region"]
            ok, why = self._writable(s, region)
            if ok:
                seq = self._write(
                    s, region, {"order_id": str(uuid.uuid4()), "amount": round(self.rng.uniform(5, 500), 2)}
                )
                wl.update(
                    ok=wl["ok"] + 1,
                    last_acked_seq=seq,
                    last_acked_at=now.timestamp(),
                    last_acked_region=region,
                    last_error=None,
                )
                if wl.get("outage_started_at"):
                    wl["last_outage"] = {
                        "started_at": wl["outage_started_at"],
                        "ended_at": now.timestamp(),
                        "duration_s": round(now.timestamp() - wl["outage_started_at"], 3),
                        "failed_writes": wl.get("outage_failed", 0),
                    }
                    wl["outage_started_at"] = None
                    wl["outage_failed"] = 0
            else:
                wl.update(failed=wl["failed"] + 1, last_error=f"{region}: {why}")
                if not wl.get("outage_started_at"):
                    wl["outage_started_at"] = now.timestamp()
                wl["outage_failed"] = wl.get("outage_failed", 0) + 1
            _set_kv(s, "workload", wl)
            return wl

    def workload_state(self) -> dict[str, Any]:
        with self.db.session() as s:
            return _get_kv(s, "workload", {})

    # ------------------------------------------------------------------ replication
    def replicate_tick(self, force: bool = False) -> dict[str, int]:
        """Asynchronous log shipping from the primary to every reachable replica."""
        shipped: dict[str, int] = {}
        now = self.clock.now()
        with self.db.session() as s:
            primaries = [r for r in s.scalars(select(SimRegion)) if r.db_role == DbRole.PRIMARY]
            replicas = [r for r in s.scalars(select(SimRegion)) if r.db_role == DbRole.REPLICA]
            for p in primaries:
                if self.broken(s, p.name) & {"replication", "database"}:
                    continue
                for r in replicas:
                    if self.broken(s, r.name) & {"replication", "database"}:
                        continue
                    last_seq, _ = self._last(s, r.name)
                    cutoff = now if force else now - timedelta(seconds=self.replication_delay_s)
                    rows = s.scalars(
                        select(SimTxn)
                        .where(SimTxn.region == p.name, SimTxn.seq > (last_seq or 0), SimTxn.committed_at <= cutoff)
                        .order_by(SimTxn.seq)
                    ).all()
                    for t in rows:
                        s.add(
                            SimTxn(
                                region=r.name,
                                seq=t.seq,
                                committed_at=t.committed_at,
                                applied_at=now,
                                origin_region=t.origin_region,
                                payload=t.payload,
                            )
                        )
                    shipped[f"{p.name}->{r.name}"] = len(rows)
        return shipped

    # ------------------------------------------------------------------ traffic
    def active_region(self) -> str:
        with self.db.session() as s:
            return _get_kv(s, "traffic", {})["active_region"]

    def switch_traffic(self, region: str) -> dict[str, Any]:
        if region not in self.specs:
            raise ProviderError(f"unknown region {region}")
        with self.db.session() as s:
            t = _get_kv(s, "traffic", {"history": []})
            prev = t.get("active_region")
            now = self.clock.now().timestamp()
            history = (t.get("history") or [])[-49:] + [{"from": prev, "to": region, "at": now}]
            _set_kv(s, "traffic", {"active_region": region, "switched_at": now, "history": history})
            return {"from": prev, "to": region, "switched_at": now}


class LocalRegion(RegionProvider):
    def __init__(self, sim: LocalSimulation, spec: RegionSpec) -> None:
        self.sim = sim
        self.name = spec.name
        self.cloud = spec.cloud
        self.display_name = spec.display_name

    def _jitter(self, base: float) -> float:
        return round(base * self.sim.rng.uniform(0.8, 1.4), 2)

    def probe(self) -> RegionProbe:
        with self.sim.db.session() as s:
            broken = self.sim.broken(s, self.name)
        base = BASE_LATENCY_MS.get(self.cloud, 5.0)
        if "network" in broken:
            timeout = ComponentHealth(False, 5000.0, "probe timed out (no route to region)")
            return RegionProbe(
                self.name, ComponentHealth(False, 0, "unreachable"), ComponentHealth(False, 0, "unreachable"), timeout
            )
        net = ComponentHealth(True, self._jitter(base))
        app = (
            ComponentHealth(False, 0, "HTTP 503 from load balancer")
            if "app" in broken
            else ComponentHealth(True, self._jitter(base * 0.6), "HTTP 200")
        )
        dbh = (
            ComponentHealth(False, 0, "connection refused")
            if "database" in broken
            else ComponentHealth(True, self._jitter(base * 0.3), "SELECT 1 ok")
        )
        return RegionProbe(self.name, app, dbh, net)

    def db_status(self) -> DbStatus:
        with self.sim.db.session() as s:
            broken = self.sim.broken(s, self.name)
            if broken & {"network", "database"}:
                return DbStatus(reachable=False, detail="database unreachable")
            role = self.sim._role(s, self.name)
            seq, at = self.sim._last(s, self.name)
            return DbStatus(
                reachable=True, role=role.value, last_seq=seq, last_committed_at=at, writable=role == DbRole.PRIMARY
            )

    def _require_db(self, s: Session) -> None:
        broken = self.sim.broken(s, self.name)
        if "network" in broken:
            raise ProviderError(f"{self.name}: control plane unreachable")
        if "database" in broken:
            raise ProviderError(f"{self.name}: database is down")

    def promote(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        with self.sim.db.session() as s:
            self._require_db(s)
            r = s.get(SimRegion, self.name)
            assert r is not None
            prev = r.db_role
            if prev == DbRole.FENCED:
                raise ProviderError(f"{self.name}: fenced database must be resynced before promotion")
            r.db_role = DbRole.PRIMARY
            seq, _ = self.sim._last(s, self.name)
        return {
            "previous_role": prev.value,
            "role": "primary",
            "last_seq": seq,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    def fence(self) -> dict[str, Any]:
        # Lease-based fencing: the write lease lives in the control plane, so fencing succeeds even when
        # the region itself is unreachable. When the region returns it boots read-only (FENCED).
        with self.sim.db.session() as s:
            r = s.get(SimRegion, self.name)
            assert r is not None
            prev = r.db_role
            if prev == DbRole.PRIMARY:
                r.db_role = DbRole.FENCED
            return {"previous_role": prev.value, "role": r.db_role.value}

    def demote_to_replica(self, source: RegionProvider) -> dict[str, Any]:
        with self.sim.db.session() as s:
            self._require_db(s)
            r = s.get(SimRegion, self.name)
            assert r is not None
            prev = r.db_role
            r.db_role = DbRole.REPLICA
            return {"previous_role": prev.value, "role": "replica", "replicating_from": source.name}

    def catch_up_from(self, source: RegionProvider) -> dict[str, Any]:
        with self.sim.db.session() as s:
            self._require_db(s)
            if self.sim.broken(s, source.name) & {"database", "network"}:
                raise ProviderError(f"{source.name} unreachable; cannot drain")
            before, _ = self.sim._last(s, self.name)
        # Temporarily treat the source as primary so its tail can be shipped even though it is fenced.
        with self.sim.db.session() as s:
            src = s.get(SimRegion, source.name)
            assert src is not None
            orig = src.db_role
            src.db_role = DbRole.PRIMARY
        try:
            self.sim.replicate_tick(force=True)
        finally:
            with self.sim.db.session() as s:
                src = s.get(SimRegion, source.name)
                assert src is not None
                src.db_role = orig
        with self.sim.db.session() as s:
            after, _ = self.sim._last(s, self.name)
            src_last, _ = self.sim._last(s, source.name)
        if (after or 0) < (src_last or 0):
            raise ProviderError(f"drain incomplete: {self.name} at {after}, {source.name} at {src_last}")
        return {"applied": (after or 0) - (before or 0), "last_seq": after}

    def resync_from(self, source: RegionProvider) -> ResyncResult:
        with self.sim.db.session() as s:
            self._require_db(s)
            if self.sim.broken(s, source.name) & {"database", "network"}:
                raise ProviderError(f"{source.name} unreachable; cannot resync")
            src = {
                seq: p.get("order_id")
                for seq, p in s.execute(select(SimTxn.seq, SimTxn.payload).where(SimTxn.region == source.name))
            }
            mine = {
                seq: p.get("order_id")
                for seq, p in s.execute(select(SimTxn.seq, SimTxn.payload).where(SimTxn.region == self.name))
            }
            divergent = sorted(seq for seq, oid in mine.items() if src.get(seq) != oid)
            if divergent:
                s.execute(delete(SimTxn).where(SimTxn.region == self.name, SimTxn.seq.in_(divergent)))
            missing = sorted(set(src) - (set(mine) - set(divergent)))
            now = self.sim.clock.now()
            if missing:
                rows = s.scalars(select(SimTxn).where(SimTxn.region == source.name, SimTxn.seq.in_(missing)))
                for t in rows:
                    s.add(
                        SimTxn(
                            region=self.name,
                            seq=t.seq,
                            committed_at=t.committed_at,
                            applied_at=now,
                            origin_region=t.origin_region,
                            payload=t.payload,
                        )
                    )
            r = s.get(SimRegion, self.name)
            assert r is not None
            r.db_role = DbRole.REPLICA
            s.flush()
            last, _ = self.sim._last(s, self.name)
        return ResyncResult(
            copied=len(missing),
            divergent_discarded=len(divergent),
            divergent_seqs=divergent[:100],
            resulting_last_seq=last,
        )

    def create_backup(self) -> BackupArtifact:
        with self.sim.db.session() as s:
            self._require_db(s)
            rows = s.execute(
                select(SimTxn.seq, SimTxn.committed_at, SimTxn.payload)
                .where(SimTxn.region == self.name)
                .order_by(SimTxn.seq)
            ).all()
        now = self.sim.clock.now()
        doc = {
            "format": "lifeboat-backup/v1",
            "region": self.name,
            "created_at": now.isoformat(),
            "records": [{"seq": r[0], "committed_at": aware(r[1]).isoformat(), "payload": r[2]} for r in rows],
        }
        raw = gzip.compress(json.dumps(doc, sort_keys=True).encode(), mtime=0)
        folder = self.sim.backup_dir / self.name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"backup-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.json.gz"
        path.write_bytes(raw)
        return BackupArtifact(
            location=str(path),
            size_bytes=len(raw),
            record_count=len(rows),
            max_seq=rows[-1][0] if rows else 0,
            checksum_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def verify_backup(self, location: str, expected_checksum: str) -> BackupVerification:
        t0 = time.perf_counter()
        issues: list[str] = []
        p = Path(location)
        if not p.exists():
            return BackupVerification(False, False, False, issues=["backup object missing"])
        raw = p.read_bytes()
        checksum_ok = hashlib.sha256(raw).hexdigest() == expected_checksum
        if not checksum_ok:
            issues.append("SHA-256 checksum mismatch (object modified or corrupted)")
        restorable, count = False, 0
        structural: list[str] = []
        try:
            doc = json.loads(gzip.decompress(raw))
            records = doc["records"]
            seqs = [r["seq"] for r in records]
            count = len(records)
            if seqs != sorted(set(seqs)):
                structural.append("sequence numbers not strictly increasing")
            # Test restore: load into an isolated scratch structure and check every record is well formed.
            restored = {r["seq"]: r["payload"]["order_id"] for r in records}
            if len(restored) != count:
                structural.append("duplicate records after restore")
            restorable = not structural
        except Exception as e:  # noqa: BLE001 - any parse failure means not restorable
            structural.append(f"test restore failed: {type(e).__name__}")
        issues += structural
        ok = checksum_ok and restorable and not issues
        return BackupVerification(
            ok=ok,
            checksum_match=checksum_ok,
            restorable=restorable,
            record_count=count,
            issues=issues,
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    def synthetic_transaction(self) -> SyntheticResult:
        t0 = time.perf_counter()
        with self.sim.db.session() as s:
            ok, why = self.sim._writable(s, self.name)
            if not ok:
                return SyntheticResult(False, 0.0, why)
            marker = f"synthetic-{uuid.uuid4().hex[:12]}"
            seq = self.sim._write(s, self.name, {"order_id": marker, "synthetic": True, "amount": 0})
            back = s.scalar(
                select(func.count()).select_from(SimTxn).where(SimTxn.region == self.name, SimTxn.seq == seq)
            )
            if back != 1:
                return SyntheticResult(False, 0.0, "read-after-write failed")
        return SyntheticResult(True, round((time.perf_counter() - t0) * 1000, 2), "write+read ok", seq)

    def restart_service(self) -> dict[str, Any]:
        with self.sim.db.session() as s:
            if "network" in self.sim.broken(s, self.name):
                raise ProviderError(f"{self.name}: unreachable, cannot restart")
            r = s.get(SimRegion, self.name)
            assert r is not None
            r.service_restarts += 1
            restarts = r.service_restarts
        cleared = self.sim.clear(self.name, "self-heal", FaultType.SERVICE_FAILURE)
        return {"restarted": True, "service_faults_cleared": cleared, "restart_count": restarts}


class LocalTrafficManager(TrafficManager):
    def __init__(self, sim: LocalSimulation) -> None:
        self.sim = sim

    def active_region(self) -> str:
        return self.sim.active_region()

    def switch_to(self, region: str) -> dict[str, Any]:
        return self.sim.switch_traffic(region)
