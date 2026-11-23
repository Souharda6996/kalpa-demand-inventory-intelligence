import numpy as np
import pandas as pd
import pytest

from kalpa_ml.hierarchy import METHODS, coherence_error, reconcile


@pytest.fixture(scope="module")
def data() -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(0)
    rows, fc = [], []
    days = pd.date_range("2024-01-01", periods=140)
    for store in ("A", "B"):
        for cat in ("x", "y"):
            for sku in ("1", "2"):
                uid = f"{store}|{cat}{sku}"
                lam = 5 + 2 * (store == "B") + 3 * (cat == "y") + int(sku)
                for i, d in enumerate(days):
                    rows.append(
                        {
                            "unique_id": uid,
                            "ds": d,
                            "y": float(rng.poisson(lam + np.sin(i / 7 * np.pi * 2))),
                            "category": cat,
                        }
                    )
                for d in pd.date_range(days[-1] + pd.Timedelta(days=1), periods=7):
                    fc.append(
                        {"unique_id": uid, "ds": d, "p50": lam * float(rng.uniform(0.7, 1.3))}
                    )  # deliberately incoherent noise
    return pd.DataFrame(rows), pd.DataFrame(fc)


def test_base_forecasts_are_incoherent_but_reconciled_ones_add_up(data: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    hist, fc = data
    bottom, everything = reconcile(hist, fc, h=7)
    assert bottom["unique_id"].nunique() == 8 and len(bottom) == 8 * 7
    for name in METHODS:
        assert coherence_error(everything, name) < 1e-6, name  # parents == sum of children at every level
    assert coherence_error(everything, "base") > 0.5  # the unreconciled mix of model + naive levels does NOT add up


def test_bottom_up_total_equals_sum_of_model_bottom_forecasts(data: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    hist, fc = data
    _, everything = reconcile(hist, fc, h=7)
    total = everything[everything["unique_id"] == "Total"].set_index("ds")["bottom_up"]
    expected = fc.groupby("ds")["p50"].sum()
    assert np.allclose(total.to_numpy(), expected.reindex(total.index).to_numpy(), atol=1e-6)


def test_mint_changes_bottom_forecasts_and_stays_non_negative(data: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    hist, fc = data
    bottom, _ = reconcile(hist, fc, h=7)
    assert not np.allclose(bottom["mint_shrink"], bottom["base"])  # MinT borrows strength across levels
    assert (bottom[list(METHODS)] >= 0).all().all()
