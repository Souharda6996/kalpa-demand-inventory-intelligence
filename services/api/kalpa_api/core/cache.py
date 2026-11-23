"""Short-lived Redis cache for heavy READ endpoints (forecast summaries, drift, recommendations, narratives).

Keys carry a per-organisation, per-group version. Group "models" covers answers that change when models, jobs or
data change (arena, summaries, drift, explanations); group "stock" covers answers that change with stock and supplier
terms (recommendations). Any successful write request bumps the groups it can affect (`invalidate_cache_after_write`,
an app-level dependency), so a cached answer cannot outlive a change the user just made, while e.g. a POS sale does not
throw away the model leaderboard. The TTL only bounds staleness from background writers (the worker, the simulator).
Redis errors fail open: the endpoint simply computes the answer.
"""

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis

from kalpa_api.core.config import get_settings
from kalpa_api.core.security import ACCESS_COOKIE, decode_token

SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


GROUPS = ("models", "stock")
STOCK_ONLY = frozenset({"pos", "purchase-orders", "inventory", "suppliers", "supplier-products"})  # first path segment


def _ver_key(org_id: uuid.UUID, group: str) -> str:
    return f"kalpa:cachever:{org_id}:{group}"


def groups_for(path: str) -> tuple[str, ...]:
    """Cache groups a write to this URL path can affect (unknown paths invalidate everything)."""
    seg = path.removeprefix("/api/v1/").split("/", 1)[0]
    return ("stock",) if seg in STOCK_ONLY else GROUPS


async def bump(redis: Redis, org_id: uuid.UUID, groups: tuple[str, ...] = GROUPS) -> None:
    """Invalidate cached answers of an organisation (all groups unless told otherwise)."""
    try:
        for g in groups:
            await redis.incr(_ver_key(org_id, g))
    except Exception:  # noqa: BLE001, S110 - cache problems never break a write
        pass


def scope_key(location_ids: list[str] | None) -> str:
    """Stable short key for a store-scoped user's visible locations (None = all)."""
    return "all" if location_ids is None else hashlib.sha1(",".join(sorted(location_ids)).encode()).hexdigest()[:10]  # noqa: S324


async def cached_json(
    redis: Redis,
    org_id: uuid.UUID,
    name: str,
    parts: str,
    ttl: int,
    build: Callable[[], Awaitable[Any]],
    group: str = "models",
) -> Response:
    """Return the cached JSON for (org, version, name, parts) or build, store and return it. X-Cache says which."""
    key = ""
    try:
        ver = await redis.get(_ver_key(org_id, group)) or b"0"
        ver_s = ver.decode() if isinstance(ver, bytes) else str(ver)
        key = f"kalpa:cache:{org_id}:{ver_s}:{name}:{parts}"
        hit = await redis.get(key)
        if hit is not None:
            return Response(content=hit, media_type="application/json", headers={"X-Cache": "hit"})
    except Exception:  # noqa: BLE001
        key = ""
    payload = json.dumps(jsonable_encoder(await build()))
    if key:
        try:
            await redis.set(key, payload, ex=ttl)
        except Exception:  # noqa: BLE001, S110
            pass
    return Response(content=payload, media_type="application/json", headers={"X-Cache": "miss"})


async def invalidate_cache_after_write(request: Request) -> AsyncIterator[None]:
    """App-level dependency: after any non-GET request that succeeded, bump the caller's cache version."""
    yield
    if request.method in SAFE:
        return
    token = request.cookies.get(ACCESS_COOKIE)
    claims = decode_token(get_settings(), token, "access") if token else None
    redis = getattr(request.app.state, "redis", None)
    if claims and redis is not None:
        await bump(redis, uuid.UUID(claims["org"]), groups_for(request.url.path))
