"""SQL data-access functions. Every function takes an explicit connection so
callers control transactions."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import asyncpg
import numpy as np

USER_COLUMNS = (
    "id, email::text AS email, full_name, role::text AS role, is_active, token_version, "
    "password_hash, failed_login_count, locked_until, created_at, last_login_at, password_changed_at"
)


class DuplicateEmailError(Exception):
    pass


class ActiveSessionConflict(Exception):
    pass


# ============================================================================ users
async def create_user(
    conn: asyncpg.Connection, *, email: str, password_hash: str, full_name: str, role: str = "PATIENT"
) -> asyncpg.Record:
    try:
        return await conn.fetchrow(
            f"INSERT INTO users (email, password_hash, full_name, role) VALUES ($1, $2, $3, $4::user_role) "
            f"RETURNING {USER_COLUMNS}",
            email,
            password_hash,
            full_name,
            role,
        )
    except asyncpg.UniqueViolationError as exc:
        raise DuplicateEmailError(email) from exc


async def get_user_by_email(conn: asyncpg.Connection, email: str, *, for_update: bool = False) -> asyncpg.Record | None:
    lock = " FOR UPDATE" if for_update else ""
    return await conn.fetchrow(f"SELECT {USER_COLUMNS} FROM users WHERE email = $1{lock}", email)


async def get_user_by_id(conn: asyncpg.Connection, user_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(f"SELECT {USER_COLUMNS} FROM users WHERE id = $1", user_id)


async def record_login_failure(
    conn: asyncpg.Connection, user_id: uuid.UUID, max_failures: int, lockout_seconds: int
) -> None:
    await conn.execute(
        """
        UPDATE users
           SET failed_login_count = failed_login_count + 1,
               locked_until = CASE WHEN failed_login_count + 1 >= $2
                                   THEN now() + make_interval(secs => $3)
                                   ELSE locked_until END
         WHERE id = $1
        """,
        user_id,
        max_failures,
        float(lockout_seconds),
    )


async def record_login_success(conn: asyncpg.Connection, user_id: uuid.UUID, new_hash: str | None) -> None:
    await conn.execute(
        """
        UPDATE users
           SET failed_login_count = 0, locked_until = NULL, last_login_at = now(),
               password_hash = COALESCE($2, password_hash)
         WHERE id = $1
        """,
        user_id,
        new_hash,
    )


async def bump_token_version(conn: asyncpg.Connection, user_id: uuid.UUID) -> int:
    return await conn.fetchval(
        "UPDATE users SET token_version = token_version + 1 WHERE id = $1 RETURNING token_version", user_id
    )


async def set_user_role(conn: asyncpg.Connection, user_id: uuid.UUID, role: str) -> asyncpg.Record | None:
    # Changing a role invalidates outstanding access tokens.
    return await conn.fetchrow(
        f"UPDATE users SET role = $2::user_role, token_version = token_version + 1 WHERE id = $1 "
        f"RETURNING {USER_COLUMNS}",
        user_id,
        role,
    )


async def list_users(conn: asyncpg.Connection, *, role: str | None, limit: int, offset: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"SELECT {USER_COLUMNS} FROM users WHERE ($1::user_role IS NULL OR role = $1::user_role) "
        f"ORDER BY created_at DESC LIMIT $2 OFFSET $3",
        role,
        limit,
        offset,
    )


# ============================================================================ refresh tokens
async def insert_refresh_token(
    conn: asyncpg.Connection,
    *,
    jti: uuid.UUID,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
) -> None:
    await conn.execute(
        "INSERT INTO refresh_tokens (jti, user_id, family_id, expires_at, user_agent, ip) "
        "VALUES ($1, $2, $3, $4, $5, $6::inet)",
        jti,
        user_id,
        family_id,
        expires_at,
        (user_agent or "")[:512] or None,
        ip,
    )


async def lock_refresh_token(conn: asyncpg.Connection, jti: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow("SELECT * FROM refresh_tokens WHERE jti = $1 FOR UPDATE", jti)


async def mark_rotated(conn: asyncpg.Connection, jti: uuid.UUID, replaced_by: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE refresh_tokens SET rotated_at = now(), replaced_by = $2 WHERE jti = $1", jti, replaced_by
    )


async def revoke_family(conn: asyncpg.Connection, family_id: uuid.UUID, reason: str) -> int:
    result = await conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now(), revoke_reason = $2 "
        "WHERE family_id = $1 AND revoked_at IS NULL",
        family_id,
        reason,
    )
    return int(result.split()[-1])


async def revoke_all_for_user(conn: asyncpg.Connection, user_id: uuid.UUID, reason: str) -> int:
    result = await conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now(), revoke_reason = $2 WHERE user_id = $1 AND revoked_at IS NULL",
        user_id,
        reason,
    )
    return int(result.split()[-1])


# ============================================================================ audit
async def audit(
    conn: asyncpg.Connection,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO audit_events (actor_id, action, target_id, ip, detail) VALUES ($1, $2, $3, $4::inet, $5::jsonb)",
        actor_id,
        action,
        target_id,
        ip,
        detail or {},
    )


# ============================================================================ care team
async def is_clinician_of(conn: asyncpg.Connection, clinician_id: uuid.UUID, patient_id: uuid.UUID) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM care_team WHERE clinician_id = $1 AND patient_id = $2)",
            clinician_id,
            patient_id,
        )
    )


async def assign_care_team(
    conn: asyncpg.Connection, clinician_id: uuid.UUID, patient_id: uuid.UUID, created_by: uuid.UUID
) -> None:
    await conn.execute(
        "INSERT INTO care_team (clinician_id, patient_id, created_by) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        clinician_id,
        patient_id,
        created_by,
    )


async def list_patients(conn: asyncpg.Connection, clinician_id: uuid.UUID | None) -> list[asyncpg.Record]:
    if clinician_id is None:
        return await conn.fetch(
            "SELECT id, email::text AS email, full_name FROM users WHERE role = 'PATIENT' AND is_active "
            "ORDER BY full_name LIMIT 500"
        )
    return await conn.fetch(
        "SELECT u.id, u.email::text AS email, u.full_name FROM care_team c JOIN users u ON u.id = c.patient_id "
        "WHERE c.clinician_id = $1 AND u.is_active ORDER BY u.full_name",
        clinician_id,
    )


# ============================================================================ sessions
async def abort_stale_sessions(conn: asyncpg.Connection, stale_seconds: int, patient_id: uuid.UUID | None = None) -> int:
    result = await conn.execute(
        """
        UPDATE diagnostic_sessions
           SET status = 'ABORTED', ended_at = GREATEST(last_heartbeat_at, started_at), end_reason = 'stale_heartbeat'
         WHERE status = 'ACTIVE'
           AND last_heartbeat_at < now() - make_interval(secs => $1)
           AND ($2::uuid IS NULL OR patient_id = $2)
        """,
        float(stale_seconds),
        patient_id,
    )
    return int(result.split()[-1])


async def create_session(
    conn: asyncpg.Connection,
    *,
    patient_id: uuid.UUID,
    started_by: uuid.UUID,
    model_version: str,
    execution_provider: str,
    baseline_samples: int,
    client_info: dict[str, Any],
) -> asyncpg.Record:
    try:
        return await conn.fetchrow(
            """
            INSERT INTO diagnostic_sessions
                (patient_id, started_by, model_version, execution_provider, baseline_samples, client_info)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id, started_at
            """,
            patient_id,
            started_by,
            model_version,
            execution_provider,
            baseline_samples,
            client_info,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ActiveSessionConflict(str(patient_id)) from exc


async def end_session(
    conn: asyncpg.Connection,
    session_id: uuid.UUID,
    *,
    status: str,
    reason: str,
    tick_count: int,
    peak_anomaly: float | None,
    mean_anomaly: float | None,
) -> None:
    await conn.execute(
        """
        UPDATE diagnostic_sessions
           SET status = $2::session_status, end_reason = $3, ended_at = now(), last_heartbeat_at = now(),
               tick_count = $4, peak_anomaly = $5, mean_anomaly = $6
         WHERE id = $1 AND status = 'ACTIVE'
        """,
        session_id,
        status,
        reason[:64],
        tick_count,
        peak_anomaly,
        mean_anomaly,
    )


async def heartbeat_sessions(conn: asyncpg.Connection, session_ids: Sequence[uuid.UUID]) -> None:
    if session_ids:
        await conn.execute(
            "UPDATE diagnostic_sessions SET last_heartbeat_at = now() WHERE id = ANY($1::uuid[]) AND status = 'ACTIVE'",
            list(session_ids),
        )


async def list_sessions(conn: asyncpg.Connection, patient_id: uuid.UUID, limit: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, patient_id, started_by, status::text AS status, started_at, ended_at, end_reason,
               model_version, execution_provider, tick_count, peak_anomaly, mean_anomaly
          FROM diagnostic_sessions WHERE patient_id = $1 ORDER BY started_at DESC LIMIT $2
        """,
        patient_id,
        limit,
    )


