"""HTTP request/response models."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import Role

_PASSWORD_CLASSES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"\d"), re.compile(r"[^A-Za-z0-9]"))


def check_password_strength(v: str) -> str:
    """Single password policy for sign-up, password change and reset (mirrored in the web client)."""
    if sum(bool(p.search(v)) for p in _PASSWORD_CLASSES) < 3:
        raise ValueError("password must mix at least three of: lowercase, uppercase, digits, symbols")
    return v


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_password_strength(v)

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("full name is required")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_password_strength(v)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        return check_password_strength(v)


class AccountOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    created_at: datetime
    last_login_at: datetime | None
    password_changed_at: datetime | None
    signed_in_devices: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    role: Role


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    expires_at: datetime
    user: UserOut


class RoleUpdate(BaseModel):
    role: Role


class CareTeamAssign(BaseModel):
    clinician_id: uuid.UUID
    patient_id: uuid.UUID


class PatientOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


class SessionOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    started_by: uuid.UUID
    status: str
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    model_version: str
    execution_provider: str
    tick_count: int
    peak_anomaly: float | None
    mean_anomaly: float | None


class TelemetryPointOut(BaseModel):
    ts: datetime
    audio_vec: list[float] | None
    vision_vec: list[float] | None
    f0_hz: float | None
    jitter_local: float | None
    shimmer_local: float | None
    hnr_db: float | None
    anomaly: float | None
    anomaly_peak: float | None
    risk_status: str
    face_coverage: float
    voiced_ratio: float


class BaselineSummary(BaseModel):
    patient_id: uuid.UUID
    audio_samples: int
    vision_samples: int
    days_covered: int
    ready: bool


class MeasureOut(BaseModel):
    name: str
    z: float
    usual: str


class FindingOut(BaseModel):
    id: str
    title: str
    severity: str
    summary: str
    measures: list[MeasureOut]
    possible_causes: list[str]


class ReportOut(BaseModel):
    session_id: uuid.UUID
    patient_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    triage: str
    headline: str
    advice: list[str]
    findings: list[FindingOut]
    red_flags: list[str]
    red_flag_options: dict[str, str]
    summary: dict[str, Any]
    rules_version: str
    demo: bool = False


class RedFlagsUpdate(BaseModel):
    red_flags: list[str] = Field(default_factory=list, max_length=16)


class CareNoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("note cannot be empty")
        return v


class CareNoteOut(BaseModel):
    id: int
    patient_id: uuid.UUID
    body: str
    created_at: datetime
    author_name: str | None
    author_role: str | None
