"""Leakage-safe feature builders (pandas, Nixtla long format: unique_id, ds, y + covariates).

Known-future features (calendar, festivals, promo, planned price) may use any date.
Observed-past features may only use rows strictly before the row's own date (shift(1) before rolling).
"""

import numpy as np
import pandas as pd

FESTIVAL_CAP = 30  # days_to_festival is capped; "no festival soon" = cap


def calendar_features(ds: pd.Series) -> pd.DataFrame:
    """Cyclical day-of-week / day-of-year encodings (deterministic from the date)."""
    d = pd.to_datetime(ds)
    dow, doy = d.dt.dayofweek.to_numpy(), d.dt.dayofyear.to_numpy()
    return pd.DataFrame(
        {
            "dow_sin": np.sin(2 * np.pi * dow / 7),
            "dow_cos": np.cos(2 * np.pi * dow / 7),
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        },
        index=ds.index,
    )


def festival_features(ds: pd.Series, festival_dates: pd.Series) -> pd.DataFrame:
    """days_to_festival (0 on the day, capped at 30; 30 = none soon) and festival_window (0-7 days before)."""
    fest = np.sort(pd.to_datetime(festival_dates).to_numpy().astype("datetime64[D]").astype("int64"))
    days = pd.to_datetime(ds).to_numpy().astype("datetime64[D]").astype("int64")
    idx = np.searchsorted(fest, days, side="left")  # first festival on/after each day
    gap = np.full(len(days), FESTIVAL_CAP, dtype=float)
    has_next = idx < len(fest)
    gap[has_next] = fest[idx[has_next]] - days[has_next]
    dtf = np.minimum(gap, FESTIVAL_CAP)
    return pd.DataFrame({"days_to_festival": dtf, "festival_window": (dtf <= 7).astype(int)}, index=ds.index)


def price_ratio(df: pd.DataFrame, window: int = 28) -> pd.Series:
    """price / trailing mean of the series' *previous* `window` prices (shift(1): never includes today)."""
    g = df.groupby("unique_id", sort=False)["price"]
    prev = g.transform(lambda s: s.shift(1).rolling(window, min_periods=1).mean())
    return (df["price"] / prev).fillna(1.0).replace([np.inf, -np.inf], 1.0)


def add_covariates(df: pd.DataFrame, festival_dates: pd.Series) -> pd.DataFrame:
    """Add all model covariates to a long frame (needs ds; price/promo optional)."""
    out = df.copy()
    out = pd.concat([out, calendar_features(out["ds"]), festival_features(out["ds"], festival_dates)], axis=1)
    if "price" in out.columns:
        out["price_ratio"] = price_ratio(out)
    if "promo" in out.columns:
        out["promo"] = out["promo"].astype(int)
    return out


EXOG = ["dow_sin", "dow_cos", "doy_sin", "doy_cos", "days_to_festival", "festival_window", "price_ratio", "promo"]
