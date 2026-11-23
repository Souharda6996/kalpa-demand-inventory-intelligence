import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from conftest import PASSWORD, Api, login_as

from kalpa_api.core.config import get_settings

API = "/api/v1"


def _priced_csv(days: int = 220) -> bytes:
    """3 series whose demand really depends on price: units ~ Poisson(12 * (price/100)^-2); price moves weekly."""
    rng = np.random.default_rng(0)
    start = date(2025, 1, 1)
    lines = ["store,sku,date,units,price"]
    for store, sku in (("S1", "A"), ("S1", "B"), ("S2", "A")):
        price = 100.0
        for i in range(days):
            if i % 7 == 0:
                price = 100.0 * float(rng.uniform(0.7, 1.3))
            units = int(rng.poisson(12 * (price / 100) ** -2))
            lines.append(f"{store},{sku},{(start + timedelta(days=i)).isoformat()},{units},{price:.2f}")
    return "\n".join(lines).encode()


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(get_settings(), "inline_jobs", True)
    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path / "models"))


async def build(owner: Api, backtest: bool = True) -> dict[str, str]:
    up = (await owner.post(f"{API}/data/uploads", files={"file": ("p.csv", _priced_csv(), "text/csv")})).json()
    assert (await owner.post(f"{API}/data/uploads/{up['upload_id']}/commit", json={})).status_code == 200
    locs = {i["name"]: i["id"] for i in (await owner.get(f"{API}/locations")).json()["items"]}
    prods = {i["sku"]: i["id"] for i in (await owner.get(f"{API}/products")).json()["items"]}
    sup = (await owner.post(f"{API}/suppliers", json={"name": "Sup"})).json()["id"]
    for sku in ("A", "B"):
        await owner.post(
            f"{API}/supplier-products",
            json={"supplier_id": sup, "product_id": prods[sku], "unit_cost": 60, "lead_time_days": 3},
        )
    for loc, sku in (("S1", "A"), ("S1", "B"), ("S2", "A")):
        await owner.post(f"{API}/inventory", json={"location_id": locs[loc], "product_id": prods[sku], "on_hand": 20})
    if backtest:
        r = await owner.post(f"{API}/models/backtests", json={"models": ["lightgbm_tweedie"], "folds": 1})
        assert r.status_code == 202 and r.json()["status"] == "succeeded", r.text
    return {**{f"loc_{k}": v for k, v in locs.items()}, **{f"prod_{k}": v for k, v in prods.items()}}


def run_body(ids: dict[str, str], loc: str = "S1", **changes: object) -> dict[str, object]:
    return {"scope": {"location_id": ids[f"loc_{loc}"]}, "changes": changes}


async def test_needs_a_saved_model_first(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a, backtest=False)
    r = await owner_a.post(f"{API}/scenarios/run", json=run_body(ids, price_change_pct=-10))
    assert r.status_code == 404 and r.json()["code"] == "no_scenario_model"


async def test_price_cut_raises_units_and_price_rise_lowers_them(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a)
    cut = (await owner_a.post(f"{API}/scenarios/run", json=run_body(ids, price_change_pct=-20))).json()
    rise = (await owner_a.post(f"{API}/scenarios/run", json=run_body(ids, price_change_pct=20))).json()
    assert (
        cut["series"] == 2 and len(cut["days"]) == 28 and cut["fan"] is None
    )  # 2 series: no fan (quantiles don't add)
    assert cut["delta_units"] > 0 > rise["delta_units"], (cut["delta_units"], rise["delta_units"])
    assert cut["delta_revenue"] is not None and cut["delta_margin"] is not None  # prices and unit costs are known
    assert all(d["scenario"] > d["baseline"] for d in cut["days"][:7])  # visibly higher from the first days on


