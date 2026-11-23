"""Run the full Arena backtest (baselines + TFT, conformal calibration) and write artifacts/benchmarks/arena.json.

    uv run python -m kalpa_ml.run_arena --per-category 10 --folds 3
Every number is produced here; nothing is hand-entered. Dataset = synthetic FreshMart (M5 is not bundled).
"""

import argparse
import json
import math
import platform
import time
from pathlib import Path
from typing import Any

import polars as pl
import torch

from kalpa_ml.backtest import run_backtest, summarise
from kalpa_ml.data.synth import SynthConfig, generate, load_festivals
from kalpa_ml.features import add_covariates
from kalpa_ml.frames import to_long
from kalpa_ml.registry import log_run


def _clean(o: object) -> object:
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/benchmarks/arena.json")
    ap.add_argument("--per-category", type=int, default=10)
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--models", default="seasonal_naive,auto_ets,lightgbm_tweedie,tft")
    ap.add_argument("--no-mlflow", action="store_true")
    a = ap.parse_args()
    models = [m for m in a.models.split(",") if m]

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
    res = run_backtest(df, models=models, h=28, folds=a.folds)
    summary = summarise(res)
    out: dict[str, Any] = {
        "dataset": {
            "name": "Bengaluru FreshMart (SYNTHETIC, seed 42)",
            "is_m5": False,
            "series": int(df["unique_id"].nunique()),
            "rows": int(len(df)),
            "start": cfg.start,
            "end": cfg.end,
            "note": "M5 was not available; do not cite as M5 results.",
        },
        "protocol": {
            "horizon_days": 28,
            "folds": a.folds,
            "gap": 0,
            "calibration": "split-conformal per category, "
            "fold k calibrated with fold k-1 residuals (fold 0 uncalibrated)",
        },
        "tft": {
            "lookback": 56,
            "d_model": 32,
            "heads": 2,
            "epochs_max": 8,
            "device": "cpu",
            "torch": torch.__version__,
        },
        "folds": res.folds,
        "summary_mean_over_folds": summary,
        "runtime_seconds": round(time.perf_counter() - t0, 1),
        "python": platform.python_version(),
    }
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(out), indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(_clean(summary), indent=2))
    print("runtime:", out["runtime_seconds"], "s")
    if not a.no_mlflow:
        flat = {f"{m}.{k}": v for m, ms in summary.items() for k, v in ms.items()}
        print(
            "mlflow run:",
            log_run("arena", "synthetic-3fold-tft", {"folds": a.folds, "models": ",".join(models)}, flat, [path]),
        )


if __name__ == "__main__":
    main()
