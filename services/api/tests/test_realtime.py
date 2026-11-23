"""Phase 8: live events, reorder alerts, simulator, jobs (progress/cancel), drift, registry, system metrics."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from conftest import PASSWORD, Api, login_as
from redis.asyncio import Redis
from sqlalchemy import func, select, update

from kalpa_api.core.config import get_settings
from kalpa_api.core.deps import Principal
from kalpa_api.core.errors import ApiError
from kalpa_api.core.events import alerts_key, channel, publish_event, sim_key, sim_stop_key
from kalpa_api.db.models import InventoryPosition, Job, ModelRun, Org, SalesDaily, User
from kalpa_api.db.session import make_sessionmaker
from kalpa_api.modules.forecasts import service as fc_service
from kalpa_api.modules.mlops import service as mlops
from kalpa_api.modules.simulator import router as sim_router
from kalpa_api.modules.simulator import service as sim
from kalpa_api.modules.stream import service as stream
from kalpa_api.modules.system import metrics

API = "/api/v1"
FIRST_DAY = date(2025, 1, 1)
DAYS = 130
DATA_END = FIRST_DAY + timedelta(days=DAYS - 1)
STORES = ("S1", "S2", "S3", "S4")
SKUS = ("A", "B", "C", "D", "E", "F")


def _csv() -> bytes:
    lines = ["store,sku,date,units"]
    for s in STORES:
        for sku in SKUS:
            for i in range(DAYS):
                d = FIRST_DAY + timedelta(days=i)
                units = 5 + (i % 7) * 2 + i // 30 + len(s) + ord(sku) % 5
                lines.append(f"{s},{sku},{d.isoformat()},{units}")
    return "\n".join(lines).encode()


@pytest.fixture
def inline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(get_settings(), "inline_jobs", True)
    monkeypatch.setattr(get_settings(), "model_dir", str(tmp_path / "models"))
    monkeypatch.setattr(get_settings(), "mlflow_db_uri", f"sqlite:///{tmp_path / 'mlflow.db'}")  # keep dev MLflow clean


async def build(
    owner: Api, on_hand: int = 60, backtest: bool = True, models: tuple[str, ...] = ("seasonal_naive",), folds: int = 2
) -> dict[str, str]:
    """24 series, supplier terms, stock, optional backtest (inline). Returns ids: store names and SKUs -> uuid."""
    up = (await owner.post(f"{API}/data/uploads", files={"file": ("x.csv", _csv(), "text/csv")})).json()
    assert (await owner.post(f"{API}/data/uploads/{up['upload_id']}/commit", json={})).status_code == 200
    locs = {
        i["name"]: i["id"] for i in (await owner.get(f"{API}/locations", params={"page_size": 100})).json()["items"]
    }
    prods = {i["sku"]: i["id"] for i in (await owner.get(f"{API}/products", params={"page_size": 100})).json()["items"]}
    sup = (await owner.post(f"{API}/suppliers", json={"name": "Sup", "default_lead_time_days": 3})).json()["id"]
    for sku in SKUS:
        terms = {"unit_cost": 100, "lead_time_days": 3, "moq": 12, "pack_size": 6}
        r = await owner.post(f"{API}/supplier-products", json={"supplier_id": sup, "product_id": prods[sku], **terms})
        assert r.status_code == 201, r.text
    for loc in STORES:
        for sku in SKUS:
            body = {"location_id": locs[loc], "product_id": prods[sku], "on_hand": on_hand}
            r = await owner.post(f"{API}/inventory", json=body)
            assert r.status_code == 201, r.text
    if backtest:
        r = await owner.post(f"{API}/models/backtests", json={"models": list(models), "folds": folds})
        assert r.status_code == 202 and r.json()["status"] == "succeeded", r.text
    return {"sup": sup, **locs, **prods}


async def me(owner: Api) -> dict[str, Any]:
    return dict((await owner.get(f"{API}/auth/me")).json())


async def team(owner: Api, make_api: Callable[[], Awaitable[Api]]) -> dict[str, Api]:
    for role in ("planner", "viewer"):
        body = {"email": f"{role}@a.com", "name": role, "password": PASSWORD, "role": role}
        assert (await owner.post(f"{API}/org/members", json=body)).status_code == 201
    return {"owner": owner, **{r: await login_as(make_api, f"{r}@a.com") for r in ("planner", "viewer")}}


async def drain(ps: Any, wait: float = 0.05) -> list[dict[str, Any]]:
    """All messages currently waiting on a pub/sub subscription."""
    out: list[dict[str, Any]] = []
    while True:
        m = await ps.get_message(ignore_subscribe_messages=True, timeout=wait)
        if m is None:
            return out
        out.append(json.loads(m["data"]))


async def subscribe(redis: Redis, org: str) -> Any:
    ps = redis.pubsub()
    await ps.subscribe(channel(uuid.UUID(org)))
    await ps.get_message(timeout=0.1)  # the subscribe confirmation
    return ps


# ------------------------------------------------------------------ jobs: progress, log, events, cancel
async def test_backtest_streams_progress_logs_and_sets_reorder_points(owner_a: Api, inline: None, redis: Redis) -> None:
    org = (await me(owner_a))["org_id"]
    ps = await subscribe(redis, org)
    await build(owner_a)
    events = [e for e in await drain(ps) if e["type"] == "job"]
    assert events[0]["status"] == "queued" and events[-1]["status"] == "succeeded" and events[-1]["progress"] == 1.0
    progress = [e["progress"] for e in events]
    assert progress == sorted(progress) and 0.05 in progress and 0.95 in progress  # monotone, with real checkpoints
    job = (await owner_a.get(f"{API}/jobs")).json()[0]
    detail = (await owner_a.get(f"{API}/jobs/{job['id']}")).json()
    assert job["status"] == "succeeded" and detail["started_at"] and detail["finished_at"]
    for needle in ("loaded 24 series", "backtesting", "reorder points updated for 24", "done"):
        assert needle in detail["log"], detail["log"]
    stock = (await owner_a.get(f"{API}/inventory")).json()["items"]
    assert len(stock) == 24 and all(i["reorder_point"] is not None and i["order_up_to"] for i in stock)
    assert all(i["order_up_to"] >= i["reorder_point"] for i in stock)  # S >= ROP: ordering refills above the trigger


async def test_cancel_a_queued_job_and_permissions(
    owner_a: Api, owner_b: Api, inline: None, make_api: Any, engine: Any
) -> None:
    maker = make_sessionmaker(engine)
    org = uuid.UUID((await me(owner_a))["org_id"])
    async with maker() as s:
        job = Job(org_id=org, type="backtest", status="queued", payload={})
        s.add(job)
        await s.commit()
        jid = job.id
    t = await team(owner_a, make_api)
    assert (await t["viewer"].post(f"{API}/jobs/{jid}/cancel")).status_code == 403
    assert (await owner_b.post(f"{API}/jobs/{jid}/cancel")).status_code == 404  # another org's job does not exist
    r = await t["planner"].post(f"{API}/jobs/{jid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and "Cancelled by planner" in r.json()["error"]
    again = await owner_a.post(f"{API}/jobs/{jid}/cancel")
    assert again.status_code == 409 and again.json()["code"] == "job_finished"


async def test_a_running_backtest_stops_at_its_next_checkpoint_when_cancelled(
    owner_a: Api, inline: None, engine: Any
) -> None:
    await build(owner_a, backtest=False)
    maker = make_sessionmaker(engine)
    org = uuid.UUID((await me(owner_a))["org_id"])
    async with maker() as s:
        job = Job(org_id=org, type="backtest", status="queued", payload={})
        s.add(job)
        await s.commit()
        jid = job.id
    seen: list[str] = []

    async def notify(ev: dict[str, Any]) -> None:
        seen.append(ev["line"])
        if ev["line"].startswith("loaded"):  # the user clicks Cancel while the job is running
            async with maker() as other:
                await other.execute(update(Job).where(Job.id == jid).values(status="cancelled", error="Cancelled by x"))
                await other.commit()

    async with maker() as s:
        res = await fc_service.execute_backtest(s, org, jid, ["seasonal_naive"], 1, notify=notify)
    assert res == {"cancelled": True}
    assert not any("backtest finished" in x for x in seen)  # it never got past the next checkpoint
    async with maker() as s:
        j = await s.get(Job, jid)
        assert j is not None and j.status == "cancelled"  # not overwritten by a failure or a success
        assert (await s.execute(select(func.count()).select_from(ModelRun))).scalar_one() == 0  # nothing persisted


# ------------------------------------------------------------------ reorder alerts + live state
async def test_crossing_the_reorder_point_alerts_exactly_once_and_live_state_follows(
    owner_a: Api, inline: None, redis: Redis
) -> None:
    ids = await build(owner_a)
    org = (await me(owner_a))["org_id"]
    item = next(
        i
        for i in (await owner_a.get(f"{API}/inventory", params={"location_id": ids["S1"]})).json()["items"]
        if i["product_id"] == ids["A"]
    )
    assert (
        await owner_a.patch(f"{API}/inventory/{item['id']}", json={"on_hand": 11, "reorder_point": 10})
    ).status_code == 200
    for other in (await owner_a.get(f"{API}/inventory", params={"page_size": 100})).json()["items"]:
        if other["id"] != item["id"]:  # park every other series far above its trigger so ours is the lowest headroom
            assert (await owner_a.patch(f"{API}/inventory/{other['id']}", json={"reorder_point": 0})).status_code == 200
    ps = await subscribe(redis, org)

    def sale(qty: int) -> dict[str, Any]:
        return {"location_id": ids["S1"], "lines": [{"product_id": ids["A"], "qty": qty, "unit_price": "20.00"}]}

    for n, qty in enumerate((2, 1), start=1):
        r = await owner_a.post(
            f"{API}/pos/checkout", json=sale(qty), headers={"Idempotency-Key": f"alert-test-{n}-key"}
        )
        assert r.status_code == 200, r.text
    ev = await drain(ps)
    sales = [e for e in ev if e["type"] == "sale"]
    alerts = [e for e in ev if e["type"] == "alert"]
    assert [s["units"] for s in sales] == [2, 1] and sales[0]["simulated"] is False
    assert sales[0]["stock"][0]["on_hand"] == 9 and sales[0]["stock"][0]["reorder_point"] == 10
    assert len(alerts) == 1  # 11 -> 9 crossed 10; 9 -> 8 was already below: no second alert
    a = alerts[0]
    assert (a["kind"], a["on_hand"], a["reorder_point"], a["sku"], a["location"]) == ("reorder", 9, 10, "A", "S1")

    st = (await owner_a.get(f"{API}/live/state")).json()
    assert st["units"] == 3 and st["revenue"] == pytest.approx(60.0)  # (2 + 1) x 20.00 taxable
    assert st["date"] == datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()  # the org's real date, not simulated
    assert len(st["feed"]) == 2 and st["feed"][0]["units"] == 1  # newest first
    assert len(st["alerts"]) == 1 and st["alerts"][0]["product_id"] == ids["A"]
    gap = [w["on_hand"] - w["reorder_point"] for w in st["watch"]]
    assert gap == sorted(gap) and len(st["watch"]) == 8  # lowest headroom first
    first = st["watch"][0]
    assert (first["location_id"], first["product_id"]) == (ids["S1"], ids["A"])  # the series 2 below its trigger
    assert (first["on_hand"], first["reorder_point"]) == (8, 10)
    assert st["sim"]["available"] is False and st["sim"]["running"] is False


async def test_stock_never_goes_negative_and_a_refused_sale_publishes_nothing(
    owner_a: Api, inline: None, redis: Redis
) -> None:
    ids = await build(owner_a, on_hand=2)
    ps = await subscribe(redis, (await me(owner_a))["org_id"])
    r = await owner_a.post(
        f"{API}/pos/checkout",
        json={"location_id": ids["S1"], "lines": [{"product_id": ids["A"], "qty": 3, "unit_price": "20.00"}]},
        headers={"Idempotency-Key": "too-many-key-1"},
    )
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    assert [e for e in await drain(ps) if e["type"] in ("sale", "alert")] == []


# ------------------------------------------------------------------ SSE relay (generator level; the HTTP path is exercised in the browser test)
def _principal(org: uuid.UUID, scope: list[str] | None = None) -> Principal:
    return Principal(user=User(id=uuid.uuid4()), org_id=org, role="owner", location_ids=scope)


async def test_event_stream_relays_org_events_filters_by_store_and_sends_keepalives(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, other_org, loc_ok, loc_no = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.0)
    gen = stream.event_stream(redis, _principal(org, [str(loc_ok)]), lambda: asyncio.sleep(0, result=False), 0.05)
    assert (await asyncio.wait_for(gen.__anext__(), 2)).startswith("retry: 2000")
    assert (await asyncio.wait_for(gen.__anext__(), 2)).startswith("event: hello")
    await publish_event(redis, other_org, {"type": "sale", "location_id": str(loc_ok), "units": 99})  # another org
    await publish_event(
        redis, org, {"type": "sale", "location_id": str(loc_no), "units": 1}
    )  # a store this user cannot see
    await publish_event(redis, org, {"type": "sale", "location_id": str(loc_ok), "units": 2})
    await publish_event(redis, org, {"type": "job", "status": "running", "progress": 0.5})  # org-wide: visible to all
    frames: list[str] = []
    while len([f for f in frames if f.startswith("event:")]) < 2:
        frames.append(await asyncio.wait_for(gen.__anext__(), 2))
    got = [(f.split("\n")[0], json.loads(f.split("data: ", 1)[1])) for f in frames if f.startswith("event:")]
    assert [g[0] for g in got] == ["event: sale", "event: job"]
    assert got[0][1]["units"] == 2  # not the other org's 99, not the store this user cannot see
    assert got[1][1]["progress"] == 0.5
    await gen.aclose()


async def test_event_stream_sends_keepalives_when_idle(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.0)
    gen = stream.event_stream(redis, _principal(uuid.uuid4()), lambda: asyncio.sleep(0, result=False), 0.02)
    frames = [await asyncio.wait_for(gen.__anext__(), 2) for _ in range(3)]
    assert (
        frames[2] == ": keepalive\n\n"
    )  # retry, hello, then a comment frame that keeps proxies from closing the stream
    await gen.aclose()


async def test_event_stream_ends_when_the_client_disconnects(redis: Redis) -> None:
    calls = {"n": 0}

    async def gone() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    frames = [f async for f in stream.event_stream(redis, _principal(uuid.uuid4()), gone, 0.01)]
    assert frames[0].startswith("retry:") and frames[1].startswith("event: hello") and len(frames) == 2


async def test_stream_endpoint_requires_a_login(make_api: Callable[[], Awaitable[Api]]) -> None:
    anon = await make_api()
    assert (await anon.get(f"{API}/stream/events")).status_code == 401
    assert (await anon.get(f"{API}/live/state")).status_code == 401


# ------------------------------------------------------------------ simulator
def test_traffic_profile_clock_and_baskets_conserve_units() -> None:
    w = sim.traffic_weights(240)
    assert len(w) == 240 and w.sum() == pytest.approx(1.0)
    assert w[100:120].sum() > w[0:20].sum() > 0  # the late-morning peak outweighs the opening hour
    assert (
        sim.clock_of(0, 240) == "08:00" and sim.clock_of(239, 240) == "21:56"
    )  # the start of the last 3.5-minute tick
    rng = np.random.default_rng(0)
    l1, l2 = uuid.uuid4(), uuid.uuid4()
    counts = np.array([3, 0, 5, 2])
    baskets = sim.make_baskets(counts, [l1, l1, l2, l2], rng)
    by_loc: dict[uuid.UUID, dict[int, int]] = {}
    for loc, lines in baskets:
        for i, q in lines.items():
            by_loc.setdefault(loc, {})[i] = by_loc.setdefault(loc, {}).get(i, 0) + q
    assert by_loc == {l1: {0: 3}, l2: {2: 5, 3: 2}}  # every unit sold once, in the right store
    big = sim.make_baskets(np.array([400, 400]), [l1, l2], rng, cap=40)
    assert sum(sum(b.values()) for _, b in big) == 800 and len(big) <= 40 + 2  # bounded work per tick


async def _run_sim(owner: Api, redis: Redis, engine: Any, **kw: Any) -> dict[str, Any]:
    m = await me(owner)
    return await sim.run_simulator(
        make_sessionmaker(engine), redis, uuid.UUID(m["org_id"]), uuid.UUID(m["user_id"]), None, tick_seconds=0.0, **kw
    )


async def test_simulator_sells_through_real_checkout_on_a_simulated_calendar(
    owner_a: Api, inline: None, redis: Redis, engine: Any
) -> None:
    await build(owner_a, on_hand=60)
    org = uuid.UUID((await me(owner_a))["org_id"])
    maker = make_sessionmaker(engine)
    async with maker() as s:
        before = (await s.execute(select(func.sum(InventoryPosition.on_hand)))).scalar_one()
    ps = await subscribe(redis, str(org))
    summary = await _run_sim(owner_a, redis, engine, day_seconds=20, max_ticks=30, seed=1)
    assert summary["running"] is False and summary["errors"] == 0 and summary["checkouts"] > 0 and summary["units"] > 0
    assert await redis.get(sim_key(org)) is None  # state key removed when it stops
    async with maker() as s:
        assert (await s.execute(select(func.min(InventoryPosition.on_hand)))).scalar_one() >= 0
        after = (await s.execute(select(func.sum(InventoryPosition.on_hand)))).scalar_one()
        dates = set((await s.execute(select(SalesDaily.date).where(SalesDaily.date > DATA_END).distinct())).scalars())
        units, revenue = (
            await s.execute(
                select(func.sum(SalesDaily.units), func.sum(SalesDaily.revenue)).where(SalesDaily.date > DATA_END)
            )
        ).one()
    assert dates == {DATA_END + timedelta(days=1), DATA_END + timedelta(days=2)}  # the days the forecast predicted
    assert date.today() not in dates  # never the real calendar day
    assert units == summary["units"] == before - after  # stock out == units sold == units recorded
    assert revenue == pytest.approx(summary["revenue"], abs=0.05)
    ev = await drain(ps)
    assert any(e["type"] == "sale" and e["simulated"] for e in ev)
    assert [e for e in ev if e["type"] == "sim"][-1]["running"] is False


async def test_simulator_counts_lost_sales_when_shelves_are_empty(
    owner_a: Api, inline: None, redis: Redis, engine: Any
) -> None:
    await build(owner_a, on_hand=1)
    summary = await _run_sim(owner_a, redis, engine, day_seconds=20, max_ticks=20, seed=2)
    assert summary["lost_units"] > 0 and summary["errors"] == 0
    async with make_sessionmaker(engine)() as s:
        assert (await s.execute(select(func.min(InventoryPosition.on_hand)))).scalar_one() == 0  # empty, never negative


async def test_simulator_needs_forecasts(owner_a: Api, inline: None, redis: Redis, engine: Any) -> None:
    await build(owner_a, backtest=False)
    with pytest.raises(ApiError, match="Run a backtest first"):
        await _run_sim(owner_a, redis, engine, day_seconds=20, max_ticks=5)


async def test_stop_flag_ends_a_running_simulator(owner_a: Api, inline: None, redis: Redis, engine: Any) -> None:
    await build(owner_a)
    org = uuid.UUID((await me(owner_a))["org_id"])

    async def stop_soon() -> None:
        await asyncio.sleep(0.4)
        await redis.set(sim_stop_key(org), "1")

    summary, _ = await asyncio.gather(
        _run_sim(owner_a, redis, engine, day_seconds=20, minutes=5, seed=3), stop_soon()
    )  # no tick budget: only the flag can end it
    assert summary["running"] is False and summary["day_index"] <= 1
    assert await redis.get(sim_key(org)) is None and await redis.exists(sim_stop_key(org)) == 0  # both keys cleaned up


async def test_simulator_endpoints_demo_gate_roles_and_lifecycle(
    owner_a: Api, inline: None, redis: Redis, engine: Any, make_api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = await team(owner_a, make_api)
    org = uuid.UUID((await me(owner_a))["org_id"])
    r = await owner_a.post(f"{API}/simulator/start", json={})
    assert r.status_code == 403 and r.json()["code"] == "demo_only"  # not the demo tenant
    assert (await t["viewer"].get(f"{API}/simulator/status")).json()["available"] is False
    async with make_sessionmaker(engine)() as s:
        o = await s.get(Org, org)
        assert o is not None
        o.is_demo = True
        await s.commit()
    r = await owner_a.post(f"{API}/simulator/start", json={})
    assert r.status_code == 404 and r.json()["code"] == "no_forecasts"  # it follows the forecast, so it needs one
    await build(owner_a)
    queued: list[tuple[Any, ...]] = []

    async def fake_enqueue(*a: Any) -> None:
        queued.append(a)

    monkeypatch.setattr(sim_router, "enqueue_simulator", fake_enqueue)
    assert (await t["viewer"].post(f"{API}/simulator/start", json={})).status_code == 403
    assert (await owner_a.post(f"{API}/simulator/start", json={"day_seconds": 5})).status_code == 422  # bounded inputs
    r = await t["planner"].post(f"{API}/simulator/start", json={"multiplier": 2.0, "day_seconds": 60})
    assert r.status_code == 202 and r.json()["running"] is True and len(queued) == 1
    jobs = (await owner_a.get(f"{API}/jobs")).json()
    assert jobs[0]["type"] == "simulator" and jobs[0]["payload"]["multiplier"] == 2.0
    await redis.set(
        sim_key(org), json.dumps({"sim_date": "2025-05-11", "clock": "10:00", "units": 5})
    )  # the worker's state
    st = (await t["viewer"].get(f"{API}/simulator/status")).json()
    assert st["running"] is True and st["sim_date"] == "2025-05-11" and st["available"] is True
    assert (await owner_a.post(f"{API}/simulator/start", json={})).status_code == 409  # already running
    assert (await t["viewer"].post(f"{API}/simulator/stop")).status_code == 403
    assert (await owner_a.post(f"{API}/simulator/stop")).status_code == 200
    assert await redis.exists(sim_stop_key(org)) == 1


# ------------------------------------------------------------------ drift monitor
async def _set_backtest_wape(engine: Any, org: uuid.UUID, value: float) -> None:
    async with make_sessionmaker(engine)() as s:
        for r in (await s.execute(select(ModelRun).where(ModelRun.org_id == org))).scalars():
            r.metrics = {**(r.metrics or {}), "wape": value}
        await s.commit()


async def _add_days(engine: Any, org: uuid.UUID, n: int, factor: float) -> None:
    """New sales after the training data: resampled from each series' own history, scaled by `factor`."""
    async with make_sessionmaker(engine)() as s:
        rows = (await s.execute(select(SalesDaily).where(SalesDaily.org_id == org))).scalars().all()
        by: dict[tuple[uuid.UUID, uuid.UUID], list[int]] = {}
        for r in sorted(rows, key=lambda r: r.date):
            by.setdefault((r.location_id, r.product_id), []).append(r.units)
        for k, (key, hist) in enumerate(by.items()):
            recent = hist[-90:]
            for d in range(n):
                u = round(factor * recent[(k * 13 + d * 11) % len(recent)])  # 11 is coprime with 7: all weekdays
                s.add(
                    SalesDaily(
                        org_id=org, location_id=key[0], product_id=key[1], date=DATA_END + timedelta(days=d + 1),
                        units=u, revenue=u * 10.0, avg_price=None, stockout_flag=False,
                    )
                )  # fmt: skip
        await s.commit()


