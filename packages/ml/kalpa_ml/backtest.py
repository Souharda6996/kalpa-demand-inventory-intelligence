"""Rolling-origin backtest: 3 folds x 28 days, gap = 0. Train strictly on ds <= cutoff; target only used for scoring."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from kalpa_ml.calibration import apply_conformal, fit_conformal
from kalpa_ml.metrics import bias, coverage80, pinball, wape
from kalpa_ml.models.baselines import STAT_MODELS, run_lgbm, run_stat
from kalpa_ml.policy import safety_table
from kalpa_ml.replay import CostConfig, SeriesParams, replay_fold

ALL_MODELS = [*STAT_MODELS, "lightgbm_tweedie"]
OPTIONAL_MODELS = ["tft"]  # heavier (CPU training per fold); opt-in
MIN_TRAIN_DAYS = 60  # a series needs this much history before the cutoff to be scored in a fold


@dataclass
class Fold:
    index: int
    cutoff: pd.Timestamp  # last training day
    start: pd.Timestamp
    end: pd.Timestamp


@dataclass
class BacktestResult:
    folds: list[dict[str, Any]] = field(default_factory=list)
    forecasts: pd.DataFrame = field(default_factory=pd.DataFrame)  # fold, model, unique_id, ds, p10, p50, p90, y
    timings: dict[str, float] = field(default_factory=dict)
    safety: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # per model: residual-sum quantile table for order-up-to levels
    deltas: dict[str, dict[str, float]] = field(default_factory=dict)  # per model: conformal delta per category


def make_folds(max_date: pd.Timestamp, h: int = 28, folds: int = 3) -> list[Fold]:
    """Contiguous test windows ending at max_date; fold 0 is the oldest."""
    out = []
    for k in range(folds):
        end = max_date - pd.Timedelta(days=h * (folds - 1 - k))
        start = end - pd.Timedelta(days=h - 1)
        out.append(Fold(k, start - pd.Timedelta(days=1), start, end))
    return out


def score(test: pd.DataFrame, fc: pd.DataFrame, train: pd.DataFrame) -> dict[str, float]:
    """All metrics for one model on one fold (test has y; fc has p10/p50/p90; train gives the MASE scale)."""
    m = test[["unique_id", "ds", "y"]].merge(
        fc, on=["unique_id", "ds"], how="inner"
    )  # a model scores the series it forecast
    y, p50 = m["y"].to_numpy(), m["p50"].to_numpy()
    res: dict[str, float] = {
        "wape": wape(y, p50),
        "bias": bias(y, p50),
        "series_scored": float(m["unique_id"].nunique()),
    }
    # MASE per series (scale = mean |y_t - y_{t-7}| on the training window), then averaged over series.
    piv = train.pivot(index="unique_id", columns="ds", values="y")
    piv_index, hist = piv.index, piv.to_numpy()
    scale = pd.Series(np.nanmean(np.abs(hist[:, 7:] - hist[:, :-7]), axis=1), index=piv_index)
    err = (m["y"] - m["p50"]).abs().groupby(m["unique_id"]).mean()
    scale = scale.reindex(err.index)
    ok = scale > 0
    res["mase"] = float((err[ok] / scale[ok]).mean()) if ok.any() else float("nan")
    if m["p10"].notna().all() and m["p90"].notna().all():
        res["pinball"] = float(np.mean([pinball(y, m["p10"], 0.1), pinball(y, p50, 0.5), pinball(y, m["p90"], 0.9)]))
        res["coverage80"] = coverage80(y, m["p10"], m["p90"])
    else:  # median-only model (e.g. Croston): pinball at q=0.5 only, no interval coverage
        res["pinball"] = pinball(y, p50, 0.5)
        res["coverage80"] = float("nan")
    return res


def run_backtest(
    df: pd.DataFrame,
    models: list[str] | None = None,
    h: int = 28,
    folds: int = 3,
    policy_params: dict[str, SeriesParams] | None = None,
    cost_cfg: CostConfig | None = None,
) -> BacktestResult:
    """df: unique_id, ds, y + covariates (see features.add_covariates). Returns per-fold metrics + forecasts."""
    models = models or ALL_MODELS
    result = BacktestResult()
    frames: list[pd.DataFrame] = []
    # Anchor on the common data end (most frequent series end date): later partial rows (e.g. live POS sales
    # for "today") must not shift the folds past everyone's history.
    as_of = df.groupby("unique_id")["ds"].max().mode().iloc[0]
    df = df[df["ds"] <= as_of]
    cat_of = df.groupby("unique_id")["category"].first() if "category" in df.columns else None
    prev_cal: dict[str, pd.DataFrame] = {}
    for f in make_folds(as_of, h, folds):
        train = df[df["ds"] <= f.cutoff]
        test = df[(df["ds"] >= f.start) & (df["ds"] <= f.end)]
        full = test.groupby("unique_id").size()
        ok = set(full[full == h].index) & set(
            train.groupby("unique_id").size().loc[lambda n: n >= MIN_TRAIN_DAYS].index
        )
        n_skipped = int(df["unique_id"].nunique() - len(ok))
        train, test = train[train["unique_id"].isin(ok)], test[test["unique_id"].isin(ok)]
        future = test.drop(columns=["y"])  # known-future covariates only: the target never reaches a model
        recent = train.sort_values("ds").groupby("unique_id").tail(7).groupby("unique_id")["y"].mean()
        start_stock = {
            u: float(recent.get(u, 0.0)) * (sp.lead_days + sp.review_days) for u, sp in (policy_params or {}).items()
        }  # model-independent opening stock: recent demand x protection period
        entry: dict[str, Any] = {
            "fold": f.index,
            "cutoff": str(f.cutoff.date()),
            "start": str(f.start.date()),
            "end": str(f.end.date()),
            "series": len(ok),
            "series_skipped": n_skipped,
            "models": {},
        }
        preds: dict[str, pd.DataFrame] = {}
        stat = [m for m in models if m in STAT_MODELS]
        t0 = time.perf_counter()
        if stat:
            preds.update(run_stat(train, h, stat))
            entry["stat_seconds"] = round(time.perf_counter() - t0, 2)
        if "lightgbm_tweedie" in models:
            t0 = time.perf_counter()
            preds["lightgbm_tweedie"] = run_lgbm(train, future, h)
            entry["lightgbm_seconds"] = round(time.perf_counter() - t0, 2)
        if "tft" in models:
            from kalpa_ml.models.tft.runner import run_tft

            t0 = time.perf_counter()
            preds["tft"] = run_tft(train, future, h)
            entry["tft_seconds"] = round(time.perf_counter() - t0, 2)
        for name, fc in preds.items():
            entry["models"][name] = score(test, fc, train)
            scored = fc.merge(test[["unique_id", "ds", "y"]], on=["unique_id", "ds"])
            if "category" in df.columns:
                scored["category"] = scored["unique_id"].map(cat_of)
            # Conformal calibration: this fold is calibrated ONLY with the previous fold's residuals (no leakage).
            if name in prev_cal and scored["p10"].notna().all():
                scored = apply_conformal(scored, fit_conformal(prev_cal[name], "category"), "category")
                inside = (scored["y"] >= scored["p10_cal"]) & (scored["y"] <= scored["p90_cal"])
                entry["models"][name]["coverage80_cal"] = float(inside.mean())
            else:
                entry["models"][name]["coverage80_cal"] = float("nan")
            # Decision-focused evaluation: replay this model's reorder policy on the held-out fold, using the
            # previous fold's residuals for safety stock (nothing from this fold's actuals reaches the policy).
            replay_keys = ("cost", "fill_rate", "stockout_days", "avg_inventory")
            if policy_params and name in prev_cal:
                rf = replay_fold(
                    scored,
                    policy_params,
                    start_stock,
                    cost_cfg or CostConfig(),
                    safety_table(prev_cal[name], "category"),
                )
                entry["models"][name].update(rf)
            else:
                entry["models"][name].update(dict.fromkeys(replay_keys, float("nan")))
            if scored["p10"].notna().all():
                prev_cal[name] = scored
            frames.append(scored.assign(fold=f.index, model=name))
        result.folds.append(entry)
    for name, cal in prev_cal.items():  # final deltas: used to calibrate true future forecasts
        result.deltas[name] = fit_conformal(cal, "category")
        result.safety[name] = safety_table(cal, "category")
    result.forecasts = pd.concat(frames, ignore_index=True)
    return result


def summarise(res: BacktestResult) -> dict[str, dict[str, float]]:
    """Mean of each metric over folds, per model."""
    out: dict[str, dict[str, float]] = {}
    for name in res.folds[0]["models"]:
        keys = res.folds[0]["models"][name].keys()
        out[name] = {k: float(np.nanmean([f["models"][name][k] for f in res.folds])) for k in keys}
    return out


def future_frame(df: pd.DataFrame, as_of: pd.Timestamp, h: int) -> pd.DataFrame:
    """Known-future covariates for the h days after `as_of`.

    Assumptions (documented, not hidden): no promotion is planned (promo = 0) and the price stays at its last
    observed value. Calendar and festival features are exact (they depend only on the date).
    """
    last = df[df["ds"] <= as_of].sort_values("ds").groupby("unique_id").tail(1).set_index("unique_id")
    days = pd.date_range(as_of + pd.Timedelta(days=1), periods=h, freq="D")
    fut = pd.MultiIndex.from_product([last.index, days], names=["unique_id", "ds"]).to_frame(index=False)
    for col, fill in (("price", None), ("promo", 0)):
        if col in df.columns:
            fut[col] = fut["unique_id"].map(last[col]) if fill is None else fill
    return fut


def forecast_future(
    df_cov: pd.DataFrame,
    festival_dates: pd.Series,
    models: list[str] | None = None,
    h: int = 28,
    deltas: dict[str, dict[str, float]] | None = None,
    save_dir: str | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Fit on all history and forecast the next h days. Returns (model, unique_id, ds, p10, p50, p90) and as_of.

    df_cov must already carry covariates (features.add_covariates); future covariates are rebuilt here so
    price_ratio of the first future days still uses the real price history.
    """
    from kalpa_ml.features import add_covariates

    models = models or ALL_MODELS
    as_of = df_cov.groupby("unique_id")["ds"].max().mode().iloc[0]
    hist = df_cov[df_cov["ds"] <= as_of]
    fut = future_frame(hist, as_of, h)
    base_cols = [c for c in ("unique_id", "ds", "price", "promo") if c in hist.columns]
    both = add_covariates(pd.concat([hist[base_cols], fut], ignore_index=True), festival_dates)
    future = both[both["ds"] > as_of]
    eligible = set(hist.groupby("unique_id").size().loc[lambda n: n >= MIN_TRAIN_DAYS].index)
    train = hist[hist["unique_id"].isin(eligible)]
    future = future[future["unique_id"].isin(eligible)]
    preds: dict[str, pd.DataFrame] = {}
    stat = [m for m in models if m in STAT_MODELS]
    if save_dir:  # context a what-if scenario needs: the future covariates and the festival calendar
        d = Path(save_dir).parent
        d.mkdir(parents=True, exist_ok=True)
        future.to_pickle(d / "future_cov.pkl")
        pd.Series(pd.to_datetime(festival_dates)).to_pickle(d / "fest.pkl")
    if stat:
        preds.update(run_stat(train, h, stat))
    if "lightgbm_tweedie" in models:
        preds["lightgbm_tweedie"] = run_lgbm(train, future, h, save_dir)
    if "tft" in models:
        from kalpa_ml.models.tft.runner import run_tft

        tft_frame, expl, tft_ids, known_cols = run_tft(train, future, h, return_explanations=True)
        preds["tft"] = tft_frame
        if save_dir:  # VSN weights + attention for the Explain panel (Phase 7), one entry per series
            import json

            from kalpa_ml.explain import summarise_tft

            summary = summarise_tft(expl, tft_ids, known_cols, expl["attention"].shape[1] - h)
            (Path(save_dir).parent / "tft_explanations.json").write_text(json.dumps(summary), encoding="utf-8")
    out = pd.concat([fc.assign(model=name) for name, fc in preds.items()], ignore_index=True)
    if deltas and "category" in hist.columns:  # conformal bands from the backtest residuals (see run_backtest)
        cat_of = hist.groupby("unique_id")["category"].first()
        out["category"] = out["unique_id"].map(cat_of)
        out = pd.concat(
            [
                apply_conformal(g, deltas[str(name)], "category")
                if str(name) in deltas and g["p10"].notna().all()
                else g
                for name, g in out.groupby("model")
            ],
            ignore_index=True,
        )
    return out, as_of
