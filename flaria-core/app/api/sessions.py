"""/api/v1 clinical data endpoints with role-scoped access (admin endpoints live in admin.py).

PATIENT    own sessions, telemetry and baseline only
CLINICIAN  patients assigned through care_team
ADMIN      everything, plus role and care-team management
"""
from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.config import get_settings
from app.core.security import CurrentUser, Role, get_current_active_user, require_roles
from app.database import repositories as repo
from app.database.session import get_db
from app.schemas import (
    BaselineSummary, CareNoteIn, CareNoteOut, PatientOut, RedFlagsUpdate, ReportOut, SessionOut, TelemetryPointOut,
)
from app.services.findings import RED_FLAGS, RULES_VERSION, assess

router = APIRouter(prefix="/api/v1", tags=["clinical"])


async def authorize_patient_access(conn: asyncpg.Connection, user: CurrentUser, patient_id: uuid.UUID) -> None:
    if user.role is Role.ADMIN:
        return
    if user.role is Role.PATIENT and user.id == patient_id:
        return
    if user.role is Role.CLINICIAN and await repo.is_clinician_of(conn, user.id, patient_id):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this patient")


@router.get("/patients", response_model=list[PatientOut])
async def list_patients(
    user: CurrentUser = Depends(require_roles(Role.CLINICIAN, Role.ADMIN)),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[PatientOut]:
    rows = await repo.list_patients(conn, None if user.role is Role.ADMIN else user.id)
    return [PatientOut(id=r["id"], email=r["email"], full_name=r["full_name"]) for r in rows]


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    patient_id: uuid.UUID | None = Query(None),
    limit: int = Query(25, ge=1, le=200),
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[SessionOut]:
    target = patient_id or user.id
    await authorize_patient_access(conn, user, target)
    rows = await repo.list_sessions(conn, target, limit)
    return [SessionOut(**dict(r)) for r in rows]


@router.get("/sessions/{session_id}/telemetry", response_model=list[TelemetryPointOut])
async def session_telemetry(
    session_id: uuid.UUID,
    since: datetime | None = Query(None),
    limit: int = Query(3600, ge=1, le=20000),
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[TelemetryPointOut]:
    session = await repo.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    await authorize_patient_access(conn, user, session["patient_id"])
    rows = await repo.fetch_telemetry(conn, session_id, since=since, limit=limit)
    return [
        TelemetryPointOut(
            ts=r["ts"],
            audio_vec=list(r["audio_vec"]) if r["audio_vec"] is not None else None,
            vision_vec=list(r["vision_vec"]) if r["vision_vec"] is not None else None,
            f0_hz=r["f0_hz"], jitter_local=r["jitter_local"], shimmer_local=r["shimmer_local"], hnr_db=r["hnr_db"],
            anomaly=r["anomaly"], anomaly_peak=r["anomaly_peak"], risk_status=r["risk_status"],
            face_coverage=r["face_coverage"], voiced_ratio=r["voiced_ratio"],
        )
        for r in rows
    ]


@router.get("/baseline", response_model=BaselineSummary)
async def baseline_summary(
    patient_id: uuid.UUID | None = Query(None),
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> BaselineSummary:
    target = patient_id or user.id
    await authorize_patient_access(conn, user, target)
    s = get_settings()
    audio = await repo.load_baseline_days(conn, target, "audio")
    vision = await repo.load_baseline_days(conn, target, "vision")
    a_n, v_n = sum(r.n for r in audio), sum(r.n for r in vision)
    days = len({r.day for r in audio} | {r.day for r in vision})
    ready = (a_n >= s.min_baseline_seconds / s.audio_hop_seconds
             and v_n >= s.min_baseline_seconds * s.vision_nominal_fps)
    return BaselineSummary(patient_id=target, audio_samples=a_n, vision_samples=v_n, days_covered=days, ready=ready)


# ------------------------------------------------------------------ session results
def _report_out(row: asyncpg.Record) -> ReportOut:
    a = row["assessment"]
    return ReportOut(
        session_id=row["session_id"], patient_id=row["patient_id"], created_at=row["created_at"],
        updated_at=row["updated_at"], triage=row["triage"], headline=a["headline"], advice=a["advice"],
        findings=a["findings"], red_flags=list(row["red_flags"]), red_flag_options=RED_FLAGS,
        summary=row["summary"], rules_version=row["rules_version"], demo=bool(a.get("demo", False)),
    )


@router.get("/sessions/{session_id}/report", response_model=ReportOut)
async def session_report(
    session_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> ReportOut:
    row = await repo.get_report(conn, session_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No result for this session yet")
    await authorize_patient_access(conn, user, row["patient_id"])
    return _report_out(row)


@router.get("/reports/latest", response_model=ReportOut)
async def latest_report(
    patient_id: uuid.UUID | None = Query(None),
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> ReportOut:
    target = patient_id or user.id
    await authorize_patient_access(conn, user, target)
    row = await repo.latest_report(conn, target)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No results yet")
    return _report_out(row)


@router.put("/sessions/{session_id}/report/red-flags", response_model=ReportOut)
async def update_red_flags(
    session_id: uuid.UUID,
    body: RedFlagsUpdate,
    request: Request,
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> ReportOut:
    unknown = set(body.red_flags) - RED_FLAGS.keys()
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown warning signs: {sorted(unknown)}")
    async with conn.transaction():
        row = await repo.get_report(conn, session_id, for_update=True)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No result for this session yet")
        await authorize_patient_access(conn, user, row["patient_id"])
        # Keep a demo result a demo when symptoms are updated later.
        demo = get_settings().demo_mode or bool((row["assessment"] or {}).get("demo"))
        result = assess(row["summary"], body.red_flags, demo=demo)
        updated = await repo.update_report_assessment(
            conn, session_id, triage=result.triage, assessment=result.to_dict(),
            red_flags=result.red_flags, rules_version=RULES_VERSION,
        )
        await repo.audit(conn, "report.red_flags", actor_id=user.id, target_id=session_id,
                         ip=request.client.host if request.client else None,
                         detail={"red_flags": result.red_flags, "triage": result.triage})
    return _report_out(updated)


# ------------------------------------------------------------------ care team notes
@router.get("/patients/{patient_id}/care-notes", response_model=list[CareNoteOut])
async def care_notes(
    patient_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[CareNoteOut]:
    await authorize_patient_access(conn, user, patient_id)
    rows = await repo.list_care_notes(conn, patient_id)
    return [CareNoteOut(id=r["id"], patient_id=r["patient_id"], body=r["body"], created_at=r["created_at"],
                        author_name=r["author_name"], author_role=r["author_role"]) for r in rows]


@router.post("/patients/{patient_id}/care-notes", response_model=CareNoteOut, status_code=status.HTTP_201_CREATED)
async def add_care_note(
    patient_id: uuid.UUID,
    body: CareNoteIn,
    user: CurrentUser = Depends(require_roles(Role.CLINICIAN, Role.ADMIN)),
    conn: asyncpg.Connection = Depends(get_db),
) -> CareNoteOut:
    if patient_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Care notes are written for patients, not yourself")
    await authorize_patient_access(conn, user, patient_id)
    async with conn.transaction():
        note_id = await repo.insert_care_note(conn, patient_id, user.id, body.body)
        await repo.audit(conn, "care_note.create", actor_id=user.id, target_id=patient_id, detail={"note_id": note_id})
    rows = await repo.list_care_notes(conn, patient_id, limit=1)
    r = rows[0]
    return CareNoteOut(id=r["id"], patient_id=r["patient_id"], body=r["body"], created_at=r["created_at"],
                       author_name=r["author_name"], author_role=r["author_role"])
