import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from conftest import Api, seed_stock
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.db.models import InventoryPosition, SalesDaily

API = "/api/v1"


def sale(ids: dict[str, str], qty: int = 1, price: float = 100, **line: Any) -> dict[str, Any]:
    return {"location_id": ids["loc"], "lines": [{"product_id": ids["prod"], "qty": qty, "unit_price": price, **line}]}


def key(n: object) -> dict[str, str]:
    return {"Idempotency-Key": f"test-key-{n}"}


async def test_checkout_totals_gst_and_discount(owner_a: Api) -> None:
    ids = await seed_stock(owner_a, 50)
    r = await owner_a.post(
        f"{API}/pos/checkout", headers=key(1), json=sale(ids, qty=3, price=199.99, discount_pct=10, gst_pct=12)
    )
    assert r.status_code == 200, r.text
    b = r.json()
    # gross 599.97, discount 60.00, taxable 539.97, gst 64.80 (539.97*0.12=64.7964), total 604.77
    assert (b["subtotal"], b["discount"], b["gst"], b["total"]) == (599.97, 60.0, 64.8, 604.77)
    assert b["lines"][0]["taxable"] == 539.97


async def test_checkout_updates_inventory_sales_and_audit(owner_a: Api, session: AsyncSession) -> None:
    ids = await seed_stock(owner_a, 50)
    await owner_a.post(f"{API}/pos/checkout", headers=key(1), json=sale(ids, qty=4, price=10, gst_pct=0))
    await owner_a.post(f"{API}/pos/checkout", headers=key(2), json=sale(ids, qty=6, price=10, gst_pct=0))
    assert (await session.execute(select(InventoryPosition.on_hand))).scalar_one() == 40
    row = (await session.execute(select(SalesDaily.units, SalesDaily.revenue))).one()
    assert row == (10, 100.0)  # two checkouts merged into today's row
    await session.rollback()  # release the SQLite write lock held by this read transaction
    audit = (await owner_a.get(f"{API}/audit?action=pos.checkout")).json()
    assert audit["total"] == 2


async def test_idempotent_replay_returns_same_receipt_and_sells_once(owner_a: Api, session: AsyncSession) -> None:
    ids = await seed_stock(owner_a, 10)
    first = await owner_a.post(f"{API}/pos/checkout", headers=key("same"), json=sale(ids, qty=2))
    again = await owner_a.post(f"{API}/pos/checkout", headers=key("same"), json=sale(ids, qty=2))
    assert first.status_code == again.status_code == 200
    assert first.json()["receipt_id"] == again.json()["receipt_id"]
    assert (await session.execute(select(InventoryPosition.on_hand))).scalar_one() == 8


async def test_key_reuse_with_different_body_rejected(owner_a: Api) -> None:
    ids = await seed_stock(owner_a, 10)
    await owner_a.post(f"{API}/pos/checkout", headers=key("k"), json=sale(ids, qty=1))
    r = await owner_a.post(f"{API}/pos/checkout", headers=key("k"), json=sale(ids, qty=2))
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reuse"


async def test_missing_idempotency_key_is_422(owner_a: Api) -> None:
    ids = await seed_stock(owner_a, 10)
    assert (await owner_a.post(f"{API}/pos/checkout", json=sale(ids))).status_code == 422


async def test_insufficient_stock_is_all_or_nothing(owner_a: Api, session: AsyncSession) -> None:
    ids = await seed_stock(owner_a, 5)
    p2 = (await owner_a.post(f"{API}/products", json={"sku": "S2", "name": "B", "category": "x"})).json()["id"]
    await owner_a.post(f"{API}/inventory", json={"location_id": ids["loc"], "product_id": p2, "on_hand": 1})
    body = {
        "location_id": ids["loc"],
        "lines": [
            {"product_id": ids["prod"], "qty": 3, "unit_price": 1},
            {"product_id": p2, "qty": 2, "unit_price": 1},
        ],
    }
    r = await owner_a.post(f"{API}/pos/checkout", headers=key("x"), json=body)
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    stock = sorted((await session.execute(select(InventoryPosition.on_hand))).scalars())
    assert stock == [1, 5]  # line 1 was NOT applied
    assert (await session.execute(select(func.count()).select_from(SalesDaily))).scalar_one() == 0
    await session.rollback()  # release the SQLite write lock held by this read transaction
    body["lines"][1]["qty"] = 1  # retry with the same key now succeeds (failed attempt left no key behind)
    assert (await owner_a.post(f"{API}/pos/checkout", headers=key("x"), json=body)).status_code == 200


async def test_sale_event_published(owner_a: Api, redis: Any) -> None:
    ids = await seed_stock(owner_a, 5)
    me = (await owner_a.get(f"{API}/auth/me")).json()
    ps = redis.pubsub()
    await ps.subscribe(f"kalpa:events:{me['org_id']}")
    await ps.get_message(timeout=1)  # subscribe confirmation
    await owner_a.post(f"{API}/pos/checkout", headers=key("e"), json=sale(ids, qty=2))
    msg = await ps.get_message(timeout=2)
    assert msg and json.loads(msg["data"])["type"] == "sale"


async def test_50_parallel_checkouts_never_oversell(
    owner_a: Api, make_api: Callable[[], Awaitable[Api]], session: AsyncSession
) -> None:
    ids = await seed_stock(owner_a, 20)
    clients = [owner_a] + [await _clone(owner_a, make_api) for _ in range(9)]

    async def one(i: int) -> int:
        return (
            await clients[i % len(clients)].post(f"{API}/pos/checkout", headers=key(f"par-{i}"), json=sale(ids, qty=1))
        ).status_code

    codes = await asyncio.gather(*(one(i) for i in range(50)))
    assert codes.count(200) == 20 and codes.count(409) == 30, sorted(set(codes))
    assert (await session.execute(select(InventoryPosition.on_hand))).scalar_one() == 0  # never negative
    assert (await session.execute(select(SalesDaily.units))).scalar_one() == 20


async def _clone(src: Api, make_api: Callable[[], Awaitable[Api]]) -> Api:
    """Another client with the same session cookies (parallel requests from one user)."""
    c = await make_api()
    for name, value in src.c.cookies.items():
        c.c.cookies.set(name, value, path="/api/v1/auth" if name == "kalpa_refresh" else "/")
    return c
