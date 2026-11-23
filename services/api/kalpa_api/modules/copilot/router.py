"""Copilot endpoints (read-only). The app works fully with the copilot unused or no API key set."""

from typing import Annotated

from fastapi import APIRouter, Depends

from kalpa_api.core.deps import Principal, Session, require
from kalpa_api.core.ratelimit import chat_limit, per_user
from kalpa_api.modules.copilot import llm, service
from kalpa_api.modules.copilot.service import ChatIn, ChatOut

router = APIRouter(prefix="/copilot", tags=["copilot"])
Viewer = Annotated[Principal, Depends(require("view"))]


@router.get("/status", response_model=dict[str, str], operation_id="copilotStatus")
async def status(p: Viewer) -> dict[str, str]:
    """Which mode the copilot is in: 'llm' (tool-calling with a key) or 'rule-based' (no key needed)."""
    return {"mode": "llm" if llm.enabled() else "rule-based"}


@router.post(
    "/chat",
    response_model=ChatOut,
    operation_id="copilotChat",
    dependencies=[Depends(per_user("chat", chat_limit, 60))],
)
async def chat(body: ChatIn, session: Session, p: Viewer) -> ChatOut:
    """Ask about this organisation's forecasts, risks, recommendations or models; answers cite their source."""
    return await service.chat(session, p, body.question)
