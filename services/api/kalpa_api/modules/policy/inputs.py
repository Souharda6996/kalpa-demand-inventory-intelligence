"""Decision-engine inputs from the database: supplier terms per product and the organisation's cost assumptions."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kalpa_api.db.models import Org, SupplierProduct
from kalpa_ml.replay import CostConfig, SeriesParams

DEFAULT_COST: dict[str, float] = {"holding_pct_year": 0.25, "stockout_penalty_ratio": 0.5, "review_days": 7.0}


@dataclass(frozen=True)
class Terms:
    supplier_id: uuid.UUID
    lead_days: int
    moq: int
    pack: int
    unit_cost: float


async def org_cost(session: AsyncSession, org_id: uuid.UUID) -> dict[str, float]:
    """The org's cost assumptions merged over the documented defaults."""
    org = (await session.execute(select(Org).where(Org.id == org_id))).scalar_one()
    return {**DEFAULT_COST, **(org.cost_params or {})}


def cost_config(c: dict[str, float]) -> CostConfig:
    return CostConfig(holding_pct_year=c["holding_pct_year"], stockout_penalty_ratio=c["stockout_penalty_ratio"])


async def supplier_terms(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, Terms]:
    """Primary supplier per product: the shortest lead time, ties broken by the lowest unit cost."""
    rows = (await session.execute(select(SupplierProduct).where(SupplierProduct.org_id == org_id))).scalars().all()
    best: dict[uuid.UUID, SupplierProduct] = {}
    for sp in rows:
        cur = best.get(sp.product_id)
        if cur is None or (sp.lead_time_days, sp.unit_cost) < (cur.lead_time_days, cur.unit_cost):
            best[sp.product_id] = sp
    return {
        pid: Terms(sp.supplier_id, sp.lead_time_days, sp.moq, sp.pack_size, sp.unit_cost) for pid, sp in best.items()
    }


def series_params(
    ids: dict[str, tuple[uuid.UUID, uuid.UUID]],
    terms: dict[uuid.UUID, Terms],
    categories: dict[str, str],
    review_days: int,
) -> dict[str, SeriesParams]:
    """unique_id -> SeriesParams for every series whose product has a supplier (others are not costed)."""
    out: dict[str, SeriesParams] = {}
    for uid, (_, pid) in ids.items():
        t = terms.get(pid)
        if t:
            out[uid] = SeriesParams(t.lead_days, review_days, t.pack, t.moq, t.unit_cost, categories.get(uid))
    return out


def assumptions(c: dict[str, float]) -> dict[str, Any]:
    return {k: c[k] for k in ("holding_pct_year", "stockout_penalty_ratio", "review_days")}