async def test_single_series_scope_returns_a_valid_fan_chart_and_notes(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a)
    body = {
        "scope": {"location_id": ids["loc_S1"], "product_ids": [ids["prod_A"]]},
        "changes": {"price_change_pct": -15, "promo_depth_pct": 10, "weather_shift_c": 3},
    }
    r = (await owner_a.post(f"{API}/scenarios/run", json=body)).json()
    assert r["series"] == 1 and r["fan"] is not None
    for side in ("baseline", "scenario"):
        f = r["fan"][side]
        assert len(f["p50"]) == 28 and all(a <= b <= c for a, b, c in zip(f["p10"], f["p50"], f["p90"], strict=True))
    assert any("weather" in n for n in r["notes"])  # honestly reported as not modelled


async def test_festival_scope_validation_and_scope_limits(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a)
    fest = await owner_a.post(f"{API}/scenarios/run", json=run_body(ids, add_festival="2025-08-20"))
    assert fest.status_code == 200
    empty = await owner_a.post(
        f"{API}/scenarios/run", json={"scope": {"location_id": ids["loc_S1"], "category": "nope"}, "changes": {}}
    )
    assert empty.status_code == 422 and empty.json()["code"] == "empty_scope"
    assert (await owner_a.post(f"{API}/scenarios/run", json=run_body(ids, price_change_pct=500))).status_code == 422


async def test_identical_requests_are_cached_and_fast(owner_a: Api, ready: None) -> None:
    ids = await build(owner_a)
    body = run_body(ids, price_change_pct=-10)
    t0 = time.perf_counter()
    a = (await owner_a.post(f"{API}/scenarios/run", json=body)).json()
    first = time.perf_counter() - t0
    t1 = time.perf_counter()
    b = (await owner_a.post(f"{API}/scenarios/run", json=body)).json()
    cached = time.perf_counter() - t1
    assert a == b and first < 3.0 and cached < 0.5, (first, cached)  # spec: first < 3 s, cached repeat fast


async def test_roles_store_scope_and_tenant_isolation(owner_a: Api, owner_b: Api, make_api, ready: None) -> None:  # type: ignore[no-untyped-def]
    ids = await build(owner_a)
    await owner_a.post(
        f"{API}/org/members",
        json={
            "email": "sm@a.com",
            "name": "SM",
            "password": PASSWORD,
            "role": "store_manager",
            "location_ids": [ids["loc_S1"]],
        },
    )
    await owner_a.post(
        f"{API}/org/members", json={"email": "vw@a.com", "name": "VW", "password": PASSWORD, "role": "viewer"}
    )
    sm, vw = await login_as(make_api, "sm@a.com"), await login_as(make_api, "vw@a.com")
    assert (await sm.post(f"{API}/scenarios/run", json=run_body(ids, "S1", price_change_pct=-5))).status_code == 200
    assert (await sm.post(f"{API}/scenarios/run", json=run_body(ids, "S2", price_change_pct=-5))).status_code == 403
    assert (await vw.post(f"{API}/scenarios/run", json=run_body(ids, price_change_pct=-5))).status_code == 403
    assert (
        await owner_b.post(f"{API}/scenarios/run", json=run_body(ids, price_change_pct=-5))
    ).status_code == 404  # B has no model


async def test_save_list_delete_scenarios_isolated(owner_a: Api, owner_b: Api, ready: None) -> None:
    ids = await build(owner_a)
    saved = (
        await owner_a.post(f"{API}/scenarios", json={"name": "Diwali promo", **run_body(ids, promo_depth_pct=20)})
    ).json()
    assert (
        saved["name"] == "Diwali promo" and saved["result"]["series"] == 2 and saved["changes"]["promo_depth_pct"] == 20
    )
    assert [s["id"] for s in (await owner_a.get(f"{API}/scenarios")).json()] == [saved["id"]]
    assert (await owner_b.get(f"{API}/scenarios")).json() == []
    assert (await owner_b.delete(f"{API}/scenarios/{saved['id']}")).status_code == 404
    assert (await owner_a.delete(f"{API}/scenarios/{saved['id']}")).status_code == 204
    assert (await owner_a.get(f"{API}/scenarios")).json() == []
