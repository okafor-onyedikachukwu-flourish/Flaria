"""Read models and mutations for the admin console.

Kept apart from repositories.py because these queries span the whole
population (counts, queues, audit) rather than one patient or session.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg

ESCALATION_TRIAGES = ("EMERGENCY", "SEE_DOCTOR")


# ============================================================================ overview
async def user_counts(conn: asyncpg.Connection) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT role::text AS role,
               count(*) FILTER (WHERE is_active) AS active,
               count(*) FILTER (WHERE NOT is_active) AS disabled,
               count(*) FILTER (WHERE locked_until > now()) AS locked,
               count(*) FILTER (WHERE created_at > now() - interval '7 days') AS new_7d
          FROM users GROUP BY role
        """
    )
    by_role = {r["role"]: {"active": r["active"], "disabled": r["disabled"]} for r in rows}
    return {
        "by_role": {role: by_role.get(role, {"active": 0, "disabled": 0}) for role in ("PATIENT", "CLINICIAN", "ADMIN")},
        "locked": sum(r["locked"] for r in rows),
        "new_7d": sum(r["new_7d"] for r in rows),
    }


async def session_counts(conn: asyncpg.Connection) -> dict[str, int]:
    row = await conn.fetchrow(
        """
        SELECT count(*) FILTER (WHERE status = 'ACTIVE') AS active,
               count(*) FILTER (WHERE started_at > now() - interval '24 hours') AS last_24h,
               count(*) FILTER (WHERE started_at > now() - interval '7 days') AS last_7d,
               count(*) FILTER (WHERE status = 'ABORTED' AND started_at > now() - interval '7 days') AS aborted_7d,
               count(DISTINCT patient_id) FILTER (WHERE started_at > now() - interval '7 days') AS patients_7d
          FROM diagnostic_sessions
        """
    )
    return dict(row)


async def triage_counts(conn: asyncpg.Connection, days: int = 7) -> dict[str, int]:
    rows = await conn.fetch(
        "SELECT triage, count(*) AS n FROM session_reports WHERE created_at > now() - make_interval(days => $1) GROUP BY triage",
        days,
    )
    return {r["triage"]: r["n"] for r in rows}


async def daily_results(conn: asyncpg.Connection, days: int = 14) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT d::date AS day, r.triage, count(r.session_id) AS n
          FROM generate_series(current_date - ($1::int - 1), current_date, interval '1 day') AS d
          LEFT JOIN session_reports r ON (r.created_at AT TIME ZONE 'UTC')::date = d::date
         GROUP BY 1, 2 ORDER BY 1
        """,
        days,
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        day = r["day"].isoformat()
        out.setdefault(day, {"day": day, "counts": {}})
        if r["triage"]:
            out[day]["counts"][r["triage"]] = r["n"]
    return list(out.values())


# ============================================================================ users
async def search_users(
    conn: asyncpg.Connection, *, search: str | None, role: str | None, state: str | None, limit: int, offset: int
) -> tuple[list[asyncpg.Record], int]:
    where = ["($1::text IS NULL OR u.email ILIKE '%' || $1 || '%' OR u.full_name ILIKE '%' || $1 || '%')",
             "($2::user_role IS NULL OR u.role = $2::user_role)"]
    if state == "active":
        where.append("u.is_active AND (u.locked_until IS NULL OR u.locked_until <= now())")
    elif state == "disabled":
        where.append("NOT u.is_active")
    elif state == "locked":
        where.append("u.locked_until > now()")
    clause = " AND ".join(where)
    total = await conn.fetchval(f"SELECT count(*) FROM users u WHERE {clause}", search, role)
    rows = await conn.fetch(
        f"""
        SELECT u.id, u.email::text AS email, u.full_name, u.role::text AS role, u.is_active, u.created_at,
               u.last_login_at, u.locked_until, u.failed_login_count,
               (SELECT count(*) FROM diagnostic_sessions s WHERE s.patient_id = u.id) AS session_count,
               (SELECT max(started_at) FROM diagnostic_sessions s WHERE s.patient_id = u.id) AS last_session_at,
               (SELECT count(*) FROM care_team c WHERE c.clinician_id = u.id OR c.patient_id = u.id) AS care_links
          FROM users u WHERE {clause}
         ORDER BY u.created_at DESC LIMIT $3 OFFSET $4
        """,
        search, role, limit, offset,
    )
    return rows, int(total)


async def set_active(conn: asyncpg.Connection, user_id: uuid.UUID, active: bool) -> asyncpg.Record | None:
    # Disabling also invalidates every outstanding access token.
    return await conn.fetchrow(
        "UPDATE users SET is_active = $2, token_version = token_version + CASE WHEN $2 THEN 0 ELSE 1 END "
        "WHERE id = $1 RETURNING id, email::text AS email, full_name, role::text AS role, is_active",
        user_id, active,
    )


async def unlock(conn: asyncpg.Connection, user_id: uuid.UUID) -> bool:
    result = await conn.execute(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = $1", user_id
    )
    return result.endswith(" 1")


# ============================================================================ care team
async def list_care_team(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT c.clinician_id, cu.full_name AS clinician_name, cu.email::text AS clinician_email,
               c.patient_id, pu.full_name AS patient_name, pu.email::text AS patient_email, c.created_at
          FROM care_team c
          JOIN users cu ON cu.id = c.clinician_id
          JOIN users pu ON pu.id = c.patient_id
         ORDER BY cu.full_name, pu.full_name
        """
    )


