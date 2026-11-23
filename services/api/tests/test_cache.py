"""Phase 9: Redis response cache (hit / miss / invalidation / isolation / fail-open) and the narrative 500 fix."""

from typing import Any

import pytest
from conftest import Api
from test_realtime import build, inline  # noqa: F401  (inline is a fixture, used through usefixtures)

from kalpa_api.core.cache import scope_key
from kalpa_api.modules.explain import service as explain_service

API = "/api/v1"


def test_scope_key_is_stable_and_distinguishes_scopes() -> None:
    assert scope_key(None) == "all"
    assert scope_key(["a", "b"]) == scope_key(["b", "a"]) != scope_key(["a"]) != "all"


@pytest.mark.usefixtures("inline")
async def test_second_read_is_a_hit_and_a_write_makes_the_next_read_fresh(owner_a: Api) -> None:
    await build(owner_a)
    first = await owner_a.get(f"{API}/forecasts/summary")
    second = await owner_a.get(f"{API}/forecasts/summary")
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("miss", "hit")
    assert first.json() == second.json() and first.json()["runs"] == 1
    assert (await owner_a.post(f"{API}/locations", json={"name": "New store", "type": "store"})).status_code == 201
    assert (await owner_a.get(f"{API}/forecasts/summary")).headers["x-cache"] == "miss"  # the write bumped the version
    assert (await owner_a.get(f"{API}/forecasts/summary")).headers["x-cache"] == "hit"


@pytest.mark.usefixtures("inline")
async def test_a_promote_is_visible_immediately_not_after_the_ttl(owner_a: Api) -> None:
    await build(owner_a, models=("seasonal_naive", "lightgbm_tweedie"))
    before = (await owner_a.get(f"{API}/models/arena")).json()
    assert not any(r["is_champion"] for r in before)
    assert (await owner_a.get(f"{API}/models/arena")).headers["x-cache"] == "hit"
    lgb = next(r for r in before if r["model_name"] == "lightgbm_tweedie")
    assert (await owner_a.post(f"{API}/models/runs/{lgb['id']}/promote")).status_code == 200
    after = await owner_a.get(f"{API}/models/arena")
    assert after.headers["x-cache"] == "miss"
    assert [r["model_name"] for r in after.json() if r["is_champion"]] == ["lightgbm_tweedie"]  # not the stale answer


@pytest.mark.usefixtures("inline")
async def test_organisations_never_share_cache_entries(owner_a: Api, owner_b: Api) -> None:
    await build(owner_a)
    assert (await owner_a.get(f"{API}/forecasts/summary")).json()["runs"] == 1
    assert (await owner_a.get(f"{API}/forecasts/summary")).headers["x-cache"] == "hit"
    b = await owner_b.get(f"{API}/forecasts/summary")
    assert b.headers["x-cache"] == "miss" and b.json() == {
        "runs": 0,
        "forecast_rows": 0,
    }  # B never sees A's cached answer


@pytest.mark.usefixtures("inline")
async def test_the_cache_fails_open_when_redis_is_down(owner_a: Api) -> None:
    await build(owner_a)
    app = owner_a.c._transport.app  # type: ignore[attr-defined]
    real = app.state.redis

    class Down:
        def __getattr__(self, _: str) -> Any:
            async def boom(*_: Any, **__: Any) -> None:
                raise ConnectionError("redis is down")

            return boom

    app.state.redis = Down()
    try:
        r = await owner_a.get(f"{API}/forecasts/summary")
        assert r.status_code == 200 and r.json()["runs"] == 1 and r.headers["x-cache"] == "miss"
    finally:
        app.state.redis = real


@pytest.mark.usefixtures("inline")
async def test_recommendations_and_narrative_are_cached_per_scope(owner_a: Api) -> None:
    ids = await build(owner_a)
    url = f"{API}/policy/recommendations"
    a, b = await owner_a.get(url), await owner_a.get(url)
    assert (a.headers["x-cache"], b.headers["x-cache"]) == ("miss", "hit") and a.json() == b.json()
    only = await owner_a.get(url, params={"only_orders": "false"})
    assert only.headers["x-cache"] == "miss"  # different parameters are different entries
    n = f"{API}/explain/{ids['S1']}/{ids['A']}/narrative"
    first, second = await owner_a.get(n), await owner_a.get(n)
    assert first.status_code == 200 and (first.headers["x-cache"], second.headers["x-cache"]) == ("miss", "hit")
    assert first.json() == second.json()


@pytest.mark.usefixtures("inline")
async def test_a_series_the_model_never_trained_on_gets_a_clean_404_and_the_narrative_still_works(
    owner_a: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (load test, 12 x 500): lgbm_shap raised 'ids weren't seen during training'."""
    ids = await build(owner_a, models=("seasonal_naive", "lightgbm_tweedie"))
    runs = (await owner_a.get(f"{API}/models/runs")).json()
    lgb = next(r for r in runs if r["model_name"] == "lightgbm_tweedie")
    assert (await owner_a.post(f"{API}/models/runs/{lgb['id']}/promote")).status_code == 200

    def unseen(*_: Any, **__: Any) -> Any:
        raise ValueError("The following ids weren't seen during training and thus can't be forecasted: {'S1|A'}")

    monkeypatch.setattr(explain_service, "lgbm_shap", unseen)
    r = await owner_a.get(f"{API}/forecasts/{ids['S1']}/{ids['A']}/explanation")
    assert r.status_code == 404 and r.json()["code"] == "no_explanation"
    n = await owner_a.get(f"{API}/explain/{ids['S1']}/{ids['A']}/narrative")
    assert n.status_code == 200 and n.json()["facts"]["drivers"] == []  # the sentence still explains the numbers


@pytest.mark.usefixtures("inline")
async def test_a_sale_refreshes_stock_answers_but_keeps_the_model_leaderboard_cached(owner_a: Api) -> None:
    ids = await build(owner_a)
    await owner_a.get(f"{API}/models/arena")
    await owner_a.get(f"{API}/policy/recommendations")
    assert [
        (await owner_a.get(u)).headers["x-cache"] for u in (f"{API}/models/arena", f"{API}/policy/recommendations")
    ] == ["hit", "hit"]
    sale = {"location_id": ids["S1"], "lines": [{"product_id": ids["A"], "qty": 1, "unit_price": "10.00"}]}
    assert (
        await owner_a.post(f"{API}/pos/checkout", json=sale, headers={"Idempotency-Key": "cache-test-sale-1"})
    ).status_code == 200
    assert (await owner_a.get(f"{API}/policy/recommendations")).headers["x-cache"] == "miss"  # stock changed
    assert (await owner_a.get(f"{API}/models/arena")).headers["x-cache"] == "hit"  # models did not


def test_which_writes_invalidate_which_group() -> None:
    from kalpa_api.core.cache import groups_for

    assert groups_for("/api/v1/pos/checkout") == groups_for("/api/v1/inventory/x") == ("stock",)
    assert groups_for("/api/v1/purchase-orders/1/receive") == ("stock",)
    assert groups_for("/api/v1/models/runs/1/promote") == ("models", "stock")
    assert groups_for("/api/v1/data/uploads") == ("models", "stock")
    assert groups_for("/api/v1/something-new") == ("models", "stock")  # unknown: invalidate everything, never stale
