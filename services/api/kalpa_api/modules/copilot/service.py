"""Copilot: read-only tools over this organisation's data, a rule-based router (no key needed) and an optional LLM.

Every answer is built from tool results; any number in it must occur in those results (otherwise the rule-based
answer is used). Questions that match no tool are refused. Prompts and answers are logged (no secrets).
"""

import re
import uuid
from datetime import date, timedelta
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select

from kalpa_api.core.deps import Principal, Session
from kalpa_api.core.errors import ApiError
from kalpa_api.db.models import Forecast, Location, Product, SalesDaily
from kalpa_api.modules.copilot import llm
from kalpa_api.modules.explain import service as explain_service
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_api.modules.policy.service import compute_recommendations
from kalpa_ml.explain import is_grounded

log = structlog.get_logger()
REFUSAL = (
    "I can only answer questions about this organisation's forecasts, stock-out risk, replenishment "
    "recommendations and model comparison."
)
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_forecast",
        "description": "Forecast units (P50) for one product at one store: next 7/14/28 days, peak day, recent sales.",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}, "product": {"type": "string"}},
            "required": ["location", "product"],
        },
    },
    {
        "name": "get_stockout_risks",
        "description": "Series projected to run out of stock within the forecast window, largest value at risk first.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "get_recommendations",
        "description": "Replenishment orders to place now (quantity, supplier, reasons), largest value at risk first.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "compare_models",
        "description": "Leaderboard of forecasting models: WAPE, bias, interval coverage, replay cost and fill rate.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "explain_forecast",
        "description": "Why the forecast for one product at one store is what it is (main drivers).",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}, "product": {"type": "string"}},
            "required": ["location", "product"],
        },
    },
]


class ChatIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class ChatOut(BaseModel):
    answer: str
    mode: Literal["llm", "rule-based", "refused"]
    sources: list[str]
    tools: list[str]