async def test_drift_reports_why_it_cannot_judge_yet(owner_a: Api, inline: None, redis: Redis, engine: Any) -> None:
    org = uuid.UUID((await me(owner_a))["org_id"])
    maker = make_sessionmaker(engine)
    async with maker() as s:
        d = await mlops.compute_drift(s, redis, org)
    assert not d.enough_data and "No model" in str(d.message) and d.status == "unknown"
    await build(owner_a)
    async with maker() as s:
        d = await mlops.compute_drift(s, redis, org)
    assert "No complete day of sales after the training data" in str(d.message)
    await _add_days(engine, org, 2, 1.0)
    async with maker() as s:
        d = await mlops.compute_drift(s, redis, org)
    assert not d.enough_data and "at least 3 complete days" in str(d.message) and d.recent_days == 2


async def test_unchanged_demand_is_stable_and_doubled_demand_alerts_then_retrain_resolves_it(
    owner_a: Api, inline: None, redis: Redis, engine: Any
) -> None:
    await build(owner_a)
    org = uuid.UUID((await me(owner_a))["org_id"])
    maker = make_sessionmaker(engine)
    await _set_backtest_wape(engine, org, 0.9)  # isolate the PSI side of the check
    await _add_days(engine, org, 25, 1.0)  # 24 series x 25 days = 600 rows (PSI needs >= 500)
    async with maker() as s:
        d = await mlops.compute_drift(s, redis, org)
    units = next(p for p in d.psi if p.feature.startswith("units"))
    assert d.enough_data and d.recent_days == 25 and units.psi < 0.1  # same distribution, adequate sample: stable
    assert d.status == "ok" and d.alerts == [] and d.wape.status == "ok"

    async with maker() as s:  # now the same days again but with demand tripled (the forced drift)
        await s.execute(update(SalesDaily).where(SalesDaily.date > DATA_END).values(units=SalesDaily.units * 3))
        await s.commit()
    async with maker() as s:
        d = await mlops.compute_drift(s, redis, org)
        units = next(p for p in d.psi if p.feature.startswith("units"))
        assert units.psi > 0.2 and d.status == "alert" and any("PSI" in a for a in d.alerts)
        ps = await subscribe(redis, str(org))
        first = await mlops.check_drift(s, redis, org, auto_retrain=False)
        again = await mlops.check_drift(s, redis, org)  # same model version: already alerted, no second retrain
    assert first.alerted and first.retrain_job_id is None and not again.alerted and again.retrain_job_id is None
    alert = [e for e in await drain(ps) if e["type"] == "alert"]
    assert len(alert) == 1 and alert[0]["kind"] == "drift" and "PSI" in alert[0]["detail"]
    assert len(await redis.lrange(alerts_key(org), 0, -1)) == 1  # persisted for the bell

    r = await owner_a.post(f"{API}/models/retrain")  # "Retrain now"
    assert r.status_code == 202 and r.json()["type"] == "retrain" and r.json()["status"] == "succeeded"
    job = (await owner_a.get(f"{API}/jobs/{r.json()['id']}")).json()
    assert job["payload"] == {"models": ["seasonal_naive"], "folds": 2, "reason": "manual"}
    d2 = (await owner_a.get(f"{API}/models/drift")).json()
    assert d2["data_end"] > d.data_end.isoformat() and d2["enough_data"] is False  # retrained on the new days
    assert d2["last_retrain"]["status"] == "succeeded" and d2["last_retrain"]["reason"] == "manual"


