"""Convert canonical sales frames <-> Nixtla long format."""

import pandas as pd
import polars as pl


def to_long(sales: pl.DataFrame) -> pd.DataFrame:
    """Canonical sales (location, sku, date, units, ...) -> Nixtla long format (unique_id, ds, y, ...)."""
    out = sales.with_columns(
        unique_id=pl.col("location") + pl.lit("|") + pl.col("sku"),
        ds=pl.col("date").cast(pl.Datetime),
        y=pl.col("units").cast(pl.Float64),
    )
    keep = ["unique_id", "ds", "y"] + [c for c in ("price", "promo", "category") if c in out.columns]
    return out.select(keep).sort(["unique_id", "ds"]).to_pandas()


def zero_fill(long: pd.DataFrame, min_coverage: float = 0.5, last_day: pd.Timestamp | None = None) -> pd.DataFrame:
    """Give every series one row per day from its first day to the common last day; a missing row means 'sold 0'.

    POS checkout (and the simulator) only write a sales row when something sold, while the forecasting models need
    complete daily series. The common last day is the latest day on which at least `min_coverage` of the series have
    a row (so a day that is still open, with few rows so far, is never turned into false zeros), optionally capped
    at `last_day`. Rows after that day are left untouched. price is carried forward (then back), promo gaps are 0.
    """
    if long.empty:
        return long
    counts = long.groupby("ds")["unique_id"].nunique()
    n = long["unique_id"].nunique()
    full = counts[counts >= min_coverage * n]
    if full.empty:
        return long
    end = full.index.max()
    if last_day is not None:
        end = min(end, pd.Timestamp(last_day))
    parts = []
    for uid, g in long.groupby("unique_id", sort=False):
        g = g.sort_values("ds").drop_duplicates("ds", keep="last")
        inside = g[g["ds"] <= end]
        if inside.empty:
            parts.append(g)
            continue
        grid = pd.date_range(inside["ds"].min(), end, freq="D")
        filled = inside.set_index("ds").reindex(grid)
        filled.index.name = "ds"
        filled["unique_id"] = uid
        filled["y"] = filled["y"].fillna(0.0)
        if "price" in filled.columns:
            filled["price"] = filled["price"].ffill().bfill()
        if "promo" in filled.columns:
            filled["promo"] = filled["promo"].fillna(0)
        if "category" in filled.columns:
            filled["category"] = filled["category"].ffill().bfill()
        parts.append(pd.concat([filled.reset_index(), g[g["ds"] > end]], ignore_index=True))
    out = pd.concat(parts, ignore_index=True)
    return out[list(long.columns)].sort_values(["unique_id", "ds"]).reset_index(drop=True)
