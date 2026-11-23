"""Backtest execution + forecast persistence (shared by the API inline mode and the ARQ worker)."""

import asyncio
import json
import math
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import polars as pl
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.config import get_settings
from kalpa_api.db.models import (
    BacktestResult,
    CalendarEvent,
    Forecast,
    ForecastExplanation,
    Job,
    Location,
    ModelRun,
    Org,
    Product,
    SalesDaily,
)
from kalpa_api.modules.policy.inputs import assumptions, cost_config, org_cost, series_params, supplier_terms
from kalpa_ml.backtest import ALL_MODELS, forecast_future, run_backtest, summarise
from kalpa_ml.data.synth import load_festivals
from kalpa_ml.features import add_covariates
from kalpa_ml.frames import to_long, zero_fill

CHUNK = 20_000
LOG_MAX_CHARS = 20_000
Notifier = Callable[[dict[str, Any]], Awaitable[None]]  # publishes a live "job" event (Redis -> SSE)


class JobCancelled(Exception):
    """Raised at a checkpoint when the job was cancelled from the Jobs page."""


def _note(job: Job, msg: str) -> None:
    """Append a timestamped line to the job log (kept to the last LOG_MAX_CHARS characters)."""
    line = f"{datetime.now(UTC).strftime('%H:%M:%S')} {msg}"
    log = f"{job.log}\n{line}" if job.log else line
    job.log = log[-LOG_MAX_CHARS:]


async def _step(session: AsyncSession, job: Job, progress: float, msg: str, notify: Notifier | None) -> None:
    """Record progress + a log line, tell live viewers, then stop if the job was cancelled meanwhile."""
    job.progress = progress
    _note(job, msg)
    await session.commit()
    if notify:
        await notify(
            {
                "type": "job",
                "id": str(job.id),
                "job_type": job.type,
                "status": job.status,
                "progress": round(progress, 3),
                "line": msg,
            }
        )
    await session.refresh(job)
    if job.status == "cancelled":
        raise JobCancelled


METRIC_KEYS = ("wape", "mase", "bias", "pinball", "coverage80")


def _num(v: object) -> float | None:
    """NaN -> None so JSON columns / DB floats stay valid."""
    if v is None:
        return None
    f = float(v)  # type: ignore[arg-type]
    return None if math.isnan(f) else f


async def load_series(
    session: AsyncSession, org_id: uuid.UUID
) -> tuple[pd.DataFrame, dict[str, tuple[uuid.UUID, uuid.UUID]], pd.Series]:
    """All sales of an org as a Nixtla long frame (+covariates) and unique_id -> (location_id, product_id)."""
    q = (
        select(
            SalesDaily.date,
            SalesDaily.units,
            SalesDaily.avg_price,
            Location.name,
            Product.sku,
            Product.category,
            SalesDaily.location_id,
            SalesDaily.product_id,
        )
        .join(Location, Location.id == SalesDaily.location_id)
        .join(Product, Product.id == SalesDaily.product_id)
        .where(SalesDaily.org_id == org_id)
    )
    rows = (await session.execute(q)).all()
    if not rows:
        raise ValueError("no sales data to forecast")
    raw = pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "units": [r[1] for r in rows],
            "price": [r[2] for r in rows],
            "location": [r[3] for r in rows],
            "sku": [r[4] for r in rows],
            "category": [r[5] for r in rows],
        },
        schema_overrides={"price": pl.Float64},
    )
    ids = {f"{r[3]}|{r[4]}": (r[6], r[7]) for r in rows}
    if raw["price"].null_count() > 0:
        raw = raw.drop("price")
    fest = (
        (
            await session.execute(
                select(CalendarEvent.date).where(CalendarEvent.org_id == org_id, CalendarEvent.type == "festival")
            )
        )
        .scalars()
        .all()
    )
    fest_dates = pd.Series(pd.to_datetime(list(fest))) if fest else load_festivals()["date"].to_pandas()
    org = await session.get(Org, org_id)
    today = datetime.now(ZoneInfo(org.timezone if org else "UTC")).date()
    long = zero_fill(to_long(raw), last_day=pd.Timestamp(today - timedelta(days=1)))  # today is still open
    return add_covariates(long, fest_dates), ids, fest_dates


