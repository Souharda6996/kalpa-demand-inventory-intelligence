"""Run the baseline backtest and write artifacts/benchmarks/baselines.json (+ MLflow run).

    uv run python -m kalpa_ml.run_baselines            # synthetic demo data (this repo's default)
Every number in the JSON is produced here; nothing is hand-entered.
"""

import argparse
import json
import math
import platform
import time
from pathlib import Path
from typing import Any

import lightgbm
import mlforecast
import polars as pl
import statsforecast

from kalpa_ml.backtest import ALL_MODELS, run_backtest, summarise
from kalpa_ml.data.synth import SynthConfig, generate, load_festivals
from kalpa_ml.features import add_covariates
from kalpa_ml.frames import to_long
from kalpa_ml.registry import log_run


def _clean(o: object) -> object:
    """NaN -> null so the file is valid JSON for browsers."""
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/benchmarks/baselines.json")
    ap.add_argument("--per-category", type=int, default=10)
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--no-mlflow", action="store_true")
    a = ap.parse_args()

    cfg = SynthConfig(
        start="2024-01-01", end="2025-12-31", categories={c: a.per_category for c in SynthConfig().categories}
    )
    data = generate(cfg)
    sales = (
        data["sales"]
        .join(data["catalog"].select("sku", "category"), on="sku")
        .with_columns(pl.col("promo").cast(pl.Int8))
    )
    df = add_covariates(to_long(sales), load_festivals()["date"].to_pandas())
    t0 = time.perf_counter()
    res = run_backtest(df, models=ALL_MODELS, h=28, folds=a.folds)
    summary = summarise(res)
    out: dict[str, Any] = {
        "dataset": {
            "name": "Bengaluru FreshMart (SYNTHETIC, seed 42)",
            "is_m5": False,
            "series": int(df["unique_id"].nunique()),
            "rows": int(len(df)),
            "start": cfg.start,
            "end": cfg.end,
            "note": "M5 was not available when this was generated; do not cite as M5 results.",
        },
        "protocol": {
            "horizon_days": 28,
            "folds": a.folds,
            "gap": 0,
            "metrics": ["wape", "mase", "bias", "pinball", "coverage80"],
        },
        "folds": res.folds,
        "summary_mean_over_folds": summary,
        "sanity": {
            "lightgbm_beats_seasonal_naive_wape": summary["lightgbm_tweedie"]["wape"]
            < summary["seasonal_naive"]["wape"]
        },
        "runtime_seconds": round(time.perf_counter() - t0, 1),
        "versions": {
            "python": platform.python_version(),
            "statsforecast": statsforecast.__version__,
            "mlforecast": mlforecast.__version__,
            "lightgbm": lightgbm.__version__,
        },
    }
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(out), indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("sanity:", out["sanity"], "| runtime:", out["runtime_seconds"], "s")
    if not a.no_mlflow:
        flat = {f"{m}.{k}": v for m, ms in summary.items() for k, v in ms.items()}
        rid = log_run(
            "baselines",
            "synthetic-3fold",
            {"folds": a.folds, "series": out["dataset"]["series"], "dataset": "synthetic"},
            flat,
            [path],
        )
        print("mlflow run:", rid)


if __name__ == "__main__":
    main()
