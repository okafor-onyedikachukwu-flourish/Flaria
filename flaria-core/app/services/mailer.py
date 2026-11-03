"""Transactional e-mail: password reset links and security notifications.

Backends
  smtp     production. STARTTLS (587) or implicit TLS (465) with certificate
           verification, optional authentication. Runs in a worker thread so
           the event loop never blocks on a slow mail server.
  brevo    Brevo's HTTPS transactional API. For hosts that block SMTP ports
           (Render's free tier) and for sending without owning a domain:
           Brevo verifies a single sender address by e-mail link.
  console  development only (refused in production by Settings): logs the
           message, including the reset link, so flows can be tested without
           a mail server.

Messages never contain health information: a password e-mail must be safe to
land in a shared or work inbox.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.core.config import Settings

logger = logging.getLogger("flaria.mail")


@dataclass(frozen=True, slots=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str


def _layout(title: str, paragraphs: list[str], button: tuple[str, str] | None = None) -> str:
    body = "".join(f'<p style="margin:0 0 16px;line-height:1.55">{p}</p>' for p in paragraphs)
    cta = ""
    if button:
        label, url = button
        cta = (
            f'<p style="margin:24px 0"><a href="{html.escape(url, quote=True)}" '
            'style="background:#1D6B73;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;'
            f'display:inline-block;font-weight:600">{html.escape(label)}</a></p>'
            '<p style="margin:0 0 16px;line-height:1.55;color:#4A5A66;font-size:13px">If the button doesn’t work, '
            f'copy this link into your browser:<br><span style="word-break:break-all">{html.escape(url)}</span></p>'
        )
    return (
        '<!doctype html><html><body style="margin:0;background:#EDF1F2;font-family:-apple-system,Segoe UI,'
        'Helvetica,Arial,sans-serif;color:#142028">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px">'
        '<tr><td align="center"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:520px;background:#ffffff;border:1px solid #C9D4D8;border-radius:12px;padding:32px">'
        '<tr><td><p style="margin:0 0 24px;font-weight:700;font-size:18px;color:#14525A">Flaria</p>'
        f'<h1 style="margin:0 0 16px;font-size:22px">{html.escape(title)}</h1>{body}{cta}'
        '<p style="margin:24px 0 0;color:#71818C;font-size:12px;line-height:1.5">Flaria will never ask for your '
        'password by e-mail or phone.</p></td></tr></table></td></tr></table></body></html>'
    )


def reset_mail(to: str, name: str, url: str, ttl_minutes: int, *, by_admin: bool) -> Mail:
    intro = ("An administrator started a password reset for your Flaria account."
             if by_admin else "Someone asked to reset the password for your Flaria account.")
    text = (
        f"Hello {name},\n\n{intro}\n\nChoose a new password here (the link works once and expires in "
        f"{ttl_minutes} minutes):\n{url}\n\nIf you didn’t ask for this, you can ignore this e-mail. "
        "Your password won’t change.\n\nFlaria will never ask for your password by e-mail or phone.\n"
    )
    page = _layout(
        "Reset your password",
        [f"Hello {html.escape(name)},", html.escape(intro),
         f"The link works once and expires in {ttl_minutes} minutes. If you didn’t ask for this, ignore this "
         "e-mail and your password won’t change."],
        ("Choose a new password", url),
    )
    return Mail(to, "Reset your Flaria password", text, page)


def password_changed_mail(to: str, name: str, app_url: str, *, via_reset: bool) -> Mail:
    how = "using a reset link" if via_reset else "from your account settings"
    text = (
        f"Hello {name},\n\nThe password for your Flaria account was just changed {how}, and every other "
        "device was signed out.\n\nIf this was you, there’s nothing else to do.\nIf it wasn’t, reset your "
        f"password now at {app_url}/forgot-password and contact your clinic.\n"
    )
    page = _layout(
        "Your password was changed",
        [f"Hello {html.escape(name)},",
         f"The password for your Flaria account was just changed {how}, and every other device was signed out.",
         "If this was you, there’s nothing else to do. If it wasn’t, reset your password now and contact your clinic."],
        ("Reset my password", f"{app_url}/forgot-password"),
    )
    return Mail(to, "Your Flaria password was changed", text, page)


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def _message(self, mail: Mail) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.s.mail_from
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self.s.mail_from.rsplit("@", 1)[-1].strip("> ") or None)
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(mail.text)
        msg.add_alternative(mail.html, subtype="html")
        return msg

    def _send_smtp(self, msg: EmailMessage) -> None:
        s = self.s
        context = ssl.create_default_context()
        if s.smtp_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=context)
        else:
            client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
        with client:
            client.ehlo()
            if s.smtp_starttls and not s.smtp_ssl:
                client.starttls(context=context)
                client.ehlo()
            if s.smtp_username:
                client.login(s.smtp_username, s.smtp_password.get_secret_value() if s.smtp_password else "")
            client.send_message(msg)

    def _send_brevo(self, mail: Mail) -> None:
        s = self.s
        name, address = parseaddr(s.mail_from)
        payload = {
            "sender": {"name": name or "Flaria", "email": address},
            "to": [{"email": mail.to}],
            "subject": mail.subject,
            "htmlContent": mail.html,
            "textContent": mail.text,
            "headers": {"Auto-Submitted": "auto-generated"},
        }
        request = urllib.request.Request(
            s.brevo_api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "api-key": s.brevo_api_key.get_secret_value() if s.brevo_api_key else "",
                "accept": "application/json",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=s.smtp_timeout_seconds, context=ssl.create_default_context()) as resp:
                if resp.status >= 300:
                    raise RuntimeError(f"Brevo responded {resp.status}")
        except urllib.error.HTTPError as exc:
            # Brevo explains rejections (unverified sender, daily quota) in the body; it holds no secrets.
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"Brevo responded {exc.code}: {detail}") from None

    async def send(self, mail: Mail) -> bool:
        """Deliver ``mail``. Returns False (and logs, without the body) on failure; never raises."""
        msg = self._message(mail)
        try:
            if self.s.mail_backend == "console":
                logger.warning("e-mail (console backend, development only)",
                               extra={"fields": {"to": mail.to, "subject": mail.subject, "text": mail.text}})
            elif self.s.mail_backend == "brevo":
                await asyncio.to_thread(self._send_brevo, mail)
            else:
                await asyncio.to_thread(self._send_smtp, msg)
            logger.info("e-mail sent", extra={"fields": {"subject": mail.subject}})
            return True
        except Exception as exc:  # noqa: BLE001 - delivery failure must not break the request
            logger.error("e-mail delivery failed", extra={"fields": {
                "subject": mail.subject, "error": type(exc).__name__, "detail": str(exc)[:300]}})
            return False


def admin_welcome_mail(to: str, name: str, url: str, ttl_minutes: int) -> Mail:
    text = (
        f"Hello {name},\n\nYour Flaria admin account is ready. Choose your password here (the link works once and "
        f"expires in {ttl_minutes} minutes):\n{url}\n\nIf the link expires, use “Forgot your password?” on the "
        "sign-in page.\n\nFlaria will never ask for your password by e-mail or phone.\n"
    )
    page = _layout(
        "Your admin account is ready",
        [f"Hello {html.escape(name)},", "Your Flaria admin account is ready. Choose a password to sign in.",
         f"The link works once and expires in {ttl_minutes} minutes. If it expires, use “Forgot your password?” "
         "on the sign-in page."],
        ("Choose my password", url),
    )
    return Mail(to, "Set up your Flaria admin account", text, page)
