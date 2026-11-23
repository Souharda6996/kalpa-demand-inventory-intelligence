import polars as pl
import pytest

from kalpa_ml.data.validate import ValidationFailed, curate_sales, quality_report, validate_sales


def _good() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "location": ["S1", "S1", "S1"],
            "sku": ["A", "A", "A"],
            "date": ["2024-01-01", "2024-01-02", "2024-01-04"],
            "units": [1.0, 2.0, 4.0],
        }
    )


def test_valid_frame_passes() -> None:
    assert validate_sales(_good()).height == 3


def test_negative_units_rejected_with_readable_message() -> None:
    bad = _good().with_columns(units=pl.Series([1.0, -5.0, 4.0]))
    with pytest.raises(ValidationFailed) as e:
        validate_sales(bad)
    assert any("units" in p for p in e.value.problems)


def test_bad_date_rejected() -> None:
    bad = _good().with_columns(date=pl.Series(["2024-01-01", "not-a-date", "2024-01-04"]))
    with pytest.raises(ValidationFailed):
        validate_sales(bad)


def test_missing_column_rejected() -> None:
    with pytest.raises(ValidationFailed, match="missing required"):
        validate_sales(_good().drop("sku"))


def test_curate_zero_fills_gap_and_report_counts_it() -> None:
    v = validate_sales(_good())
    c = curate_sales(v)
    assert c.height == 4 and c.filter(pl.col("date") == pl.date(2024, 1, 3))["units"][0] == 0.0
    r = quality_report(v, v)
    assert r.issues["date_gaps"] == 1 and r.rows_in == 3
