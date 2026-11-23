"""Live stream (SSE) + catch-up state."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.modules.stream import service
from kalpa_api.modules.stream.service import LiveState

router = APIRouter(tags=["stream"])
Viewer = Annotated[Principal, Depends(require("view"))]


@router.get("/stream/events", operation_id="streamEvents", response_class=StreamingResponse)
async def stream_events(request: Request, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> StreamingResponse:
    """Server-Sent Events for this organisation: sale, alert, job and sim events (cookie auth)."""
    return StreamingResponse(
        service.event_stream(redis, p, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/live/state", response_model=LiveState, operation_id="getLiveState")
async def get_live_state(session: Session, p: Viewer, redis: Annotated[Redis, Depends(get_redis)]) -> LiveState:
    """What a page needs to catch up after loading or reconnecting: totals, feed, alerts, stock watch, simulator."""
    return await service.live_state(session, redis, p)
