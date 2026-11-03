#!/usr/bin/env python3
"""Capture the password screens and the e-mails users receive.

    python3 capture_passwords.py --out /path/to/screenshots

Runs the real API with SMTP delivery into a local capturing server (aiosmtpd),
so the reset link used in the browser is the one the e-mail actually carried.
All accounts are fictional test accounts.
"""
from __future__ import annotations

import argparse
import email
import json
import os
import re
import secrets
import socket
import subprocess
import time
from email import policy
from email.message import EmailMessage
from pathlib import Path

from aiosmtpd.controller import Controller
from playwright.sync_api import Page, sync_playwright

from capture import API, APP, CORE, VENV_PY, WEB, Shots, log, psql, wait_http
from capture_admin import call, login

DB = "flaria_pw_ui"
SMTP_PORT = 2526
ADMIN_NAME, ADMIN_EMAIL = "ADMIN_FLOURISH", "admin123@gmail.com"
ADMIN_PW = f"Fl-{secrets.token_urlsafe(18)}"
ADMIN_PW_NEW = f"Fl-{secrets.token_urlsafe(18)}"
PATIENT = ("Amara Okafor", "amara.okafor@example.org", "Amara-Original-2026!")
PATIENT_NEW = "Amara-Brand-New-2026!"
INBOX: list[EmailMessage] = []


class Capture:
    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001, N802
        INBOX.append(email.message_from_bytes(envelope.content, policy=policy.default))
        return "250 OK"


def wait_for_mail(subject: str, to: str, timeout: float = 15) -> EmailMessage:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for m in reversed(INBOX):
            if m["Subject"] == subject and m["To"] == to:
                return m
        time.sleep(0.2)
    raise RuntimeError(f"no '{subject}' e-mail for {to}")