async def execute_backtest(
    session: AsyncSession,
    org_id: uuid.UUID,
    job_id: uuid.UUID,
    models: list[str] | None = None,
    folds: int = 3,
    h: int = 28,
    notify: Notifier | None = None,
) -> dict[str, Any]:
    """Run the rolling-origin backtest for an org, persist runs/results/forecasts, update the Job row."""
    models = models or ALL_MODELS
    job = await session.get(Job, job_id)
    if job is None or job.org_id != org_id:
        raise ValueError("job not found for this organisation")
    if job.status == "cancelled":  # cancelled while still queued
        return {"cancelled": True}
    job.status, job.started_at = "running", datetime.now(UTC)
    try:
        await _step(session, job, 0.05, f"started: models {', '.join(models)}, {folds} fold(s), horizon {h} d", notify)
        df, ids, fest_dates = await load_series(session, org_id)
        await _step(session, job, 0.15, f"loaded {df['unique_id'].nunique()} series, {len(df):,} rows", notify)
        cost = await org_cost(session, org_id)
        terms = await supplier_terms(session, org_id)
        cats = df.groupby("unique_id")["category"].first().to_dict() if "category" in df.columns else {}
        params = series_params(ids, terms, cats, int(cost["review_days"])) or None  # no supplier terms -> no costing
        await _step(session, job, 0.2, "backtesting (rolling origin)...", notify)
        res = await asyncio.to_thread(run_backtest, df, models, h, folds, params, cost_config(cost))
        await _step(session, job, 0.7, "backtest finished; forecasting the next 28 days", notify)
        save_dir = (
            str(Path(get_settings().model_dir) / str(job_id) / "lightgbm_tweedie")
            if "lightgbm_tweedie" in models or "tft" in models  # tft needs the dir for its explanations
            else None
        )
        future, as_of = await asyncio.to_thread(forecast_future, df, fest_dates, models, h, res.deltas, save_dir)
        if save_dir:  # per-series context for what-if scenarios (prices, supplier terms, ids)
            last = df.sort_values("ds").groupby("unique_id").tail(1).set_index("unique_id")
            meta = {
                uid: {
                    "location_id": str(loc),
                    "product_id": str(prod),
                    "category": cats.get(uid),
                    "price": float(last["price"].get(uid)) if "price" in last.columns else None,
                    "unit_cost": terms[prod].unit_cost if prod in terms else None,
                    "lead_days": terms[prod].lead_days if prod in terms else None,
                }
                for uid, (loc, prod) in ids.items()
            }
            Path(save_dir).parent.joinpath("meta.json").write_text(json.dumps(meta), encoding="utf-8")
        await _step(session, job, 0.85, "future forecasts ready; saving results", notify)
        summary = summarise(res)
        version = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_ids: dict[str, str] = {}
        last = res.forecasts[res.forecasts["fold"] == folds - 1]
        for name in models:
            run = ModelRun(
                org_id=org_id,
                model_name=name,
                version=version,
                status="succeeded",
                scope="all",
                config={
                    "horizon": h,
                    "folds": folds,
                    "series": int(df["unique_id"].nunique()),
                    "data_end": str(as_of.date()),
                    "future_days": h,
                    "cost_assumptions": assumptions(cost),
                    "costed_series": len(params or {}),
                    "safety": res.safety.get(name),
                    **(
                        {"artifacts": {"dir": str(Path(save_dir).parent)}}
                        if save_dir and name == "lightgbm_tweedie"
                        else {}
                    ),
                },
                metrics={k: _num(v) for k, v in summary[name].items()},
            )
            session.add(run)
            await session.flush()
            run_ids[name] = str(run.id)
            await session.execute(
                insert(BacktestResult),
                [
                    {
                        "id": uuid.uuid4(),
                        "org_id": org_id,
                        "run_id": run.id,
                        "fold": f["fold"],
                        "level": "all",
                        "key": "all",
                        **{k: _num(v) for k, v in f["models"][name].items() if k in METRIC_KEYS},
                    }
                    for f in res.folds
                ],
            )
            recs = []
            pieces = (last[last["model"] == name], future[future["model"] == name])  # backtest fold + true future
            for r in pd.concat(pieces).itertuples():
                p50 = float(r.p50)
                recs.append(
                    {
                        "id": uuid.uuid4(),
                        "org_id": org_id,
                        "run_id": run.id,
                        "location_id": ids[r.unique_id][0],
                        "product_id": ids[r.unique_id][1],
                        "target_date": r.ds.date(),
                        "p10": p50 if math.isnan(r.p10) else float(r.p10),
                        "p50": p50,
                        "p90": p50 if math.isnan(r.p90) else float(r.p90),
                        "p10_cal": _num(getattr(r, "p10_cal", None)),
                        "p90_cal": _num(getattr(r, "p90_cal", None)),
                        "level": "sku",
                    }
                )
            for i in range(0, len(recs), CHUNK):
                await session.execute(insert(Forecast), recs[i : i + CHUNK])
        if "tft" in run_ids and save_dir:
            await _store_explanations(session, org_id, uuid.UUID(run_ids["tft"]), Path(save_dir).parent, ids)
        from kalpa_api.modules.policy.service import refresh_reorder_points  # lazy: policy imports this package

        await session.commit()
        if session.get_bind().dialect.name == "postgresql":  # fresh bulk insert: give the planner real row counts
            await session.execute(text("ANALYZE forecasts"))
        n_rop = await refresh_reorder_points(session, org_id)
        await _step(session, job, 0.95, f"reorder points updated for {n_rop} stocked series", notify)
        await _log_mlflow(session, summary, folds, df, run_ids, version)
        await session.refresh(job)
        if job.status == "cancelled":
            raise JobCancelled
        job.status, job.progress, job.finished_at, job.result = (
            "succeeded",
            1.0,
            datetime.now(UTC),
            {"run_ids": run_ids},
        )
        _note(job, "done")
        await session.commit()
        if notify:
            await notify(
                {
                    "type": "job",
                    "id": str(job.id),
                    "job_type": job.type,
                    "status": "succeeded",
                    "progress": 1.0,
                    "line": "done",
                }
            )
        return {"run_ids": run_ids}
    except JobCancelled:
        await session.rollback()
        return {"cancelled": True}
    except (Exception, asyncio.CancelledError) as e:  # CancelledError = worker timeout / shutdown / abort
        await session.rollback()
        failed = await session.get(Job, job_id)
        if failed is not None and failed.status != "cancelled":  # a cancelled job keeps its status
            msg = str(e) or "Job was cancelled (worker timeout or shutdown)"
            failed.status, failed.error, failed.finished_at = "failed", msg[:1000], datetime.now(UTC)
            _note(failed, f"failed: {msg[:200]}")
            await session.commit()
            if notify:
                await notify(
                    {
                        "type": "job",
                        "id": str(failed.id),
                        "job_type": failed.type,
                        "status": "failed",
                        "progress": failed.progress,
                        "line": msg[:200],
                    }
                )
        raise


