"""Explanations of a forecast: TFT variable-selection weights + attention, LightGBM SHAP, and a grounded narrative."""

import asyncio
import json
import uuid
from datetime import date, timedelta
from typing import Any, Literal, cast

import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select

from kalpa_api.core.deps import Principal, Session
from kalpa_api.core.errors import ApiError
from kalpa_api.db.models import CalendarEvent, Forecast, ForecastExplanation, Location, ModelRun, Product, SalesDaily
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_api.modules.scenarios.router import _artifacts, _load
from kalpa_ml.explain import (
    build_facts,
    grounded_or_template,
    group_weights,
    lgbm_shap,
    shap_drivers,
)

EXPLAINABLE = ("tft", "lightgbm_tweedie")
FEATURE_LABELS = {
    "dow_sin": "weekday (sin)",
    "dow_cos": "weekday (cos)",
    "doy_sin": "day of year (sin)",
    "doy_cos": "day of year (cos)",
    "days_to_festival": "days to next festival",
    "festival_window": "festival window",
    "price_ratio": "price vs usual",
    "promo": "promotion",
    "lag7": "sales 7 days earlier",
    "lag14": "sales 14 days earlier",
    "lag28": "sales 28 days earlier",
    "rolling_mean_lag7_window_size7": "7-day average (lagged 7 d)",
    "rolling_mean_lag28_window_size28": "28-day average (lagged 28 d)",
}


class Driver(BaseModel):
    name: str
    weight: float


class Attention(BaseModel):
    dates: list[date]
    weights: list[float]


class ShapRow(BaseModel):
    feature: str
    label: str
    mean_abs: float
    mean_signed: float


class ExplanationOut(BaseModel):
    run_id: uuid.UUID
    model: str
    kind: Literal["tft", "lightgbm"]
    static: list[Driver]
    past: list[Driver]
    future: list[Driver]
    attention: Attention | None
    shap: list[ShapRow] | None
    note: str


class NarrativeOut(BaseModel):
    text: str
    source: Literal["template", "llm"]
    facts: dict[str, Any]


async def _pick_run(session: Session, org_id: uuid.UUID, model: str | None) -> ModelRun:
    """The run to explain: the requested model, else the champion if explainable, else TFT, else LightGBM."""
    runs = await _latest_runs(session, org_id)
    if model:
        hit = next((r for r in runs if r.model_name == model), None)
        if hit is None:
            raise ApiError(404, "not_found", f"no run for model {model}")
        if hit.model_name not in EXPLAINABLE:
            raise ApiError(
                422, "not_explainable", f"{model} has no feature attributions; pick one of {', '.join(EXPLAINABLE)}"
            )
        return hit
    champ = next((r for r in runs if r.is_champion and r.model_name in EXPLAINABLE), None)
    if champ:
        return champ
    for name in EXPLAINABLE:
        hit = next((r for r in runs if r.model_name == name), None)
        if hit:
            return hit
    raise ApiError(404, "no_explainable_run", "Run a backtest that includes TFT or LightGBM first")


def _drivers(names: list[str], w: list[float]) -> list[Driver]:
    return [Driver(name=n, weight=round(v, 4)) for n, v in group_weights(names, w)]


