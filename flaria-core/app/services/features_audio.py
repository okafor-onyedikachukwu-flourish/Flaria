"""Acoustic biomarker extraction.

15-D vector (model input): 13 MFCCs (frame-averaged over the analysis window),
spectral centroid (Hz) and zero-crossing rate.

Voice-quality scalars (display + persistence, not model input):
* f0          median fundamental frequency of voiced frames (autocorrelation)
* HNR         harmonics-to-noise ratio from the window-corrected normalised
              autocorrelation peak (Boersma 1993), averaged in dB over voiced frames
* jitter      local, cycle-to-cycle: mean |T_i - T_{i+1}| / mean T
* shimmer     local, cycle-to-cycle: mean |A_i - A_{i+1}| / mean A
  Glottal cycles are located by peak picking on a low-passed copy of the
  longest voiced run with parabolic sub-sample refinement, which is required
  at 16 kHz where typical jitter is below one sample period.

These are research-grade estimators intended for within-patient trend
monitoring against that patient's own baseline. They are not calibrated
against Praat/MDVP reference values and must not be read as absolute norms.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import librosa
import numpy as np
from scipy.fft import dct
from scipy.signal import butter, find_peaks, sosfiltfilt

N_MFCC = 13
AUDIO_DIM = 15
AUDIO_FEATURE_NAMES: tuple[str, ...] = tuple(f"mfcc_{i}" for i in range(N_MFCC)) + ("spectral_centroid_hz", "zcr")


@dataclass(frozen=True, slots=True)
class VoiceQuality:
    f0_hz: float | None
    jitter_local: float | None
    shimmer_local: float | None
    hnr_db: float | None
    voiced_ratio: float

    @staticmethod
    def empty(voiced_ratio: float = 0.0) -> "VoiceQuality":
        return VoiceQuality(None, None, None, None, voiced_ratio)


@dataclass(frozen=True, slots=True)
class AudioFeatures:
    vector: np.ndarray  # (15,) float32
    voice: VoiceQuality
    rms_dbfs: float
    valid: bool  # False for silence, dropout or corrupt windows


class AcousticFeatureExtractor:
    def __init__(
        self,
        sample_rate: int = 16000,
        n_fft: int = 512,
        hop_length: int = 160,
        n_mels: int = 40,
        f0_min: float = 75.0,
        f0_max: float = 500.0,
        silence_dbfs: float = -55.0,
        voicing_threshold: float = 0.45,
    ) -> None:
        if sample_rate < 8000:
            raise ValueError("sample_rate must be >= 8000")
        self.sr = sample_rate
        self.n_fft = n_fft
        self.hop = hop_length
        self.f0_min = f0_min
        self.f0_max = f0_max
        self.silence_dbfs = silence_dbfs
        self.voicing_threshold = voicing_threshold

        self._window = np.hanning(n_fft + 1)[:-1].astype(np.float32)
        self._mel_fb = librosa.filters.mel(
            sr=sample_rate, n_fft=n_fft, n_mels=n_mels, fmin=20.0, fmax=sample_rate / 2
        ).astype(np.float32)
        self._freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate).astype(np.float32)

        # Autocorrelation analysis: 40 ms frames (3 periods at 75 Hz), 10 ms hop.
        self._ac_len = int(round(0.040 * sample_rate))
        self._ac_hop = int(round(0.010 * sample_rate))
        self._ac_win = np.hanning(self._ac_len)
        self._ac_nfft = 1 << (2 * self._ac_len - 1).bit_length()
        w_spec = np.fft.rfft(self._ac_win, n=self._ac_nfft)
        acw = np.fft.irfft(np.abs(w_spec) ** 2, n=self._ac_nfft)[: self._ac_len]
        self._ac_win_norm = np.maximum(acw / acw[0], 1e-6)
        self._lag_min = max(2, int(math.floor(sample_rate / f0_max)))
        self._lag_max = min(int(math.ceil(sample_rate / f0_min)), self._ac_len // 2)

        self._lowpass = butter(4, min(1000.0, 0.45 * sample_rate), btype="lowpass", fs=sample_rate, output="sos")

    # ------------------------------------------------------------------ public
    def extract(self, window: np.ndarray) -> AudioFeatures:
        x = np.asarray(window, dtype=np.float32).ravel()
        if x.size < max(self.n_fft, self._ac_len) or not np.all(np.isfinite(x)):
            return self._invalid(-120.0)
        x = x - np.float32(x.mean())
        rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
        rms_dbfs = 20.0 * math.log10(max(rms, 1e-9))
        if rms_dbfs < self.silence_dbfs:
            return self._invalid(rms_dbfs)
        vector = self._spectral_vector(x)
        if not np.all(np.isfinite(vector)):
            return self._invalid(rms_dbfs)
        voice = self._voice_quality(x)
        return AudioFeatures(vector, voice, rms_dbfs, True)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _invalid(rms_dbfs: float) -> AudioFeatures:
        return AudioFeatures(np.zeros(AUDIO_DIM, np.float32), VoiceQuality.empty(), rms_dbfs, False)

    def _spectral_vector(self, x: np.ndarray) -> np.ndarray:
        frames = librosa.util.frame(x, frame_length=self.n_fft, hop_length=self.hop, axis=0)
        mag = np.abs(np.fft.rfft(frames * self._window, axis=1)).astype(np.float32)
        mel = (mag * mag) @ self._mel_fb.T
        log_mel = 10.0 * np.log10(np.maximum(mel, 1e-10))
        mfcc = dct(log_mel, type=2, axis=1, norm="ortho")[:, :N_MFCC]
        centroid = (mag @ self._freqs) / np.maximum(mag.sum(axis=1), 1e-10)
        signs = np.signbit(frames)
        zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)
        return np.concatenate((mfcc.mean(axis=0), (centroid.mean(), zcr.mean()))).astype(np.float32)

    def _voice_quality(self, x: np.ndarray) -> VoiceQuality:
        frames = librosa.util.frame(
            x.astype(np.float64), frame_length=self._ac_len, hop_length=self._ac_hop, axis=0
        )
        frames = frames - frames.mean(axis=1, keepdims=True)
        spec = np.fft.rfft(frames * self._ac_win, n=self._ac_nfft, axis=1)
        ac = np.fft.irfft(np.abs(spec) ** 2, n=self._ac_nfft, axis=1)[:, : self._ac_len]
        ac0 = ac[:, :1]
        acn = np.where(ac0 > 1e-12, ac / np.maximum(ac0, 1e-12), 0.0) / self._ac_win_norm

        band = acn[:, self._lag_min : self._lag_max + 1]
        idx = np.argmax(band, axis=1)
        r_max = band[np.arange(band.shape[0]), idx]
        lags = (idx + self._lag_min).astype(np.float64)

        # Parabolic interpolation of the autocorrelation peak for a finer f0.
        rows = np.arange(band.shape[0])
        inner = (idx > 0) & (idx < band.shape[1] - 1)
        a = np.where(inner, band[rows, np.clip(idx - 1, 0, None)], 0.0)
        c = np.where(inner, band[rows, np.clip(idx + 1, None, band.shape[1] - 1)], 0.0)
        denom = a - 2 * r_max + c
        delta = np.where(inner & (np.abs(denom) > 1e-12), 0.5 * (a - c) / np.where(denom == 0, 1, denom), 0.0)
        lags = lags + np.clip(delta, -0.5, 0.5)

        energy = np.sqrt(np.mean(frames * frames, axis=1))
        energy_floor = max(10 ** (self.silence_dbfs / 20.0), 0.1 * float(energy.max()))
        voiced = (r_max > self.voicing_threshold) & (energy > energy_floor)
        voiced_ratio = float(voiced.mean()) if voiced.size else 0.0
        if voiced.sum() < 3:
            return VoiceQuality.empty(voiced_ratio)

        f0 = float(np.median(self.sr / lags[voiced]))
        r = np.clip(r_max[voiced], 1e-6, 1 - 1e-6)
        hnr = float(np.clip(np.mean(10.0 * np.log10(r / (1.0 - r))), -20.0, 50.0))

        start, stop = self._longest_run(voiced)
        seg = x[start * self._ac_hop : min(x.size, (stop - 1) * self._ac_hop + self._ac_len)]
        jitter, shimmer = self._perturbation(seg, f0)
        return VoiceQuality(f0, jitter, shimmer, hnr, voiced_ratio)

    @staticmethod
    def _longest_run(mask: np.ndarray) -> tuple[int, int]:
        padded = np.concatenate(([False], mask, [False])).astype(np.int8)
        edges = np.flatnonzero(np.diff(padded))
        starts, stops = edges[0::2], edges[1::2]
        k = int(np.argmax(stops - starts))
        return int(starts[k]), int(stops[k])

    def _perturbation(self, seg: np.ndarray, f0: float) -> tuple[float | None, float | None]:
        t0 = self.sr / f0
        if seg.size < 6 * t0 or seg.size <= 27:  # sosfiltfilt padding needs > 3*(2*order+1) samples
            return None, None
        y = sosfiltfilt(self._lowpass, seg.astype(np.float64))
        if abs(float(y.min())) > float(y.max()):
            y = -y
        peak_amp = float(y.max())
        if peak_amp <= 0:
            return None, None
        peaks, _ = find_peaks(y, distance=max(1, int(0.7 * t0)), height=0.2 * peak_amp)
        peaks = peaks[(peaks > 0) & (peaks < y.size - 1)]
        if peaks.size < 6:
            return None, None

        a, b, c = y[peaks - 1], y[peaks], y[peaks + 1]
        denom = a - 2 * b + c
        delta = np.where(np.abs(denom) > 1e-12, 0.5 * (a - c) / np.where(denom == 0, 1, denom), 0.0)
        delta = np.clip(delta, -0.5, 0.5)
        positions = peaks + delta
        amps = b - 0.25 * (a - c) * delta

        periods = np.diff(positions) / self.sr
        expected = 1.0 / f0
        valid = (periods > 0.7 * expected) & (periods < 1.3 * expected)
        pairs = valid[:-1] & valid[1:]
        if int(pairs.sum()) < 3:
            return None, None
        jitter = float(np.mean(np.abs(np.diff(periods))[pairs]) / np.mean(periods[valid]))
        amp_diffs = np.abs(np.diff(amps))[valid]
        mean_amp = float(np.mean(np.abs(amps)))
        shimmer = float(np.mean(amp_diffs) / mean_amp) if mean_amp > 0 else None
        return min(jitter, 1.0), (min(shimmer, 1.0) if shimmer is not None else None)
