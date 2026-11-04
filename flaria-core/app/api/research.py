"""/api/v1/research: informed consent, clinician labels and de-identified export.

Who can do what:
  participant (PATIENT)   read the study information, consent, withdraw, delete own data
  CLINICIAN               label patients on their care team
  ADMIN                   label anyone, list participants, export
Every action is audited. Live sessions are refused without a current consent
(enforced in the WebSocket handler, not just in the web app).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import CurrentUser, Role, get_current_active_user, require_roles
from app.database import repositories as repo
from app.database.session import get_db
from app.services.research import LABELS, StudyInfo, build_export, study_info

router = APIRouter(prefix="/api/v1/research", tags=["research"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_research_mode() -> StudyInfo:
    s = get_settings()
    if not s.research_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Research mode is off")
    return study_info(s)


# ----------------------------------------------------------------------------- queries
async def active_consent(conn: asyncpg.Connection, user_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT consent_version, consented_at FROM research_consents WHERE user_id = $1 AND withdrawn_at IS NULL",
        user_id,
    )


async def has_current_consent(conn: asyncpg.Connection, user_id: uuid.UUID) -> bool:
    row = await active_consent(conn, user_id)
    return row is not None and row["consent_version"] == study_info(get_settings()).version


async def _study_code(conn: asyncpg.Connection, user_id: uuid.UUID) -> str:
    code = await conn.fetchval("SELECT study_code FROM research_participants WHERE user_id = $1", user_id)
    if code:
        return code
    return await conn.fetchval(
        """
        INSERT INTO research_participants (user_id, study_code)
        VALUES ($1, 'FLR-' || lpad(nextval('research_study_code_seq')::text, 4, '0'))
        ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING study_code
        """,
        user_id,
    )


async def _latest_label(conn: asyncpg.Connection, user_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT l.label, l.severity, l.detail, l.recorded_at, u.full_name AS recorded_by_name
          FROM research_labels l LEFT JOIN users u ON u.id = l.recorded_by
         WHERE l.user_id = $1 ORDER BY l.recorded_at DESC, l.id DESC LIMIT 1
        """,
        user_id,
    )


# ----------------------------------------------------------------------------- participant
@router.get("/study")
async def get_study() -> dict[str, Any]:
    """Public: the participant information sheet and consent wording."""
    s = get_settings()
    return {"research_mode": s.research_mode, **(study_info(s).as_dict() if s.research_mode else {})}


@router.get("/me")
async def my_status(
    user: CurrentUser = Depends(get_current_active_user), conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, Any]:
    info = _require_research_mode()
    consent = await active_consent(conn, user.id)
    code = await conn.fetchval("SELECT study_code FROM research_participants WHERE user_id = $1", user.id)
    return {
        "eligible": user.role is Role.PATIENT,
        "consented": consent is not None and consent["consent_version"] == info.version,
        "needs_reconsent": consent is not None and consent["consent_version"] != info.version,
        "consented_at": consent["consented_at"] if consent else None,
        "study_code": code,
        "current_version": info.version,
    }


class ConsentBody(BaseModel):
    consent_version: str = Field(min_length=1, max_length=64)
    agreements_accepted: bool