async def remove_care_team(conn: asyncpg.Connection, clinician_id: uuid.UUID, patient_id: uuid.UUID) -> bool:
    result = await conn.execute(
        "DELETE FROM care_team WHERE clinician_id = $1 AND patient_id = $2", clinician_id, patient_id
    )
    return result.endswith(" 1")


# ============================================================================ escalations
async def escalations(conn: asyncpg.Connection, *, only_open: bool, limit: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"""
        SELECT r.session_id, r.patient_id, p.full_name AS patient_name, p.email::text AS patient_email,
               r.triage, r.red_flags, r.created_at, r.assessment->>'headline' AS headline,
               f.created_at AS followed_up_at, a.full_name AS followed_up_by,
               (SELECT string_agg(cu.full_name, ', ' ORDER BY cu.full_name)
                  FROM care_team c JOIN users cu ON cu.id = c.clinician_id
                 WHERE c.patient_id = r.patient_id) AS clinicians
          FROM session_reports r
          JOIN users p ON p.id = r.patient_id
          LEFT JOIN LATERAL (
                SELECT n.created_at, n.author_id FROM care_notes n
                 WHERE n.patient_id = r.patient_id AND n.created_at >= r.created_at
                 ORDER BY n.created_at LIMIT 1) f ON true
          LEFT JOIN users a ON a.id = f.author_id
         WHERE r.triage = ANY($1::text[]) {"AND f.created_at IS NULL" if only_open else ""}
         ORDER BY (r.triage = 'EMERGENCY') DESC, r.created_at DESC
         LIMIT $2
        """,
        list(ESCALATION_TRIAGES), limit,
    )


async def open_escalation_count(conn: asyncpg.Connection) -> int:
    return int(await conn.fetchval(
        """
        SELECT count(*) FROM session_reports r
         WHERE r.triage = ANY($1::text[])
           AND NOT EXISTS (SELECT 1 FROM care_notes n WHERE n.patient_id = r.patient_id AND n.created_at >= r.created_at)
        """,
        list(ESCALATION_TRIAGES),
    ))


# ============================================================================ audit
async def audit_events(
    conn: asyncpg.Connection, *, category: str | None, search: str | None, before_id: int | None, limit: int
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT e.id, e.ts, e.action, e.ip::text AS ip, e.detail,
               e.actor_id, au.full_name AS actor_name, au.email::text AS actor_email,
               e.target_id, tu.full_name AS target_name, tu.email::text AS target_email
          FROM audit_events e
          LEFT JOIN users au ON au.id = e.actor_id
          LEFT JOIN users tu ON tu.id = e.target_id
         WHERE ($1::text IS NULL OR e.action LIKE $1 || '.%')
           AND ($2::text IS NULL OR au.email ILIKE '%' || $2 || '%' OR tu.email ILIKE '%' || $2 || '%'
                OR au.full_name ILIKE '%' || $2 || '%' OR tu.full_name ILIKE '%' || $2 || '%')
           AND ($3::bigint IS NULL OR e.id < $3)
         ORDER BY e.id DESC LIMIT $4
        """,
        category, search, before_id, limit,
    )


# ============================================================================ system
async def database_stats(conn: asyncpg.Connection) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT pg_database_size(current_database()) AS db_bytes,
               (SELECT count(*) FROM telemetry_samples) AS telemetry_rows,
               (SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhparent
                 WHERE c.relname = 'telemetry_samples') AS telemetry_partitions,
               (SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL AND rotated_at IS NULL
                   AND expires_at > now()) AS live_refresh_sessions,
               (SELECT max(ts) FROM telemetry_samples) AS last_telemetry_at,
               version() AS server_version
        """
    )
    return dict(row)


async def first_event_time(conn: asyncpg.Connection) -> datetime | None:
    return await conn.fetchval("SELECT min(ts) FROM audit_events")
