"""Drift measures: Population Stability Index (PSI) and live (post-training) WAPE vs the backtest WAPE.

PSI = sum_i (a_i - e_i) * ln(a_i / e_i) over quantile bins of the EXPECTED (training-window) sample.
Conventional reading: < 0.1 stable, 0.1-0.2 moderate shift, > 0.2 significant shift (the alert threshold).
"""

from dataclasses import dataclass

import numpy as np

PSI_WARN = 0.1
PSI_ALERT = 0.2
WAPE_WARN_RATIO = 1.1
WAPE_ALERT_RATIO = 1.2  # live WAPE more than 20 % worse than the backtest WAPE
EPS = 1e-4  # empty bins would make ln(0) infinite; both shares are floored at EPS


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """PSI of `actual` against `expected` using up to `bins` quantile bins of `expected` (ties merge bins)."""
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    if len(e) == 0 or len(a) == 0:
        raise ValueError("PSI needs at least one value on each side")
    edges = np.unique(np.quantile(e, np.linspace(0.0, 1.0, bins + 1)))
    if len(edges) < 2:  # constant expected sample: one bin around that value, anything else is outside it
        edges = np.array([e[0] - 0.5, e[0] + 0.5])
    inner = edges[1:-1]  # open-ended outer bins: values outside the training range still count
    e_share = np.bincount(np.searchsorted(inner, e, side="right"), minlength=len(inner) + 1) / len(e)
    a_share = np.bincount(np.searchsorted(inner, a, side="right"), minlength=len(inner) + 1) / len(a)
    e_share, a_share = np.clip(e_share, EPS, None), np.clip(a_share, EPS, None)
    return float(np.sum((a_share - e_share) * np.log(a_share / e_share)))


def psi_status(value: float) -> str:
    """'ok' | 'warn' | 'alert' for a PSI value."""
    return "alert" if value > PSI_ALERT else "warn" if value > PSI_WARN else "ok"


@dataclass(frozen=True)
class WapeDrift:
    live: float
    backtest: float
    ratio: float
    status: str


def wape(y: np.ndarray, yhat: np.ndarray) -> float:
    """sum|y - yhat| / sum|y| (NaN when there is no demand at all)."""
    den = float(np.abs(y).sum())
    return float(np.abs(y - yhat).sum() / den) if den > 0 else float("nan")


def wape_drift(y: np.ndarray, yhat: np.ndarray, backtest_wape: float) -> WapeDrift:
    """Live WAPE of forecasts against what actually sold, compared with the WAPE measured in the backtest."""
    live = wape(np.asarray(y, dtype=float), np.asarray(yhat, dtype=float))
    ratio = live / backtest_wape if backtest_wape > 0 and live == live else float("nan")
    status = "alert" if ratio > WAPE_ALERT_RATIO else "warn" if ratio > WAPE_WARN_RATIO else "ok"
    return WapeDrift(live, backtest_wape, ratio, status if ratio == ratio else "unknown")
