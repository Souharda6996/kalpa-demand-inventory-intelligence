"""Phase 6 decision engine: recommendations, PO workflow, cost parameters."""

from datetime import date, timedelta

import pytest
from conftest import PASSWORD, Api, login_as

from kalpa_api.core.config import get_settings

API = "/api/v1"


def _csv(days: int = 130) -> bytes:
    start = date(2025, 1, 1)
    lines = ["store,sku,date,units"]
    for s, sku in (("S1", "A"), ("S1", "B"), ("S2", "A")):
        for i in range(days):
            d = start + timedelta(days=i)
            lines.append(f"{s},{sku},{d.isoformat()},{5 + (i % 7) * 2 + i // 30 + len(sku) + len(s)}")
    return "\n".join(lines).encode()


@pytest.fixture
def inline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "inline_jobs", True)


async def build(owner: Api, on_hand: int = 1, backtest: bool = True) -> dict[str, str]:
    """Sales data -> supplier terms (pack 6, MOQ 12, lead 3 d) -> inventory -> optional 2-fold backtest."""
    up = (await owner.post(f"{API}/data/uploads", files={"file": ("x.csv", _csv(), "text/csv")})).json()
    assert (await owner.post(f"{API}/data/uploads/{up['upload_id']}/commit", json={})).status_code == 200
    locs = {i["name"]: i["id"] for i in (await owner.get(f"{API}/locations")).json()["items"]}
    prods = {i["sku"]: i["id"] for i in (await owner.get(f"{API}/products")).json()["items"]}
    sup = (await owner.post(f"{API}/suppliers", json={"name": "Sup", "default_lead_time_days": 3})).json()["id"]
    for sku in ("A", "B"):
        r = await owner.post(
            f"{API}/supplier-products",
            json={
                "supplier_id": sup,
                "product_id": prods[sku],
                "unit_cost": 100,
                "lead_time_days": 3,
                "moq": 12,
                "pack_size": 6,
            },
        )
        assert r.status_code == 201, r.text
    for loc, sku in (("S1", "A"), ("S1", "B"), ("S2", "A")):
        r = await owner.post(
            f"{API}/inventory", json={"location_id": locs[loc], "product_id": prods[sku], "on_hand": on_hand}
        )
        assert r.status_code == 201, r.text
    if backtest:
        r = await owner.post(f"{API}/models/backtests", json={"models": ["seasonal_naive"], "folds": 2})
        assert r.status_code == 202 and r.json()["status"] == "succeeded", r.text
    return {"sup": sup, "S1": locs["S1"], "S2": locs["S2"], "A": prods["A"], "B": prods["B"]}


def po_body(
    ids: dict[str, str], qty: int = 12, loc: str = "S1", products: tuple[str, ...] = ("A",)
) -> dict[str, object]:
    return {
        "supplier_id": ids["sup"],
        "location_id": ids[loc],
        "lines": [{"product_id": ids[k], "qty": qty} for k in products],
    }


async def position(owner: Api, ids: dict[str, str], loc: str, sku: str) -> dict[str, int]:
    items = (await owner.get(f"{API}/inventory", params={"location_id": ids[loc]})).json()["items"]
    return next(i for i in items if i["product_id"] == ids[sku])


# ------------------------------------------------------------------ purchase-order workflow
async def test_po_happy_path_updates_inventory_at_each_step(owner_a: Api) -> None:
    ids = await build(owner_a, on_hand=10, backtest=False)
    po = (await owner_a.post(f"{API}/purchase-orders", json=po_body(ids, 12))).json()
    assert po["status"] == "draft" and po["total_cost"] == 1200.0 and po["lines"][0]["unit_cost"] == 100.0
    assert (await position(owner_a, ids, "S1", "A"))["on_order"] == 0  # a draft changes nothing

    assert (await owner_a.post(f"{API}/purchase-orders/{po['id']}/approve")).json()["status"] == "approved"
    assert (await owner_a.post(f"{API}/purchase-orders/{po['id']}/send")).json()["status"] == "sent"
    assert (await position(owner_a, ids, "S1", "A"))["on_order"] == 12

    line = po["lines"][0]["id"]
    part = (
        await owner_a.post(f"{API}/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": line, "qty": 6}]})
    ).json()
    assert part["status"] == "partially_received" and part["lines"][0]["received_qty"] == 6
    p = await position(owner_a, ids, "S1", "A")
    assert (p["on_hand"], p["on_order"]) == (16, 6)

    done = (
        await owner_a.post(f"{API}/purchase-orders/{po['id']}/receive", json={"lines": [{"line_id": line, "qty": 6}]})
    ).json()
    assert done["status"] == "received"
    p = await position(owner_a, ids, "S1", "A")
    assert (p["on_hand"], p["on_order"]) == (22, 0)


