import math

import numpy as np
import pytest

from kalpa_ml.drift import psi, psi_status, wape, wape_drift


def test_psi_hand_calculated_two_bins() -> None:
    # expected: 5 zeros + 5 ones -> median edge 0.5 -> bins [-inf,.5) [.5,inf); shares 0.5/0.5
    expected = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1], dtype=float)
    actual = np.array([0, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=float)  # shares 0.1 / 0.9
    want = (0.1 - 0.5) * math.log(0.1 / 0.5) + (0.9 - 0.5) * math.log(0.9 / 0.5)  # 0.6438 + 0.2351 by hand
    assert psi(expected, actual, bins=2) == pytest.approx(want)
    assert want == pytest.approx(0.8788898309344878)


def test_identical_samples_have_zero_psi_and_shift_has_large_psi() -> None:
    rng = np.random.default_rng(0)
    x = rng.poisson(8, 5000).astype(float)
    assert psi(x, x) == pytest.approx(0.0, abs=1e-12)
    assert psi(x, rng.poisson(8, 5000).astype(float)) < 0.1  # same distribution, fresh sample: stable
    shifted = rng.poisson(16, 5000).astype(float)  # demand doubled
    assert psi(x, shifted) > 0.2 and psi_status(psi(x, shifted)) == "alert"


def test_psi_is_finite_when_a_bin_is_empty_and_for_constant_training_data() -> None:
    e = np.arange(100, dtype=float)
    assert math.isfinite(psi(e, np.full(50, 1e6)))  # everything beyond the training range
    assert math.isfinite(psi(np.full(40, 3.0), np.full(40, 7.0)))  # constant expected sample
    assert psi(np.full(40, 3.0), np.full(40, 3.0)) == pytest.approx(0.0, abs=1e-12)
    with pytest.raises(ValueError):
        psi(np.array([]), np.array([1.0]))


def test_status_thresholds() -> None:
    assert [psi_status(v) for v in (0.0, 0.1, 0.1001, 0.2, 0.2001)] == ["ok", "ok", "warn", "warn", "alert"]


def test_wape_and_degradation_alert_at_twenty_percent() -> None:
    y = np.array([10.0, 10.0, 10.0, 10.0])
    assert wape(y, np.array([8.0, 12.0, 10.0, 10.0])) == pytest.approx(0.1)  # 4 / 40
    assert math.isnan(wape(np.zeros(3), np.ones(3)))
    # backtest WAPE 0.10: live 0.11 -> ok; 0.115 -> warn; 0.13 -> alert (> 1.2x)
    assert wape_drift(y, np.array([8.9, 10, 10, 10]), 0.10).status == "ok"  # 1.1/40 = 0.0275 -> ratio 0.275
    d = wape_drift(y, y + np.array([4.6, 0, 0, 0]), 0.10)  # 4.6/40 = 0.115 -> ratio 1.15
    assert d.status == "warn" and d.ratio == pytest.approx(1.15)
    assert wape_drift(y, y + np.array([5.2, 0, 0, 0]), 0.10).status == "alert"  # 0.13 -> ratio 1.3
    assert wape_drift(np.zeros(3), np.ones(3), 0.10).status == "unknown"  # no demand: nothing to compare
