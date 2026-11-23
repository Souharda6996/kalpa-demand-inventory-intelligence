import numpy as np
import pandas as pd
import polars as pl
import pytest

from kalpa_ml.backtest import make_folds, run_backtest, summarise
from kalpa_ml.data.synth import SynthConfig, generate, load_festivals
from kalpa_ml.features import add_covariates, calendar_features, festival_features, price_ratio
from kalpa_ml.frames import to_long


def test_festival_days_to_event_and_window() -> None:
    ds = pd.Series(pd.to_datetime(["2024-10-24", "2024-10-31", "2024-11-01", "2024-01-01"]))
    f = festival_features(ds, pd.Series(pd.to_datetime(["2024-10-31"])))
    assert f["days_to_festival"].tolist() == [7.0, 0.0, 30.0, 30.0]  # day after / no festival soon -> capped
    assert f["festival_window"].tolist() == [1, 1, 0, 0]


def test_calendar_features_are_cyclic() -> None:
    ds = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-08"]))  # both Mondays
    c = calendar_features(ds)
    assert np.isclose(c["dow_sin"][0], c["dow_sin"][1]) and np.isclose(c["dow_cos"][0], 1.0)


def test_price_ratio_never_uses_todays_price() -> None:
    df = pd.DataFrame({"unique_id": "a", "ds": pd.date_range("2024-01-01", periods=5), "price": [10.0, 10, 10, 10, 10]})
    base = price_ratio(df)
    spiked = df.copy()
    spiked.loc[4, "price"] = 1000.0
    r = price_ratio(spiked)
    assert base.iloc[4] == 1.0 and r.iloc[4] == 100.0  # ratio = today's price / mean of PREVIOUS prices (still 10)
    assert (r.iloc[:4] == base.iloc[:4]).all()  # earlier rows unaffected by a later price


def test_make_folds_are_contiguous_and_end_at_max_date() -> None:
    fs = make_folds(pd.Timestamp("2024-12-31"), h=28, folds=3)
    assert fs[-1].end == pd.Timestamp("2024-12-31") and fs[0].start < fs[1].start < fs[2].start
    assert all((f.end - f.start).days == 27 for f in fs)
    assert fs[1].start == fs[0].end + pd.Timedelta(days=1)  # gap = 0
    assert all(f.cutoff == f.start - pd.Timedelta(days=1) for f in fs)


@pytest.fixture(scope="module")
def small() -> pd.DataFrame:
    cfg = SynthConfig(start="2024-01-01", end="2024-12-31", categories={"Sweets": 2, "Beverages": 2})
    sales = generate(cfg)["sales"].filter(pl.col("location").is_in(["Koramangala", "Indiranagar"]))
    fest = load_festivals()["date"].to_pandas()
    return add_covariates(to_long(sales.with_columns(category=pl.lit("x"))), fest)


def test_backtest_forecasts_do_not_depend_on_test_targets(small: pd.DataFrame) -> None:
    """Leakage: corrupting y inside the test window must not change any forecast."""
    a = run_backtest(small, models=["seasonal_naive", "lightgbm_tweedie"], folds=1)
    start = pd.Timestamp(a.folds[0]["start"])
    tampered = small.copy()
    tampered.loc[tampered["ds"] >= start, "y"] = 1e6
    b = run_backtest(tampered, models=["seasonal_naive", "lightgbm_tweedie"], folds=1)
    key = ["model", "unique_id", "ds"]
    fa, fb = a.forecasts.sort_values(key), b.forecasts.sort_values(key)
    assert np.allclose(fa[["p10", "p50", "p90"]].to_numpy(), fb[["p10", "p50", "p90"]].to_numpy())
    assert not np.allclose(fa["y"].to_numpy(), fb["y"].to_numpy())  # ...while the scored actuals did change
    assert a.folds[0]["models"]["seasonal_naive"]["wape"] != b.folds[0]["models"]["seasonal_naive"]["wape"]


def test_backtest_outputs_all_metrics_and_forecasts_are_valid(small: pd.DataFrame) -> None:
    res = run_backtest(small, models=["seasonal_naive", "auto_ets", "croston", "lightgbm_tweedie"], folds=2)
    assert len(res.folds) == 2
    s = summarise(res)
    assert set(s) == {"seasonal_naive", "auto_ets", "croston", "lightgbm_tweedie"}
    for name, m in s.items():
        assert {"wape", "mase", "bias", "pinball", "coverage80"} <= m.keys(), name
        assert np.isfinite(m["wape"]) and m["wape"] >= 0
    f = res.forecasts
    assert (f["p50"] >= 0).all() and f.dropna(subset=["p10", "p90"]).eval("p10 <= p50 <= p90").all()
    assert np.isnan(s["croston"]["coverage80"]) and 0 <= s["lightgbm_tweedie"]["coverage80"] <= 1


