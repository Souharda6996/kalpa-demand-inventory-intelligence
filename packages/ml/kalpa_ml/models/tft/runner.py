"""TFT adapter with the same contract as the baselines: (train, future covariates, h) -> p10/p50/p90 frame."""

import numpy as np
import pandas as pd

from kalpa_ml.models.tft.data import build_arrays
from kalpa_ml.models.tft.model import TFTConfig
from kalpa_ml.models.tft.train import TrainConfig, fit, predict


def run_tft(
    train: pd.DataFrame,
    future: pd.DataFrame,
    h: int,
    lookback: int = 56,
    d_model: int = 32,
    tc: TrainConfig | None = None,
    return_explanations: bool = False,
) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, np.ndarray], list[str], list[str]]:
    """Train on `train` (history only) and forecast the h days in `future` (known covariates, no target).

    Series that do not have every day on the common grid are dropped (not imputed); the frame only contains kept series.
    """
    hist_dates = pd.DatetimeIndex(sorted(train["ds"].unique()))
    fut_dates = pd.DatetimeIndex(sorted(future["ds"].unique()))
    if len(fut_dates) != h:
        raise ValueError(f"future must contain exactly {h} days, got {len(fut_dates)}")
    grid = hist_dates.append(fut_dates)
    cat = {"category": train.groupby("unique_id")["category"].first()} if "category" in train.columns else {}
    fut = future.copy()
    for col, mapping in cat.items():
        fut[col] = fut["unique_id"].map(mapping)
    fut["y"] = np.nan
    both = pd.concat([train, fut[[c for c in train.columns if c in fut.columns]]], ignore_index=True)
    a = build_arrays(both, grid)
    if not a.ids:
        raise ValueError("no series have a complete history on the common date grid")
    cfg = TFTConfig(
        static_cardinalities=a.cardinalities, n_known=len(a.known_cols), lookback=lookback, horizon=h, d_model=d_model
    )
    cutoff = len(hist_dates) - 1
    trained = fit(a, cutoff, cfg, tc)
    q, expl = predict(trained, a, cutoff)
    n = len(a.ids)
    frame = pd.DataFrame(
        {
            "unique_id": np.repeat(a.ids, h),
            "ds": np.tile(fut_dates, n),
            "p10": q[:, :, 0].ravel(),
            "p50": q[:, :, 1].ravel(),
            "p90": q[:, :, 2].ravel(),
        }
    )
    if return_explanations:
        names = {"known": a.known_cols}
        return frame, expl, a.ids, names["known"]
    return frame
