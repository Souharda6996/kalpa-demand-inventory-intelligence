"""Windowed tensors for the TFT from a Nixtla-style long frame (unique_id, ds, y + covariates).

All series are put on ONE common date grid; series with missing days are dropped (reported, never imputed).
Per-series target scale is computed from history up to the cutoff ONLY (no leakage from the horizon).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

KNOWN_COLS = ["dow_sin", "dow_cos", "doy_sin", "doy_cos", "days_to_festival", "festival_window", "price_ratio", "promo"]


@dataclass
class SeriesArrays:
    ids: list[str]
    dates: pd.DatetimeIndex
    y: np.ndarray  # [S, T] raw units (future rows are 0 and are never used as inputs or targets)
    known: np.ndarray  # [S, T, K] known-future features, normalised
    static: np.ndarray  # [S, n_static] ints
    cardinalities: list[int]
    known_cols: list[str]
    dropped: list[str]


def build_arrays(df: pd.DataFrame, dates: pd.DatetimeIndex) -> SeriesArrays:
    """Pivot to [S, T]; keep series that have a row for every date in `dates` (future rows may carry y=NaN)."""
    cols = [c for c in KNOWN_COLS if c in df.columns]
    d = df[df["ds"].isin(dates)]
    counts = d.groupby("unique_id").size()
    keep = sorted(counts[counts == len(dates)].index)
    dropped = sorted(set(df["unique_id"].unique()) - set(keep))
    d = d[d["unique_id"].isin(keep)].sort_values(["unique_id", "ds"])
    S, T = len(keep), len(dates)
    y = d["y"].fillna(0.0).to_numpy(dtype=np.float32).reshape(S, T)
    known = np.zeros((S, T, len(cols)), dtype=np.float32)
    for k, c in enumerate(cols):
        v = d[c].to_numpy(dtype=np.float32).reshape(S, T)
        if c == "days_to_festival":
            v = v / 30.0
        elif c == "price_ratio":
            v = np.clip(v, 0.3, 3.0) - 1.0
        known[:, :, k] = v
    stores = [u.split("|")[0] for u in keep]
    skus = [u.split("|")[1] for u in keep]
    cat_col = (
        d.groupby("unique_id")["category"].first().reindex(keep).tolist() if "category" in d.columns else ["all"] * S
    )
    codes = [pd.factorize(pd.Series(v))[0] for v in (stores, skus, cat_col)]
    static = np.stack(codes, axis=1).astype(np.int64)
    return SeriesArrays(
        keep, pd.DatetimeIndex(dates), y, known, static, [int(c.max()) + 1 for c in codes], cols, dropped
    )


def series_scale(y: np.ndarray, cutoff: int) -> np.ndarray:
    """Mean of NON-ZERO history up to and including `cutoff` (1.0 for all-zero series). Uses no future data."""
    hist = y[:, : cutoff + 1]
    nz = np.where(hist > 0, hist, np.nan)
    with np.errstate(all="ignore"):
        s = np.nanmean(nz, axis=1)
    return np.where(np.isfinite(s) & (s > 0), s, 1.0).astype(np.float32)


class WindowDataset(Dataset[dict[str, torch.Tensor]]):
    """(series, t) windows: encoder = [t-L+1, t], decoder = [t+1, t+H]; target = scaled y over the decoder."""

    def __init__(self, a: SeriesArrays, scale: np.ndarray, pairs: np.ndarray, L: int, H: int) -> None:
        self.a, self.scale, self.pairs, self.L, self.H = a, scale, pairs, L, H

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, i: int) -> dict[str, torch.Tensor]:
        s, t = (int(v) for v in self.pairs[i])
        L, H = self.L, self.H
        y = self.a.y[s] / self.scale[s]
        obs = y[t - L + 1 : t + 1, None]
        return {
            "static": torch.from_numpy(self.a.static[s]),
            "observed": torch.from_numpy(obs.astype(np.float32)),
            "known": torch.from_numpy(self.a.known[s, t - L + 1 : t + H + 1]),
            "target": torch.from_numpy(y[t + 1 : t + H + 1].astype(np.float32)),
        }


def train_pairs(
    a: SeriesArrays, cutoff: int, L: int, H: int, n: int, rng: np.random.Generator, recent_tau: float = 120.0
) -> np.ndarray:
    """Sample n (series, t) pairs, oversampling recent windows. Every target stays at or before `cutoff`."""
    t_min, t_max = L - 1, cutoff - H
    if t_max < t_min:
        raise ValueError("not enough history for the lookback + horizon")
    ts = np.arange(t_min, t_max + 1)
    w = np.exp((ts - t_max) / recent_tau)
    t_draw = rng.choice(ts, size=n, p=w / w.sum())
    s_draw = rng.integers(0, len(a.ids), size=n)
    return np.stack([s_draw, t_draw], axis=1)
