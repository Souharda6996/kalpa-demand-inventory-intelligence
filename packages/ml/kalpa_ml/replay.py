"""Inventory replay simulator: decision-focused evaluation of a forecaster.

For each series the policy (policy.py) is replayed day by day over a held-out window using ONLY forecasts made before
the window and the real demand as it happened. Orders arrive after the supplier lead time; unmet demand is lost.
    cost = sum_days ( holding_per_unit_day * end_of_day_inventory + penalty_per_unit * lost_units )
    fill rate = fulfilled units / demanded units
Deterministic: identical inputs always give identical results (no random draws).
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from kalpa_ml.policy import lookup_safety, order_up_to, recommend_qty, service_level_from_costs


@dataclass(frozen=True)
class CostConfig:
    """Cost assumptions (documented, configurable per organisation)."""

    holding_pct_year: float = 0.25  # holding cost = this share of unit cost per year
    stockout_penalty_ratio: float = 0.5  # penalty per unit lost = this x unit cost (proxy for lost margin)


@dataclass(frozen=True)
class SeriesParams:
    lead_days: int
    review_days: int
    pack: int
    moq: int
    unit_cost: float
    category: str | None = None


def unit_costs(cfg: CostConfig, p: SeriesParams) -> tuple[float, float]:
    """(holding per unit per day, penalty per unit lost) for one series."""
    return cfg.holding_pct_year * p.unit_cost / 365.0, cfg.stockout_penalty_ratio * p.unit_cost


def replay_series(
    actual: np.ndarray,
    p50: np.ndarray,
    start_stock: float,
    p: SeriesParams,
    cfg: CostConfig,
    safety_table: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Replay one series. Returns cost, fulfilled, demand, stockout_days, inventory_days (sum of end-of-day stock)."""
    H = len(actual)
    h_cost, penalty = unit_costs(cfg, p)
    P = p.lead_days + p.review_days
    tau = service_level_from_costs(penalty, h_cost, P)
    on_hand, arrivals = float(start_stock), np.zeros(H + p.lead_days + 2)
    cost = fulfilled = demand = inv_days = 0.0
    stockout_days = 0
    for t in range(H):
        on_hand += arrivals[t]
        if t % max(p.review_days, 1) == 0:  # review: order up to S
            pipeline = float(arrivals[t + 1 :].sum())
            window = p50[t : t + P]
            safety = lookup_safety(safety_table, p.category, P, tau) if safety_table else 0.0
            s = order_up_to(float(window.sum()), safety)
            q = recommend_qty(s, on_hand + pipeline, p.pack, p.moq)
            if q > 0:
                arrivals[t + p.lead_days] += q if p.lead_days > 0 else 0.0
                if p.lead_days == 0:
                    on_hand += q
        d = float(actual[t])
        sold = min(on_hand, d)
        on_hand -= sold
        lost = d - sold
        fulfilled, demand = fulfilled + sold, demand + d
        stockout_days += int(lost > 1e-9)
        inv_days += on_hand
        cost += h_cost * on_hand + penalty * lost
    return {
        "cost": cost,
        "fulfilled": fulfilled,
        "demand": demand,
        "stockout_days": float(stockout_days),
        "inventory_days": inv_days,
    }


def replay_fold(
    scored: pd.DataFrame,
    params: dict[str, SeriesParams],
    start_stock: dict[str, float],
    cfg: CostConfig,
    safety_table: dict[str, Any] | None,
) -> dict[str, float]:
    """Aggregate replay over every series of a fold. scored needs unique_id, ds, y, p50."""
    tot = {"cost": 0.0, "fulfilled": 0.0, "demand": 0.0, "stockout_days": 0.0, "inventory_days": 0.0}
    days = 0
    for uid, g in scored.sort_values("ds").groupby("unique_id"):
        if uid not in params:
            continue
        r = replay_series(
            g["y"].to_numpy(float),
            g["p50"].to_numpy(float),
            start_stock.get(str(uid), 0.0),
            params[str(uid)],
            cfg,
            safety_table,
        )
        for k in tot:
            tot[k] += r[k]
        days = len(g)
    n = scored["unique_id"].nunique()
    return {
        "cost": tot["cost"],
        "fill_rate": tot["fulfilled"] / tot["demand"] if tot["demand"] > 0 else float("nan"),
        "stockout_days": tot["stockout_days"],
        "avg_inventory": tot["inventory_days"] / max(n * max(days, 1), 1),
    }
