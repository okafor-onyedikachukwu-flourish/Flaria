#!/usr/bin/env python3
"""End-to-end test of password change and password reset, including real SMTP delivery.

Starts a capturing SMTP server (aiosmtpd) on --smtp-port, so run the API with:
    FLARIA_MAIL_BACKEND=smtp FLARIA_SMTP_HOST=127.0.0.1 FLARIA_SMTP_PORT=2525 FLARIA_SMTP_STARTTLS=false
    FLARIA_COOKIE_SECURE=false
then:
    python scripts/smoke_passwords.py --base http://127.0.0.1:8000 --smtp-port 2525
"""
from __future__ import annotations

import argparse
import email
import re
import sys
import time
import uuid
from email.message import Message

import httpx
from aiosmtpd.controller import Controller

INBOX: list[Message] = []


class Capture:
    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001, N802 - aiosmtpd hook
        INBOX.append(email.message_from_bytes(envelope.content))
        return "250 OK"


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def wait_mail(count: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while len(INBOX) < count and time.time() < deadline:
        time.sleep(0.1)


def text_of(msg: Message) -> str:
    from email import policy

    parsed = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
    return parsed.get_body(preferencelist=("plain",)).get_content()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--smtp-port", type=int, default=2525)
    args = ap.parse_args()
    controller = Controller(Capture(), hostname="127.0.0.1", port=args.smtp_port)
    controller.start()
    try:
        run(args.base)
    finally:
        controller.stop()


def run(base: str) -> None:
    from email import policy

    email_addr = f"pw-{uuid.uuid4().hex[:8]}@example.org"
    p1, p2, p3 = "First-Password-123", "Second-Password-456", "Third-Password-789"
    c = httpx.Client(base_url=base, timeout=20)

    r = c.post("/api/v1/auth/register", json={"email": email_addr, "password": p1, "full_name": "Password Tester"})
    check(r.status_code == 201, "register")
    token = r.json()["access_token"]
    old_cookie = c.cookies.get("flaria_rt")
    h = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ change password
    r = c.post("/api/v1/auth/change-password", headers=h, json={"current_password": "Wrong-Password-000", "new_password": p2})
    check(r.status_code == 400 and "incorrect" in r.text, "wrong current password rejected with 400 (not 401)")
    r = c.post("/api/v1/auth/change-password", headers=h, json={"current_password": p1, "new_password": "Qz7!"})
    check(r.status_code == 422 and "Qz7!" not in r.text and p1 not in r.text, "weak new password rejected without echoing input")
    r = c.post("/api/v1/auth/change-password", headers=h, json={"current_password": p1, "new_password": p1})
    check(r.status_code == 400, "reusing the current password rejected")
    r = c.post("/api/v1/auth/change-password", headers=h, json={"current_password": p1, "new_password": p2})
    check(r.status_code == 200 and r.json()["access_token"] != token, "password changed; fresh session issued")
    new_token = r.json()["access_token"]
    check(c.get("/api/v1/auth/me", headers=h).status_code == 401, "old access token revoked immediately")
    check(c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200, "new access token works")
    with httpx.Client(base_url=base, timeout=20, cookies={"flaria_rt": old_cookie}) as other_device:
        check(other_device.post("/api/v1/auth/refresh").status_code == 401, "other devices signed out (old refresh token dead)")
    check(c.post("/api/v1/auth/login", json={"email": email_addr, "password": p1}).status_code == 401, "old password no longer works")
    check(c.post("/api/v1/auth/login", json={"email": email_addr, "password": p2}).status_code == 200, "new password works")
    acct = c.get("/api/v1/auth/account", headers={"Authorization": f"Bearer {c.post('/api/v1/auth/refresh').json()['access_token']}"}).json()
    check(acct["password_changed_at"] is not None, "account shows when the password changed")
    wait_mail(1)
    check(len(INBOX) == 1 and INBOX[0]["Subject"] == "Your Flaria password was changed" and INBOX[0]["To"] == email_addr,
          "security notification e-mailed after change")

    # ------------------------------------------------------------------ forgot password
    INBOX.clear()
    r1 = c.post("/api/v1/auth/password-reset/request", json={"email": email_addr})
    r2 = c.post("/api/v1/auth/password-reset/request", json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.org"})
    check(r1.status_code == r2.status_code == 202 and r1.json() == r2.json(), "same answer for known and unknown e-mails")
    wait_mail(1)
    check(len(INBOX) == 1, "exactly one reset e-mail delivered (none for the unknown address)")
    msg = email.message_from_bytes(INBOX[0].as_bytes(), policy=policy.default)
    body = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    m = re.search(r"/reset-password#token=([A-Za-z0-9_-]+)", body)
    check(m is not None and "#token=" in html, "reset link (token in URL fragment) present in text and HTML parts")
    reset_token = m.group(1)
    check(p2 not in body and p2 not in html, "e-mail contains no password")

    r = c.post("/api/v1/auth/password-reset/confirm", json={"token": reset_token, "new_password": "weak"})
    check(r.status_code == 422, "weak password rejected on reset")
    r = c.post("/api/v1/auth/password-reset/confirm", json={"token": reset_token, "new_password": p3})
    check(r.status_code == 204, "reset with valid link")
    r = c.post("/api/v1/auth/password-reset/confirm", json={"token": reset_token, "new_password": "Fourth-Password-000"})
    check(r.status_code == 400, "reset link works only once")
    r = c.post("/api/v1/auth/password-reset/confirm", json={"token": "x" * 43, "new_password": "Fourth-Password-000"})
    check(r.status_code == 400, "made-up token rejected")
    check(c.post("/api/v1/auth/login", json={"email": email_addr, "password": p2}).status_code == 401, "previous password dead after reset")
    check(c.post("/api/v1/auth/login", json={"email": email_addr, "password": p3}).status_code == 200, "signed in with reset password")
    wait_mail(2)
    check(any(m["Subject"] == "Your Flaria password was changed" for m in INBOX[1:]), "notification e-mailed after reset")

    # ------------------------------------------------------------------ superseding and throttling
    INBOX.clear()
    for _ in range(4):
        c.post("/api/v1/auth/password-reset/request", json={"email": email_addr})
    wait_mail(2, timeout=4)
    time.sleep(1.5)
    check(len(INBOX) == 2, f"hourly limit enforced ({len(INBOX)} more links sent, 3 per hour in total)")
    tokens = [re.search(r"#token=([A-Za-z0-9_-]+)", text_of(m)).group(1) for m in INBOX]
    r = c.post("/api/v1/auth/password-reset/confirm", json={"token": tokens[0], "new_password": "Fifth-Password-111"})
    check(r.status_code == 400, "a newer link supersedes older unused links")
    r = c.post("/api/v1/auth/password-reset/confirm", json={"token": tokens[-1], "new_password": "Fifth-Password-111"})
    check(r.status_code == 204, "latest link still works")
    print("ALL PASSWORD CHECKS PASSED")


if __name__ == "__main__":
    main()
