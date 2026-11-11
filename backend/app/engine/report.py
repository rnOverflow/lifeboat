"""RTO/RPO measurement and recovery reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.clock import aware
from app.models import RecoveryRun


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    a, b = aware(a), aware(b)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds(), 3)


def compute_metrics(run: RecoveryRun, rto_target_s: int, rpo_target_s: int) -> dict[str, Any]:
    m = dict(run.metrics or {})
    planned = bool(m.get("planned"))
    promote_started = None
    for st in run.steps:
        if st.name == "promote":
            promote_started = st.started_at
    if run.outage_started_at and not planned:
        rto = _secs(run.outage_started_at, run.service_restored_at)
        rto_basis = "first failed health probe -> verified recovery"
    else:
        rto = _secs(promote_started, run.service_restored_at)
        rto_basis = "write freeze (drain) -> verified recovery (planned switchover)"
    rpo = m.get("rpo_s")
    m.update(
        {
            "rto_s": rto,
            "rto_basis": rto_basis,
            "rto_target_s": rto_target_s,
            "rpo_target_s": rpo_target_s,
            "rto_met": None if rto is None else rto <= rto_target_s,
            "rpo_met": None if rpo is None else rpo <= rpo_target_s,
            "time_to_detect_s": _secs(run.outage_started_at, run.detected_at),
            "time_to_approve_s": _secs(run.detected_at or run.created_at, run.approved_at),
            "time_to_execute_s": _secs(run.approved_at, run.service_restored_at),
        }
    )
    return m


def recommendations(run: RecoveryRun, m: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    rto, tgt = m.get("rto_s"), m.get("rto_target_s") or 0
    if rto is not None and tgt and m.get("time_to_approve_s") and m["time_to_approve_s"] > 0.5 * tgt:
        recs.append(
            f"Human approval took {m['time_to_approve_s']:.0f}s ({m['time_to_approve_s'] / tgt:.0%} of the RTO "
            "budget). Consider pre-approved automatic failover for confirmed full-region outages."
        )
    if m.get("rpo_met") is False:
        recs.append(
            f"RPO breached ({m['rpo_s']:.1f}s > {m['rpo_target_s']}s): reduce replication lag or use "
            "synchronous commit for critical tables."
        )
    if (m.get("data_loss_txns") or 0) > 0:
        recs.append(
            f"{m['data_loss_txns']} acknowledged transactions were not replicated before promotion. "
            "Reconcile them from the old primary's divergent set during failback."
        )
    if m.get("time_to_detect_s") and tgt and m["time_to_detect_s"] > 0.2 * tgt:
        recs.append("Detection consumed >20% of the RTO budget: lower probe interval or failure threshold.")
    if m.get("rto_met") is False:
        recs.append("RTO target missed: review the timeline for the slowest phase and automate it.")
    if not recs:
        recs.append("Recovery met all objectives. Keep running game days to keep this proven.")
    return recs


def customer_impact(run: RecoveryRun, workload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Customer-visible write unavailability attributable to *this* run (synthetic workload)."""
    if not workload:
        return None
    restored = aware(run.service_restored_at)
    window_start = aware(run.outage_started_at) or aware(run.created_at)
    ongoing = workload.get("outage_started_at")
    if ongoing and restored and ongoing >= window_start.timestamp() - 5:
        return {
            "started_at": ongoing,
            "duration_s": round(restored.timestamp() - ongoing, 3),
            "failed_writes": workload.get("outage_failed", 0),
            "basis": "first failed write -> verified recovery",
        }
    last = workload.get("last_outage")
    if last and last.get("started_at", 0) >= window_start.timestamp() - 5:
        return {**last, "basis": "first failed write -> first successful write"}
    return {"duration_s": 0.0, "failed_writes": 0, "basis": "no failed customer writes during this run"}