async def get_session(conn: asyncpg.Connection, session_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, patient_id, status::text AS status, started_at, ended_at FROM diagnostic_sessions WHERE id = $1",
        session_id,
    )


# ============================================================================ telemetry
TELEMETRY_COLUMNS = (
    "session_id", "patient_id", "ts", "audio_vec", "vision_vec", "f0_hz", "jitter_local", "shimmer_local",
    "hnr_db", "anomaly", "anomaly_peak", "risk_status", "face_coverage", "voiced_ratio", "sample_count",
)


async def copy_telemetry(conn: asyncpg.Connection, records: Iterable[tuple[Any, ...]]) -> None:
    await conn.copy_records_to_table("telemetry_samples", records=list(records), columns=list(TELEMETRY_COLUMNS))


async def fetch_telemetry(
    conn: asyncpg.Connection, session_id: uuid.UUID, *, since: datetime | None, limit: int
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT ts, audio_vec, vision_vec, f0_hz, jitter_local, shimmer_local, hnr_db,
               anomaly, anomaly_peak, risk_status, face_coverage, voiced_ratio
          FROM telemetry_samples
         WHERE session_id = $1 AND ($2::timestamptz IS NULL OR ts > $2)
         ORDER BY ts ASC LIMIT $3
        """,
        session_id,
        since,
        limit,
    )


async def ensure_partitions(conn: asyncpg.Connection, months_ahead: int = 3) -> int:
    return int(await conn.fetchval("SELECT flaria_ensure_telemetry_partitions($1)", months_ahead))


# ============================================================================ baselines
@dataclass(slots=True)
class BaselineRow:
    day: date
    n: int
    mean: np.ndarray
    m2: np.ndarray


async def load_baseline_days(
    conn: asyncpg.Connection, patient_id: uuid.UUID, modality: str, days: int = 7
) -> list[BaselineRow]:
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
    rows = await conn.fetch(
        "SELECT day, n, mean, m2 FROM baseline_daily WHERE patient_id = $1 AND modality = $2 AND day >= $3 "
        "ORDER BY day",
        patient_id,
        modality,
        since,
    )
    return [
        BaselineRow(r["day"], int(r["n"]), np.asarray(r["mean"], dtype=np.float64), np.asarray(r["m2"], dtype=np.float64))
        for r in rows
    ]


async def lock_baseline_day(
    conn: asyncpg.Connection, patient_id: uuid.UUID, modality: str, day: date
) -> BaselineRow | None:
    r = await conn.fetchrow(
        "SELECT day, n, mean, m2 FROM baseline_daily WHERE patient_id = $1 AND modality = $2 AND day = $3 FOR UPDATE",
        patient_id,
        modality,
        day,
    )
    if r is None:
        return None
    return BaselineRow(r["day"], int(r["n"]), np.asarray(r["mean"], np.float64), np.asarray(r["m2"], np.float64))


async def upsert_baseline_day(
    conn: asyncpg.Connection, patient_id: uuid.UUID, modality: str, row: BaselineRow
) -> None:
    await conn.execute(
        """
        INSERT INTO baseline_daily (patient_id, modality, day, n, mean, m2)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (patient_id, modality, day)
        DO UPDATE SET n = EXCLUDED.n, mean = EXCLUDED.mean, m2 = EXCLUDED.m2, updated_at = now()
        """,
        patient_id,
        modality,
        row.day,
        row.n,
        [float(x) for x in row.mean],
        [float(x) for x in row.m2],
    )


# ============================================================================ session reports
REPORT_COLUMNS = "session_id, patient_id, created_at, updated_at, triage, summary, assessment, red_flags, rules_version"


async def insert_report(
    conn: asyncpg.Connection, *, session_id: uuid.UUID, patient_id: uuid.UUID, triage: str,
    summary: dict[str, Any], assessment: dict[str, Any], rules_version: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO session_reports (session_id, patient_id, triage, summary, assessment, rules_version)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
        ON CONFLICT (session_id) DO NOTHING
        """,
        session_id, patient_id, triage, summary, assessment, rules_version,
    )


