"""Pre-allocated, thread-safe RAM ring buffers.

Raw audio only ever lives in :class:`FloatRingBuffer`: fixed capacity (default
1 s at 16 kHz = 64 KiB), continuously overwritten, zero-filled on ``purge()``
and never serialised. Nothing in this module touches the filesystem.
"""
from __future__ import annotations

import threading

import numpy as np


class FloatRingBuffer:
    """Mono float32 sample ring with O(1) writes and contiguous window reads."""

    __slots__ = ("_buf", "_cap", "_write", "_size", "_total", "_lock")

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._cap = capacity
        self._write = 0
        self._size = 0
        self._total = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._cap

    @property
    def size(self) -> int:
        return self._size

    @property
    def total_written(self) -> int:
        """Monotonic count of samples ever written (used to detect new data)."""
        return self._total

    def write(self, samples: np.ndarray) -> None:
        x = np.asarray(samples, dtype=np.float32).ravel()
        n = x.size
        if n == 0:
            return
        with self._lock:
            if n >= self._cap:
                self._buf[:] = x[-self._cap :]
                self._write = 0
                self._size = self._cap
            else:
                end = self._write + n
                if end <= self._cap:
                    self._buf[self._write : end] = x
                else:
                    first = self._cap - self._write
                    self._buf[self._write :] = x[:first]
                    self._buf[: n - first] = x[first:]
                self._write = end % self._cap
                self._size = min(self._cap, self._size + n)
            self._total += n

    def latest(self, n: int) -> np.ndarray:
        """Copy of the most recent ``n`` samples (fewer if not yet available)."""
        with self._lock:
            n = min(n, self._size)
            if n <= 0:
                return np.empty(0, dtype=np.float32)
            start = (self._write - n) % self._cap
            if start + n <= self._cap:
                return self._buf[start : start + n].copy()
            first = self._cap - start
            return np.concatenate((self._buf[start:], self._buf[: n - first]))

    def purge(self) -> None:
        """Zero-fill the backing memory and reset indices."""
        with self._lock:
            self._buf.fill(0.0)
            self._write = 0
            self._size = 0


class VectorRingBuffer:
    """Fixed-length history of feature vectors, read out oldest-to-newest."""

    __slots__ = ("_buf", "_cap", "_write", "_size", "_lock")

    def __init__(self, capacity: int, dim: int) -> None:
        self._buf = np.zeros((capacity, dim), dtype=np.float32)
        self._cap = capacity
        self._write = 0
        self._size = 0
        self._lock = threading.Lock()

    @property
    def full(self) -> bool:
        return self._size == self._cap

    @property
    def size(self) -> int:
        return self._size

    def push(self, v: np.ndarray) -> None:
        with self._lock:
            self._buf[self._write] = v
            self._write = (self._write + 1) % self._cap
            self._size = min(self._cap, self._size + 1)

    def ordered(self) -> np.ndarray:
        with self._lock:
            if self._size < self._cap:
                return self._buf[: self._size].copy()
            return np.concatenate((self._buf[self._write :], self._buf[: self._write]))

    def purge(self) -> None:
        with self._lock:
            self._buf.fill(0.0)
            self._write = 0
            self._size = 0
