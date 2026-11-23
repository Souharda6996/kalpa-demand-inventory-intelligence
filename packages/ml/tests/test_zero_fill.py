import pandas as pd

from kalpa_ml.frames import zero_fill

D = pd.date_range("2025-01-01", periods=6, freq="D")


def frame() -> pd.DataFrame:
    rows = []
    for uid, days in {"A": range(6), "B": (0, 1, 3, 4, 5), "C": (0, 1, 2, 3, 4)}.items():
        for i in days:
            rows.append({"unique_id": uid, "ds": D[i], "y": float(i + 1), "price": 10.0 + i, "category": "c"})
    return pd.DataFrame(rows)


def test_missing_days_become_zero_sales_with_price_carried_forward() -> None:
    # B has no row on day 3 (index 2), C none on day 6 (index 5): both are 'nothing sold'
    out = zero_fill(frame())
    for uid in "ABC":
        s = out[out["unique_id"] == uid]
        assert list(s["ds"]) == list(D)  # one row per day, no duplicates, sorted
    b = out[(out["unique_id"] == "B")].set_index("ds")
    assert b.loc[D[2], "y"] == 0.0 and b.loc[D[2], "price"] == 11.0  # price of the previous day
    assert out[(out["unique_id"] == "C") & (out["ds"] == D[5])]["y"].item() == 0.0
    assert (out[out["unique_id"] == "A"]["y"] == [1, 2, 3, 4, 5, 6]).all()  # real values untouched
    assert (out["category"] == "c").all()
    assert len(out) == 18 and list(out.columns) == list(frame().columns)


def test_a_day_that_is_still_open_is_not_turned_into_false_zeros() -> None:
    f = pd.concat(
        [
            frame(),
            pd.DataFrame(
                [{"unique_id": "A", "ds": D[5] + pd.Timedelta(days=1), "y": 3.0, "price": 9.0, "category": "c"}]
            ),
        ]
    )
    out = zero_fill(f)  # day 7 has 1 of 3 series (33 % < 50 %): the common last day stays day 6
    assert out[out["unique_id"] == "B"]["ds"].max() == D[5]
    assert out[out["unique_id"] == "A"]["ds"].max() == D[5] + pd.Timedelta(days=1)  # A's own row is kept, not dropped
    assert len(out[out["ds"] > D[5]]) == 1


def test_last_day_cap_and_degenerate_inputs() -> None:
    capped = zero_fill(frame(), last_day=D[3])
    assert (
        capped[capped["unique_id"] == "C"]["ds"].max() == D[4]
    )  # C's day-5 row is untouched, nothing added after day 4
    assert len(capped[(capped["unique_id"] == "B") & (capped["ds"] <= D[3])]) == 4  # B filled up to the cap
    empty = frame().iloc[0:0]
    assert zero_fill(empty).empty
    one = frame()[frame()["unique_id"] == "A"]
    assert zero_fill(one).equals(
        one.sort_values(["unique_id", "ds"]).reset_index(drop=True)
    )  # already complete: identical
    assert zero_fill(frame(), min_coverage=1.1).equals(frame())  # nothing qualifies: returned as is
