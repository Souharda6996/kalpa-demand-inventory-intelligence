"""Models, backtests, arena, forecasts, jobs."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from redis.asyncio import Redis
from sqlalchemy import func, select

from kalpa_api.core.audit import write_audit
from kalpa_api.core.cache import cached_json
from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.core.errors import ApiError
from kalpa_api.core.events import publish_event
from kalpa_api.db.models import Forecast, Job, ModelRun, SalesDaily
from kalpa_api.modules.forecasts import queue
from kalpa_api.modules.forecasts.schemas import (
    BacktestRequest,
    ForecastPoint,
    HistoryPoint,
    JobDetail,
    JobOut,
    ModelSeries,
    RunOut,
    SeriesOut,
)
from kalpa_ml.backtest import ALL_MODELS, OPTIONAL_MODELS

router = APIRouter(tags=["forecasts"])
Viewer = Annotated[Principal, Depends(require("view"))]
Runner = Annotated[Principal, Depends(require("upload_data"))]  # "run training & backtests" = owner/planner


@router.post("/models/backtests", response_model=JobOut, status_code=202, operation_id="startBacktest")
async def start_backtest(
    body: BacktestRequest, session: Session, p: Runner, redis: Annotated[Redis, Depends(get_redis)]
) -> Job:
    """Queue a rolling-origin backtest (28-day folds). Runs in the worker; inline when INLINE_JOBS=true."""
    models = body.models or ALL_MODELS
    allowed = [*ALL_MODELS, *OPTIONAL_MODELS]
    bad = sorted(set(models) - set(allowed))
    if bad:
        raise ApiError(422, "unknown_model", f"Unknown model(s): {', '.join(bad)}", allowed)
    return await queue.submit_backtest(session, redis, p.org_id, models, body.folds)


@router.get("/jobs/{job_id}", response_model=JobDetail, operation_id="getJob")
async def get_job(job_id: uuid.UUID, session: Session, p: Viewer) -> Job:
    """One job (progress / status / error / log tail)."""
    job = (await session.execute(select(Job).where(Job.id == job_id, Job.org_id == p.org_id))).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "not_found", "job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobDetail, operation_id="cancelJob")
async def cancel_job(
    job_id: uuid.UUID, session: Session, p: Runner, redis: Annotated[Redis, Depends(get_redis)]
) -> Job:
    """Cancel a queued or running job. The status changes at once; a running backtest stops at its next checkpoint."""
    job = (await session.execute(select(Job).where(Job.id == job_id, Job.org_id == p.org_id))).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "not_found", "job not found")
    if job.status not in ("queued", "running"):
        raise ApiError(409, "job_finished", f"Job already {job.status}")
    job.status, job.finished_at, job.error = "cancelled", datetime.now(UTC), f"Cancelled by {p.user.name}"
    job.log = f"{job.log or ''}\n{datetime.now(UTC).strftime('%H:%M:%S')} cancelled by {p.user.name}".strip()
    await session.commit()
    await publish_event(
        redis,
        p.org_id,
        {"type": "job", "id": str(job.id), "job_type": job.type, "status": "cancelled", "progress": job.progress},
    )
    if not get_settings().inline_jobs:
        await queue.abort_queued_or_running(job.id)
    await session.refresh(job)
    return job


@router.get("/jobs", response_model=list[JobOut], operation_id="listJobs")
async def list_jobs(session: Session, p: Viewer, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[Job]:
    """Recent jobs, newest first."""
    q = select(Job).where(Job.org_id == p.org_id).order_by(Job.created_at.desc()).limit(limit)
    return list((await session.execute(q)).scalars().all())


@router.get("/models/runs", response_model=list[RunOut], operation_id="listRuns")
async def list_runs(session: Session, p: Viewer, model_name: str | None = None) -> list[ModelRun]:
    """Model runs for this org, newest first."""
    q = select(ModelRun).where(ModelRun.org_id == p.org_id)
    if model_name:
        q = q.where(ModelRun.model_name == model_name)
    return list((await session.execute(q.order_by(ModelRun.created_at.desc()).limit(200))).scalars().all())


def _wape(r: ModelRun) -> float:
    """Sort key: WAPE, with missing/NaN last."""
    v = (r.metrics or {}).get("wape")
    return float(v) if isinstance(v, int | float) else 9e9


async def _latest_runs(session: Session, org_id: uuid.UUID) -> list[ModelRun]:
    """Most recent successful run per model, best WAPE first."""
    rows = (
        (
            await session.execute(
                select(ModelRun)
                .where(ModelRun.org_id == org_id, ModelRun.status == "succeeded")
                .order_by(ModelRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    seen: dict[str, ModelRun] = {}
    for r in rows:
        seen.setdefault(r.model_name, r)
    return sorted(seen.values(), key=lambda r: _wape(r))


@router.get("/models/arena", response_model=list[RunOut], operation_id="getArena")
async def arena(session: Session, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> Response:
    """Leaderboard: latest run per model, best WAPE first (cached 30 s, refreshed by any write)."""

    async def build() -> list[RunOut]:
        return [RunOut.model_validate(r) for r in await _latest_runs(session, p.org_id)]

    return await cached_json(redis, p.org_id, "arena", "", 30, build)


@router.get("/forecasts/series", response_model=SeriesOut, operation_id="getSeriesForecast")
async def series_forecast(
    session: Session,
    p: Viewer,
    location_id: uuid.UUID,
    product_id: uuid.UUID,
    days: Annotated[int, Query(ge=28, le=730)] = 120,
) -> SeriesOut:
    """Recent history + each model's latest forecast band for one (location, product)."""
    if not p.can_access_location(location_id):
        raise ApiError(403, "forbidden", "No access to this location")
    hist = (
        await session.execute(
            select(SalesDaily.date, SalesDaily.units)
            .where(
                SalesDaily.org_id == p.org_id,
                SalesDaily.location_id == location_id,
                SalesDaily.product_id == product_id,
            )
            .order_by(SalesDaily.date.desc())
            .limit(days)
        )
    ).all()
    models: list[ModelSeries] = []
    for run in await _latest_runs(session, p.org_id):
        pts = (
            await session.execute(
                select(Forecast.target_date, Forecast.p10, Forecast.p50, Forecast.p90)
                .where(
                    Forecast.org_id == p.org_id,
                    Forecast.run_id == run.id,
                    Forecast.location_id == location_id,
                    Forecast.product_id == product_id,
                )
                .order_by(Forecast.target_date)
            )
        ).all()
        if pts:
            models.append(
                ModelSeries(
                    model_name=run.model_name,
                    run_id=run.id,
                    metrics=run.metrics,
                    points=[ForecastPoint(date=d, p10=a, p50=b, p90=c) for d, a, b, c in pts],
                )
            )
    return SeriesOut(history=[HistoryPoint(date=d, units=u) for d, u in reversed(hist)], models=models)


