from pathlib import Path
from typing import Any

import polars as pl
import pytest

from kalpa_ml.data import weather
from kalpa_ml.data.m5_loader import M5Subset, load_m5


def test_m5_loader_subset_and_melt(tmp_path: Path) -> None:
    pl.DataFrame(
        {
            "id": ["a", "b", "c"],
            "item_id": ["I1", "I2", "I3"],
            "dept_id": ["FOODS_3"] * 3,
            "cat_id": ["FOODS"] * 3,
            "store_id": ["CA_1"] * 3,
            "state_id": ["CA"] * 3,
            "d_1": [5, 1, 0],
            "d_2": [6, 2, 0],
        }
    ).write_csv(tmp_path / "sales_train_evaluation.csv")
    pl.DataFrame(
        {
            "date": ["2011-01-29", "2011-01-30"],
            "wm_yr_wk": [11101, 11101],
            "d": ["d_1", "d_2"],
            "event_name_1": [None, "SuperBowl"],
            "event_type_1": [None, "Sporting"],
        }
    ).write_csv(tmp_path / "calendar.csv")
    pl.DataFrame({"store_id": ["CA_1"], "item_id": ["I1"], "wm_yr_wk": [11101], "sell_price": [2.5]}).write_csv(
        tmp_path / "sell_prices.csv"
    )
    out = load_m5(tmp_path, M5Subset(dept="FOODS_3", top_n_skus=2))
    s = out["sales"]
    assert s["sku"].n_unique() == 2 and "I3" not in s["sku"].to_list()
    assert s.height == 4 and s.filter(pl.col("sku") == "I1")["price"].to_list() == [2.5, 2.5]
    assert out["calendar"].height == 1


def test_weather_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class R:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict[str, Any]:
            return {
                "daily": {
                    "time": ["2024-01-01"],
                    "temperature_2m_max": [30.0],
                    "temperature_2m_min": [20.0],
                    "precipitation_sum": [0.0],
                }
            }

    def fake_get(*a: Any, **k: Any) -> R:
        calls.append(1)
        return R()

    monkeypatch.setattr(weather.requests, "get", fake_get)
    a = weather.fetch_weather(12.9, 77.6, "2024-01-01", "2024-01-01", tmp_path)
    b = weather.fetch_weather(12.9, 77.6, "2024-01-01", "2024-01-01", tmp_path)
    assert a.equals(b) and len(calls) == 1 and a["temp_max"][0] == 30.0
