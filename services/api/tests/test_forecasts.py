from datetime import date, timedelta

import pytest
from conftest import PASSWORD, Api, login_as

from kalpa_api.core.config import get_settings

API = "/api/v1"


def _csv(days: int = 130) -> bytes:
    """3 series, weekly pattern + a small trend, deterministic."""
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


async def _load(api: Api) -> None:
    up = (await api.post(f"{API}/data/uploads", files={"file": ("x.csv", _csv(), "text/csv")})).json()
    assert (await api.post(f"{API}/data/uploads/{up['upload_id']}/commit", json={})).status_code == 200


async def test_backtest_job_persists_runs_results_and_forecasts(owner_a: Api, inline: None) -> None:
    await _load(owner_a)
    r = await owner_a.post(
        f"{API}/models/backtests", json={"models": ["seasonal_naive", "lightgbm_tweedie"], "folds": 1}
    )
    assert r.status_code == 202, r.text
    job = r.json()
    assert (
        job["status"] == "succeeded"
        and job["progress"] == 1.0
        and set(job["result"]["run_ids"]) == {"seasonal_naive", "lightgbm_tweedie"}
    )
    assert (await owner_a.get(f"{API}/jobs/{job['id']}")).json()["status"] == "succeeded"

    arena = (await owner_a.get(f"{API}/models/arena")).json()
    assert {a["model_name"] for a in arena} == {"seasonal_naive", "lightgbm_tweedie"}
    assert all({"wape", "mase", "bias", "pinball"} <= a["metrics"].keys() for a in arena)
    assert arena[0]["metrics"]["wape"] <= arena[1]["metrics"]["wape"]  # sorted best-first
    assert (await owner_a.get(f"{API}/models/runs")).json()

    locs = {i["name"]: i["id"] for i in (await owner_a.get(f"{API}/locations")).json()["items"]}
    prods = {i["sku"]: i["id"] for i in (await owner_a.get(f"{API}/products")).json()["items"]}
    s = (await owner_a.get(f"{API}/forecasts/series?location_id={locs['S1']}&product_id={prods['A']}")).json()
    assert len(s["history"]) == 120 and len(s["models"]) == 2
    for m in s["models"]:
        assert len(m["points"]) == 56  # 28 backtest-fold days + 28 true future days
        assert all(p["p10"] <= p["p50"] <= p["p90"] and p["p50"] >= 0 for p in m["points"])
    assert (
        max(p["date"] for p in s["models"][0]["points"]) > s["history"][-1]["date"]
    )  # forecasts run past the data end
    assert (await owner_a.get(f"{API}/forecasts/summary")).json()["forecast_rows"] == 3 * 56 * 2


async def test_backtest_requires_permission_and_valid_models(owner_a: Api, make_api, inline: None) -> None:  # type: ignore[no-untyped-def]
    await owner_a.post(
        f"{API}/org/members", json={"email": "v@a.com", "name": "V", "password": PASSWORD, "role": "viewer"}
    )
    viewer = await login_as(make_api, "v@a.com")
    assert (await viewer.post(f"{API}/models/backtests", json={})).status_code == 403
    r = await owner_a.post(f"{API}/models/backtests", json={"models": ["nonsense"]})
    assert r.status_code == 422 and r.json()["code"] == "unknown_model"


async def test_backtest_without_data_fails_the_job_cleanly(owner_a: Api, inline: None) -> None:
    with pytest.raises(ValueError, match="no sales data"):
        await owner_a.post(f"{API}/models/backtests", json={"models": ["seasonal_naive"], "folds": 1})
    jobs = (await owner_a.get(f"{API}/jobs")).json()
    assert jobs[0]["status"] == "failed" and "no sales data" in jobs[0]["error"]


async def test_models_and_forecasts_are_tenant_isolated(owner_a: Api, owner_b: Api, inline: None) -> None:
    await _load(owner_a)
    job = (await owner_a.post(f"{API}/models/backtests", json={"models": ["seasonal_naive"], "folds": 1})).json()
    assert (await owner_b.get(f"{API}/jobs/{job['id']}")).status_code == 404
    assert (await owner_b.get(f"{API}/models/arena")).json() == []
    assert (await owner_b.get(f"{API}/models/runs")).json() == []
    assert (await owner_b.get(f"{API}/forecasts/summary")).json() == {"runs": 0, "forecast_rows": 0}


