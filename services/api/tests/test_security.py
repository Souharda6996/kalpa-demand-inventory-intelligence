"""Phase 9 security checks (OWASP-style): config guard, headers, error shape, rate limits, injection, uploads, tokens."""

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import PASSWORD, Api
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from kalpa_api.core.config import Settings, assert_safe_secrets, get_settings
from kalpa_api.core.ratelimit import hit
from kalpa_api.db.models import Upload

API = "/api/v1"
SAFE: dict[str, Any] = {
    "environment": "production",
    "jwt_access_secret": "a" * 40,
    "jwt_refresh_secret": "b" * 40,
    "database_url": "postgresql+asyncpg://app:Xk29fj3@db:5432/kalpa",
    "cookie_secure": True,
}


def settings(**over: Any) -> Settings:
    return Settings(_env_file=None, **{**SAFE, **over})  # type: ignore[call-arg]


# ------------------------------------------------------------------ configuration guard
def test_a_safe_production_config_and_the_dev_defaults_both_start() -> None:
    assert_safe_secrets(settings())
    assert_safe_secrets(Settings(_env_file=None))  # type: ignore[call-arg]  # environment=dev with the built-in defaults


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"jwt_access_secret": "dev-only-access-secret-change-me-0123456789"}, "JWT_ACCESS_SECRET"),
        ({"jwt_refresh_secret": "dev-only-refresh-secret-change-me-012345678"}, "JWT_ACCESS_SECRET"),
        ({"jwt_access_secret": "short", "jwt_refresh_secret": "b" * 40}, "at least 32"),
        ({"jwt_access_secret": "c" * 40, "jwt_refresh_secret": "c" * 40}, "must differ"),
        ({"database_url": "postgresql+asyncpg://kalpa:kalpa@localhost:5432/kalpa"}, "DATABASE_URL"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
    ],
)
def test_unsafe_production_configs_are_refused_with_a_reason(override: dict[str, Any], needle: str) -> None:
    with pytest.raises(RuntimeError, match=needle):
        assert_safe_secrets(settings(**override))


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(RuntimeError) as e:
        assert_safe_secrets(Settings(environment="production", _env_file=None))  # type: ignore[call-arg]
    assert all(k in str(e.value) for k in ("JWT_ACCESS_SECRET", "DATABASE_URL", "COOKIE_SECURE"))


# ------------------------------------------------------------------ headers and errors
async def test_security_headers_on_success_and_error_responses(owner_a: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    want = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
        "cross-origin-opener-policy": "same-origin",
        "cache-control": "no-store",
    }
    for path in (f"{API}/locations", f"{API}/health", f"{API}/does-not-exist"):
        r = await owner_a.get(path)
        for k, v in want.items():
            assert r.headers.get(k) == v, (path, k, r.headers.get(k))
        assert "camera=()" in r.headers["permissions-policy"]
        assert "strict-transport-security" not in r.headers  # plain HTTP in dev: HSTS would be meaningless
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    assert "max-age=63072000" in (await owner_a.get(f"{API}/health")).headers["strict-transport-security"]


async def test_an_unhandled_error_returns_the_standard_shape_without_internals(owner_a: Api) -> None:
    app = owner_a.c._transport.app  # type: ignore[attr-defined]

    async def boom() -> None:
        raise RuntimeError("password is hunter2 and the table is secret_table")

    app.add_api_route(f"{API}/_boom", boom, methods=["GET"])
    r = await owner_a.get(f"{API}/_boom", headers={"Origin": "http://localhost:3000"})
    body = r.json()
    assert r.status_code == 500 and body["code"] == "internal_error" and body["details"] is None
    assert "hunter2" not in r.text and "secret_table" not in r.text and "Traceback" not in r.text
    assert body["request_id"] == r.headers["x-request-id"]
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"  # a browser can read the error
    assert r.headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------ rate limits
