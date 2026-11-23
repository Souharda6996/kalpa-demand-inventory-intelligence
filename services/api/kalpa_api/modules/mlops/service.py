"""MLOps: model registry view, drift monitor (PSI + live WAPE), retrain, scheduled maintenance."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.config import get_settings
from kalpa_api.core.errors import ApiError
from kalpa_api.core.events import publish_event
from kalpa_api.db.models import AuditLog, Forecast, Job, ModelRun, Org, SalesDaily, User
from kalpa_api.modules.forecasts.queue import submit_backtest
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_api.modules.simulator import service as sim_service
from kalpa_ml.drift import PSI_ALERT, WAPE_ALERT_RATIO, psi, psi_status, wape_drift

BASELINE_DAYS = 90  # training-window sample the PSI bins come from
MIN_NEW_DAYS = 3  # complete days of new sales needed before drift is judged
MIN_PSI_ROWS = 500  # PSI over 10 bins is biased upward on small samples (~ 9 / n): 50 rows per bin keeps it < 0.02
HORIZON = 28
RETRAIN_WEEKDAY: Literal["mon"] = "mon"  # ARQ cron (worker) and the text shown in the UI
RETRAIN_HOUR, RETRAIN_MINUTE = 2, 0
DRIFT_HOUR, DRIFT_MINUTE = 3, 0
SCHEDULE_TEXT = (
    f"Retrain: Mondays {RETRAIN_HOUR:02d}:{RETRAIN_MINUTE:02d}; drift check: daily {DRIFT_HOUR:02d}:{DRIFT_MINUTE:02d} "
    "(worker server time)"
)
RANK = {"unknown": 0, "ok": 1, "warn": 2, "alert": 3}


class RunRow(BaseModel):
    id: uuid.UUID
    version: str
    status: str
    created_at: datetime
    is_champion: bool
    metrics: dict[str, float | None]
    mlflow_run_id: str | None
    mlflow_url: str | None


class ModelVersions(BaseModel):
    model_name: str
    versions: list[RunRow]


class ChampionEvent(BaseModel):
    at: datetime
    by: str | None
    model_name: str
    version: str
    run_id: uuid.UUID | None


class RegistryOut(BaseModel):
    models: list[ModelVersions]
    champion_history: list[ChampionEvent]
    mlflow_ui: str
    schedule: str


class PsiRow(BaseModel):
    feature: str
    psi: float
    status: Literal["ok", "warn", "alert"]
    baseline_n: int
    recent_n: int


class WapeBlock(BaseModel):
    backtest: float | None
    live: float | None
    ratio: float | None
    status: Literal["ok", "warn", "alert", "unknown"]
    days: int


class LastRetrain(BaseModel):
    job_id: uuid.UUID
    status: str
    reason: str | None
    created_at: datetime


class DriftOut(BaseModel):
    run_id: uuid.UUID | None
    model: str | None
    data_end: date | None
    complete_through: date | None
    recent_days: int
    enough_data: bool
    message: str | None
    psi: list[PsiRow]
    wape: WapeBlock
    status: Literal["ok", "warn", "alert", "unknown"]
    alerts: list[str]
    last_retrain: LastRetrain | None
    schedule: str
    thresholds: dict[str, float]


class DriftCheckOut(BaseModel):
    drift: DriftOut
    alerted: bool
    retrain_job_id: uuid.UUID | None


def _mlflow_url(run: ModelRun) -> str | None:
    exp = (run.config or {}).get("mlflow_experiment_id")
    if not (run.mlflow_run_id and exp):
        return None
    return f"{get_settings().mlflow_tracking_uri.rstrip('/')}/#/experiments/{exp}/runs/{run.mlflow_run_id}"


async def registry(session: AsyncSession, org_id: uuid.UUID) -> RegistryOut:
    """Every run per model (newest first), champion history from the audit log, MLflow links."""
    runs = (
        (await session.execute(select(ModelRun).where(ModelRun.org_id == org_id).order_by(ModelRun.created_at.desc())))
        .scalars()
        .all()
    )
    by: dict[str, list[RunRow]] = {}
    for r in runs:
        keep = ("wape", "mase", "bias", "pinball", "coverage80", "coverage80_cal", "cost", "fill_rate")
        m = {k: v for k, v in (r.metrics or {}).items() if k in keep and isinstance(v, int | float | type(None))}
        by.setdefault(r.model_name, []).append(
            RunRow(
                id=r.id,
                version=r.version,
                status=r.status,
                created_at=r.created_at,
                is_champion=r.is_champion,
                metrics={k: (None if v is None else float(v)) for k, v in m.items()},
                mlflow_run_id=r.mlflow_run_id,
                mlflow_url=_mlflow_url(r),
            )
        )
    hist = (
        await session.execute(
            select(AuditLog.created_at, User.name, AuditLog.after, AuditLog.entity_id)
            .join(User, User.id == AuditLog.user_id, isouter=True)
            .where(AuditLog.org_id == org_id, AuditLog.action == "model.promote")
            .order_by(AuditLog.created_at.desc())
            .limit(20)
        )
    ).all()
    events = [
        ChampionEvent(
            at=at,
            by=by_name,
            model_name=str((after or {}).get("model_name", "?")),
            version=str((after or {}).get("version", "?")),
            run_id=uuid.UUID(eid) if eid else None,
        )
        for at, by_name, after, eid in hist
    ]
    return RegistryOut(
        models=[ModelVersions(model_name=k, versions=v) for k, v in sorted(by.items())],
        champion_history=events,
        mlflow_ui=get_settings().mlflow_tracking_uri,
        schedule=SCHEDULE_TEXT,
    )


async def _complete_through(session: AsyncSession, redis: Redis, org_id: uuid.UUID) -> date | None:
    """Last business day whose sales are complete: the day before the simulated one while it runs, else the latest day
    in the data, never today's (still open) real day."""
    sim = await sim_service.read_state(redis, org_id, True)
    if sim.running and sim.sim_date:
        return sim.sim_date - timedelta(days=1)
    last = (await session.execute(select(func.max(SalesDaily.date)).where(SalesDaily.org_id == org_id))).scalar_one()
    org = await session.get(Org, org_id)
    today = datetime.now(ZoneInfo(org.timezone if org else "UTC")).date()
    return None if last is None else min(last, today - timedelta(days=1))


