"""Temporal Fusion Transformer (pure PyTorch) with a quantile output head and interpretability outputs."""

from dataclasses import dataclass, field

import torch
from torch import nn

from kalpa_ml.models.tft.layers import (
    GatedLinearUnit,
    GatedResidualNetwork,
    InterpretableMultiHeadAttention,
    VariableSelectionNetwork,
    causal_mask,
)


@dataclass
class TFTConfig:
    static_cardinalities: list[int]  # one embedding per categorical static variable (store, sku, category)
    n_known: int  # known-future real inputs (calendar, festival, promo, price ratio ...)
    n_observed: int = 1  # observed-past real inputs (scaled units ...)
    lookback: int = 56
    horizon: int = 28
    d_model: int = 32
    n_heads: int = 2
    dropout: float = 0.1
    quantiles: list[float] = field(default_factory=lambda: [0.1, 0.5, 0.9])


class TemporalFusionTransformer(nn.Module):
    """forward(static_cat [B,S], observed [B,L,O], known [B,L+H,K]) -> dict with quantiles [B,H,Q] + weights."""

    def __init__(self, cfg: TFTConfig) -> None:
        super().__init__()
        self.cfg = cfg
        d, p = cfg.d_model, cfg.dropout
        self.static_emb = nn.ModuleList(nn.Embedding(c, d) for c in cfg.static_cardinalities)
        self.obs_proj = nn.ModuleList(nn.Linear(1, d) for _ in range(cfg.n_observed))
        self.known_proj = nn.ModuleList(nn.Linear(1, d) for _ in range(cfg.n_known))
        n_static = len(cfg.static_cardinalities)
        self.static_vsn = VariableSelectionNetwork(n_static, d, None, p)
        self.enc_vsn = VariableSelectionNetwork(cfg.n_observed + cfg.n_known, d, d, p)
        self.dec_vsn = VariableSelectionNetwork(cfg.n_known, d, d, p)
        # four static context vectors: variable selection, LSTM h0, LSTM c0, static enrichment
        self.c_select, self.c_enrich, self.c_h, self.c_c = (GatedResidualNetwork(d, d, d, None, p) for _ in range(4))
        self.lstm_enc = nn.LSTM(d, d, batch_first=True)
        self.lstm_dec = nn.LSTM(d, d, batch_first=True)
        self.post_lstm_gate = GatedLinearUnit(d, d, p)
        self.post_lstm_norm = nn.LayerNorm(d)
        self.enrich = GatedResidualNetwork(d, d, d, d, p)
        self.attn = InterpretableMultiHeadAttention(d, cfg.n_heads, p)
        self.post_attn_gate = GatedLinearUnit(d, d, p)
        self.post_attn_norm = nn.LayerNorm(d)
        self.pos_grn = GatedResidualNetwork(d, d, d, None, p)
        self.pre_out_gate = GatedLinearUnit(d, d, p)
        self.pre_out_norm = nn.LayerNorm(d)
        self.head = nn.Linear(d, len(cfg.quantiles))

    def forward(self, static_cat: torch.Tensor, observed: torch.Tensor, known: torch.Tensor) -> dict[str, torch.Tensor]:
        cfg = self.cfg
        L, H = cfg.lookback, cfg.horizon
        B = static_cat.shape[0]
        s_in = torch.stack([emb(static_cat[:, i]) for i, emb in enumerate(self.static_emb)], dim=1)  # [B,S,d]
        s_sel, static_w = self.static_vsn(s_in)
        c_s, c_e = self.c_select(s_sel), self.c_enrich(s_sel)
        h0, c0 = self.c_h(s_sel).unsqueeze(0).contiguous(), self.c_c(s_sel).unsqueeze(0).contiguous()

        obs = torch.stack([proj(observed[..., i : i + 1]) for i, proj in enumerate(self.obs_proj)], dim=2)  # [B,L,O,d]
        kn = torch.stack([proj(known[..., i : i + 1]) for i, proj in enumerate(self.known_proj)], dim=2)  # [B,L+H,K,d]
        enc_in, enc_w = self.enc_vsn(torch.cat([obs, kn[:, :L]], dim=2), c_s.unsqueeze(1).expand(B, L, -1))
        dec_in, dec_w = self.dec_vsn(kn[:, L:], c_s.unsqueeze(1).expand(B, H, -1))

        enc_out, (h, c) = self.lstm_enc(enc_in, (h0, c0))
        dec_out, _ = self.lstm_dec(dec_in, (h, c))
        seq_in = torch.cat([enc_in, dec_in], dim=1)
        temporal = self.post_lstm_norm(seq_in + self.post_lstm_gate(torch.cat([enc_out, dec_out], dim=1)))
        enriched = self.enrich(temporal, c_e.unsqueeze(1).expand(B, L + H, -1))

        mask = causal_mask(L + H, device=enriched.device)
        att_out, attn_w = self.attn(enriched, enriched, enriched, mask=mask)
        att = self.post_attn_norm(enriched + self.post_attn_gate(att_out))
        out = self.pos_grn(att)
        final = self.pre_out_norm(temporal + self.pre_out_gate(out))
        return {
            "quantiles": self.head(final[:, L:]),
            "static_weights": static_w,
            "encoder_weights": enc_w,
            "decoder_weights": dec_w,
            "attention": attn_w,
        }