async def _tft_explanation(
    session: Session, p: Principal, run: ModelRun, location_id: uuid.UUID, product_id: uuid.UUID
) -> ExplanationOut:
    row = (
        await session.execute(
            select(ForecastExplanation).where(
                ForecastExplanation.org_id == p.org_id,
                ForecastExplanation.run_id == run.id,
                ForecastExplanation.location_id == location_id,
                ForecastExplanation.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or not (row.static_w and row.encoder_w and row.decoder_w and row.attention):
        raise ApiError(404, "no_explanation", "No TFT explanation stored for this series (TFT skips ragged series)")
    sw, ew, dw, at = (cast("dict[str, Any]", x) for x in (row.static_w, row.encoder_w, row.decoder_w, row.attention))
    data_end = date.fromisoformat(str((run.config or {}).get("data_end")))
    n = int(at["lookback_days"])
    days = [data_end - timedelta(days=n - 1 - i) for i in range(n)]
    return ExplanationOut(
        run_id=run.id,
        model=run.model_name,
        kind="tft",
        static=[Driver(name=a, weight=round(float(b), 4)) for a, b in zip(sw["names"], sw["w"], strict=True)],
        past=_drivers(list(ew["names"]), list(ew["w"])),
        future=_drivers(list(dw["names"]), list(dw["w"])),
        attention=Attention(dates=days, weights=[float(v) for v in at["w"]]),
        shap=None,
        note="Weights come from the TFT's variable-selection networks (softmax, sum to 1) for this series; "
        "attention is the forecast days' attention over each past day, averaged over the horizon and heads.",
    )


async def _lgbm_shap_rows(run: ModelRun, location_id: uuid.UUID, product_id: uuid.UUID) -> pd.DataFrame:
    d = _artifacts(run)
    if d is None or not (d / "meta.json").exists():
        raise ApiError(404, "no_artifacts", "The fitted LightGBM model for this run is not available")
    model, cov, _fest, meta = await asyncio.to_thread(_load, d)
    uid = next(
        (u for u, m in meta.items() if m["location_id"] == str(location_id) and m["product_id"] == str(product_id)),
        None,
    )
    if uid is None:
        raise ApiError(404, "not_found", "series not in this run")
    h = int(cov[cov["unique_id"] == uid]["ds"].nunique())
    try:
        return await asyncio.to_thread(lgbm_shap, model, cov, [uid], h)
    except ValueError as e:  # e.g. "ids weren't seen during training": the series is in the data but not in this model
        raise ApiError(404, "no_explanation", "This series was not part of the fitted model") from e


async def explanation(
    session: Session, p: Principal, location_id: uuid.UUID, product_id: uuid.UUID, model: str | None = None
) -> ExplanationOut:
    """Variable importance for one series from the chosen explainable run."""
    if not p.can_access_location(location_id):
        raise ApiError(403, "forbidden", "No access to this location")
    run = await _pick_run(session, p.org_id, model)
    if run.model_name == "tft":
        return await _tft_explanation(session, p, run, location_id, product_id)
    agg = (await _lgbm_shap_rows(run, location_id, product_id)).sort_values("mean_abs", ascending=False)
    rows = [
        ShapRow(
            feature=r.feature,
            label=FEATURE_LABELS.get(r.feature, r.feature),
            mean_abs=round(float(r.mean_abs), 5),
            mean_signed=round(float(r.mean_signed), 5),
        )
        for r in agg.itertuples()
    ]
    return ExplanationOut(
        run_id=run.id,
        model=run.model_name,
        kind="lightgbm",
        static=[],
        past=[],
        future=[],
        attention=None,
        shap=rows,
        note="SHAP values from LightGBM's built-in TreeSHAP on the median (Tweedie) model, over the next 28 days. "
        "Values are in the model's log-demand scale: positive pushes the forecast up, negative pushes it down.",
    )


async def narrative(
    session: Session, p: Principal, location_id: uuid.UUID, product_id: uuid.UUID, days: int = 14, polish: Any = None
) -> NarrativeOut:
    """'Why this forecast?': deterministic sentence from stored numbers; optional LLM polish, guarded for numbers."""
    if not p.can_access_location(location_id):
        raise ApiError(403, "forbidden", "No access to this location")
    runs = await _latest_runs(session, p.org_id)
    fc_run = next((r for r in runs if r.is_champion), runs[0] if runs else None)
    if fc_run is None:
        raise ApiError(404, "no_forecasts", "Run a backtest first")
    data_end = date.fromisoformat(str((fc_run.config or {}).get("data_end")))
    fut = (
        (
            await session.execute(
                select(Forecast.p50)
                .where(
                    Forecast.org_id == p.org_id,
                    Forecast.run_id == fc_run.id,
                    Forecast.location_id == location_id,
                    Forecast.product_id == product_id,
                    Forecast.target_date > data_end,
                    Forecast.target_date <= data_end + timedelta(days=days),
                )
                .order_by(Forecast.target_date)
            )
        )
        .scalars()
        .all()
    )
    if len(fut) < days:
        raise ApiError(404, "no_forecasts", "No forecast covering that horizon for this series")
    recent = (
        (
            await session.execute(
                select(SalesDaily.units).where(
                    SalesDaily.org_id == p.org_id,
                    SalesDaily.location_id == location_id,
                    SalesDaily.product_id == product_id,
                    SalesDaily.date > data_end - timedelta(days=days),
                    SalesDaily.date <= data_end,
                )
            )
        )
        .scalars()
        .all()
    )
    loc = await session.get(Location, location_id)
    prod = await session.get(Product, product_id)
    if loc is None or prod is None or loc.org_id != p.org_id or prod.org_id != p.org_id:
        raise ApiError(404, "not_found", "series not found")
    try:
        ex = await explanation(session, p, location_id, product_id)
        if ex.kind == "tft":
            drivers = [(d.name, d.weight) for d in ex.future]
        else:
            drivers = shap_drivers(
                pd.DataFrame([{"feature": r.feature, "mean_abs": r.mean_abs} for r in (ex.shap or [])])
            )
        source_model, source_run = ex.model, str(ex.run_id)
    except ApiError:
        drivers, source_model, source_run = [], None, None  # still explain the numbers, just without drivers
    fest = (
        await session.execute(
            select(CalendarEvent.date, CalendarEvent.name)
            .where(
                CalendarEvent.org_id == p.org_id,
                CalendarEvent.type == "festival",
                CalendarEvent.date > data_end,
                CalendarEvent.date <= data_end + timedelta(days=days),
            )
            .order_by(CalendarEvent.date)
        )
    ).first()
    facts = build_facts(
        prod.name,
        loc.name,
        days,
        float(sum(recent)),
        float(sum(fut[:days])),
        drivers,
        None if fest is None else (fest[1], (fest[0] - data_end).days),
        source_model,
        source_run,
    )
    candidate = await polish(facts) if polish else None
    text, source = grounded_or_template(candidate, facts)
    return NarrativeOut(text=text, source="llm" if source == "llm" else "template", facts=json.loads(json.dumps(facts)))
