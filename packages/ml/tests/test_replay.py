import math

import numpy as np
import pandas as pd

from kalpa_ml.replay import CostConfig, SeriesParams, replay_fold, replay_series, unit_costs

# holding 1/unit/day and penalty 10/unit: unit_cost 100, holding 365%/yr -> 1.0/day, penalty ratio 0.1 -> 10
CFG = CostConfig(holding_pct_year=3.65, stockout_penalty_ratio=0.1)
P = SeriesParams(lead_days=1, review_days=1, pack=1, moq=1, unit_cost=100.0)


def test_unit_costs() -> None:
    assert unit_costs(CFG, P) == (1.0, 10.0)


def test_perfect_forecast_hand_simulated() -> None:
    """actual = p50 = 2 every day, start stock 4, L=1, R=1, no safety. By hand: holding 2 on day 0, then stock hits 0
    each day while a 2-unit order lands exactly on time -> no lost sales, total cost = 2 (day 0 end stock 2 x 1)."""
    r = replay_series(np.full(5, 2.0), np.full(5, 2.0), 4.0, P, CFG)
    assert r["cost"] == 2.0 and r["fulfilled"] == 10.0 and r["demand"] == 10.0 and r["stockout_days"] == 0


def test_forecast_of_zero_never_orders_and_loses_sales() -> None:
    """p50 = 0 -> never orders. Start 4 serves days 0-1; days 2-4 lose 2 each = 6 lost x 10 = 60; holding day 0 = 2."""
    r = replay_series(np.full(5, 2.0), np.zeros(5), 4.0, P, CFG)
    assert r["cost"] == 62.0 and r["fulfilled"] == 4.0 and r["stockout_days"] == 3
    assert math.isclose(r["fulfilled"] / r["demand"], 0.4)


def test_better_forecast_costs_less_than_a_bad_one_on_the_same_demand() -> None:
    rng = np.random.default_rng(0)
    actual = rng.poisson(10, 28).astype(float)
    good = replay_series(actual, np.full(28, 10.0), 40.0, P, CFG)
    low = replay_series(actual, np.full(28, 1.0), 40.0, P, CFG)  # badly under-forecasts
    assert good["cost"] < low["cost"] and good["fulfilled"] > low["fulfilled"]


def test_replay_is_deterministic() -> None:
    actual = np.arange(1, 29, dtype=float)
    a = replay_series(actual, actual * 0.9, 10.0, P, CFG)
    b = replay_series(actual, actual * 0.9, 10.0, P, CFG)
    assert a == b


def test_safety_stock_trades_holding_cost_for_fill_rate() -> None:
    rng = np.random.default_rng(1)
    actual = rng.poisson(10, 28).astype(float) + rng.integers(0, 12, 28)  # noisy demand around the forecast
    p50 = np.full(28, 10.0)
    table = {"__all__": {str(p): {str(t): 25.0 for t in (0.8, 0.9, 0.95, 0.975, 0.99)} for p in range(1, 29)}}
    base = replay_series(actual, p50, 20.0, P, CFG, None)
    safe = replay_series(actual, p50, 20.0, P, CFG, table)
    assert safe["fulfilled"] >= base["fulfilled"] and safe["inventory_days"] > base["inventory_days"]


def test_pack_and_moq_are_respected_in_replay() -> None:
    p = SeriesParams(lead_days=1, review_days=1, pack=6, moq=24, unit_cost=100.0)
    r = replay_series(np.full(10, 2.0), np.full(10, 2.0), 0.0, p, CFG)
    # nothing in stock on day 0 -> lost; first order of at least 24 lands day 1, so later days are fully served
    assert r["fulfilled"] == 18.0 and r["stockout_days"] == 1


def test_replay_fold_aggregates_series() -> None:
    rows = [
        {"unique_id": u, "ds": d, "y": 2.0, "p50": 2.0}
        for u in ("a", "b")
        for d in pd.date_range("2025-01-01", periods=5)
    ]
    out = replay_fold(pd.DataFrame(rows), {"a": P, "b": P}, {"a": 4.0, "b": 4.0}, CFG, None)
    assert out["cost"] == 4.0 and out["fill_rate"] == 1.0 and out["stockout_days"] == 0  # 2 series x 2.0