async def _store_explanations(
    session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, d: Path, ids: dict[str, tuple[uuid.UUID, uuid.UUID]]
) -> None:
    """Persist the TFT's variable-selection weights and attention (written by forecast_future) per series."""
    f = d / "tft_explanations.json"
    if not f.exists():
        return
    summary: dict[str, dict[str, Any]] = json.loads(f.read_text(encoding="utf-8"))
    rows = [
        {
            "id": uuid.uuid4(),
            "org_id": org_id,
            "run_id": run_id,
            "location_id": ids[uid][0],
            "product_id": ids[uid][1],
            **e,
        }
        for uid, e in summary.items()
        if uid in ids
    ]
    for i in range(0, len(rows), CHUNK):
        await session.execute(insert(ForecastExplanation), rows[i : i + CHUNK])


async def _log_mlflow(
    session: AsyncSession,
    summary: dict[str, dict[str, float]],
    folds: int,
    df: pd.DataFrame,
    run_ids: dict[str, str],
    version: str,
) -> None:
    """Best-effort: one MLflow run per model; its ids are stored on the model run. Never fails a backtest."""
    try:
        from kalpa_ml.registry import log_run_ref

        n_series = int(df["unique_id"].nunique())
        for name, rid in run_ids.items():
            params = {"model": name, "version": version, "folds": folds, "series": n_series, "db_run_id": rid}
            ml_run, ml_exp = await asyncio.to_thread(
                log_run_ref, "backtests", f"{name}-{version}", params, summary[name], [], get_settings().mlflow_db_uri
            )
            run = await session.get(ModelRun, uuid.UUID(rid))
            if run is not None:
                run.mlflow_run_id = ml_run
                run.config = {**(run.config or {}), "mlflow_experiment_id": ml_exp}
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
