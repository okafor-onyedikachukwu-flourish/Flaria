#!/usr/bin/env python3
"""Seed a populated Flaria deployment and capture every admin console screen.

    python3 capture_admin.py --out /path/to/screenshots

Everything goes through the real system: the admin is created with
scripts/create_admin.py, clinicians are promoted and care teams assigned
through the admin API, and every session result comes from synthetic patient
streams processed by the live pipeline (scripts/synthetic_patient.py).

The only direct database edit moves earlier sessions back in time (session and
result timestamps), so the 14-day results chart has history to show.
All people are fictional test accounts.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture import API, APP, CORE, VENV_PY, WEB, Shots, log, psql, wait_http

DB = "flaria_admin_ui"
ADMIN_NAME, ADMIN_EMAIL = "ADMIN_FLOURISH", "admin123@gmail.com"
ADMIN_PASSWORD = os.environ.get("FLARIA_CAPTURE_ADMIN_PASSWORD") or f"Fl-{secrets.token_urlsafe(18)}"
PW = "Test-Account-2026!"

CLINICIANS = [("Dr Lena Moreau", "lena.moreau@example.org"), ("Dr Samuel Adeyemi", "samuel.adeyemi@example.org"),
              ("Dr Priya Nair", "priya.nair@example.org")]
# name, email, clinician index (None = unassigned), follow-up session flags
PATIENTS = [
    ("Daniel Reyes", "daniel.reyes@example.org", 0, ["--droop", "10", "--rough"]),
    ("Amara Okafor", "amara.okafor@example.org", 0, ["--rough"]),
    ("Hannah Schultz", "hannah.schultz@example.org", 0, []),
    ("Kenji Watanabe", "kenji.watanabe@example.org", 1, []),
    ("Fatima Haddad", "fatima.haddad@example.org", 1, ["--rough"]),
    ("Grace Mensah", "grace.mensah@example.org", 1, ["--droop", "5"]),
    ("Ines Duarte", "ines.duarte@example.org", 2, []),
    ("Oliver Bennett", "oliver.bennett@example.org", 2, None),   # baseline only, later disabled
    ("Lucas Ferreira", "lucas.ferreira@example.org", None, None),  # never streams, gets locked out
]


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict | None:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        if e.code in (401, 429) and path.endswith("/login"):
            return None
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300]!r}") from e


def login(email: str, password: str) -> str:
    out = call("POST", "/api/v1/auth/login", {"email": email, "password": password})
    assert out is not None, f"login failed for {email}"
    return out["access_token"]


def stream(email: str, name: str, flags: list[str], seed: int) -> None:
    subprocess.run([VENV_PY, str(CORE / "scripts" / "synthetic_patient.py"), "--base", API, "--email", email,
                    "--password", PW, "--full-name", name, "--seconds", "22", "--seed", str(seed), *flags],
                   check=True, cwd=CORE, capture_output=True)


def backdate_latest(email: str, days: float) -> None:
    psql(
        "WITH s AS (SELECT d.id FROM diagnostic_sessions d JOIN users u ON u.id = d.patient_id "
        f"WHERE u.email = '{email}' ORDER BY d.started_at DESC LIMIT 1), "
        f"a AS (UPDATE diagnostic_sessions d SET started_at = started_at - interval '{days} days', "
        f"ended_at = ended_at - interval '{days} days', last_heartbeat_at = last_heartbeat_at - interval '{days} days' "
        "FROM s WHERE d.id = s.id RETURNING d.id) "
        f"UPDATE session_reports r SET created_at = created_at - interval '{days} days', "
        f"updated_at = updated_at - interval '{days} days' FROM a WHERE r.session_id = a.id",
        DB,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    shots = Shots(args.out)
    rng = random.Random(11)

    for port in (8000, 4173):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"port {port} is busy")
    psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    psql(f"CREATE DATABASE {DB} OWNER flaria")
    psql("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS citext;", DB)
    env = {**os.environ,
           "FLARIA_DATABASE_URL": f"postgresql://flaria:flaria@127.0.0.1:5432/{DB}",
           "FLARIA_COOKIE_SECURE": "false",
           "FLARIA_CORS_ORIGINS": json.dumps([APP]),
           "FLARIA_JWT_SECRET": "capture-secret-capture-secret-capture-secret-0123456789",
           "FLARIA_MIN_BASELINE_SECONDS": "15",
           "FLARIA_MODEL_PATH": "/tmp/stub-model/flaria_core.onnx",
           "FLARIA_MODEL_META_PATH": "/tmp/stub-model/flaria_core.meta.json"}
    api = subprocess.Popen([VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                           cwd=CORE, env=env, stdout=open("/tmp/capture-admin-api.log", "w"), stderr=subprocess.STDOUT)
    web = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", "4173",
                            "--strictPort"], cwd=WEB, stdout=open("/tmp/capture-admin-web.log", "w"), stderr=subprocess.STDOUT)
    try:
        wait_http(f"{API}/health/ready")
        wait_http(APP)

        log(f"bootstrapping {ADMIN_NAME} <{ADMIN_EMAIL}> with scripts/create_admin.py")
        subprocess.run([VENV_PY, "scripts/create_admin.py", ADMIN_EMAIL, ADMIN_NAME], cwd=CORE, check=True,
                       env={**env, "FLARIA_ADMIN_PASSWORD": ADMIN_PASSWORD}, capture_output=True)
        admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)

        ids: dict[str, str] = {}
        for name, email in CLINICIANS:
            ids[email] = call("POST", "/api/v1/auth/register", {"email": email, "password": PW, "full_name": name})["user"]["id"]
            call("PUT", f"/api/v1/admin/users/{ids[email]}/role", {"role": "CLINICIAN"}, admin)
        for name, email, clin, _ in PATIENTS:
            ids[email] = call("POST", "/api/v1/auth/register", {"email": email, "password": PW, "full_name": name})["user"]["id"]
            if clin is not None:
                call("POST", "/api/v1/admin/care-team", {"clinician_id": ids[CLINICIANS[clin][1]], "patient_id": ids[email]}, admin)
        log("accounts and care teams created")

        streamers = [(n, e, f) for n, e, _, f in PATIENTS if f is not None or e.startswith("oliver")]
        rounds = [("baseline", lambda f: []), ("check-in", lambda f: []), ("follow-up", lambda f: f)]
        for label, flags_for in rounds:
            todo = [(n, e, flags_for(f or [])) for n, e, f in streamers
                    if not (label != "baseline" and e.startswith("oliver"))]
            log(f"streaming {label} sessions for {len(todo)} patients")
            with ThreadPoolExecutor(max_workers=len(todo)) as pool:
                list(pool.map(lambda t: stream(t[1], t[0], t[2], rng.randrange(10_000)), todo))
            time.sleep(2.5)
            if label == "baseline":
                for _, e, _ in todo:
                    backdate_latest(e, round(rng.uniform(7.0, 13.0), 2))
            elif label == "check-in":
                for _, e, _ in todo:
                    backdate_latest(e, round(rng.uniform(1.0, 6.0), 2))

        fatima = login("fatima.haddad@example.org", PW)
        latest = call("GET", "/api/v1/reports/latest", token=fatima)
        call("PUT", f"/api/v1/sessions/{latest['session_id']}/report/red-flags", {"red_flags": ["breathing"]}, fatima)
        moreau = login("lena.moreau@example.org", PW)
        call("POST", f"/api/v1/patients/{ids['amara.okafor@example.org']}/care-notes",
             {"body": "Spoke with Amara by phone. Hoarseness started with a cold on Monday; no breathing difficulty. "
                      "Rest the voice, fluids, repeat a session Friday. Call the clinic sooner if it worsens."}, moreau)
        call("PUT", f"/api/v1/admin/users/{ids['oliver.bennett@example.org']}/active", {"is_active": False}, admin)
        for _ in range(5):
            call("POST", "/api/v1/auth/login", {"email": "lucas.ferreira@example.org", "password": "not-the-password"})
        log("escalation follow-up, disabled account and lockout seeded")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page = ctx.new_page()
            page.goto(f"{APP}/login")
            page.get_by_label("Email").fill(ADMIN_EMAIL)
            page.get_by_label("Password", exact=True).fill(ADMIN_PASSWORD)
            shots.page(page, "admin-sign-in")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url(f"{APP}/admin", timeout=15000)
            page.get_by_role("heading", name="Overview").wait_for()
            page.get_by_text("Session results, last 14 days").wait_for()
            page.wait_for_timeout(1500)
            shots.page(page, "admin-overview")
            shots.page(page, "admin-overview-full", full=True)

            page.get_by_role("button", name="Users", exact=True).click()
            page.get_by_role("heading", name="Users").wait_for()
            page.locator("tbody tr").first.wait_for()
            page.wait_for_timeout(600)
            shots.page(page, "admin-users", full=True)

            page.get_by_label("Role for Grace Mensah").select_option("CLINICIAN")
            page.get_by_role("dialog").wait_for()
            page.wait_for_timeout(300)
            shots.page(page, "admin-users-role-change-confirmation")
            page.get_by_role("button", name="Cancel").click()

            page.get_by_label("Status").select_option("locked")
            page.get_by_text("Lucas Ferreira", exact=True).wait_for()
            page.wait_for_timeout(500)
            page.get_by_role("button", name="Unlock").click()
            page.get_by_text("Lucas Ferreira can sign in again.").wait_for()
            shots.page(page, "admin-users-unlocked-with-confirmation")
            page.get_by_label("Status").select_option("")

            for section, heading, name in (("care-teams", "Care teams", "admin-care-teams"),
                                           ("escalations", "Escalations", "admin-escalations"),
                                           ("audit", "Audit log", "admin-audit-log"),
                                           ("system", "System", "admin-system")):
                page.goto(f"{APP}/admin#{section}")
                page.get_by_role("heading", name=heading, exact=True).wait_for()
                page.wait_for_timeout(1200)
                shots.page(page, name, full=True)

            page.goto(f"{APP}/admin#audit")
            page.get_by_label("Type").select_option("admin")
            page.wait_for_timeout(1200)
            shots.page(page, "admin-audit-log-admin-actions")

            mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mp = mctx.new_page()
            mp.goto(f"{APP}/login")
            mp.get_by_label("Email").fill(ADMIN_EMAIL)
            mp.get_by_label("Password", exact=True).fill(ADMIN_PASSWORD)
            mp.get_by_role("button", name="Sign in").click()
            mp.wait_for_url(f"{APP}/admin", timeout=15000)
            mp.get_by_text("Session results, last 14 days").wait_for()
            mp.wait_for_timeout(1500)
            shots.page(mp, "admin-overview-mobile", full=True)
            browser.close()
        log("done")
    finally:
        api.terminate()
        web.terminate()


if __name__ == "__main__":
    main()
