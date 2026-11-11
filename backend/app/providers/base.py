"""Cloud-agnostic provider contracts.

The DR engine only ever talks to these interfaces. Concrete implementations:
  * local.py  - full simulation of an AWS and an Azure region with real, mutable state
  * aws.py    - RDS / Route 53 / S3 via boto3
  * azure.py  - PostgreSQL Flexible Server / Traffic Manager / Blob Storage via the Azure SDK
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


class ProviderError(RuntimeError):
    """Raised when a provider operation cannot be completed (region unreachable, API error...)."""


@dataclass
class ComponentHealth:
    ok: bool
    latency_ms: float = 0.0
    detail: str = ""


@dataclass
class RegionProbe:
    region: str
    app: ComponentHealth
    database: ComponentHealth
    network: ComponentHealth

    @property
    def healthy(self) -> bool:
        return self.app.ok and self.database.ok and self.network.ok

    @property
    def latency_ms(self) -> float:
        return round(self.app.latency_ms + self.database.latency_ms + self.network.latency_ms, 2)

    def failing_components(self) -> list[str]:
        return [n for n, c in (("app", self.app), ("database", self.database), ("network", self.network)) if not c.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
            "app": asdict(self.app),
            "database": asdict(self.database),
            "network": asdict(self.network),
        }


@dataclass
class DbStatus:
    reachable: bool
    role: str | None = None
    last_seq: int | None = None
    last_committed_at: datetime | None = None
    writable: bool = False
    detail: str = ""


@dataclass
class BackupArtifact:
    location: str
    size_bytes: int
    record_count: int
    max_seq: int
    checksum_sha256: str


@dataclass
class BackupVerification:
    ok: bool
    checksum_match: bool
    restorable: bool
    record_count: int = 0
    issues: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResyncResult:
    copied: int
    divergent_discarded: int
    divergent_seqs: list[int] = field(default_factory=list)
    resulting_last_seq: int | None = None


@dataclass
class SyntheticResult:
    ok: bool
    latency_ms: float
    detail: str = ""
    seq: int | None = None


class RegionProvider(ABC):
    """One deployment of the application stack in one cloud region."""

    name: str
    cloud: str
    display_name: str

    @abstractmethod
    def probe(self) -> RegionProbe: ...

    @abstractmethod
    def db_status(self) -> DbStatus: ...

    @abstractmethod
    def promote(self) -> dict[str, Any]:
        """Promote this region's replica database to a writable primary."""

    @abstractmethod
    def fence(self) -> dict[str, Any]:
        """Revoke this region's write lease so a returning old primary cannot accept writes (split-brain guard)."""

    @abstractmethod
    def demote_to_replica(self, source: RegionProvider) -> dict[str, Any]:
        """Turn this (in-sync) region into a replica of `source` (graceful role swap)."""

    @abstractmethod
    def catch_up_from(self, source: RegionProvider) -> dict[str, Any]:
        """Block until this replica has applied everything `source` committed (planned switchover drain)."""

    @abstractmethod
    def resync_from(self, source: RegionProvider) -> ResyncResult:
        """Rebuild this region's database as a replica of `source`, discarding divergent writes."""

    @abstractmethod
    def create_backup(self) -> BackupArtifact: ...

    @abstractmethod
    def verify_backup(self, location: str, expected_checksum: str) -> BackupVerification: ...

    @abstractmethod
    def synthetic_transaction(self) -> SyntheticResult:
        """End-to-end write + read through this region's application tier."""

    @abstractmethod
    def restart_service(self) -> dict[str, Any]:
        """Self-healing action: restart the application tier."""


class TrafficManager(ABC):
    """Global traffic steering (DNS failover / GSLB)."""

    @abstractmethod
    def active_region(self) -> str: ...

    @abstractmethod
    def switch_to(self, region: str) -> dict[str, Any]: ...
