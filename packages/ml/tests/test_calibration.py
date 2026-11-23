import math

import numpy as np
import pandas as pd

from kalpa_ml.calibration import apply_conformal, conformal_delta, fit_conformal


def test_delta_hand_calculated() -> None:
    # raw band [4, 6]; y = 3 -> score 1 ; 7 -> 1 ; 10 -> 4 ; 5 -> -1 (inside, negative) ; 0 -> 4
    y = np.array([3.0, 7.0, 10.0, 5.0, 0.0])
    # n=5, cov=0.8: k = ceil(6*0.8) = 5 -> the 5th smallest score = 4
    assert conformal_delta(y, np.full(5, 4.0), np.full(5, 6.0)) == 4.0
    # n=10, k = ceil(11*0.8) = 9 -> 9th smallest of scores 0..9 -> 8
    y2 = np.arange(10, dtype=float) + 6.0  # scores = y - 6 = 0..9
    assert conformal_delta(y2, np.full(10, 0.0), np.full(10, 6.0)) == 8.0


def test_calibration_fixes_an_overconfident_band_on_held_out_data() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    y = rng.normal(100, 20, size=2 * n)
    raw_lo, raw_hi = np.full(2 * n, 90.0), np.full(2 * n, 110.0)  # far too narrow (about 38 % coverage)
    cal = pd.DataFrame({"y": y[:n], "p10": raw_lo[:n], "p90": raw_hi[:n]})
    hold = pd.DataFrame({"y": y[n:], "p10": raw_lo[n:], "p50": 100.0, "p90": raw_hi[n:]})
    raw_cov = ((hold["y"] >= hold["p10"]) & (hold["y"] <= hold["p90"])).mean()
    out = apply_conformal(hold, fit_conformal(cal))
    cov = ((out["y"] >= out["p10_cal"]) & (out["y"] <= out["p90_cal"])).mean()
    assert raw_cov < 0.5 and math.isclose(cov, 0.8, abs_tol=0.03)


def test_wide_band_can_shrink_and_bands_stay_valid() -> None:
    rng = np.random.default_rng(1)
    y = rng.normal(50, 5, size=3000)
    cal = pd.DataFrame({"y": y[:1500], "p10": 0.0, "p90": 100.0})  # absurdly wide -> negative delta
    d = fit_conformal(cal)["__all__"]
    assert d < 0
    out = apply_conformal(pd.DataFrame({"y": y[1500:], "p10": 0.0, "p50": 50.0, "p90": 100.0}), {"__all__": d})
    assert (out["p10_cal"] >= 0).all() and (out["p10_cal"] <= out["p50"]).all() and (out["p90_cal"] >= out["p50"]).all()


def test_group_specific_deltas_and_small_group_fallback() -> None:
    rng = np.random.default_rng(2)
    a = pd.DataFrame({"y": rng.normal(0, 1, 500), "p10": -0.3, "p90": 0.3, "g": "a"})
    b = pd.DataFrame({"y": rng.normal(0, 5, 500), "p10": -0.3, "p90": 0.3, "g": "b"})
    tiny = pd.DataFrame({"y": [100.0] * 5, "p10": -0.3, "p90": 0.3, "g": "tiny"})
    d = fit_conformal(pd.concat([a, b, tiny]), "g")
    assert d["b"] > d["a"] > 0 and "tiny" not in d  # noisier group needs a bigger correction; tiny group is pooled
    fc = pd.DataFrame({"p10": [1.0, 1.0], "p50": [2.0, 2.0], "p90": [3.0, 3.0], "g": ["b", "tiny"]})
    out = apply_conformal(fc, d, "g")
    assert math.isclose(out["p90_cal"][0] - 3.0, d["b"]) and math.isclose(out["p90_cal"][1] - 3.0, d["__all__"])
