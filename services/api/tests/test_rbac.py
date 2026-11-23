"""RBAC matrix (Part D3) enforced end-to-end."""

from collections.abc import Awaitable, Callable

import pytest
from conftest import PASSWORD, Api, login_as, seed_stock

from kalpa_api.core.rbac import PERMISSIONS, allowed

API = "/api/v1"

# Part D3, written out independently of rbac.py so a typo there is caught.
D3 = {
    "manage_org": {"owner"},
    "upload_data": {"owner", "planner"},
    "promote_model": {"owner", "planner"},
    "edit_scenarios": {"owner", "planner", "store_manager"},
    "create_po": {"owner", "planner", "store_manager"},
    "approve_po": {"owner", "planner"},
    "receive_po": {"owner", "planner", "store_manager"},
    "view": {"owner", "planner", "store_manager", "viewer"},
}


@pytest.mark.parametrize("perm", sorted(D3))
@pytest.mark.parametrize("role", ["owner", "planner", "store_manager", "viewer"])
def test_matrix_matches_d3(perm: str, role: str) -> None:
    assert allowed(role, perm) == (role in D3[perm])


def test_pos_checkout_follows_receive_po() -> None:
    assert PERMISSIONS["pos_checkout"] == PERMISSIONS["receive_po"]


async def _team(owner: Api, make_api: Callable[[], Awaitable[Api]], store: str | None = None) -> dict[str, Api]:
    for role in ("planner", "store_manager", "viewer"):
        body: dict[str, object] = {"email": f"{role}@a.com", "name": role, "password": PASSWORD, "role": role}
        if role == "store_manager" and store:
            body["location_ids"] = [store]
        assert (await owner.post(f"{API}/org/members", json=body)).status_code == 201
    return {
        "owner": owner,
        **{r: await login_as(make_api, f"{r}@a.com") for r in ("planner", "store_manager", "viewer")},
    }


async def test_master_data_write_matrix(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    ids = await seed_stock(owner_a)
    team = await _team(owner_a, make_api, ids["loc"])
    expect = {"owner": 201, "planner": 201, "store_manager": 403, "viewer": 403}
    for role, status in expect.items():
        r = await team[role].post(f"{API}/suppliers", json={"name": f"S-{role}"})
        assert r.status_code == status, role
    for role in team:  # everyone can read
        assert (await team[role].get(f"{API}/suppliers")).status_code == 200


async def test_org_and_member_management_owner_only(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    team = await _team(owner_a, make_api)
    for role in ("planner", "store_manager", "viewer"):
        assert (await team[role].patch(f"{API}/org", json={"name": "Hacked"})).status_code == 403
        assert (await team[role].get(f"{API}/org/members")).status_code == 403
        assert (await team[role].get(f"{API}/audit")).status_code == 403
    assert (await team["owner"].patch(f"{API}/org", json={"name": "Renamed"})).json()["name"] == "Renamed"


async def test_upload_data_owner_planner_only(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    team = await _team(owner_a, make_api)
    csv = b"store,sku,date,units\nS1,A1,2024-01-01,3\n"
    for role, status in {"owner": 200, "planner": 200, "store_manager": 403, "viewer": 403}.items():
        r = await team[role].post(f"{API}/data/uploads", files={"file": ("x.csv", csv, "text/csv")})
        assert r.status_code == status, role


async def test_pos_roles_and_store_scope(owner_a: Api, make_api: Callable[[], Awaitable[Api]]) -> None:
    ids = await seed_stock(owner_a)
    other = (await owner_a.post(f"{API}/locations", json={"name": "Store 2", "type": "store"})).json()["id"]
    await owner_a.post(f"{API}/inventory", json={"location_id": other, "product_id": ids["prod"], "on_hand": 10})
    team = await _team(owner_a, make_api, ids["loc"])

    def sale(loc: str) -> dict[str, object]:
        return {"location_id": loc, "lines": [{"product_id": ids["prod"], "qty": 1, "unit_price": 10}]}

    n = 0

    async def go(role: str, loc: str) -> int:
        nonlocal n
        n += 1
        return (
            await team[role].post(
                f"{API}/pos/checkout", headers={"Idempotency-Key": f"key-{role}-{n}-xxxx"}, json=sale(loc)
            )
        ).status_code

    assert await go("viewer", ids["loc"]) == 403
    assert await go("store_manager", ids["loc"]) == 200  # own store
    assert await go("store_manager", other) == 403  # someone else's store
    assert await go("planner", other) == 200
    inv = (await team["store_manager"].get(f"{API}/inventory")).json()
    assert {i["location_id"] for i in inv["items"]} == {ids["loc"]}  # scoped read


async def test_last_owner_cannot_be_removed_or_demoted(owner_a: Api) -> None:
    me = (await owner_a.get(f"{API}/auth/me")).json()
    members = (await owner_a.get(f"{API}/org/members")).json()
    mid = next(m["membership_id"] for m in members if m["user_id"] == me["user_id"])
    assert (await owner_a.patch(f"{API}/org/members/{mid}", json={"role": "viewer"})).status_code == 409
    assert (await owner_a.delete(f"{API}/org/members/{mid}")).status_code == 409
