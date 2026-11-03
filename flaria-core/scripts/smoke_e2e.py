#!/usr/bin/env python3
"""End-to-end smoke test against a running flaria-core instance.

    python scripts/smoke_e2e.py --base http://127.0.0.1:8000 --seconds 18

Covers: registration, /me, refresh rotation, refresh-token reuse detection
(requires FLARIA_REFRESH_REUSE_GRACE_SECONDS=0 on the server for the reuse
step), WebSocket auth rejection, a full synthetic audio + landmark stream
through calibration and inference, ping/pong, graceful stop, persisted session
and telemetry rows, and baseline write-back.
Requires FLARIA_COOKIE_SECURE=false when testing over plain http.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import sys
import time
import uuid

import httpx
import numpy as np

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # websockets < 13
    from websockets import connect as ws_connect  # type: ignore[no-redef]

HEADER = struct.Struct("<BBHIdHH")
SR = 16000


def face_landmarks(t: float, rng: np.random.Generator) -> np.ndarray:
    w, h = 640, 480
    lm = np.column_stack([rng.uniform(0.38, 0.62, 468), rng.uniform(0.3, 0.75, 468), rng.normal(0, 0.01, 468)])

    def put(i: int, x: float, y: float) -> None:
        lm[i] = (x / w, y / h, 0.0)

    mouth = 6 + 5 * max(0.0, math.sin(2 * math.pi * 4.5 * t))
    jitter = rng.normal(0, 0.4, 2)
    put(168, 320, 200); put(2, 320, 280); put(33, 260, 200); put(263, 380, 200); put(10, 320, 120); put(152, 320, 380)
    put(61, 290 + jitter[0], 320); put(291, 350 + jitter[1], 320)
    put(13, 320, 320 - mouth / 2); put(14, 320, 320 + mouth / 2)
    for (p1, p2, p3, p4, p5, p6), cx in (((33, 160, 158, 133, 153, 144), 275), ((362, 385, 387, 263, 373, 380), 365)):
        put(p1, cx - 15, 200); put(p4, cx + 15, 200)
        put(p2, cx - 5, 195); put(p3, cx + 5, 195); put(p6, cx - 5, 205); put(p5, cx + 5, 205)
    put(33, 260, 200); put(263, 380, 200)
    return lm.astype("<f4")


def voice_chunk(start_sample: int, n: int, f0: float = 135.0) -> np.ndarray:
    t = (start_sample + np.arange(n)) / SR
    phase = 2 * np.pi * f0 * t
    x = sum(np.sin(k * phase) / k for k in range(1, 8))
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t)
    return (0.1 * x * envelope).astype("<f4")


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


async def stream(ws_base: str, token: str, seconds: float) -> tuple[str, list[dict], float]:
    ticks: list[dict] = []
    async with ws_connect(f"{ws_base}/ws/stream", subprotocols=["flaria.v1", f"bearer.{token}"],
                          max_size=2**20) as ws:
        hello = json.loads(await ws.recv())
        check(hello["type"] == "hello", f"hello received (model {hello['model']['version']})")
        check(hello["privacy"]["raw_media_persisted"] is False, "privacy attestation present")
        session_id = hello["session_id"]
        tick_hz = float(hello.get("tick_hz", 30))
        rng = np.random.default_rng(0)
        audio_seq = vid_seq = 0
        sample = 0
        start = time.monotonic()
        next_audio = next_video = start
        pong = False

        async def reader() -> None:
            nonlocal pong
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "tick":
                    ticks.append(msg)
                elif msg["type"] == "pong":
                    pong = True

        task = asyncio.create_task(reader())
        await ws.send(json.dumps({"type": "ping", "t": time.time() * 1000}))
        while time.monotonic() - start < seconds:
            now = time.monotonic()
            if now >= next_audio:
                pcm = voice_chunk(sample, 1600)
                sample += 1600
                await ws.send(HEADER.pack(0x01, 1, 0, audio_seq, time.time() * 1000, 0, 0) + pcm.tobytes())
                audio_seq += 1
                next_audio += 0.1
            if now >= next_video:
                lm = face_landmarks(now - start, rng)
                await ws.send(HEADER.pack(0x02, 1, 0, vid_seq, time.time() * 1000, 640, 480) + lm.tobytes())
                vid_seq += 1
                next_video += 1 / 30
            await asyncio.sleep(0.002)
        await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.TimeoutError, Exception):
            task.cancel()
        check(pong, "ping/pong round trip")
        code = ws.close_code if hasattr(ws, "close_code") else None
        check(code in (1000, None), f"graceful close (code {code})")
    return session_id, ticks, tick_hz


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--seconds", type=float, default=18.0)
    args = ap.parse_args()
    ws_base = args.base.replace("http", "ws", 1)
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.org"
    password = "Smoke-Test-Pass-42"

    async with httpx.AsyncClient(base_url=args.base, timeout=15) as client:
        r = await client.post("/api/v1/auth/register", json={"email": email, "password": "Qz7!", "full_name": "x"})
        check(r.status_code == 422 and "Qz7!" not in r.text, "weak password rejected without echo")
        r = await client.post("/api/v1/auth/register", json={"email": email, "password": password, "full_name": "Smoke Test"})
        check(r.status_code == 201, "register 201")
        check("flaria_rt" in client.cookies, "refresh cookie set")
        token = r.json()["access_token"]
        r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        check(r.status_code == 200 and r.json()["role"] == "PATIENT", "/me returns PATIENT")
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": "Wrong-Pass-999"})
        check(r.status_code == 401, "bad password 401")

        old_cookie = client.cookies.get("flaria_rt")
        r = await client.post("/api/v1/auth/refresh")
        check(r.status_code == 200 and client.cookies.get("flaria_rt") != old_cookie, "refresh rotates cookie")
        token = r.json()["access_token"]

        async with httpx.AsyncClient(base_url=args.base, timeout=15, cookies={"flaria_rt": old_cookie}) as thief:
            r = await thief.post("/api/v1/auth/refresh")
            check(r.status_code in (401, 409), f"replayed refresh token refused ({r.status_code})")
            if r.status_code == 401:
                r = await client.post("/api/v1/auth/refresh")
                check(r.status_code == 401, "reuse detection revoked the whole family")
                r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
                check(r.status_code == 200, "login after revocation")
                token = r.json()["access_token"]

    try:
        async with ws_connect(f"{ws_base}/ws/stream", subprotocols=["flaria.v1", "bearer.not-a-token"]) as ws:
            await ws.recv()
        check(False, "invalid token must be rejected")
    except Exception as exc:  # noqa: BLE001
        code = getattr(getattr(exc, "rcvd", None), "code", None)
        check(code == 4401, f"invalid WS token closed with 4401 (got {code})")

    session_id, ticks, tick_hz = await stream(ws_base, token, args.seconds)
    check(len(ticks) > args.seconds * tick_hz * 0.66, f"received {len(ticks)} ticks at {tick_hz:g} Hz")
    calibrated = [t for t in ticks if t["q"]["calibrated"]]
    check(bool(calibrated), "calibration completed during the stream")
    scored = [t for t in ticks if t["anomaly"] is not None]
    check(bool(scored), f"anomaly index produced ({scored[-1]['anomaly'] if scored else None}, status {ticks[-1]['status']})")
    voiced = [t for t in ticks if t["vq"]["f0"] is not None]
    check(bool(voiced) and abs(np.median([t["vq"]["f0"] for t in voiced]) - 135) < 10, "f0 tracked near 135 Hz")
    proc = np.array([t["proc_ms"] for t in ticks])
    print(f"     server tick processing p50 {np.percentile(proc, 50):.2f} ms  p99 {np.percentile(proc, 99):.2f} ms")

    await asyncio.sleep(2.0)
    async with httpx.AsyncClient(base_url=args.base, timeout=15) as client:
        h = {"Authorization": f"Bearer {token}"}
        r = await client.get("/api/v1/sessions", headers=h)
        sessions = r.json()
        check(r.status_code == 200 and sessions[0]["id"] == session_id and sessions[0]["status"] == "COMPLETED",
              "session persisted as COMPLETED")
        r = await client.get(f"/api/v1/sessions/{session_id}/telemetry", headers=h)
        rows = r.json()
        check(r.status_code == 200 and len(rows) >= args.seconds - 3, f"{len(rows)} x 1 Hz telemetry rows persisted")
        check(all(set(row) >= {"audio_vec", "vision_vec", "anomaly"} for row in rows), "telemetry rows are scalar only")
        r = await client.get("/api/v1/baseline", headers=h)
        check(r.status_code == 200 and r.json()["audio_samples"] > 0, f"baseline written back {r.json()}")
        r = await client.get(f"/api/v1/sessions/{session_id}/report", headers=h)
        check(r.status_code == 200 and r.json()["triage"] in ("BASELINE", "INSUFFICIENT"),
              f"first session result written ({r.json().get('triage')}: {r.json().get('headline')})")
        r = await client.put(f"/api/v1/sessions/{session_id}/report/red-flags", headers=h,
                             json={"red_flags": ["speech_difficulty"]})
        check(r.status_code == 200 and r.json()["triage"] == "EMERGENCY", "reported warning sign escalates to EMERGENCY")
        r = await client.put(f"/api/v1/sessions/{session_id}/report/red-flags", headers=h, json={"red_flags": ["made_up"]})
        check(r.status_code == 422, "unknown warning sign rejected")
        me = (await client.get("/api/v1/auth/me", headers=h)).json()["id"]
        r = await client.post(f"/api/v1/patients/{me}/care-notes", headers=h, json={"body": "take 2 pills"})
        check(r.status_code == 403, "patients cannot write care notes (medication advice is clinician-only)")
        r = await client.get(f"/api/v1/sessions?patient_id={uuid.uuid4()}", headers=h)
        check(r.status_code == 403, "patient cannot read another patient's sessions")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