@router.post("/consent", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def give_consent(
    body: ConsentBody, request: Request,
    user: CurrentUser = Depends(get_current_active_user), conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    info = _require_research_mode()
    if user.role is not Role.PATIENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only participant accounts can join the study")
    if not body.agreements_accepted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Both statements must be accepted to take part")
    if body.consent_version != info.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "The study information changed. Please read it again.")
    async with conn.transaction():
        await conn.execute(
            "UPDATE research_consents SET withdrawn_at = now() WHERE user_id = $1 AND withdrawn_at IS NULL", user.id
        )  # an older version is superseded by the new consent
        await conn.execute(
            "INSERT INTO research_consents (user_id, consent_version, ip) VALUES ($1, $2, $3::inet)",
            user.id, info.version, _ip(request),
        )
        code = await _study_code(conn, user.id)
        await repo.audit(conn, "research.consent_given", actor_id=user.id, target_id=user.id, ip=_ip(request),
                         detail={"version": info.version, "study_code": code})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/withdraw", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def withdraw(
    request: Request, user: CurrentUser = Depends(get_current_active_user), conn: asyncpg.Connection = Depends(get_db)
) -> Response:
    _require_research_mode()
    async with conn.transaction():
        result = await conn.execute(
            "UPDATE research_consents SET withdrawn_at = now() WHERE user_id = $1 AND withdrawn_at IS NULL", user.id
        )
        if result.endswith(" 0"):
            raise HTTPException(status.HTTP_409_CONFLICT, "You're not currently taking part")
        await repo.audit(conn, "research.consent_withdrawn", actor_id=user.id, target_id=user.id, ip=_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me/data", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_my_data(
    request: Request, user: CurrentUser = Depends(get_current_active_user), conn: asyncpg.Connection = Depends(get_db)
) -> Response:
    """Erase the participant's sessions (with their telemetry and results), baselines and labels."""
    _require_research_mode()
    async with conn.transaction():
        if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM diagnostic_sessions WHERE patient_id = $1 AND status = 'ACTIVE')", user.id
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "Stop your live session first")
        sessions = await conn.execute("DELETE FROM diagnostic_sessions WHERE patient_id = $1", user.id)
        await conn.execute("DELETE FROM baseline_daily WHERE patient_id = $1", user.id)
        await conn.execute("DELETE FROM research_labels WHERE user_id = $1", user.id)
        await conn.execute(
            "UPDATE research_consents SET withdrawn_at = now() WHERE user_id = $1 AND withdrawn_at IS NULL", user.id
        )
        await repo.audit(conn, "research.data_deleted", actor_id=user.id, target_id=user.id, ip=_ip(request),
                         detail={"sessions_deleted": int(sessions.split()[-1])})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- labels
class LabelBody(BaseModel):
    label: Literal["healthy_control", "stroke", "als", "parkinsons", "bells_palsy", "respiratory", "other"]
    severity: Literal["mild", "moderate", "severe"] | None = None
    detail: str | None = Field(None, max_length=500)


async def _authorize_labeller(conn: asyncpg.Connection, user: CurrentUser, patient_id: uuid.UUID) -> None:
    if user.role is Role.ADMIN:
        return
    if user.role is Role.CLINICIAN and await repo.is_clinician_of(conn, user.id, patient_id):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the participant's clinicians or an admin can record this")


