"""Hierarchical reconciliation (total > store > category > store x category > SKU) with hierarchicalforecast.

Bottom-level (store|sku) forecasts come from the model under test; upper-level base forecasts come from SeasonalNaive
on the aggregated history. BottomUp and MinT-shrink then make every level add up exactly.

Scope note: this reconciles the point forecast (P50). Quantiles are NOT summed (the P90 of a sum is not the sum of
P90s); aggregate-level intervals must be calibrated on aggregate residuals.
"""

import numpy as np
import pandas as pd
from hierarchicalforecast.core import HierarchicalReconciliation
from hierarchicalforecast.methods import BottomUp, MinTrace
from hierarchicalforecast.utils import aggregate
from statsforecast import StatsForecast
from statsforecast.models import SeasonalNaive

SPEC = [
    ["store"],
    ["category"],
    ["store", "category"],
    ["store", "category", "sku"],
]  # a "Total" level is added automatically
METHODS = {"bottom_up": "base/BottomUp", "mint_shrink": "base/MinTrace_method-mint_shrink"}


def _bottom_frame(hist: pd.DataFrame) -> pd.DataFrame:
    parts = hist["unique_id"].str.split("|", n=1, expand=True)
    return hist.assign(store=parts[0], sku=parts[1])[["store", "category", "sku", "ds", "y"]]


def reconcile(hist: pd.DataFrame, base_p50: pd.DataFrame, h: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """hist: unique_id ('store|sku'), ds, y, category. base_p50: unique_id, ds, p50 (h days).

    Returns (reconciled bottom-level frame with columns bottom_up / mint_shrink, reconciled frame for ALL levels).
    """
    y_df, s_df, tags = aggregate(_bottom_frame(hist), SPEC)
    sf = StatsForecast(models=[SeasonalNaive(season_length=7)], freq="D", n_jobs=1)
    base = pd.DataFrame(sf.forecast(df=y_df, h=h, fitted=True)).reset_index().rename(columns={"SeasonalNaive": "base"})
    fitted = pd.DataFrame(sf.forecast_fitted_values()).reset_index()  # type: ignore[no-untyped-call]
    fitted = fitted.rename(columns={"SeasonalNaive": "base"})

    # swap in the model's bottom-level forecasts (ids in the aggregated frame are 'store/category/sku')
    cat = hist.groupby("unique_id")["category"].first()
    parts = base_p50["unique_id"].str.split("|", n=1, expand=True)
    hf_id = parts[0] + "/" + base_p50["unique_id"].map(cat) + "/" + parts[1]
    model_bottom = pd.Series(base_p50["p50"].to_numpy(), index=pd.MultiIndex.from_arrays([hf_id, base_p50["ds"]]))
    is_bottom = base["unique_id"].isin(set(hf_id))
    idx = pd.MultiIndex.from_arrays([base.loc[is_bottom, "unique_id"], base.loc[is_bottom, "ds"]])
    base.loc[is_bottom, "base"] = model_bottom.reindex(idx).to_numpy()

    rec = HierarchicalReconciliation(reconcilers=[BottomUp(), MinTrace(method="mint_shrink")])
    out = pd.DataFrame(rec.reconcile(Y_hat_df=base, Y_df=fitted, S_df=s_df, tags=tags))
    for name, col in METHODS.items():
        out[name] = out[col].clip(lower=0)
    bottom = out[out["unique_id"].isin(set(hf_id))].copy()
    return bottom[["unique_id", "ds", "base", *METHODS]], out


def coherence_error(all_levels: pd.DataFrame, col: str) -> float:
    """Largest |parent - sum(children)| over all days for total, store and category levels (0 = fully coherent)."""
    d = all_levels.copy()
    is_bottom = d["unique_id"].str.count("/") == 2
    b = d[is_bottom].copy()
    parts = b["unique_id"].str.split("/", expand=True)
    b["store"], b["category"] = parts[0], parts[1]
    errs = []
    for keys in (["store"], ["category"]):
        agg = b.groupby([*keys, "ds"])[col].sum().rename("sum").reset_index()
        agg["unique_id"] = agg[keys[0]]
        m = d.merge(agg[["unique_id", "ds", "sum"]], on=["unique_id", "ds"])
        errs.append(float(np.abs(m[col] - m["sum"]).max()))
    total = b.groupby("ds")[col].sum()
    tot = d[d["unique_id"] == "Total"].set_index("ds")[col]
    errs.append(float(np.abs(tot - total.reindex(tot.index)).max()))
    return max(errs)