async def test_live_wape_worse_than_backtest_by_over_twenty_percent_alerts(
    owner_a: Api, inline: None, redis: Redis, engine: Any
) -> None:
    await build(owner_a)
    org = uuid.UUID((await me(owner_a))["org_id"])
    await _set_backtest_wape(engine, org, 0.05)  # a very accurate backtest ...
    await _add_days(engine, org, 5, 1.0)  # ... then real demand that the forecast does not match
    async with make_sessionmaker(engine)() as s:
        d = await mlops.compute_drift(s, redis, org)
    assert d.wape.backtest == pytest.approx(0.05) and d.wape.live is not None and d.wape.ratio is not None
    assert d.wape.ratio > 1.2 and d.wape.status == "alert" and any("Live WAPE" in a for a in d.alerts)


async def test_drift_and_retrain_endpoints_roles_and_scheduler(
    owner_a: Api, owner_b: Api, inline: None, redis: Redis, engine: Any, make_api: Any
) -> None:
    t = await team(owner_a, make_api)
    assert (await t["viewer"].post(f"{API}/models/retrain")).status_code == 403
    assert (await t["viewer"].post(f"{API}/models/drift/check")).status_code == 403
    assert (await t["viewer"].get(f"{API}/models/drift")).status_code == 200
    assert (await owner_a.post(f"{API}/models/retrain")).json()["code"] == "no_forecasts"  # nothing to retrain yet
    await build(owner_a)
    maker = make_sessionmaker(engine)
    async with maker() as s:  # weekly cron body: A has models, B has none
        started = await mlops.scheduled_retrain_all(s, redis)
    assert len(started) == 1
    org = uuid.UUID((await me(owner_a))["org_id"])
    async with maker() as s:
        j = (await s.execute(select(Job).where(Job.id == started[0]))).scalar_one()
        assert (j.org_id, j.type, (j.payload or {})["reason"], j.status) == (org, "retrain", "scheduled", "succeeded")
        s.add(Job(org_id=org, type="backtest", status="running", payload={}))
        await s.commit()
        assert await mlops.scheduled_retrain_all(s, redis) == []  # already busy: skipped, not stacked
        assert (await mlops.drift_check_all(s, redis))["orgs"] == 1
    assert (await owner_b.get(f"{API}/models/drift")).json()["enough_data"] is False  # org B sees only its own state


