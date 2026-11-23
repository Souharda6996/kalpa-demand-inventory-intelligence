from datetime import date

import numpy as np
import pandas as pd
import pytest

from kalpa_ml.backtest import forecast_future
from kalpa_ml.data.synth import load_festivals
from kalpa_ml.features import add_covariates
from kalpa_ml.scenario import apply_changes, compare, load_model

FEST = load_festivals()["date"].to_pandas()


@pytest.fixture(scope="module")
def priced() -> pd.DataFrame:
    """Demand with a STRONG, learnable price effect: y ~ 12 * price_ratio^-2 (+ noise); prices move +/-30 % weekly."""
    rng = np.random.default_rng(0)
    rows = []
    days = pd.date_range("2024-01-01", periods=330)
    for s in range(6):
        level = 100.0
        for i, d in enumerate(days):
            if i % 7 == 0:
                level = 100.0 * float(rng.uniform(0.7, 1.3))
            ratio = level / 100.0
            rows.append(
                {
                    "unique_id": f"S{s % 2}|K{s}",
                    "ds": d,
                    "price": level,
                    "y": float(rng.poisson(12 * ratio**-2)),
                    "category": "c",
                }
            )
    return add_covariates(pd.DataFrame(rows), FEST)


def test_apply_changes_hand_checked(priced: pd.DataFrame) -> None:
    x = priced.tail(10).copy()
    out = apply_changes(x, FEST, price_change_pct=10, promo_depth_pct=20)
    assert np.allclose(out["price_ratio"], x["price_ratio"] * 1.1 * 0.8)  # (1+0.10)*(1-0.20)
    assert x["price_ratio"].equals(priced.tail(10)["price_ratio"])  # the input frame is not modified
    out2 = apply_changes(x, FEST, add_festival=date(2024, 11, 20))
    assert (out2["days_to_festival"] <= x["days_to_festival"]).all()  # adding a festival can only bring one closer


def test_price_cut_raises_and_price_rise_lowers_the_forecast_and_model_roundtrips(
    priced: pd.DataFrame, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    h = 14
    out, as_of = forecast_future(priced, FEST, models=["lightgbm_tweedie"], h=h, save_dir=str(tmp_path / "mlf"))
    assert len(out) == priced["unique_id"].nunique() * h
    model = load_model(tmp_path / "mlf")  # the saved model reloads and predicts without retraining
    hist = priced[priced["ds"] <= as_of]
    from kalpa_ml.backtest import future_frame

    base_cov = add_covariates(
        pd.concat([hist[["unique_id", "ds", "price"]], future_frame(hist, as_of, h)], ignore_index=True), FEST
    )
    x = base_cov[base_cov["ds"] > as_of]
    ids = sorted(x["unique_id"].unique())
    base = compare(model, x, FEST, ids, h)["baseline"]["p50"].sum()
    cut = compare(model, x, FEST, ids, h, price_change_pct=-20)["scenario"]["p50"].sum()
    rise = compare(model, x, FEST, ids, h, price_change_pct=+20)["scenario"]["p50"].sum()
    assert cut > base * 1.05, (base, cut)  # cheaper -> clearly more units
    assert rise < base * 0.95, (base, rise)  # dearer -> clearly fewer units
    again = compare(model, x, FEST, ids, h, price_change_pct=-20)["scenario"]["p50"].sum()
    assert again == cut  # deterministic
