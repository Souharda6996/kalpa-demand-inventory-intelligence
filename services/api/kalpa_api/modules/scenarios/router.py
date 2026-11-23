"""Scenario Studio: change price, promo depth or festivals; re-infer with the fitted LightGBM; compare to baseline."""

import asyncio
import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select

from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.core.errors import ApiError
from kalpa_api.db.models import InventoryPosition, ModelRun, Scenario
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_ml.scenario import NOT_MODELLED, apply_changes, load_model, predict

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
Editor = Annotated[Principal, Depends(require("edit_scenarios"))]
Viewer = Annotated[Principal, Depends(require("view"))]
MAX_SERIES = 200
CACHE_SECONDS = 600
HORIZON = 28
_MODEL_CACHE: dict[str, tuple[float, Any, pd.DataFrame, pd.Series, dict[str, dict[str, Any]]]] = {}


class Scope(BaseModel):
    location_id: uuid.UUID
    category: str | None = None
    product_ids: list[uuid.UUID] | None = None


class Changes(BaseModel):
    price_change_pct: float = Field(default=0, ge=-60, le=100)
    promo_depth_pct: float = Field(default=0, ge=0, le=60)
    add_festival: date | None = None
    weather_shift_c: float | None = Field(default=None, ge=-15, le=15)  # accepted, reported as NOT modelled


class RunIn(BaseModel):
    scope: Scope
    changes: Changes


class DayPoint(BaseModel):
    date: date
    baseline: float
    scenario: float


class Fan(BaseModel):
    dates: list[date]
    baseline: dict[str, list[float]]  # p10 / p50 / p90
    scenario: dict[str, list[float]]


class Result(BaseModel):
    run_id: uuid.UUID
    series: int
    days: list[DayPoint]  # P50 summed over the scope
    fan: Fan | None  # only for a single series (quantiles of a sum are not the sum of quantiles)
    delta_units: float
    delta_revenue: float | None
    delta_margin: float | None
    at_risk_baseline: int
    at_risk_scenario: int
    notes: list[str]


class SaveIn(RunIn):
    name: str = Field(min_length=1, max_length=200)


class SavedOut(BaseModel):
    id: uuid.UUID
    name: str
    scope: dict[str, Any] | None
    changes: dict[str, Any] | None
    result: dict[str, Any] | None


def _artifacts(run: ModelRun) -> Path | None:
    d = ((run.config or {}).get("artifacts") or {}).get("dir")  # type: ignore[attr-defined]
    return Path(d) if d else None


def _load(dir_: Path) -> tuple[Any, pd.DataFrame, pd.Series, dict[str, dict[str, Any]]]:
    """Fitted model + future covariates + festival calendar + per-series meta, cached until the files change."""
    key = str(dir_)
    mtime = (dir_ / "meta.json").stat().st_mtime
    hit = _MODEL_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1:]
    model = load_model(dir_ / "lightgbm_tweedie")
    cov = pd.read_pickle(dir_ / "future_cov.pkl")  # noqa: S301 - written by this application's own worker
    fest = pd.read_pickle(dir_ / "fest.pkl")  # noqa: S301
    meta = json.loads((dir_ / "meta.json").read_text(encoding="utf-8"))
    _MODEL_CACHE.clear()  # keep at most one model in memory
    _MODEL_CACHE[key] = (mtime, model, cov, fest, meta)
    return model, cov, fest, meta


def _compute(dir_: Path, body: RunIn, on_hand: dict[str, float]) -> Result:
    model, cov, fest, meta = _load(dir_)
    sc = body.scope
    ids = [
        u
        for u, m in meta.items()
        if m["location_id"] == str(sc.location_id)
        and (sc.category is None or m["category"] == sc.category)
        and (not sc.product_ids or m["product_id"] in {str(x) for x in sc.product_ids})
    ]
    if not ids:
        raise ApiError(422, "empty_scope", "No forecastable series match this scope")
    if len(ids) > MAX_SERIES:
        raise ApiError(422, "scope_too_large", f"Scope has {len(ids)} series; narrow it to at most {MAX_SERIES}")
    ch = body.changes
    base_x = cov[cov["unique_id"].isin(ids)]
    scn_x = apply_changes(
        base_x,
        fest,
        price_change_pct=ch.price_change_pct,
        promo_depth_pct=ch.promo_depth_pct,
        add_festival=ch.add_festival,
    )
    base, scn = predict(model, base_x, ids, HORIZON), predict(model, scn_x, ids, HORIZON)
    mult = (1 + ch.price_change_pct / 100) * (1 - ch.promo_depth_pct / 100)
    b = base.groupby("unique_id")["p50"].sum()
    s = scn.groupby("unique_id")["p50"].sum()
    price = pd.Series({u: meta[u]["price"] for u in ids}, dtype=float)
    cost = pd.Series({u: meta[u]["unit_cost"] for u in ids}, dtype=float)
    have_price, have_cost = price.notna().all(), cost.notna().all()
    rev = float((s * price * mult).sum() - (b * price).sum()) if have_price else None
    margin = float((s * (price * mult - cost)).sum() - (b * (price - cost)).sum()) if have_price and have_cost else None

    def at_risk(frame: pd.DataFrame) -> int:
        n = 0
        for u, g in frame.sort_values("ds").groupby("unique_id"):
            lead = meta[str(u)]["lead_days"]
            if lead and on_hand.get(str(u), 0.0) < float(g["p50"].head(int(lead)).sum()):
                n += 1
        return n

    day = pd.DataFrame(
        {"baseline": base.groupby("ds")["p50"].sum(), "scenario": scn.groupby("ds")["p50"].sum()}
    ).reset_index()
    fan = None
    if len(ids) == 1:
        fan = Fan(
            dates=[d.date() for d in base["ds"]],
            baseline={k: base[k].round(3).tolist() for k in ("p10", "p50", "p90")},
            scenario={k: scn[k].round(3).tolist() for k in ("p10", "p50", "p90")},
        )
    notes = list(NOT_MODELLED) if ch.weather_shift_c else []
    notes.append("Assumes unchanged price and no other promotion in the baseline.")
    return Result(
        run_id=uuid.uuid4(),
        series=len(ids),  # replaced by the real run id in _run()
        days=[
            DayPoint(date=r.ds.date(), baseline=round(float(r.baseline), 2), scenario=round(float(r.scenario), 2))
            for r in day.itertuples()
        ],
        fan=fan,
        delta_units=round(float(s.sum() - b.sum()), 2),
        delta_revenue=None if rev is None else round(rev, 2),
        delta_margin=None if margin is None else round(margin, 2),
        at_risk_baseline=at_risk(base),
        at_risk_scenario=at_risk(scn),
        notes=notes,
    )