async def get_report(conn: asyncpg.Connection, session_id: uuid.UUID, *, for_update: bool = False) -> asyncpg.Record | None:
    lock = " FOR UPDATE" if for_update else ""
    return await conn.fetchrow(f"SELECT {REPORT_COLUMNS} FROM session_reports WHERE session_id = $1{lock}", session_id)


async def latest_report(conn: asyncpg.Connection, patient_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        f"SELECT {REPORT_COLUMNS} FROM session_reports WHERE patient_id = $1 ORDER BY created_at DESC LIMIT 1",
        patient_id,
    )


async def update_report_assessment(
    conn: asyncpg.Connection, session_id: uuid.UUID, *, triage: str, assessment: dict[str, Any],
    red_flags: list[str], rules_version: str,
) -> asyncpg.Record:
    return await conn.fetchrow(
        f"""
        UPDATE session_reports
           SET triage = $2, assessment = $3::jsonb, red_flags = $4, rules_version = $5, updated_at = now()
         WHERE session_id = $1
     RETURNING {REPORT_COLUMNS}
        """,
        session_id, triage, assessment, red_flags, rules_version,
    )


# ============================================================================ care notes
async def list_care_notes(conn: asyncpg.Connection, patient_id: uuid.UUID, limit: int = 20) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT n.id, n.patient_id, n.body, n.created_at, n.author_id, u.full_name AS author_name, u.role::text AS author_role
          FROM care_notes n LEFT JOIN users u ON u.id = n.author_id
         WHERE n.patient_id = $1 ORDER BY n.created_at DESC LIMIT $2
        """,
        patient_id, limit,
    )


async def insert_care_note(conn: asyncpg.Connection, patient_id: uuid.UUID, author_id: uuid.UUID, body: str) -> int:
    return int(await conn.fetchval(
        "INSERT INTO care_notes (patient_id, author_id, body) VALUES ($1, $2, $3) RETURNING id",
        patient_id, author_id, body,
    ))


# ============================================================================ passwords
async def update_password(conn: asyncpg.Connection, user_id: uuid.UUID, password_hash: str) -> int:
    """Set a new hash, clear lockout and invalidate every outstanding access token. Returns the new token_version."""
    return await conn.fetchval(
        """
        UPDATE users
           SET password_hash = $2, password_changed_at = now(), failed_login_count = 0, locked_until = NULL,
               token_version = token_version + 1
         WHERE id = $1
     RETURNING token_version
        """,
        user_id,
        password_hash,
    )


async def recent_reset_requests(conn: asyncpg.Connection, user_id: uuid.UUID) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM password_reset_tokens WHERE user_id = $1 AND created_at > now() - interval '1 hour'",
        user_id,
    ))


async def create_reset_token(
    conn: asyncpg.Connection, *, user_id: uuid.UUID, token_hash: str, ttl_minutes: int,
    requested_by: uuid.UUID | None, ip: str | None,
) -> None:
    # A new link supersedes any earlier unused one.
    await conn.execute(
        "UPDATE password_reset_tokens SET used_at = now() WHERE user_id = $1 AND used_at IS NULL", user_id
    )
    await conn.execute(
        """
        INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, requested_by, requested_ip)
        VALUES ($1, $2, now() + make_interval(mins => $3), $4, $5::inet)
        """,
        user_id, token_hash, ttl_minutes, requested_by, ip,
    )


async def consume_reset_token(conn: asyncpg.Connection, token_hash: str) -> asyncpg.Record | None:
    """Atomically mark a valid token used; returns the owning user row or None if invalid, used or expired."""
    return await conn.fetchrow(
        """
        WITH t AS (
            UPDATE password_reset_tokens SET used_at = now()
             WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now()
         RETURNING user_id
        )
        SELECT u.id, u.email::text AS email, u.full_name, u.is_active FROM t JOIN users u ON u.id = t.user_id
        """,
        token_hash,
    )


async def live_device_count(conn: asyncpg.Connection, user_id: uuid.UUID) -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM refresh_tokens WHERE user_id = $1 AND revoked_at IS NULL AND rotated_at IS NULL "
        "AND expires_at > now()",
        user_id,
    ))
