"""Server-Sent Events relay (Redis pub/sub -> browser) and the catch-up state a page loads on (re)connect."""

import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.deps import Principal
from kalpa_api.core.events import alerts_key, channel, feed_key, recent
from kalpa_api.db.models import InventoryPosition, Location, Org, Product, SalesDaily
from kalpa_api.modules.simulator import service as sim_service
from kalpa_api.modules.simulator.schemas import SimState

KEEPALIVE_SECONDS = 15
RETRY_MS = 2000  # tells EventSource how long to wait before reconnecting


def sse(event: str, data: str) -> str:
    """One SSE frame (data must be a single line: compact JSON)."""
    return f"event: {event}\ndata: {data}\n\n"


def visible(event: dict[str, Any], p: Principal) -> bool:
    """Store-scoped users only receive events of their own stores; org-wide events (jobs, simulator) go to everyone."""
    loc = event.get("location_id")
    return loc is None or p.location_ids is None or str(loc) in p.location_ids


async def event_stream(
    redis: Redis, p: Principal, is_disconnected: Callable[[], Awaitable[bool]], poll_seconds: float = 1.0
) -> AsyncIterator[str]:
    """Yield SSE frames for this organisation until the client goes away."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel(p.org_id))
    try:
        yield f"retry: {RETRY_MS}\n\n"
        yield sse(
            "hello", json.dumps({"org_id": str(p.org_id), "server_time": datetime.now().astimezone().isoformat()})
        )
        last = time.monotonic()
        while not await is_disconnected():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_seconds)
            if msg and msg.get("type") == "message":
                raw = msg["data"].decode() if isinstance(msg["data"], bytes) else str(msg["data"])
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if visible(ev, p):
                    yield sse(str(ev.get("type", "message")), raw)
                    last = time.monotonic()
            elif time.monotonic() - last > KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last = time.monotonic()
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]


class WatchRow(BaseModel):
    location_id: uuid.UUID
    location: str
    product_id: uuid.UUID
    product: str
    sku: str
    on_hand: int
    reorder_point: int


class LiveState(BaseModel):
    date: str  # the business day the totals are for (simulated day while the simulator runs)
    revenue: float
    units: int
    feed: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    watch: list[WatchRow]
    sim: SimState


async def live_state(session: AsyncSession, redis: Redis, p: Principal) -> LiveState:
    """Totals for the current business day + recent feed/alerts + the lowest-cover stocked series."""
    org = await session.get(Org, p.org_id)
    available = bool(org and org.is_demo)
    sim = await sim_service.read_state(redis, p.org_id, available)
    day = (
        sim.sim_date.isoformat()
        if sim.sim_date
        else datetime.now(ZoneInfo(org.timezone if org else "UTC")).date().isoformat()
    )
    scope = (
        [] if p.location_ids is None else [InventoryPosition.location_id.in_([uuid.UUID(x) for x in p.location_ids])]
    )
    sales_scope = [] if p.location_ids is None else [SalesDaily.location_id.in_([uuid.UUID(x) for x in p.location_ids])]
    rev, units = (
        await session.execute(
            select(
                func.coalesce(func.sum(SalesDaily.revenue), 0.0), func.coalesce(func.sum(SalesDaily.units), 0)
            ).where(SalesDaily.org_id == p.org_id, SalesDaily.date == datetime.fromisoformat(day).date(), *sales_scope)
        )
    ).one()
    watch = (
        await session.execute(
            select(
                InventoryPosition.location_id,
                Location.name,
                InventoryPosition.product_id,
                Product.name,
                Product.sku,
                InventoryPosition.on_hand,
                InventoryPosition.reorder_point,
            )
            .join(Location, Location.id == InventoryPosition.location_id)
            .join(Product, Product.id == InventoryPosition.product_id)
            .where(InventoryPosition.org_id == p.org_id, InventoryPosition.reorder_point.is_not(None), *scope)
            .order_by((InventoryPosition.on_hand - InventoryPosition.reorder_point).asc(), Product.sku)
            .limit(8)
        )
    ).all()
    return LiveState(
        date=day,
        revenue=round(float(rev), 2),
        units=int(units),
        feed=[e for e in await recent(redis, feed_key(p.org_id), 30) if visible(e, p)],
        alerts=[e for e in await recent(redis, alerts_key(p.org_id), 20) if visible(e, p)],
        watch=[
            WatchRow(location_id=a, location=b, product_id=c, product=d, sku=e, on_hand=f, reorder_point=g)
            for a, b, c, d, e, f, g in watch
        ],
        sim=sim,
    )
