#!/usr/bin/env python3
"""Flaria offline workstation HUD (backup path when the web client or network is unavailable).

    python scripts/live_hud.py --camera 0 --model models/flaria_core.onnx

Threads (all decoupled through bounded, latest-wins handoffs):
  video-capture   cv2.VideoCapture -> 1-slot queue (stale frames are dropped)
  audio-capture   sounddevice callback -> RAM FloatRingBuffer (1 s)
  analysis        MediaPipe Face Mesh -> 5-D vision, 100 ms audio hop -> 15-D
                  acoustic, calibration, ONNX inference, risk state machine
  main/render     OpenCV window: frame, landmarks, lip geometry, metrics, trend

Uses exactly the same feature, calibration and inference code as the server.
Nothing is written to disk unless --baseline-file is given, in which case only
the Welford baseline statistics (non-reversible aggregates) are saved on exit.

Keys: q / Esc quit, r reset session calibration, m toggle mesh overlay.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

CORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE_ROOT))

from app.services.calibration import AUDIO_STD_FLOOR, VISION_STD_FLOOR, BaselineCalibrator, RunningStats  # noqa: E402
from app.services.features_audio import AcousticFeatureExtractor, AudioFeatures  # noqa: E402
from app.services.features_vision import (  # noqa: E402
    LIP_CORNER_L, LIP_CORNER_R, NASAL_BRIDGE, SUBNASALE, VisionFeatureExtractor, VisionFeatures,
)
from app.services.inference import InferenceEngine, ModelUnavailableError  # noqa: E402
from app.services.ring_buffer import FloatRingBuffer, VectorRingBuffer  # noqa: E402
from app.services.stream_session import RiskStateMachine  # noqa: E402

log = logging.getLogger("live_hud")
SR = 16000
TICK_HZ = 30.0
STATUS_COLORS = {  # BGR
    "CALIBRATING": (200, 170, 90), "STABLE": (150, 200, 60), "WATCH": (40, 160, 230),
    "ELEVATED": (70, 60, 210), "SIGNAL_LOST": (150, 150, 150),
}


# ============================================================================ capture threads
class VideoCapture(threading.Thread):
    def __init__(self, index: int, width: int, height: int, stop: threading.Event) -> None:
        super().__init__(name="video-capture", daemon=True)
        self.index, self.width, self.height, self.stop = index, width, height, stop
        self.frames: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=1)
        self.error: str | None = None

    def run(self) -> None:
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.error = f"camera {self.index} could not be opened"
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, 30)
        failures = 0
        try:
            while not self.stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    failures += 1
                    if failures > 60:
                        self.error = "camera stopped delivering frames"
                        return
                    time.sleep(0.02)
                    continue
                failures = 0
                item = (time.monotonic(), frame)
                try:
                    self.frames.put_nowait(item)
                except queue.Full:
                    try:
                        self.frames.get_nowait()  # latest frame wins
                    except queue.Empty:
                        pass
                    self.frames.put_nowait(item)
        finally:
            cap.release()


class AudioCapture:
    def __init__(self, device: int | None, ring: FloatRingBuffer) -> None:
        self.ring = ring
        self.device = device
        self.stream = None
        self.overflows = 0
        self.error: str | None = None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001 - sounddevice signature
        if status.input_overflow:
            self.overflows += 1
        self.ring.write(indata[:, 0])

    def start(self) -> None:
        try:
            import sounddevice as sd

            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=SR // 20,
                                         device=self.device, callback=self._callback)
            self.stream.start()
        except Exception as exc:  # noqa: BLE001 - continue vision-only
            self.error = f"microphone unavailable: {exc}"
            log.warning(self.error)

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


# ============================================================================ analysis
@dataclass
class HudState:
    status: str = "CALIBRATING"
    anomaly: float | None = None
    vision: VisionFeatures | None = None
    audio: AudioFeatures | None = None
    landmarks_px: np.ndarray | None = None
    frame: np.ndarray | None = None
    calibration: float = 0.0
    proc_ms: float = 0.0
    history: list[float] = field(default_factory=list)


class Analyzer(threading.Thread):
    def __init__(self, video: VideoCapture, ring: FloatRingBuffer, engine: InferenceEngine | None,
                 baseline: dict[str, RunningStats], stop: threading.Event) -> None:
        super().__init__(name="analysis", daemon=True)
        import mediapipe as mp

        self.mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False, max_num_faces=1, refine_landmarks=False,
            min_detection_confidence=0.5, min_tracking_confidence=0.5,
        )
        self.video, self.ring, self.engine, self.stop = video, ring, engine, stop
        self.audio_x = AcousticFeatureExtractor(sample_rate=SR)
        self.vision_x = VisionFeatureExtractor()
        seq = engine.meta.seq_len if engine else 48
        self.seq_a, self.seq_v = VectorRingBuffer(seq, 15), VectorRingBuffer(seq, 5)
        self.cal_a = BaselineCalibrator(15, baseline["audio"], abs_floor=AUDIO_STD_FLOOR,
                                        min_baseline_samples=600, warmup_samples=100)
        self.cal_v = BaselineCalibrator(5, baseline["vision"], abs_floor=VISION_STD_FLOOR,
                                        min_baseline_samples=1200, warmup_samples=200)
        thresholds = (engine.meta.threshold_watch, engine.meta.threshold_elevated) if engine else (0.6, 0.8)
        self.risk = RiskStateMachine(*thresholds)
        self.state = HudState()
        self.lock = threading.Lock()
        self.reset_requested = threading.Event()
        self._ema: float | None = None

    def snapshot(self) -> HudState:
        with self.lock:
            s = self.state
            return HudState(s.status, s.anomaly, s.vision, s.audio, s.landmarks_px, s.frame, s.calibration,
                            s.proc_ms, list(s.history))

    def run(self) -> None:
        period, next_audio = 1.0 / TICK_HZ, 0.0
        audio: AudioFeatures | None = None
        last_total = 0
        while not self.stop.is_set():
            try:
                _, frame = self.video.frames.get(timeout=0.25)
            except queue.Empty:
                frame = None
            t0 = time.perf_counter()
            now = time.monotonic()
            if self.reset_requested.is_set():
                self.reset_requested.clear()
                self.cal_a.session, self.cal_v.session = RunningStats(15), RunningStats(5)
                self.seq_a.purge(); self.seq_v.purge(); self._ema = None

            vision, lm_px = VisionFeatures.absent(), None
            if frame is not None:
                h, w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                result = self.mesh.process(rgb)
                if result.multi_face_landmarks:
                    pts = np.array([(p.x, p.y, p.z) for p in result.multi_face_landmarks[0].landmark[:468]], np.float32)
                    vision = self.vision_x.extract(pts, w, h)
                    lm_px = np.column_stack([pts[:, 0] * w, pts[:, 1] * h]).astype(np.int32)
                    pts.fill(0)

            if now >= next_audio and self.ring.size >= SR // 2 and self.ring.total_written != last_total:
                next_audio = now + 0.1
                last_total = self.ring.total_written
                window = self.ring.latest(SR // 2)
                audio = self.audio_x.extract(window)
                window.fill(0)
                if audio.valid:
                    self.cal_a.observe(audio.vector, stable=self.risk.level == 0)
            if vision.valid:
                self.cal_v.observe(vision.vector, stable=self.risk.level == 0)

            za = self.cal_a.normalize(audio.vector) if audio is not None and audio.valid else None
            zv = self.cal_v.normalize(vision.vector) if vision.valid else None
            self.seq_a.push(za if za is not None else np.zeros(15, np.float32))
            self.seq_v.push(zv if zv is not None else np.zeros(5, np.float32))
            calibrated = self.cal_a.ready and self.cal_v.ready
            if self.engine and calibrated and self.seq_a.full and (za is not None or zv is not None):
                raw = self.engine.predict(self.seq_a.ordered(), self.seq_v.ordered())
                self._ema = raw if self._ema is None else self._ema + (1 - math.exp(-period / 0.5)) * (raw - self._ema)
            signal_ok = vision.face_present or (audio is not None and audio.valid)
            status = self.risk.update(self._ema, now, signal_ok=signal_ok, calibrated=calibrated and self._ema is not None)

            with self.lock:
                st = self.state
                st.status, st.anomaly, st.vision, st.audio = status, self._ema, vision, audio
                st.landmarks_px, st.frame = lm_px, frame
                st.calibration = min(self.cal_a.progress, self.cal_v.progress)
                st.proc_ms = (time.perf_counter() - t0) * 1000
                if self._ema is not None:
                    st.history.append(self._ema)
                    del st.history[:-int(30 * TICK_HZ)]
        self.mesh.close()


# ============================================================================ rendering
def _pt(p: np.ndarray) -> tuple[int, int]:
    return int(p[0]), int(p[1])


def draw(state: HudState, show_mesh: bool, notes: list[str]) -> np.ndarray:
    frame = state.frame.copy() if state.frame is not None else np.zeros((480, 640, 3), np.uint8)
    frame = cv2.flip(frame, 1)
    h, w = frame.shape[:2]
    color = STATUS_COLORS[state.status]
    if state.landmarks_px is not None:
        pts = state.landmarks_px.copy()
        pts[:, 0] = w - 1 - pts[:, 0]
        if show_mesh:
            for x, y in pts[::2]:
                cv2.circle(frame, (int(x), int(y)), 1, (200, 210, 120), -1, cv2.LINE_AA)
        cv2.line(frame, _pt(pts[NASAL_BRIDGE]), _pt(pts[SUBNASALE]), (230, 230, 230), 1, cv2.LINE_AA)
        cv2.line(frame, _pt(pts[LIP_CORNER_R]), _pt(pts[LIP_CORNER_L]), color, 2, cv2.LINE_AA)
        for idx in (LIP_CORNER_R, LIP_CORNER_L):
            cv2.circle(frame, _pt(pts[idx]), 4, color, -1, cv2.LINE_AA)

    panel = frame.copy()
    cv2.rectangle(panel, (0, 0), (w, 92), (25, 30, 36), -1)
    cv2.rectangle(panel, (0, h - 70), (w, h), (25, 30, 36), -1)
    frame = cv2.addWeighted(panel, 0.78, frame, 0.22, 0)
    font = cv2.FONT_HERSHEY_SIMPLEX
    anomaly = "--" if state.anomaly is None else f"{state.anomaly:.2f}"
    cv2.putText(frame, f"{state.status}", (14, 34), font, 0.9, color, 2, cv2.LINE_AA)
    cv2.putText(frame, f"Anomaly index {anomaly}   calibration {state.calibration * 100:.0f}%   "
                       f"{state.proc_ms:.1f} ms", (14, 62), font, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
    v = state.vision
    if v is not None and v.face_present:
        a, lv, ear, pitch, yaw = v.vector
        cv2.putText(frame, f"asym {a:.3f}  lips {lv:.3f}  EAR {ear:.2f}  pitch {pitch:+.0f}  yaw {yaw:+.0f}",
                    (14, 84), font, 0.45, (200, 220, 230), 1, cv2.LINE_AA)
    au = state.audio
    if au is not None and au.valid and au.voice.f0_hz is not None:
        vq = au.voice
        jit = f"{vq.jitter_local * 100:.2f}%" if vq.jitter_local is not None else "--"
        shim = f"{vq.shimmer_local * 100:.1f}%" if vq.shimmer_local is not None else "--"
        cv2.putText(frame, f"f0 {vq.f0_hz:.0f} Hz  jitter {jit}  shimmer {shim}  HNR {vq.hnr_db:.1f} dB",
                    (14, h - 48), font, 0.45, (200, 220, 230), 1, cv2.LINE_AA)
    if len(state.history) > 1:
        hist = np.array(state.history[-w:])
        xs = np.linspace(10, w - 10, hist.size).astype(np.int32)
        ys = (h - 10 - hist * 30).astype(np.int32)
        cv2.polylines(frame, [np.column_stack([xs, ys])], False, color, 1, cv2.LINE_AA)
    for i, note in enumerate(notes):
        cv2.putText(frame, note, (14, 116 + 20 * i), font, 0.45, (80, 180, 240), 1, cv2.LINE_AA)
    return frame


def load_baseline(path: Path | None) -> dict[str, RunningStats]:
    base = {"audio": RunningStats(15), "vision": RunningStats(5)}
    if path and path.is_file():
        raw = json.loads(path.read_text())
        for k, dim in (("audio", 15), ("vision", 5)):
            if k in raw:
                base[k] = RunningStats(dim, int(raw[k]["n"]), np.array(raw[k]["mean"]), np.array(raw[k]["m2"]))
    return base


def save_baseline(path: Path, analyzer: Analyzer) -> None:
    out = {}
    for k, cal in (("audio", analyzer.cal_a), ("vision", analyzer.cal_v)):
        merged = RunningStats.merge(cal.baseline, cal.contribution)
        out[k] = {"n": merged.n, "mean": merged.mean.tolist(), "m2": merged.m2.tolist()}
    path.write_text(json.dumps(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--audio-device", type=int, default=None)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--model", type=Path, default=CORE_ROOT / "models" / "flaria_core.onnx")
    ap.add_argument("--meta", type=Path, default=CORE_ROOT / "models" / "flaria_core.meta.json")
    ap.add_argument("--baseline-file", type=Path, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    notes: list[str] = []
    try:
        engine = InferenceEngine(args.model, args.meta)
    except ModelUnavailableError as exc:
        engine = None
        notes.append(f"Model unavailable ({exc}). Showing biomarkers only.")

    stop = threading.Event()
    ring = FloatRingBuffer(SR)
    video = VideoCapture(args.camera, args.width, args.height, stop)
    audio = AudioCapture(args.audio_device, ring)
    analyzer = Analyzer(video, ring, engine, load_baseline(args.baseline_file), stop)
    video.start()
    audio.start()
    analyzer.start()
    if audio.error:
        notes.append(audio.error)

    show_mesh = True
    cv2.namedWindow("Flaria workstation", cv2.WINDOW_NORMAL)
    try:
        while True:
            if video.error and video.error not in notes:
                notes.append(video.error)
            cv2.imshow("Flaria workstation", draw(analyzer.snapshot(), show_mesh, notes))
            key = cv2.waitKey(15) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                analyzer.reset_requested.set()
            if key == ord("m"):
                show_mesh = not show_mesh
            if cv2.getWindowProperty("Flaria workstation", cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        audio.stop()
        analyzer.join(timeout=2)
        video.join(timeout=2)
        ring.purge()
        if args.baseline_file:
            save_baseline(args.baseline_file, analyzer)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
