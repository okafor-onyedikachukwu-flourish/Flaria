"""Per-connection runtime behind ``/ws/stream``.

Five cooperating tasks share one session:

  receive   parses binary frames; raw PCM goes into the RAM ring, landmarks are
            reduced to the 5-D vector immediately and the landmark array is
            zeroed. Enforces size, rate and protocol limits.
  audio     every hop (100 ms) copies the latest 500 ms window out of the ring,
            extracts features in the thread pool, wipes the copy.
  tick      at ``tick_hz`` fuses the latest calibrated vectors into the model
            sequence, runs ONNX inference, smooths, updates the risk state
            machine and queues a telemetry tick.
  send      drains a small bounded outbound queue (slow clients lose old ticks,
            never stall the pipeline).
  persist   folds ticks into 1 Hz aggregates for the TelemetryWriter.

Missing modalities are fed to the model as neutral z = 0 so absent data can
never raise the anomaly index on its own; the status reports SIGNAL_LOST
instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import asyncpg
import numpy as np
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from app.core.config import Settings
from app.core.security import CurrentUser
from app.database import repositories as repo
from app.services.calibration import (
    AUDIO_STD_FLOOR, VISION_STD_FLOOR, VOICE_STD_FLOOR, BaselineCalibrator, RunningStats,
)
from app.services.findings import RULES_VERSION, assess
from app.services.features_audio import AUDIO_DIM, AcousticFeatureExtractor, AudioFeatures, VoiceQuality
from app.services.features_vision import VISION_DIM, VisionFeatureExtractor, VisionFeatures
from app.services.inference import InferenceEngine
from app.services.protocol import (
    FLAG_AUDIO_DROPOUT, FLAG_CALIBRATED, FLAG_FACE, FLAG_POSE_OK, FLAG_VOICED, MSG_AUDIO, MSG_FACE_ABSENT,
    MSG_LANDMARKS, InboundFrame, ProtocolError, encode_tick_binary, parse_frame,
)
from app.services.ring_buffer import FloatRingBuffer, VectorRingBuffer
from app.services.telemetry_writer import TelemetryRecord, TelemetryWriter

logger = logging.getLogger("flaria.stream")

ZERO_AUDIO = np.zeros(AUDIO_DIM, np.float32)
ZERO_VISION = np.zeros(VISION_DIM, np.float32)
VOICE_DIM = 4  # f0, jitter, shimmer, HNR
STATUS_SEVERITY = {"STABLE": 0, "CALIBRATING": 1, "SIGNAL_LOST": 2, "WATCH": 3, "ELEVATED": 4}

AuthResolver = Callable[[str], Awaitable[tuple[CurrentUser, dict[str, Any]]]]


class CloseCode:
    NORMAL = 1000
    GOING_AWAY = 1001
    POLICY = 1008
    TOO_BIG = 1009
    INTERNAL = 1011
    TRY_AGAIN_LATER = 1013
    UNAUTHORIZED = 4401
    FORBIDDEN = 4403
    IDLE = 4408
    CONFLICT = 4409


# ============================================================================ risk state machine
class RiskStateMachine:
    """Hysteretic escalation so single-frame spikes never flip the badge."""

    LEVELS = ("STABLE", "WATCH", "ELEVATED")

    def __init__(self, watch: float, elevated: float, *, hysteresis: float = 0.05, watch_hold: float = 1.5,
                 elevated_hold: float = 3.0, release_hold: float = 2.0) -> None:
        self.watch, self.elevated, self.hysteresis = watch, elevated, hysteresis
        self.watch_hold, self.elevated_hold, self.release_hold = watch_hold, elevated_hold, release_hold
        self.level = 0
        self._watch_since: float | None = None
        self._elevated_since: float | None = None
        self._below_since: float | None = None

    def update(self, anomaly: float | None, now: float, *, signal_ok: bool, calibrated: bool) -> str:
        if not signal_ok:
            self._watch_since = self._elevated_since = self._below_since = None
            return "SIGNAL_LOST"
        if not calibrated or anomaly is None:
            return "CALIBRATING"
        a = anomaly
        self._watch_since = (self._watch_since if self._watch_since is not None else now) if a >= self.watch else None
        self._elevated_since = (
            (self._elevated_since if self._elevated_since is not None else now) if a >= self.elevated else None
        )
        if self.level < 2 and self._elevated_since is not None and now - self._elevated_since >= self.elevated_hold:
            self.level = 2
        elif self.level < 1 and self._watch_since is not None and now - self._watch_since >= self.watch_hold:
            self.level = 1

        if self.level > 0:
            release_threshold = (self.elevated if self.level == 2 else self.watch) - self.hysteresis
            if a < release_threshold:
                self._below_since = self._below_since if self._below_since is not None else now
                if now - self._below_since >= self.release_hold:
                    self.level -= 1
                    self._below_since = None
            else:
                self._below_since = None
        return self.LEVELS[self.level]


# ============================================================================ 1 Hz aggregation
@dataclass(slots=True)
class _Aggregate:
    ticks: int = 0
    audio_sum: np.ndarray = field(default_factory=lambda: np.zeros(AUDIO_DIM, np.float64))
    audio_n: int = 0
    vision_sum: np.ndarray = field(default_factory=lambda: np.zeros(VISION_DIM, np.float64))
    vision_n: int = 0
    vq_sum: np.ndarray = field(default_factory=lambda: np.zeros(4, np.float64))
    vq_n: np.ndarray = field(default_factory=lambda: np.zeros(4, np.int64))
    anomaly_sum: float = 0.0
    anomaly_n: int = 0
    anomaly_peak: float | None = None
    face_ticks: int = 0
    voiced_ticks: int = 0
    worst_status: str = "STABLE"

    def add(self, *, audio: AudioFeatures | None, vision: VisionFeatures | None, anomaly: float | None, status: str) -> None:
        self.ticks += 1
        if audio is not None and audio.valid:
            self.audio_sum += audio.vector
            self.audio_n += 1
            vq = audio.voice
            for i, v in enumerate((vq.f0_hz, vq.jitter_local, vq.shimmer_local, vq.hnr_db)):
                if v is not None and math.isfinite(v):
                    self.vq_sum[i] += v
                    self.vq_n[i] += 1
            if vq.voiced_ratio > 0.2:
                self.voiced_ticks += 1
        if vision is not None and vision.valid:
            self.vision_sum += vision.vector
            self.vision_n += 1
        if vision is not None and vision.face_present:
            self.face_ticks += 1
        if anomaly is not None:
            self.anomaly_sum += anomaly
            self.anomaly_n += 1
            self.anomaly_peak = anomaly if self.anomaly_peak is None else max(self.anomaly_peak, anomaly)
        if STATUS_SEVERITY[status] > STATUS_SEVERITY[self.worst_status]:
            self.worst_status = status

    def to_record(self, session_id: uuid.UUID, patient_id: uuid.UUID) -> TelemetryRecord:
        vq = [float(self.vq_sum[i] / self.vq_n[i]) if self.vq_n[i] else None for i in range(4)]
        return TelemetryRecord(
            session_id=session_id,
            patient_id=patient_id,
            ts=datetime.now(timezone.utc),
            audio_vec=[float(x) for x in self.audio_sum / self.audio_n] if self.audio_n else None,
            vision_vec=[float(x) for x in self.vision_sum / self.vision_n] if self.vision_n else None,
            f0_hz=vq[0], jitter_local=vq[1], shimmer_local=vq[2], hnr_db=vq[3],
            anomaly=(self.anomaly_sum / self.anomaly_n) if self.anomaly_n else None,
            anomaly_peak=self.anomaly_peak,
            risk_status=self.worst_status,
            face_coverage=self.face_ticks / self.ticks,
            voiced_ratio=self.voiced_ticks / self.ticks,
            sample_count=self.ticks,
        )


def _finite(v: float) -> float:
    return float(v) if math.isfinite(v) else 0.0


def _r(v: float | None, nd: int = 4) -> float | None:
    if v is None or not math.isfinite(v):
        return None
    return round(float(v), nd)


# ============================================================================ session
class StreamSession:
    def __init__(
        self,
        *,
        ws: WebSocket,
        user: CurrentUser,
        patient_id: uuid.UUID,
        session_id: uuid.UUID,
        auth_exp: float,
        settings: Settings,
        engine: InferenceEngine,
        executor: Executor,
        writer: TelemetryWriter,
        pool: asyncpg.Pool,
        auth_resolver: AuthResolver,
        audio_baseline: RunningStats,
        vision_baseline: RunningStats,
        voice_baseline: RunningStats,
    ) -> None:
        self.ws, self.user, self.patient_id, self.session_id = ws, user, patient_id, session_id
        self.auth_exp = auth_exp
        self.s, self.engine, self.executor, self.writer, self.pool = settings, engine, executor, writer, pool
        self.auth_resolver = auth_resolver

        sr = settings.audio_sample_rate
        self.sr = sr
        self.window_n = int(round(settings.audio_window_seconds * sr))
        self.hop_s = settings.audio_hop_seconds
        self.audio_ring = FloatRingBuffer(int(round(settings.audio_ring_seconds * sr)))
        self.audio_extractor = AcousticFeatureExtractor(sample_rate=sr)
        self.vision_extractor = VisionFeatureExtractor()

        seq_len = engine.meta.seq_len
        self.audio_seq = VectorRingBuffer(seq_len, AUDIO_DIM)
        self.vision_seq = VectorRingBuffer(seq_len, VISION_DIM)
        audio_rate = 1.0 / self.hop_s
        vision_rate = settings.vision_nominal_fps
        self.audio_cal = BaselineCalibrator(
            AUDIO_DIM, audio_baseline, abs_floor=AUDIO_STD_FLOOR,
            min_baseline_samples=int(settings.min_baseline_seconds * audio_rate),
            warmup_samples=int(settings.session_warmup_seconds * audio_rate),
        )
        self.vision_cal = BaselineCalibrator(
            VISION_DIM, vision_baseline, abs_floor=VISION_STD_FLOOR,
            min_baseline_samples=int(settings.min_baseline_seconds * vision_rate),
            warmup_samples=int(settings.session_warmup_seconds * vision_rate),
        )
        self.voice_cal = BaselineCalibrator(
            VOICE_DIM, voice_baseline, abs_floor=VOICE_STD_FLOOR,
            min_baseline_samples=int(settings.min_baseline_seconds * audio_rate * 0.5),
            warmup_samples=int(settings.session_warmup_seconds * audio_rate * 0.5),
        )
        self.risk = RiskStateMachine(engine.meta.threshold_watch, engine.meta.threshold_elevated)
        # Session-level z-score accumulators against the *persisted* baseline, for the result.
        self._z_session = {"audio": RunningStats(AUDIO_DIM), "vision": RunningStats(VISION_DIM), "voice": RunningStats(VOICE_DIM)}
        self._status_ticks: dict[str, int] = {k: 0 for k in STATUS_SEVERITY}
        self._face_ticks = 0
        self._voiced_ticks = 0
        self._started_mono = time.monotonic()

        now = time.monotonic()
        self._stop = asyncio.Event()
        self._outbound: asyncio.Queue[str | bytes] = asyncio.Queue(maxsize=16)
        self._close_code = CloseCode.NORMAL
        self._close_reason = "client_disconnect"
        self._end_status = "COMPLETED"

        self._audio_latest: AudioFeatures | None = None
        self._audio_t = 0.0
        self._audio_expected_seq: int | None = None
        self._audio_gap_marker: int | None = None
        self._audio_dropout_until = 0.0
        self._last_audio_total = 0
        self._audio_gaps = 0

        self._vision_latest: VisionFeatures | None = None
        self._vision_t = 0.0
        self._vision_src_ts = 0.0
        self._vision_interval = 1.0 / 30.0  # EMA of frame inter-arrival; sizes the staleness window

        self._last_msg_t = now
        self._rate_window_start = now
        self._rate_count = 0
        self._protocol_errors = 0
        self._signal_lost_since: float | None = None
        self._ema: float | None = None
        self._raw: float | None = None
        self._last_tick_t = now
        self._ticks_since_calibrated = 0
        self._tick_seq = 0
        self._dropped_ticks = 0
        self._agg = _Aggregate()
        self._session_anomaly_sum = 0.0
        self._session_anomaly_n = 0
        self._session_peak: float | None = None
        self._status = "CALIBRATING"

    # ------------------------------------------------------------------ factory
    @classmethod
    async def open(
        cls, *, ws: WebSocket, user: CurrentUser, patient_id: uuid.UUID, auth_exp: float, settings: Settings,
        engine: InferenceEngine, executor: Executor, writer: TelemetryWriter, pool: asyncpg.Pool,
        auth_resolver: AuthResolver, client_info: dict[str, Any],
    ) -> "StreamSession":
        async with pool.acquire() as conn:
            async with conn.transaction():
                await repo.abort_stale_sessions(conn, settings.stale_session_seconds, patient_id)
                audio_rows = await repo.load_baseline_days(conn, patient_id, "audio")
                vision_rows = await repo.load_baseline_days(conn, patient_id, "vision")
                voice_rows = await repo.load_baseline_days(conn, patient_id, "voice")
                audio_base = RunningStats.merge_all(AUDIO_DIM, (RunningStats(AUDIO_DIM, r.n, r.mean, r.m2) for r in audio_rows))
                vision_base = RunningStats.merge_all(VISION_DIM, (RunningStats(VISION_DIM, r.n, r.mean, r.m2) for r in vision_rows))
                voice_base = RunningStats.merge_all(VOICE_DIM, (RunningStats(VOICE_DIM, r.n, r.mean, r.m2) for r in voice_rows))
                row = await repo.create_session(
                    conn, patient_id=patient_id, started_by=user.id, model_version=engine.meta.version,
                    execution_provider=engine.provider, baseline_samples=int(audio_base.n + vision_base.n),
                    client_info=client_info,
                )
                await repo.audit(conn, "session.start", actor_id=user.id, target_id=row["id"],
                                 detail={"patient_id": str(patient_id)})
        return cls(
            ws=ws, user=user, patient_id=patient_id, session_id=row["id"], auth_exp=auth_exp, settings=settings,
            engine=engine, executor=executor, writer=writer, pool=pool, auth_resolver=auth_resolver,
            audio_baseline=audio_base, vision_baseline=vision_base, voice_baseline=voice_base,
        )

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        self._enqueue(json.dumps(self._hello()))
        tasks = [
            asyncio.create_task(self._receive_loop(), name="ws-receive"),
            asyncio.create_task(self._audio_loop(), name="ws-audio"),
            asyncio.create_task(self._tick_loop(), name="ws-tick"),
            asyncio.create_task(self._send_loop(), name="ws-send"),
            asyncio.create_task(self._persist_loop(), name="ws-persist"),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    exc = t.exception()
                    logger.error("stream task failed", exc_info=exc,
                                 extra={"fields": {"session_id": str(self.session_id), "task": t.get_name()}})
                    self._set_close(CloseCode.INTERNAL, "internal_error", "ABORTED")
        finally:
            self._stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._finalize()

    async def shutdown(self, code: int = CloseCode.GOING_AWAY, reason: str = "server_shutdown") -> None:
        await self._close(code, reason, "ABORTED")

    def _set_close(self, code: int, reason: str, end_status: str) -> None:
        if not self._stop.is_set():
            self._close_code, self._close_reason, self._end_status = code, reason, end_status
        self._stop.set()

    async def _close(self, code: int, reason: str, end_status: str = "COMPLETED") -> None:
        self._set_close(code, reason, end_status)
        if self.ws.application_state == WebSocketState.CONNECTED:
            try:
                await self.ws.close(code=self._close_code, reason=self._close_reason[:120])
            except Exception:  # noqa: BLE001 - socket may already be gone
                pass

    async def _finalize(self) -> None:
        if self.ws.application_state == WebSocketState.CONNECTED:
            try:
                await self.ws.close(code=self._close_code, reason=self._close_reason[:120])
            except Exception:  # noqa: BLE001
                pass
        if self._agg.ticks:
            self.writer.submit(self._agg.to_record(self.session_id, self.patient_id))
            self._agg = _Aggregate()
        # Privacy: zero every buffer that ever held raw audio or derived sequences.
        self.audio_ring.purge()
        self.audio_seq.purge()
        self.vision_seq.purge()
        self._audio_latest = None
        self._vision_latest = None

        mean = self._session_anomaly_sum / self._session_anomaly_n if self._session_anomaly_n else None
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await repo.end_session(
                        conn, self.session_id, status=self._end_status, reason=self._close_reason,
                        tick_count=self._tick_seq, peak_anomaly=self._session_peak, mean_anomaly=mean,
                    )
                    await self._write_report(conn)
                    await self._merge_baseline(conn)
                    await repo.audit(conn, "session.end", actor_id=self.user.id, target_id=self.session_id,
                                     detail={"reason": self._close_reason, "status": self._end_status,
                                             "ticks": self._tick_seq, "audio_gaps": self._audio_gaps,
                                             "dropped_ticks": self._dropped_ticks})
        except Exception:  # noqa: BLE001 - stale-heartbeat sweep will still close the row
            logger.exception("session finalize failed", extra={"fields": {"session_id": str(self.session_id)}})
        logger.info("session closed", extra={"fields": {
            "session_id": str(self.session_id), "reason": self._close_reason, "ticks": self._tick_seq}})

    async def _merge_baseline(self, conn: asyncpg.Connection) -> None:
        today = datetime.now(timezone.utc).date()
        for modality, cal in (("audio", self.audio_cal), ("vision", self.vision_cal), ("voice", self.voice_cal)):
            contrib = cal.contribution
            if contrib.n == 0:
                continue
            existing = await repo.lock_baseline_day(conn, self.patient_id, modality, today)
            base = RunningStats(cal.dim) if existing is None else RunningStats(cal.dim, existing.n, existing.mean, existing.m2)
            merged = RunningStats.merge(base, contrib)
            await repo.upsert_baseline_day(conn, self.patient_id, modality,
                                           repo.BaselineRow(today, merged.n, merged.mean, merged.m2))

    def build_summary(self) -> dict[str, Any]:
        """Session-level inputs for the findings engine: no raw media, only aggregates."""
        hz = self.s.tick_hz
        z: dict[str, list[float] | None] = {}
        n: dict[str, int] = {}
        for key, cal in (("audio", self.audio_cal), ("vision", self.vision_cal), ("voice", self.voice_cal)):
            acc = self._z_session[key]
            n[key] = acc.n
            z[key] = [round(float(v), 3) for v in acc.mean] if acc.n and cal.using_persisted_baseline else None
        return {
            "persisted_baseline": self.audio_cal.using_persisted_baseline and self.vision_cal.using_persisted_baseline,
            "seconds": round(time.monotonic() - self._started_mono, 1),
            "valid_seconds": {"face": round(self._face_ticks / hz, 1), "voice": round(self._voiced_ticks / hz, 1)},
            "z": z,
            "n": n,
            "status_seconds": {k: round(v / hz, 1) for k, v in self._status_ticks.items()},
            "anomaly_mean": (self._session_anomaly_sum / self._session_anomaly_n) if self._session_anomaly_n else None,
            "anomaly_peak": self._session_peak,
            "thresholds": {"watch": self.risk.watch, "elevated": self.risk.elevated},
            "baseline_samples": {"audio": self.audio_cal.baseline.n, "vision": self.vision_cal.baseline.n,
                                 "voice": self.voice_cal.baseline.n},
            "model_version": self.engine.meta.version,
        }

    async def _write_report(self, conn: asyncpg.Connection) -> None:
        if self._tick_seq == 0:
            return
        summary = self.build_summary()
        # A stub model can never produce real health advice, whatever FLARIA_DEMO_MODE says.
        demo = self.s.demo_mode or self.engine.meta.version.startswith("STUB")
        result = assess(summary, demo=demo)
        await repo.insert_report(
            conn, session_id=self.session_id, patient_id=self.patient_id, triage=result.triage,
            summary=summary, assessment=result.to_dict(), rules_version=RULES_VERSION,
        )

    def _accumulate_z(self, key: str, cal: BaselineCalibrator, x: np.ndarray) -> None:
        if cal.using_persisted_baseline:
            z = cal.normalize(x)
            if z is not None:
                self._z_session[key].update(z)

    # ------------------------------------------------------------------ outbound
    def _enqueue(self, payload: str | bytes) -> None:
        if self._outbound.full():
            try:
                self._outbound.get_nowait()
                self._dropped_ticks += 1
            except asyncio.QueueEmpty:
                pass
        self._outbound.put_nowait(payload)

    async def _send_loop(self) -> None:
        while not self._stop.is_set():
            payload = await self._outbound.get()
            if self.ws.application_state != WebSocketState.CONNECTED:
                return
            try:
                if isinstance(payload, bytes):
                    await self.ws.send_bytes(payload)
                else:
                    await self.ws.send_text(payload)
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                # Peer vanished mid-send: a normal end of stream, not a server fault.
                self._set_close(CloseCode.GOING_AWAY, "client_disconnect", "COMPLETED")
                return

    def _hello(self) -> dict[str, Any]:
        m = self.engine.meta
        return {
            "type": "hello",
            "session_id": str(self.session_id),
            "patient_id": str(self.patient_id),
            "model": {"version": m.version, "provider": self.engine.provider, "seq_len": m.seq_len,
                      "threshold_watch": m.threshold_watch, "threshold_elevated": m.threshold_elevated},
            "tick_hz": self.s.tick_hz,
            "tick_encoding": self.s.tick_encoding,
            "audio": {"sample_rate": self.sr, "max_chunk_samples": self.sr},
            "auth_exp": self.auth_exp,
            "privacy": {
                "raw_media_persisted": False,
                "video_frames_received": False,
                "landmarks_persisted": False,
                "audio_ring_seconds": self.s.audio_ring_seconds,
                "audio_ring_bytes": self.audio_ring.capacity * 4,
                "persist_hz": self.s.persist_hz,
                "persisted_fields": ["audio_vec_15d_1s_mean", "vision_vec_5d_1s_mean", "f0_hz", "jitter_local",
                                     "shimmer_local", "hnr_db", "anomaly", "risk_status", "quality_ratios"],
            },
            "calibration": {
                "audio_baseline_samples": self.audio_cal.baseline.n,
                "vision_baseline_samples": self.vision_cal.baseline.n,
                "using_persisted_baseline": self.audio_cal.using_persisted_baseline and self.vision_cal.using_persisted_baseline,
            },
        }

    # ------------------------------------------------------------------ receive
    def _rate_ok(self, now: float) -> bool:
        if now - self._rate_window_start >= 1.0:
            self._rate_window_start, self._rate_count = now, 0
        self._rate_count += 1
        return self._rate_count <= self.s.ws_max_messages_per_second

    async def _receive_loop(self) -> None:
        while not self._stop.is_set():
            message = await self.ws.receive()
            if message["type"] == "websocket.disconnect":
                self._set_close(int(message.get("code") or CloseCode.NORMAL), "client_disconnect", "COMPLETED")
                return
            now = time.monotonic()
            self._last_msg_t = now
            if not self._rate_ok(now):
                await self._close(CloseCode.POLICY, "rate_limit", "ABORTED")
                return
            data: bytes | None = message.get("bytes")
            text: str | None = message.get("text")
            if data is not None:
                if len(data) > self.s.ws_max_message_bytes:
                    await self._close(CloseCode.TOO_BIG, "frame_too_large", "ABORTED")
                    return
                try:
                    frame = parse_frame(data, max_audio_samples=self.sr)
                except ProtocolError:
                    self._protocol_errors += 1
                    if self._protocol_errors > 50:
                        await self._close(CloseCode.POLICY, "protocol_errors", "ABORTED")
                        return
                    continue
                self._handle_frame(frame, now)
            elif text is not None:
                if len(text) > 8192:
                    await self._close(CloseCode.TOO_BIG, "control_too_large", "ABORTED")
                    return
                await self._handle_control(text)

    def _handle_frame(self, frame: InboundFrame, now: float) -> None:
        if frame.kind == MSG_AUDIO:
            assert frame.payload is not None
            pcm = frame.payload
            if self._audio_expected_seq is not None and frame.seq != self._audio_expected_seq:
                self._audio_gaps += 1
                self._audio_gap_marker = self.audio_ring.total_written
            self._audio_expected_seq = (frame.seq + 1) & 0xFFFFFFFF
            np.nan_to_num(pcm, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
            np.clip(pcm, -1.0, 1.0, out=pcm)
            self.audio_ring.write(pcm)
            pcm.fill(0.0)
        elif frame.kind == MSG_LANDMARKS:
            assert frame.payload is not None
            feats = self.vision_extractor.extract(frame.payload, frame.width, frame.height)
            frame.payload.fill(0.0)  # landmarks are biometric: never retained beyond this call
            self._note_vision_arrival(now)
            self._vision_latest, self._vision_t, self._vision_src_ts = feats, now, _finite(frame.t_ms)
            if feats.valid:
                self.vision_cal.observe(feats.vector, stable=self._is_stable())
                self._accumulate_z("vision", self.vision_cal, feats.vector)
        elif frame.kind == MSG_FACE_ABSENT:
            self._note_vision_arrival(now)
            self._vision_latest, self._vision_t, self._vision_src_ts = VisionFeatures.absent(), now, _finite(frame.t_ms)

    async def _handle_control(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            self._protocol_errors += 1
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "ping":
            t = msg.get("t")
            if isinstance(t, (int, float)) and math.isfinite(t):
                self._enqueue(json.dumps({"type": "pong", "t": t, "ts": time.time() * 1000.0}))
        elif kind == "reauth":
            token = msg.get("token")
            if not isinstance(token, str) or len(token) > 4096:
                await self._close(CloseCode.UNAUTHORIZED, "reauth_invalid", "ABORTED")
                return
            try:
                user, claims = await self.auth_resolver(token)
            except Exception:  # noqa: BLE001 - any failure is an auth failure
                await self._close(CloseCode.UNAUTHORIZED, "reauth_failed", "ABORTED")
                return
            if user.id != self.user.id:
                await self._close(CloseCode.UNAUTHORIZED, "reauth_subject_mismatch", "ABORTED")
                return
            self.auth_exp = float(claims["exp"])
            self._enqueue(json.dumps({"type": "reauth_ok", "auth_exp": self.auth_exp}))
        elif kind == "stop":
            await self._close(CloseCode.NORMAL, "client_stop", "COMPLETED")

    def _note_vision_arrival(self, now: float) -> None:
        if self._vision_t > 0.0:
            gap = min(2.0, max(0.0, now - self._vision_t))
            self._vision_interval += 0.1 * (gap - self._vision_interval)

    def _vision_staleness(self) -> float:
        """A frame stays current for ~3 frame intervals, so slow trackers aren't reported as face-absent."""
        return min(2.0, max(0.5, 3.0 * self._vision_interval))

    # ------------------------------------------------------------------ audio
    def _is_stable(self) -> bool:
        return self.risk.level == 0 and (self._ema is None or self._ema < 0.8 * self.risk.watch)

    async def _audio_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            await asyncio.sleep(self.hop_s)
            total = self.audio_ring.total_written
            if total == self._last_audio_total or self.audio_ring.size < self.window_n:
                continue
            self._last_audio_total = total
            window = self.audio_ring.latest(self.window_n)
            gap_in_window = self._audio_gap_marker is not None and self._audio_gap_marker > total - self.window_n
            try:
                feats = await loop.run_in_executor(self.executor, self.audio_extractor.extract, window)
            finally:
                window.fill(0.0)
            now = time.monotonic()
            if gap_in_window:
                self._audio_dropout_until = now + self.s.audio_window_seconds
                if feats.valid:
                    # Cycle-to-cycle measures across a discontinuity are meaningless.
                    feats = AudioFeatures(feats.vector, VoiceQuality.empty(feats.voice.voiced_ratio), feats.rms_dbfs, True)
            self._audio_latest, self._audio_t = feats, now
            if feats.valid:
                stable = self._is_stable()
                self.audio_cal.observe(feats.vector, stable=stable)
                self._accumulate_z("audio", self.audio_cal, feats.vector)
                vq = feats.voice
                if None not in (vq.f0_hz, vq.jitter_local, vq.shimmer_local, vq.hnr_db):
                    voice_vec = np.array([vq.f0_hz, vq.jitter_local, vq.shimmer_local, vq.hnr_db], dtype=np.float64)
                    self.voice_cal.observe(voice_vec, stable=stable)
                    self._accumulate_z("voice", self.voice_cal, voice_vec)

    # ------------------------------------------------------------------ tick
    async def _tick_loop(self) -> None:
        period = 1.0 / self.s.tick_hz
        next_t = time.monotonic()
        while not self._stop.is_set():
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_t = time.monotonic()  # fell behind: skip, never burst
            await self._tick()

    async def _tick(self) -> None:
        t0 = time.perf_counter()
        now = time.monotonic()

        if time.time() > self.auth_exp + self.s.ws_reauth_grace_seconds:
            await self._close(CloseCode.UNAUTHORIZED, "token_expired", "COMPLETED")
            return
        if now - self._last_msg_t > self.s.ws_idle_timeout_seconds:
            await self._close(CloseCode.IDLE, "idle_timeout", "ABORTED")
            return

        audio = self._audio_latest if (self._audio_latest is not None and now - self._audio_t < 0.6) else None
        vision = (self._vision_latest
                  if self._vision_latest is not None and now - self._vision_t < self._vision_staleness() else None)
        audio_ok = audio is not None and audio.valid
        vision_ok = vision is not None and vision.valid
        face_present = vision is not None and vision.face_present
        voiced = audio_ok and audio.voice.voiced_ratio > 0.2  # type: ignore[union-attr]
        dropout = now < self._audio_dropout_until or (self.audio_ring.size > 0 and now - self._audio_t > 0.6)

        if audio_ok or face_present:
            self._signal_lost_since = None
        elif self._signal_lost_since is None:
            self._signal_lost_since = now
        signal_ok = self._signal_lost_since is None or now - self._signal_lost_since < self.s.signal_lost_seconds

        za = self.audio_cal.normalize(audio.vector) if audio_ok else None  # type: ignore[union-attr]
        zv = self.vision_cal.normalize(vision.vector) if vision_ok else None  # type: ignore[union-attr]
        calibrated = self.audio_cal.ready and self.vision_cal.ready
        self.audio_seq.push(za if za is not None else ZERO_AUDIO)
        self.vision_seq.push(zv if zv is not None else ZERO_VISION)
        self._ticks_since_calibrated = self._ticks_since_calibrated + 1 if calibrated else 0

        anomaly: float | None = None
        if calibrated and self._ticks_since_calibrated >= self.engine.meta.seq_len and (za is not None or zv is not None):
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                self.executor, self.engine.predict, self.audio_seq.ordered(), self.vision_seq.ordered()
            )
            dt = max(1e-3, now - self._last_tick_t)
            alpha = 1.0 - math.exp(-dt / 0.5)  # 0.5 s time constant
            self._ema = raw if self._ema is None else self._ema + alpha * (raw - self._ema)
            self._raw = raw
            anomaly = self._ema
        self._last_tick_t = now

        status = self.risk.update(anomaly if anomaly is not None else self._ema, now,
                                  signal_ok=signal_ok, calibrated=calibrated and self._ema is not None)
        self._status = status
        self._status_ticks[status] += 1
        self._face_ticks += int(vision_ok)
        self._voiced_ticks += int(bool(voiced))
        self._agg.add(audio=audio, vision=vision, anomaly=anomaly, status=status)
        if anomaly is not None:
            self._session_anomaly_sum += anomaly
            self._session_anomaly_n += 1
            self._session_peak = anomaly if self._session_peak is None else max(self._session_peak, anomaly)

        self._tick_seq += 1
        calibration = min(self.audio_cal.progress, self.vision_cal.progress)
        proc_ms = (time.perf_counter() - t0) * 1000.0
        vq = audio.voice if audio_ok else VoiceQuality.empty()  # type: ignore[union-attr]
        a_vec = audio.vector if audio_ok else ZERO_AUDIO  # type: ignore[union-attr]
        v_vec = vision.vector if vision_ok or face_present else ZERO_VISION  # type: ignore[union-attr]

        if self.s.tick_encoding == "binary":
            flags = ((FLAG_FACE if face_present else 0) | (FLAG_VOICED if voiced else 0)
                     | (FLAG_AUDIO_DROPOUT if dropout else 0) | (FLAG_POSE_OK if vision_ok else 0)
                     | (FLAG_CALIBRATED if calibrated else 0))
            self._enqueue(encode_tick_binary(
                seq=self._tick_seq, ts=time.time() * 1000.0, src_ts=self._vision_src_ts, proc_ms=proc_ms,
                anomaly=float("nan") if self._ema is None else self._ema,
                raw=float("nan") if self._raw is None else self._raw, status=status, flags=flags,
                audio=a_vec, vision=v_vec, f0=vq.f0_hz, jitter=vq.jitter_local, shimmer=vq.shimmer_local,
                hnr=vq.hnr_db, calibration=calibration,
            ))
        else:
            self._enqueue(json.dumps({
                "type": "tick",
                "seq": self._tick_seq,
                "ts": time.time() * 1000.0,
                "src_ts": self._vision_src_ts,
                "proc_ms": round(proc_ms, 3),
                "anomaly": _r(self._ema),
                "raw": _r(self._raw),
                "status": status,
                "audio": [round(float(x), 4) for x in a_vec],
                "vision": [round(float(x), 5) for x in v_vec],
                "vq": {"f0": _r(vq.f0_hz, 2), "jitter": _r(vq.jitter_local, 5), "shimmer": _r(vq.shimmer_local, 5),
                       "hnr": _r(vq.hnr_db, 2), "voiced": _r(vq.voiced_ratio, 3)},
                "q": {"face": face_present, "voiced": voiced, "audio_dropout": dropout, "pose_ok": vision_ok,
                      "calibration": round(calibration, 3), "calibrated": calibrated},
            }, separators=(",", ":"), allow_nan=False))

    # ------------------------------------------------------------------ persist
    async def _persist_loop(self) -> None:
        period = 1.0 / self.s.persist_hz
        while not self._stop.is_set():
            await asyncio.sleep(period)
            if self._agg.ticks:
                agg, self._agg = self._agg, _Aggregate()
                self.writer.submit(agg.to_record(self.session_id, self.patient_id))


# ============================================================================ registry
class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: set[StreamSession] = set()

    def add(self, s: StreamSession) -> None:
        self._sessions.add(s)

    def discard(self, s: StreamSession) -> None:
        self._sessions.discard(s)

    def __len__(self) -> int:
        return len(self._sessions)

    async def shutdown(self) -> None:
        await asyncio.gather(*(s.shutdown() for s in list(self._sessions)), return_exceptions=True)
