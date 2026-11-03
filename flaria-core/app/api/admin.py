"""/api/v1/admin: the administration console's API. Every route requires ADMIN.

Every mutation is written to audit_events with the acting admin, and guards
stop an admin from locking themselves out (self-demotion, self-disable).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from app.api.auth import get_mailer, issue_password_reset
from app.core.config import get_settings
from app.core.security import CurrentUser, Role, require_roles
from app.database import admin_repo
from app.database import repositories as repo
from app.database.session import db, get_db
from app.services.mailer import Mailer

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
admin_only = require_roles(Role.ADMIN)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ----------------------------------------------------------------------------- models
class AdminUser(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    locked: bool
    failed_login_count: int
    created_at: datetime
    last_login_at: datetime | None
    session_count: int
    last_session_at: datetime | None
    care_links: int


class UserPage(BaseModel):
    items: list[AdminUser]
    total: int
    limit: int
    offset: int


class RoleBody(BaseModel):
    role: Role


class ActiveBody(BaseModel):
    is_active: bool


class CareLink(BaseModel):
    clinician_id: uuid.UUID
    clinician_name: str
    clinician_email: str
    patient_id: uuid.UUID
    patient_name: str
    patient_email: str
    created_at: datetime


class CareLinkBody(BaseModel):
    clinician_id: uuid.UUID
    patient_id: uuid.UUID


class Escalation(BaseModel):
    session_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_email: str
    triage: Literal["EMERGENCY", "SEE_DOCTOR"]
    headline: str | None
    red_flags: list[str]
    created_at: datetime
    clinicians: str | None
    followed_up_at: datetime | None
    followed_up_by: str | None


class AuditEvent(BaseModel):
    id: int
    ts: datetime
    action: str
    ip: str | None
    detail: dict[str, Any]
    actor_id: uuid.UUID | None
    actor_name: str | None
    actor_email: str | None
    target_id: uuid.UUID | None
    target_name: str | None
    target_email: str | None


class AuditPage(BaseModel):
    items: list[AuditEvent]
    next_before: int | None


# ----------------------------------------------------------------------------- overview & system
@router.get("/overview")
async def overview(
    request: Request,
    _: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    return {
        "users": await admin_repo.user_counts(conn),
        "sessions": await admin_repo.session_counts(conn),
        "results_7d": await admin_repo.triage_counts(conn, 7),
        "daily_results": await admin_repo.daily_results(conn, 14),
        "open_escalations": await admin_repo.open_escalation_count(conn),
        "system": _runtime(request),
    }


def _runtime(request: Request) -> dict[str, Any]:
    state = request.app.state
    engine = state.engine
    writer = state.writer
    s = get_settings()
    return {
        "environment": s.env,
        "version": request.app.version,
        "uptime_seconds": round(time.time() - getattr(state, "started_at", time.time())),
        "model": None if engine is None else {
            "version": engine.meta.version, "provider": engine.provider, "seq_len": engine.meta.seq_len,
            "threshold_watch": engine.meta.threshold_watch, "threshold_elevated": engine.meta.threshold_elevated,
        },
        "model_error": state.engine_error,
        "live_streams": len(state.sessions),
        "telemetry": {"backlog": writer.backlog, "written": writer.written, "dropped": writer.dropped,
                      "consecutive_failures": writer.consecutive_failures},
        "config": {"tick_hz": s.tick_hz, "persist_hz": s.persist_hz, "audio_ring_seconds": s.audio_ring_seconds,
                   "access_token_minutes": s.access_token_ttl_seconds // 60,
                   "refresh_token_days": s.refresh_token_ttl_seconds // 86400,
                   "login_max_failures": s.login_max_failures, "min_baseline_seconds": s.min_baseline_seconds},
    }


@router.get("/system")
async def system(
    request: Request,
    _: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> dict[str, Any]:
    stats = await admin_repo.database_stats(conn)
    return {**_runtime(request), "database": {**stats, "reachable": await db.ping()}}


# ----------------------------------------------------------------------------- users
@router.get("/users", response_model=UserPage)
async def users(
    search: str | None = Query(None, max_length=120),
    role: Role | None = Query(None),
    state: Literal["active", "disabled", "locked"] | None = Query(None),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> UserPage:
    rows, total = await admin_repo.search_users(
        conn, search=(search or "").strip() or None, role=role.value if role else None, state=state,
        limit=limit, offset=offset,
    )
    now = datetime.now().astimezone()
    items = [
        AdminUser(**{k: r[k] for k in r.keys() if k != "locked_until"},
                  locked=r["locked_until"] is not None and r["locked_until"] > now)
        for r in rows
    ]
    return UserPage(items=items, total=total, limit=limit, offset=offset)


async def _require_user(conn: asyncpg.Connection, user_id: uuid.UUID) -> asyncpg.Record:
    row = await repo.get_user_by_id(conn, user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return row


@router.put("/users/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def set_role(
    user_id: uuid.UUID, body: RoleBody, request: Request,
    admin: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    if user_id == admin.id and body.role is not Role.ADMIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can’t remove your own admin role")
    async with conn.transaction():
        before = await _require_user(conn, user_id)
        await repo.set_user_role(conn, user_id, body.role.value)
        await repo.revoke_all_for_user(conn, user_id, "role_changed")
        if before["role"] == Role.CLINICIAN.value and body.role is not Role.CLINICIAN:
            await conn.execute("DELETE FROM care_team WHERE clinician_id = $1", user_id)
        await repo.audit(conn, "admin.set_role", actor_id=admin.id, target_id=user_id, ip=_ip(request),
                         detail={"from": before["role"], "to": body.role.value})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/users/{user_id}/active", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def set_active(
    user_id: uuid.UUID, body: ActiveBody, request: Request,
    admin: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    if user_id == admin.id and not body.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can’t disable your own account")
    async with conn.transaction():
        await _require_user(conn, user_id)
        await admin_repo.set_active(conn, user_id, body.is_active)
        if not body.is_active:
            await repo.revoke_all_for_user(conn, user_id, "account_disabled")
        await repo.audit(conn, "admin.enable_user" if body.is_active else "admin.disable_user",
                         actor_id=admin.id, target_id=user_id, ip=_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/unlock", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def unlock_user(
    user_id: uuid.UUID, request: Request,
    admin: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    async with conn.transaction():
        await _require_user(conn, user_id)
        await admin_repo.unlock(conn, user_id)
        await repo.audit(conn, "admin.unlock_user", actor_id=admin.id, target_id=user_id, ip=_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/sign-out", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def sign_out_everywhere(
    user_id: uuid.UUID, request: Request,
    admin: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    async with conn.transaction():
        await _require_user(conn, user_id)
        await repo.bump_token_version(conn, user_id)
        revoked = await repo.revoke_all_for_user(conn, user_id, "admin_sign_out")
        await repo.audit(conn, "admin.sign_out_user", actor_id=admin.id, target_id=user_id, ip=_ip(request),
                         detail={"refresh_tokens_revoked": revoked})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def send_password_reset(
    user_id: uuid.UUID, request: Request, background: BackgroundTasks,
    admin: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
) -> dict[str, str]:
    """E-mail the user a reset link. Admins never see or set another person's password."""
    row = await _require_user(conn, user_id)
    if not row["is_active"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enable the account before sending a reset link")
    async with conn.transaction():
        mail = await issue_password_reset(conn, user_row=row, requested_by=admin.id, ip=_ip(request))
        if mail is not None:
            await repo.audit(conn, "admin.password_reset_sent", actor_id=admin.id, target_id=user_id, ip=_ip(request))
    if mail is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Several reset links were sent in the last hour. Try again later.")
    background.add_task(mailer.send, mail)
    return {"detail": f"Reset link sent to {row['email']}"}


# ----------------------------------------------------------------------------- care team
@router.get("/care-team", response_model=list[CareLink])
async def care_team(_: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db)) -> list[CareLink]:
    return [CareLink(**dict(r)) for r in await admin_repo.list_care_team(conn)]


