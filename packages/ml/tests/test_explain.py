from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kalpa_ml.backtest import forecast_future, future_frame
from kalpa_ml.data.synth import load_festivals
from kalpa_ml.explain import (
    build_facts,
    grounded_or_template,
    group_weights,
    is_grounded,
    lgbm_shap,
    numbers_in,
    shap_drivers,
    summarise_tft,
    template_narrative,
)
from kalpa_ml.features import add_covariates
from kalpa_ml.scenario import load_model

FEST = load_festivals()["date"].to_pandas()


def facts() -> dict[str, object]:
    return build_facts(
        "Kaju Katli", "Koramangala", 14, 100.0, 138.0, [("days to the next festival", 0.31), ("the planned promotion", 0.19)],
        ("Diwali", 9), "tft", "11111111-2222-3333-4444-555555555555",
    )  # fmt: skip


def test_numbers_in_parses_indian_grouping_and_ignores_quantile_names() -> None:
    assert numbers_in("Cost ₹1,25,875 and 38% (P90 is 140.5)") == [125875.0, 38.0, 140.5]
    assert numbers_in("P10 P90 lag7 abc123") == []  # digits glued to letters are names, not figures


def test_template_matches_the_spec_example_and_is_grounded() -> None:
    f = facts()
    t = template_narrative(f)
    assert t == (
        "Demand for Kaju Katli at Koramangala is expected to rise by 38% over the next 14 days "
        "(138 units against 100 in the previous 14 days), mainly driven by days to the next festival (31% weight) "
        "and the planned promotion (19% weight). Diwali falls 9 days after the last observed day."
    )
    assert is_grounded(t, f)


def test_injected_fake_number_is_rejected_and_template_used() -> None:
    f = facts()
    fake = template_narrative(f).replace("38%", "92%")
    assert not is_grounded(fake, f)
    text, source = grounded_or_template(fake, f)
    assert source == "template" and text == template_narrative(f)
    ok = "Expect about 138 units over 14 days, up 38%."
    assert grounded_or_template(ok, f) == (ok, "llm")
    assert grounded_or_template(None, f)[1] == "template"


def test_run_id_digits_do_not_whitelist_numbers() -> None:
    assert not is_grounded("Sales will be 4444 units", facts())  # 4444 appears only inside the run id


def test_flat_and_no_history_cases_never_divide_by_zero() -> None:
    flat = build_facts("A", "B", 7, 100.0, 101.0, [])
    assert flat["direction"] == "flat" and "roughly flat" in template_narrative(flat)
    none = build_facts("A", "B", 7, 0.0, 50.0, [])
    assert none["change_pct"] is None and "by None" not in template_narrative(none)


def test_group_weights_merges_sin_cos_and_sorts() -> None:
    g = group_weights(["dow_sin", "dow_cos", "promo", "days_to_festival"], [0.1, 0.2, 0.4, 0.3])
    assert g[0][0] == "the planned promotion" and abs(g[0][1] - 0.4) < 1e-9
    assert dict(g)["the weekly pattern"] == pytest.approx(0.3)


def test_summarise_tft_shapes_and_attention_uses_only_lookback_columns() -> None:
    S, L, H, K = 3, 10, 4, 5
    rng = np.random.default_rng(0)
    expl = {
        "static": rng.random((S, 3)),
        "encoder": rng.random((S, L, 1 + K)),
        "decoder": rng.random((S, H, K)),
        "attention": rng.random((S, L + H, L + H)),
    }
    cols = ["a", "b", "c", "d", "e"]
    out = summarise_tft(expl, ["s0", "s1", "s2"], cols, L)
    e = out["s1"]
    assert len(e["static_w"]["w"]) == 3 and e["static_w"]["names"] == ["store", "sku", "category"]
    assert e["encoder_w"]["names"][0] == "units (past)" and len(e["encoder_w"]["w"]) == 1 + K
    assert len(e["decoder_w"]["w"]) == K and len(e["attention"]["w"]) == L
    want = expl["attention"][1, L:, :L].mean(axis=0)
    assert np.allclose(e["attention"]["w"], want, atol=1e-5)


def test_shap_drivers_shares_sum_to_one() -> None:
    agg = pd.DataFrame({"feature": ["dow_sin", "dow_cos", "lag7", "promo"], "mean_abs": [0.1, 0.1, 0.5, 0.3]})
    d = shap_drivers(agg)
    assert d[0][0] == "recent sales level" and abs(sum(w for _, w in d) - 1.0) < 1e-9
    assert shap_drivers(agg.assign(mean_abs=0.0)) == []


@pytest.fixture(scope="module")
def priced() -> pd.DataFrame:
    """Demand driven mostly by price (same construction as test_scenario): price_ratio must be the top SHAP feature."""
    rng = np.random.default_rng(0)
    rows = []
    days = pd.date_range("2024-01-01", periods=330)
    for s in range(6):
        level = 100.0
        for i, d in enumerate(days):
            if i % 7 == 0:
                level = 100.0 * float(rng.uniform(0.7, 1.3))
            rows.append(
                {
                    "unique_id": f"S{s % 2}|K{s}",
                    "ds": d,
                    "price": level,
                    "y": float(rng.poisson(12 * (level / 100.0) ** -2)),
                    "category": "c",
                }
            )
    return add_covariates(pd.DataFrame(rows), FEST)


def test_lgbm_shap_finds_the_price_driver_and_is_deterministic(priced: pd.DataFrame, tmp_path: Path) -> None:
    h = 14
    _, as_of = forecast_future(priced, FEST, models=["lightgbm_tweedie"], h=h, save_dir=str(tmp_path / "mlf"))
    model = load_model(tmp_path / "mlf")
    hist = priced[priced["ds"] <= as_of]
    cov = add_covariates(
        pd.concat([hist[["unique_id", "ds", "price"]], future_frame(hist, as_of, h)], ignore_index=True), FEST
    )
    x = cov[cov["ds"] > as_of]
    ids = sorted(x["unique_id"].unique())[:2]
    a = lgbm_shap(model, x, ids, h)
    assert set(a["unique_id"]) == set(ids)
    top = a.sort_values("mean_abs", ascending=False).groupby("unique_id").head(1)
    assert (top["feature"] == "price_ratio").all(), top
    b = lgbm_shap(model, x, ids, h)
    assert a.equals(b)
