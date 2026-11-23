"""Live events: one Redis pub/sub channel per organisation (+ short capped lists so a page can catch up).

Publishers: POS checkout (sale / alert), simulator (sim), jobs (job), drift check (alert).
Consumers: GET /stream/events (SSE) and GET /live/state. A failed publish must never undo business work.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()
FEED_MAX = 60
ALERTS_MAX = 50
ALERT_TTL_SECONDS = 7 * 24 * 3600


def channel(org_id: uuid.UUID) -> str:
    """Pub/sub channel of an organisation (the POS service already published here in Phase 2)."""
    return f"kalpa:events:{org_id}"


def feed_key(org_id: uuid.UUID) -> str:
    """Capped list of the latest sale events."""
    return f"kalpa:feed:{org_id}"


def alerts_key(org_id: uuid.UUID) -> str:
    """Capped list of the latest alerts (reorder point crossed, drift)."""
    return f"kalpa:alerts:{org_id}"


def sim_key(org_id: uuid.UUID) -> str:
    """Simulator state (JSON, short TTL refreshed every tick: a dead simulator disappears by itself)."""
    return f"kalpa:sim:{org_id}:state"


def sim_stop_key(org_id: uuid.UUID) -> str:
    """Flag the simulator loop polls to stop."""
    return f"kalpa:sim:{org_id}:stop"


async def publish_event(redis: Redis, org_id: uuid.UUID, event: dict[str, Any]) -> None:
    """Publish an event; sale events go to the feed list, alerts to the alerts list. Never raises."""
    event = {"id": uuid.uuid4().hex, "ts": datetime.now(UTC).isoformat(), **event}
    payload = json.dumps(event, default=str)
    try:
        await redis.publish(channel(org_id), payload)
        kind = event.get("type")
        if kind == "sale":
            await redis.lpush(feed_key(org_id), payload)  # type: ignore[misc]
            await redis.ltrim(feed_key(org_id), 0, FEED_MAX - 1)  # type: ignore[misc]
        elif kind == "alert":
            await redis.lpush(alerts_key(org_id), payload)  # type: ignore[misc]
            await redis.ltrim(alerts_key(org_id), 0, ALERTS_MAX - 1)  # type: ignore[misc]
            await redis.expire(alerts_key(org_id), ALERT_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001 - a Redis hiccup must not fail a committed sale or a running job
        log.warning("event_publish_failed", error=type(e).__name__)


async def recent(redis: Redis, key: str, n: int) -> list[dict[str, Any]]:
    """Newest-first list of stored events (bad entries are skipped)."""
    out: list[dict[str, Any]] = []
    for raw in await redis.lrange(key, 0, n - 1):  # type: ignore[misc]
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out
