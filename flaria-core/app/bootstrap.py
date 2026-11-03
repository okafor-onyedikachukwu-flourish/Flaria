"""First-start admin provisioning for hosts without a shell (e.g. Render's free tier).

With FLARIA_BOOTSTRAP_ADMIN_EMAIL set and no active admin in the database:
  * the account is created as ADMIN, or an existing account with that e-mail is
    promoted, and in both cases its password is replaced with an unknown random one
    and all its sessions are revoked;
  * a single-use link to choose a password is e-mailed to that address.
Only the owner of the inbox can therefore sign in, even if someone else registered
the address first. Once any admin exists this does nothing, so it never re-sends.
"""
from __future__ import annotations

import logging
import secrets

from app.api.auth import hash_reset_token
from app.core.config import Settings
from app.core.security import hash_password
from app.database import repositories as repo
from app.database.session import Database
from app.services.mailer import Mailer, admin_welcome_mail

logger = logging.getLogger("flaria.bootstrap")


async def bootstrap_admin(db: Database, settings: Settings, mailer: Mailer) -> None:
    email = (settings.bootstrap_admin_email or "").strip().lower()
    if not email:
        return
    async with db.pool.acquire() as conn:
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users WHERE role = 'ADMIN' AND is_active)"):
            return
        unusable = hash_password(secrets.token_urlsafe(48))
        token = secrets.token_urlsafe(32)
        async with conn.transaction():
            existing = await repo.get_user_by_email(conn, email, for_update=True)
            if existing is None:
                row = await repo.create_user(conn, email=email, password_hash=unusable,
                                             full_name=settings.bootstrap_admin_name, role="ADMIN")
                user_id, name = row["id"], row["full_name"]
            else:
                user_id, name = existing["id"], existing["full_name"]
                await repo.set_user_role(conn, user_id, "ADMIN")
                await repo.update_password(conn, user_id, unusable)
                await conn.execute("UPDATE users SET is_active = true WHERE id = $1", user_id)
                await repo.revoke_all_for_user(conn, user_id, "admin_bootstrap")
            await repo.create_reset_token(conn, user_id=user_id, token_hash=hash_reset_token(token),
                                          ttl_minutes=settings.password_reset_ttl_minutes, requested_by=None, ip=None)
            await repo.audit(conn, "cli.create_admin", target_id=user_id, detail={"via": "bootstrap"})
    url = f"{settings.public_app_url.rstrip('/')}/reset-password#token={token}"
    sent = await mailer.send(admin_welcome_mail(email, name, url, settings.password_reset_ttl_minutes))
    logger.warning("bootstrap admin provisioned", extra={"fields": {"email_sent": sent}})
