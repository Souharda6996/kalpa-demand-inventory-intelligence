"""Replenishment maths shared by the recommendations endpoint, the reorder-point refresh and the copilot."""

import math
import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, cast

import numpy as np
from pydantic import BaseModel
from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.core.errors import ApiError
from kalpa_api.db.models import CalendarEvent, Forecast, InventoryPosition, Location, Product, Supplier
from kalpa_api.modules.forecasts.router import _latest_runs
from kalpa_api.modules.policy.inputs import org_cost, supplier_terms
from kalpa_ml.policy import (
    days_of_cover,
    inventory_position,
    lookup_safety,
    order_up_to,
    recommend_qty,
    service_level_from_costs,
    stockout_date,
)

HORIZON = 28


class Recommendation(BaseModel):
    location_id: uuid.UUID
    location: str
    product_id: uuid.UUID
    sku: str
    product: str
    category: str
    supplier_id: uuid.UUID
    supplier: str
    lead_days: int
    review_days: int
    pack_size: int
    moq: int
    unit_cost: float
    on_hand: int
    on_order: int
    backorder: int
    inventory_position: float
    service_level: float
    safety_units: float
    order_up_to: float
    recommended_qty: int
    days_of_cover: float | None  # None = no forecast demand
    stockout_date: date | None
    value_at_risk: float  # shortfall over the lead time x unit cost (at cost, not selling price)
    over_capacity: bool
    lead_demand: float  # forecast units over the supplier lead time (P50)
    reorder_point: int  # lead_demand + safety stock: crossing it means "order now"
    reasons: list[str]


class RecommendationSet(BaseModel):
    run_id: uuid.UUID
    model: str
    is_champion: bool
    as_of: date
    items: list[Recommendation]