def inr(n: float) -> str:
    """Whole rupees with Indian digit grouping: 125875 -> ₹1,25,875."""
    s = str(int(round(abs(n))))
    head, tail = s[:-3], s[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ("-" if n < 0 else "") + "₹" + ",".join([*parts, tail])


# ------------------------------------------------------------------ name resolution


async def _names(session: Session, org_id: uuid.UUID) -> tuple[list[Location], list[Product]]:
    locs = (
        await session.execute(select(Location).where(Location.org_id == org_id, Location.type == "store"))
    ).scalars()
    prods = (await session.execute(select(Product).where(Product.org_id == org_id))).scalars()
    return list(locs), list(prods)


def _find(text: str, items: list[Any], keys: tuple[str, ...], word: bool = False) -> Any | None:
    """Best match of a name/SKU in `text`. word=True needs whole-word hits (free-text questions)."""
    t = text.lower().strip()
    if not t:
        return None

    def hit(i: Any) -> bool:
        for k in keys:
            v = str(getattr(i, k)).lower()
            if word and re.search(rf"(?<!\w){re.escape(v)}(?!\w)", t):
                return True
            if not word and (v in t or (len(t) >= 2 and t in v)):
                return True
        return False

    hits = [i for i in items if hit(i)]
    return max(hits, key=lambda i: len(str(getattr(i, keys[0])))) if hits else None


async def _resolve(session: Session, p: Principal, location: str, product: str) -> tuple[Location, Product] | str:
    locs, prods = await _names(session, p.org_id)
    loc, prod = _find(location, locs, ("name",)), _find(product, prods, ("name", "sku"))
    if loc is None:
        return "I could not find that store. Stores: " + ", ".join(sorted(x.name for x in locs)[:8])
    if prod is None:
        return "I could not find that product (use its name or SKU)."
    if not p.can_access_location(loc.id):
        return "You do not have access to that store."
    return loc, prod


# ------------------------------------------------------------------ tools


async def _forecast_run(session: Session, p: Principal) -> Any:
    runs = await _latest_runs(session, p.org_id)
    if not runs:
        raise ApiError(404, "no_forecasts", "Run a backtest first")
    return next((r for r in runs if r.is_champion), runs[0])


def _src(run: Any) -> str:
    return f"{run.model_name.replace('_', ' ')} run {run.version} (data to {(run.config or {}).get('data_end')})"


async def tool_get_forecast(session: Session, p: Principal, location: str, product: str) -> dict[str, Any]:
    """P50 totals over 7/14/28 days for one series (quantiles are never summed across days)."""
    r = await _resolve(session, p, location, product)
    if isinstance(r, str):
        return {"error": r}
    loc, prod = r
    run = await _forecast_run(session, p)
    end = date.fromisoformat(str((run.config or {}).get("data_end")))
    pts = (
        await session.execute(
            select(Forecast.target_date, Forecast.p50)
            .where(
                Forecast.org_id == p.org_id,
                Forecast.run_id == run.id,
                Forecast.location_id == loc.id,
                Forecast.product_id == prod.id,
                Forecast.target_date > end,
            )
            .order_by(Forecast.target_date)
        )
    ).all()
    if not pts:
        return {"error": "No forecast stored for that product at that store."}
    recent = (
        (
            await session.execute(
                select(SalesDaily.units).where(
                    SalesDaily.org_id == p.org_id,
                    SalesDaily.location_id == loc.id,
                    SalesDaily.product_id == prod.id,
                    SalesDaily.date > end - timedelta(days=14),
                    SalesDaily.date <= end,
                )
            )
        )
        .scalars()
        .all()
    )
    peak = max(pts, key=lambda x: x[1])

    def tot(n: int) -> int:
        return round(sum(float(v) for _, v in pts[:n]))

    return {
        "product": prod.name,
        "location": loc.name,
        "windows": [7, 14, 28],
        "next_7d_units": tot(7),
        "next_14d_units": tot(14),
        "next_28d_units": tot(28),
        "recent_14d_units": round(sum(recent)),
        "peak_day": str(peak[0]),
        "peak_units": round(float(peak[1])),
        "source": _src(run),
    }


async def tool_get_stockout_risks(session: Session, p: Principal, limit: int = 5) -> dict[str, Any]:
    """Series whose stock runs out inside the forecast window."""
    rs = await compute_recommendations(session, p.org_id, p.location_ids, None, None, False)
    risky = [i for i in rs.items if i.stockout_date is not None]
    return {
        "count": len(risky),
        "items": [
            {
                "product": i.product,
                "location": i.location,
                "on_hand": i.on_hand,
                "stockout_date": str(i.stockout_date),
                "value_at_risk": round(i.value_at_risk),
            }
            for i in risky[: max(1, min(int(limit), 10))]
        ],
        "source": f"{rs.model.replace('_', ' ')} run for data to {rs.as_of}",
    }


async def tool_get_recommendations(session: Session, p: Principal, limit: int = 5) -> dict[str, Any]:
    """Orders to place now."""
    rs = await compute_recommendations(session, p.org_id, p.location_ids, None, None, True)
    return {
        "count": len(rs.items),
        "items": [
            {
                "product": i.product,
                "location": i.location,
                "supplier": i.supplier,
                "recommended_qty": i.recommended_qty,
                "lead_days": i.lead_days,
            }
            for i in rs.items[: max(1, min(int(limit), 10))]
        ],
        "source": f"{rs.model.replace('_', ' ')} run for data to {rs.as_of}",
    }


async def tool_compare_models(session: Session, p: Principal) -> dict[str, Any]:
    """Leaderboard rows with only the metrics that exist."""
    runs = await _latest_runs(session, p.org_id)
    rows = []
    for r in runs:
        m: dict[str, Any] = dict(r.metrics or {})
        row: dict[str, Any] = {"model": r.model_name.replace("_", " ")}
        for k, name, pct in (
            ("wape", "wape_pct", True),
            ("bias", "bias_pct", True),
            ("coverage80", "coverage80_pct", True),
            ("fill_rate", "fill_rate_pct", True),
        ):
            if isinstance(m.get(k), int | float):
                row[name] = round(float(m[k]) * 100, 1) if pct else float(m[k])
        if isinstance(m.get("cost"), int | float):
            row["cost_inr"] = round(float(m["cost"]))
        rows.append(row)
    costed = [r for r in rows if "cost_inr" in r]
    return {
        "models": rows,
        "best_wape": rows[0]["model"] if rows else None,  # _latest_runs sorts by WAPE
        "cheapest": min(costed, key=lambda r: r["cost_inr"])["model"] if costed else None,
        "costed_models": len(costed),
        "min_folds_for_cost": 2,  # the replay needs the previous fold's residuals for safety stock
        "source": "latest backtest run of each model",
    }


async def tool_explain_forecast(session: Session, p: Principal, location: str, product: str) -> dict[str, Any]:
    """Narrative + its facts for one series."""
    r = await _resolve(session, p, location, product)
    if isinstance(r, str):
        return {"error": r}
    n = await explain_service.narrative(session, p, r[0].id, r[1].id, 14, None)
    return {"text": n.text, "facts": n.facts, "source": f"{n.facts.get('model') or 'forecast'} explanation"}


async def run_tool(session: Session, p: Principal, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call; unknown tools and API errors become an error result, never an exception."""
    try:
        if name == "get_forecast":
            return await tool_get_forecast(session, p, str(args.get("location", "")), str(args.get("product", "")))
        if name == "explain_forecast":
            return await tool_explain_forecast(session, p, str(args.get("location", "")), str(args.get("product", "")))
        if name == "get_stockout_risks":
            return await tool_get_stockout_risks(session, p, int(args.get("limit", 5)))
        if name == "get_recommendations":
            return await tool_get_recommendations(session, p, int(args.get("limit", 5)))
        if name == "compare_models":
            return await tool_compare_models(session, p)
    except ApiError as e:
        return {"error": e.message}
    return {"error": f"unknown tool {name}"}


# ------------------------------------------------------------------ rule-based router + renderers


def classify(q: str) -> str | None:
    """Pick the tool for a question by keywords (rule-based mode)."""
    t = q.lower()
    if any(w in t for w in ("compare", "best model", "which model", "leaderboard", "accuracy", "wape", "cheapest")):
        return "compare_models"
    if any(w in t for w in ("stock-out", "stockout", "stock out", "run out", "running out", "at risk", "shortage")):
        return "get_stockout_risks"
    if any(w in t for w in ("why", "explain", "driver", "reason")):
        return "explain_forecast"
    if any(w in t for w in ("order", "reorder", "replenish", "recommend", "purchase", "buy")):
        return "get_recommendations"
    if any(w in t for w in ("forecast", "demand", "expect", "sell", "next week", "next month", "next 7", "next 14")):
        return "get_forecast"
    return None


def render(tool: str, r: dict[str, Any]) -> str:
    """Turn a tool result into a sentence; every number comes from the result."""
    if "error" in r:
        return str(r["error"])
    src = f" Source: {r['source']}." if r.get("source") else ""
    if tool == "get_forecast":
        return (
            f"{r['product']} at {r['location']} is forecast to sell {r['next_7d_units']} units in the next 7 days, "
            f"{r['next_14d_units']} in 14 days and {r['next_28d_units']} in 28 days (median forecast), against "
            f"{r['recent_14d_units']} in the last 14 days. The busiest day is {r['peak_day']} "
            f"with {r['peak_units']} units." + src
        )
    if tool == "explain_forecast":
        return f"{r['text']}{src}"
    if tool == "get_stockout_risks":
        if not r["count"]:
            return "No series is projected to run out of stock within the forecast window." + src
        top = r["items"][0]
        return (
            f"{r['count']} series are projected to run out of stock within the forecast window. The largest value at "
            f"risk is {top['product']} at {top['location']}: {top['on_hand']} on hand, running out on "
            f"{top['stockout_date']}, {inr(top['value_at_risk'])} at risk." + src
        )
    if tool == "get_recommendations":
        if not r["count"]:
            return "No orders are recommended right now." + src
        top = r["items"][0]
        return (
            f"{r['count']} order lines are recommended. The first is {top['recommended_qty']} units of "
            f"{top['product']} for {top['location']} from {top['supplier']} (lead time {top['lead_days']} days)." + src
        )
    if tool == "compare_models":
        if not r["models"]:
            return "No model has been evaluated yet."
        best = r["models"][0]
        s = f"{best['model']} has the lowest WAPE" + (f" ({best['wape_pct']}%)" if "wape_pct" in best else "") + "."
        if not r.get("costed_models"):
            s += (
                f" No replay cost is available yet: it needs a backtest with at least {r['min_folds_for_cost']} folds "
                "and supplier terms."
            )
        if r.get("cheapest"):
            ch = next(m for m in r["models"] if m["model"] == r["cheapest"])
            same = r["cheapest"] == r["best_wape"]
            s += (
                f" It is also the cheapest in the replay ({inr(ch['cost_inr'])})."
                if same
                else f" The cheapest in the replay is {ch['model']} ({inr(ch['cost_inr'])}), so the most accurate "
                "model is not the cheapest one."
            )
        return s + src
    return REFUSAL


async def _mentions(session: Session, p: Principal, q: str) -> tuple[str, str]:
    locs, prods = await _names(session, p.org_id)
    loc, prod = _find(q, locs, ("name",), True), _find(q, prods, ("name", "sku"), True)
    return (loc.name if loc else ""), (prod.name if prod else "")


async def chat(session: Session, p: Principal, question: str) -> ChatOut:
    """Answer a question: LLM tool-calling when a key is set (guarded), else rule-based; refuse out-of-scope."""
    used: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    answer: str | None = None
    mode: Literal["llm", "rule-based", "refused"] = "rule-based"
    if llm.enabled():

        async def runner(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await run_tool(session, p, name, args)

        text, used = await llm.chat_with_tools(question, TOOLS, runner)
        if text and used and is_grounded(text, [u[2] for u in used]):
            answer, mode = text, "llm"
        else:
            used = []  # rejected or failed: fall through to the rule-based path
    if answer is None:
        tool = classify(question)
        if tool is None:
            answer, mode = REFUSAL, "refused"
        else:
            loc, prod = await _mentions(session, p, question)
            args: dict[str, Any] = {"location": loc, "product": prod}
            if tool in ("get_forecast", "explain_forecast") and not (loc and prod):
                answer = "Tell me which product and which store, for example: 'Why is <product> at <store> going up?'"
                mode = "rule-based"
            else:
                result = await run_tool(session, p, tool, args if tool in ("get_forecast", "explain_forecast") else {})
                used = [(tool, args, result)]
                answer = render(tool, result)
    sources = sorted({str(u[2]["source"]) for u in used if "source" in u[2]})
    log.info(
        "copilot_chat",
        org_id=str(p.org_id),
        user_id=str(p.user.id),
        question=question,
        mode=mode,
        tools=[u[0] for u in used],
        answer=answer,
    )
    return ChatOut(answer=answer, mode=mode, sources=sources, tools=[u[0] for u in used])
