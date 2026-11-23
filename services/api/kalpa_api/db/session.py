"""Async engine / session factory.

The API shares ONE engine (and its connection pool) for the whole process. It used to build and dispose an engine for
every request, which meant a new Postgres connection (TCP + authentication) per call: a ~50 ms floor on every endpoint
and a connection storm under load (found by the 50-user load test). Workers and scripts that want their own engine
still use make_engine().
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kalpa_api.core.config import get_settings

_engine: AsyncEngine | None = None
_maker: async_sessionmaker[AsyncSession] | None = None


def make_engine(url: str | None = None) -> AsyncEngine:
    """Create a standalone async engine for the configured (or given) URL (workers, scripts, tests)."""
    return create_async_engine(url or get_settings().database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to an engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    """The process-wide engine with a sized connection pool (created on first use)."""
    global _engine, _maker
    if _engine is None:
        s = get_settings()
        pool: dict[str, Any] = (
            {}
            if s.database_url.startswith("sqlite")
            else {"pool_size": s.db_pool_size, "max_overflow": s.db_max_overflow, "pool_recycle": 1800}
        )
        _engine = create_async_engine(s.database_url, pool_pre_ping=True, **pool)
        _maker = make_sessionmaker(_engine)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session from the shared pool."""
    get_engine()
    assert _maker is not None
    async with _maker() as session:
        yield session


async def dispose_engine() -> None:
    """Close the shared pool (application shutdown)."""
    global _engine, _maker
    if _engine is not None:
        await _engine.dispose()
    _engine, _maker = None, None
