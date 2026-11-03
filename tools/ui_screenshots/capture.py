#!/usr/bin/env python3
"""Capture every screen of Flaria from a live stack.

Runs the real API (PostgreSQL + ONNX engine) and the production web build, then
drives Chromium with fake camera/microphone devices fed from make_media.py.
Sessions go through the genuine pipeline: in-browser face mesh, AudioWorklet,
WebSocket, features, calibration, model, results engine.

    python3 capture.py --out /path/to/screenshots

Requires: PostgreSQL reachable as postgres superuser via `su postgres`, the
flaria-core virtualenv, `npm run build` already done in flaria-web, Playwright.
Test data only: accounts are fictional, the camera shows NASA's public-domain
astronaut portrait and the voices are synthetic.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "flaria-core"
WEB = ROOT / "flaria-web"
VENV_PY = os.environ.get("FLARIA_VENV_PY", "/home/claude/venv/bin/python")
MEDIA = Path(os.environ.get("FLARIA_MEDIA", "/tmp/flaria-media"))
API = "http://127.0.0.1:8000"
APP = "http://127.0.0.1:4173"
DB = "flaria_ui"
PATIENT = ("sample.patient@example.org", "Sample-Patient-2026!", "Sample Patient")
CLINICIAN = ("lena.moreau@example.org", "Clinician-Pass-2026!", "Dr Lena Moreau")
DANIEL = ("daniel.reyes@example.org", "Daniel-Reyes-2026!", "Daniel Reyes")
HUD = 'section[aria-label="Live camera with facial landmark overlay"]'


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def psql(sql: str, db: str = "postgres") -> str:
    return subprocess.run(["su", "postgres", "-c", f"psql -d {db} -tAq -c \"{sql}\""],
                          check=True, capture_output=True, text=True).stdout.strip()


def api_post(path: str, body: dict) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def wait_http(url: str, timeout: float = 40) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready")


class Shots:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.n = 0
        out.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> str:
        self.n += 1
        p = self.out / f"{self.n:02d}-{name}.png"
        log(f"shot {p.name}")
        return str(p)

    def page(self, page: Page, name: str, full: bool = False) -> None:
        page.wait_for_timeout(400)
        page.screenshot(path=self.path(name), full_page=full)

    def el(self, page: Page, selector: str, name: str) -> None:
        # The sticky app header would otherwise overlay the top of elements taller than the viewport.
        page.evaluate("document.querySelectorAll('header.sticky').forEach(h => h.style.position = 'static')")
        loc = page.locator(selector).first
        loc.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        loc.screenshot(path=self.path(name))
        page.evaluate("document.querySelectorAll('header.sticky').forEach(h => h.style.position = '')")


def launch(pw, voice: str) -> Browser:
    return pw.chromium.launch(headless=True, args=[
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-video-capture={MEDIA / 'camera.y4m'}",
        f"--use-file-for-fake-audio-capture={MEDIA / voice}",
        "--autoplay-policy=no-user-gesture-required",
    ])


def sign_in(page: Page, email: str, password: str) -> None:
    page.goto(f"{APP}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{APP}/app", timeout=15000)
    page.get_by_role("heading", name="Live session").wait_for()


def hud_status(page: Page, pattern: str, timeout: float) -> str:
    loc = page.locator(HUD).get_by_text(re.compile(pattern)).first
    loc.wait_for(timeout=timeout * 1000)
    return loc.inner_text()


def run_session(page: Page, shots: Shots, *, seconds: float, captures: list[tuple]) -> None:
    """Start a session; take (status-regex, shot-name, kind[, not-before-seconds]) captures as states appear;
    stop; wait for the result."""
    page.get_by_role("button", name="Start session").click()
    started = time.time()
    for capture in captures:
        pattern, name, kind = capture[:3]
        not_before = capture[3] if len(capture) > 3 else 0.0
        remaining = max(5.0, seconds + 30 - (time.time() - started))
        status = hud_status(page, pattern, remaining)
        while time.time() - started < not_before:
            page.wait_for_timeout(250)
        page.wait_for_timeout(1500)
        fps = page.locator(HUD).get_attribute("data-tracking-fps")
        detect = page.locator(HUD).get_attribute("data-detect-ms")
        log(f"  status {status!r} after {time.time() - started:.0f}s, face tracking {fps}/s, detect {detect} ms")
        if kind == "viewport":
            page.evaluate("window.scrollTo(0, 0)")
            shots.page(page, name)
        elif kind == "privacy":
            page.evaluate("window.scrollTo(0, 0)")
            page.get_by_text("Raw media never stored").click()
            page.wait_for_timeout(500)
            shots.page(page, name)
            page.get_by_text("Raw media never stored").click()
        elif kind == "biomarkers":
            shots.el(page, 'section[aria-labelledby="biomarkers-heading"]', name)
        elif kind == "anomaly":
            shots.el(page, 'section[aria-labelledby="anomaly-heading"]', name)
    while time.time() - started < seconds:
        page.wait_for_timeout(500)
    page.evaluate("window.scrollTo(0, 0)")
    page.get_by_role("button", name="Stop session").click()
    page.locator("#result-heading").wait_for(timeout=20000)
    page.wait_for_timeout(1200)


def daniel_sessions(done: threading.Event) -> None:
    email, password, name = DANIEL
    for extra in ([], ["--droop", "10", "--rough"]):
        subprocess.run([VENV_PY, str(CORE / "scripts" / "synthetic_patient.py"), "--base", API, "--email", email,
                        "--password", password, "--full-name", name, "--seconds", "30", *extra],
                       check=True, cwd=CORE, capture_output=True)
        log(f"Daniel session done {extra}")
    done.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    shots = Shots(args.out)

    for port in (8000, 4173):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"port {port} is already in use; stop the process holding it first")
    log("fresh database")
    psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    psql(f"CREATE DATABASE {DB} OWNER flaria")
    psql("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS citext;", DB)

    env = {**os.environ,
           "FLARIA_DATABASE_URL": f"postgresql://flaria:flaria@127.0.0.1:5432/{DB}",
           "FLARIA_COOKIE_SECURE": "false",
           "FLARIA_CORS_ORIGINS": json.dumps([APP]),
           "FLARIA_JWT_SECRET": "capture-secret-capture-secret-capture-secret-0123456789",
           "FLARIA_MIN_BASELINE_SECONDS": "15",
           "FLARIA_MODEL_PATH": os.environ.get("FLARIA_MODEL_PATH", "/tmp/stub-model/flaria_core.onnx"),
           "FLARIA_MODEL_META_PATH": os.environ.get("FLARIA_MODEL_META_PATH", "/tmp/stub-model/flaria_core.meta.json")}
    api = subprocess.Popen([VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000",
                            "--ws-max-size", "65536"], cwd=CORE, env=env, stdout=open("/tmp/capture-api.log", "w"),
                           stderr=subprocess.STDOUT)
    web = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", "4173",
                            "--strictPort"], cwd=WEB, stdout=open("/tmp/capture-web.log", "w"), stderr=subprocess.STDOUT)
    try:
        wait_http(f"{API}/health/ready")
        wait_http(APP)
        log("stack ready")

        api_post("/api/v1/auth/register", {"email": CLINICIAN[0], "password": CLINICIAN[1], "full_name": CLINICIAN[2]})
        psql(f"UPDATE users SET role = 'CLINICIAN', token_version = token_version + 1 WHERE email = '{CLINICIAN[0]}'", DB)
        daniel_done = threading.Event()
        daniel_sessions(daniel_done)  # before the browser: this sandbox has one CPU core

        with sync_playwright() as pw:
            # ---------------------------------------------------------------- public pages
            browser = launch(pw, "voice_healthy.wav")
            desk = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = desk.new_page()
            page.goto(APP)
            page.wait_for_timeout(2500)  # let the hero trace draw
            shots.page(page, "landing-hero")
            shots.page(page, "landing-full-page", full=True)
            mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mpage = mobile.new_page()
            mpage.goto(APP)
            mpage.wait_for_timeout(2000)
            shots.page(mpage, "landing-mobile", full=True)
            mpage.goto(f"{APP}/no-such-page")
            shots.page(mpage, "not-found-mobile")
            mobile.close()

            page.goto(f"{APP}/login")
            shots.page(page, "sign-in")
            page.get_by_label("Email").fill(PATIENT[0])
            page.get_by_label("Password", exact=True).fill("wrong-password")
            page.get_by_role("button", name="Sign in").click()
            page.get_by_text("Email or password is incorrect").wait_for()
            shots.page(page, "sign-in-error")

            page.goto(f"{APP}/register")
            page.get_by_label("Full name").fill(PATIENT[2])
            page.get_by_label("Email").fill(PATIENT[0])
            page.get_by_label("Password", exact=True).fill("short")
            page.get_by_role("button", name="Create account").click()
            shots.page(page, "register-password-rules")
            page.get_by_label("Password", exact=True).fill(PATIENT[1])
            page.get_by_role("button", name="Create account").click()
            page.wait_for_url(f"{APP}/app", timeout=15000)
            page.get_by_role("heading", name="Live session").wait_for()
            page.wait_for_timeout(1500)
            shots.page(page, "dashboard-before-first-session", full=True)

            # ---------------------------------------------------------------- session 1: baseline
            log("session 1 (baseline)")
            run_session(page, shots, seconds=50, captures=[
                ("^Calibrating$", "live-calibrating", "viewport"),
                ("^Stable$", "live-stable-face-mesh", "viewport"),
                ("^Stable$", "privacy-details", "privacy"),
                ("^Stable$", "biomarker-sparklines", "biomarkers"),
            ])
            shots.el(page, '[aria-labelledby="result-heading"]', "result-first-session-baseline")

            # ---------------------------------------------------------------- session 2: no change
            log("session 2 (same voice, compared with baseline)")
            run_session(page, shots, seconds=30, captures=[("^Stable$", "live-stable-compared-with-baseline", "anomaly", 24.0)])
            shots.el(page, '[aria-labelledby="result-heading"]', "result-no-change")
            browser.close()

            # ---------------------------------------------------------------- session 3: rough voice
            log("session 3 (rough voice)")
            browser = launch(pw, "voice_rough.wav")
            desk = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = desk.new_page()
            sign_in(page, PATIENT[0], PATIENT[1])
            run_session(page, shots, seconds=36, captures=[
                ("^(Watch|Elevated)$", "live-watch-or-elevated", "viewport"),
                ("^(Watch|Elevated)$", "live-anomaly-rising", "anomaly"),
            ])
            shots.el(page, '[aria-labelledby="result-heading"]', "result-voice-change")
            page.get_by_label(re.compile("shortness of breath|struggling to breathe", re.I)).first.check()
            page.get_by_role("button", name="Update result").click()
            page.get_by_role("heading", name=re.compile("emergency", re.I)).first.wait_for(timeout=10000)
            shots.el(page, '[aria-labelledby="result-heading"]', "result-emergency-after-symptom-check")
            page.locator("details", has_text="Clinical reference").locator("summary").click()
            shots.el(page, "details:has-text('Clinical reference')", "clinical-reference")
            shots.el(page, 'section[aria-labelledby="history-heading"]', "session-history")

            # ---------------------------------------------------------------- clinician
            daniel_done.wait(timeout=180)
            for email in (PATIENT[0], DANIEL[0]):
                psql("INSERT INTO care_team (clinician_id, patient_id) SELECT c.id, p.id FROM users c, users p "
                     f"WHERE c.email = '{CLINICIAN[0]}' AND p.email = '{email}' ON CONFLICT DO NOTHING", DB)
            log("clinician views")
            cctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            cpage = cctx.new_page()
            sign_in(cpage, CLINICIAN[0], CLINICIAN[1])
            cpage.get_by_label("Patient").select_option(label=DANIEL[2])
            cpage.locator("#result-heading").wait_for(timeout=15000)
            cpage.wait_for_timeout(1200)
            shots.page(cpage, "clinician-dashboard-patient-selected")
            shots.el(cpage, '[aria-labelledby="result-heading"]', "clinician-view-result-stroke-pattern")
            cpage.get_by_label("Patient").select_option(label=PATIENT[2])
            cpage.locator("#result-heading").wait_for(timeout=15000)
            cpage.wait_for_timeout(1000)
            cpage.get_by_label(re.compile("^Advice for")).fill(
                "Thanks for running these. The voice change fits the cold you described on Monday. Rest your voice, drink "
                "plenty of fluids and run another session on Friday. If your breathing gets worse before then, call 999.")
            shots.el(cpage, '[aria-labelledby="result-heading"]', "clinician-writing-care-note")
            cpage.get_by_role("button", name="Save note").click()
            cpage.get_by_text("Thanks for running these").last.wait_for()
            cctx.close()

            # ---------------------------------------------------------------- patient sees the note
            page.reload()
            page.locator("#result-heading").wait_for(timeout=15000)
            page.get_by_text("Thanks for running these").first.wait_for(timeout=10000)
            shots.el(page, '[aria-labelledby="result-heading"]', "patient-sees-care-team-advice")
            shots.page(page, "dashboard-after-sessions", full=True)

            mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mp = mctx.new_page()
            sign_in(mp, PATIENT[0], PATIENT[1])
            mp.locator("#result-heading").wait_for(timeout=15000)
            mp.wait_for_timeout(1000)
            shots.page(mp, "dashboard-mobile", full=True)
            browser.close()
        log("done")
    finally:
        api.terminate()
        web.terminate()


if __name__ == "__main__":
    main()
