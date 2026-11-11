from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app import audit
from app.api.deps import Principal, get_rt, require
from app.copilot.copilot import Copilot
from app.engine.runtime import Runtime
from app.security import Perm

router = APIRouter(prefix="/copilot", tags=["copilot"])


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


@router.post("/chat")
def chat(body: AskIn, rt: Runtime = Depends(get_rt), p: Principal = Depends(require(Perm.COPILOT))) -> dict:
    with rt.db.session() as s:
        out = Copilot(rt).ask(s, p.role, body.message, [t.model_dump() for t in body.history])
        audit.record(
            s,
            ts=rt.clock.now(),
            actor=p.username,
            action="copilot.ask",
            details={
                "question": body.message[:300],
                "engine": out.get("engine"),
                "tools": out.get("tools_used"),
                "proposals": [a["action"] for a in out.get("suggested_actions", [])],
            },
            ip=p.ip,
        )
        return out


@router.get("/info")
def info(rt: Runtime = Depends(get_rt), _: Principal = Depends(require(Perm.COPILOT))) -> dict:
    return {
        "engine": "llm" if rt.settings.anthropic_api_key else "rules",
        "model": rt.settings.copilot_model if rt.settings.anthropic_api_key else None,
        "capabilities": "read-only tools + action proposals (no execution)",
    }
