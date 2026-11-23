"""Inventory policy engine (Part D6): service level, order-up-to level, pack/MOQ rounding, cover and stock-out date.

Protection period P = supplier lead time L + review period R.
Order-up-to  S = sum of P50 over P days + safety, where safety = the tau-quantile of backtest residual SUMS over
P days (a quantile of sums, not a sum of per-day quantiles: the P90 of a sum is not the sum of P90s).
Recommended order  q = ceil(max(0, S - IP) / pack) * pack, raised to the MOQ (as a pack multiple) if q > 0.
"""

import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

TAUS = (0.8, 0.9, 0.95, 0.975, 0.99)
MAX_P = 28


def critical_ratio(c_under: float, c_over: float) -> float:
    """Newsvendor critical ratio tau = c_u / (c_u + c_o)."""
    if c_under < 0 or c_over < 0 or c_under + c_over == 0:
        raise ValueError("costs must be non-negative and not both zero")
    return c_under / (c_under + c_over)


def service_level_from_costs(stockout_penalty: float, holding_per_unit_day: float, protection_days: int) -> float:
    """tau with c_u = penalty per unit short and c_o = holding cost per unit over the protection period."""
    return critical_ratio(stockout_penalty, holding_per_unit_day * max(protection_days, 1))


def inventory_position(on_hand: float, on_order: float, backorder: float) -> float:
    """IP = on-hand + on-order - backorders (negative when backorders exceed stock)."""
    return on_hand + on_order - backorder


def window_residual_sums(df: pd.DataFrame, p: int) -> np.ndarray:
    """Sums of (y - p50) over every full p-day window of every series. df needs unique_id, ds, y, p50."""
    out: list[np.ndarray] = []
    for _, g in df.sort_values("ds").groupby("unique_id"):
        r = (g["y"] - g["p50"]).to_numpy()
        if len(r) >= p:
            out.append(np.convolve(r, np.ones(p), mode="valid"))
    return np.concatenate(out) if out else np.array([])


def safety_table(
    df: pd.DataFrame, group_col: str | None, taus: tuple[float, ...] = TAUS, max_p: int = MAX_P
) -> dict[str, dict[str, dict[str, float]]]:
    """{group: {P: {tau: units}}} of residual-sum quantiles (>= 0). '__all__' pools every group (fallback)."""
    groups: dict[str, pd.DataFrame] = {"__all__": df}
    if group_col and group_col in df.columns:
        groups |= {str(g): d for g, d in df.groupby(group_col) if d["unique_id"].nunique() >= 5}
    table: dict[str, dict[str, dict[str, float]]] = {}
    for name, d in groups.items():
        table[name] = {}
        for p in range(1, max_p + 1):
            sums = window_residual_sums(d, p)
            table[name][str(p)] = {str(t): (max(0.0, float(np.quantile(sums, t))) if len(sums) else 0.0) for t in taus}
    return table


def lookup_safety(table: dict[str, Any], group: str | None, p: int, tau: float) -> float:
    """Safety units for P days at service level tau: the smallest tabulated tau >= requested (conservative)."""
    g = table.get(str(group)) or table["__all__"]
    row = g[str(min(max(int(p), 1), max(int(k) for k in g)))]
    levels = sorted(row, key=float)
    for k in levels:
        if float(k) >= tau - 1e-9:
            return float(row[k])
    return float(row[levels[-1]])


def order_up_to(p50_over_protection: float, safety: float) -> float:
    """S = demand forecast over the protection period + safety stock (negative safety is ignored)."""
    return p50_over_protection + max(safety, 0.0)


def recommend_qty(s: float, ip: float, pack: int, moq: int) -> int:
    """q = ceil(max(0, S - IP) / pack) * pack; if q > 0 it is at least the MOQ (as a pack multiple)."""
    pack, moq = max(int(pack), 1), max(int(moq), 1)
    need = max(0.0, s - ip)
    if need <= 1e-9:
        return 0
    q = math.ceil(need / pack) * pack
    return max(q, math.ceil(moq / pack) * pack)


def days_of_cover(on_hand: float, p50_next: np.ndarray) -> float:
    """on_hand / mean daily P50 over the given days (inf when no demand is forecast)."""
    mean = float(np.mean(p50_next)) if len(p50_next) else 0.0
    return math.inf if mean <= 0 else on_hand / mean


def stockout_date(on_hand: float, p50_daily: np.ndarray, start: date) -> date | None:
    """First day on which cumulative forecast demand reaches on-hand (None if it lasts the whole horizon)."""
    cum = np.cumsum(p50_daily)
    hit = np.nonzero(cum >= on_hand)[0]
    return start + timedelta(days=int(hit[0])) if len(hit) else None
