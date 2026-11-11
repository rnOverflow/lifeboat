from datetime import timedelta

from app.engine import report
from app.models import RecoveryRun, RunKind, RunStatus, RunStep, StepStatus
from tests.factories import NOW


def _run(approve_after: float, rpo: float, lost: int) -> RecoveryRun:
    r = RecoveryRun(id=7, kind=RunKind.FAILOVER, status=RunStatus.COMPLETED, source_region="a", target_region="b",
                    reason="t", initiated_by="op", approved_by="ap", created_at=NOW,
                    outage_started_at=NOW, detected_at=NOW + timedelta(seconds=3),
                    approved_at=NOW + timedelta(seconds=3 + approve_after),
                    service_restored_at=NOW + timedelta(seconds=5 + approve_after),
                    metrics={"rpo_s": rpo, "data_loss_txns": lost, "planned": False})
    r.steps = [RunStep(position=0, name="promote", title="Promote", status=StepStatus.SUCCEEDED,
                       started_at=NOW + timedelta(seconds=4 + approve_after), finished_at=NOW, output={})]
    return r


def test_metrics_and_pass():
    r = _run(10, 2.0, 2)
    m = report.compute_metrics(r, 300, 30)
    assert m["rto_s"] == 15.0 and m["rto_met"] and m["rpo_met"]
    assert m["time_to_detect_s"] == 3.0 and m["time_to_approve_s"] == 10.0 and m["time_to_execute_s"] == 2.0
    rep = report.build_report(r, m, None)
    assert rep["objectives"]["rto_met"] is True
    assert any("acknowledged transactions" in x for x in rep["recommendations"])
    md = report.to_markdown(rep)
    assert "| RTO |" in md and "PASS" in md


def test_slow_approval_and_rpo_breach_recommendations():
    r = _run(400, 45.0, 30)
    m = report.compute_metrics(r, 300, 30)
    assert m["rto_met"] is False and m["rpo_met"] is False
    recs = report.recommendations(r, m)
    assert any("approval" in x for x in recs) and any("RPO breached" in x for x in recs)
    assert "FAIL" in report.to_markdown(report.build_report(r, m, None))