def test_worker_schedules_abort_support_and_simulator_job() -> None:
    from kalpa_worker.main import WorkerSettings

    names = {c.name: c for c in WorkerSettings.cron_jobs}
    assert (
        names["cron:scheduled_retrain"].weekday == "mon" and names["cron:scheduled_retrain"].hour == 2
    )  # Monday 02:00
    assert names["cron:drift_check"].hour == 3
    assert WorkerSettings.allow_abort_jobs is True
    assert {f.__name__ for f in WorkerSettings.functions} == {"run_backtest_job", "run_simulator_job"}
    assert "02:00" in mlops.SCHEDULE_TEXT and "03:00" in mlops.SCHEDULE_TEXT  # the page shows the real schedule


# ------------------------------------------------------------------ registry + system metrics
async def test_registry_lists_versions_mlflow_links_and_champion_history(owner_a: Api, inline: None) -> None:
    await build(owner_a, models=("seasonal_naive", "lightgbm_tweedie"))
    reg = (await owner_a.get(f"{API}/models/registry")).json()
    assert [m["model_name"] for m in reg["models"]] == ["lightgbm_tweedie", "seasonal_naive"]
    lgb = reg["models"][0]["versions"][0]
    assert (
        lgb["mlflow_run_id"]
        and "/#/experiments/" in lgb["mlflow_url"]
        and lgb["mlflow_url"].endswith(lgb["mlflow_run_id"])
    )
    assert lgb["metrics"]["wape"] is not None and reg["champion_history"] == [] and "Mondays" in reg["schedule"]
    assert (await owner_a.post(f"{API}/models/runs/{lgb['id']}/promote")).status_code == 200
    reg = (await owner_a.get(f"{API}/models/registry")).json()
    assert reg["models"][0]["versions"][0]["is_champion"] is True
    ev = reg["champion_history"][0]
    assert (ev["by"], ev["model_name"], ev["run_id"]) == ("Owner A", "lightgbm_tweedie", lgb["id"])


