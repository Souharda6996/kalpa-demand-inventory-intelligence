"""Explain endpoints (read-only): variable importance + 'why this forecast?' narrative."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from redis.asyncio import Redis

from kalpa_api.core.cache import cached_json, scope_key
from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.modules.copilot.llm import polish_narrative
from kalpa_api.modules.explain import service
from kalpa_api.modules.explain.service import ExplanationOut, NarrativeOut

router = APIRouter(tags=["explain"])
Viewer = Annotated[Principal, Depends(require("view"))]


@router.get(
    "/forecasts/{location_id}/{product_id}/explanation", response_model=ExplanationOut, operation_id="getExplanation"
)
async def get_explanation(
    location_id: uuid.UUID,
    product_id: uuid.UUID,
    session: Session,
    p: Viewer,
    redis: Annotated[Redis, Depends(get_redis)],
    model: str | None = None,
) -> Response:
    """TFT variable-selection weights + attention, or LightGBM SHAP, for one series (cached 5 min)."""
    parts = f"{location_id}|{product_id}|{model}|{scope_key(p.location_ids)}"
    return await cached_json(
        redis,
        p.org_id,
        "explanation",
        parts,
        300,
        lambda: service.explanation(session, p, location_id, product_id, model),
    )


@router.get("/explain/{location_id}/{product_id}/narrative", response_model=NarrativeOut, operation_id="getNarrative")
async def get_narrative(
    location_id: uuid.UUID,
    product_id: uuid.UUID,
    session: Session,
    p: Viewer,
    redis: Annotated[Redis, Depends(get_redis)],
    days: Annotated[int, Query(ge=7, le=28)] = 14,
) -> Response:
    """Plain-language reason for the forecast; every number comes from stored data (cached 5 min)."""
    parts = f"{location_id}|{product_id}|{days}|{scope_key(p.location_ids)}"
    return await cached_json(
        redis,
        p.org_id,
        "narrative",
        parts,
        300,
        lambda: service.narrative(session, p, location_id, product_id, days, polish_narrative),
    )
