import struct

import numpy as np
import pytest

from app.services.protocol import (
    HEADER, MSG_AUDIO, MSG_LANDMARKS, TICK_STRUCT, ProtocolError, encode_tick_binary, parse_frame,
)
from app.services.ring_buffer import FloatRingBuffer
from app.services.stream_session import RiskStateMachine


def test_parse_audio_and_landmarks():
    pcm = np.linspace(-1, 1, 1600, dtype="<f4")
    f = parse_frame(HEADER.pack(MSG_AUDIO, 1, 0, 7, 123.0, 0, 0) + pcm.tobytes(), max_audio_samples=16000)
    assert f.seq == 7 and np.allclose(f.payload, pcm)
    lm = np.random.rand(468, 3).astype("<f4")
    f = parse_frame(HEADER.pack(MSG_LANDMARKS, 1, 0, 1, 1.0, 640, 480) + lm.tobytes(), max_audio_samples=16000)
    assert f.payload.shape == (468, 3) and f.width == 640


@pytest.mark.parametrize("data", [
    b"\x01",
    HEADER.pack(MSG_AUDIO, 2, 0, 0, 0.0, 0, 0) + b"\0" * 8,
    HEADER.pack(MSG_AUDIO, 1, 0, 0, 0.0, 0, 0) + b"\0" * 7,
    HEADER.pack(MSG_LANDMARKS, 1, 0, 0, 0.0, 640, 480) + b"\0" * 100,
    HEADER.pack(0x7F, 1, 0, 0, 0.0, 0, 0),
])
def test_parse_rejects_malformed(data):
    with pytest.raises(ProtocolError):
        parse_frame(data, max_audio_samples=16000)


def test_binary_tick_size():
    b = encode_tick_binary(seq=1, ts=1.0, src_ts=0.5, proc_ms=1.2, anomaly=0.3, raw=0.4, status="WATCH", flags=3,
                           audio=np.zeros(15), vision=np.zeros(5), f0=None, jitter=0.01, shimmer=None, hnr=20.0,
                           calibration=1.0)
    assert len(b) == TICK_STRUCT.size == 140


def test_ring_buffer_wraps_and_purges():
    r = FloatRingBuffer(10)
    r.write(np.arange(7, dtype=np.float32))
    r.write(np.arange(7, 14, dtype=np.float32))
    assert list(r.latest(10)) == list(range(4, 14))
    r.purge()
    assert r.size == 0 and r.latest(5).size == 0


def test_risk_hysteresis():
    sm = RiskStateMachine(0.6, 0.8, watch_hold=1.0, elevated_hold=2.0, release_hold=1.0)
    t = 0.0
    assert sm.update(0.9, t, signal_ok=True, calibrated=True) == "STABLE"  # spike alone does nothing
    for _ in range(25):
        t += 0.1
        status = sm.update(0.9, t, signal_ok=True, calibrated=True)
    assert status == "ELEVATED"
    for _ in range(5):
        t += 0.1
        status = sm.update(0.78, t, signal_ok=True, calibrated=True)  # inside hysteresis band
    assert status == "ELEVATED"
    for _ in range(12):
        t += 0.1
        status = sm.update(0.5, t, signal_ok=True, calibrated=True)
    assert status == "WATCH"
    assert sm.update(0.5, t, signal_ok=False, calibrated=True) == "SIGNAL_LOST"
