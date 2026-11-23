"""Live store simulator: fast-forwarded operations that follow the forecast, sold through the REAL checkout.

Time is a simulated business calendar that starts the day after the last day of sales, so simulated days are exactly
the days the forecast predicted (live WAPE and drift then compare like with like). Each real second is one tick:
expected units of a series in a tick = P50(day) x multiplier x share of the day's traffic in that hour; the actual
count is Poisson. Stock can only go down through checkout (never below zero); refused sales are counted as lost.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kalpa_api.core.deps import Principal
from kalpa_api.core.errors import ApiError
from kalpa_api.core.events import publish_event, sim_key, sim_stop_key
from kalpa_api.db.models import Forecast, SalesDaily, User
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_api.modules.pos import service as pos
from kalpa_api.modules.pos.schemas import CheckoutIn, PosLineIn
from kalpa_api.modules.simulator.schemas import SimState

log = structlog.get_logger()
STATE_TTL = 15  # seconds; refreshed every tick, so a crashed simulator stops showing as running by itself
MAX_BASKETS_PER_TICK = 40
OPEN_HOUR = 8
HOUR_PROFILE = np.array([1, 2, 3, 4, 3, 3, 3, 3, 3, 4, 5, 5, 4, 2], dtype=float)  # 08:00-22:00 shop traffic shape
GST_PCT = 5
MAX_CONSECUTIVE_ERRORS = 50


class _Stop(Exception):
    """Internal: leave the loop (stop requested, time limit or tick budget reached)."""


@dataclass
class Plan:
    """Everything the loop needs, loaded once at start."""

    series: list[tuple[uuid.UUID, uuid.UUID]]  # (location_id, product_id)
    daily: np.ndarray  # [S, 28] P50 units per forecast day
    price: np.ndarray  # [S] unit price
    first_forecast_day: date
    start_date: date


def traffic_weights(ticks: int) -> np.ndarray:
    """Share of the day's demand per tick (sums to 1): the hourly profile spread over the open hours."""
    hour = np.minimum((np.arange(ticks) * len(HOUR_PROFILE)) // ticks, len(HOUR_PROFILE) - 1)
    w = HOUR_PROFILE[hour] / np.bincount(hour, minlength=len(HOUR_PROFILE))[hour]
    return w / w.sum()


def clock_of(tick: int, ticks: int) -> str:
    """Simulated wall-clock time (HH:MM) of a tick."""
    minutes = int(tick / ticks * len(HOUR_PROFILE) * 60)
    return f"{OPEN_HOUR + minutes // 60:02d}:{minutes % 60:02d}"


def make_baskets(
    counts: np.ndarray, loc_of: list[uuid.UUID], rng: np.random.Generator, cap: int = MAX_BASKETS_PER_TICK
) -> list[tuple[uuid.UUID, dict[int, int]]]:
    """Group this tick's units into baskets per store: [(location_id, {series_index: qty})]. Units are conserved."""
    by_loc: dict[uuid.UUID, list[int]] = {}
    for i in np.flatnonzero(counts):
        by_loc.setdefault(loc_of[int(i)], []).extend([int(i)] * int(counts[i]))
    total = sum(len(v) for v in by_loc.values())
    floor = max(1, -(-total // cap)) if total else 1  # bigger baskets when there is a lot to sell (bounded work)
    out: list[tuple[uuid.UUID, dict[int, int]]] = []
    for loc, items in by_loc.items():
        rng.shuffle(items)
        pos_ = 0
        while pos_ < len(items):
            size = max(floor, int(rng.integers(1, 6)))
            lines: dict[int, int] = {}
            for it in items[pos_ : pos_ + size]:
                lines[it] = lines.get(it, 0) + 1
            out.append((loc, lines))
            pos_ += size
    return out


async def load_plan(session: AsyncSession, org_id: uuid.UUID) -> Plan:
    """Per-series daily P50 from the champion (else best-WAPE) run, unit prices and the first simulated day."""
    runs = await _latest_runs(session, org_id)
    if not runs:
        raise ApiError(404, "no_forecasts", "Run a backtest first: the simulator follows the forecast")
    run = next((r for r in runs if r.is_champion), runs[0])
    data_end = date.fromisoformat(str((run.config or {}).get("data_end")))
    rows = (
        await session.execute(
            select(Forecast.location_id, Forecast.product_id, Forecast.target_date, Forecast.p50)
            .where(Forecast.org_id == org_id, Forecast.run_id == run.id, Forecast.target_date > data_end)
            .order_by(Forecast.location_id, Forecast.product_id, Forecast.target_date)
        )
    ).all()
    if not rows:
        raise ApiError(404, "no_forecasts", "The latest run has no future forecasts")
    by: dict[tuple[uuid.UUID, uuid.UUID], dict[int, float]] = {}
    for loc, prod, d, p50 in rows:
        by.setdefault((loc, prod), {})[(d - data_end).days - 1] = float(p50)
    series = sorted(by)
    daily = np.array([[by[k].get(j, 0.0) for j in range(28)] for k in series])
    prices = {
        (loc, prod): float(pr or 0.0)
        for loc, prod, pr in (
            await session.execute(
                select(SalesDaily.location_id, SalesDaily.product_id, SalesDaily.avg_price).where(
                    SalesDaily.org_id == org_id, SalesDaily.date == data_end, SalesDaily.avg_price.is_not(None)
                )
            )
        ).all()
    }
    last_sale = (
        await session.execute(select(func.max(SalesDaily.date)).where(SalesDaily.org_id == org_id))
    ).scalar_one()
    return Plan(
        series=series,
        daily=daily,
        price=np.array([prices.get(k, 100.0) for k in series]),
        first_forecast_day=data_end + timedelta(days=1),
        start_date=max(data_end, last_sale) + timedelta(days=1),
    )


async def read_state(redis: Redis, org_id: uuid.UUID, available: bool) -> SimState:
    """Current simulator state from Redis (not running when the key is gone)."""
    raw = await redis.get(sim_key(org_id))
    if raw:
        try:
            return SimState(**{**json.loads(raw), "available": available, "running": True})
        except ValueError:
            pass
    return SimState(available=available, running=False)


async def run_simulator(
    maker: async_sessionmaker[AsyncSession],
    redis: Redis,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    job_id: str | None,
    multiplier: float = 1.0,
    day_seconds: int = 240,
    minutes: int = 60,
    max_ticks: int | None = None,
    tick_seconds: float = 1.0,
    seed: int | None = None,
) -> dict[str, Any]:
    """Run until stopped, the time limit, or max_ticks (tests). Returns the final summary."""
    async with maker() as s:
        plan = await load_plan(s, org_id)
    rng = np.random.default_rng(seed)
    weights = traffic_weights(day_seconds)
    loc_of = [k[0] for k in plan.series]
    principal = Principal(user=User(id=user_id), org_id=org_id, role="owner", location_ids=None)
    st = SimState(
        available=True,
        running=True,
        job_id=job_id,
        sim_date=plan.start_date,
        clock=clock_of(0, day_seconds),
        multiplier=multiplier,
        day_seconds=day_seconds,
        started_at=datetime.now(UTC).isoformat(),
    )
    await redis.delete(sim_stop_key(org_id))
    deadline, errors_in_row, tick_no = time.monotonic() + minutes * 60, 0, 0
    try:
        while True:
            day = plan.start_date + timedelta(days=st.day_index)
            base = plan.daily[:, ((day - plan.first_forecast_day).days) % 28]
            for tick in range(day_seconds):
                if await redis.exists(sim_stop_key(org_id)) or time.monotonic() > deadline:
                    raise _Stop
                if max_ticks is not None and tick_no >= max_ticks:
                    raise _Stop
                t0 = time.monotonic()
                counts = rng.poisson(base * multiplier * weights[tick])
                for loc, lines in make_baskets(counts, loc_of, rng):
                    body = CheckoutIn(
                        location_id=loc,
                        lines=[
                            PosLineIn(
                                product_id=plan.series[i][1],
                                qty=q,
                                unit_price=round(float(plan.price[i]), 2),
                                gst_pct=GST_PCT,
                            )
                            for i, q in lines.items()
                        ],
                        payment_method="upi",
                    )
                    try:
                        async with maker() as s:
                            r = await pos.checkout(s, redis, principal, body, uuid.uuid4().hex, None, day, True)
                        st.checkouts += 1
                        st.units += sum(lines.values())
                        st.revenue = round(st.revenue + sum(ln.taxable for ln in r.lines), 2)
                        errors_in_row = 0
                    except ApiError as e:
                        if e.code == "insufficient_stock":  # real behaviour: the shelf is empty, the sale is lost
                            st.lost_units += sum(lines.values())
                        else:
                            st.errors, errors_in_row = st.errors + 1, errors_in_row + 1
                            log.warning("sim_checkout_refused", code=e.code)
                    except Exception as e:  # noqa: BLE001 - one bad checkout must not stop the day
                        st.errors, errors_in_row = st.errors + 1, errors_in_row + 1
                        log.warning("sim_checkout_failed", error=type(e).__name__)
                    if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(f"simulator stopped: {errors_in_row} checkouts failed in a row")
                st.sim_date, st.clock, tick_no = day, clock_of(tick, day_seconds), tick_no + 1
                payload = st.model_dump(mode="json")
                await redis.set(sim_key(org_id), json.dumps(payload), ex=STATE_TTL)
                await publish_event(redis, org_id, {"type": "sim", **payload})
                await asyncio.sleep(max(0.0, tick_seconds - (time.monotonic() - t0)))
            st.day_index += 1
    except _Stop:
        pass
    finally:
        st.running = False
        summary = st.model_dump(mode="json")
        await redis.delete(sim_key(org_id), sim_stop_key(org_id))
        await publish_event(redis, org_id, {"type": "sim", **summary})
    return summary
