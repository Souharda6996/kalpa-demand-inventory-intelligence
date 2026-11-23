"""ARQ job functions. Heavy lifting lives in kalpa_api services (shared with inline mode and the tests)."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from kalpa_api.core.cache import bump
from kalpa_api.core.events import publish_event
from kalpa_api.db.models import Job
from kalpa_api.db.session import make_engine, make_sessionmaker
from kalpa_api.modules.forecasts.service import execute_backtest
from kalpa_api.modules.mlops import service as mlops
from kalpa_api.modules.simulator.service import run_simulator


async def run_backtest_job(
    ctx: dict[str, Any], org_id: str, job_id: str, models: list[str], folds: int
) -> dict[str, Any]:
    """Run a backtest / retrain for an org (progress, log and status are written to the jobs table by the service)."""
    engine = make_engine()
    org = uuid.UUID(org_id)

    async def notify(event: dict[str, Any]) -> None:
        await publish_event(ctx["redis"], org, event)

    try:
        async with make_sessionmaker(engine)() as session:
            result = await execute_backtest(session, org, uuid.UUID(job_id), models, folds, notify=notify)
            await bump(ctx["redis"], org)  # forecasts, runs and drift changed: cached answers are stale
            return result
    finally:
        await engine.dispose()


async def run_simulator_job(
    ctx: dict[str, Any], org_id: str, user_id: str, job_id: str, multiplier: float, day_seconds: int, minutes: int
) -> dict[str, Any]:
    """Run the live store simulator; the Jobs row tracks its status (cancel = abort this task)."""
    engine = make_engine()
    maker = make_sessionmaker(engine)
    org, jid = uuid.UUID(org_id), uuid.UUID(job_id)
    try:
        async with maker() as s:
            job = await s.get(Job, jid)
            if job is None or job.status == "cancelled":
                return {"cancelled": True}
            job.status, job.started_at = "running", datetime.now(UTC)
            await s.commit()
        await publish_event(
            ctx["redis"],
            org,
            {"type": "job", "id": job_id, "job_type": "simulator", "status": "running", "progress": 0.0},
        )
        try:
            summary = await run_simulator(
                maker, ctx["redis"], org, uuid.UUID(user_id), job_id, multiplier, day_seconds, minutes
            )
            status, error = "succeeded", None
        except asyncio.CancelledError:
            status, error, summary = "cancelled", "Cancelled", {}
            raise
        except Exception as e:  # noqa: BLE001
            status, error, summary = "failed", str(e)[:500], {}
        finally:
            async with maker() as s:
                job = await s.get(Job, jid)
                if job is not None and job.status != "cancelled":
                    job.status, job.error, job.result, job.finished_at, job.progress = (
                        status,
                        error,
                        summary,
                        datetime.now(UTC),
                        1.0,
                    )
                    await s.commit()
            await publish_event(
                ctx["redis"],
                org,
                {"type": "job", "id": job_id, "job_type": "simulator", "status": status, "progress": 1.0},
            )
        return summary
    finally:
        await engine.dispose()


async def scheduled_retrain(ctx: dict[str, Any]) -> dict[str, Any]:
    """Weekly cron: retrain every organisation that has trained models."""
    engine = make_engine()
    try:
        async with make_sessionmaker(engine)() as session:
            started = await mlops.scheduled_retrain_all(session, ctx["redis"])
        return {"started": [str(j) for j in started]}
    finally:
        await engine.dispose()


async def drift_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily cron: PSI + live-WAPE drift check per organisation (alert once per model version, then retrain)."""
    engine = make_engine()
    try:
        async with make_sessionmaker(engine)() as session:
            return await mlops.drift_check_all(session, ctx["redis"])
    finally:
        await engine.dispose()
