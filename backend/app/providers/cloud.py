"""Factory for real-cloud providers (AWS primary, Azure secondary, Route 53 global traffic)."""

from __future__ import annotations

from app.config import Settings
from app.providers.base import RegionProvider, TrafficManager


def build_cloud_providers(st: Settings) -> tuple[dict[str, RegionProvider], TrafficManager]:
    from app.providers.aws import AwsRegion, Route53TrafficManager
    from app.providers.azure import AzureRegion

    missing = [
        k
        for k in (
            "aws_rds_instance_id",
            "aws_route53_zone_id",
            "aws_record_name",
            "aws_app_endpoint",
            "azure_subscription_id",
            "azure_resource_group",
            "azure_pg_server",
            "azure_app_endpoint",
            "primary_db_dsn",
            "secondary_db_dsn",
        )
        if not getattr(st, k)
    ]
    if missing:
        raise RuntimeError("cloud mode requires: " + ", ".join(f"LIFEBOAT_{m.upper()}" for m in missing))
    aws = AwsRegion.from_settings(st)
    azure = AzureRegion.from_settings(st)
    traffic = Route53TrafficManager.from_settings(
        st, {aws.name: st.aws_app_endpoint, azure.name: st.azure_app_endpoint}
    )
    return {aws.name: aws, azure.name: azure}, traffic
