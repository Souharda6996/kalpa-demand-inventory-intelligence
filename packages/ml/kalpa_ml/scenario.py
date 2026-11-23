"""What-if scenarios: change the KNOWN-FUTURE inputs (price, promo depth, festivals) and re-infer with a fitted model.

Weather is not a model input yet, so a weather shift is reported as "not modelled" rather than faked.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from mlforecast import MLForecast

from kalpa_ml.features import festival_features

NOT_MODELLED = ["weather shift (weather is not an input of the current model)"]


def apply_changes(
    x: pd.DataFrame,
    fest_dates: pd.Series,
    *,
    price_change_pct: float = 0.0,
    promo_depth_pct: float = 0.0,
    add_festival: date | None = None,
) -> pd.DataFrame:
    """Return a modified COPY of the future covariates.

    A price change of p % and a promotion of depth d % both scale the price ratio by (1+p/100)*(1-d/100); a promotion
    also switches the promo flag on. An extra festival date is added before festival features are rebuilt.
    """
    out = x.copy()
    mult = (1 + price_change_pct / 100.0) * (1 - promo_depth_pct / 100.0)
    if "price_ratio" in out.columns:
        out["price_ratio"] = out["price_ratio"] * mult
    if promo_depth_pct > 0 and "promo" in out.columns:
        out["promo"] = 1
    if add_festival is not None:
        dates = pd.concat(
            [pd.Series(pd.to_datetime(fest_dates)), pd.Series([pd.Timestamp(add_festival)])], ignore_index=True
        )
        ff = festival_features(out["ds"].reset_index(drop=True), dates)
        out["days_to_festival"] = ff["days_to_festival"].to_numpy()
        out["festival_window"] = ff["festival_window"].to_numpy()
    return out


def load_model(path: str | Path) -> MLForecast:
    """Load a fitted MLForecast saved by run_lgbm (a file this application wrote itself)."""
    return MLForecast.load(str(path))


def predict(model: MLForecast, x: pd.DataFrame, ids: list[str], h: int) -> pd.DataFrame:
    """p10 / p50 / p90 per (unique_id, ds) for the given series and covariates, clipped at 0 and ordered."""
    cols = [c for c in x.columns if c not in ("price",)]
    pred = model.predict(h=h, X_df=x[x["unique_id"].isin(ids)][cols], ids=ids)
    for c in ("p10", "p50", "p90"):
        pred[c] = pred[c].clip(lower=0)
    pred["p10"] = pred[["p10", "p50"]].min(axis=1)
    pred["p90"] = pred[["p90", "p50"]].max(axis=1)
    return pred[["unique_id", "ds", "p10", "p50", "p90"]]


def compare(
    model: MLForecast, x: pd.DataFrame, fest_dates: pd.Series, ids: list[str], h: int, **changes: Any
) -> dict[str, pd.DataFrame]:
    """Baseline and scenario forecasts for the same series."""
    return {
        "baseline": predict(model, x, ids, h),
        "scenario": predict(model, apply_changes(x, fest_dates, **changes), ids, h),
    }
