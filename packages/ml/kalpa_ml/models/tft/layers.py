"""TFT building blocks in pure PyTorch (Lim, Arik, Loeff, Pfister 2021)."""

import math

import torch
from torch import nn


class GatedLinearUnit(nn.Module):
    """GLU(x) = (W1 x) * sigmoid(W2 x), with dropout on the input."""

    def __init__(self, d_in: int, d_out: int, dropout: float = 0.0) -> None:
        super().__init__()
        self.drop = nn.Dropout(dropout)
        self.fc = nn.Linear(d_in, d_out * 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return nn.functional.glu(self.fc(self.drop(x)), dim=-1)


class GatedResidualNetwork(nn.Module):
    """GRN(a, c) = LayerNorm(skip(a) + GLU(W2 ELU(W1 a + W3 c))). The context c is optional."""

    def __init__(
        self, d_in: int, d_hidden: int, d_out: int, context_dim: int | None = None, dropout: float = 0.0
    ) -> None:
        super().__init__()
        self.fc1 = nn.Linear(d_in, d_hidden)
        self.ctx = nn.Linear(context_dim, d_hidden, bias=False) if context_dim else None
        self.fc2 = nn.Linear(d_hidden, d_hidden)
        self.glu = GatedLinearUnit(d_hidden, d_out, dropout)
        self.skip = nn.Linear(d_in, d_out) if d_in != d_out else nn.Identity()
        self.norm = nn.LayerNorm(d_out)

    def forward(self, a: torch.Tensor, c: torch.Tensor | None = None) -> torch.Tensor:
        h = self.fc1(a)
        if self.ctx is not None and c is not None:
            h = h + self.ctx(c)
        h = self.fc2(nn.functional.elu(h))
        out: torch.Tensor = self.norm(self.skip(a) + self.glu(h))
        return out


class VariableSelectionNetwork(nn.Module):
    """Softmax weights over variables from a GRN on the flattened inputs; each variable also gets its own GRN.

    Input  [B, n_vars, d] (static) or [B, T, n_vars, d] (temporal); optional context [B, d] / [B, T, d].
    Output (selected [B, d] or [B, T, d], weights [B, n_vars] or [B, T, n_vars]).
    """

    def __init__(self, n_vars: int, d_model: int, context_dim: int | None = None, dropout: float = 0.0) -> None:
        super().__init__()
        self.n_vars = n_vars
        self.weight_grn = GatedResidualNetwork(n_vars * d_model, d_model, n_vars, context_dim, dropout)
        self.var_grns = nn.ModuleList(
            GatedResidualNetwork(d_model, d_model, d_model, None, dropout) for _ in range(n_vars)
        )

    def forward(self, x: torch.Tensor, context: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor]:
        flat = x.flatten(start_dim=-2)
        weights = torch.softmax(self.weight_grn(flat, context), dim=-1)
        processed = torch.stack([g(x[..., i, :]) for i, g in enumerate(self.var_grns)], dim=-2)
        return (processed * weights.unsqueeze(-1)).sum(dim=-2), weights


def causal_mask(n: int, device: torch.device | None = None) -> torch.Tensor:
    """[n, n] bool, True where attention is FORBIDDEN (key position j is after query position i)."""
    return torch.triu(torch.ones(n, n, dtype=torch.bool, device=device), diagonal=1)


class InterpretableMultiHeadAttention(nn.Module):
    """Multi-head attention with ONE value projection shared by all heads, so averaging heads is meaningful.

    Returns (output [B, Tq, d], attention averaged over heads [B, Tq, Tk]).
    """

    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0) -> None:
        super().__init__()
        self.h, self.d_k = n_heads, d_model // n_heads
        self.q = nn.ModuleList(nn.Linear(d_model, self.d_k) for _ in range(n_heads))
        self.k = nn.ModuleList(nn.Linear(d_model, self.d_k) for _ in range(n_heads))
        self.v = nn.Linear(d_model, self.d_k)  # shared across heads
        self.out = nn.Linear(self.d_k, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(
        self, q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, mask: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor]:
        vv = self.v(v)
        heads, attns = [], []
        for qi, ki in zip(self.q, self.k, strict=True):
            scores = qi(q) @ ki(k).transpose(-2, -1) / math.sqrt(self.d_k)
            if mask is not None:
                scores = scores.masked_fill(mask, float("-inf"))
            a = self.drop(torch.softmax(scores, dim=-1))
            heads.append(a @ vv)
            attns.append(a)
        mean_attn = torch.stack(attns).mean(dim=0)
        return self.out(torch.stack(heads).mean(dim=0)), mean_attn


def pinball_loss(y: torch.Tensor, pred: torch.Tensor, quantiles: list[float]) -> torch.Tensor:
    """Mean pinball loss over batch, horizon and quantiles. y [B, H, 1] (or [B, H]); pred [B, H, Q]."""
    if y.dim() == 2:
        y = y.unsqueeze(-1)
    if pred.shape[-1] != len(quantiles) or pred.shape[:-1] != y.shape[:-1]:
        raise ValueError(f"pred {tuple(pred.shape)} does not match y {tuple(y.shape)} and {len(quantiles)} quantiles")
    q = torch.tensor(quantiles, dtype=pred.dtype, device=pred.device)
    d = y - pred
    return torch.maximum(q * d, (q - 1) * d).mean()
