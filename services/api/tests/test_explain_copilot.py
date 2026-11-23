"""Phase 7: stored TFT explanations, LightGBM SHAP, grounded narrative, rule-based + LLM copilot."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from conftest import Api
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.config import get_settings
from kalpa_api.db.models import ForecastExplanation
from kalpa_api.modules.copilot import llm
from kalpa_ml.explain import is_grounded
from kalpa_ml.models.tft import runner
from kalpa_ml.models.tft.train import TrainConfig

API = "/api/v1"


def _csv(days: int = 220) -> bytes:
    """3 series whose demand depends on price (so LightGBM's top driver is known); weekly price changes."""
    rng = np.random.default_rng(0)
    start = date(2025, 1, 1)
    lines = ["store,sku,date,units,price"]
    for store, sku in (("S1", "A"), ("S1", "B"), ("S2", "A")):
        price = 100.0
        for i in range(days):
            if i % 7 == 0:
                price = 100.0 * float(rng.uniform(0.7, 1.3))
            units = int(rng.poisson(12 * (price / 100) ** -2))
            lines.append(f"{store},{sku},{(start + timedelta(days=i)).isoformat()},{units},{price:.2f}")
    return "\n".join(lines).encode()


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(get_settings(), "inline_jobs", True)
    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path / "models"))
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    orig = runner.fit  # keep the TFT tiny so the test is fast; the code path is the real one
    monkeypatch.setattr(
        runner,
        "fit",
        lambda a, c, cfg, tc=None: orig(a, c, cfg, TrainConfig(epochs=1, windows_per_epoch=256, val_windows=64)),
    )


async def build(owner: Api, models: list[str], folds: int = 1) -> dict[str, str]:
    up = (await owner.post(f"{API}/data/uploads", files={"file": ("p.csv", _csv(), "text/csv")})).json()
    assert (await owner.post(f"{API}/data/uploads/{up['upload_id']}/commit", json={})).status_code == 200
    locs = {i["name"]: i["id"] for i in (await owner.get(f"{API}/locations")).json()["items"]}
    prods = {i["sku"]: i["id"] for i in (await owner.get(f"{API}/products")).json()["items"]}
    sup = (await owner.post(f"{API}/suppliers", json={"name": "Sup"})).json()["id"]
    for sku in ("A", "B"):
        await owner.post(
            f"{API}/supplier-products",
            json={"supplier_id": sup, "product_id": prods[sku], "unit_cost": 60, "lead_time_days": 3},
        )
    for loc, sku in (("S1", "A"), ("S1", "B"), ("S2", "A")):
        await owner.post(f"{API}/inventory", json={"location_id": locs[loc], "product_id": prods[sku], "on_hand": 15})
    r = await owner.post(f"{API}/models/backtests", json={"models": models, "folds": folds})
    assert r.status_code == 202 and r.json()["status"] == "succeeded", r.text
    return {"S1": locs["S1"], "S2": locs["S2"], "A": prods["A"], "B": prods["B"]}


MODELS = ["seasonal_naive", "lightgbm_tweedie", "tft"]


