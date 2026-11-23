"""Dependency probes used by the health endpoint."""

import redis.asyncio as aioredis
from sqlalchemy import text

from kalpa_api.core.config import Settings
from kalpa_api.db.session import get_engine
from kalpa_api.modules.system.schemas import HealthResponse, ServiceStatus

WORKER_HEARTBEAT_KEY = "kalpa:worker:heartbeat"


async def check_db(url: str) -> ServiceStatus:
    """Return 'up' if a trivial query succeeds on the shared pool."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "up"
    except Exception:
        return "down"


async def check_redis(url: str) -> tuple[ServiceStatus, ServiceStatus]:
    """Return (redis status, worker status) - worker is up if its heartbeat key exists."""
    client = aioredis.from_url(url, socket_connect_timeout=2)  # type: ignore[no-untyped-call]
    try:
        await client.ping()
        beat = await client.exists(WORKER_HEARTBEAT_KEY)
        return "up", "up" if beat else "down"
    except Exception:
        return "down", "down"
    finally:
        await client.aclose()


async def build_health(settings: Settings) -> HealthResponse:
    """Probe all dependencies and assemble the response."""
    db = await check_db(settings.database_url)
    redis_status, worker = await check_redis(settings.redis_url)
    ok = db == "up" and redis_status == "up" and worker == "up"
    return HealthResponse(
        status="ok" if ok else "degraded",
        version=settings.app_version,
        git_sha=settings.git_sha,
        db=db,
        redis=redis_status,
        worker=worker,
    )