def show_email(page: Page, shots: Shots, msg: EmailMessage, name: str) -> None:
    """Render the HTML part as a mail client would, with the envelope headers above it."""
    html = msg.get_body(preferencelist=("html",)).get_content()
    header = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:13px;color:#4A5A66;'
        'background:#fff;border-bottom:1px solid #C9D4D8;padding:14px 20px;line-height:1.6">'
        f'<b style="color:#142028">{msg["Subject"]}</b><br>From: {msg["From"].replace("<", "&lt;").replace(">", "&gt;")}<br>'
        f'To: {msg["To"]}</div>'
    )
    page.set_content(re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + header, html, count=1))
    page.wait_for_timeout(300)
    page.screenshot(path=shots.path(name), full_page=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    shots = Shots(args.out)
    for port in (8000, 4173, SMTP_PORT):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"port {port} is busy")
    smtp = Controller(Capture(), hostname="127.0.0.1", port=SMTP_PORT)
    smtp.start()

    psql(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    psql(f"CREATE DATABASE {DB} OWNER flaria")
    psql("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS citext;", DB)
    env = {**os.environ,
           "FLARIA_DATABASE_URL": f"postgresql://flaria:flaria@127.0.0.1:5432/{DB}",
           "FLARIA_COOKIE_SECURE": "false",
           "FLARIA_CORS_ORIGINS": json.dumps([APP]),
           "FLARIA_PUBLIC_APP_URL": APP,
           "FLARIA_MAIL_BACKEND": "smtp", "FLARIA_SMTP_HOST": "127.0.0.1", "FLARIA_SMTP_PORT": str(SMTP_PORT),
           "FLARIA_SMTP_STARTTLS": "false", "FLARIA_MAIL_FROM": "Flaria <no-reply@flaria.example.org>",
           "FLARIA_JWT_SECRET": "capture-secret-capture-secret-capture-secret-0123456789",
           "FLARIA_MODEL_PATH": "/tmp/stub-model/flaria_core.onnx",
           "FLARIA_MODEL_META_PATH": "/tmp/stub-model/flaria_core.meta.json"}
    api = subprocess.Popen([VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                           cwd=CORE, env=env, stdout=open("/tmp/capture-pw-api.log", "w"), stderr=subprocess.STDOUT)
    web = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", "4173",
                            "--strictPort"], cwd=WEB, stdout=open("/tmp/capture-pw-web.log", "w"), stderr=subprocess.STDOUT)
    try:
        wait_http(f"{API}/health/ready")
        wait_http(APP)
        subprocess.run([VENV_PY, "scripts/create_admin.py", ADMIN_EMAIL, ADMIN_NAME], cwd=CORE, check=True,
                       env={**env, "FLARIA_ADMIN_PASSWORD": ADMIN_PW}, capture_output=True)
        call("POST", "/api/v1/auth/register", {"email": PATIENT[1], "password": PATIENT[2], "full_name": PATIENT[0]})
        for name, mail in (("Dr Lena Moreau", "lena.moreau@example.org"), ("Kenji Watanabe", "kenji.watanabe@example.org")):
            call("POST", "/api/v1/auth/register", {"email": mail, "password": "Test-Account-2026!", "full_name": name})
        log("accounts ready")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 860}, device_scale_factor=1)
            page = ctx.new_page()

            # ---------------------------------------------------------------- forgot password (patient)
            page.goto(f"{APP}/login")
            page.get_by_text("Forgot your password?").wait_for()
            shots.page(page, "sign-in-with-forgot-link")
            page.get_by_text("Forgot your password?").click()
            page.get_by_role("heading", name="Reset your password").wait_for()
            page.get_by_label("Email").fill(PATIENT[1])
            shots.page(page, "forgot-password")
            page.get_by_role("button", name="Send reset link").click()
            page.get_by_role("heading", name="Check your email").wait_for()
            shots.page(page, "forgot-password-check-your-email")

            reset_msg = wait_for_mail("Reset your Flaria password", PATIENT[1])
            text = reset_msg.get_body(preferencelist=("plain",)).get_content()
            link = re.search(r"(https?://\S+/reset-password#token=[A-Za-z0-9_-]+)", text).group(1)
            mail_page = ctx.new_page()
            mail_page.set_viewport_size({"width": 680, "height": 760})
            show_email(mail_page, shots, reset_msg, "email-reset-link")

            page.goto(link)
            page.get_by_role("heading", name="Choose a new password").wait_for()
            if "#token=" in page.url:
                raise RuntimeError("token was not stripped from the address bar")
            page.get_by_label("New password", exact=True).fill("short")
            page.get_by_label("Confirm new password", exact=True).fill("different")
            page.get_by_role("button", name="Save new password").click()
            page.wait_for_timeout(300)
            shots.page(page, "reset-password-validation")
            page.get_by_label("New password", exact=True).fill(PATIENT_NEW)
            page.get_by_label("Confirm new password", exact=True).fill(PATIENT_NEW)
            page.get_by_role("button", name="Save new password").click()
            page.get_by_role("heading", name="Password changed").wait_for()
            shots.page(page, "reset-password-done")
            show_email(mail_page, shots, wait_for_mail("Your Flaria password was changed", PATIENT[1]), "email-password-changed")

            page.goto("about:blank")  # a fresh load: same-path hash navigation wouldn't remount the page
            page.goto(link)  # the same link a second time
            page.get_by_label("New password", exact=True).fill("Another-Password-2026!")
            page.get_by_label("Confirm new password", exact=True).fill("Another-Password-2026!")
            page.get_by_role("button", name="Save new password").click()
            page.get_by_text("invalid, already used or expired").wait_for()
            shots.page(page, "reset-link-already-used")

            # ---------------------------------------------------------------- account page (admin)
            page.goto(f"{APP}/login")
            page.get_by_label("Email").fill(ADMIN_EMAIL)
            page.get_by_label("Password", exact=True).fill(ADMIN_PW)
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url(f"{APP}/admin", timeout=15000)
            page.get_by_text("Account and password").click()
            page.get_by_role("heading", name="Account", exact=True).wait_for()
            page.wait_for_timeout(600)
            shots.page(page, "account-settings", full=True)
            page.get_by_label("Current password", exact=True).fill("not-my-password")
            page.get_by_label("New password", exact=True).fill(ADMIN_PW_NEW)
            page.get_by_label("Confirm new password", exact=True).fill(ADMIN_PW_NEW)
            page.get_by_role("button", name="Change password").click()
            page.get_by_text("Your current password is incorrect.").wait_for()
            shots.page(page, "account-wrong-current-password", full=True)
            page.get_by_label("Current password", exact=True).fill(ADMIN_PW)
            page.get_by_role("button", name="Change password").click()
            page.get_by_text("Password changed. Other devices were signed out").wait_for()
            page.wait_for_timeout(600)
            shots.page(page, "account-password-changed", full=True)

            # ---------------------------------------------------------------- admin: send a reset link
            page.goto(f"{APP}/admin#users")
            page.get_by_role("button", name="Email Kenji Watanabe a password reset link").click()
            page.get_by_role("dialog").wait_for()
            page.wait_for_timeout(300)
            shots.page(page, "admin-send-reset-link-confirmation")
            page.get_by_role("dialog").get_by_role("button", name="Send reset link").click()
            page.get_by_text("Reset link sent to kenji.watanabe@example.org.").wait_for()
            shots.page(page, "admin-reset-link-sent")
            show_email(mail_page, shots, wait_for_mail("Reset your Flaria password", "kenji.watanabe@example.org"),
                       "email-reset-link-sent-by-admin")
            page.goto(f"{APP}/admin#audit")
            page.get_by_role("heading", name="Audit log", exact=True).wait_for()
            page.wait_for_timeout(1000)
            shots.page(page, "admin-audit-log-password-events")

            mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            mp = mctx.new_page()
            mp.goto(f"{APP}/login")
            mp.get_by_label("Email").fill(PATIENT[1])
            mp.get_by_label("Password", exact=True).fill(PATIENT_NEW)
            mp.get_by_role("button", name="Sign in").click()
            mp.wait_for_url(f"{APP}/app", timeout=15000)
            mp.goto(f"{APP}/account")
            mp.get_by_role("heading", name="Account", exact=True).wait_for()
            mp.wait_for_timeout(800)
            shots.page(mp, "account-mobile", full=True)
            browser.close()
        log("done")
    finally:
        api.terminate()
        web.terminate()
        smtp.stop()


if __name__ == "__main__":
    main()