def build_report(run: RecoveryRun, m: dict[str, Any], workload: dict[str, Any] | None) -> dict[str, Any]:
    timeline = [
        {
            "step": s.name,
            "title": s.title,
            "status": s.status.value,
            "started_at": aware(s.started_at).isoformat() if s.started_at else None,
            "finished_at": aware(s.finished_at).isoformat() if s.finished_at else None,
            "duration_s": _secs(s.started_at, s.finished_at),
            "output": s.output,
        }
        for s in run.steps
    ]
    customer = customer_impact(run, workload)
    return {
        "title": f"{run.kind.value.title()} #{run.id}: {run.source_region} -> {run.target_region}",
        "status": run.status.value,
        "reason": run.reason,
        "initiated_by": run.initiated_by,
        "approved_by": run.approved_by,
        "objectives": {
            "rto_target_s": m.get("rto_target_s"),
            "rpo_target_s": m.get("rpo_target_s"),
            "rto_measured_s": m.get("rto_s"),
            "rpo_measured_s": m.get("rpo_s"),
            "rto_met": m.get("rto_met"),
            "rpo_met": m.get("rpo_met"),
        },
        "phases": {
            "detect_s": m.get("time_to_detect_s"),
            "approve_s": m.get("time_to_approve_s"),
            "execute_s": m.get("time_to_execute_s"),
        },
        "data_loss": {
            "transactions": m.get("data_loss_txns"),
            "window_s": m.get("rpo_s"),
            "planned": m.get("planned", False),
        },
        "customer_impact": customer,
        "timeline": timeline,
        "recommendations": recommendations(run, m),
    }


def to_markdown(report: dict[str, Any]) -> str:
    o, ph, dl = report["objectives"], report["phases"], report["data_loss"]

    def f(v: Any, unit: str = "s") -> str:
        return "n/a" if v is None else f"{v:.2f}{unit}" if isinstance(v, float) else f"{v}{unit}"

    def ok(v: bool | None) -> str:
        return "n/a" if v is None else ("PASS" if v else "FAIL")

    lines = [
        f"# {report['title']}",
        "",
        f"*Status:* **{report['status']}**  ",
        f"*Reason:* {report['reason']}  ",
        f"*Initiated by:* {report['initiated_by']} | *Approved by:* {report['approved_by'] or 'n/a'}",
        "",
        "## Objectives",
        "",
        "| Objective | Target | Measured | Result |",
        "|---|---|---|---|",
        f"| RTO | {f(o['rto_target_s'])} | {f(o['rto_measured_s'])} | {ok(o['rto_met'])} |",
        f"| RPO | {f(o['rpo_target_s'])} | {f(o['rpo_measured_s'])} | {ok(o['rpo_met'])} |",
        "",
        "## Phase breakdown",
        "",
        f"- Detection: {f(ph['detect_s'])}",
        f"- Decision/approval: {f(ph['approve_s'])}",
        f"- Execution: {f(ph['execute_s'])}",
        "",
        "## Data loss",
        "",
        f"- Lost acknowledged transactions: {dl['transactions'] if dl['transactions'] is not None else 'n/a'}",
        f"- Loss window: {f(dl['window_s'])}",
        f"- Planned switchover: {dl['planned']}",
        "",
    ]
    if report.get("customer_impact"):
        ci = report["customer_impact"]
        lines += [
            "## Customer impact (synthetic workload)",
            "",
            f"- Write unavailability: {f(ci.get('duration_s'))}, failed writes: {ci.get('failed_writes')}",
            "",
        ]
    lines += ["## Timeline", "", "| Step | Status | Duration | Started |", "|---|---|---|---|"]
    for t in report["timeline"]:
        lines.append(f"| {t['title']} | {t['status']} | {f(t['duration_s'])} | {t['started_at'] or '-'} |")
    lines += ["", "## Recommendations", ""] + [f"- {r}" for r in report["recommendations"]]
    return "\n".join(lines) + "\n"
