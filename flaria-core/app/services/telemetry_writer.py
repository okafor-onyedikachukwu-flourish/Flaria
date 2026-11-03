"""Batched, back-pressured persistence of 1 Hz scalar telemetry aggregates.

Sessions enqueue small immutable records; one writer task COPYs them in
batches. If PostgreSQL is unavailable the writer retries with exponential
backoff while the bounded queue absorbs the gap; if the queue overflows the
oldest records are dropped and counted (exposed on /health/ready) rather than
letting memory grow without bound.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from app.database import repositories as repo
from app.database.session import Database

logger = logging.getLogger("flaria.telemetry_writer")


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    session_id: uuid.UUID
    patient_id: uuid.UUID
    ts: datetime
    audio_vec: list[float] | None
    vision_vec: list[float] | None
    f0_hz: float | None
    jitter_local: float | None
    shimmer_local: float | None
    hnr_db: float | None
    anomaly: float | None
    anomaly_peak: float | None
    risk_status: str
    face_coverage: float
    voiced_ratio: float
    sample_count: int

    def as_row(self) -> tuple:
        return (
            self.session_id, self.patient_id, self.ts, self.audio_vec, self.vision_vec, self.f0_hz,
            self.jitter_local, self.shimmer_local, self.hnr_db, self.anomaly, self.anomaly_peak,
            self.risk_status, self.face_coverage, self.voiced_ratio, self.sample_count,
        )


class TelemetryWriter:
    def __init__(self, database: Database, *, max_queue: int = 20000, batch_size: int = 500, flush_interval: float = 1.0) -> None:
        self._db = database
        self._pending: deque[TelemetryRecord] = deque()
        self._max_queue = max_queue
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.dropped = 0
        self.written = 0
        self.consecutive_failures = 0

    @property
    def backlog(self) -> int:
        return len(self._pending)

    def submit(self, record: TelemetryRecord) -> None:
        if len(self._pending) >= self._max_queue:
            self._pending.popleft()
            self.dropped += 1
            if self.dropped % 100 == 1:
                logger.error("telemetry queue overflow; dropping oldest", extra={"fields": {"dropped": self.dropped}})
        self._pending.append(record)
        if len(self._pending) >= self._batch_size:
            self._wake.set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="telemetry-writer")

    async def stop(self, timeout: float = 10.0) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout)
            except asyncio.TimeoutError:
                self._task.cancel()
                logger.error("telemetry writer did not drain in time", extra={"fields": {"backlog": self.backlog}})

    async def _run(self) -> None:
        backoff = 0.5
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._flush_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            while self._pending:
                # Take ownership of the batch before awaiting so concurrent
                # overflow eviction can never remove records we are writing.
                batch = [self._pending.popleft() for _ in range(min(self._batch_size, len(self._pending)))]
                try:
                    async with self._db.pool.acquire() as conn:
                        async with conn.transaction():
                            await repo.copy_telemetry(conn, (r.as_row() for r in batch))
                            await repo.heartbeat_sessions(conn, list({r.session_id for r in batch}))
                except Exception as exc:  # noqa: BLE001
                    self.consecutive_failures += 1
                    self._pending.extendleft(reversed(batch))
                    while len(self._pending) > self._max_queue:
                        self._pending.popleft()
                        self.dropped += 1
                    logger.warning("telemetry flush failed", extra={"fields": {"error": str(exc), "backlog": self.backlog}})
                    if self._stopping and self.consecutive_failures > 5:
                        return
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 15.0)
                    break
                self.written += len(batch)
                self.consecutive_failures = 0
                backoff = 0.5
            if self._stopping and not self._pending:
                return
