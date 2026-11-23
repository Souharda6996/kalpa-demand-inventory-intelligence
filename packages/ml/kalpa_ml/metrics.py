"""Forecast metrics, exactly as in Part D6. All take numpy arrays; y = actual, yhat = P50 forecast."""

import numpy as np
from numpy.typing import ArrayLike


def _a(x: ArrayLike) -> np.ndarray:
    return np.asarray(x, dtype=float)


def wape(y: ArrayLike, yhat: ArrayLike) -> float:
    """WAPE = sum|y - yhat| / sum|y| (nan if the actuals are all zero)."""
    y, yhat = _a(y), _a(yhat)
    denom = np.abs(y).sum()
    return float("nan") if denom == 0 else float(np.abs(y - yhat).sum() / denom)


def bias(y: ArrayLike, yhat: ArrayLike) -> float:
    """Bias = sum(yhat - y) / sum|y|  (positive = over-forecast)."""
    y, yhat = _a(y), _a(yhat)
    denom = np.abs(y).sum()
    return float("nan") if denom == 0 else float((yhat - y).sum() / denom)


def mase(y: ArrayLike, yhat: ArrayLike, insample: ArrayLike, season: int = 7) -> float:
    """MASE = mean|y - yhat| / mean|y_t - y_{t-season}| using the in-sample (training) series as scale."""
    y, yhat, ins = _a(y), _a(yhat), _a(insample)
    if len(ins) <= season:
        return float("nan")
    scale = np.abs(ins[season:] - ins[:-season]).mean()
    return float("nan") if scale == 0 else float(np.abs(y - yhat).mean() / scale)


def pinball(y: ArrayLike, yhat_q: ArrayLike, q: float) -> float:
    """Pinball loss for quantile q: mean(max(q*(y-yq), (q-1)*(y-yq)))."""
    y, yq = _a(y), _a(yhat_q)
    d = y - yq
    return float(np.maximum(q * d, (q - 1) * d).mean())


def coverage80(y: ArrayLike, p10: ArrayLike, p90: ArrayLike) -> float:
    """Share of actuals inside [P10, P90]."""
    y, lo, hi = _a(y), _a(p10), _a(p90)
    return float(((y >= lo) & (y <= hi)).mean())
