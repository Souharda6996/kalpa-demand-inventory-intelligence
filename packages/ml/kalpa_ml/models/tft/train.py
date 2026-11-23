"""TFT training (AdamW + OneCycle, gradient clipping, early stopping, deterministic seeds) and inference."""

import random
from dataclasses import dataclass, field

import numpy as np
import torch
from torch.utils.data import DataLoader

from kalpa_ml.models.tft.data import SeriesArrays, WindowDataset, series_scale, train_pairs
from kalpa_ml.models.tft.layers import pinball_loss
from kalpa_ml.models.tft.model import TemporalFusionTransformer, TFTConfig


@dataclass
class TrainConfig:
    epochs: int = 8
    windows_per_epoch: int = 6000
    batch_size: int = 128
    lr: float = 2e-3
    weight_decay: float = 1e-4
    grad_clip: float = 0.5
    patience: int = 3
    val_windows: int = 1500
    seed: int = 0


@dataclass
class Trained:
    model: TemporalFusionTransformer
    scale: np.ndarray
    history: list[dict[str, float]] = field(default_factory=list)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _batch_loss(model: TemporalFusionTransformer, b: dict[str, torch.Tensor], q: list[float]) -> torch.Tensor:
    out = model(b["static"], b["observed"], b["known"])
    return pinball_loss(b["target"].unsqueeze(-1), out["quantiles"], q)


def fit(a: SeriesArrays, cutoff: int, cfg: TFTConfig, tc: TrainConfig | None = None) -> Trained:
    """Train on windows whose targets end at or before `cutoff`; validate on the most recent windows."""
    tc = tc or TrainConfig()
    seed_everything(tc.seed)
    rng = np.random.default_rng(tc.seed)
    scale = series_scale(a.y, cutoff)  # history <= cutoff only
    model = TemporalFusionTransformer(cfg)
    L, H, q = cfg.lookback, cfg.horizon, cfg.quantiles

    # validation = the latest windows (t near cutoff-H); training windows are drawn from the whole past
    t_val = np.arange(max(L - 1, cutoff - H - 6), cutoff - H + 1)
    vp = np.array([(s, t) for t in t_val for s in range(len(a.ids))])
    vp = vp[rng.permutation(len(vp))[: tc.val_windows]]
    val_dl = DataLoader(WindowDataset(a, scale, vp, L, H), batch_size=tc.batch_size)

    steps = tc.epochs * int(np.ceil(tc.windows_per_epoch / tc.batch_size))
    opt = torch.optim.AdamW(model.parameters(), lr=tc.lr, weight_decay=tc.weight_decay)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=tc.lr, total_steps=steps)
    best, best_state, bad, hist = float("inf"), None, 0, []
    for ep in range(tc.epochs):
        model.train()
        pairs = train_pairs(a, cutoff, L, H, tc.windows_per_epoch, rng)
        tr_dl = DataLoader(
            WindowDataset(a, scale, pairs, L, H),
            batch_size=tc.batch_size,
            shuffle=True,
            generator=torch.Generator().manual_seed(tc.seed + ep),
        )
        tot, n = 0.0, 0
        for b in tr_dl:
            opt.zero_grad()
            loss = _batch_loss(model, b, q)
            loss.backward()  # type: ignore[no-untyped-call]
            torch.nn.utils.clip_grad_norm_(model.parameters(), tc.grad_clip)
            opt.step()
            sched.step()
            tot, n = tot + float(loss.detach()) * len(b["target"]), n + len(b["target"])
        model.eval()
        with torch.no_grad():
            vl = float(np.mean([float(_batch_loss(model, b, q)) for b in val_dl]))
        hist.append({"epoch": ep, "train_loss": tot / n, "val_loss": vl})
        if vl < best - 1e-5:
            best, bad, best_state = vl, 0, {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= tc.patience:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    return Trained(model.eval(), scale, hist)


@torch.no_grad()
def predict(
    tr: Trained, a: SeriesArrays, cutoff: int, batch_size: int = 256
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Quantile forecasts [S, H, Q] in original units (sorted, never crossing, clipped at 0) + interpretability."""
    cfg = tr.model.cfg
    pairs = np.array([(s, cutoff) for s in range(len(a.ids))])
    dl = DataLoader(WindowDataset(a, tr.scale, pairs, cfg.lookback, cfg.horizon), batch_size=batch_size)
    qs, st_w, enc_w, dec_w, att = [], [], [], [], []
    for b in dl:
        out = tr.model(b["static"], b["observed"], b["known"])
        qs.append(out["quantiles"].numpy())
        st_w.append(out["static_weights"].numpy())
        enc_w.append(out["encoder_weights"].numpy())
        dec_w.append(out["decoder_weights"].numpy())
        att.append(out["attention"].numpy())
    q = np.sort(np.concatenate(qs), axis=-1) * tr.scale[:, None, None]
    expl = {
        "static": np.concatenate(st_w),
        "encoder": np.concatenate(enc_w),
        "decoder": np.concatenate(dec_w),
        "attention": np.concatenate(att),
    }
    return np.clip(q, 0.0, None), expl
