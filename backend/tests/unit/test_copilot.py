from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import func, select

from app.copilot.copilot import Copilot
from app.models import FaultType, RecoveryRun, Role, SimFault
from tests.conftest import run_ticks


def _state_fingerprint(rt):
    with rt.db.session() as s:
        return (rt.traffic.active_region(), s.scalar(select(func.count()).select_from(RecoveryRun)),
                s.scalar(select(func.count()).select_from(SimFault)))


def test_rules_copilot_answers_from_real_state_and_never_mutates(rt, clock):
    run_ticks(rt, clock, 4)
    rt.sim.inject("aws-us-east-1", FaultType.CLOUD_OUTAGE, "t")
    run_ticks(rt, clock, 3)
    before = _state_fingerprint(rt)
    with rt.db.session() as s:
        out = Copilot(rt).ask(s, Role.VIEWER, "What is happening and what should we do? What's our RPO?", [])
    assert out["engine"] == "rules"
    assert "DOWN" in out["answer"] and "failover" in out["answer"].lower()
    assert "RPO" in out["answer"]
    approve = [a for a in out["suggested_actions"] if a["action"] == "approve_run"]
    assert approve and approve[0]["permitted"] is False  # viewer may not approve
    assert _state_fingerprint(rt) == before


def test_tools_respect_rbac(rt):
    cp = Copilot(rt)
    assert "recent_audit_events" not in {t["name"] for t in cp.tool_specs(Role.VIEWER)}
    assert "recent_audit_events" in {t["name"] for t in cp.tool_specs(Role.APPROVER)}
    with rt.db.session() as s:
        assert "error" in cp.call_tool(s, Role.VIEWER, "recent_audit_events", {}, [])
        assert "error" in cp.call_tool(s, Role.ADMIN, "drop_database", {}, [])
        props: list = []
        res = cp.call_tool(s, Role.OPERATOR, "propose_action", {"action": "rm -rf", "reason": "x"}, props)
        assert "error" in res and props == []


def test_readiness_question(rt, clock):
    run_ticks(rt, clock, 4)
    with rt.db.session() as s:
        out = Copilot(rt).ask(s, Role.OPERATOR, "how ready are we?", [])
    assert "Readiness is" in out["answer"] and "get_readiness" in out["tools_used"]


def _block(**kw):
    return SimpleNamespace(**kw)


def test_llm_loop_executes_only_read_tools(rt, clock):
    run_ticks(rt, clock, 3)
    rt.settings.anthropic_api_key = "test-key-not-real"
    responses = [
        SimpleNamespace(stop_reason="tool_use", model="m", content=[
            _block(type="tool_use", id="t1", name="get_readiness", input={}),
            _block(type="tool_use", id="t2", name="propose_action",
                   input={"action": "create_backup", "reason": "no backups"})]),
        SimpleNamespace(stop_reason="end_turn", model="m", content=[_block(type="text", text="Score is fine.")]),
    ]
    fake = MagicMock()
    fake.beta.messages.create.side_effect = responses
    with patch("anthropic.Anthropic", return_value=fake), rt.db.session() as s:
        out = Copilot(rt).ask(s, Role.OPERATOR, "status?", [])
    assert out["engine"] == "llm" and out["answer"] == "Score is fine."
    assert out["tools_used"] == ["get_readiness", "propose_action"]
    assert out["suggested_actions"][0]["path"] == "/api/v1/backups" and out["suggested_actions"][0]["permitted"]
    second_call = fake.beta.messages.create.call_args_list[1].kwargs
    assert second_call["messages"][2]["content"][0]["type"] == "tool_result"  # user, assistant, tool_results
    assert second_call["model"] == "claude-opus-5-5"


def test_llm_failure_falls_back_to_rules(rt, clock):
    run_ticks(rt, clock, 2)
    rt.settings.anthropic_api_key = "test-key-not-real"
    fake = MagicMock()
    fake.beta.messages.create.side_effect = ConnectionError("offline")
    with patch("anthropic.Anthropic", return_value=fake), rt.db.session() as s:
        out = Copilot(rt).ask(s, Role.VIEWER, "status", [])
    assert out["engine"] == "rules" and "LLM unavailable" in out["note"]
