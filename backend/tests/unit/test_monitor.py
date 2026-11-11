from sqlalchemy import select

from app.models import FaultType, Incident, Region, RegionStatus
from tests.conftest import run_ticks

AWS, AZ = "aws-us-east-1", "azure-eastus"


def _region(rt, name):
    with rt.db.session() as s:
        return s.scalar(select(Region).where(Region.name == name))


def test_detection_threshold_and_hysteresis(rt, clock):
    with rt.db.session() as s:
        rt.policy(s).auto_initiate_failover = False
    run_ticks(rt, clock, 2)
    rt.sim.inject(AZ, FaultType.DATABASE_FAILURE, "t")
    run_ticks(rt, clock, 1)
    assert _region(rt, AZ).status == RegionStatus.DEGRADED
    run_ticks(rt, clock, 1)
    assert _region(rt, AZ).status == RegionStatus.DEGRADED
    run_ticks(rt, clock, 1)
    r = _region(rt, AZ)
    assert r.status == RegionStatus.DOWN
    with rt.db.session() as s:
        inc = s.scalar(select(Incident).where(Incident.region == AZ))
        assert inc.kind == "database_failure" and inc.severity == "major" and inc.status == "open"
        assert (inc.detected_at - inc.started_at).total_seconds() == 2.0
    rt.sim.clear(AZ, "t")
    run_ticks(rt, clock, 1)
    assert _region(rt, AZ).status == RegionStatus.DOWN  # needs 2 good probes
    run_ticks(rt, clock, 1)
    assert _region(rt, AZ).status == RegionStatus.UP
    with rt.db.session() as s:
        assert s.scalar(select(Incident).where(Incident.region == AZ)).status == "resolved"


def test_transient_blip_does_not_open_incident(rt, clock):
    rt.sim.inject(AWS, FaultType.NETWORK_FAILURE, "t")
    run_ticks(rt, clock, 1)
    rt.sim.clear(AWS, "t")
    run_ticks(rt, clock, 1)
    assert _region(rt, AWS).status == RegionStatus.UP
    with rt.db.session() as s:
        assert s.scalar(select(Incident)) is None


def test_replication_lag_incident(rt, clock):
    run_ticks(rt, clock, 3)
    rt.sim.inject(AWS, FaultType.REPLICATION_FAILURE, "t")
    run_ticks(rt, clock, 20)
    with rt.db.session() as s:
        inc = s.scalar(select(Incident).where(Incident.kind == "replication_lag"))
        assert inc is not None and inc.region == AZ and inc.severity == "warning"
        assert _region(rt, AZ).replication_lag_s > 15
    rt.sim.clear(AWS, "t")
    run_ticks(rt, clock, 4)
    with rt.db.session() as s:
        assert s.scalar(select(Incident).where(Incident.kind == "replication_lag")).status == "resolved"


def test_service_failure_self_heals_without_failover(rt, clock):
    run_ticks(rt, clock, 2)
    rt.sim.inject(AWS, FaultType.SERVICE_FAILURE, "t")
    run_ticks(rt, clock, 6)
    with rt.db.session() as s:
        inc = s.scalar(select(Incident).where(Incident.region == AWS))
        assert inc.kind == "service_failure" and inc.status == "resolved"
        assert inc.details["auto_remediation"]["result"]["restarted"] is True
        assert rt.orchestrator.active_run(s) is None
    assert rt.traffic.active_region() == AWS
