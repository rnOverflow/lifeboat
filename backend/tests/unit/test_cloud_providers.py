"""Cloud adapters exercised against moto (AWS) and mocked Azure SDK clients - no real credentials."""
import hashlib
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.providers.aws import AwsRegion, Route53TrafficManager
from app.providers.azure import AzureRegion
from app.providers.base import DbStatus, ProviderError


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)


@mock_aws
def test_route53_switch_and_active_region():
    r53 = boto3.client("route53")
    zone = r53.create_hosted_zone(Name="example.com.", CallerReference="t")["HostedZone"]["Id"]
    r53.change_resource_record_sets(HostedZoneId=zone, ChangeBatch={"Changes": [{"Action": "UPSERT",
        "ResourceRecordSet": {"Name": "app.example.com.", "Type": "CNAME", "TTL": 30,
                              "ResourceRecords": [{"Value": "aws-alb.example.net"}]}}]})
    tm = Route53TrafficManager(zone, "app.example.com", {"aws-us-east-1": "https://aws-alb.example.net",
                                                         "azure-eastus": "https://az-fd.example.net"})
    assert tm.active_region() == "aws-us-east-1"
    res = tm.switch_to("azure-eastus")
    assert res["from"] == "aws-us-east-1" and res["to"] == "azure-eastus"
    assert tm.active_region() == "azure-eastus"


@mock_aws
def test_rds_snapshot_backup_and_verification():
    rds = boto3.client("rds")
    rds.create_db_instance(DBInstanceIdentifier="prod-db", DBInstanceClass="db.t3.micro", Engine="postgres",
                           MasterUsername="u", MasterUserPassword="moto-only-pw-123", AllocatedStorage=20,
                           StorageEncrypted=True)
    region = AwsRegion("aws-us-east-1", "us-east-1", "prod-db", "https://aws-alb.example.net",
                       "postgresql://invalid:1/x")
    art = region.create_backup()
    assert art.location.startswith("rds-snapshot:")
    v = region.verify_backup(art.location, art.checksum_sha256)
    assert v.checksum_match and v.restorable
    bad = region.verify_backup(art.location, hashlib.sha256(b"other").hexdigest())
    assert not bad.ok and "identity" in bad.issues[0]
    status, inst = region._rds_status()
    assert status == "available" and inst["DBInstanceIdentifier"] == "prod-db"


@mock_aws
def test_aws_probe_reports_unreachable_app_and_db(monkeypatch):
    boto3.client("rds").create_db_instance(DBInstanceIdentifier="prod-db", DBInstanceClass="db.t3.micro",
                                           Engine="postgres", MasterUsername="u",
                                           MasterUserPassword="moto-only-pw-123", AllocatedStorage=20)
    region = AwsRegion("aws-us-east-1", "us-east-1", "prod-db", "http://127.0.0.1:9", "postgresql://u@127.0.0.1:9/x")
    p = region.probe()
    assert not p.network.ok and not p.app.ok and not p.database.ok  # connection refused everywhere
    assert region.fence()["deferred"] is True  # unreachable old primary: fencing deferred, not crashed


@mock_aws
def test_promote_read_replica_calls_rds(monkeypatch):
    region = AwsRegion("aws-us-east-1", "us-east-1", "replica-db", "https://x", "postgresql://u@127.0.0.1:9/x")
    region.rds = MagicMock()
    region.rds.describe_db_instances.return_value = {"DBInstances": [{
        "DBInstanceStatus": "available", "ReadReplicaSourceDBInstanceIdentifier": "src"}]}
    region.pg = MagicMock()
    out = region.promote()
    region.rds.promote_read_replica.assert_called_once()
    region.pg.stop_subscription.assert_called_once()
    region.pg.unfence.assert_called_once()
    assert out["role"] == "primary"


def test_azure_region_with_mocked_sdk():
    client = MagicMock()
    client.servers.get.return_value.state = "Ready"
    region = AzureRegion("azure-eastus", "sub", "rg", "pg", "http://127.0.0.1:9", "postgresql://u@127.0.0.1:9/x",
                         pg_client=client)
    region.pg = MagicMock()
    region.pg.status.return_value = DbStatus(reachable=True, role="replica", last_seq=10)
    p = region.probe()
    assert p.database.ok and not p.app.ok  # app endpoint refused
    assert region.db_status().role == "replica"
    assert region.promote()["role"] == "primary"
    region.pg.stop_subscription.assert_called_once()
    client.servers.get.side_effect = RuntimeError("boom")
    assert not region.probe().database.ok
    with pytest.raises(ProviderError):
        region.restart_service()


def test_cloud_mode_requires_configuration():
    from app.providers.cloud import build_cloud_providers

    with pytest.raises(RuntimeError, match="LIFEBOAT_AWS_RDS_INSTANCE_ID"):
        build_cloud_providers(Settings(provider_mode="cloud", _env_file=None))