async def _run(session: Session, redis: Redis, p: Principal, body: RunIn) -> Result:
    if not p.can_access_location(body.scope.location_id):
        raise ApiError(403, "forbidden", "No access to this location")
    runs = [r for r in await _latest_runs(session, p.org_id) if r.model_name == "lightgbm_tweedie" and _artifacts(r)]
    if not runs:
        raise ApiError(
            404,
            "no_scenario_model",
            "Run a backtest that includes LightGBM first: scenarios re-infer with its saved model",
        )
    run, dir_ = runs[0], _artifacts(runs[0])
    assert dir_ is not None
    key = (
        "kalpa:scn:"
        + hashlib.sha256(
            json.dumps([str(p.org_id), str(run.id), body.model_dump(mode="json")], sort_keys=True).encode()
        ).hexdigest()
    )
    cached = await redis.get(key)
    if cached:
        return Result.model_validate_json(cached)
    inv = (
        (
            await session.execute(
                select(InventoryPosition).where(
                    InventoryPosition.org_id == p.org_id, InventoryPosition.location_id == body.scope.location_id
                )
            )
        )
        .scalars()
        .all()
    )
    meta = json.loads((dir_ / "meta.json").read_text(encoding="utf-8"))
    by_prod = {m["product_id"]: u for u, m in meta.items() if m["location_id"] == str(body.scope.location_id)}
    on_hand = {by_prod[str(i.product_id)]: float(i.on_hand + i.on_order) for i in inv if str(i.product_id) in by_prod}
    result = await asyncio.to_thread(_compute, dir_, body, on_hand)
    result.run_id = run.id
    await redis.set(key, result.model_dump_json(), ex=CACHE_SECONDS)
    return result


@router.post("/run", response_model=Result, operation_id="runScenario")
async def run_scenario(body: RunIn, session: Session, p: Editor, redis: Annotated[Redis, Depends(get_redis)]) -> Result:
    """Baseline vs scenario for the scope; cached for 10 minutes per identical request."""
    return await _run(session, redis, p, body)


@router.post("", response_model=SavedOut, status_code=201, operation_id="saveScenario")
async def save_scenario(
    body: SaveIn, session: Session, p: Editor, redis: Annotated[Redis, Depends(get_redis)]
) -> Scenario:
    """Run and save a scenario (name, scope, changes and the result at the time)."""
    result = await _run(session, redis, p, RunIn(scope=body.scope, changes=body.changes))
    sc = Scenario(
        org_id=p.org_id,
        name=body.name,
        scope=body.scope.model_dump(mode="json"),
        changes=body.changes.model_dump(mode="json"),
        result=json.loads(result.model_dump_json()),
        created_by=p.user.id,
    )
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


@router.get("", response_model=list[SavedOut], operation_id="listScenarios")
async def list_scenarios(session: Session, p: Viewer) -> list[Scenario]:
    """Saved scenarios of this organisation, newest first."""
    q = select(Scenario).where(Scenario.org_id == p.org_id).order_by(Scenario.created_at.desc()).limit(100)
    return list((await session.execute(q)).scalars().all())


@router.delete("/{scenario_id}", status_code=204, operation_id="deleteScenario")
async def delete_scenario(scenario_id: uuid.UUID, session: Session, p: Editor) -> None:
    """Delete a saved scenario."""
    sc = (
        await session.execute(select(Scenario).where(Scenario.id == scenario_id, Scenario.org_id == p.org_id))
    ).scalar_one_or_none()
    if sc is None:
        raise ApiError(404, "not_found", "scenario not found")
    await session.delete(sc)
    await session.commit()