@router.get("/forecasts/summary", response_model=dict[str, int], operation_id="getForecastSummary")
async def forecast_summary(session: Session, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> Response:
    """Counts used by dashboards: runs, forecast rows (cached 30 s, refreshed by any write)."""

    async def build() -> dict[str, int]:
        runs = (
            await session.execute(select(func.count()).select_from(ModelRun).where(ModelRun.org_id == p.org_id))
        ).scalar_one()
        rows = (
            await session.execute(select(func.count()).select_from(Forecast).where(Forecast.org_id == p.org_id))
        ).scalar_one()
        return {"runs": runs, "forecast_rows": rows}

    return await cached_json(redis, p.org_id, "forecast-summary", "", 30, build)


Promoter = Annotated[Principal, Depends(require("promote_model"))]


@router.post("/models/runs/{run_id}/promote", response_model=RunOut, operation_id="promoteRun")
async def promote_run(run_id: uuid.UUID, session: Session, p: Promoter) -> ModelRun:
    """Make this run the champion for its scope; every other run of this org in that scope is demoted."""
    run = (
        await session.execute(select(ModelRun).where(ModelRun.id == run_id, ModelRun.org_id == p.org_id))
    ).scalar_one_or_none()
    if run is None or run.status != "succeeded":
        raise ApiError(404, "not_found", "run not found")
    for other in (
        await session.execute(select(ModelRun).where(ModelRun.org_id == p.org_id, ModelRun.scope == run.scope))
    ).scalars():
        other.is_champion = other.id == run.id
    write_audit(
        session,
        org_id=p.org_id,
        user_id=p.user.id,
        action="model.promote",
        entity="model_run",
        entity_id=run.id,
        after={"model_name": run.model_name, "version": run.version, "wape": (run.metrics or {}).get("wape")},
    )
    await session.commit()
    await session.refresh(run)
    return run