def client_from(app: Any, ip: str) -> Api:
    return Api(AsyncClient(transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://test"))


def signup_body(i: int) -> dict[str, str]:
    return {"email": f"u{i}@rl.com", "password": PASSWORD, "name": f"U{i}", "org_name": f"Org {i}"}


async def test_signup_is_limited_per_client_ip_and_a_spoofed_forwarded_for_does_not_help(
    make_api: Callable[[], Awaitable[Api]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "signup_per_hour", 3)
    a = await make_api()
    app = a.c._transport.app  # type: ignore[attr-defined]
    for i in range(3):
        assert (await a.post(f"{API}/auth/signup", json=signup_body(i))).status_code == 201
    r = await a.post(f"{API}/auth/signup", json=signup_body(3), headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"  # the header is ignored: same TCP peer
    other = client_from(app, "203.0.113.9")  # a different machine is a different bucket
    assert (await other.post(f"{API}/auth/signup", json=signup_body(4))).status_code == 201


async def test_login_lockout_is_keyed_on_the_peer_not_on_forwarded_for(
    owner_a: Api, make_api: Callable[[], Awaitable[Api]]
) -> None:
    c = await make_api()
    for i in range(5):
        r = await c.post(
            f"{API}/auth/login",
            json={"email": "owner@a.com", "password": "wrong-password-1"},
            headers={"X-Forwarded-For": f"1.1.1.{i}"},
        )
        assert r.status_code == 401
    r = await c.post(
        f"{API}/auth/login", json={"email": "owner@a.com", "password": PASSWORD}, headers={"X-Forwarded-For": "9.9.9.9"}
    )
    assert r.status_code == 429  # rotating the header did not reset the counter, and the right password is refused too


async def test_copilot_uploads_and_demo_are_limited_per_user(
    owner_a: Api, owner_b: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "copilot_per_minute", 2)
    monkeypatch.setattr(get_settings(), "uploads_per_hour", 2)
    monkeypatch.setattr(get_settings(), "demo_loads_per_hour", 1)
    q = {"question": "Compare the models"}
    assert [(await owner_a.post(f"{API}/copilot/chat", json=q)).status_code for _ in range(3)] == [200, 200, 429]
    assert (await owner_b.post(f"{API}/copilot/chat", json=q)).status_code == 200  # another user has their own budget
    csv = ("x.csv", b"store,sku,date,units\nS1,A,2025-01-01,3\n", "text/csv")
    assert [(await owner_a.post(f"{API}/data/uploads", files={"file": csv})).status_code for _ in range(3)] == [
        200,
        200,
        429,
    ]
    r = await owner_a.post(f"{API}/data/demo")
    assert r.status_code == 200
    again = await owner_a.post(f"{API}/data/demo")
    assert again.status_code == 429 and "Too many requests" in again.json()["message"]


async def test_the_limiter_fails_open_when_redis_is_down() -> None:
    class Down:
        async def incr(self, *_: Any) -> int:
            raise ConnectionError("redis is down")

    await hit(Down(), "k", 1, 60)  # type: ignore[arg-type]  # must not raise: availability beats throttling here


# ------------------------------------------------------------------ injection, uploads, tokens
INJECTIONS = [
    "' OR '1'='1",
    "'; DROP TABLE products; --",
    '" OR 1=1 --',
    "1; SELECT pg_sleep(5)",
    "%' UNION SELECT password_hash FROM users --",
    "\x00",
]


async def test_injection_payloads_are_data_never_sql(owner_a: Api) -> None:
    assert (
        await owner_a.post(f"{API}/products", json={"sku": "SAFE-1", "name": "Widget", "category": "Snacks"})
    ).status_code == 201
    for p in INJECTIONS:
        r = await owner_a.get(f"{API}/products", params={"category": p})
        assert r.status_code in (200, 422) and (r.status_code == 422 or r.json()["items"] == []), p
        for bad in ({"sort": p}, {"page_size": p}, {"page": p}, {p: "x"}):
            assert (await owner_a.get(f"{API}/products", params=bad)).status_code == 422, (p, bad)
        made = await owner_a.post(
            f"{API}/products", json={"sku": f"I-{uuid.uuid4().hex[:6]}", "name": p, "category": "Snacks"}
        )
        if made.status_code == 201:
            assert made.json()["name"] == p  # stored verbatim through bound parameters
    still = await owner_a.get(f"{API}/products")
    assert still.status_code == 200 and any(i["sku"] == "SAFE-1" for i in still.json()["items"])  # nothing was dropped
    assert (await owner_a.get(f"{API}/products/not-a-uuid")).status_code == 422
    assert (await owner_a.get(f"{API}/auth/me")).json()["email"] == "owner@a.com"  # users table intact


def upload_root() -> Path:
    return Path(get_settings().upload_dir).resolve()


def files_outside_upload_root(root: Path) -> list[Path]:
    """Blocking filesystem probe, kept out of the async test body."""
    return [*root.parent.glob("outside*.csv"), *root.glob("**/outside*.csv")]


def inside_root(path: str, root: Path) -> bool:
    p = Path(path).resolve()
    return root in p.parents and p.suffix == ".csv" and bool(uuid.UUID(p.stem))


async def test_upload_filenames_cannot_escape_the_upload_directory(owner_a: Api, session: Any) -> None:
    csv = b"store,sku,date,units\nS1,A,2025-01-01,3\n"
    root = upload_root()
    names = ("../../outside.csv", "..\\..\\outside2.csv", "/etc/passwd", "a/b/c.csv")
    for name in names:
        assert (
            await owner_a.post(f"{API}/data/uploads", files={"file": (name, csv, "text/csv")})
        ).status_code == 200, name
    rows = (await session.execute(select(Upload))).scalars().all()
    assert all(inside_root(u.object_path, root) for u in rows)  # server-generated uuid name inside the upload root
    assert {u.filename for u in rows} == {"outside.csv", "outside2.csv", "passwd", "c.csv"}  # both separators stripped
    assert files_outside_upload_root(root) == []


async def test_an_expired_access_token_is_rejected(
    make_api: Callable[[], Awaitable[Api]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "access_ttl_minutes", -5)  # tokens are issued already expired
    api = await make_api()
    assert (await api.post(f"{API}/auth/signup", json=signup_body(99))).status_code == 201
    r = await api.get(f"{API}/auth/me")
    assert r.status_code == 401 and r.json()["code"] == "not_authenticated"
    monkeypatch.setattr(get_settings(), "access_ttl_minutes", 15)
    assert (await api.post(f"{API}/auth/refresh")).status_code == 200  # the refresh token is still good
    assert (await api.get(f"{API}/auth/me")).status_code == 200
