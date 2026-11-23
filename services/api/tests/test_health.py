"""Health endpoint tests (dependencies monkeypatched - unit level)."""

import pytest
from httpx import ASGITransport, AsyncClient

from kalpa_api.main import create_app
from kalpa_api.modules.system import service


@pytest.fixture
def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


async def test_health_all_up(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def up_db(_: str) -> str:
        return "up"

    async def up_redis(_: str) -> tuple[str, str]:
        return "up", "up"

    monkeypatch.setattr(service, "check_db", up_db)
    monkeypatch.setattr(service, "check_redis", up_redis)
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert {"version", "git_sha", "db", "redis", "worker"} <= body.keys()


async def test_health_degraded_when_unreachable(client: AsyncClient) -> None:
    """With no DB/Redis on the test box, the endpoint reports degraded, not 500."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
