"""Explainability: TFT interpretability summaries, LightGBM SHAP, the deterministic narrative and its number guard.

Design rule (Phase 7): a sentence shown to a user may only contain numbers that are present in the structured
facts it was built from. `numbers_in` + `is_grounded` enforce this for template text AND for LLM-polished text.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

STATIC_NAMES = ["store", "sku", "category"]
OBSERVED_NAMES = ["units (past)"]
LABELS = {
    "days_to_festival": "days to the next festival",
    "festival_window": "the festival window",
    "price_ratio": "the price level",
    "promo": "the planned promotion",
    "weekly": "the weekly pattern",
    "yearly": "yearly seasonality",
}
_GROUP = {"dow_sin": "weekly", "dow_cos": "weekly", "doy_sin": "yearly", "doy_cos": "yearly"}


def _round(a: np.ndarray, nd: int = 4) -> list[float]:
    return [round(float(v), nd) for v in a]


def summarise_tft(
    expl: Mapping[str, np.ndarray], ids: Sequence[str], known_cols: Sequence[str], lookback: int
) -> dict[str, dict[str, Any]]:
    """Per series: static / encoder / decoder variable weights (time-averaged) and attention over the lookback days.

    Attention is [L+H, L+H] per series; the rows of the forecast days (decoder) over the lookback columns, averaged
    over the horizon, tell which past days the forecast leaned on. Each weight vector sums to ~1 (softmax output).
    """
    n = len(ids)
    static = expl["static"].reshape(n, -1)
    enc = expl["encoder"].reshape(n, lookback, -1).mean(axis=1)
    dec = expl["decoder"].reshape(n, -1, len(known_cols)).mean(axis=1)
    att = expl["attention"][:, lookback:, :lookback].mean(axis=1)  # [S, L]
    out: dict[str, dict[str, Any]] = {}
    for i, uid in enumerate(ids):
        out[uid] = {
            "static_w": {"names": STATIC_NAMES[: static.shape[1]], "w": _round(static[i])},
            "encoder_w": {"names": [*OBSERVED_NAMES, *known_cols], "w": _round(enc[i])},
            "decoder_w": {"names": list(known_cols), "w": _round(dec[i])},
            "attention": {"lookback_days": lookback, "w": _round(att[i], 5)},
        }
    return out


def group_weights(names: Sequence[str], w: Sequence[float]) -> list[tuple[str, float]]:
    """Merge sin/cos pairs into one human driver, sorted by weight (largest first)."""
    acc: dict[str, float] = {}
    for name, v in zip(names, w, strict=True):
        key = _GROUP.get(name, name)
        acc[key] = acc.get(key, 0.0) + float(v)
    return sorted(((LABELS.get(k, k), v) for k, v in acc.items()), key=lambda kv: -kv[1])


def lgbm_shap(model: Any, x: pd.DataFrame, ids: list[str], h: int, target: str = "p50") -> pd.DataFrame:
    """Mean |SHAP| per feature for the given series using LightGBM's built-in TreeSHAP (`pred_contrib=True`).

    The feature frames mlforecast builds at each recursive step are captured with `before_predict_callback`, so the
    attribution is for exactly the inputs the model saw. Returns rows (unique_id, feature, mean_abs, mean_signed).
    """
    frames: list[pd.DataFrame] = []

    def grab(new_x: pd.DataFrame) -> pd.DataFrame:
        frames.append(new_x.copy())
        return new_x

    cols = [c for c in x.columns if c not in ("price",)]
    model.predict(h=h, X_df=x[x["unique_id"].isin(ids)][cols], ids=ids, before_predict_callback=grab)
    booster = model.models_[target].booster_
    names: list[str] = list(booster.feature_name())
    # mlforecast recurses model by model (outer) and step by step (inner): keep only the target model's h frames,
    # because later steps of each model are built from THAT model's own earlier predictions.
    k = list(model.models_).index(target)
    frames = frames[k * h : (k + 1) * h]
    rows = []
    for uid_frame in frames:
        feats = uid_frame[names]
        contrib = booster.predict(feats, pred_contrib=True)[:, :-1]  # last column is the expected value (bias)
        rows.append(pd.DataFrame(contrib, columns=names, index=getattr(uid_frame, "index", None)))
    allc = pd.concat(rows)
    # series identity: mlforecast keeps one row per series per step, in the order of `ids`
    allc["unique_id"] = np.tile(np.array(ids), len(frames))[: len(allc)]
    long = allc.melt(id_vars="unique_id", var_name="feature", value_name="shap")
    agg = long.groupby(["unique_id", "feature"])["shap"].agg(
        mean_abs=lambda s: float(np.abs(s).mean()), mean_signed="mean"
    )
    return agg.reset_index()


def _shap_group(feature: str) -> str:
    if feature.startswith("dow_"):
        return "weekly"
    if feature.startswith("doy_"):
        return "yearly"
    if feature.startswith(("lag", "rolling_")):
        return "recent sales level"
    return feature


def shap_drivers(agg: pd.DataFrame) -> list[tuple[str, float]]:
    """Share of total mean |SHAP| per human-readable driver (largest first); shares sum to 1."""
    g = agg.assign(group=agg["feature"].map(_shap_group)).groupby("group")["mean_abs"].sum()
    total = float(g.sum())
    if total <= 0:
        return []
    return [(LABELS.get(str(k), str(k)), float(v) / total) for k, v in g.sort_values(ascending=False).items()]


# ---------------------------------------------------------------- narrative + number guard

ID_KEYS = frozenset({"run_id", "id", "location_id", "product_id"})
_NUM = re.compile(r"(?<![A-Za-z0-9_])\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every standalone number in a text (Indian/Western digit grouping allowed); 'P90' style tokens are ignored."""
    return [float(m.group(0).replace(",", "")) for m in _NUM.finditer(text)]


