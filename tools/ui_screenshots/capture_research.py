#!/usr/bin/env python3
"""Capture the research-mode screens: consent, participation, clinician labels, admin research, audit.

    python3 capture_research.py --out /path/to/screenshots

Runs the app as Render does (one process serving the web build and the API),
in research mode with example study details. All accounts are fictional.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture import CORE, VENV_PY, WEB, Shots, log, psql, wait_http

DB = "flaria_research_ui"
APP = "http://127.0.0.1:8000"
ADMIN = ("admin123@gmail.com", f"Fl-{secrets.token_urlsafe(18)}", "ADMIN_FLOURISH")
PW = "Test-Account-2026!"


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict | None:
    req = urllib.request.Request(APP + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def sign_in(page, email: str, password: str, landing: str) -> None:
    page.goto(f"{APP}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{APP}{landing}", timeout=15000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    shots = Shots(args.out)
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 8000)) == 0:
            raise SystemExit("port 8000 is busy")
    psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    psql(f"CREATE DATABASE {DB} OWNER flaria")
    psql("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS citext;", DB)
    env = {**os.environ, "PYTHONUNBUFFERED": "1",
           "FLARIA_DATABASE_URL": f"postgresql://flaria:flaria@127.0.0.1:5432/{DB}",
           "FLARIA_ENV": "staging", "FLARIA_COOKIE_SECURE": "false", "FLARIA_CORS_ORIGINS": json.dumps([APP]),
           "FLARIA_JWT_SECRET": "capture-secret-capture-secret-capture-secret-0123456789",
           "FLARIA_WEB_DIST_PATH": str(WEB / "dist"), "FLARIA_DEMO_MODE": "true", "FLARIA_TICK_HZ": "10",
           "FLARIA_RESEARCH_MODE": "true",
           "FLARIA_STUDY_TITLE": "Voice and facial movement as early signs of neurological change",
           "FLARIA_STUDY_INSTITUTION": "Example University",
           "FLARIA_STUDY_INVESTIGATOR": "Okafor Onyedikachukwu Flourish",
           "FLARIA_STUDY_CONTACT_EMAIL": "flourishokafor13@gmail.com",
           "FLARIA_MODEL_PATH": "/tmp/stub-model/flaria_core.onnx",
           "FLARIA_MODEL_META_PATH": "/tmp/stub-model/flaria_core.meta.json"}
    api = subprocess.Popen([VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                           cwd=CORE, env=env, stdout=open("/tmp/capture-research-api.log", "w"), stderr=subprocess.STDOUT)
    try:
        wait_http(f"{APP}/health/ready")
        subprocess.run([VENV_PY, "scripts/create_admin.py", ADMIN[0], ADMIN[2]], cwd=CORE, check=True,
                       env={**env, "FLARIA_ADMIN_PASSWORD": ADMIN[1]}, capture_output=True)
        admin = call("POST", "/api/v1/auth/login", {"email": ADMIN[0], "password": ADMIN[1]})["access_token"]
        ids = {}
        for name, email in (("Dr Lena Moreau", "lena.moreau@example.org"), ("Amara Okafor", "amara.okafor@example.org"),
                            ("Daniel Reyes", "daniel.reyes@example.org"), ("Grace Mensah", "grace.mensah@example.org")):
            ids[email] = call("POST", "/api/v1/auth/register", {"email": email, "password": PW, "full_name": name})["user"]["id"]
        call("PUT", f"/api/v1/admin/users/{ids['lena.moreau@example.org']}/role", {"role": "CLINICIAN"}, admin)
        for p in ("amara.okafor@example.org", "daniel.reyes@example.org", "grace.mensah@example.org"):
            call("POST", "/api/v1/admin/care-team", {"clinician_id": ids["lena.moreau@example.org"], "patient_id": ids[p]}, admin)
        version = call("GET", "/api/v1/research/study")["version"]
        # Daniel joins and records two sessions; Grace joins then withdraws (both through the real API and stream).
        for email in ("daniel.reyes@example.org", "grace.mensah@example.org"):
            tok = call("POST", "/api/v1/auth/login", {"email": email, "password": PW})["access_token"]
            call("POST", "/api/v1/research/consent", {"consent_version": version, "agreements_accepted": True}, tok)
        for _ in range(2):
            subprocess.run([VENV_PY, "scripts/synthetic_patient.py", "--base", APP, "--email", "daniel.reyes@example.org",
                            "--password", PW, "--seconds", "12"], cwd=CORE, check=True, capture_output=True)
        grace = call("POST", "/api/v1/auth/login", {"email": "grace.mensah@example.org", "password": PW})["access_token"]
        call("POST", "/api/v1/research/withdraw", None, grace)
        lena = call("POST", "/api/v1/auth/login", {"email": "lena.moreau@example.org", "password": PW})["access_token"]
        call("PUT", f"/api/v1/research/participants/{ids['daniel.reyes@example.org']}/label",
             {"label": "stroke", "severity": "mild", "detail": "Confirmed on MRI"}, lena)
        log("seeded")

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            # ------------------------------------------------ participant: consent
            ctx = b.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            sign_in(page, "amara.okafor@example.org", PW, "/app")
            page.get_by_role("heading", name="Before you start: join the study").wait_for()
            page.wait_for_timeout(800)
            shots.page(page, "participant-consent-before-first-session")
            for box in page.get_by_role("checkbox").all():
                box.check()
            shots.el(page, 'section:has(h2:has-text("Before you start"))', "participant-consent-statements-ticked")
            page.get_by_role("button", name="I agree and want to take part").click()
            page.get_by_role("heading", name="Before you start: join the study").wait_for(state="detached")
            page.wait_for_timeout(600)
            shots.page(page, "participant-after-consent-ready-to-start")
            page.goto(f"{APP}/account")
            page.get_by_role("heading", name="Research participation").wait_for()
            page.wait_for_timeout(600)
            shots.el(page, 'section[aria-labelledby="research-heading"]', "participant-account-research-participation")
            page.get_by_role("button", name="Withdraw from the study").click()
            shots.el(page, 'section[aria-labelledby="research-heading"]', "participant-withdraw-confirmation")
            page.get_by_role("button", name="Cancel").click()
            ctx.close()
            # ------------------------------------------------ clinician: label
            ctx = b.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            sign_in(page, "lena.moreau@example.org", PW, "/app")
            page.get_by_label("Patient").select_option(label="Amara Okafor")
            page.get_by_text("Research label for Amara Okafor").wait_for()
            page.get_by_label("Confirmed group").select_option("healthy_control")
            page.get_by_role("button", name="Save label").click()
            page.get_by_text("Label saved.").wait_for()
            shots.el(page, 'section:has(h2:has-text("Research label"))', "clinician-research-label-saved")
            ctx.close()
            # ------------------------------------------------ admin: research
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page = ctx.new_page()
            sign_in(page, ADMIN[0], ADMIN[1], "/admin")
            page.goto(f"{APP}/admin#research")
            page.get_by_role("heading", name="Research", exact=True).wait_for()
            page.get_by_text("FLR-0001").wait_for()
            page.wait_for_timeout(800)
            shots.page(page, "admin-research-participants", full=True)
            with page.expect_download() as dl:
                page.get_by_role("button", name="Download export").click()
            path = dl.value.path()
            log(f"export downloaded: {dl.value.suggested_filename} ({Path(path).stat().st_size} bytes)")
            page.get_by_text("Export downloaded.").wait_for()
            shots.page(page, "admin-research-export-downloaded")
            page.goto(f"{APP}/admin#audit")
            page.get_by_label("Type").select_option("research")
            page.wait_for_timeout(1200)
            shots.page(page, "admin-audit-research-events")
            import shutil
            shutil.copy(path, args.out / "example-research-export.zip")
            b.close()
        log("done")
    finally:
        api.terminate()


if __name__ == "__main__":
    main()