@router.post("/care-team", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def assign(
    body: CareLinkBody, request: Request,
    admin: CurrentUser = Depends(admin_only), conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    clinician = await repo.get_user_by_id(conn, body.clinician_id)
    patient = await repo.get_user_by_id(conn, body.patient_id)
    if clinician is None or clinician["role"] != Role.CLINICIAN.value or not clinician["is_active"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an active clinician")
    if patient is None or patient["role"] != Role.PATIENT.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a patient")
    async with conn.transaction():
        await repo.assign_care_team(conn, body.clinician_id, body.patient_id, admin.id)
        await repo.audit(conn, "admin.care_team_assign", actor_id=admin.id, target_id=body.patient_id, ip=_ip(request),
                         detail={"clinician_id": str(body.clinician_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/care-team", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def unassign(
    request: Request,
    clinician_id: uuid.UUID = Query(...),
    patient_id: uuid.UUID = Query(...),
    admin: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    async with conn.transaction():
        if not await admin_repo.remove_care_team(conn, clinician_id, patient_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That assignment doesn’t exist")
        await repo.audit(conn, "admin.care_team_remove", actor_id=admin.id, target_id=patient_id, ip=_ip(request),
                         detail={"clinician_id": str(clinician_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- escalations & audit
@router.get("/escalations", response_model=list[Escalation])
async def escalations(
    open_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    _: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[Escalation]:
    rows = await admin_repo.escalations(conn, only_open=open_only, limit=limit)
    return [Escalation(**{**dict(r), "red_flags": list(r["red_flags"] or [])}) for r in rows]


@router.get("/audit", response_model=AuditPage)
async def audit(
    category: Literal["auth", "session", "admin", "report", "care_note", "cli"] | None = Query(None),
    search: str | None = Query(None, max_length=120),
    before: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> AuditPage:
    rows = await admin_repo.audit_events(
        conn, category=category, search=(search or "").strip() or None, before_id=before, limit=limit
    )
    items = [AuditEvent(**{**dict(r), "detail": r["detail"] or {}}) for r in rows]
    return AuditPage(items=items, next_before=items[-1].id if len(items) == limit else None)


# Patient choice lists for the care-team form (admins see everyone; kept small and paged by search).
@router.get("/people")
async def people(
    role: Role = Query(...),
    search: str | None = Query(None, max_length=120),
    _: CurrentUser = Depends(admin_only),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[dict[str, Any]]:
    rows, _total = await admin_repo.search_users(
        conn, search=(search or "").strip() or None, role=role.value, state="active", limit=200, offset=0
    )
    return [{"id": str(r["id"]), "full_name": r["full_name"], "email": r["email"]} for r in rows]

