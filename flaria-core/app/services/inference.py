"""ONNX Runtime inference engine for the CrossAttentionFusion model.

* Verifies the model file against the SHA-256 recorded in its metadata at
  load time, so a swapped or corrupted artefact is refused.
* Prefers CUDAExecutionProvider when present and falls back to CPU if CUDA
  initialisation fails (missing driver/cuDNN) instead of crashing the service.
* ``InferenceSession.run`` is thread-safe; callers dispatch through a shared
  ThreadPoolExecutor so the asyncio loop never blocks on inference.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort

logger = logging.getLogger("flaria.inference")


class ModelUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelMeta:
    version: str
    seq_len: int
    audio_dim: int
    vision_dim: int
    threshold_watch: float
    threshold_elevated: float
    sha256: str

    @staticmethod
    def load(path: Path) -> "ModelMeta":
        raw = json.loads(path.read_text(encoding="utf-8"))
        meta = ModelMeta(
            version=str(raw["version"]),
            seq_len=int(raw["seq_len"]),
            audio_dim=int(raw["audio_dim"]),
            vision_dim=int(raw["vision_dim"]),
            threshold_watch=float(raw["threshold_watch"]),
            threshold_elevated=float(raw["threshold_elevated"]),
            sha256=str(raw["sha256"]).lower(),
        )
        if not (0.0 < meta.threshold_watch < meta.threshold_elevated < 1.0):
            raise ModelUnavailableError("metadata thresholds must satisfy 0 < watch < elevated < 1")
        if meta.audio_dim != 15 or meta.vision_dim != 5 or meta.seq_len < 4:
            raise ModelUnavailableError("metadata dimensions do not match the 15-D/5-D contract")
        return meta


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.tanh(0.5 * x))  # numerically stable


class InferenceEngine:
    def __init__(self, model_path: Path, meta_path: Path, *, intra_op_threads: int = 1, prefer_cuda: bool = True) -> None:
        if not model_path.is_file():
            raise ModelUnavailableError(f"model not found: {model_path} (run scripts/train_and_export.py)")
        if not meta_path.is_file():
            raise ModelUnavailableError(f"model metadata not found: {meta_path}")
        self.meta = ModelMeta.load(meta_path)
        digest = _sha256(model_path)
        if digest != self.meta.sha256:
            raise ModelUnavailableError("model checksum does not match metadata; refusing to load")

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = intra_op_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        available = ort.get_available_providers()
        candidates: list[list[str]] = []
        if prefer_cuda and "CUDAExecutionProvider" in available:
            candidates.append(["CUDAExecutionProvider", "CPUExecutionProvider"])
        candidates.append(["CPUExecutionProvider"])

        last_error: Exception | None = None
        self.session: ort.InferenceSession | None = None
        for providers in candidates:
            try:
                self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
                break
            except Exception as exc:  # noqa: BLE001 - provider init failures vary by platform
                last_error = exc
                logger.warning("execution provider init failed", extra={"fields": {"providers": providers, "error": str(exc)}})
        if self.session is None:
            raise ModelUnavailableError(f"could not create inference session: {last_error}")

        self.provider = self.session.get_providers()[0]
        inputs = {i.name: i for i in self.session.get_inputs()}
        if set(inputs) != {"audio", "vision"}:
            raise ModelUnavailableError(f"unexpected model inputs: {sorted(inputs)}")
        self._output = self.session.get_outputs()[0].name

        # Warm-up: first run allocates arenas / compiles kernels.
        t0 = time.perf_counter()
        self.predict(np.zeros((self.meta.seq_len, 15), np.float32), np.zeros((self.meta.seq_len, 5), np.float32))
        logger.info(
            "inference engine ready",
            extra={"fields": {"provider": self.provider, "version": self.meta.version,
                              "warmup_ms": round((time.perf_counter() - t0) * 1000, 2)}},
        )

    def predict(self, audio_seq: np.ndarray, vision_seq: np.ndarray) -> float:
        """Single sequence -> anomaly probability in [0, 1]."""
        return float(self.predict_batch(audio_seq[None, ...], vision_seq[None, ...])[0])

    def predict_batch(self, audio: np.ndarray, vision: np.ndarray) -> np.ndarray:
        if audio.ndim != 3 or vision.ndim != 3 or audio.shape[:2] != vision.shape[:2]:
            raise ValueError("expected audio (B,T,15) and vision (B,T,5) with matching B,T")
        if audio.shape[2] != self.meta.audio_dim or vision.shape[2] != self.meta.vision_dim:
            raise ValueError("feature dimension mismatch")
        assert self.session is not None
        logits = self.session.run(
            [self._output],
            {"audio": np.ascontiguousarray(audio, dtype=np.float32),
             "vision": np.ascontiguousarray(vision, dtype=np.float32)},
        )[0]
        probs = _sigmoid(np.asarray(logits, dtype=np.float64).reshape(-1))
        return np.nan_to_num(probs, nan=0.0, posinf=1.0, neginf=0.0)
