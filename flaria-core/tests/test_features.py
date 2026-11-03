import numpy as np
import pytest

from app.services.features_audio import AUDIO_DIM, AcousticFeatureExtractor
from app.services.features_vision import (
    LIP_CORNER_L, LIP_CORNER_R, NASAL_BRIDGE, SUBNASALE, VisionFeatureExtractor,
)

SR = 16000


def glottal_train(f0: float, seconds: float, jitter: float = 0.0, shimmer: float = 0.0, noise: float = 0.0, seed: int = 0):
    """Synthetic voiced signal: a decaying-harmonic pulse per cycle with controlled perturbation."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.zeros(n)
    t = 0.0
    k = np.arange(int(SR / f0 * 1.6))
    pulse = np.exp(-k / (SR / f0 / 4)) * np.sin(2 * np.pi * 2.0 * f0 * k / SR)
    while t < n:
        period = (1.0 / f0) * (1 + rng.normal(0, jitter))
        amp = 1 + rng.normal(0, shimmer)
        i = int(t)
        seg = pulse[: max(0, min(len(pulse), n - i))]
        x[i : i + len(seg)] += amp * seg
        t += period * SR
    x = 0.3 * x / np.max(np.abs(x))
    return (x + rng.normal(0, noise, n)).astype(np.float32)


def test_silence_is_invalid():
    feats = AcousticFeatureExtractor().extract(np.zeros(8000, np.float32))
    assert not feats.valid and feats.vector.shape == (AUDIO_DIM,)


def test_nan_window_is_invalid():
    x = glottal_train(140, 0.5)
    x[100] = np.nan
    assert not AcousticFeatureExtractor().extract(x).valid


def test_clean_voice_quality():
    feats = AcousticFeatureExtractor().extract(glottal_train(140, 0.5))
    vq = feats.voice
    assert feats.valid and np.all(np.isfinite(feats.vector))
    assert vq.f0_hz == pytest.approx(140, rel=0.05)
    assert vq.jitter_local is not None and vq.jitter_local < 0.01
    assert vq.shimmer_local is not None and vq.shimmer_local < 0.03
    assert vq.hnr_db is not None and vq.hnr_db > 15


def test_perturbation_is_detected():
    ex = AcousticFeatureExtractor()
    clean = ex.extract(glottal_train(140, 0.5, seed=1)).voice
    rough = ex.extract(glottal_train(140, 0.5, jitter=0.03, shimmer=0.15, noise=0.03, seed=1)).voice
    assert rough.jitter_local > clean.jitter_local * 2
    assert rough.shimmer_local > clean.shimmer_local * 2
    assert rough.hnr_db < clean.hnr_db


def _face(asym_px: float = 0.0, width: int = 640, height: int = 480):
    """Synthetic frontal landmark set with controllable lip-corner droop."""
    rng = np.random.default_rng(3)
    lm = np.column_stack([rng.uniform(0.35, 0.65, 468), rng.uniform(0.3, 0.75, 468), rng.normal(0, 0.01, 468)])
    def put(i, x, y, z=0.0):
        lm[i] = (x / width, y / height, z / width)
    put(NASAL_BRIDGE, 320, 200); put(SUBNASALE, 320, 280)
    put(33, 260, 200); put(263, 380, 200); put(10, 320, 120); put(152, 320, 380)
    put(LIP_CORNER_R, 290, 320); put(LIP_CORNER_L, 350, 320 + asym_px)
    put(13, 320, 315); put(14, 320, 325)
    for (p1, p2, p3, p4, p5, p6), cx in (((33, 160, 158, 133, 153, 144), 275), ((362, 385, 387, 263, 373, 380), 365)):
        put(p1, cx - 15, 200); put(p4, cx + 15, 200)
        put(p2, cx - 5, 195); put(p3, cx + 5, 195); put(p6, cx - 5, 205); put(p5, cx + 5, 205)
    lm[33] = (260 / width, 200 / height, 0); lm[263] = (380 / width, 200 / height, 0)
    return lm.astype(np.float32)


def test_symmetric_face():
    f = VisionFeatureExtractor().extract(_face(0), 640, 480)
    assert f.valid and f.face_present
    assert f.vector[0] == pytest.approx(0.0, abs=1e-3)
    assert abs(f.vector[3]) < 5 and abs(f.vector[4]) < 5


def test_droop_increases_asymmetry():
    sym = VisionFeatureExtractor().extract(_face(0), 640, 480).vector[0]
    droop = VisionFeatureExtractor().extract(_face(12), 640, 480).vector[0]
    assert droop > sym + 0.05


def test_garbage_landmarks_rejected():
    ex = VisionFeatureExtractor()
    assert not ex.extract(np.full((468, 3), np.nan, np.float32), 640, 480).valid
    assert not ex.extract(np.zeros((10, 3), np.float32), 640, 480).valid
    assert not ex.extract(_face(0), 0, 480).valid
