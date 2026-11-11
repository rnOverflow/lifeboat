import pytest
from sqlalchemy import select

from app import audit
from app.engine.orchestrator import RunError
from app.models import AuditLog, FaultType, RecoveryRun, Role, RunKind, RunStatus
from tests.conftest import run_ticks

AWS, AZ = "aws-us-east-1", "azure-eastus"


def _outage(rt, clock):
    run_ticks(rt, clock, 5)
    rt.sim.inject(AWS, FaultType.CLOUD_OUTAGE, "op")
    run_ticks(rt, clock, 3)
    with rt.db.session() as s:
        run = rt.orchestrator.active_run(s)
        assert run is not None and run.status == RunStatus.AWAITING_APPROVAL
        return run.id


def test_two_person_rule_and_roles(rt, clock):
    with rt.db.session() as s:
        run = rt.orchestrator.create(s, RunKind.FAILOVER, "drill", "alice")
        assert run.status == RunStatus.AWAITING_APPROVAL
        assert run.metrics["planned"] is True
        with pytest.raises(RunError) as e:
            rt.orchestrator.approve(s, run, "alice", Role.ADMIN)
        assert e.value.status == 403
        with pytest.raises(RunError):
            rt.orchestrator.approve(s, run, "bob", Role.OPERATOR)
        with pytest.raises(RunError):
            rt.orchestrator.create(s, RunKind.FAILOVER, "again", "carol")  # one run at a time
        rt.orchestrator.approve(s, run, "bob", Role.APPROVER)
        assert run.status == RunStatus.COMPLETED
        denied = s.scalars(select(AuditLog).where(AuditLog.outcome == "denied")).all()
        assert len(denied) == 2


def test_planned_switchover_has_zero_data_loss(rt, clock):
    run_ticks(rt, clock, 8)
    with rt.db.session() as s:
        run = rt.orchestrator.create(s, RunKind.FAILOVER, "planned", "alice")
        rt.orchestrator.approve(s, run, "bob", Role.APPROVER)
        assert run.status == RunStatus.COMPLETED
        assert run.metrics["graceful"] is True and run.metrics["data_loss_txns"] == 0
        assert run.metrics["rpo_s"] == 0
    assert rt.regions[AWS].db_status().role == "replica"  # demoted cleanly, no resync needed
    run_ticks(rt, clock, 3)
    assert rt.workload_state()["last_acked_region"] == AZ


def test_unplanned_failover_measures_data_loss(rt, clock):
    rid = _outage(rt, clock)
    with rt.db.session() as s:
        run = s.get(RecoveryRun, rid)
        assert run.initiated_by == "system:monitor"
        rt.orchestrator.approve(s, run, "bob", Role.APPROVER)
        assert run.status == RunStatus.COMPLETED
        m = run.metrics
        assert m["planned"] is False and m["data_loss_txns"] == 2 and m["rpo_s"] == 2.0
        assert m["time_to_detect_s"] == 2.0 and m["rto_met"] and m["rpo_met"]
        assert run.report["objectives"]["rto_measured_s"] == m["rto_s"]
    assert rt.traffic.active_region() == AZ
    assert rt.regions[AZ].db_status().role == "primary"


def test_reject_and_cancel(rt, clock):
    rid = _outage(rt, clock)
    with rt.db.session() as s:
        run = s.get(RecoveryRun, rid)
        rt.orchestrator.reject(s, run, "bob", Role.APPROVER, "investigating first")
        assert run.status == RunStatus.REJECTED
        assert rt.traffic.active_region() == AWS
        run2 = rt.orchestrator.create(s, RunKind.FAILOVER, "retry", "alice")
        rt.orchestrator.cancel(s, run2, "alice")
        assert run2.status == RunStatus.CANCELLED


def test_validation_fails_when_secondary_unhealthy(rt, clock):
    run_ticks(rt, clock, 3)
    rt.sim.inject(AZ, FaultType.DATABASE_FAILURE, "t")
    with rt.db.session() as s:
        run = rt.orchestrator.create(s, RunKind.FAILOVER, "bad idea", "alice")
        assert run.status == RunStatus.FAILED and "secondary not ready" in run.error
        assert rt.traffic.active_region() == AWS


def test_auto_failover_blocked_when_standby_down(rt, clock):
    run_ticks(rt, clock, 2)
    rt.sim.inject(AZ, FaultType.CLOUD_OUTAGE, "t")
    run_ticks(rt, clock, 3)
    rt.sim.inject(AWS, FaultType.CLOUD_OUTAGE, "t")
    run_ticks(rt, clock, 3)
    with rt.db.session() as s:
        assert rt.orchestrator.active_run(s) is None
        assert s.scalar(select(RecoveryRun)) is None


def test_no_approval_policy_runs_automatically(rt, clock):
    with rt.db.session() as s:
        rt.policy(s).require_approval = False
    _ = run_ticks(rt, clock, 3)
    rt.sim.inject(AWS, FaultType.DATABASE_FAILURE, "t")
    run_ticks(rt, clock, 3)
    with rt.db.session() as s:
        run = s.scalar(select(RecoveryRun))
        assert run.status == RunStatus.COMPLETED and run.approved_by == "policy:auto-approve"


def test_failback_requires_failover_first(rt):
    with rt.db.session() as s, pytest.raises(RunError):
        rt.orchestrator.create(s, RunKind.FAILBACK, "nothing to do", "alice")


def test_audit_chain_detects_tampering(rt, clock):
    _outage(rt, clock)
    with rt.db.session() as s:
        assert audit.verify_chain(s)["valid"]
        e = s.scalars(select(AuditLog).order_by(AuditLog.id)).first()
        e.details = {"forged": True}
    with rt.db.session() as s:
        res = audit.verify_chain(s)
        assert res["valid"] is False and res["broken_at_id"] == e.id
