"""AWS adapter: ALB/app health over HTTPS, RDS control plane, Route 53 traffic steering, RDS snapshots."""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from app.config import Settings
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
from app.providers.pgrepl import PostgresEndpoint, wait_caught_up


def http_probe(url: str, timeout: float = 3.0) -> tuple[ComponentHealth, ComponentHealth]:
    """Returns (network, app) health for an HTTPS health endpoint."""
    t0 = time.perf_counter()
    try:
        r = httpx.get(url, timeout=timeout)
    except httpx.TransportError as e:
        return ComponentHealth(False, timeout * 1000, f"unreachable: {type(e).__name__}"), ComponentHealth(
            False, 0, "unreachable"
        )
    ms = round((time.perf_counter() - t0) * 1000, 2)
    return ComponentHealth(True, ms), ComponentHealth(r.status_code < 500, ms, f"HTTP {r.status_code}")


class AwsRegion(RegionProvider):
    cloud = "aws"

    def __init__(
        self,
        name: str,
        region: str,
        rds_instance_id: str,
        app_url: str,
        dsn: str,
        backup_bucket: str | None = None,
        session: Any = None,
    ) -> None:
        import boto3

        self.name = name
        self.display_name = f"AWS {region}"
        self.app_url = app_url.rstrip("/") + "/healthz"
        self.instance_id = rds_instance_id
        self.pg = PostgresEndpoint(dsn)
        self.dsn = dsn
        sess = session or boto3.session.Session(region_name=region)
        self.rds = sess.client("rds")

    @classmethod
    def from_settings(cls, st: Settings) -> AwsRegion:
        assert st.aws_rds_instance_id and st.aws_app_endpoint and st.primary_db_dsn
        return cls(
            st.primary_region,
            st.aws_region,
            st.aws_rds_instance_id,
            st.aws_app_endpoint,
            st.primary_db_dsn,
            st.aws_backup_bucket,
        )

    def _rds_status(self) -> tuple[str, dict[str, Any]]:
        try:
            inst = self.rds.describe_db_instances(DBInstanceIdentifier=self.instance_id)["DBInstances"][0]
        except Exception as e:  # noqa: BLE001 - botocore errors
            raise ProviderError(f"RDS API: {type(e).__name__}") from e
        return inst["DBInstanceStatus"], inst

    def probe(self) -> RegionProbe:
        net, app = http_probe(self.app_url)
        try:
            status, _ = self._rds_status()
            db = ComponentHealth(status == "available", 0, f"RDS {status}")
        except ProviderError as e:
            db = ComponentHealth(False, 0, str(e))
        if db.ok:
            st = self.pg.status()
            db = ComponentHealth(st.reachable, 0, db.detail if st.reachable else st.detail)
        return RegionProbe(self.name, app, db, net)

    def db_status(self) -> DbStatus:
        return self.pg.status()

    def promote(self) -> dict[str, Any]:
        _, inst = self._rds_status()
        out: dict[str, Any] = {}
        if inst.get("ReadReplicaSourceDBInstanceIdentifier"):
            self.rds.promote_read_replica(DBInstanceIdentifier=self.instance_id, BackupRetentionPeriod=7)
            out["rds_promote_read_replica"] = True
        self.pg.stop_subscription()
        self.pg.unfence()
        out["role"] = "primary"
        return out

    def fence(self) -> dict[str, Any]:
        try:
            return self.pg.fence()
        except ProviderError as e:
            # Region unreachable: traffic is moved away and the database is fenced on return (runbook step).
            return {"fenced": False, "deferred": True, "reason": str(e)}

    def demote_to_replica(self, source: RegionProvider) -> dict[str, Any]:
        src_dsn = getattr(source, "dsn", None)
        if not src_dsn:
            raise ProviderError("source DSN unknown")
        self.pg.fence()
        self.pg.subscribe_to(src_dsn, copy_data=False)
        return {"role": "replica", "replicating_from": source.name}

    def catch_up_from(self, source: RegionProvider) -> dict[str, Any]:
        return wait_caught_up(source.pg, self.pg)  # type: ignore[attr-defined]

    def resync_from(self, source: RegionProvider) -> ResyncResult:
        src_dsn = getattr(source, "dsn", None)
        if not src_dsn:
            raise ProviderError("source DSN unknown")
        before = self.pg.status()
        tables = source.pg.published_tables()  # type: ignore[attr-defined]
        self.pg.truncate(tables)
        self.pg.subscribe_to(src_dsn, copy_data=True)
        return ResyncResult(copied=-1, divergent_discarded=-1, resulting_last_seq=before.last_seq)

    def create_backup(self) -> BackupArtifact:
        snap_id = f"lifeboat-{self.instance_id}-{int(time.time())}"
        try:
            snap = self.rds.create_db_snapshot(DBSnapshotIdentifier=snap_id, DBInstanceIdentifier=self.instance_id)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"create_db_snapshot failed: {type(e).__name__}") from e
        arn = snap["DBSnapshot"]["DBSnapshotArn"]
        return BackupArtifact(
            location=f"rds-snapshot:{snap_id}",
            size_bytes=snap["DBSnapshot"].get("AllocatedStorage", 0) * 1024**3,
            record_count=0,
            max_seq=0,
            checksum_sha256=hashlib.sha256(arn.encode()).hexdigest(),
        )

    def verify_backup(self, location: str, expected_checksum: str) -> BackupVerification:
        t0 = time.perf_counter()
        snap_id = location.removeprefix("rds-snapshot:")
        try:
            snap = self.rds.describe_db_snapshots(DBSnapshotIdentifier=snap_id)["DBSnapshots"][0]
        except Exception as e:  # noqa: BLE001
            return BackupVerification(False, False, False, issues=[f"snapshot lookup failed: {type(e).__name__}"])
        issues = []
        fp_ok = hashlib.sha256(snap["DBSnapshotArn"].encode()).hexdigest() == expected_checksum
        if not fp_ok:
            issues.append("snapshot identity does not match catalogue")
        if snap["Status"] != "available":
            issues.append(f"snapshot status {snap['Status']}")
        if not snap.get("Encrypted", False):
            issues.append("snapshot is not encrypted")
        return BackupVerification(
            ok=not issues,
            checksum_match=fp_ok,
            restorable=snap["Status"] == "available",
            issues=issues,
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
        )

    def synthetic_transaction(self) -> SyntheticResult:
        return self.pg.heartbeat()

    def restart_service(self) -> dict[str, Any]:
        raise ProviderError("service restart is delegated to the orchestrator (ECS/EKS rollout restart) - see runbook")


