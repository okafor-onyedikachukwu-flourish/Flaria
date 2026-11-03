import numpy as np

from app.services.calibration import AUDIO_STD_FLOOR, BaselineCalibrator, RunningStats


def test_welford_matches_numpy():
    rng = np.random.default_rng(0)
    x = rng.normal(5, 2, (500, 4))
    s = RunningStats(4)
    for row in x:
        s.update(row)
    assert np.allclose(s.mean, x.mean(0))
    assert np.allclose(s.variance, x.var(0, ddof=1))


def test_parallel_merge_is_exact():
    rng = np.random.default_rng(1)
    days = [rng.normal(i, 1 + i, (rng.integers(50, 200), 3)) for i in range(7)]
    parts = []
    for d in days:
        s = RunningStats(3)
        s.update_batch(d)
        parts.append(s)
    merged = RunningStats.merge_all(3, parts)
    allx = np.concatenate(days)
    assert merged.n == len(allx)
    assert np.allclose(merged.mean, allx.mean(0))
    assert np.allclose(merged.variance, allx.var(0, ddof=1))


def test_calibrator_warmup_then_zscore():
    rng = np.random.default_rng(2)
    cal = BaselineCalibrator(15, None, abs_floor=AUDIO_STD_FLOOR, warmup_samples=50)
    base = rng.normal(0, 10, 15)
    for _ in range(49):
        cal.observe(base + rng.normal(0, 3, 15), stable=True)
    assert cal.normalize(base) is None
    cal.observe(base, stable=True)
    z = cal.normalize(base)
    assert z is not None and np.all(np.abs(z) < 1.0)
    assert np.all(np.abs(cal.normalize(base + 1000.0)) == cal.z_clip)


def test_contamination_guard():
    cal = BaselineCalibrator(5, None, abs_floor=np.ones(5) * 0.01, warmup_samples=5)
    for i in range(10):
        cal.observe(np.ones(5) * i, stable=i < 5)
    assert cal.session.n == 10 and cal.contribution.n == 5
