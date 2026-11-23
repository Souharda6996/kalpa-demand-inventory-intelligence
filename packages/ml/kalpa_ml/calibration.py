"""Split-conformal interval calibration (Part D6).

Score s_i = max(P10_i - y_i, y_i - P90_i) on a calibration fold; delta = the ceil((n+1)*cov)/n empirical quantile of s;
calibrated band = [P10 - delta, P90 + delta] (clipped at 0). Guarantee: marginal coverage >= cov on exchangeable data.
"""

import math

import numpy as np
import pandas as pd


def conformal_delta(y: np.ndarray, p10: np.ndarray, p90: np.ndarray, coverage: float = 0.8) -> float:
    """The correction delta (may be negative if the raw band is already too wide)."""
    s = np.maximum(p10 - y, y - p90)
    n = len(s)
    if n == 0:
        return 0.0
    k = min(math.ceil((n + 1) * coverage), n)  # with very small n the guarantee degrades; k is capped at n
    return float(np.sort(s)[k - 1])


def fit_conformal(
    cal: pd.DataFrame, group_col: str | None = None, coverage: float = 0.8, min_group: int = 30
) -> dict[str, float]:
    """cal needs y, p10, p90 (+ group_col). Returns {group: delta}; '__all__' is the pooled fallback."""
    cal = cal.dropna(subset=["p10", "p90", "y"])
    out = {"__all__": conformal_delta(cal["y"].to_numpy(), cal["p10"].to_numpy(), cal["p90"].to_numpy(), coverage)}
    if group_col and group_col in cal.columns:
        for g, d in cal.groupby(group_col):
            if len(d) >= min_group:  # tiny groups fall back to the pooled delta
                out[str(g)] = conformal_delta(d["y"].to_numpy(), d["p10"].to_numpy(), d["p90"].to_numpy(), coverage)
    return out


def apply_conformal(fc: pd.DataFrame, deltas: dict[str, float], group_col: str | None = None) -> pd.DataFrame:
    """Add p10_cal / p90_cal (>= 0, p10_cal <= p50 <= p90_cal) using each row's group delta (else the pooled one)."""
    out = fc.copy()
    if group_col and group_col in out.columns:
        d = out[group_col].astype(str).map(deltas).fillna(deltas["__all__"]).to_numpy()
    else:
        d = np.full(len(out), deltas["__all__"])
    out["p10_cal"] = np.minimum(np.clip(out["p10"].to_numpy() - d, 0, None), out["p50"].to_numpy())
    out["p90_cal"] = np.maximum(out["p90"].to_numpy() + d, out["p50"].to_numpy())
    return out