def test_latency_summary_hand_checked() -> None:
    now = 10_000.0
    lines = [f"{int(now) - 60}|GET|/api/v1/x|200|{ms}" for ms in (10, 20, 30, 40, 100)]
    lines += [
        f"{int(now) - 30}|POST|/api/v1/y|500|50.0",
        f"{int(now) - 3600}|GET|/old|200|9999",
        "garbage",
        "1|GET|only|3",
    ]
    s = metrics.summarise(lines, now, window_minutes=30)
    assert s["total"] == 6  # the hour-old and the malformed lines are ignored
    assert s["error_rate"] == pytest.approx(0.1667) and s["per_min"] == pytest.approx(0.2)
    assert s["p50"] == 35.0 and s["p95"] == 87.5  # numpy linear interpolation over [10,20,30,40,50,100]: 50 + 0.75 x 50
    assert len(s["series"]) == 2 and sum(p.requests for p in s["series"]) == 6
    assert [r.route for r in s["slowest"]] == ["GET /api/v1/x"] and s["slowest"][
        0
    ].p95_ms == 88.0  # needs >= 3 requests


async def test_system_metrics_endpoint_reports_live_values(owner_a: Api, inline: None) -> None:
    for _ in range(4):
        assert (await owner_a.get(f"{API}/locations")).status_code == 200
    r = await owner_a.get(f"{API}/system/metrics")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["requests_total"] >= 4 and m["p50_ms"] is not None and m["p95_ms"] >= m["p50_ms"] and m["series"]
    assert m["error_rate"] == 0.0 and m["db_engine"] == "SQLite" and m["db_size_bytes"] > 0
    assert m["worker"] == "down" and m["queue_depth"] == 0  # no worker in the test app, queue empty
    assert (
        {"fastapi", "sqlalchemy"} <= set(m["versions"])
        and m["docs_url"] == "/docs"
        and m["mlflow_url"].startswith("http")
    )
    assert any(s["route"].startswith("GET /api/v1/locations") for s in m["slowest"])


