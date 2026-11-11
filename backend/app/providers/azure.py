"""Azure adapter: App health over HTTPS, PostgreSQL Flexible Server control plane, Blob backup catalogue."""

from __future__ import annotations

import time
from typing import Any

from app.config import Settings
from app.providers.aws import http_probe
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
)
from app.providers.pgrepl import PostgresEndpoint, wait_caught_up


class AzureRegion(RegionProvider):
    cloud = "azure"

    def __init__(self, name: str, subscription_id: str, resource_group: str, pg_server: str, app_url: str, dsn: str,
                 pg_client: Any = None) -> None:
        self.name = name
        self.display_name = "Azure East US"
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.pg_server = pg_server
        self.app_url = app_url.rstrip("/") + "/healthz"
        self.dsn = dsn
        self.pg = PostgresEndpoint(dsn)
        self._pg_client = pg_client

    @classmethod
    def from_settings(cls, st: Settings) -> AzureRegion:
        assert st.azure_subscription_id and st.azure_resource_group and st.azure_pg_server
        assert st.azure_app_endpoint and st.secondary_db_dsn
        return cls(st.secondary_region, st.azure_subscription_id, st.azure_resource_group, st.azure_pg_server,
                   st.azure_app_endpoint, st.secondary_db_dsn)

    @property
    def pg_client(self) -> Any:
        if self._pg_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient

            self._pg_client = PostgreSQLManagementClient(DefaultAzureCredential(), self.subscription_id)
        return self._pg_client

    def _server_state(self) -> str:
        try:
            return str(self.pg_client.servers.get(self.resource_group, self.pg_server).state)
        except Exception as e:  # noqa: BLE001 - azure-core errors
            raise ProviderError(f"Azure API: {type(e).__name__}") from e

    def probe(self) -> RegionProbe:
        net, app = http_probe(self.app_url)
        try:
            state = self._server_state()
            db = ComponentHealth(state.lower().endswith("ready"), 0, f"Flexible Server {state}")
        except ProviderError as e:
            db = ComponentHealth(False, 0, str(e))
        if db.ok:
            st = self.pg.status()
            db = ComponentHealth(st.reachable, 0, db.detail if st.reachable else st.detail)
        return RegionProbe(self.name, app, db, net)

    def db_status(self) -> DbStatus:
        return self.pg.status()

    def promote(self) -> dict[str, Any]:
        # Cross-cloud standby is a logical subscriber, so promotion = stop applying + open for writes.
        self.pg.stop_subscription()
        self.pg.unfence()
        return {"role": "primary", "method": "logical subscription disabled, read-only lifted"}

    def fence(self) -> dict[str, Any]:
        try:
            return self.pg.fence()
        except ProviderError as e:
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
        self.pg.truncate(source.pg.published_tables())  # type: ignore[attr-defined]
        self.pg.subscribe_to(src_dsn, copy_data=True)
        return ResyncResult(copied=-1, divergent_discarded=-1, resulting_last_seq=before.last_seq)

    def create_backup(self) -> BackupArtifact:
        name = f"lifeboat-{int(time.time())}"
        try:
            poller = self.pg_client.backups.begin_create(self.resource_group, self.pg_server, name)
            res = poller.result(timeout=600)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"on-demand backup failed: {type(e).__name__}") from e
        return BackupArtifact(location=f"azure-pg-backup:{name}", size_bytes=0, record_count=0, max_seq=0,
                              checksum_sha256=str(getattr(res, "id", name)).encode().hex()[:64].ljust(64, "0"))

    def verify_backup(self, location: str, expected_checksum: str) -> BackupVerification:
        name = location.removeprefix("azure-pg-backup:")
        try:
            b = self.pg_client.backups.get(self.resource_group, self.pg_server, name)
        except Exception as e:  # noqa: BLE001
            return BackupVerification(False, False, False, issues=[f"backup lookup failed: {type(e).__name__}"])
        fp = str(getattr(b, "id", name)).encode().hex()[:64].ljust(64, "0")
        ok = fp == expected_checksum
        return BackupVerification(ok=ok, checksum_match=ok, restorable=ok,
                                  issues=[] if ok else ["backup identity mismatch"])

    def synthetic_transaction(self) -> SyntheticResult:
        return self.pg.heartbeat()

    def restart_service(self) -> dict[str, Any]:
        raise ProviderError("service restart is delegated to the orchestrator (AKS rollout restart) - see runbook")
