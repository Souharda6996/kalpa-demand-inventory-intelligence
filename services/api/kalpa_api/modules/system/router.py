"""System routes: health + backend metrics."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis

from kalpa_api.core.cache import cached_json
from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.modules.system import metrics
from kalpa_api.modules.system.schemas import HealthResponse
from kalpa_api.modules.system.service import build_health

router = APIRouter(tags=["system"])
Viewer = Annotated[Principal, Depends(require("view"))]


@router.get("/health", response_model=HealthResponse, operation_id="getHealth")
async def health() -> HealthResponse:
    """Report API version, git SHA and DB / Redis / worker status."""
    return await build_health(get_settings())


@router.get("/system/metrics", response_model=metrics.SystemMetrics, operation_id="getSystemMetrics")
async def system_metrics(session: Session, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> Response:
    """Latency percentiles, throughput, error rate, queue depth, DB size, cache hit rate and versions (live values)."""
    return await cached_json(
        redis, p.org_id, "system-metrics", "", 5, lambda: metrics.collect(session, redis, p.org_id)
    )