# ------------------------------------------------------------------ explanations
async def test_tft_explanations_are_persisted_and_served(owner_a: Api, ready: None, session: AsyncSession) -> None:
    ids = await build(owner_a, MODELS)
    n = (await session.execute(select(func.count()).select_from(ForecastExplanation))).scalar_one()
    await session.rollback()  # release the SQLite write lock this session took (test engine quirk)
    assert n == 3  # one row per series, written by the worker path

    r = await owner_a.get(f"{API}/forecasts/{ids['S1']}/{ids['A']}/explanation", params={"model": "tft"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["kind"] == "tft" and e["shap"] is None
    assert [d["name"] for d in e["static"]] == ["store", "sku", "category"]
    assert abs(sum(d["weight"] for d in e["static"]) - 1) < 0.01  # softmax weights sum to 1
    assert abs(sum(d["weight"] for d in e["future"]) - 1) < 0.02  # (sin/cos merged, rounding to 4 d.p.)
    names = {d["name"] for d in e["future"]}
    assert {"the weekly pattern", "yearly seasonality", "days to the next festival"} <= names  # no promo column here
    att = e["attention"]
    assert len(att["dates"]) == len(att["weights"]) == 56 and min(att["weights"]) >= 0
    last = (
        await owner_a.get(f"{API}/forecasts/series", params={"location_id": ids["S1"], "product_id": ids["A"]})
    ).json()
    assert att["dates"][-1] == last["history"][-1]["date"]  # the lookback ends on the last observed day


async def test_default_pick_champion_and_lightgbm_shap(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a, MODELS)
    url = f"{API}/forecasts/{ids['S1']}/{ids['A']}/explanation"
    assert (await owner_a.get(url)).json()["kind"] == "tft"  # no champion yet -> TFT (first explainable)
    runs = (await owner_a.get(f"{API}/models/runs")).json()
    lgb = next(r for r in runs if r["model_name"] == "lightgbm_tweedie")
    assert (await owner_a.post(f"{API}/models/runs/{lgb['id']}/promote")).status_code == 200
    e = (await owner_a.get(url)).json()
    assert e["kind"] == "lightgbm" and e["attention"] is None  # the champion is explainable, so it is used
    vals = [r["mean_abs"] for r in e["shap"]]
    assert vals == sorted(vals, reverse=True) and len(e["shap"]) == 12  # all model features, largest first
    assert {r["feature"]: r["label"] for r in e["shap"]}["price_ratio"] == "price vs usual"
    assert "log-demand" in e["note"]  # the scale is stated, not hidden
    assert (await owner_a.get(url, params={"model": "seasonal_naive"})).json()["code"] == "not_explainable"


async def test_no_explainable_run_is_a_clear_404(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a, ["seasonal_naive"])
    r = await owner_a.get(f"{API}/forecasts/{ids['S1']}/{ids['A']}/explanation")
    assert r.status_code == 404 and r.json()["code"] == "no_explainable_run"


async def test_other_org_cannot_read_explanations_or_narratives(owner_a: Api, owner_b: Api, ready: None) -> None:
    ids = await build(owner_a, MODELS)
    for url in (
        f"{API}/forecasts/{ids['S1']}/{ids['A']}/explanation",
        f"{API}/explain/{ids['S1']}/{ids['A']}/narrative",
    ):
        r = await owner_b.get(url)
        assert r.status_code == 404, (url, r.text)  # org B has no runs at all, and never sees A's rows


# ------------------------------------------------------------------ narrative
async def test_narrative_is_a_grounded_template_without_a_key(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a, MODELS)
    r = await owner_a.get(f"{API}/explain/{ids['S1']}/{ids['A']}/narrative", params={"days": 14})
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["source"] == "template" and n["text"].startswith("Demand for A at S1 is expected to")
    assert is_grounded(n["text"], n["facts"])
    f = n["facts"]
    assert f["horizon_days"] == 14 and f["recent_units"] > 0 and f["forecast_units"] > 0
    assert 1 <= len(f["drivers"]) <= 2 and f["model"] in ("tft", "lightgbm_tweedie")


def _fake_llm(reply: str) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(200, json={"content": [{"type": "text", "text": reply}]}))


async def test_llm_polish_is_accepted_only_when_every_number_is_in_the_facts(
    owner_a: Api, ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await build(owner_a, MODELS)
    url = f"{API}/explain/{ids['S1']}/{ids['A']}/narrative"
    f = (await owner_a.get(url)).json()["facts"]
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    good = f"Expect {f['forecast_units']} units over the next {f['horizon_days']} days."
    monkeypatch.setattr(llm, "transport", _fake_llm(good))
    n = (await owner_a.get(url)).json()
    assert (n["source"], n["text"]) == ("llm", good)
    monkeypatch.setattr(llm, "transport", _fake_llm("Sales will jump 9731 units, a 412% surge."))
    n = (await owner_a.get(url)).json()
    assert n["source"] == "template" and "9731" not in n["text"]  # fabricated figures never reach the user
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(lambda req: httpx.Response(500)))
    assert (await owner_a.get(url)).json()["source"] == "template"  # API failure -> template, not an error


# ------------------------------------------------------------------ copilot
SCRIPTED = [
    ("Which model is the most accurate?", "compare_models"),
    ("Compare the models for me", "compare_models"),
    ("Which model is cheapest?", "compare_models"),
    ("What is at risk of stock-out?", "get_stockout_risks"),
    ("Which products will run out soon?", "get_stockout_risks"),
    ("What should I reorder?", "get_recommendations"),
    ("Which purchase orders do you recommend?", "get_recommendations"),
    ("What is the forecast for A at S1?", "get_forecast"),
    ("Why is A at S1 forecast to change?", "explain_forecast"),
    ("What is the capital of France?", None),
]


async def test_ten_scripted_questions_with_rule_based_copilot(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a, MODELS)
    assert (await owner_a.get(f"{API}/copilot/status")).json() == {"mode": "rule-based"}
    arena = (await owner_a.get(f"{API}/models/arena")).json()
    recs = (await owner_a.get(f"{API}/policy/recommendations")).json()
    risky = [
        i
        for i in (await owner_a.get(f"{API}/policy/recommendations", params={"only_orders": "false"})).json()["items"]
        if i["stockout_date"]
    ]
    series = (
        await owner_a.get(f"{API}/forecasts/series", params={"location_id": ids["S1"], "product_id": ids["A"]})
    ).json()
    end = series["history"][-1]["date"]
    champ = arena[0]["model_name"]  # best WAPE (no champion promoted)
    future = [
        p["p50"] for p in next(m for m in series["models"] if m["model_name"] == champ)["points"] if p["date"] > end
    ]

    got: dict[str, Any] = {}
    for question, tool in SCRIPTED:
        r = await owner_a.post(f"{API}/copilot/chat", json={"question": question})
        assert r.status_code == 200, (question, r.text)
        out = r.json()
        got[question] = out
        if tool is None:
            assert (
                out["mode"] == "refused"
                and out["tools"] == []
                and out["sources"] == []
                and "only answer" in out["answer"]
            )
        else:
            assert out["mode"] == "rule-based" and out["tools"] == [tool] and out["sources"], (question, out)

    best = arena[0]["model_name"].replace("_", " ")
    assert best in got["Which model is the most accurate?"]["answer"]
    assert f"({round(arena[0]['metrics']['wape'] * 100, 1)}%)" in got["Compare the models for me"]["answer"]
    assert "No replay cost is available" in got["Which model is cheapest?"]["answer"]  # 1 fold: no replay
    risk = got["What is at risk of stock-out?"]["answer"]
    if risky:
        assert f"{len(risky)} series are projected" in risk and risky[0]["stockout_date"] in risk
    else:
        assert "No series is projected" in risk
    if recs["items"]:
        assert f"{len(recs['items'])} order lines are recommended" in got["What should I reorder?"]["answer"]
        assert str(recs["items"][0]["recommended_qty"]) in got["What should I reorder?"]["answer"]
    fc = got["What is the forecast for A at S1?"]["answer"]
    assert f"sell {round(sum(future[:7]))} units in the next 7 days" in fc  # the exact stored P50 sum
    assert f"{round(sum(future[:14]))} in 14 days" in fc and f"{round(sum(future[:28]))} in 28 days" in fc
    assert got["Why is A at S1 forecast to change?"]["answer"].startswith("Demand for A at S1")


async def test_copilot_needs_series_for_forecast_questions_and_validates_input(owner_a: Api, ready: None) -> None:
    await build(owner_a, MODELS)
    out = (await owner_a.post(f"{API}/copilot/chat", json={"question": "What is the forecast?"})).json()
    assert out["tools"] == [] and "which product and which store" in out["answer"]
    assert (await owner_a.post(f"{API}/copilot/chat", json={"question": "hi"})).status_code == 422  # too short
    assert (await owner_a.post(f"{API}/copilot/chat", json={"question": "x" * 501})).status_code == 422


async def test_copilot_is_tenant_scoped(owner_a: Api, owner_b: Api, ready: None) -> None:
    await build(owner_a, MODELS)
    out = (await owner_b.post(f"{API}/copilot/chat", json={"question": "Compare the models"})).json()
    assert out["answer"].startswith("No model has been evaluated yet")  # org B sees nothing of org A
    out = (await owner_b.post(f"{API}/copilot/chat", json={"question": "What is the forecast for A at S1?"})).json()
    assert "could not find" in out["answer"] or "which product" in out["answer"]


def _tool_then_text(make_text: Any) -> httpx.MockTransport:
    """Fake Messages API: 1st call asks for compare_models; 2nd call (with the tool result) returns make_text(result)."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        last = body["messages"][-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            result = json.loads(last["content"][0]["content"])
            return httpx.Response(200, json={"content": [{"type": "text", "text": make_text(result)}]})
        return httpx.Response(
            200, json={"content": [{"type": "tool_use", "id": "t1", "name": "compare_models", "input": {}}]}
        )

    return httpx.MockTransport(handler)


async def test_llm_copilot_uses_tools_and_rejects_ungrounded_answers(
    owner_a: Api, ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await build(owner_a, MODELS)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    assert (await owner_a.get(f"{API}/copilot/status")).json() == {"mode": "llm"}
    q = {"question": "Which model should I trust?"}
    monkeypatch.setattr(
        llm,
        "transport",
        _tool_then_text(lambda r: f"{r['best_wape']} is the most accurate, WAPE {r['models'][0]['wape_pct']}%."),
    )
    ok = (await owner_a.post(f"{API}/copilot/chat", json=q)).json()
    assert ok["mode"] == "llm" and ok["tools"] == ["compare_models"] and ok["sources"]
    monkeypatch.setattr(llm, "transport", _tool_then_text(lambda r: "It is 99.9% accurate with 12345 stockouts."))
    bad = (await owner_a.post(f"{API}/copilot/chat", json=q)).json()
    assert bad["mode"] == "rule-based" and "99.9" not in bad["answer"]  # falls back to the tool-built answer
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(lambda req: httpx.Response(500)))
    down = (await owner_a.post(f"{API}/copilot/chat", json={"question": "Compare the models"})).json()
    assert down["mode"] == "rule-based"  # LLM outage never breaks the copilot


def test_every_rule_based_sentence_is_grounded_in_its_tool_result() -> None:
    from kalpa_api.modules.copilot.service import inr, render

    cases = {
        "get_forecast": {
            "product": "Kaju Katli",
            "location": "HSR Layout",
            "windows": [7, 14, 28],
            "next_7d_units": 61,
            "next_14d_units": 138,
            "next_28d_units": 290,
            "recent_14d_units": 100,
            "peak_day": "2025-11-03",
            "peak_units": 12,
            "source": "tft run 20251122-101500 (data to 2025-10-20)",
        },
        "get_stockout_risks": {
            "count": 4,
            "items": [
                {"product": "P", "location": "L", "on_hand": 3, "stockout_date": "2025-11-03", "value_at_risk": 125875}
            ],
            "source": "tft run for data to 2025-10-20",
        },
        "get_recommendations": {
            "count": 2,
            "items": [{"product": "P", "location": "L", "supplier": "Sup", "recommended_qty": 48, "lead_days": 6}],
            "source": "x",
        },
        "compare_models": {
            "models": [
                {"model": "lightgbm tweedie", "wape_pct": 39.7, "cost_inr": 125875},
                {"model": "tft", "wape_pct": 39.8, "cost_inr": 165829},
            ],
            "costed_models": 2,
            "min_folds_for_cost": 2,
            "best_wape": "lightgbm tweedie",
            "cheapest": "lightgbm tweedie",
            "source": "latest backtest run of each model",
        },
    }
    cases["compare_models_no_cost"] = {
        "models": [{"model": "tft", "wape_pct": 39.8}],
        "best_wape": "tft",
        "cheapest": None,
        "costed_models": 0,
        "min_folds_for_cost": 2,
        "source": "latest backtest run of each model",
    }
    for tool, result in cases.items():
        tool = tool.removesuffix("_no_cost")
        assert is_grounded(render(tool, result), result), (tool, render(tool, result))
    assert (
        inr(125875) == "₹1,25,875" and inr(999) == "₹999" and inr(1000) == "₹1,000" and inr(12345678) == "₹1,23,45,678"
    )


async def test_cheapest_question_uses_replay_cost_when_two_folds_exist(owner_a: Api, ready: None) -> None:
    await build(owner_a, ["seasonal_naive", "lightgbm_tweedie"], folds=2)
    arena = (await owner_a.get(f"{API}/models/arena")).json()
    costs = {a["model_name"]: a["metrics"]["cost"] for a in arena}
    assert all(isinstance(c, float) for c in costs.values()), costs
    cheapest = min(costs, key=lambda k: costs[k]).replace("_", " ")
    out = (await owner_a.post(f"{API}/copilot/chat", json={"question": "Which model is cheapest?"})).json()
    assert cheapest in out["answer"] and "replay" in out["answer"], out["answer"]
    rupees = f"{int(round(costs[min(costs, key=lambda k: costs[k])])):,}"  # western grouping, digits compared below
    assert "".join(ch for ch in rupees if ch.isdigit()) in "".join(ch for ch in out["answer"] if ch.isdigit())


def test_a_rupee_sign_in_a_log_line_cannot_crash_a_cp1252_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: on a Windows console the copilot's logged answer (with a rupee sign) raised UnicodeEncodeError -> 500."""
    import io
    import sys

    from kalpa_api.main import _safe_console

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    with pytest.raises(UnicodeEncodeError):
        print("\u20b91,25,875", file=console, flush=True)  # the failure mode, reproduced
    _safe_console()
    print("\u20b91,25,875", flush=True)  # now degrades to '?' instead of raising
    assert raw.getvalue().endswith(b"?1,25,875\r\n") or raw.getvalue().endswith(b"?1,25,875\n")
