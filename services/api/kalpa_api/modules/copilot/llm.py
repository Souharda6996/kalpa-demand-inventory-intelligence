"""Optional Claude access (Messages API over httpx). Nothing here runs unless ANTHROPIC_API_KEY is set.

Only structured facts / tool results are ever sent; the caller validates every number in the reply.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from kalpa_api.core.config import get_settings

log = structlog.get_logger()
API_URL = "https://api.anthropic.com/v1/messages"
MAX_TOOL_ROUNDS = 4
transport: httpx.AsyncBaseTransport | None = None  # tests inject a fake transport; None = real network

POLISH_SYSTEM = (
    "You rewrite a demand-forecast explanation for a retail planner in one or two plain sentences. "
    "Use ONLY the facts in the JSON. Do not add, compute or round any number; every number you write must appear "
    "in the JSON exactly. Do not mention anything that is not in the JSON."
)
CHAT_SYSTEM = (
    "You are Kalpa's read-only planning assistant. Answer ONLY from the results of the provided tools, which return "
    "this organisation's data. Always call a tool before answering. Never state a number that is not in a tool result. "
    "If the question is not about forecasts, stock-out risk, replenishment or model comparison for this organisation, "
    "say you can only help with those. Keep answers short."
)


def enabled() -> bool:
    """True when an API key is configured."""
    return bool(get_settings().anthropic_api_key)


async def _post(payload: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(transport=transport, timeout=30) as c:
        r = await c.post(
            API_URL,
            headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={"model": s.copilot_model, **payload},
        )
        r.raise_for_status()
        return dict(r.json())


def _text(resp: dict[str, Any]) -> str:
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()


async def polish_narrative(facts: dict[str, Any]) -> str | None:
    """LLM rewording of the template facts, or None (no key / any failure). The caller still validates numbers."""
    if not enabled():
        return None
    try:
        import json

        resp = await _post(
            {
                "max_tokens": 200,
                "system": POLISH_SYSTEM,
                "messages": [{"role": "user", "content": json.dumps(facts)}],
            }
        )
        return _text(resp) or None
    except Exception as e:  # noqa: BLE001 - the template is always a safe fallback
        log.warning("narrative_polish_failed", error=type(e).__name__)
        return None


ToolRunner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def chat_with_tools(
    question: str, tools: list[dict[str, Any]], run_tool: ToolRunner
) -> tuple[str | None, list[tuple[str, dict[str, Any], dict[str, Any]]]]:
    """Tool-calling loop. Returns (final text or None, [(tool, args, result)]). Raises nothing on API errors."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await _post({"max_tokens": 500, "system": CHAT_SYSTEM, "tools": tools, "messages": messages})
            uses = [b for b in resp.get("content", []) if b.get("type") == "tool_use"]
            if not uses:
                return (_text(resp) or None), calls
            messages.append({"role": "assistant", "content": resp["content"]})
            results = []
            for u in uses:
                out = await run_tool(u["name"], u.get("input", {}))
                calls.append((u["name"], u.get("input", {}), out))
                import json

                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out)})
            messages.append({"role": "user", "content": results})
    except Exception as e:  # noqa: BLE001
        log.warning("copilot_llm_failed", error=type(e).__name__)
    return None, calls
