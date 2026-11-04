"""asyncpg connection pool lifecycle and FastAPI dependency."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import AsyncIterator

import asyncpg

logger = logging.getLogger("flaria.db")


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.execute("SET TIME ZONE 'UTC'")


def clean_dsn(dsn: str) -> str:
    """Drop libpq-only parameters asyncpg would forward to the server as settings.

    Hosted Postgres (e.g. Neon) copy-paste strings include ``channel_binding=require``;
    asyncpg negotiates SCRAM itself and would otherwise fail with
    "unrecognized configuration parameter".
    """
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"channel_binding", "pgbouncer"}]
    return urlunsplit(parts._replace(query=urlencode(query)))


def uses_transaction_pooler(dsn: str) -> bool:
    """Neon's pooled endpoints (host contains ``-pooler``) and explicit ``pgbouncer=true`` URLs."""
    parts = urlsplit(dsn)
    params = dict(parse_qsl(parts.query))
    return "-pooler" in (parts.hostname or "") or params.get("pgbouncer", "").lower() == "true"


class Database:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool is not initialised")
        return self._pool

    @property
    def connected(self) -> bool:
        return self._pool is not None

    async def connect(self, dsn: str, min_size: int, max_size: int) -> None:
        if self._pool is not None:
            return
        pooled = uses_transaction_pooler(dsn)
        self._pool = await asyncpg.create_pool(
            clean_dsn(dsn),
            min_size=min_size,
            max_size=max_size,
            command_timeout=15,
            max_inactive_connection_lifetime=300,
            # PgBouncer in transaction mode can't keep prepared statements across transactions.
            statement_cache_size=0 if pooled else 100,
            init=_init_connection,
        )
        logger.info("database pool ready", extra={"fields": {"min": min_size, "max": max_size, "pooled": pooled}})

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def apply_schema(self, schema_path: Path) -> None:
        sql = schema_path.read_text(encoding="utf-8")
        async with self.pool.acquire() as conn:
            # One transaction with a transaction-scoped lock: it serialises concurrent starts
            # across replicas and is released at COMMIT/ROLLBACK, so it also works through
            # transaction-mode poolers (Neon's -pooler endpoints, PgBouncer), where a
            # session lock could land on one server connection and its unlock on another.
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(7212026)")
                await conn.execute(sql)
        logger.info("schema applied")

    async def ping(self) -> bool:
        try:
            async with self.pool.acquire(timeout=2) as conn:
                return await conn.fetchval("SELECT 1") == 1
        except Exception:  # noqa: BLE001 - readiness probe must never raise
            return False


db = Database()


async def get_db() -> AsyncIterator[asyncpg.Connection]:
    async with db.pool.acquire() as conn:
        yield conn
