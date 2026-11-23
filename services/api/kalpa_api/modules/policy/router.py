"""Replenishment recommendations: forecast + stock + supplier terms -> what to order, with reasons."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis

from kalpa_api.core.cache import cached_json, scope_key
from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.modules.policy.service import Recommendation, RecommendationSet, compute_recommendations

__all__ = ["Recommendation", "RecommendationSet", "recommendations", "router"]

router = APIRouter(prefix="/policy", tags=["policy"])
Viewer = Annotated[Principal, Depends(require("view"))]


@router.get("/recommendations", response_model=RecommendationSet, operation_id="getRecommendations")
async def recommendations(
    session: Session,
    p: Viewer,
    redis: Annotated[Redis, Depends(get_redis)],
    location_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    only_orders: bool = True,
) -> Response:
    """Order recommendations from the champion run (else the best-WAPE run) for every stocked, supplied product."""

    async def build() -> RecommendationSet:
        return await compute_recommendations(session, p.org_id, p.location_ids, location_id, supplier_id, only_orders)

    parts = f"{location_id}|{supplier_id}|{only_orders}|{scope_key(p.location_ids)}"
    return await cached_json(redis, p.org_id, "recommendations", parts, 10, build, group="stock")
