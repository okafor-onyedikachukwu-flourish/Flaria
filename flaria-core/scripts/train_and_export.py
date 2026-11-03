#!/usr/bin/env python3
"""Train CrossAttentionFusion on synthetic cohorts and export ONNX.

    python scripts/train_and_export.py --subjects 480 --epochs 30 --out models/

Pipeline
  1. Simulate per-subject 30 Hz feature timelines (15-D acoustic held at the
     10 Hz audio hop, 5-D facial geometry at 30 Hz) with realistic structure:
     speech/pause alternation, syllabic articulation driving both lip aperture
     and acoustic energy with a small lag, blinks, head motion, occlusions and
     healthy confounders (loud speech, head turns, smiles, yawns, noisy light).
  2. Pathological subjects develop a progressive drift after a healthy
     baseline period: neuro-motor (lip-corner droop, slowed/weakened
     audio-visual coupling, slurred spectra), respiratory (short phrases,
     breathy spectra, breathing-locked head motion, mouth breathing) or mixed.
  3. Each subject is z-scored with the *same* BaselineCalibrator used in
     production, fitted on that subject's baseline segment only.
  4. AdamW + BCEWithLogitsLoss (class-balanced), cosine schedule, gradient
     clipping, modality-dropout augmentation, subject-level splits, early
     stopping on validation AUROC.
  5. Thresholds from the healthy validation distribution (95th / 99th
     percentile), ONNX export with dynamic batch and sequence axes, numerical
     parity check against ONNX Runtime, SHA-256 metadata.

IMPORTANT: a model trained on synthetic data demonstrates and tests the
pipeline end to end. It has no clinical validity. Replace the generator with
labelled, consented, IRB-approved recordings and a locked validation protocol
before any clinical use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from scipy.signal import lfilter
from scipy.stats import rankdata
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

CORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE_ROOT))

from app.ml.model import CrossAttentionFusion  # noqa: E402
from app.services.calibration import AUDIO_STD_FLOOR, VISION_STD_FLOOR, BaselineCalibrator  # noqa: E402

TICK_HZ = 30
AUDIO_HOLD = 3  # audio features refresh every 100 ms -> held for 3 ticks at 30 Hz
log = logging.getLogger("train")


# ============================================================================ synthetic cohort
@dataclass(frozen=True)
class SynthConfig:
    seconds: float = 60.0
    baseline_fraction: float = 0.3
    seq_len: int = 48
    stride: int = 24


def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float) -> np.ndarray:
    return lfilter([1.0], [1.0, -phi], rng.normal(0.0, sigma * math.sqrt(1 - phi * phi), n))


def _segments(rng: np.random.Generator, n: int, speech_s: tuple[float, float], pause_s: tuple[float, float]) -> np.ndarray:
    speaking = np.zeros(n, dtype=bool)
    i, talk = 0, bool(rng.random() < 0.7)
    while i < n:
        lo, hi = speech_s if talk else pause_s
        length = max(3, int(rng.uniform(lo, hi) * TICK_HZ))
        speaking[i : i + length] = talk
        i += length
        talk = not talk
    return speaking


def _shift(x: np.ndarray, lag: np.ndarray) -> np.ndarray:
    """Delay x by a (possibly time-varying, fractional) number of ticks."""
    idx = np.arange(x.size) - lag
    return np.interp(idx, np.arange(x.size), x, left=x[0], right=x[-1])


def _event_mask(rng: np.random.Generator, n: int, rate_per_min: float, dur_s: tuple[float, float]) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    count = rng.poisson(rate_per_min * n / TICK_HZ / 60.0)
    for _ in range(count):
        start = int(rng.integers(0, n))
        mask[start : start + int(rng.uniform(*dur_s) * TICK_HZ)] = True
    return mask


def simulate_subject(rng: np.random.Generator, cfg: SynthConfig, kind: str, severity: float):
    """Return audio (N,15), audio_valid (N,), vision (N,5), vision_valid (N,), drift d(t) (N,)."""
    n = int(cfg.seconds * TICK_HZ)
    t = np.arange(n) / TICK_HZ
    onset = int(n * cfg.baseline_fraction) + int(rng.uniform(0, 0.15) * n)
    ramp = max(1, int(rng.uniform(8, 25) * TICK_HZ))
    d = np.zeros(n)
    if kind != "healthy":
        d[onset:] = np.clip((np.arange(n - onset)) / ramp, 0, 1) * severity
    neuro = d * (1.0 if kind == "neuro" else 0.6 if kind == "mixed" else 0.0)
    resp = d * (1.0 if kind == "respiratory" else 0.6 if kind == "mixed" else 0.0)

    # --- speech activity: respiratory drift shortens phrases and lengthens pauses
    speaking = _segments(rng, n, (1.5, 4.0), (0.3, 1.2))
    if resp.max() > 0:
        breath_breaks = _event_mask(rng, n, 40.0, (0.3, 0.9)) & (rng.random(n) < resp)
        speaking &= ~breath_breaks

    syl_rate = rng.uniform(3.5, 5.5)
    phase = rng.uniform(0, 2 * np.pi)
    artic = np.log1p(np.exp(2.0 * np.sin(2 * np.pi * syl_rate * t + phase) + _ar1(rng, n, 0.8, 0.8)))
    artic *= speaking * (1.0 - 0.45 * neuro)  # weaker articulation with neuro-motor drift
    artic += _ar1(rng, n, 0.6, 0.05)

    # --- healthy confounders
    loud = _event_mask(rng, n, 3.0, (1.5, 4.0)) & speaking
    turn = _event_mask(rng, n, 2.0, (0.8, 2.0))
    smile = _event_mask(rng, n, 2.0, (1.0, 3.0))
    yawn = _event_mask(rng, n, 0.5, (1.5, 3.0))
    noisy_light = _event_mask(rng, n, 2.0, (1.0, 4.0))
    occluded = _event_mask(rng, n, 1.5, (0.4, 2.0))

    # --- acoustic 15-D (subject-specific baseline + coupling)
    base_mfcc = rng.normal(0, 6, 13)
    base_mfcc[0] = rng.normal(-180, 15)
    weights = rng.normal(0, 3, 13)
    weights[0] = rng.uniform(25, 40)
    energy = lfilter([0.3], [1, -0.7], artic) * (1.0 + 0.5 * loud) * (1.0 - 0.35 * resp)
    mfcc = base_mfcc[None, :] + energy[:, None] * weights[None, :] * (1.0 - 0.5 * neuro)[:, None]
    mfcc_noise_sigma = 1.2 * (1.0 + 0.8 * neuro + 0.6 * resp)
    mfcc += np.stack([_ar1(rng, n, 0.7, 1.0) for _ in range(13)], axis=1) * mfcc_noise_sigma[:, None]
    centroid = rng.normal(1400, 220) + 300 * energy - 160 * neuro + 260 * resp + _ar1(rng, n, 0.6, 60)
    zcr = rng.normal(0.07, 0.012) + 0.02 * energy + 0.025 * resp + _ar1(rng, n, 0.6, 0.004)
    audio = np.column_stack([mfcc, centroid, zcr])
    audio_valid = speaking.copy()
    # sample-and-hold at the 10 Hz audio hop
    hold_idx = (np.arange(n) // AUDIO_HOLD) * AUDIO_HOLD
    audio, audio_valid = audio[hold_idx], audio_valid[hold_idx]

    # --- visual 5-D
    lag = rng.uniform(1.0, 2.5) + 6.0 * neuro  # ticks; coordination slows with neuro drift
    mouth_drive = _shift(artic, lag) * (1.0 - 0.6 * neuro)
    light_gain = 1.0 + 1.5 * noisy_light
    lip_vertical = (rng.normal(0.02, 0.005) + 0.09 * mouth_drive + 0.05 * smile + 0.35 * yawn
                    + 0.04 * resp * ~speaking + _ar1(rng, n, 0.5, 0.004) * light_gain)
    yaw = rng.normal(0, 4) + _ar1(rng, n, 0.995, 3.0) + 18.0 * turn * rng.choice([-1, 1])
    pitch = (rng.normal(0, 4) + _ar1(rng, n, 0.995, 2.5) + 2.5 * resp * np.sin(2 * np.pi * 0.35 * t))
    lip_asym = (abs(rng.normal(0.03, 0.012)) + 0.055 * neuro + 0.01 * smile + 0.0012 * np.abs(yaw)
                + 0.012 * neuro * mouth_drive + np.abs(_ar1(rng, n, 0.5, 0.004)) * light_gain)
    ear = rng.normal(0.28, 0.025) - 0.04 * neuro + _ar1(rng, n, 0.5, 0.008) * light_gain
    blink_rate = 15.0 * (1.0 + 0.5 * (neuro.max() > 0))
    blinks = _event_mask(rng, n, blink_rate, (0.12, 0.25))
    ear = np.where(blinks, 0.09 + rng.normal(0, 0.01, n), ear)
    vision = np.column_stack([lip_asym, lip_vertical, ear, pitch, yaw])
    vision_valid = ~occluded & (np.abs(yaw) <= 30) & (np.abs(pitch) <= 25)
    return audio.astype(np.float32), audio_valid, vision.astype(np.float32), vision_valid, d


def calibrate_subject(audio, a_valid, vision, v_valid, baseline_end: int) -> tuple[np.ndarray, np.ndarray]:
    """Z-score with the production BaselineCalibrator fitted on the baseline segment."""
    out = []
    for x, valid, dim, floor in ((audio, a_valid, 15, AUDIO_STD_FLOOR), (vision, v_valid, 5, VISION_STD_FLOOR)):
        obs = x[:baseline_end][valid[:baseline_end]]
        cal = BaselineCalibrator(dim, None, abs_floor=floor, warmup_samples=max(1, len(obs)))
        for row in obs:
            cal.observe(row, stable=True)
        ref = cal.reference()
        z = (x - ref.mean) / ref.std(cal.abs_floor, cal.rel_floor)
        z = np.clip(z, -cal.z_clip, cal.z_clip).astype(np.float32)
        z[~valid] = 0.0  # missing modality -> neutral, exactly as in production
        out.append(z)
    return out[0], out[1]


def build_cohort(rng: np.random.Generator, cfg: SynthConfig, n_subjects: int):
    kinds = np.array(["healthy"] * (n_subjects // 2)
                     + list(rng.choice(["neuro", "respiratory", "mixed"], n_subjects - n_subjects // 2)))
    rng.shuffle(kinds)
    xa, xv, ys, subj = [], [], [], []
    for sid, kind in enumerate(kinds):
        severity = rng.uniform(0.35, 1.0)
        audio, a_valid, vision, v_valid, d = simulate_subject(rng, cfg, str(kind), severity)
        baseline_end = int(len(d) * cfg.baseline_fraction)
        za, zv = calibrate_subject(audio, a_valid, vision, v_valid, baseline_end)
        for start in range(baseline_end, len(d) - cfg.seq_len + 1, cfg.stride):
            stop = start + cfg.seq_len
            drift = float(d[start:stop].mean())
            if kind == "healthy":
                label = 0.0
            elif drift >= 0.35:
                label = 1.0
            elif drift <= 0.02:
                label = 0.0  # pathological subject still pre-onset: indistinguishable from healthy
            else:
                continue  # ambiguous transition windows are excluded
            xa.append(za[start:stop]); xv.append(zv[start:stop]); ys.append(label); subj.append(sid)
    return (np.stack(xa), np.stack(xv), np.asarray(ys, np.float32), np.asarray(subj), kinds)


# ============================================================================ metrics
def auroc(y: np.ndarray, p: np.ndarray) -> float:
    pos, neg = y == 1, y == 0
    if pos.sum() == 0 or neg.sum() == 0:
        return float("nan")
    ranks = rankdata(p)
    return float((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * neg.sum()))


@torch.no_grad()
def predict(model: nn.Module, loader: DataLoader, device: torch.device) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    probs, labels = [], []
    for a, v, y in loader:
        logits = model(a.to(device), v.to(device)).squeeze(-1)
        probs.append(torch.sigmoid(logits).cpu().numpy())
        labels.append(y.numpy())
    return np.concatenate(labels), np.concatenate(probs)


# ============================================================================ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subjects", type=int, default=480)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--seq-len", type=int, default=48)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--weight-decay", type=float, default=1e-2)
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--out", type=Path, default=CORE_ROOT / "models")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rng = np.random.default_rng(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cfg = SynthConfig(seconds=args.seconds, seq_len=args.seq_len, stride=max(4, args.seq_len // 2))

    t0 = time.time()
    xa, xv, y, subj, kinds = build_cohort(rng, cfg, args.subjects)
    log.info("cohort: %d windows from %d subjects (%.1f%% positive) in %.1fs",
             len(y), args.subjects, 100 * y.mean(), time.time() - t0)

    # Subject-level split: no subject contributes windows to two splits.
    order = rng.permutation(args.subjects)
    n_train, n_val = int(0.7 * args.subjects), int(0.15 * args.subjects)
    split_of = np.empty(args.subjects, dtype=np.int8)
    split_of[order[:n_train]] = 0
    split_of[order[n_train : n_train + n_val]] = 1
    split_of[order[n_train + n_val :]] = 2
    split = split_of[subj]

    def loader(mask: np.ndarray, shuffle: bool) -> DataLoader:
        ds = TensorDataset(torch.from_numpy(xa[mask]), torch.from_numpy(xv[mask]), torch.from_numpy(y[mask]))
        return DataLoader(ds, batch_size=args.batch_size, shuffle=shuffle, drop_last=False)

    train_dl, val_dl, test_dl = loader(split == 0, True), loader(split == 1, False), loader(split == 2, False)
    y_train = y[split == 0]
    pos_weight = torch.tensor([(y_train == 0).sum() / max(1, (y_train == 1).sum())], device=device)

    model = CrossAttentionFusion().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs * len(train_dl))
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    log.info("model parameters: %d on %s", sum(p.numel() for p in model.parameters()), device)

    best_auc, best_state, stale = -1.0, None, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        running, seen = 0.0, 0
        for a, v, target in train_dl:
            a, v, target = a.to(device), v.to(device), target.to(device)
            # Augmentation: measurement noise and whole-modality dropout (camera/mic loss).
            a = a + 0.05 * torch.randn_like(a)
            v = v + 0.05 * torch.randn_like(v)
            drop = torch.rand(a.size(0), device=device)
            a = a * (drop >= 0.05).float()[:, None, None]
            v = v * ((drop < 0.05) | (drop >= 0.10)).float()[:, None, None]
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(a, v).squeeze(-1), target)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            running += loss.item() * target.size(0)
            seen += target.size(0)
        yv, pv = predict(model, val_dl, device)
        val_auc = auroc(yv, pv)
        log.info("epoch %02d  loss %.4f  val_auroc %.4f", epoch, running / max(1, seen), val_auc)
        if val_auc > best_auc + 1e-4:
            best_auc, stale = val_auc, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= args.patience:
                log.info("early stopping at epoch %d", epoch)
                break

    assert best_state is not None
    model.load_state_dict(best_state)
    model = model.to("cpu").eval()
    cpu = torch.device("cpu")

    # Thresholds from the healthy validation distribution.
    yv, pv = predict(model, val_dl, cpu)
    healthy = pv[yv == 0]
    watch = float(np.clip(np.quantile(healthy, 0.95), 0.05, 0.90))
    elevated = float(np.clip(max(np.quantile(healthy, 0.99), watch + 0.05), watch + 0.02, 0.98))
    yt, pt = predict(model, test_dl, cpu)
    metrics = {
        "val_auroc": round(best_auc, 4),
        "test_auroc": round(auroc(yt, pt), 4),
        "test_sensitivity_at_watch": round(float((pt[yt == 1] >= watch).mean()), 4),
        "test_specificity_at_watch": round(float((pt[yt == 0] < watch).mean()), 4),
        "test_sensitivity_at_elevated": round(float((pt[yt == 1] >= elevated).mean()), 4),
        "test_specificity_at_elevated": round(float((pt[yt == 0] < elevated).mean()), 4),
    }
    log.info("test metrics (synthetic): %s", metrics)

    # ------------------------------------------------------------------ export
    args.out.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out / "flaria_core.onnx"
    meta_path = args.out / "flaria_core.meta.json"
    torch.save({"state_dict": model.state_dict(), "seq_len": args.seq_len}, args.out / "flaria_core.pt")

    dummy_a = torch.zeros(2, args.seq_len, 15)
    dummy_v = torch.zeros(2, args.seq_len, 5)
    torch.onnx.export(
        model, (dummy_a, dummy_v), str(onnx_path),
        input_names=["audio", "vision"], output_names=["logits"],
        dynamic_axes={"audio": {0: "batch", 1: "seq"}, "vision": {0: "batch", 1: "seq"}, "logits": {0: "batch"}},
        opset_version=args.opset, do_constant_folding=True, dynamo=False,
    )

    import onnx
    import onnxruntime as ort

    onnx.checker.check_model(onnx.load(str(onnx_path)))
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    for batch, seq in ((3, args.seq_len), (1, args.seq_len * 2), (5, max(8, args.seq_len // 2))):
        a = torch.randn(batch, seq, 15)
        v = torch.randn(batch, seq, 5)
        with torch.no_grad():
            ref = model(a, v).numpy()
        got = session.run(["logits"], {"audio": a.numpy(), "vision": v.numpy()})[0]
        err = float(np.max(np.abs(ref - got)))
        if err > 1e-4:
            raise SystemExit(f"ONNX parity check failed for batch={batch} seq={seq}: max abs err {err:.2e}")
        log.info("onnx parity ok  batch=%d seq=%d  max_err=%.2e", batch, seq, err)

    sha = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
    meta = {
        "version": f"flaria-core-{datetime.now(timezone.utc):%Y%m%d}-{sha[:8]}",
        "seq_len": args.seq_len,
        "tick_hz": TICK_HZ,
        "audio_dim": 15,
        "vision_dim": 5,
        "threshold_watch": round(watch, 4),
        "threshold_elevated": round(elevated, 4),
        "sha256": sha,
        "opset": args.opset,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "training_data": "synthetic",
        "seed": args.seed,
        "metrics": metrics,
        "intended_use": "Pipeline verification only. Not clinically validated. Not a medical device.",
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("exported %s (%s) and %s", onnx_path, meta["version"], meta_path)


if __name__ == "__main__":
    main()
