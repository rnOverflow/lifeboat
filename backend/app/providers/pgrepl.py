"""Cloud-neutral PostgreSQL data-plane operations used by the AWS and Azure providers.

Cross-cloud replication (RDS -> Azure Flexible Server) is PostgreSQL *logical* replication: a publication
`lifeboat_pub` on the primary and a subscription `lifeboat_sub` on the standby. Replication progress is
measured with a heartbeat table the platform writes on the primary and reads on both sides, which works the
same for physical and logical replication and across clouds.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.providers.base import DbStatus, ProviderError, SyntheticResult

HEARTBEAT_DDL = (
    "CREATE SCHEMA IF NOT EXISTS lifeboat; "
    "CREATE TABLE IF NOT EXISTS lifeboat.heartbeat (id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now())"
)
SUB = "lifeboat_sub"
PUB = "lifeboat_pub"


class PostgresEndpoint:
    def __init__(self, dsn: str, connect_timeout: int = 3) -> None:
        self.dsn = dsn
        self.connect_timeout = connect_timeout

    def _conn(self, autocommit: bool = True):  # noqa: ANN202
        import psycopg

        try:
            return psycopg.connect(self.dsn, connect_timeout=self.connect_timeout, autocommit=autocommit)
        except psycopg.Error as e:
            raise ProviderError(f"database unreachable: {type(e).__name__}") from e

    def status(self) -> DbStatus:
        try:
            with self._conn() as c:
                in_recovery = c.execute("SELECT pg_is_in_recovery()").fetchone()[0]
                subscribed = c.execute(
                    "SELECT count(*) FROM pg_subscription WHERE subname = %s AND subenabled", (SUB,)
                ).fetchone()[0]
                read_only = c.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
                row = c.execute("SELECT max(id), max(ts) FROM lifeboat.heartbeat").fetchone()
        except ProviderError as e:
            return DbStatus(reachable=False, detail=str(e))
        except Exception as e:  # noqa: BLE001 - e.g. heartbeat table missing
            return DbStatus(reachable=True, detail=f"status query failed: {type(e).__name__}")
        role = "replica" if (in_recovery or subscribed) else ("fenced" if read_only else "primary")
        return DbStatus(
            reachable=True,
            role=role,
            last_seq=row[0],
            last_committed_at=row[1],
            writable=role == "primary",
        )

    def heartbeat(self) -> SyntheticResult:
        t0 = time.perf_counter()
        try:
            with self._conn() as c:
                c.execute(HEARTBEAT_DDL)
                rid = c.execute("INSERT INTO lifeboat.heartbeat DEFAULT VALUES RETURNING id").fetchone()[0]
                ok = c.execute("SELECT 1 FROM lifeboat.heartbeat WHERE id = %s", (rid,)).fetchone() is not None
        except Exception as e:  # noqa: BLE001
            return SyntheticResult(False, 0.0, f"{type(e).__name__}: {e}")
        return SyntheticResult(ok, round((time.perf_counter() - t0) * 1000, 2), "heartbeat write+read", rid)

    def fence(self) -> dict[str, Any]:
        with self._conn() as c:
            db = c.execute("SELECT current_database()").fetchone()[0]
            c.execute(f'ALTER DATABASE "{db}" SET default_transaction_read_only = on')
            c.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        return {"fenced": True, "method": "default_transaction_read_only + session termination"}

    def unfence(self) -> None:
        with self._conn() as c:
            db = c.execute("SELECT current_database()").fetchone()[0]
            c.execute(f'ALTER DATABASE "{db}" SET default_transaction_read_only = off')

    def stop_subscription(self) -> None:
        with self._conn() as c:
            if c.execute("SELECT 1 FROM pg_subscription WHERE subname = %s", (SUB,)).fetchone():
                c.execute(f"ALTER SUBSCRIPTION {SUB} DISABLE")

    def subscribe_to(self, source_dsn: str, copy_data: bool) -> None:
        with self._conn() as c:
            if c.execute("SELECT 1 FROM pg_subscription WHERE subname = %s", (SUB,)).fetchone():
                c.execute(f"ALTER SUBSCRIPTION {SUB} DISABLE")
                c.execute(f"ALTER SUBSCRIPTION {SUB} SET (slot_name = NONE)")
                c.execute(f"DROP SUBSCRIPTION {SUB}")
            c.execute(
                f"CREATE SUBSCRIPTION {SUB} CONNECTION %s PUBLICATION {PUB} WITH (copy_data = %s)",
                (source_dsn, copy_data),
            )

    def published_tables(self) -> list[tuple[str, str]]:
        with self._conn() as c:
            return [
                (r[0], r[1])
                for r in c.execute("SELECT schemaname, tablename FROM pg_publication_tables WHERE pubname = %s", (PUB,))
            ]

    def truncate(self, tables: list[tuple[str, str]]) -> None:
        if not tables:
            return
        with self._conn() as c:
            c.execute("TRUNCATE " + ", ".join(f'"{s}"."{t}"' for s, t in tables) + " CASCADE")


def wait_caught_up(src: PostgresEndpoint, dst: PostgresEndpoint, timeout_s: float = 60.0) -> dict[str, Any]:
    marker = src.heartbeat()
    if not marker.ok:
        raise ProviderError(f"cannot write drain marker on source: {marker.detail}")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = dst.status()
        if st.reachable and (st.last_seq or 0) >= (marker.seq or 0):
            return {"drained_to": marker.seq, "at": datetime.now(UTC).isoformat()}
        time.sleep(0.5)
    raise ProviderError(f"replica did not apply drain marker {marker.seq} within {timeout_s}s")