async def test_system_metrics_need_a_login(make_api: Callable[[], Awaitable[Api]]) -> None:
    anon = await make_api()
    assert (await anon.get(f"{API}/system/metrics")).status_code == 401


async def test_psi_is_withheld_on_small_samples_instead_of_giving_false_alarms(
    owner_a: Api, inline: None, redis: Redis, engine: Any
) -> None:
    """Same-distribution data gave PSI 0.217 (> the 0.2 alert line) on 75 rows: small samples must not be judged by PSI."""
    await build(owner_a)
    org = uuid.UUID((await me(owner_a))["org_id"])
    await _set_backtest_wape(engine, org, 0.9)
    await _add_days(engine, org, 5, 1.0)  # 24 series x 5 days = 120 rows < 500
    async with make_sessionmaker(engine)() as s:
        d = await mlops.compute_drift(s, redis, org)
    assert d.enough_data and d.psi == [] and "PSI needs at least 500 new rows" in str(d.message)
    assert d.wape.status == "ok" and d.status == "ok"  # live WAPE is still judged


async def test_retrain_after_sparse_pos_style_sales_zero_fills_instead_of_failing(
    owner_a: Api, inline: None, redis: Redis, engine: Any
) -> None:
    """Regression (found end to end): POS and the simulator only write a row when something sold, which broke retraining
    with 'missing or duplicate timestamps'. The ingestion pipeline zero-fills; the training load must too."""
    await build(owner_a)
    org = uuid.UUID((await me(owner_a))["org_id"])
    async with make_sessionmaker(engine)() as s:
        base = (await s.execute(select(SalesDaily).where(SalesDaily.date == DATA_END))).scalars().all()
        for k, r in enumerate(base):
            for d in range(1, 7):
                if (k + d) % 3 == 0:  # no sale that day for this series: no row, exactly like checkout
                    continue
                s.add(SalesDaily(org_id=org, location_id=r.location_id, product_id=r.product_id,
                                 date=DATA_END + timedelta(days=d), units=3, revenue=30.0, avg_price=None, stockout_flag=False))  # fmt: skip
        await s.commit()
    r = await owner_a.post(f"{API}/models/retrain")
    assert r.status_code == 202 and r.json()["status"] == "succeeded", r.json().get("error")
    d = (await owner_a.get(f"{API}/models/drift")).json()
    assert d["data_end"] == (DATA_END + timedelta(days=6)).isoformat()  # trained through the new days
