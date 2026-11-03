"""Dynamic per-patient baseline calibration.

Each feature is z-scored against the patient's own running mean and variance,
so a naturally asymmetric smile or a low-pitched voice is that patient's zero
point instead of a permanent false positive.

* :class:`RunningStats` - vectorised Welford accumulator with Chan's parallel
  merge, so daily rows can be combined into a 7-day baseline exactly.
* :class:`BaselineCalibrator` - z-scores with the persisted 7-day baseline when
  it holds enough samples, otherwise bootstraps from an in-session warm-up.
  Only observations taken while the patient looks stable feed the stats that
  are written back (contamination guard), so a developing shift is not
  silently absorbed into the baseline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np


@dataclass(slots=True)
class RunningStats:
    dim: int
    n: int = 0
    mean: np.ndarray = field(default=None)  # type: ignore[assignment]
    m2: np.ndarray = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.mean is None:
            self.mean = np.zeros(self.dim, dtype=np.float64)
        if self.m2 is None:
            self.m2 = np.zeros(self.dim, dtype=np.float64)
        self.mean = np.asarray(self.mean, dtype=np.float64).reshape(self.dim)
        self.m2 = np.asarray(self.m2, dtype=np.float64).reshape(self.dim)

    def update(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float64).reshape(self.dim)
        if not np.all(np.isfinite(x)):
            return
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    def update_batch(self, xs: np.ndarray) -> None:
        xs = np.asarray(xs, dtype=np.float64).reshape(-1, self.dim)
        xs = xs[np.all(np.isfinite(xs), axis=1)]
        if xs.shape[0] == 0:
            return
        other = RunningStats(self.dim, xs.shape[0], xs.mean(axis=0), ((xs - xs.mean(axis=0)) ** 2).sum(axis=0))
        merged = RunningStats.merge(self, other)
        self.n, self.mean, self.m2 = merged.n, merged.mean, merged.m2

    @property
    def variance(self) -> np.ndarray:
        if self.n < 2:
            return np.full(self.dim, np.nan)
        return self.m2 / (self.n - 1)

    def std(self, abs_floor: np.ndarray | float, rel_floor: float) -> np.ndarray:
        var = self.variance
        std = np.sqrt(np.where(np.isfinite(var), var, 0.0))
        floor = np.maximum(np.asarray(abs_floor, dtype=np.float64), rel_floor * np.abs(self.mean))
        return np.maximum(std, floor)

    @staticmethod
    def merge(a: "RunningStats", b: "RunningStats") -> "RunningStats":
        if a.dim != b.dim:
            raise ValueError("dimension mismatch")
        if a.n == 0:
            return RunningStats(b.dim, b.n, b.mean.copy(), b.m2.copy())
        if b.n == 0:
            return RunningStats(a.dim, a.n, a.mean.copy(), a.m2.copy())
        n = a.n + b.n
        delta = b.mean - a.mean
        mean = a.mean + delta * (b.n / n)
        m2 = a.m2 + b.m2 + delta * delta * (a.n * b.n / n)
        return RunningStats(a.dim, n, mean, m2)

    @staticmethod
    def merge_all(dim: int, parts: Iterable["RunningStats"]) -> "RunningStats":
        acc = RunningStats(dim)
        for part in parts:
            acc = RunningStats.merge(acc, part)
        return acc


# Absolute std floors per feature (scale-aware) - prevent a near-constant
# baseline feature from exploding into huge z-scores.
AUDIO_STD_FLOOR = np.array([1.0] * 13 + [25.0, 0.005], dtype=np.float64)
VISION_STD_FLOOR = np.array([0.005, 0.005, 0.01, 1.0, 1.0], dtype=np.float64)
# f0 (Hz), local jitter (ratio), local shimmer (ratio), HNR (dB)
VOICE_STD_FLOOR = np.array([4.0, 0.002, 0.008, 1.0], dtype=np.float64)


class BaselineCalibrator:
    def __init__(
        self,
        dim: int,
        baseline: RunningStats | None,
        *,
        abs_floor: np.ndarray,
        rel_floor: float = 0.02,
        min_baseline_samples: int = 300,
        warmup_samples: int = 90,
        z_clip: float = 6.0,
    ) -> None:
        self.dim = dim
        self.baseline = baseline if baseline is not None else RunningStats(dim)
        self.session = RunningStats(dim)       # every valid observation this session
        self.contribution = RunningStats(dim)  # stable observations written back to the baseline
        self.abs_floor = abs_floor
        self.rel_floor = rel_floor
        self.min_baseline_samples = min_baseline_samples
        self.warmup_samples = warmup_samples
        self.z_clip = z_clip

    @property
    def using_persisted_baseline(self) -> bool:
        return self.baseline.n >= self.min_baseline_samples

    @property
    def progress(self) -> float:
        if self.using_persisted_baseline:
            return 1.0
        return min(1.0, self.session.n / max(1, self.warmup_samples))

    @property
    def ready(self) -> bool:
        return self.progress >= 1.0

    def observe(self, x: np.ndarray, *, stable: bool) -> None:
        self.session.update(x)
        if stable:
            self.contribution.update(x)

    def reference(self) -> RunningStats:
        return self.baseline if self.using_persisted_baseline else self.session

    def normalize(self, x: np.ndarray) -> np.ndarray | None:
        if not self.ready:
            return None
        ref = self.reference()
        z = (np.asarray(x, dtype=np.float64) - ref.mean) / ref.std(self.abs_floor, self.rel_floor)
        return np.clip(z, -self.z_clip, self.z_clip).astype(np.float32)
