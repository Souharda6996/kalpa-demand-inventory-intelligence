from datetime import date, timedelta

import polars as pl

from kalpa_ml.data.synth import SynthConfig, generate, load_festivals, make_catalog, render_card

SMALL = SynthConfig(start="2024-01-01", end="2024-12-31", categories={"Sweets": 6, "Beverages": 4})


def test_reproducible_with_same_seed() -> None:
    a, b = generate(SMALL)["sales"], generate(SMALL)["sales"]
    assert a.equals(b)


def test_different_seed_differs() -> None:
    other = SynthConfig(seed=7, start=SMALL.start, end=SMALL.end, categories=SMALL.categories)
    assert not generate(SMALL)["sales"].equals(generate(other)["sales"])


def test_festival_uplift_visible_for_kaju_katli() -> None:
    s = generate(SMALL)["sales"]
    kk = make_catalog(SMALL).filter(pl.col("name") == "Kaju Katli")["sku"][0]
    diwali = load_festivals().filter((pl.col("name") == "Diwali") & (pl.col("date").dt.year() == 2024))["date"][0]
    pre = s.filter((pl.col("sku") == kk) & pl.col("date").is_between(diwali - timedelta(days=3), diwali))[
        "units"
    ].mean()
    base = s.filter((pl.col("sku") == kk) & pl.col("date").is_between(date(2024, 6, 1), date(2024, 6, 30)))[
        "units"
    ].mean()
    assert pre > 1.8 * base  # type: ignore[operator]


def test_intermittent_share_in_range_and_sales_nonnegative() -> None:
    cfg = SynthConfig(categories={"Sweets": 100})
    share = make_catalog(cfg)["intermittent"].mean()
    assert 0.15 <= share <= 0.25  # type: ignore[operator]
    assert generate(SMALL)["sales"]["units"].min() >= 0  # type: ignore[operator]


def test_card_lists_parameters() -> None:
    assert "nb_dispersion" in render_card(SMALL)