async def test_po_validation_moq_pack_supplier_and_duplicates(owner_a: Api) -> None:
    ids = await build(owner_a, backtest=False)
    for qty in (7, 18 + 1, 6):  # not a pack multiple / not a pack multiple / below MOQ 12
        r = await owner_a.post(f"{API}/purchase-orders", json=po_body(ids, qty))
        assert r.status_code == 422 and r.json()["code"] == "pack_or_moq", qty
    assert (
        await owner_a.post(f"{API}/purchase-orders", json=po_body(ids, 18))
    ).status_code == 201  # 18 = 3 packs >= MOQ
    other = (await owner_a.post(f"{API}/suppliers", json={"name": "Other"})).json()["id"]
    r = await owner_a.post(f"{API}/purchase-orders", json={**po_body(ids), "supplier_id": other})
    assert r.status_code == 422 and r.json()["code"] == "not_supplied"
    dup = {**po_body(ids), "lines": [{"product_id": ids["A"], "qty": 12}, {"product_id": ids["A"], "qty": 12}]}
    assert (await owner_a.post(f"{API}/purchase-orders", json=dup)).json()["code"] == "duplicate_line"


async def test_po_state_machine_rejects_invalid_transitions(owner_a: Api) -> None:
    ids = await build(owner_a, backtest=False)
    po = (await owner_a.post(f"{API}/purchase-orders", json=po_body(ids))).json()
    base = f"{API}/purchase-orders/{po['id']}"
    line = po["lines"][0]["id"]
    assert (await owner_a.post(f"{base}/send")).status_code == 409  # not approved yet
    assert (await owner_a.post(f"{base}/receive", json={"lines": [{"line_id": line, "qty": 1}]})).status_code == 409
    await owner_a.post(f"{base}/approve")
    again = await owner_a.post(f"{base}/approve")
    assert (
        again.status_code == 409
        and again.json()["code"] == "invalid_transition"
        and again.json()["details"]["status"] == "approved"
    )
    await owner_a.post(f"{base}/send")
    over = await owner_a.post(f"{base}/receive", json={"lines": [{"line_id": line, "qty": 13}]})
    assert over.status_code == 422 and over.json()["code"] == "over_receipt"
    await owner_a.post(f"{base}/receive", json={"lines": [{"line_id": line, "qty": 12}]})
    assert (await owner_a.post(f"{base}/cancel")).status_code == 409  # already received


async def test_cancelling_a_sent_po_releases_on_order(owner_a: Api) -> None:
    ids = await build(owner_a, on_hand=0, backtest=False)
    po = (await owner_a.post(f"{API}/purchase-orders", json=po_body(ids, 24))).json()
    base = f"{API}/purchase-orders/{po['id']}"
    await owner_a.post(f"{base}/approve")
    await owner_a.post(f"{base}/send")
    await owner_a.post(f"{base}/receive", json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 6}]})
    assert (await owner_a.post(f"{base}/cancel")).json()["status"] == "cancelled"
    p = await position(owner_a, ids, "S1", "A")
    assert (p["on_hand"], p["on_order"]) == (6, 0)  # received stock stays; the open 18 are released


async def test_po_roles_store_scope_and_audit(owner_a: Api, make_api) -> None:  # type: ignore[no-untyped-def]
    ids = await build(owner_a, backtest=False)
    await owner_a.post(
        f"{API}/org/members",
        json={
            "email": "sm@a.com",
            "name": "SM",
            "password": PASSWORD,
            "role": "store_manager",
            "location_ids": [ids["S1"]],
        },
    )
    await owner_a.post(
        f"{API}/org/members", json={"email": "pl@a.com", "name": "PL", "password": PASSWORD, "role": "planner"}
    )
    await owner_a.post(
        f"{API}/org/members", json={"email": "vw@a.com", "name": "VW", "password": PASSWORD, "role": "viewer"}
    )
    sm = await login_as(make_api, "sm@a.com")
    pl = await login_as(make_api, "pl@a.com")
    vw = await login_as(make_api, "vw@a.com")

    assert (await vw.post(f"{API}/purchase-orders", json=po_body(ids))).status_code == 403
    assert (await sm.post(f"{API}/purchase-orders", json=po_body(ids, loc="S2"))).status_code == 403  # not their store
    po = (await sm.post(f"{API}/purchase-orders", json=po_body(ids, loc="S1"))).json()  # their own store
    base = f"{API}/purchase-orders/{po['id']}"
    assert (await sm.post(f"{base}/approve")).status_code == 403  # store managers cannot approve
    assert (await pl.post(f"{base}/approve")).status_code == 200  # planners can
    other_po = (await pl.post(f"{API}/purchase-orders", json=po_body(ids, loc="S2"))).json()
    assert (await sm.get(f"{API}/purchase-orders/{other_po['id']}")).status_code == 404  # scoped read
    assert {x["id"] for x in (await sm.get(f"{API}/purchase-orders")).json()} == {po["id"]}
    actions = {
        a["action"] for a in (await owner_a.get(f"{API}/audit", params={"entity": "purchase_orders"})).json()["items"]
    }
    assert {"po.create", "po.approve"} <= actions


