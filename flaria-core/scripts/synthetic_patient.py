#!/usr/bin/env python3
"""Stream a synthetic patient session through the real API (development and demo data).

    python scripts/synthetic_patient.py --email dan@example.org --password '...' --seconds 25
    python scripts/synthetic_patient.py --email dan@example.org --password '...' --droop 9 --rough

Sends generated face landmarks and generated voice, never real media. Use it to
build a baseline, then to produce a changed session (lip-corner droop and/or a
rough voice) and check how results, triage and the clinician view respond.
Registers the account on first use.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import time

import httpx
import numpy as np

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # websockets < 13
    from websockets import connect as ws_connect  # type: ignore[no-redef]

HEADER = struct.Struct("<BBHIdHH")
SR = 16000


def landmarks(t: float, rng: np.random.Generator, droop_px: float) -> np.ndarray:
    w, h = 640, 480
    lm = np.column_stack([rng.uniform(0.38, 0.62, 468), rng.uniform(0.3, 0.75, 468), rng.normal(0, 0.01, 468)])

    def put(i: int, x: float, y: float) -> None:
        lm[i] = (x / w, y / h, 0.0)

    mouth = 6 + 5 * max(0.0, math.sin(2 * math.pi * 4.5 * t)) * (1 - min(droop_px, 12) / 24)
    j = rng.normal(0, 0.4, 4)
    put(168, 320 + j[2], 200); put(2, 320 + j[2], 280); put(33, 260, 200); put(263, 380, 200); put(10, 320, 120); put(152, 320, 380)
    put(61, 290 + j[0], 320); put(291, 350 + j[1], 320 + droop_px)
    put(13, 320, 320 - mouth / 2); put(14, 320, 320 + mouth / 2)
    for (p1, p2, p3, p4, p5, p6), cx in (((33, 160, 158, 133, 153, 144), 275), ((362, 385, 387, 263, 373, 380), 365)):
        put(p1, cx - 15, 200); put(p4, cx + 15, 200)
        put(p2, cx - 5, 195 + j[3] * 0.3); put(p3, cx + 5, 195); put(p6, cx - 5, 205); put(p5, cx + 5, 205)
    put(33, 260, 200); put(263, 380, 200)
    return lm.astype("<f4")


class Voice:
    """Pulse-train voice at 16 kHz with controllable cycle-to-cycle perturbation."""

    def __init__(self, rough: bool, seed: int) -> None:
        self.rng = np.random.default_rng(seed)
        self.jitter, self.shimmer = (0.03, 0.2) if rough else (0.004, 0.03)
        self.noise = 0.02 if rough else 0.004
        self.next_pulse = 0.0
        self.pos = 0

    def chunk(self, n: int) -> np.ndarray:
        x = np.zeros(n)
        while self.next_pulse < self.pos + n:
            i = int(self.next_pulse) - self.pos
            if 0 <= i < n:
                x[i] = 1 + self.rng.normal(0, self.shimmer)
            self.next_pulse += SR / 132.0 * (1 + self.rng.normal(0, self.jitter))
        k = np.arange(48)
        pulse = np.exp(-k / 12) * np.sin(2 * np.pi * 560 * k / SR)
        y = np.convolve(x, pulse)[:n]
        t = (self.pos + np.arange(n)) / SR
        y *= 0.6 + 0.4 * np.clip(np.sin(2 * np.pi * 4.0 * t), 0, None)
        y = 0.2 * y / max(1e-6, np.max(np.abs(y))) + self.rng.normal(0, self.noise, n)
        self.pos += n
        return y.astype("<f4")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--full-name", default="Synthetic Patient")
    ap.add_argument("--seconds", type=float, default=25.0)
    ap.add_argument("--droop", type=float, default=0.0, help="left lip-corner droop in pixels")
    ap.add_argument("--rough", action="store_true", help="raise jitter, shimmer and breath noise")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    async with httpx.AsyncClient(base_url=args.base, timeout=15) as client:
        r = await client.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
        if r.status_code == 401:
            r = await client.post("/api/v1/auth/register",
                                  json={"email": args.email, "password": args.password, "full_name": args.full_name})
        r.raise_for_status()
        token = r.json()["access_token"]

    rng = np.random.default_rng(args.seed)
    voice = Voice(args.rough, args.seed)
    ws_url = args.base.replace("http", "ws", 1) + "/ws/stream"
    async with ws_connect(ws_url, subprotocols=["flaria.v1", f"bearer.{token}"], max_size=2**20) as ws:
        hello = json.loads(await ws.recv())
        session_id = hello["session_id"]
        last: dict = {}

        async def reader() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "tick":
                    last.update(msg)

        task = asyncio.create_task(reader())
        start = time.monotonic()
        next_audio = next_video = start
        a_seq = v_seq = 0
        while time.monotonic() - start < args.seconds:
            now = time.monotonic()
            if now >= next_audio:
                await ws.send(HEADER.pack(0x01, 1, 0, a_seq, time.time() * 1000, 0, 0) + voice.chunk(1600).tobytes())
                a_seq += 1
                next_audio += 0.1
            if now >= next_video:
                lm = landmarks(now - start, rng, args.droop)
                await ws.send(HEADER.pack(0x02, 1, 0, v_seq, time.time() * 1000, 640, 480) + lm.tobytes())
                v_seq += 1
                next_video += 1 / 30
            await asyncio.sleep(0.002)
        await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(task, 5)
        except Exception:  # noqa: BLE001
            task.cancel()
    print(json.dumps({"session_id": session_id, "last_status": last.get("status"), "last_anomaly": last.get("anomaly")}))


if __name__ == "__main__":
    asyncio.run(main())
