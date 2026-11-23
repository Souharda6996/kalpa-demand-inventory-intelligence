"""Error format, request id, pagination/sort/filter, audit before/after."""

from conftest import Api

API = "/api/v1"


async def test_error_shape_and_request_id(owner_a: Api) -> None:
    r = await owner_a.get(
        f"{API}/products/00000000-0000-0000-0000-000000000000", headers={"X-Request-ID": "trace-abcdef12"}
    )
    assert r.status_code == 404
    body = r.json()
    assert set(body) == {"code", "message", "details", "request_id"} and body["code"] == "not_found"
    assert body["request_id"] == "trace-abcdef12" == r.headers["x-request-id"]
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_validation_error_shape(owner_a: Api) -> None:
    r = await owner_a.post(f"{API}/products", json={"sku": ""})
    assert r.status_code == 422 and r.json()["code"] == "validation_error" and r.json()["details"]


async def test_pagination_sorting_filtering(owner_a: Api) -> None:
    for i, cat in enumerate(["b", "a", "b", "c", "a"]):
        await owner_a.post(f"{API}/products", json={"sku": f"S{i}", "name": f"N{i}", "category": cat})
    page = (await owner_a.get(f"{API}/products?page=2&page_size=2&sort=sku")).json()
    assert page["total"] == 5 and [p["sku"] for p in page["items"]] == ["S2", "S3"]
    desc = (await owner_a.get(f"{API}/products?sort=-sku&page_size=1")).json()["items"][0]["sku"]
    assert desc == "S4"
    assert (await owner_a.get(f"{API}/products?category=a")).json()["total"] == 2
    assert (await owner_a.get(f"{API}/products?sort=password")).status_code == 422
    assert (await owner_a.get(f"{API}/products?nonsense=1")).status_code == 422
    assert (await owner_a.get(f"{API}/products?page_size=1000")).status_code == 422


async def test_audit_records_before_and_after(owner_a: Api) -> None:
    sup = (await owner_a.post(f"{API}/suppliers", json={"name": "Old"})).json()["id"]
    await owner_a.patch(f"{API}/suppliers/{sup}", json={"name": "New"})
    await owner_a.delete(f"{API}/suppliers/{sup}")
    items = (await owner_a.get(f"{API}/audit?entity=suppliers")).json()["items"]
    by_action = {i["action"]: i for i in items}
    assert by_action["update"]["before"]["name"] == "Old" and by_action["update"]["after"]["name"] == "New"
    assert by_action["delete"]["before"]["name"] == "New" and by_action["create"]["after"]["name"] == "Old"
    assert all(i["user_id"] for i in items)


async def test_latency_metrics_recorded_in_redis(owner_a: Api, redis) -> None:  # type: ignore[no-untyped-def]
    await owner_a.get(f"{API}/auth/me")
    rows = await redis.lrange("kalpa:metrics:latency", 0, 5)
    assert rows and b"/api/v1/auth/me" in rows[0]


async def test_cors_allow_list(make_api) -> None:  # type: ignore[no-untyped-def]
    api = await make_api()
    ok = await api.c.options(
        f"{API}/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    bad = await api.c.options(
        f"{API}/health", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in bad.headers
