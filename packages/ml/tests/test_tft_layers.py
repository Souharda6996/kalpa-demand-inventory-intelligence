"""Written BEFORE the TFT implementation (Phase 5 rule): shapes, causal mask, pinball loss."""

import math

import pytest
import torch

from kalpa_ml.models.tft.layers import (
    GatedLinearUnit,
    GatedResidualNetwork,
    InterpretableMultiHeadAttention,
    VariableSelectionNetwork,
    causal_mask,
    pinball_loss,
)


def test_glu_shape() -> None:
    assert GatedLinearUnit(8, 5)(torch.randn(3, 4, 8)).shape == (3, 4, 5)


def test_grn_shapes_with_and_without_context_and_skip_projection() -> None:
    x, ctx = torch.randn(2, 6, 8), torch.randn(2, 6, 4)
    assert GatedResidualNetwork(8, 16, 8)(x).shape == (2, 6, 8)
    assert GatedResidualNetwork(8, 16, 12, context_dim=4)(x, ctx).shape == (2, 6, 12)  # in != out -> projected skip


def test_vsn_weights_are_a_distribution_over_variables() -> None:
    vsn = VariableSelectionNetwork(n_vars=5, d_model=8, context_dim=8)
    out, w = vsn(torch.randn(4, 7, 5, 8), torch.randn(4, 7, 8))  # temporal: [B, T, n_vars, d]
    assert out.shape == (4, 7, 8) and w.shape == (4, 7, 5)
    assert torch.allclose(w.sum(-1), torch.ones(4, 7), atol=1e-5) and (w >= 0).all()
    s_out, s_w = VariableSelectionNetwork(n_vars=3, d_model=8)(torch.randn(4, 3, 8))  # static: [B, n_vars, d]
    assert s_out.shape == (4, 8) and s_w.shape == (4, 3)


def test_causal_mask_blocks_the_future() -> None:
    m = causal_mask(4)
    assert m.shape == (4, 4) and m.dtype == torch.bool
    assert m[0, 1] and m[1, 3] and not m[2, 2] and not m[3, 0]  # True = masked (j > i)


def test_attention_weights_are_zero_on_future_positions_and_rows_sum_to_one() -> None:
    torch.manual_seed(0)
    att = InterpretableMultiHeadAttention(d_model=8, n_heads=2, dropout=0.0)
    x = torch.randn(2, 6, 8)
    out, w = att(x, x, x, mask=causal_mask(6))
    assert out.shape == (2, 6, 8) and w.shape == (2, 6, 6)
    assert torch.all(torch.triu(w, diagonal=1) == 0)  # nothing attends to the future
    assert torch.allclose(w.sum(-1), torch.ones(2, 6), atol=1e-5)


def test_no_future_leakage_changing_a_later_input_never_changes_earlier_outputs() -> None:
    torch.manual_seed(1)
    att = InterpretableMultiHeadAttention(d_model=8, n_heads=2, dropout=0.0).eval()
    x = torch.randn(1, 6, 8)
    base, _ = att(x, x, x, mask=causal_mask(6))
    x2 = x.clone()
    x2[:, 4:, :] += 100.0  # perturb positions 4 and 5 only
    changed, _ = att(x2, x2, x2, mask=causal_mask(6))
    assert torch.allclose(base[:, :4], changed[:, :4], atol=1e-5)  # positions 0-3 unaffected
    assert not torch.allclose(base[:, 4:], changed[:, 4:])  # sanity: the perturbation does matter later


def test_pinball_loss_hand_calculated() -> None:
    y = torch.tensor([[[10.0], [10.0]]])  # [B=1, H=2, 1]
    # q=0.9 forecasts 8 (under) and 12 (over): 0.9*2 = 1.8 and (0.9-1)*(-2) = 0.2 -> mean 1.0
    pred = torch.tensor([[[8.0], [12.0]]])
    assert math.isclose(float(pinball_loss(y, pred, [0.9])), 1.0, rel_tol=1e-6)
    # two quantiles are averaged: q=0.1 forecasts 8 and 12 -> 0.1*2=0.2 and (0.1-1)*(-2)=1.8 -> 1.0; mean(1.0, 1.0)
    pred2 = torch.tensor([[[8.0, 8.0], [12.0, 12.0]]])
    assert math.isclose(float(pinball_loss(y, pred2, [0.1, 0.9])), 1.0, rel_tol=1e-6)


def test_pinball_loss_is_zero_when_perfect_and_rejects_shape_mismatch() -> None:
    y = torch.rand(2, 5, 1)
    assert float(pinball_loss(y, y.repeat(1, 1, 3), [0.1, 0.5, 0.9])) == 0.0
    with pytest.raises(ValueError):
        pinball_loss(y, torch.rand(2, 5, 2), [0.1, 0.5, 0.9])
