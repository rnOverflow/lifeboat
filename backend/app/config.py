"""Runtime configuration. Every secret comes from the environment - nothing is hardcoded."""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIFEBOAT_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://lifeboat@localhost:5432/lifeboat"
    jwt_secret: str = Field(default="", description="HS256 signing key; required in prod")
    jwt_ttl_minutes: int = 60
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # Provider selection: "local" simulates both clouds with real state; "cloud" uses AWS + Azure SDKs.
    provider_mode: Literal["local", "cloud"] = "local"
    primary_region: str = "aws-us-east-1"
    secondary_region: str = "azure-eastus"

    # Engine loop
    tick_interval_s: float = 1.0
    scheduler_enabled: bool = True
    detection_threshold: int = 3  # consecutive failed probes before a region is declared DOWN
    recovery_threshold: int = 2  # consecutive good probes before a region is declared UP

    # Local simulation
    sim_replication_delay_s: float = 2.0  # async replication delay between AWS and Azure
    sim_write_interval_s: float = 1.0  # synthetic workload write rate
    backup_dir: str = "./var/backups"

    # Bootstrap users (only used when the users table is empty). Passwords MUST come from env.
    bootstrap_admin_password: str | None = None
    bootstrap_operator_password: str | None = None
    bootstrap_approver_password: str | None = None
    bootstrap_viewer_password: str | None = None

    # Cloud provider settings (cloud mode only)
    aws_region: str = "us-east-1"
    aws_rds_instance_id: str | None = None
    aws_route53_zone_id: str | None = None
    aws_record_name: str | None = None
    aws_app_endpoint: str | None = None
    aws_backup_bucket: str | None = None
    azure_subscription_id: str | None = None
    azure_resource_group: str | None = None
    azure_pg_server: str | None = None
    azure_app_endpoint: str | None = None
    azure_tm_profile: str | None = None
    azure_storage_account_url: str | None = None
    azure_backup_container: str = "dr-backups"
    primary_db_dsn: str | None = None
    secondary_db_dsn: str | None = None

    # AI copilot (optional)
    anthropic_api_key: str | None = None
    copilot_model: str = "claude-opus-5-5"

    otel_exporter_otlp_endpoint: str | None = None

    def effective_jwt_secret(self) -> str:
        if self.jwt_secret:
            return self.jwt_secret
        if self.env == "prod":
            raise RuntimeError("LIFEBOAT_JWT_SECRET must be set in prod")
        return _ephemeral_secret()


@lru_cache
def _ephemeral_secret() -> str:
    log.warning("LIFEBOAT_JWT_SECRET not set - using an ephemeral per-process secret (tokens reset on restart)")
    return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