async def test_po_is_tenant_isolated(owner_a: Api, owner_b: Api) -> None:
    ids = await build(owner_a, backtest=False)
    po = (await owner_a.post(f"{API}/purchase-orders", json=po_body(ids))).json()
    for verb in ("approve", "send", "cancel"):
        assert (await owner_b.post(f"{API}/purchase-orders/{po['id']}/{verb}")).status_code == 404
    assert (await owner_b.get(f"{API}/purchase-orders/{po['id']}")).status_code == 404
    assert (await owner_b.get(f"{API}/purchase-orders")).json() == []
    assert (await owner_b.post(f"{API}/purchase-orders", json=po_body(ids))).status_code == 404  # A's supplier/location


# ------------------------------------------------------------------ recommendations
async def test_recommendations_need_forecasts_first(owner_a: Api) -> None:
    await build(owner_a, backtest=False)
    r = await owner_a.get(f"{API}/policy/recommendations")
    assert r.status_code == 404 and r.json()["code"] == "no_forecasts"


async def test_recommendations_are_whole_packs_respect_moq_and_explain_themselves(owner_a: Api, inline: None) -> None:
    await build(owner_a, on_hand=1)
    r = (await owner_a.get(f"{API}/policy/recommendations")).json()
    assert r["model"] == "seasonal_naive" and r["items"]
    risks = [i["value_at_risk"] for i in r["items"]]
    assert risks == sorted(risks, reverse=True)  # most at risk first
    for i in r["items"]:
        assert i["recommended_qty"] % i["pack_size"] == 0 and i["recommended_qty"] >= i["moq"]
        assert i["recommended_qty"] >= i["order_up_to"] - i["inventory_position"] - 1e-6  # covers the gap
        assert 0.5 <= i["service_level"] < 1 and i["lead_days"] == 3 and i["reasons"][0] == "lead time 3 d"
    assert "Sup" in {i["supplier"] for i in r["items"]}


async def test_well_stocked_items_need_no_order(owner_a: Api, inline: None) -> None:
    await build(owner_a, on_hand=100000)
    assert (await owner_a.get(f"{API}/policy/recommendations")).json()["items"] == []
    everything = (await owner_a.get(f"{API}/policy/recommendations", params={"only_orders": "false"})).json()["items"]
    assert len(everything) == 3 and all(i["recommended_qty"] == 0 and i["stockout_date"] is None for i in everything)


async def test_higher_stockout_penalty_never_lowers_the_order(owner_a: Api, inline: None) -> None:
    await build(owner_a, on_hand=1)
    total = lambda r: sum(i["recommended_qty"] for i in r["items"])  # noqa: E731
    base = total((await owner_a.get(f"{API}/policy/recommendations")).json())
    bad = await owner_a.patch(
        f"{API}/org", json={"cost_params": {"holding_pct_year": 0.25, "stockout_penalty_ratio": 99, "review_days": 7}}
    )
    assert bad.status_code == 422  # ratio is capped at 20
    ok = await owner_a.patch(
        f"{API}/org", json={"cost_params": {"holding_pct_year": 0.25, "stockout_penalty_ratio": 10, "review_days": 7}}
    )
    assert ok.status_code == 200 and ok.json()["cost_params"]["stockout_penalty_ratio"] == 10
    assert total((await owner_a.get(f"{API}/policy/recommendations")).json()) >= base


async def test_recommendations_are_tenant_isolated(owner_a: Api, owner_b: Api, inline: None) -> None:
    await build(owner_a)
    assert (await owner_b.get(f"{API}/policy/recommendations")).status_code == 404  # B has no forecasts of its own


async def test_backtest_stores_replay_costs_and_safety_with_the_run(owner_a: Api, inline: None) -> None:
    await build(owner_a)
    arena = (await owner_a.get(f"{API}/models/arena")).json()
    m = arena[0]["metrics"]
    assert m["cost"] > 0 and 0 < m["fill_rate"] <= 1 and m["stockout_days"] is not None  # Cost tab data (fold 1 replay)
    cfg = arena[0]["config"]
    assert cfg["costed_series"] == 3 and cfg["cost_assumptions"]["review_days"] == 7 and "safety" in cfg