@router.get("/participants/{patient_id}/label")
async def get_label(
    patient_id: uuid.UUID,
    user: CurrentUser = Depends(require_roles(Role.CLINICIAN, Role.ADMIN)),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    _require_research_mode()
    await _authorize_labeller(conn, user, patient_id)
    row = await _latest_label(conn, patient_id)
    consent = await active_consent(conn, patient_id)
    return {
        "label": dict(row) if row else None,
        "consented": consent is not None and consent["consent_version"] == study_info(get_settings()).version,
        "options": [{"id": k, "label": v} for k, v in LABELS.items()],
    }


@router.put("/participants/{patient_id}/label", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def set_label(
    patient_id: uuid.UUID, body: LabelBody, request: Request,
    user: CurrentUser = Depends(require_roles(Role.CLINICIAN, Role.ADMIN)),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    _require_research_mode()
    await _authorize_labeller(conn, user, patient_id)
    target = await repo.get_user_by_id(conn, patient_id)
    if target is None or target["role"] != Role.PATIENT.value:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participant not found")
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO research_labels (user_id, label, severity, detail, recorded_by) VALUES ($1, $2, $3, $4, $5)",
            patient_id, body.label, body.severity, (body.detail or "").strip() or None, user.id,
        )
        await repo.audit(conn, "research.label_set", actor_id=user.id, target_id=patient_id, ip=_ip(request),
                         detail={"label": body.label, "severity": body.severity})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- admin
@router.get("/participants")
async def list_participants(
    _: CurrentUser = Depends(require_roles(Role.ADMIN)), conn: asyncpg.Connection = Depends(get_db)
) -> dict[str, Any]:
    info = _require_research_mode()
    rows = await conn.fetch(
        """
        SELECT u.id, u.full_name, u.email::text AS email, p.study_code,
               c.consent_version, c.consented_at,
               (SELECT max(withdrawn_at) FROM research_consents w WHERE w.user_id = u.id) AS last_withdrawn_at,
               l.label, l.severity, l.recorded_at AS label_recorded_at,
               (SELECT count(*) FROM diagnostic_sessions s WHERE s.patient_id = u.id) AS sessions
          FROM users u
          LEFT JOIN research_participants p ON p.user_id = u.id
          LEFT JOIN research_consents c ON c.user_id = u.id AND c.withdrawn_at IS NULL
          LEFT JOIN LATERAL (SELECT label, severity, recorded_at FROM research_labels
                              WHERE user_id = u.id ORDER BY recorded_at DESC, id DESC LIMIT 1) l ON true
         WHERE u.role = 'PATIENT'
         ORDER BY p.study_code NULLS LAST, u.full_name
        """
    )
    items = []
    for r in rows:
        if r["consent_version"] == info.version:
            state = "consented"
        elif r["consent_version"]:
            state = "needs_reconsent"
        elif r["last_withdrawn_at"]:
            state = "withdrawn"
        else:
            state = "not_joined"
        items.append({**{k: r[k] for k in ("id", "full_name", "email", "study_code", "consented_at", "label",
                                           "severity", "label_recorded_at", "sessions")}, "state": state})
    return {"study": info.as_dict(), "participants": items}


@router.get("/export")
async def export(
    request: Request,
    admin: CurrentUser = Depends(require_roles(Role.ADMIN)),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    info = _require_research_mode()
    participants = [dict(r) for r in await conn.fetch(
        """
        SELECT p.user_id, p.study_code, c.consent_version, l.label, l.severity
          FROM research_participants p
          JOIN research_consents c ON c.user_id = p.user_id AND c.withdrawn_at IS NULL AND c.consent_version = $1
          LEFT JOIN LATERAL (SELECT label, severity FROM research_labels
                              WHERE user_id = p.user_id ORDER BY recorded_at DESC, id DESC LIMIT 1) l ON true
        """,
        info.version,
    )]
    ids = [p["user_id"] for p in participants]
    code_of = {p["user_id"]: p["study_code"] for p in participants}
    sessions = [
        {**dict(r), "study_code": code_of[r["patient_id"]]}
        for r in await conn.fetch(
            """
            SELECT s.id AS session_id, s.patient_id, s.started_at, s.ended_at, s.status::text AS status, s.tick_count,
                   s.mean_anomaly, s.peak_anomaly, s.model_version, r.triage, r.assessment
              FROM diagnostic_sessions s LEFT JOIN session_reports r ON r.session_id = s.id
             WHERE s.patient_id = ANY($1::uuid[]) AND s.status <> 'ACTIVE'
            """,
            ids,
        )
    ]
    samples = await conn.fetch(
        """
        SELECT session_id, ts, audio_vec, vision_vec, f0_hz, jitter_local, shimmer_local, hnr_db,
               anomaly, anomaly_peak, risk_status, face_coverage, voiced_ratio
          FROM telemetry_samples WHERE session_id = ANY($1::uuid[]) ORDER BY session_id, ts
        """,
        [s["session_id"] for s in sessions],
    )
    for p in participants:
        p.pop("user_id")
    data, counts = await asyncio.to_thread(build_export, info=info, participants=participants, sessions=sessions,
                                           samples=[dict(r) for r in samples])
    await repo.audit(conn, "research.export", actor_id=admin.id, ip=_ip(request), detail=counts)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="flaria-research-export-{stamp}.zip"',
                 "Cache-Control": "no-store"},
    )
