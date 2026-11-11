from pathlib import Path

from sqlalchemy import select

from app.models import FaultType, SimTxn
from tests.conftest import run_ticks

AWS, AZ = "aws-us-east-1", "azure-eastus"


def test_workload_writes_and_async_replication_delay(rt, clock):
    run_ticks(rt, clock, 6)
    aws, az = rt.regions[AWS].db_status(), rt.regions[AZ].db_status()
    assert aws.role == "primary" and az.role == "replica"
    assert aws.last_seq == 6
    # 2s replication delay: replica is behind but not empty
    assert 0 < az.last_seq < aws.last_seq
    assert (aws.last_committed_at - az.last_committed_at).total_seconds() == 2.0


def test_replication_failure_grows_lag_and_recovers(rt, clock):
    run_ticks(rt, clock, 4)
    rt.sim.inject(AZ, FaultType.REPLICATION_FAILURE, "t")
    before = rt.regions[AZ].db_status().last_seq
    run_ticks(rt, clock, 5)
    assert rt.regions[AZ].db_status().last_seq == before
    rt.sim.clear(AZ, "t")
    run_ticks(rt, clock, 3)
    assert rt.regions[AZ].db_status().last_seq > before


def test_outage_blocks_writes_and_probe(rt, clock):
    run_ticks(rt, clock, 2)
    rt.sim.inject(AWS, FaultType.CLOUD_OUTAGE, "t")
    p = rt.regions[AWS].probe()
    assert not p.healthy and set(p.failing_components()) == {"app", "database", "network"}
    assert not rt.regions[AWS].db_status().reachable
    run_ticks(rt, clock, 2)
    wl = rt.workload_state()
    assert wl["failed"] == 2 and "unreachable" in wl["last_error"]


def test_service_failure_only_breaks_app(rt):
    rt.sim.inject(AWS, FaultType.SERVICE_FAILURE, "t")
    p = rt.regions[AWS].probe()
    assert p.failing_components() == ["app"]
    res = rt.regions[AWS].restart_service()
    assert res["service_faults_cleared"] == 1 and rt.regions[AWS].probe().healthy


def test_fenced_primary_cannot_be_promoted_and_resync_discards_divergent(rt, clock):
    run_ticks(rt, clock, 6)  # aws has 1..6, azure ~1..4
    az_last = rt.regions[AZ].db_status().last_seq
    rt.regions[AWS].fence()
    rt.regions[AZ].promote()
    rt.sim.switch_traffic(AZ)
    run_ticks(rt, clock, 3)  # azure writes az_last+1.. with different payloads
    st = rt.regions[AWS].db_status()
    assert st.role == "fenced" and not st.writable
    try:
        rt.regions[AWS].promote()
        raise AssertionError("fenced db must not be promotable")
    except Exception as e:  # noqa: BLE001
        assert "resync" in str(e)
    res = rt.regions[AWS].resync_from(rt.regions[AZ])
    assert res.divergent_discarded == 6 - az_last
    with rt.db.session() as s:
        aws = {t.seq: t.payload["order_id"] for t in s.scalars(select(SimTxn).where(SimTxn.region == AWS))}
        az = {t.seq: t.payload["order_id"] for t in s.scalars(select(SimTxn).where(SimTxn.region == AZ))}
    assert aws == az and rt.regions[AWS].db_status().role == "replica"


def test_backup_verify_detects_corruption(rt, clock):
    run_ticks(rt, clock, 5)
    art = rt.regions[AWS].create_backup()
    assert art.record_count == 5 and Path(art.location).exists()
    v = rt.regions[AWS].verify_backup(art.location, art.checksum_sha256)
    assert v.ok and v.checksum_match and v.restorable and v.record_count == 5
    raw = bytearray(Path(art.location).read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    Path(art.location).write_bytes(bytes(raw))
    v2 = rt.regions[AWS].verify_backup(art.location, art.checksum_sha256)
    assert not v2.ok and not v2.checksum_match
    Path(art.location).unlink()
    assert not rt.regions[AWS].verify_backup(art.location, art.checksum_sha256).ok


def test_synthetic_transaction_requires_writable_primary(rt):
    assert rt.regions[AWS].synthetic_transaction().ok
    assert not rt.regions[AZ].synthetic_transaction().ok  # replica is read-only
