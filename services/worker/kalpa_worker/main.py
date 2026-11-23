"""ARQ worker entrypoint. Phase 0: heartbeat only; real jobs arrive in Phase 4+."""

import os
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from kalpa_api.modules.mlops.service import DRIFT_HOUR, DRIFT_MINUTE, RETRAIN_HOUR, RETRAIN_MINUTE, RETRAIN_WEEKDAY
from kalpa_worker.jobs import drift_check, run_backtest_job, run_simulator_job, scheduled_retrain

WORKER_HEARTBEAT_KEY = "kalpa:worker:heartbeat"
HEARTBEAT_TTL_SECONDS = 30


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Refresh the liveness key the API health check reads."""
    await ctx["redis"].set(WORKER_HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL_SECONDS)


async def startup(ctx: dict[str, Any]) -> None:
    """Mark the worker alive immediately on boot."""
    await heartbeat(ctx)


class WorkerSettings:
    """ARQ settings."""

    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    functions: list[Any] = [run_backtest_job, run_simulator_job]
    allow_abort_jobs = True  # the Jobs page can cancel a running job
    job_timeout = 3600  # ARQ's default (300 s) kills real backtests; 1 h is the hard ceiling
    max_tries = 1  # a failed/timed-out backtest is reported, never silently re-run
    keep_result = 3600
    cron_jobs = [
        cron(heartbeat, second={0, 10, 20, 30, 40, 50}, run_at_startup=True),
        cron(
            scheduled_retrain, weekday=RETRAIN_WEEKDAY, hour=RETRAIN_HOUR, minute=RETRAIN_MINUTE, timeout=3600
        ),  # weekly retrain
        cron(
            drift_check, hour=DRIFT_HOUR, minute=DRIFT_MINUTE, timeout=900
        ),  # daily drift check (+ auto retrain on alert)
    ]
    on_startup = startup