def _empty(run: ModelRun | None, message: str, through: date | None = None, days: int = 0) -> DriftOut:
    return DriftOut(
        run_id=run.id if run else None,
        model=run.model_name if run else None,
        data_end=date.fromisoformat(str((run.config or {}).get("data_end"))) if run else None,
        complete_through=through,
        recent_days=days,
        enough_data=False,
        message=message,
        psi=[],
        wape=WapeBlock(backtest=None, live=None, ratio=None, status="unknown", days=days),
        status="unknown",
        alerts=[],
        last_retrain=None,
        schedule=SCHEDULE_TEXT,
        thresholds={"psi_alert": PSI_ALERT, "wape_degradation": WAPE_ALERT_RATIO - 1.0},
    )


async def compute_drift(session: AsyncSession, redis: Redis, org_id: uuid.UUID) -> DriftOut:
    """PSI of scaled units / price (training window vs new days) and live WAPE of the champion vs its backtest WAPE."""
    runs = await _latest_runs(session, org_id)
    run = next((r for r in runs if r.is_champion), runs[0] if runs else None)
    last_job = (
        await session.execute(
            select(Job).where(Job.org_id == org_id, Job.type == "retrain").order_by(Job.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    lr = (
        None
        if last_job is None
        else LastRetrain(
            job_id=last_job.id,
            status=last_job.status,
            reason=(last_job.payload or {}).get("reason"),
            created_at=last_job.created_at,
        )
    )
    if run is None:
        out = _empty(None, "No model has been trained yet: run a backtest first.")
        return out.model_copy(update={"last_retrain": lr})
    data_end = date.fromisoformat(str((run.config or {}).get("data_end")))
    through = await _complete_through(session, redis, org_id)
    recent_to = min(through, data_end + timedelta(days=HORIZON)) if through else None
    if recent_to is None or recent_to <= data_end:
        return _empty(run, "No complete day of sales after the training data yet.", through).model_copy(
            update={"last_retrain": lr}
        )
    days = (recent_to - data_end).days
    if days < MIN_NEW_DAYS:
        msg = f"Need at least {MIN_NEW_DAYS} complete days of new sales to judge drift (have {days})."
        return _empty(run, msg, through, days).model_copy(update={"last_retrain": lr})

    base_from = data_end - timedelta(days=BASELINE_DAYS - 1)
    rows = (
        await session.execute(
            select(
                SalesDaily.location_id, SalesDaily.product_id, SalesDaily.date, SalesDaily.units, SalesDaily.avg_price
            ).where(SalesDaily.org_id == org_id, SalesDaily.date >= base_from, SalesDaily.date <= recent_to)
        )
    ).all()
    df = pd.DataFrame(rows, columns=["loc", "prod", "date", "units", "price"])
    df["units"], df["price"] = df["units"].astype(float), pd.to_numeric(df["price"], errors="coerce")
    base, new = df[df["date"] <= data_end], df[df["date"] > data_end]
    out_psi: list[PsiRow] = []
    keys = ["loc", "prod"]
    mean_units = base.groupby(keys)["units"].mean()
    mean_units = mean_units[mean_units > 0]
    for feature, col in (("units (scaled per series)", "units"), ("price (scaled per series)", "price")):
        ref = base.dropna(subset=[col]).groupby(keys)[col].mean() if col == "price" else mean_units
        ref = ref[ref > 0]
        a = base.dropna(subset=[col]).join(ref.rename("ref"), on=keys, how="inner")
        b = new.dropna(subset=[col]).join(ref.rename("ref"), on=keys, how="inner")
        if len(a) < MIN_PSI_ROWS or len(b) < MIN_PSI_ROWS:
            continue
        v = psi((a[col] / a["ref"]).to_numpy(), (b[col] / b["ref"]).to_numpy())
        out_psi.append(
            PsiRow(feature=feature, psi=round(v, 4), status=psi_status(v), baseline_n=len(a), recent_n=len(b))
        )

    # Two small indexed range reads merged in pandas. A SQL join of forecasts and sales_daily relies on the planner's
    # statistics: on a freshly bulk-loaded multi-tenant table it picked a plan that ran for minutes.
    fdf = pd.DataFrame(
        (
            await session.execute(
                select(Forecast.location_id, Forecast.product_id, Forecast.target_date, Forecast.p50).where(
                    Forecast.org_id == org_id,
                    Forecast.run_id == run.id,
                    Forecast.target_date > data_end,
                    Forecast.target_date <= recent_to,
                )
            )
        ).all(),
        columns=["loc", "prod", "date", "p50"],
    )
    fc = fdf.merge(new[["loc", "prod", "date", "units"]], on=["loc", "prod", "date"], how="inner")
    fc = fc[["p50", "units"]].to_numpy(dtype=float)
    bt = (run.metrics or {}).get("wape")
    if len(fc) and isinstance(bt, int | float) and bt > 0:
        wd = wape_drift(fc[:, 1], fc[:, 0], float(bt))
        wape_block = WapeBlock(
            backtest=wd.backtest,
            live=None if wd.live != wd.live else wd.live,
            ratio=None if wd.ratio != wd.ratio else wd.ratio,
            status=wd.status,
            days=days,
        )
    else:
        wape_block = WapeBlock(
            backtest=None if not isinstance(bt, int | float) else float(bt),
            live=None,
            ratio=None,
            status="unknown",
            days=days,
        )

    note = (
        None
        if out_psi
        else f"PSI needs at least {MIN_PSI_ROWS} new rows per feature (have {len(new)}); only live WAPE is judged."
    )
    statuses = [p.status for p in out_psi] + [wape_block.status]
    status = max(statuses, key=lambda s: RANK[s]) if statuses else "unknown"
    alerts = [f"PSI {p.psi:.2f} on {p.feature} is above {PSI_ALERT}" for p in out_psi if p.status == "alert"]
    if wape_block.status == "alert" and wape_block.live is not None and wape_block.backtest is not None:
        alerts.append(
            f"Live WAPE {wape_block.live:.1%} is {(wape_block.ratio or 1) - 1:.0%} worse than the backtest WAPE "
            f"{wape_block.backtest:.1%}"
        )
    return DriftOut(
        run_id=run.id,
        model=run.model_name,
        data_end=data_end,
        complete_through=through,
        recent_days=days,
        enough_data=True,
        message=note,
        psi=out_psi,
        wape=wape_block,
        status=status,
        alerts=alerts,
        last_retrain=lr,
        schedule=SCHEDULE_TEXT,
        thresholds={"psi_alert": PSI_ALERT, "wape_degradation": WAPE_ALERT_RATIO - 1.0},
    )


async def _active_job(session: AsyncSession, org_id: uuid.UUID, since: timedelta | None = None) -> Job | None:
    """A queued/running backtest or retrain (optionally also one created within `since`)."""
    q = select(Job).where(
        Job.org_id == org_id, Job.type.in_(["retrain", "backtest"]), Job.status.in_(["queued", "running"])
    )
    return (await session.execute(q.limit(1))).scalar_one_or_none()


async def start_retrain(session: AsyncSession, redis: Redis, org_id: uuid.UUID, reason: str) -> Job:
    """Retrain every model of the latest runs with the same fold count, as a 'retrain' job."""
    runs = await _latest_runs(session, org_id)
    if not runs:
        raise ApiError(404, "no_forecasts", "Nothing to retrain yet: run a backtest first")
    if await _active_job(session, org_id):
        raise ApiError(409, "already_running", "A backtest or retrain is already queued or running")
    folds = max(int((r.config or {}).get("folds", 3)) for r in runs)  # type: ignore[call-overload]
    return await submit_backtest(session, redis, org_id, sorted({r.model_name for r in runs}), folds, "retrain", reason)


async def check_drift(
    session: AsyncSession, redis: Redis, org_id: uuid.UUID, auto_retrain: bool = True
) -> DriftCheckOut:
    """Compute drift; on an alert (once per model version): publish it and start a retrain."""
    d = await compute_drift(session, redis, org_id)
    alerted, job_id = False, None
    if d.status == "alert" and d.run_id:
        alerted = bool(await redis.set(f"kalpa:drift:alerted:{org_id}:{d.run_id}", "1", nx=True, ex=24 * 3600))
        if alerted:
            retrain_note = ""
            if auto_retrain:
                try:
                    job = await start_retrain(session, redis, org_id, "drift")
                    job_id, retrain_note = job.id, " A retrain has been started."
                except ApiError as e:
                    retrain_note = f" Retrain not started: {e.message}."
            await publish_event(
                redis,
                org_id,
                {
                    "type": "alert",
                    "kind": "drift",
                    "title": "Model drift detected",
                    "detail": "; ".join(d.alerts) + retrain_note,
                },
            )
    return DriftCheckOut(drift=d, alerted=alerted, retrain_job_id=job_id)


async def scheduled_retrain_all(session: AsyncSession, redis: Redis) -> list[uuid.UUID]:
    """Weekly cron body: retrain every organisation that has trained models (skips ones already busy)."""
    org_ids = (
        (await session.execute(select(ModelRun.org_id).where(ModelRun.status == "succeeded").distinct()))
        .scalars()
        .all()
    )
    started: list[uuid.UUID] = []
    for org_id in org_ids:
        try:
            job = await start_retrain(session, redis, org_id, "scheduled")
            started.append(job.id)
        except ApiError:
            continue
    return started


async def drift_check_all(session: AsyncSession, redis: Redis) -> dict[str, Any]:
    """Daily cron body: drift check (with auto-retrain on alert) for every organisation with a model."""
    org_ids = (
        (await session.execute(select(ModelRun.org_id).where(ModelRun.status == "succeeded").distinct()))
        .scalars()
        .all()
    )
    alerts = 0
    for org_id in org_ids:
        r = await check_drift(session, redis, org_id)
        alerts += int(r.alerted)
    return {"orgs": len(org_ids), "alerts": alerts}
