"""Flaria stream wire protocol v1 (little-endian).

Client -> server binary frames, 20-byte header ``<BBHIdHH``:
    u8  type      0x01 audio PCM | 0x02 face landmarks | 0x03 face absent
    u8  version   1
    u16 flags     reserved (0)
    u32 seq       per-type sequence number (gap => packet loss)
    f64 t_ms      client epoch milliseconds at capture
    u16 width     video width  (landmark frames; 0 otherwise)
    u16 height    video height (landmark frames; 0 otherwise)
  payload:
    audio     float32[n]      mono PCM at the server sample rate, n <= 1 s
    landmarks float32[468*3]  normalised x, y, z
    absent    (empty)

Server -> client: JSON text frames by default; with tick_encoding=binary the
telemetry tick is a 140-byte frame ``TICK_STRUCT`` (type 0x10).

Client -> server JSON control messages: ping, reauth, stop.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

PROTOCOL_VERSION = 1
HEADER = struct.Struct("<BBHIdHH")
HEADER_SIZE = HEADER.size  # 20

MSG_AUDIO = 0x01
MSG_LANDMARKS = 0x02
MSG_FACE_ABSENT = 0x03
MSG_TICK = 0x10

LANDMARK_FLOATS = 468 * 3
LANDMARK_PAYLOAD_BYTES = LANDMARK_FLOATS * 4

STATUS_CODES = {"CALIBRATING": 0, "STABLE": 1, "WATCH": 2, "ELEVATED": 3, "SIGNAL_LOST": 4}

FLAG_FACE = 1 << 0
FLAG_VOICED = 1 << 1
FLAG_AUDIO_DROPOUT = 1 << 2
FLAG_POSE_OK = 1 << 3
FLAG_CALIBRATED = 1 << 4

# type, ver, flags, seq, ts, src_ts, proc_ms, anomaly, raw, status, pad,
# audio[15], vision[5], f0, jitter, shimmer, hnr, calibration
TICK_STRUCT = struct.Struct("<BBHIddfffB3x15f5f5f")
assert TICK_STRUCT.size == 140


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InboundFrame:
    kind: int
    seq: int
    t_ms: float
    width: int
    height: int
    payload: np.ndarray | None


def parse_frame(data: bytes, *, max_audio_samples: int) -> InboundFrame:
    if len(data) < HEADER_SIZE:
        raise ProtocolError("frame shorter than header")
    kind, version, _flags, seq, t_ms, width, height = HEADER.unpack_from(data, 0)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {version}")
    body = memoryview(data)[HEADER_SIZE:]
    if kind == MSG_AUDIO:
        if len(body) == 0 or len(body) % 4:
            raise ProtocolError("audio payload must be a non-empty float32 array")
        n = len(body) // 4
        if n > max_audio_samples:
            raise ProtocolError("audio chunk too long")
        pcm = np.frombuffer(body, dtype="<f4").astype(np.float32)  # copy out of the transport buffer
        return InboundFrame(kind, seq, t_ms, 0, 0, pcm)
    if kind == MSG_LANDMARKS:
        if len(body) != LANDMARK_PAYLOAD_BYTES:
            raise ProtocolError("landmark payload must be 468x3 float32")
        if not (16 <= width <= 8192 and 16 <= height <= 8192):
            raise ProtocolError("invalid frame dimensions")
        pts = np.frombuffer(body, dtype="<f4").astype(np.float32).reshape(468, 3)
        return InboundFrame(kind, seq, t_ms, width, height, pts)
    if kind == MSG_FACE_ABSENT:
        return InboundFrame(kind, seq, t_ms, 0, 0, None)
    raise ProtocolError(f"unknown frame type {kind:#x}")


def _f(v: float | None) -> float:
    return float("nan") if v is None else float(v)


def encode_tick_binary(
    *, seq: int, ts: float, src_ts: float, proc_ms: float, anomaly: float, raw: float, status: str, flags: int,
    audio: np.ndarray, vision: np.ndarray, f0: float | None, jitter: float | None, shimmer: float | None,
    hnr: float | None, calibration: float,
) -> bytes:
    return TICK_STRUCT.pack(
        MSG_TICK, PROTOCOL_VERSION, flags & 0xFFFF, seq & 0xFFFFFFFF, ts, src_ts, proc_ms, anomaly, raw,
        STATUS_CODES[status], *[float(v) for v in audio], *[float(v) for v in vision],
        _f(f0), _f(jitter), _f(shimmer), _f(hnr), calibration,
    )
