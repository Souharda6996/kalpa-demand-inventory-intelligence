"""Baseline forecasters. Every runner returns a long frame: unique_id, ds, p10, p50, p90 (clipped at 0).

Each takes `train` (history only) and, for covariate models, `future` (known-future covariates, no target).
"""

from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from mlforecast import MLForecast
from mlforecast.lag_transforms import RollingMean
from statsforecast import StatsForecast
from statsforecast.models import AutoETS, CrostonOptimized, SeasonalNaive

from kalpa_ml.features import EXOG

STAT_MODELS = {"seasonal_naive": "SeasonalNaive", "auto_ets": "AutoETS", "croston": "CrostonOptimized"}
LGBM_SEED = 0


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    """Clip at 0 and enforce p10 <= p50 <= p90."""
    out = df.copy()
    for c in ("p10", "p50", "p90"):
        out[c] = out[c].clip(lower=0)
    out["p10"] = np.minimum(out["p10"], out["p50"])  # NaN stays NaN (models without intervals keep "no band")
    out["p90"] = np.maximum(out["p90"], out["p50"])
    return out[["unique_id", "ds", "p10", "p50", "p90"]]


def run_stat(train: pd.DataFrame, h: int, models: list[str]) -> dict[str, pd.DataFrame]:
    """SeasonalNaive(7) / AutoETS / CrostonOptimized via statsforecast; 80 % intervals where the model supports them."""
    objs = {
        "seasonal_naive": SeasonalNaive(season_length=7),
        "auto_ets": AutoETS(season_length=7),
        "croston": CrostonOptimized(),
    }
    data = train[["unique_id", "ds", "y"]]
    out: dict[str, pd.DataFrame] = {}
    for m in models:
        interval = m != "croston"  # Croston is a point (median-only) forecaster in statsforecast
        sf = StatsForecast(models=[objs[m]], freq="D", n_jobs=1)
        fc = pd.DataFrame(sf.forecast(df=data, h=h, level=[80] if interval else None)).reset_index()
        col = STAT_MODELS[m]
        d = pd.DataFrame({"unique_id": fc["unique_id"], "ds": fc["ds"], "p50": fc[col]})
        d["p10"] = fc[f"{col}-lo-80"] if interval else float("nan")
        d["p90"] = fc[f"{col}-hi-80"] if interval else float("nan")
        out[m] = _finish(d)
    return out


def run_lgbm(train: pd.DataFrame, future: pd.DataFrame, h: int, save_dir: str | None = None) -> pd.DataFrame:
    """LightGBM: Tweedie point forecast (P50 proxy) + quantile 0.1/0.9 models; lags, rolling means, covariates."""
    base: dict[str, Any] = {
        "n_jobs": 4,
        "verbose": -1,
        "random_state": LGBM_SEED,
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_child_samples": 20,
    }
    models = {
        "p50": LGBMRegressor(objective="tweedie", tweedie_variance_power=1.3, n_estimators=300, **base),
        "p10": LGBMRegressor(objective="quantile", alpha=0.1, n_estimators=200, **base),
        "p90": LGBMRegressor(objective="quantile", alpha=0.9, n_estimators=200, **base),
    }
    fc = MLForecast(
        models=models,
        freq="D",
        lags=[7, 14, 28],
        lag_transforms={7: [RollingMean(window_size=7)], 28: [RollingMean(window_size=28)]},
    )
    cols = ["unique_id", "ds", "y", *[c for c in EXOG if c in train.columns]]
    fc.fit(train[cols], static_features=[])
    x_cols = ["unique_id", "ds", *[c for c in EXOG if c in future.columns]]
    pred = fc.predict(h=h, X_df=future[x_cols])
    if save_dir:  # keep the fitted model so what-if scenarios can re-infer without retraining
        fc.save(save_dir)
    return _finish(pred)
