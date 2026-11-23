from collections.abc import Awaitable, Callable

from conftest import PASSWORD, Api, login_as

A = "/api/v1/auth"


async def test_signup_sets_httponly_cookies_and_me_works(owner_a: Api) -> None:
    r = await owner_a.get(f"{A}/me")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "owner" and body["email"] == "owner@a.com" and "manage_org" in body["permissions"]
    assert "password" not in r.text and "hash" not in r.text


async def test_cookie_flags(make_api: Callable[[], Awaitable[Api]]) -> None:
    api = await make_api()
    r = await api.post(f"{A}/signup", json={"email": "x@x.com", "password": PASSWORD, "name": "X", "org_name": "X"})
    cookies = {h.split("=")[0]: h for h in r.headers.get_list("set-cookie")}
    assert "HttpOnly" in cookies["kalpa_access"] and "SameSite=lax" in cookies["kalpa_access"]
    assert "HttpOnly" in cookies["kalpa_refresh"] and "Path=/api/v1/auth" in cookies["kalpa_refresh"]
    assert "HttpOnly" not in cookies["kalpa_csrf"]  # must be readable by JS


async def test_unauthenticated_and_tampered_token_rejected(
    make_api: Callable[[], Awaitable[Api]], owner_a: Api
) -> None:
    anon = await make_api()
    assert (await anon.get(f"{A}/me")).status_code == 401
    anon.c.cookies.set("kalpa_access", "not.a.jwt")
    r = await anon.get(f"{A}/me")
    assert r.status_code == 401 and r.json()["code"] == "not_authenticated"


async def test_password_is_argon2_hashed(owner_a: Api, session) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from kalpa_api.db.models import User

    h = (await session.execute(select(User.password_hash))).scalar_one()
    assert h.startswith("$argon2")


async def test_duplicate_email_and_weak_password(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    api = await make_api()
    dup = await api.post(
        f"{A}/signup", json={"email": "OWNER@a.com", "password": PASSWORD, "name": "D", "org_name": "D"}
    )
    assert dup.status_code == 409
    weak = await api.post(f"{A}/signup", json={"email": "w@w.com", "password": "short", "name": "W", "org_name": "W"})
    assert weak.status_code == 422 and weak.json()["code"] == "validation_error"


async def test_login_wrong_password_then_rate_limited(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    api = await make_api()
    for _ in range(5):
        r = await api.post(f"{A}/login", json={"email": "owner@a.com", "password": "wrong-password-1"})
        assert r.status_code == 401
    locked = await api.post(f"{A}/login", json={"email": "owner@a.com", "password": PASSWORD})  # even the right one
    assert locked.status_code == 429 and locked.json()["code"] == "too_many_attempts"


async def test_successful_login_resets_failure_counter(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    api = await make_api()
    for _ in range(3):
        await api.post(f"{A}/login", json={"email": "owner@a.com", "password": "wrong-password-1"})
    assert (await api.post(f"{A}/login", json={"email": "owner@a.com", "password": PASSWORD})).status_code == 200
    for _ in range(4):
        assert (
            await api.post(f"{A}/login", json={"email": "owner@a.com", "password": "wrong-password-1"})
        ).status_code == 401


async def test_unknown_user_gets_same_error_as_wrong_password(
    make_api: Callable[[], Awaitable[Api]], owner_a: Api
) -> None:
    api = await make_api()
    a = await api.post(f"{A}/login", json={"email": "nobody@a.com", "password": "whatever-1234"})
    b = await api.post(f"{A}/login", json={"email": "owner@a.com", "password": "whatever-1234"})
    assert a.status_code == b.status_code == 401 and a.json()["message"] == b.json()["message"]


async def test_csrf_required_on_state_changing_requests(owner_a: Api) -> None:
    r = await owner_a.c.post("/api/v1/suppliers", json={"name": "S"})  # no X-CSRF-Token
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"
    bad = await owner_a.c.post("/api/v1/suppliers", json={"name": "S"}, headers={"X-CSRF-Token": "wrong"})
    assert bad.status_code == 403
    assert (await owner_a.post("/api/v1/suppliers", json={"name": "S"})).status_code == 201


async def test_refresh_rotates_and_old_refresh_is_revoked(owner_a: Api) -> None:
    old_refresh = owner_a.c.cookies.get("kalpa_refresh")
    r = await owner_a.post(f"{A}/refresh")
    assert r.status_code == 200
    owner_a.c.cookies.set("kalpa_refresh", old_refresh or "", path="/api/v1/auth")
    replay = await owner_a.post(f"{A}/refresh")
    assert replay.status_code == 401


async def test_logout_revokes_refresh_and_clears_session(owner_a: Api) -> None:
    refresh = owner_a.c.cookies.get("kalpa_refresh")
    assert (await owner_a.post(f"{A}/logout")).status_code == 204
    assert (await owner_a.get(f"{A}/me")).status_code == 401
    owner_a.c.cookies.set("kalpa_refresh", refresh or "", path="/api/v1/auth")
    owner_a.c.cookies.set("kalpa_csrf", "t")
    r = await owner_a.c.post(f"{A}/refresh", headers={"X-CSRF-Token": "t"})
    assert r.status_code == 401


async def test_removed_member_loses_access_immediately(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    add = await owner_a.post(
        "/api/v1/org/members", json={"email": "v@a.com", "name": "V", "password": PASSWORD, "role": "viewer"}
    )
    assert add.status_code == 201
    viewer = await login_as(make_api, "v@a.com")
    assert (await viewer.get("/api/v1/locations")).status_code == 200
    assert (await owner_a.delete(f"/api/v1/org/members/{add.json()['membership_id']}")).status_code == 204
    assert (await viewer.get("/api/v1/locations")).status_code == 401  # membership re-checked per request
