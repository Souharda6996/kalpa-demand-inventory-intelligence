import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kalpa_ml.policy import (
    critical_ratio,
    days_of_cover,
    inventory_position,
    lookup_safety,
    order_up_to,
    recommend_qty,
    safety_table,
    service_level_from_costs,
    stockout_date,
    window_residual_sums,
)


def test_newsvendor_critical_ratio_hand_calculated() -> None:
    assert critical_ratio(3, 1) == 0.75 and critical_ratio(1, 1) == 0.5
    # penalty 6/unit, holding 0.1/unit/day over a 10-day protection period -> c_o = 1 -> tau = 6/7
    assert math.isclose(service_level_from_costs(6.0, 0.1, 10), 6 / 7)
    with pytest.raises(ValueError):
        critical_ratio(0, 0)


def test_moq_rounding_hand_calculated() -> None:
    assert recommend_qty(s=50, ip=37, pack=6, moq=24) == 24  # need 13 -> 18 by pack, raised to MOQ 24
    assert recommend_qty(s=50, ip=37, pack=6, moq=10) == 18  # MOQ 10 -> 12 (pack multiple) < 18
    assert recommend_qty(s=50, ip=20, pack=10, moq=1) == 30  # exact multiple stays


def test_negative_inventory_position_from_backorders() -> None:
    ip = inventory_position(on_hand=5, on_order=0, backorder=20)  # -15
    assert ip == -15 and recommend_qty(50, ip, pack=10, moq=1) == 70  # need 65 -> 70


def test_nothing_to_order_when_position_covers_target() -> None:
    assert recommend_qty(s=40, ip=40, pack=6, moq=24) == 0 and recommend_qty(s=40, ip=100, pack=6, moq=24) == 0


def test_zero_lead_time_protection_is_just_the_review_period() -> None:
    # L=0, R=7 -> P=7: S = 7 days of demand + safety
    assert order_up_to(7 * 10.0, 12.0) == 82.0 and order_up_to(70.0, -5.0) == 70.0  # negative safety is ignored


def test_days_of_cover_and_stockout_date() -> None:
    assert days_of_cover(100, np.array([10.0, 10.0, 10.0])) == 10.0
    assert days_of_cover(100, np.zeros(14)) == math.inf
    assert stockout_date(25, np.array([10.0] * 5), date(2025, 1, 1)) == date(2025, 1, 3)  # cum 10, 20, 30 >= 25
    assert stockout_date(1000, np.array([10.0] * 5), date(2025, 1, 1)) is None


def _resid_frame() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for s in range(40):
        for ds in pd.date_range("2025-01-01", periods=28):
            rows.append(
                {
                    "unique_id": f"S|{s}",
                    "ds": ds,
                    "y": 10 + rng.normal(0, 3),
                    "p50": 10.0,
                    "category": "a" if s < 20 else "b",
                }
            )
    return pd.DataFrame(rows)


def test_window_residual_sums_hand_calculated() -> None:
    df = pd.DataFrame(
        {"unique_id": "u", "ds": pd.date_range("2025-01-01", periods=4), "y": [3.0, 5.0, 4.0, 10.0], "p50": 4.0}
    )
    assert window_residual_sums(df, 2).tolist() == [0.0, 1.0, 6.0]  # (-1+1), (1+0), (0+6)


def test_safety_grows_with_service_level_and_protection_period() -> None:
    t = safety_table(_resid_frame(), "category")
    assert lookup_safety(t, "a", 7, 0.95) >= lookup_safety(t, "a", 7, 0.8) >= 0
    assert lookup_safety(t, "a", 14, 0.95) > lookup_safety(t, "a", 3, 0.95)  # longer P -> larger residual sums
    assert lookup_safety(t, "unknown", 7, 0.9) == lookup_safety(t, "__all__", 7, 0.9)  # unknown group -> pooled
    assert lookup_safety(t, "a", 7, 0.999) == lookup_safety(t, "a", 7, 0.99)  # above the table -> highest tau


@settings(max_examples=300, deadline=None)
@given(s=st.floats(0, 5000), ip=st.floats(-2000, 5000), pack=st.integers(1, 48), moq=st.integers(1, 500))
def test_recommend_qty_invariants(s: float, ip: float, pack: int, moq: int) -> None:
    q = recommend_qty(s, ip, pack, moq)
    assert isinstance(q, int) and q >= 0 and q % pack == 0  # whole packs only
    if q > 0:
        assert q >= moq and q >= s - ip - 1e-6  # respects MOQ and covers the gap
        assert q < max(s - ip, 0) + pack + math.ceil(moq / pack) * pack  # never wildly over
    if ip >= s:
        assert q == 0  # nothing to order when the position already covers the target


@settings(max_examples=200, deadline=None)
@given(
    s=st.floats(0, 3000),
    ip=st.floats(-500, 3000),
    delta=st.floats(0, 500),
    pack=st.integers(1, 24),
    moq=st.integers(1, 100),
)
def test_more_stock_never_increases_the_order(s: float, ip: float, delta: float, pack: int, moq: int) -> None:
    assert recommend_qty(s, ip + delta, pack, moq) <= recommend_qty(s, ip, pack, moq)


@settings(max_examples=150, deadline=None)
@given(c_u=st.floats(0.01, 1000), c_o=st.floats(0.01, 1000))
def test_critical_ratio_is_a_probability_and_monotone(c_u: float, c_o: float) -> None:
    r = critical_ratio(c_u, c_o)
    assert 0 < r < 1 and critical_ratio(c_u * 2, c_o) > r