def _walk(obj: Any) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int | float):
        return [float(obj)]
    if isinstance(obj, str):
        return numbers_in(obj)
    if isinstance(obj, Mapping):  # ids are not figures a reader sees: their digits must not whitelist numbers
        return [v for k, x in obj.items() if k not in ID_KEYS for v in _walk(x)]
    if isinstance(obj, Sequence):
        return [v for x in obj for v in _walk(x)]
    return []


def is_grounded(text: str, facts: Any, tol: float = 1e-9) -> bool:
    """True only if every number in `text` also occurs in `facts` (the JSON the text was derived from)."""
    allowed = _walk(facts)
    return all(any(abs(n - a) <= tol * max(1.0, abs(a)) for a in allowed) for n in numbers_in(text))


def build_facts(
    product: str,
    location: str,
    horizon_days: int,
    recent_units: float,
    forecast_units: float,
    drivers: Sequence[tuple[str, float]],
    festival: tuple[str, int] | None = None,
    model: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The structured JSON every narrative is generated from (only these numbers may appear in the text)."""
    change = (forecast_units - recent_units) / recent_units * 100.0 if recent_units > 0 else None
    return {
        "product": product,
        "location": location,
        "horizon_days": horizon_days,
        "recent_units": round(recent_units),
        "forecast_units": round(forecast_units),
        "change_pct": None if change is None else round(abs(change)),
        "direction": "flat" if change is None or abs(change) < 2 else ("rise" if change > 0 else "fall"),
        "drivers": [{"name": n, "weight_pct": round(w * 100)} for n, w in drivers[:2]],
        "festival": None if festival is None else {"name": festival[0], "days_away": festival[1]},
        "model": model,
        "run_id": run_id,
    }


def template_narrative(f: Mapping[str, Any]) -> str:
    """Deterministic 'why this forecast' sentence(s); works with no API key and never invents a number."""
    d = f["direction"]
    verb = {"rise": "rise", "fall": "fall", "flat": "stay roughly flat"}[d]
    pct = f" by {f['change_pct']}%" if d != "flat" and f["change_pct"] is not None else ""
    s = (
        f"Demand for {f['product']} at {f['location']} is expected to {verb}{pct} "
        f"over the next {f['horizon_days']} days "
        f"({f['forecast_units']} units against {f['recent_units']} in the previous {f['horizon_days']} days)"
    )
    drv = f["drivers"]
    if drv:
        parts = [f"{x['name']} ({x['weight_pct']}% weight)" for x in drv]
        s += ", mainly driven by " + " and ".join(parts)
    s += "."
    fe = f.get("festival")
    if fe:
        s += f" {fe['name']} falls {fe['days_away']} days after the last observed day."
    return s


def grounded_or_template(candidate: str | None, facts: Mapping[str, Any]) -> tuple[str, str]:
    """(text, source). An LLM text is accepted only if every number is in `facts`; otherwise the template is used."""
    if candidate and is_grounded(candidate, facts):
        return candidate, "llm"
    return template_narrative(facts), "template"
