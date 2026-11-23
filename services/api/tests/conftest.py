"""Shared fixtures: SQLite-backed app + fakeredis. Schema via metadata (migrations are applied separately)."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from kalpa_api.core.config import get_settings
from kalpa_api.db.base import Base
from kalpa_api.db.session import get_session, make_sessionmaker
from kalpa_api.main import create_app

PASSWORD = "correct-horse-battery"


class Api:
    """httpx client wrapper that echoes the CSRF cookie into the header like the web app does."""

    def __init__(self, client: AsyncClient) -> None:
        self.c = client

    def _h(self, extra: dict[str, str] | None) -> dict[str, str]:
        h = dict(extra or {})
        tok = self.c.cookies.get("kalpa_csrf")
        if tok and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = tok
        return h

    async def get(self, url: str, **kw: Any) -> Response:
        return await self.c.get(url, **kw)

    async def post(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Response:
        return await self.c.post(url, headers=self._h(headers), **kw)

    async def patch(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Response:
        return await self.c.patch(url, headers=self._h(headers), **kw)

    async def delete(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Response:
        return await self.c.delete(url, headers=self._h(headers), **kw)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    pg = os.environ.get("TEST_DATABASE_URL")  # e.g. postgresql+asyncpg://...; default = SQLite
    if pg:
        peng = create_async_engine(pg, pool_size=20, max_overflow=20)
        async with peng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        yield peng
        await peng.dispose()
        return
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}", connect_args={"timeout": 60})

    # pysqlite quirk: without an explicit BEGIN, SAVEPOINT/ROLLBACK do not behave transactionally.
    # BEGIN IMMEDIATE takes the write lock up-front, which also serialises the 50-way concurrency test.
    @event.listens_for(eng.sync_engine, "connect")
    def _no_autobegin(dbapi_connection: Any, _rec: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(eng.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with make_sessionmaker(engine)() as s:
        yield s


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


@pytest.fixture
def make_api(
    engine: AsyncEngine, redis: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], Awaitable[Api]]:
    """Factory: each call returns an independent client (own cookie jar) on the same app/db."""
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path / "uploads"))
    app = create_app(redis_client=redis)
    maker = make_sessionmaker(engine)

    async def override() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override

    async def _make() -> Api:
        return Api(AsyncClient(transport=ASGITransport(app=app), base_url="http://test"))

    return _make


@pytest.fixture
async def owner_a(make_api: Callable[[], Awaitable[Api]]) -> Api:
    """Signed-up owner of 'Org A'."""
    api = await make_api()
    r = await api.post(
        "/api/v1/auth/signup",
        json={"email": "owner@a.com", "password": PASSWORD, "name": "Owner A", "org_name": "Org A"},
    )
    assert r.status_code == 201, r.text
    return api


@pytest.fixture
async def owner_b(make_api: Callable[[], Awaitable[Api]]) -> Api:
    """Signed-up owner of 'Org B'."""
    api = await make_api()
    r = await api.post(
        "/api/v1/auth/signup",
        json={"email": "owner@b.com", "password": PASSWORD, "name": "Owner B", "org_name": "Org B"},
    )
    assert r.status_code == 201, r.text
    return api


async def login_as(make_api: Callable[[], Awaitable[Api]], email: str) -> Api:
    """New client logged in as email."""
    api = await make_api()
    r = await api.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return api


async def seed_stock(api: Api, on_hand: int = 100) -> dict[str, str]:
    """Create a location, product and inventory position via the API; returns ids."""
    loc = (await api.post("/api/v1/locations", json={"name": "Store 1", "type": "store"})).json()
    prod = (await api.post("/api/v1/products", json={"sku": "SKU1", "name": "Widget", "category": "Snacks"})).json()
    inv = await api.post(
        "/api/v1/inventory", json={"location_id": loc["id"], "product_id": prod["id"], "on_hand": on_hand}
    )
    assert inv.status_code == 201, inv.text
    return {"loc": loc["id"], "prod": prod["id"], "inv": inv.json()["id"]}
