import math

import numpy as np

from kalpa_ml.metrics import bias, coverage80, mase, pinball, wape


def test_wape_hand_calculated() -> None:
    # |10-8| + |20-25| + |0-1| = 8 ; sum|y| = 30
    assert math.isclose(wape([10, 20, 0], [8, 25, 1]), 8 / 30)


def test_wape_all_zero_actuals_is_nan() -> None:
    assert math.isnan(wape([0, 0], [1, 2]))


def test_bias_sign_and_value() -> None:
    assert math.isclose(bias([10, 10], [12, 13]), 5 / 20)  # over-forecast is positive
    assert bias([10, 10], [8, 8]) < 0


def test_mase_hand_calculated() -> None:
    insample = [1, 2, 3, 4, 5, 6, 7, 9, 12]  # season 7 diffs: 9-1=8, 12-2=10 -> scale 9
    assert math.isclose(mase([10, 10], [8, 13], insample, season=7), 2.5 / 9)  # mean|err| = (2+3)/2


def test_pinball_hand_calculated() -> None:
    # q=0.9: y=10,yq=8 -> 0.9*2=1.8 ; y=10,yq=12 -> (0.9-1)*(-2)=0.2 ; mean = 1.0
    assert math.isclose(pinball([10, 10], [8, 12], 0.9), 1.0)
    assert math.isclose(pinball([10, 10], [8, 12], 0.5), 1.0)  # q=.5 is MAE/2: (1+1)/2


def test_pinball_is_zero_for_perfect_forecast() -> None:
    assert pinball([1, 2, 3], [1, 2, 3], 0.1) == 0.0


def test_coverage80() -> None:
    assert coverage80([5, 5, 5, 5], [4, 6, 0, 5], [6, 9, 4, 5]) == 0.5  # rows 1 and 4 inside (bounds inclusive)


def test_numpy_inputs() -> None:
    assert wape(np.array([1.0, 1.0]), np.array([1.0, 1.0])) == 0.0
