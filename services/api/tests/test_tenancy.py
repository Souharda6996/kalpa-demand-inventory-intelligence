"""Tenant-isolation suite: org B must never see, change or reference org A's rows."""

import pytest
from conftest import Api, seed_stock

API = "/api/v1"


async def _make_all(a: Api) -> dict[str, str]:
    ids = await seed_stock(a)
    sup = (await a.post(f"{API}/suppliers", json={"name": "Sup A"})).json()["id"]
    sp = (
        await a.post(
            f"{API}/supplier-products",
            json={"supplier_id": sup, "product_id": ids["prod"], "unit_cost": 5, "lead_time_days": 3},
        )
    ).json()["id"]
    promo = (
        await a.post(
            f"{API}/promotions",
            json={"name": "P", "start_date": "2024-01-01", "end_date": "2024-01-07", "discount_pct": 10},
        )
    ).json()["id"]
    return {**ids, "sup": sup, "sp": sp, "promo": promo}


@pytest.mark.parametrize(
    "path_key",
    [
        ("locations", "loc"),
        ("products", "prod"),
        ("suppliers", "sup"),
        ("supplier-products", "sp"),
        ("promotions", "promo"),
    ],
)
async def test_other_org_gets_404_on_get_patch_delete(owner_a: Api, owner_b: Api, path_key: tuple[str, str]) -> None:
    path, key = path_key
    ids = await _make_all(owner_a)
    url = f"{API}/{path}/{ids[key]}"
    assert (await owner_a.get(url)).status_code == 200
    assert (await owner_b.get(url)).status_code == 404
    assert (await owner_b.patch(url, json={})).status_code == 404
    assert (await owner_b.delete(url)).status_code == 404
    assert (await owner_a.get(url)).status_code == 200  # untouched


async def test_lists_only_contain_own_rows(owner_a: Api, owner_b: Api) -> None:
    await _make_all(owner_a)
    for path in ("locations", "products", "suppliers", "supplier-products", "promotions", "inventory"):
        r = (await owner_b.get(f"{API}/{path}")).json()
        assert r["total"] == 0 and r["items"] == [], path


async def test_inventory_patch_and_audit_isolated(owner_a: Api, owner_b: Api) -> None:
    ids = await _make_all(owner_a)
    assert (await owner_b.patch(f"{API}/inventory/{ids['inv']}", json={"on_hand": 0})).status_code == 404
    assert (await owner_b.get(f"{API}/audit")).json()["total"] == 1  # only B's own signup entry
    assert (await owner_a.get(f"{API}/audit")).json()["total"] > 1


async def test_cannot_reference_other_orgs_rows(owner_a: Api, owner_b: Api) -> None:
    a = await _make_all(owner_a)
    b_prod = (await owner_b.post(f"{API}/products", json={"sku": "B1", "name": "B", "category": "x"})).json()["id"]
    r = await owner_b.post(
        f"{API}/supplier-products",
        json={"supplier_id": a["sup"], "product_id": b_prod, "unit_cost": 1, "lead_time_days": 1},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_reference"
    r2 = await owner_b.post(f"{API}/inventory", json={"location_id": a["loc"], "product_id": b_prod, "on_hand": 5})
    assert r2.status_code == 422


async def test_cannot_sell_from_other_orgs_location(owner_a: Api, owner_b: Api) -> None:
    a = await _make_all(owner_a)
    r = await owner_b.post(
        f"{API}/pos/checkout",
        headers={"Idempotency-Key": "key-12345678"},
        json={"location_id": a["loc"], "lines": [{"product_id": a["prod"], "qty": 1, "unit_price": 10}]},
    )
    assert r.status_code == 404
    inv = (await owner_a.get(f"{API}/inventory")).json()["items"][0]
    assert inv["on_hand"] == 100


async def test_members_endpoint_isolated(owner_a: Api, owner_b: Api) -> None:
    members_a = (await owner_a.get(f"{API}/org/members")).json()
    mid = members_a[0]["membership_id"]
    assert (await owner_b.patch(f"{API}/org/members/{mid}", json={"role": "viewer"})).status_code == 404
    assert (await owner_b.delete(f"{API}/org/members/{mid}")).status_code == 404


async def test_org_id_in_body_is_ignored(owner_a: Api, owner_b: Api) -> None:
    sup = (await owner_a.post(f"{API}/suppliers", json={"name": "S"})).json()
    me_b = (await owner_b.get(f"{API}/auth/me")).json()
    r = await owner_b.patch(f"{API}/suppliers/{sup['id']}", json={"org_id": me_b["org_id"], "name": "hijack"})
    assert r.status_code == 404


async def test_no_endpoint_returns_password_hash(owner_a: Api) -> None:
    for path in ("auth/me", "org", "org/members", "audit"):
        assert "argon2" not in (await owner_a.get(f"{API}/{path}")).text and "password" not in (
            await owner_a.get(f"{API}/{path}")
        ).text.lower().replace("password_hash", "")
