#!/usr/bin/env python3
"""Bootstrap or promote an ADMIN account (public registration only creates PATIENTs).

    FLARIA_DATABASE_URL=postgresql://... python scripts/create_admin.py admin@example.org "Ada Admin"

The password is read interactively, or from FLARIA_ADMIN_PASSWORD for automated
provisioning (never from argv, which leaks into shell history and process lists).
"""
from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database import repositories as repo  # noqa: E402
from app.database.session import db  # noqa: E402
from app.schemas import RegisterRequest  # noqa: E402


async def main(email: str, full_name: str) -> None:
    s = get_settings()
    await db.connect(s.database_url, 1, 2)
    await db.apply_schema(s.schema_path)
    try:
        async with db.pool.acquire() as conn:
            existing = await repo.get_user_by_email(conn, email.lower())
            if existing is not None:
                await repo.set_user_role(conn, existing["id"], "ADMIN")
                await repo.audit(conn, "cli.promote_admin", target_id=existing["id"])
                print(f"Promoted {email} to ADMIN")
                return
            password = os.environ.pop("FLARIA_ADMIN_PASSWORD", None)
            if password is None:
                password = getpass.getpass("Password (min 12 chars, 3 character classes): ")
                if password != getpass.getpass("Repeat password: "):
                    raise SystemExit("Passwords do not match")
            RegisterRequest(email=email, password=password, full_name=full_name)  # same policy as the API
            async with conn.transaction():
                row = await repo.create_user(conn, email=email.lower(), password_hash=hash_password(password),
                                             full_name=full_name, role="ADMIN")
                await repo.audit(conn, "cli.create_admin", target_id=row["id"])
            print(f"Created ADMIN {email} ({row['id']})")
    finally:
        await db.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
