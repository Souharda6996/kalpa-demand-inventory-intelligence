"""POS routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from redis.asyncio import Redis

from kalpa_api.core.deps import Principal, Session, get_redis, require
from kalpa_api.modules.pos import service
from kalpa_api.modules.pos.schemas import CheckoutIn, Receipt

router = APIRouter(prefix="/pos", tags=["pos"])


@router.post("/checkout", response_model=Receipt, operation_id="posCheckout")
async def pos_checkout(
    body: CheckoutIn,
    request: Request,
    session: Session,
    redis: Annotated[Redis, Depends(get_redis)],
    p: Annotated[Principal, Depends(require("pos_checkout"))],
    idempotency_key: Annotated[str, Header(min_length=8, max_length=100)],
) -> Receipt:
    """Sell items atomically. Requires an Idempotency-Key header; replays return the original receipt."""
    return await service.checkout(
        session, redis, p, body, idempotency_key, request.client.host if request.client else None
    )