class Route53TrafficManager(TrafficManager):
    """Active/standby steering with a single low-TTL CNAME that the platform UPSERTs on failover."""

    def __init__(
        self, zone_id: str, record_name: str, endpoints: dict[str, str], ttl: int = 30, session: Any = None
    ) -> None:
        import boto3

        self.zone_id = zone_id
        self.record = record_name.rstrip(".") + "."
        self.endpoints = {k: httpx.URL(v).host for k, v in endpoints.items()}
        self.ttl = ttl
        self.r53 = (session or boto3.session.Session()).client("route53")

    @classmethod
    def from_settings(cls, st: Settings, endpoints: dict[str, str]) -> Route53TrafficManager:
        assert st.aws_route53_zone_id and st.aws_record_name
        return cls(st.aws_route53_zone_id, st.aws_record_name, endpoints)

    def active_region(self) -> str:
        rrs = self.r53.list_resource_record_sets(
            HostedZoneId=self.zone_id, StartRecordName=self.record, StartRecordType="CNAME", MaxItems="1"
        )["ResourceRecordSets"]
        if not rrs or rrs[0]["Name"] != self.record:
            raise ProviderError(f"record {self.record} not found")
        target = rrs[0]["ResourceRecords"][0]["Value"].rstrip(".")
        for region, host in self.endpoints.items():
            if host == target:
                return region
        raise ProviderError(f"record points at unknown target {target}")

    def switch_to(self, region: str) -> dict[str, Any]:
        prev = self.active_region()
        resp = self.r53.change_resource_record_sets(
            HostedZoneId=self.zone_id,
            ChangeBatch={
                "Comment": f"lifeboat failover {prev} -> {region}",
                "Changes": [
                    {
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": self.record,
                            "Type": "CNAME",
                            "TTL": self.ttl,
                            "ResourceRecords": [{"Value": self.endpoints[region]}],
                        },
                    }
                ],
            },
        )
        return {"from": prev, "to": region, "change_id": resp["ChangeInfo"]["Id"], "ttl": self.ttl}