async def test_public_demo_forecast_is_only_the_demo_tenant(owner_a: Api, make_api, inline: None, session) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from kalpa_api.db.models import Org

    anon = await make_api()
    assert (await anon.get(f"{API}/public/demo-forecast")).status_code == 404  # no demo tenant yet
    await _load(owner_a)
    await owner_a.post(f"{API}/models/backtests", json={"models": ["seasonal_naive"], "folds": 1})
    # Org A has forecasts but is NOT the demo tenant -> still not exposed.
    assert (await anon.get(f"{API}/public/demo-forecast")).status_code == 404
    org = (await session.execute(select(Org).where(Org.name == "Org A"))).scalar_one()
    org.slug = "bengaluru-freshmart"  # make it the demo tenant
    await session.commit()
    r = await anon.get(f"{API}/public/demo-forecast")
    assert r.status_code == 200
    body = r.json()
    assert (
        body["model"] == "seasonal_naive"
        and len(body["points"]) == 56
        and body["history"]
        and "Synthetic" in body["note"]
    )
    assert "org_id" not in r.text and "email" not in r.text  # nothing tenant-identifying


async def test_cancelled_job_is_marked_failed_not_left_running(
    owner_a: Api, session, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Regression: an ARQ timeout cancels the job; the row must end 'failed' with a message, never stay 'running'."""
    import asyncio

    from sqlalchemy import select

    from kalpa_api.db.models import Job, Org
    from kalpa_api.modules.forecasts import service

    await _load(owner_a)
    org_id = (await session.execute(select(Org.id).where(Org.name == "Org A"))).scalar_one()
    job = Job(org_id=org_id, type="backtest", status="queued", payload={})
    session.add(job)
    await session.commit()
    job_id = job.id  # read now: the service's rollback expires ORM attributes

    async def cancelled(*_a: object, **_k: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(service.asyncio, "to_thread", cancelled)
    with pytest.raises(asyncio.CancelledError):  # what the worker sees when ARQ's timeout fires
        await service.execute_backtest(session, org_id, job_id, ["seasonal_naive"], 1)
    session.expire_all()
    done = await session.get(Job, job_id)
    assert done is not None and done.status == "failed" and "cancelled" in (done.error or "").lower()


async def test_promote_champion_is_role_gated_exclusive_and_tenant_isolated(
    owner_a: Api, owner_b: Api, make_api, inline: None
) -> None:  # type: ignore[no-untyped-def]
    from conftest import PASSWORD, login_as

    await _load(owner_a)
    await owner_a.post(f"{API}/models/backtests", json={"models": ["seasonal_naive", "lightgbm_tweedie"], "folds": 1})
    runs = (await owner_a.get(f"{API}/models/arena")).json()
    first, second = runs[0]["id"], runs[1]["id"]
    assert (await owner_a.post(f"{API}/models/runs/{first}/promote")).json()["is_champion"] is True
    assert (await owner_a.post(f"{API}/models/runs/{second}/promote")).status_code == 200
    champs = [r["id"] for r in (await owner_a.get(f"{API}/models/runs")).json() if r["is_champion"]]
    assert champs == [second]  # exactly one champion; promoting another demotes the first
    assert (await owner_b.post(f"{API}/models/runs/{first}/promote")).status_code == 404  # other org
    await owner_a.post(
        f"{API}/org/members", json={"email": "v@a.com", "name": "V", "password": PASSWORD, "role": "viewer"}
    )
    viewer = await login_as(make_api, "v@a.com")
    assert (await viewer.post(f"{API}/models/runs/{first}/promote")).status_code == 403
    assert (await owner_a.post(f"{API}/models/backtests", json={"models": ["tft_nonsense"]})).json()[
        "code"
    ] == "unknown_model"
