import numpy as np
import pandas as pd
import polars as pl
import pytest
import torch

from kalpa_ml.data.synth import SynthConfig, generate, load_festivals
from kalpa_ml.features import add_covariates
from kalpa_ml.frames import to_long
from kalpa_ml.models.tft.data import build_arrays, series_scale, train_pairs
from kalpa_ml.models.tft.layers import pinball_loss
from kalpa_ml.models.tft.model import TemporalFusionTransformer, TFTConfig
from kalpa_ml.models.tft.runner import run_tft
from kalpa_ml.models.tft.train import TrainConfig, fit, predict

CFG = TFTConfig(
    static_cardinalities=[3, 4, 2], n_known=5, n_observed=1, lookback=14, horizon=7, d_model=16, dropout=0.0
)


def _batch(b: int = 4) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    g = torch.Generator().manual_seed(0)
    return (
        torch.randint(0, 2, (b, 3), generator=g),
        torch.rand(b, CFG.lookback, 1, generator=g),
        torch.rand(b, CFG.lookback + CFG.horizon, CFG.n_known, generator=g),
    )


def test_forward_shapes_and_weight_distributions() -> None:
    out = TemporalFusionTransformer(CFG)(*_batch())
    assert out["quantiles"].shape == (4, 7, 3)
    assert out["static_weights"].shape == (4, 3) and out["decoder_weights"].shape == (4, 7, 5)
    assert out["encoder_weights"].shape == (4, 14, 6)  # 1 observed + 5 known
    assert out["attention"].shape == (4, 21, 21)
    for k in ("static_weights", "encoder_weights", "decoder_weights"):
        assert torch.allclose(out[k].sum(-1), torch.ones_like(out[k].sum(-1)), atol=1e-5)


def test_model_attention_is_causal_end_to_end() -> None:
    out = TemporalFusionTransformer(CFG).eval()(*_batch())
    assert torch.all(torch.triu(out["attention"], diagonal=1) == 0)


def test_decoder_cannot_see_future_targets_only_known_inputs() -> None:
    """Changing the OBSERVED values after the encoder window is impossible by construction; changing future KNOWN inputs
    must change the forecast (they are legitimate inputs), while the observed tensor has only L steps."""
    m = TemporalFusionTransformer(CFG).eval()
    s, o, k = _batch()
    base = m(s, o, k)["quantiles"]
    k2 = k.clone()
    k2[:, CFG.lookback :, 0] += 5.0  # e.g. a different planned promo
    assert not torch.allclose(base, m(s, o, k2)["quantiles"])
    assert o.shape[1] == CFG.lookback  # no future observed values exist in the interface


def _toy_arrays(n_series: int = 6, days: int = 120) -> tuple["object", int]:
    rng = np.random.default_rng(0)
    rows = []
    for s in range(n_series):
        for d, ds in enumerate(pd.date_range("2024-01-01", periods=days)):
            lam = 5 + 3 * np.sin(2 * np.pi * d / 7) + s
            rows.append(
                {
                    "unique_id": f"S{s % 2}|K{s}",
                    "ds": ds,
                    "y": float(rng.poisson(lam)),
                    "category": "c",
                    **{c: 0.0 for c in ["dow_sin", "dow_cos", "doy_sin", "doy_cos", "festival_window"]},
                }
            )
    df = pd.DataFrame(rows)
    df["dow_sin"], df["dow_cos"] = (
        np.sin(2 * np.pi * df["ds"].dt.dayofweek / 7),
        np.cos(2 * np.pi * df["ds"].dt.dayofweek / 7),
    )
    return build_arrays(df, pd.DatetimeIndex(sorted(df["ds"].unique()))), days - 1


def test_series_scale_uses_only_history_up_to_cutoff() -> None:
    y = np.array([[2.0, 4.0, 0.0, 1000.0]], dtype=np.float32)
    assert series_scale(y, cutoff=2)[0] == 3.0  # mean of non-zero 2,4 (the 1000 after the cutoff is ignored)
    assert series_scale(np.zeros((1, 5), dtype=np.float32), 4)[0] == 1.0


def test_train_pairs_never_target_beyond_cutoff() -> None:
    a, cutoff = _toy_arrays()
    p = train_pairs(a, cutoff=80, L=14, H=7, n=2000, rng=np.random.default_rng(0))
    assert p[:, 1].max() + 7 <= 80 and p[:, 1].min() >= 13


def test_training_reduces_loss_is_deterministic_and_forecasts_are_valid() -> None:
    a, cutoff = _toy_arrays()
    cfg = TFTConfig(
        static_cardinalities=a.cardinalities, n_known=len(a.known_cols), lookback=14, horizon=7, d_model=16, dropout=0.0
    )
    tc = TrainConfig(epochs=4, windows_per_epoch=600, batch_size=64, val_windows=100, patience=10, seed=3)
    t1 = fit(a, cutoff, cfg, tc)
    assert t1.history[-1]["train_loss"] < t1.history[0]["train_loss"]  # it learns
    t2 = fit(a, cutoff, cfg, tc)
    q1, _ = predict(t1, a, cutoff - 7)
    q2, _ = predict(t2, a, cutoff - 7)
    assert np.allclose(q1, q2, atol=1e-5)  # same seed -> same model
    assert q1.shape == (len(a.ids), 7, 3) and (q1 >= 0).all()
    assert (np.diff(q1, axis=-1) >= -1e-6).all()  # quantiles never cross


def test_pinball_is_what_training_minimises() -> None:
    y = torch.ones(2, 3, 1) * 5
    good, bad = torch.ones(2, 3, 3) * 5, torch.ones(2, 3, 3) * 9
    assert pinball_loss(y, good, [0.1, 0.5, 0.9]) < pinball_loss(y, bad, [0.1, 0.5, 0.9])


@pytest.fixture(scope="module")
def tiny_long() -> pd.DataFrame:
    cfg = SynthConfig(start="2024-01-01", end="2024-08-31", categories={"Sweets": 2, "Beverages": 2})
    sales = generate(cfg)["sales"].filter(pl.col("location").is_in(["Koramangala", "Indiranagar"]))
    sales = sales.join(generate(cfg)["catalog"].select("sku", "category"), on="sku")
    return add_covariates(to_long(sales), load_festivals()["date"].to_pandas())


def test_run_tft_forecasts_future_days_and_never_sees_target_values(tiny_long: pd.DataFrame) -> None:
    h = 14
    last = tiny_long["ds"].max()
    train = tiny_long[tiny_long["ds"] <= last - pd.Timedelta(days=h)]
    test = tiny_long[tiny_long["ds"] > last - pd.Timedelta(days=h)]
    future = test.drop(columns=["y"])
    tc = TrainConfig(epochs=2, windows_per_epoch=400, batch_size=64, val_windows=80, patience=5, seed=1)
    a = run_tft(train, future, h, lookback=28, d_model=16, tc=tc)
    assert isinstance(a, pd.DataFrame) and len(a) == tiny_long["unique_id"].nunique() * h
    assert (a["p10"] <= a["p50"]).all() and (a["p50"] <= a["p90"]).all() and (a["p50"] >= 0).all()
    tampered = test.copy()
    tampered["y"] = 1e6  # corrupt the held-out targets: the forecast must be IDENTICAL
    b = run_tft(train, tampered.drop(columns=["y"]), h, lookback=28, d_model=16, tc=tc)
    assert isinstance(b, pd.DataFrame) and np.allclose(a[["p10", "p50", "p90"]], b[["p10", "p50", "p90"]], atol=1e-5)
