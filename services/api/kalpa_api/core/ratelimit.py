"""Fixed-window rate limits in Redis for expensive or abuse-prone endpoints (login has its own failure counter).

Keys are per client IP (anonymous endpoints) or per user (authenticated ones). The IP is the TCP peer address, never a
client-supplied X-Forwarded-For header (behind a proxy, run uvicorn with --proxy-headers and --forwarded-allow-ips).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal, current_principal, get_redis
from kalpa_api.core.errors import ApiError


async def hit(redis: Redis, key: str, limit: int, window_seconds: int) -> None:
    """Count one call; raise 429 once `limit` calls were made in the current window. Redis down = allow (fail open)."""
    try:
        n = int(await redis.incr(key))
        if n == 1:
            await redis.expire(key, window_seconds)
    except Exception:  # noqa: BLE001 - a Redis outage must not take the API down with it
        return
    if n > limit:
        raise ApiError(429, "rate_limited", f"Too many requests: limit is {limit} per {window_seconds // 60} min")


def per_ip(name: str, limit: Callable[[], int], window_seconds: int) -> Callable[..., Awaitable[None]]:
    """Dependency: limit anonymous calls per client IP."""

    async def _dep(request: Request, redis: Annotated[Redis, Depends(get_redis)]) -> None:
        ip = request.client.host if request.client else "unknown"
        await hit(redis, f"kalpa:rl:{name}:{ip}", limit(), window_seconds)

    return _dep


def per_user(name: str, limit: Callable[[], int], window_seconds: int) -> Callable[..., Awaitable[None]]:
    """Dependency: limit authenticated calls per user (and organisation)."""

    async def _dep(
        p: Annotated[Principal, Depends(current_principal)], redis: Annotated[Redis, Depends(get_redis)]
    ) -> None:
        await hit(redis, f"kalpa:rl:{name}:{p.org_id}:{p.user.id}", limit(), window_seconds)

    return _dep


def signup_limit() -> int:
    """Signups per hour per IP."""
    return get_settings().signup_per_hour


def chat_limit() -> int:
    """Copilot questions per minute per user."""
    return get_settings().copilot_per_minute


def upload_limit() -> int:
    """File uploads per hour per user."""
    return get_settings().uploads_per_hour


def demo_limit() -> int:
    """Demo-data loads per hour per user."""
    return get_settings().demo_loads_per_hour