def test_ragged_series_and_stray_future_rows_do_not_break_the_backtest(small: pd.DataFrame) -> None:
    """Regression: a live 'today' row, a series that ended early and a tiny series used to crash LightGBM."""
    ids = sorted(small["unique_id"].unique())
    ragged = small.copy()
    last_day = ragged["ds"].max()
    stray = (
        ragged[ragged["unique_id"] == ids[0]].tail(1).assign(ds=last_day + pd.Timedelta(days=300))
    )  # live sale "today"
    early = ragged[(ragged["unique_id"] == ids[1]) & (ragged["ds"] <= last_day - pd.Timedelta(days=60))]
    tiny = ragged[ragged["unique_id"] == ids[2]].head(20)
    ragged = pd.concat([ragged[~ragged["unique_id"].isin(ids[1:3])], early, tiny, stray], ignore_index=True)
    res = run_backtest(ragged, models=["seasonal_naive", "lightgbm_tweedie"], folds=1)
    f = res.folds[0]
    assert f["series_skipped"] == 2 and f["series"] == len(ids) - 2  # early-ending + tiny series skipped, none crash
    assert pd.Timestamp(f["end"]) == last_day  # stray future row ignored: folds anchor on the common end date
    assert res.forecasts["ds"].max() <= last_day


def test_future_forecast_covers_next_h_days_after_common_end_and_is_valid(small: pd.DataFrame) -> None:
    from kalpa_ml.backtest import forecast_future, future_frame

    fest = load_festivals()["date"].to_pandas()
    as_of = small["ds"].max()
    fut = future_frame(small, as_of, 28)
    assert fut["ds"].min() == as_of + pd.Timedelta(days=1) and (fut["promo"] == 0).all()
    last_price = small[small["ds"] == as_of].set_index("unique_id")["price"]
    first = fut.groupby("unique_id")["price"].first().sort_index()
    assert np.allclose(first.to_numpy(), last_price.sort_index().to_numpy())  # price held flat

    out, got_as_of = forecast_future(small, fest, models=["seasonal_naive", "lightgbm_tweedie"], h=28)
    assert got_as_of == as_of
    assert set(out["model"]) == {"seasonal_naive", "lightgbm_tweedie"}
    n_series = small["unique_id"].nunique()
    assert len(out) == 2 * n_series * 28 and out["ds"].min() == as_of + pd.Timedelta(days=1)
    assert (out["p50"] >= 0).all() and (out["p10"] <= out["p50"]).all() and (out["p50"] <= out["p90"]).all()


def test_backtest_replay_costs_each_model_and_is_reproducible(small: pd.DataFrame) -> None:
    from kalpa_ml.replay import SeriesParams

    params = {
        u: SeriesParams(lead_days=3, review_days=7, pack=6, moq=12, unit_cost=100.0, category="x")
        for u in small["unique_id"].unique()
    }
    a = run_backtest(small, models=["seasonal_naive", "lightgbm_tweedie"], folds=2, policy_params=params)
    b = run_backtest(small, models=["seasonal_naive", "lightgbm_tweedie"], folds=2, policy_params=params)
    f0, f1 = a.folds[0]["models"]["seasonal_naive"], a.folds[1]["models"]["seasonal_naive"]
    assert np.isnan(f0["cost"])  # fold 0 has no earlier residuals -> no honest safety stock -> no replay
    for name in ("seasonal_naive", "lightgbm_tweedie"):
        m = a.folds[1]["models"][name]
        assert m["cost"] > 0 and 0 < m["fill_rate"] <= 1 and m["stockout_days"] >= 0 and m["avg_inventory"] >= 0
        assert m["cost"] == b.folds[1]["models"][name]["cost"]  # reproducible
    assert a.safety["seasonal_naive"]["__all__"]["7"]["0.95"] >= 0
    assert f1["cost"] > 0
    s = summarise(a)
    assert {"cost", "fill_rate", "stockout_days", "avg_inventory"} <= s["lightgbm_tweedie"].keys()
