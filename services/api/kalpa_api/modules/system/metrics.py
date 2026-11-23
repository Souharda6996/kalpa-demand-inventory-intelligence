"""Backend visibility: request latency / throughput / errors (from the middleware's Redis list), queue, DB, cache."""

import time
from collections import defaultdict
from importlib import metadata
from typing import Any

import numpy as np
import structlog
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.config import get_settings
from kalpa_api.core.middleware import LATENCY_KEY
from kalpa_api.db.models import Job
from kalpa_api.modules.system.service import WORKER_HEARTBEAT_KEY

log = structlog.get_logger()
WINDOW_MINUTES = 30
ARQ_QUEUE_KEY = "arq:queue"
PACKAGES = ["fastapi", "sqlalchemy", "pydantic", "arq", "redis", "mlflow", "lightgbm", "torch", "pandas"]


class LatencyPoint(BaseModel):
    minute: int  # unix time of the start of the minute
    requests: int
    p50_ms: float | None
    p95_ms: float | None
    errors: int  # HTTP 5xx


class RouteStat(BaseModel):
    route: str
    requests: int
    p95_ms: float


class SystemMetrics(BaseModel):
    window_minutes: int
    requests_total: int
    requests_per_min: float
    error_rate: float  # share of 5xx in the window
    p50_ms: float | None
    p95_ms: float | None
    series: list[LatencyPoint]
    slowest: list[RouteStat]
    queue_depth: int | None  # jobs waiting in the ARQ queue (all organisations)
    jobs_queued: int  # this organisation
    jobs_running: int
    db_engine: str
    db_version: str | None
    db_size_bytes: int | None
    cache_hit_rate: float | None  # Redis keyspace hits / (hits + misses), server-wide since the Redis start
    redis_version: str | None
    worker: str
    api_version: str
    git_sha: str
    versions: dict[str, str]
    docs_url: str
    mlflow_url: str


def _pct(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(values, q)), 1) if values else None


def summarise(entries: list[str], now: float, window_minutes: int = WINDOW_MINUTES) -> dict[str, Any]:
    """Parse 'ts|METHOD|path|status|ms' lines into overall stats, a per-minute series and the slowest routes."""
    cutoff = now - window_minutes * 60
    all_ms: list[float] = []
    errors = 0
    per_min: dict[int, list[tuple[float, int]]] = defaultdict(list)
    per_route: dict[str, list[float]] = defaultdict(list)
    for line in entries:
        try:
            ts_s, method, path, status_s, ms_s = line.split("|")
            ts, status, ms = int(ts_s), int(status_s), float(ms_s)
        except ValueError:
            continue
        if ts < cutoff:
            continue
        all_ms.append(ms)
        errors += int(status >= 500)
        per_min[ts // 60 * 60].append((ms, status))
        per_route[f"{method} {path}"].append(ms)
    series = [
        LatencyPoint(
            minute=m,
            requests=len(v),
            p50_ms=_pct([x[0] for x in v], 50),
            p95_ms=_pct([x[0] for x in v], 95),
            errors=sum(1 for x in v if x[1] >= 500),
        )
        for m, v in sorted(per_min.items())
    ]
    slowest = sorted(
        (RouteStat(route=r, requests=len(v), p95_ms=_pct(v, 95) or 0.0) for r, v in per_route.items() if len(v) >= 3),
        key=lambda s: -s.p95_ms,
    )[:6]
    return {
        "total": len(all_ms),
        "per_min": round(len(all_ms) / window_minutes, 2),
        "error_rate": round(errors / len(all_ms), 4) if all_ms else 0.0,
        "p50": _pct(all_ms, 50),
        "p95": _pct(all_ms, 95),
        "series": series,
        "slowest": slowest,
    }


async def _db_facts(session: AsyncSession) -> tuple[str, str | None, int | None]:
    name = session.get_bind().dialect.name
    try:
        if name == "postgresql":
            size = (await session.execute(text("select pg_database_size(current_database())"))).scalar_one()
            ver = (await session.execute(text("select version()"))).scalar_one()
            return "PostgreSQL", " ".join(str(ver).split()[:2]), int(size)
        if name == "sqlite":
            size = (
                await session.execute(
                    text("select page_count * page_size from pragma_page_count(), pragma_page_size()")
                )
            ).scalar_one()
            ver = (await session.execute(text("select sqlite_version()"))).scalar_one()
            return "SQLite", f"SQLite {ver}", int(size)
    except Exception:  # noqa: BLE001 - diagnostics must never take the page down
        await session.rollback()
    return name, None, None


def _versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for pkg in PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            continue
    return out


async def collect(session: AsyncSession, redis: Redis, org_id: Any) -> SystemMetrics:
    """Everything the System page shows, from live sources (no stored or invented numbers)."""
    s = get_settings()
    entries = [b.decode() if isinstance(b, bytes) else str(b) for b in await redis.lrange(LATENCY_KEY, 0, -1)]  # type: ignore[misc]
    stats = summarise(entries, time.time())
    info: dict[str, Any] = {}
    try:
        info = dict(await redis.info())
    except Exception as e:  # noqa: BLE001
        log.debug("redis_info_failed", error=type(e).__name__)
    hits, misses = info.get("keyspace_hits"), info.get("keyspace_misses")
    try:
        depth: int | None = int(await redis.zcard(ARQ_QUEUE_KEY))
    except Exception:  # noqa: BLE001
        depth = None
    counts = dict(
        (await session.execute(select(Job.status, func.count()).where(Job.org_id == org_id).group_by(Job.status))).all()
    )
    engine, db_version, db_size = await _db_facts(session)
    return SystemMetrics(
        window_minutes=WINDOW_MINUTES,
        requests_total=stats["total"],
        requests_per_min=stats["per_min"],
        error_rate=stats["error_rate"],
        p50_ms=stats["p50"],
        p95_ms=stats["p95"],
        series=stats["series"],
        slowest=stats["slowest"],
        queue_depth=depth,
        jobs_queued=int(counts.get("queued", 0)),
        jobs_running=int(counts.get("running", 0)),
        db_engine=engine,
        db_version=db_version,
        db_size_bytes=db_size,
        cache_hit_rate=round(hits / (hits + misses), 4)
        if isinstance(hits, int) and isinstance(misses, int) and hits + misses
        else None,
        redis_version=info.get("redis_version"),
        worker="up" if await redis.exists(WORKER_HEARTBEAT_KEY) else "down",
        api_version=s.app_version,
        git_sha=s.git_sha,
        versions=_versions(),
        docs_url="/docs",
        mlflow_url=s.mlflow_tracking_uri,
    )
