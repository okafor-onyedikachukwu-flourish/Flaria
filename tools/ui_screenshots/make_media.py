#!/usr/bin/env python3
"""Fake camera and microphone inputs for automated UI capture (Chromium fake devices).

camera.y4m      NASA public-domain astronaut portrait (scikit-image test image),
                with slight head motion so the tracker sees a live-looking feed.
voice_*.wav     synthetic sustained-vowel speech: glottal pulses through three
                formant resonators with syllable and phrase envelopes.
                'healthy' has normal perturbation; 'rough' has raised jitter,
                shimmer and breath noise (as with laryngitis).
Nothing here is a recording of a real voice.
"""
from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
from scipy.ndimage import affine_transform
from scipy.signal import lfilter
from skimage import data

W, H, FPS = 640, 480, 30


def rgb_to_yuv420(frame: np.ndarray) -> bytes:
    f = frame.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    u = -0.168736 * r - 0.331264 * g + 0.5 * b + 128
    v = 0.5 * r - 0.418688 * g - 0.081312 * b + 128
    sub = lambda c: c.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))  # noqa: E731
    planes = [np.clip(p, 0, 255).astype(np.uint8) for p in (y, sub(u), sub(v))]
    return b"".join(p.tobytes() for p in planes)


def make_video(path: Path, seconds: float) -> None:
    src = data.astronaut().astype(np.float32)  # 512x512 RGB, public domain (NASA)
    canvas = np.full((H, W, 3), 24.0, np.float32)
    n = int(seconds * FPS)
    with path.open("wb") as fh:
        fh.write(f"YUV4MPEG2 W{W} H{H} F{FPS}:1 Ip A1:1 C420jpeg\n".encode())
        for i in range(n):
            t = i / FPS
            s = 480 / 512 * (1 + 0.008 * np.sin(2 * np.pi * 0.17 * t))
            dx = 2.5 * np.sin(2 * np.pi * 0.21 * t)
            dy = 1.5 * np.sin(2 * np.pi * 0.13 * t)
            out = canvas.copy()
            ox = (W - 512 * s) / 2 + dx
            for c in range(3):
                # output(row, col) samples source((row - dy)/s, (col - ox)/s)
                out[..., c] = affine_transform(src[..., c], [1 / s, 1 / s], offset=[-dy / s, -ox / s],
                                               output_shape=(H, W), order=1, mode="constant", cval=24.0)
            fh.write(b"FRAME\n" + rgb_to_yuv420(out))


def make_voice(path: Path, seconds: float, *, jitter: float, shimmer: float, noise_db: float, seed: int) -> None:
    sr = 48000
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    src = np.zeros(n)
    t, f0 = 0.0, 128.0
    while t < n:
        period = sr / (f0 * (1 + 0.04 * np.sin(2 * np.pi * 0.3 * t / sr))) * (1 + rng.normal(0, jitter))
        i = int(t)
        if i + 2 < n:
            amp = 1 + rng.normal(0, shimmer)
            src[i] += amp
            src[i + 1] -= 0.6 * amp
        t += period
    x = lfilter([1, -0.97], [1], src)  # glottal-ish spectral tilt
    for fc, bw in ((560, 90), (1480, 120), (2500, 180)):  # vowel formants
        r = np.exp(-np.pi * bw / sr)
        x = lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * fc / sr), r * r], x)
    tt = np.arange(n) / sr
    syllables = 0.55 + 0.45 * np.clip(np.sin(2 * np.pi * 4.0 * tt), 0, None)
    phrases = 0.5 + 0.5 * np.tanh(4 * np.sin(2 * np.pi * 0.28 * tt + 0.5))
    x = x / np.max(np.abs(x)) * syllables * phrases
    x += rng.normal(0, 10 ** (noise_db / 20), n) * phrases
    x = 0.35 * x / np.max(np.abs(x))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("/tmp/flaria-media"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    make_video(args.out / "camera.y4m", 6.0)
    make_voice(args.out / "voice_healthy.wav", 20.0, jitter=0.004, shimmer=0.03, noise_db=-48, seed=1)
    make_voice(args.out / "voice_rough.wav", 20.0, jitter=0.03, shimmer=0.2, noise_db=-24, seed=2)
    print("media written to", args.out)