async def compute_recommendations(
    session: AsyncSession,
    org_id: uuid.UUID,
    location_ids: list[str] | None,
    location_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    only_orders: bool = True,
) -> RecommendationSet:
    """Order recommendations from the champion run (else the best-WAPE run) for every stocked, supplied product."""
    runs = await _latest_runs(session, org_id)
    if not runs:
        raise ApiError(404, "no_forecasts", "Run a backtest first: recommendations need forecasts")
    run = next((r for r in runs if r.is_champion), runs[0])
    cfg = run.config or {}
    as_of = date.fromisoformat(str(cfg.get("data_end")))
    safety_table: dict[str, Any] = cast("dict[str, Any]", cfg.get("safety") or {"__all__": {}})

    fc = (
        await session.execute(
            select(Forecast.location_id, Forecast.product_id, Forecast.target_date, Forecast.p50)
            .where(Forecast.org_id == org_id, Forecast.run_id == run.id, Forecast.target_date > as_of)
            .order_by(Forecast.target_date)
        )
    ).all()
    demand: dict[tuple[uuid.UUID, uuid.UUID], list[float]] = defaultdict(list)
    for loc, prod, _, p50 in fc:
        demand[(loc, prod)].append(float(p50))

    q = select(InventoryPosition).where(InventoryPosition.org_id == org_id)
    if location_id:
        q = q.where(InventoryPosition.location_id == location_id)
    if location_ids is not None:
        q = q.where(InventoryPosition.location_id.in_([uuid.UUID(x) for x in location_ids]))
    positions = (await session.execute(q)).scalars().all()

    terms = await supplier_terms(session, org_id)
    cost = await org_cost(session, org_id)
    locs = {x.id: x for x in (await session.execute(select(Location).where(Location.org_id == org_id))).scalars()}
    prods = {x.id: x for x in (await session.execute(select(Product).where(Product.org_id == org_id))).scalars()}
    sups = {x.id: x.name for x in (await session.execute(select(Supplier).where(Supplier.org_id == org_id))).scalars()}
    festivals = (
        await session.execute(
            select(CalendarEvent.date, CalendarEvent.name)
            .where(
                CalendarEvent.org_id == org_id,
                CalendarEvent.type == "festival",
                CalendarEvent.date > as_of,
                CalendarEvent.date <= as_of + timedelta(days=30),
            )
            .order_by(CalendarEvent.date)
        )
    ).all()

    items: list[Recommendation] = []
    for pos in positions:
        t, arr = terms.get(pos.product_id), demand.get((pos.location_id, pos.product_id))
        if t is None or not arr or (supplier_id and t.supplier_id != supplier_id):
            continue
        d = np.array(arr)
        review = int(cost["review_days"])
        horizon_p = min(t.lead_days + review, HORIZON)
        penalty = cost["stockout_penalty_ratio"] * t.unit_cost
        holding = cost["holding_pct_year"] * t.unit_cost / 365.0
        tau = service_level_from_costs(penalty, holding, horizon_p)
        category = prods[pos.product_id].category
        safety = lookup_safety(safety_table, category, horizon_p, tau) if len(safety_table) > 1 else 0.0
        s = order_up_to(float(d[:horizon_p].sum()), safety)
        ip = inventory_position(pos.on_hand, pos.on_order, pos.backorder)
        qty = recommend_qty(s, ip, t.pack, t.moq)
        cover = days_of_cover(pos.on_hand, d[:14])
        so = stockout_date(pos.on_hand, d, as_of + timedelta(days=1))
        lead_demand = float(d[: t.lead_days].sum())
        short = max(0.0, lead_demand - (pos.on_hand + pos.on_order))
        reasons = [f"lead time {t.lead_days} d"]
        if festivals:
            fd, fname = festivals[0]
            reasons.append(f"{fname} in {(fd - as_of).days} days")
        if cover != float("inf") and cover < t.lead_days:
            reasons.append(f"cover {cover:.0f} d is below the lead time")
        if qty and qty > max(s - ip, 0) + 1e-6:
            reasons.append(f"rounded up to packs of {t.pack}" + (f" / MOQ {t.moq}" if qty >= t.moq else ""))
        items.append(
            Recommendation(
                location_id=pos.location_id,
                location=locs[pos.location_id].name,
                product_id=pos.product_id,
                sku=prods[pos.product_id].sku,
                product=prods[pos.product_id].name,
                category=category,
                supplier_id=t.supplier_id,
                supplier=sups.get(t.supplier_id, "?"),
                lead_days=t.lead_days,
                review_days=review,
                pack_size=t.pack,
                moq=t.moq,
                unit_cost=t.unit_cost,
                on_hand=pos.on_hand,
                on_order=pos.on_order,
                backorder=pos.backorder,
                inventory_position=ip,
                service_level=round(tau, 4),
                safety_units=round(safety, 2),
                order_up_to=round(s, 2),
                recommended_qty=qty,
                days_of_cover=None if cover == float("inf") else round(cover, 1),
                stockout_date=so,
                value_at_risk=round(short * t.unit_cost, 2),
                over_capacity=False,
                lead_demand=round(lead_demand, 2),
                reorder_point=math.ceil(lead_demand + safety),
                reasons=reasons,
            )
        )
    # location shelf capacity: current stock + on order + everything recommended must fit
    load: dict[uuid.UUID, float] = defaultdict(float)
    for pos in positions:
        load[pos.location_id] += pos.on_hand + pos.on_order
    for it in items:
        load[it.location_id] += it.recommended_qty
    for it in items:
        cap = locs[it.location_id].capacity_units
        it.over_capacity = bool(cap and load[it.location_id] > cap)
    if only_orders:
        items = [i for i in items if i.recommended_qty > 0]
    items.sort(key=lambda i: (-i.value_at_risk, i.sku))
    return RecommendationSet(run_id=run.id, model=run.model_name, is_champion=run.is_champion, as_of=as_of, items=items)


async def refresh_reorder_points(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Store reorder_point / order_up_to on every stocked, supplied position (used by the live reorder alerts).

    Returns how many positions were updated; 0 when there are no forecasts or supplier terms yet.
    """
    try:
        rs = await compute_recommendations(session, org_id, None, only_orders=False)
    except ApiError:  # no forecasts yet
        return 0
    if not rs.items:
        return 0
    # Core statement: a parameter list on the ORM entity would mean bulk-update-by-primary-key
    t = cast(Table, InventoryPosition.__table__)
    stmt = (
        update(t)
        .where(
            t.c.org_id == org_id,
            t.c.location_id == bindparam("b_loc"),
            t.c.product_id == bindparam("b_prod"),
        )
        .values(reorder_point=bindparam("b_rop"), order_up_to=bindparam("b_s"))
    )
    await session.execute(
        stmt,
        [
            {"b_loc": i.location_id, "b_prod": i.product_id, "b_rop": i.reorder_point, "b_s": math.ceil(i.order_up_to)}
            for i in rs.items
        ],
    )
    await session.commit()
    return len(rs.items)
