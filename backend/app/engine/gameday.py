"""DR Game Day: inject a real fault, let the platform detect and recover, fail back, and report."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.clock import aware
from app.engine import readiness
from app.models import (
    FaultType,
    GameDay,
    Incident,
    RecoveryRun,
    Region,
    RegionStatus,
    RunKind,
    RunStatus,
)

if TYPE_CHECKING:
    from app.engine.runtime import Runtime

PHASE_TIMEOUT_S = 300
TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.REJECTED, RunStatus.CANCELLED)


class GameDayError(Exception):
    pass


class GameDayEngine:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    def start(
        self,
        s: Session,
        name: str,
        scenario: FaultType,
        target_region: str | None,
        auto_approve: bool,
        include_failback: bool,
        user: str,
        ip: str | None = None,
    ) -> GameDay:
        if self.rt.sim is None:
            raise GameDayError("game days need a fault injector (local provider mode)")
        if s.scalar(select(GameDay).where(GameDay.status == "running")):
            raise GameDayError("another game day is already running")
        if self.rt.orchestrator.active_run(s):
            raise GameDayError("a recovery run is in progress")
        target = target_region or self.rt.traffic.active_region()
        if target not in self.rt.regions:
            raise GameDayError(f"unknown region {target}")
        now = self.rt.clock.now()
        gd = GameDay(
            name=name,
            scenario=scenario.value,
            target_region=target,
            status="running",
            phase="baseline",
            auto_approve=auto_approve,
            include_failback=include_failback,
            created_by=user,
            started_at=now,
            timeline=[],
        )
        s.add(gd)
        s.flush()
        audit.record(
            s,
            ts=now,
            actor=user,
            action="gameday.start",
            target=f"gameday:{gd.id}",
            details={"scenario": scenario.value, "region": target, "auto_approve": auto_approve},
            ip=ip,
        )
        self.advance(s, gd)
        return gd

    def abort(self, s: Session, gd: GameDay, user: str, ip: str | None = None) -> GameDay:
        if gd.status != "running":
            raise GameDayError(f"game day is {gd.status}")
        assert self.rt.sim is not None
        self.rt.sim.clear(gd.target_region, f"gameday-abort:{user}")
        self._event(gd, "aborted", f"aborted by {user}; faults cleared")
        gd.status, gd.finished_at = "aborted", self.rt.clock.now()
        gd.report = self._report(s, gd)
        audit.record(s, ts=self.rt.clock.now(), actor=user, action="gameday.abort", target=f"gameday:{gd.id}", ip=ip)
        return gd

    # --------------------------------------------------------------------------- engine
    def advance_running(self, s: Session) -> None:
        for gd in s.scalars(select(GameDay).where(GameDay.status == "running")):
            self.advance(s, gd)

    def _event(self, gd: GameDay, phase: str, detail: str, **extra: Any) -> None:
        gd.timeline = [
            *(gd.timeline or []),
            {"at": self.rt.clock.now().isoformat(), "phase": phase, "detail": detail, **extra},
        ]

    def _phase_started(self, gd: GameDay) -> datetime:
        for e in reversed(gd.timeline or []):
            if e.get("phase_start"):
                return datetime.fromisoformat(e["at"])
        return aware(gd.started_at)

    def _goto(self, gd: GameDay, phase: str, detail: str, **extra: Any) -> None:
        gd.phase = phase
        self._event(gd, phase, detail, phase_start=True, **extra)

    def _preapprove(self, s: Session, run: RecoveryRun, gd: GameDay) -> None:
        if run.status == RunStatus.AWAITING_APPROVAL and gd.auto_approve:
            run.approved_by = f"gameday-preapproved:{gd.created_by}"
            run.approved_at = self.rt.clock.now()
            run.status = RunStatus.RUNNING
            audit.record(
                s,
                ts=run.approved_at,
                actor=gd.created_by,
                action=f"{run.kind.value}.approve",
                target=f"run:{run.id}",
                details={"via": f"gameday:{gd.id} pre-approval"},
            )
            self.rt.orchestrator.advance(s, run)

    def advance(self, s: Session, gd: GameDay) -> None:  # noqa: C901 - explicit phase machine
        assert self.rt.sim is not None
        now = self.rt.clock.now()
        if gd.phase != "baseline" and (now - self._phase_started(gd)).total_seconds() > PHASE_TIMEOUT_S:
            self.rt.sim.clear(gd.target_region, f"gameday:{gd.id}:timeout")
            self._event(gd, gd.phase, f"phase timed out after {PHASE_TIMEOUT_S}s; faults cleared")
            gd.status, gd.finished_at = "failed", now
            gd.report = self._report(s, gd)
            return
        scenario = FaultType(gd.scenario)

        if gd.phase == "baseline":
            snap = self.rt.snapshot(s)
            rd = readiness.compute(snap)
            self._event(
                gd,
                "baseline",
                f"readiness {rd['score']} ({rd['grade']}), traffic on {snap.active_region}",
                readiness=rd["score"],
                active_region=snap.active_region,
            )
            self.rt.sim.inject(gd.target_region, scenario, f"gameday:{gd.created_by}", game_day_id=gd.id)
            audit.record(
                s,
                ts=now,
                actor=gd.created_by,
                action="fault.inject",
                target=gd.target_region,
                details={"fault": scenario.value, "gameday": gd.id},
            )
            self._goto(gd, "detect", f"injected {scenario.value} into {gd.target_region}")
            return

        if gd.phase == "detect":
            want_region = gd.target_region
            kinds = None
            if scenario == FaultType.REPLICATION_FAILURE:
                want_region = next(n for n in self.rt.regions if n != self.rt.traffic.active_region())
                kinds = ("replication_lag",)
            q = select(Incident).where(Incident.region == want_region, Incident.detected_at >= gd.started_at)
            if kinds:
                q = q.where(Incident.kind.in_(kinds))
            inc = s.scalar(q.order_by(Incident.id))
            if inc:
                det = (aware(inc.detected_at) - aware(gd.started_at)).total_seconds()
                self._goto(
                    gd,
                    "respond",
                    f"incident #{inc.id} ({inc.kind}) detected after {det:.1f}s",
                    incident_id=inc.id,
                    detection_s=round(det, 3),
                )
            return

        if gd.phase == "respond":
            run = s.scalar(
                select(RecoveryRun)
                .where(RecoveryRun.kind == RunKind.FAILOVER, RecoveryRun.created_at >= gd.started_at)
                .order_by(RecoveryRun.id)
            )
            target_row = s.scalar(select(Region).where(Region.name == gd.target_region))
            serving = gd.target_region == self.rt.traffic.active_region() or bool(run)
            if run:
                if gd.failover_run_id is None:
                    gd.failover_run_id, run.game_day_id = run.id, gd.id
                    self._event(gd, "respond", f"failover run #{run.id} created by {run.initiated_by}")
                self._preapprove(s, run, gd)
                if run.status in TERMINAL:
                    self._goto(gd, "recover", f"failover run #{run.id} {run.status.value}")
                return
            if target_row and target_row.status == RegionStatus.UP and scenario == FaultType.SERVICE_FAILURE:
                self._goto(gd, "recover", "region self-healed (service restart) without failover")
                return
            if not serving or scenario == FaultType.REPLICATION_FAILURE:
                self._goto(gd, "recover", "fault does not affect the serving path; no failover expected")
                return
            policy = self.rt.policy(s)
            if not policy.auto_initiate_failover and target_row and target_row.status == RegionStatus.DOWN:
                run = self.rt.orchestrator.create(
                    s, RunKind.FAILOVER, f"game day #{gd.id}: {scenario.value}", gd.created_by, game_day_id=gd.id
                )
                gd.failover_run_id = run.id
            return

        if gd.phase == "recover":
            if not any(e.get("phase") == "recover" and e.get("faults_cleared") for e in gd.timeline or []):
                n = self.rt.sim.clear(gd.target_region, f"gameday:{gd.id}")
                audit.record(
                    s,
                    ts=now,
                    actor=gd.created_by,
                    action="fault.clear",
                    target=gd.target_region,
                    details={"gameday": gd.id, "cleared": n},
                )
                self._event(
                    gd, "recover", f"cleared {n} fault(s) on {gd.target_region} (region repaired)", faults_cleared=True
                )
                return
            region = s.scalar(select(Region).where(Region.name == gd.target_region))
            if region is None or region.status != RegionStatus.UP:
                return
            if scenario == FaultType.REPLICATION_FAILURE and s.scalar(
                select(Incident).where(Incident.kind == "replication_lag", Incident.status == "open")
            ):
                return
            fo = s.get(RecoveryRun, gd.failover_run_id) if gd.failover_run_id else None
            if fo and fo.status == RunStatus.COMPLETED and gd.include_failback:
                fb = self.rt.orchestrator.create(
                    s, RunKind.FAILBACK, f"game day #{gd.id}: failback", gd.created_by, game_day_id=gd.id
                )
                gd.failback_run_id = fb.id
                self._goto(gd, "failback", f"{gd.target_region} healthy; failback run #{fb.id} started")
            else:
                self._goto(gd, "report", f"{gd.target_region} healthy again")
            return

        if gd.phase == "failback":
            fb = s.get(RecoveryRun, gd.failback_run_id)
            if fb is None:
                self._goto(gd, "report", "failback run missing")
                return
            self._preapprove(s, fb, gd)
            if fb.status in TERMINAL:
                self._goto(gd, "report", f"failback run #{fb.id} {fb.status.value}")
            return

        if gd.phase == "report":
            gd.report = self._report(s, gd)
            gd.status = "completed" if gd.report["passed"] else "failed"
            gd.finished_at = now
            self._event(gd, "done", f"game day {gd.status}")
            audit.record(
                s,
                ts=now,
                actor="system",
                action="gameday.complete",
                target=f"gameday:{gd.id}",
                details={"passed": gd.report["passed"]},
            )

    def _report(self, s: Session, gd: GameDay) -> dict[str, Any]:
        fo = s.get(RecoveryRun, gd.failover_run_id) if gd.failover_run_id else None
        fb = s.get(RecoveryRun, gd.failback_run_id) if gd.failback_run_id else None
        detection = next((e.get("detection_s") for e in gd.timeline or [] if "detection_s" in e), None)
        snap = self.rt.snapshot(s)
        rd_after = readiness.compute(snap)
        baseline = next((e.get("readiness") for e in gd.timeline or [] if e.get("phase") == "baseline"), None)
        findings: list[str] = []
        passed = gd.status != "aborted" and detection is not None
        if detection is None:
            findings.append("Fault was NOT detected - monitoring gap.")
        for label, run in (("Failover", fo), ("Failback", fb)):
            if run is None:
                continue
            m = run.metrics or {}
            if run.status != RunStatus.COMPLETED:
                passed = False
                findings.append(f"{label} run #{run.id} ended {run.status.value}: {run.error}")
            if m.get("rto_met") is False:
                passed = False
                findings.append(f"{label} RTO {m.get('rto_s')}s exceeded target {m.get('rto_target_s')}s")
            if m.get("rpo_met") is False:
                passed = False
                findings.append(f"{label} RPO {m.get('rpo_s')}s exceeded target {m.get('rpo_target_s')}s")
            for r in (run.report or {}).get("recommendations", []):
                if r not in findings and "met all objectives" not in r:
                    findings.append(r)
        if gd.include_failback and fo and fo.status == RunStatus.COMPLETED and not fb:
            passed = False
            findings.append("Failback was not executed.")
        if not findings:
            findings.append("All objectives met. No action required.")

        def run_summary(run: RecoveryRun | None) -> dict[str, Any] | None:
            if run is None:
                return None
            m = run.metrics or {}
            return {
                "id": run.id,
                "status": run.status.value,
                "rto_s": m.get("rto_s"),
                "rpo_s": m.get("rpo_s"),
                "rto_met": m.get("rto_met"),
                "rpo_met": m.get("rpo_met"),
                "data_loss_txns": m.get("data_loss_txns"),
                "divergent_discarded": m.get("divergent_discarded"),
                "approved_by": run.approved_by,
            }

        return {
            "game_day": gd.id,
            "name": gd.name,
            "scenario": gd.scenario,
            "target_region": gd.target_region,
            "passed": passed,
            "detection_s": detection,
            "failover": run_summary(fo),
            "failback": run_summary(fb),
            "readiness_before": baseline,
            "readiness_after": rd_after["score"],
            "customer_impact": (self.rt.workload_state() or {}).get("last_outage"),
            "findings": findings,
            "timeline": gd.timeline,
            "generated_at": self.rt.clock.now().isoformat(),
        }
