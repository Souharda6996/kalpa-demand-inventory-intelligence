"""Create + enqueue backtest/retrain jobs (used by the API, the drift check and the weekly cron)."""

import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.cache import bump
from kalpa_api.core.config import get_settings
from kalpa_api.core.events import publish_event
from kalpa_api.db.models import Job
from kalpa_api.modules.forecasts import service


async def submit_backtest(
    session: AsyncSession,
    redis: Redis,
    org_id: uuid.UUID,
    models: list[str],
    folds: int,
    job_type: str = "backtest",
    reason: str | None = None,
) -> Job:
    """Create the Job row and run it inline (dev/tests) or via ARQ (job id = ARQ job id, so it can be aborted)."""
    job = Job(
        org_id=org_id,
        type=job_type,
        status="queued",
        payload={"models": models, "folds": folds, **({"reason": reason} if reason else {})},
    )
    session.add(job)
    await session.commit()
    await publish_event(
        redis, org_id, {"type": "job", "id": str(job.id), "job_type": job_type, "status": "queued", "progress": 0.0}
    )
    if get_settings().inline_jobs:

        async def notify(ev: dict[str, Any]) -> None:
            await publish_event(redis, org_id, ev)

        await service.execute_backtest(session, org_id, job.id, models, folds, notify=notify)
        await bump(redis, org_id)
    else:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            await pool.enqueue_job("run_backtest_job", str(org_id), str(job.id), models, folds, _job_id=str(job.id))
        finally:
            await pool.aclose()
    await session.refresh(job)
    return job


async def abort_queued_or_running(job_id: uuid.UUID) -> None:
    """Ask ARQ to stop the job (cancels the running task, or removes it from the queue). Best effort."""
    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.jobs import Job as ArqJob

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        await ArqJob(str(job_id), pool).abort(timeout=2)
    except Exception:  # noqa: BLE001, S110 - the DB status is already 'cancelled'; execution checkpoints enforce it
        pass
    finally:
        await pool.aclose()
