"""Password hashing, JWT issuance/validation and FastAPI auth dependencies.

* Argon2id via passlib (64 MiB memory, t=3, p=4) with transparent re-hash when
  parameters are raised.
* Access tokens: 15 min JWTs carrying ``sub``, ``role``, ``ver`` (the user's
  token_version, bumped on password change / logout-all) and ``jti``.
* Refresh tokens: 7 day JWTs bound to a rotation *family*. Every refresh
  rotates the token; presenting an already-rotated token outside a short race
  grace window is treated as theft and revokes the whole family.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

import asyncpg
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from app.core.config import get_settings
from app.database.session import get_db

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__type="ID",
    argon2__memory_cost=65536,
    argon2__time_cost=3,
    argon2__parallelism=4,
)

# Hash used to equalise timing when the e-mail is unknown (prevents user enumeration).
_TIMING_DUMMY_HASH = pwd_context.hash("flaria-timing-equaliser-not-a-password")


class Role(str, Enum):
    PATIENT = "PATIENT"
    CLINICIAN = "CLINICIAN"
    ADMIN = "ADMIN"


class TokenError(Exception):
    """Raised for any invalid, expired, revoked or wrongly-typed token."""


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    token_version: int


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    jti: uuid.UUID
    expires_at: datetime


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> tuple[bool, str | None]:
    """Return ``(valid, new_hash_if_rehash_needed)``. Always spends Argon2 time."""
    if not password_hash:
        pwd_context.verify(password, _TIMING_DUMMY_HASH)
        return False, None
    try:
        return pwd_context.verify_and_update(password, password_hash)
    except (ValueError, TypeError):
        return False, None


# --------------------------------------------------------------------------- tokens
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(claims: dict[str, Any]) -> str:
    s = get_settings()
    return jwt.encode(claims, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)


def create_access_token(user_id: uuid.UUID, role: Role, token_version: int) -> IssuedToken:
    s = get_settings()
    now = _now()
    exp = now + timedelta(seconds=s.access_token_ttl_seconds)
    jti = uuid.uuid4()
    token = _encode(
        {
            "iss": s.jwt_issuer,
            "aud": s.jwt_audience,
            "sub": str(user_id),
            "role": role.value,
            "ver": token_version,
            "typ": "access",
            "jti": str(jti),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
    )
    return IssuedToken(token, jti, exp)


def create_refresh_token(user_id: uuid.UUID, family_id: uuid.UUID, token_version: int) -> IssuedToken:
    s = get_settings()
    now = _now()
    exp = now + timedelta(seconds=s.refresh_token_ttl_seconds)
    jti = uuid.uuid4()
    token = _encode(
        {
            "iss": s.jwt_issuer,
            "aud": s.jwt_audience,
            "sub": str(user_id),
            "fam": str(family_id),
            "ver": token_version,
            "typ": "refresh",
            "jti": str(jti),
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
    )
    return IssuedToken(token, jti, exp)


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    s = get_settings()
    try:
        claims = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "jti", "typ"]},
            leeway=5,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if claims.get("typ") != expected_type:
        raise TokenError("wrong token type")
    try:
        uuid.UUID(str(claims["sub"]))
        uuid.UUID(str(claims["jti"]))
    except (ValueError, KeyError) as exc:
        raise TokenError("malformed token subject") from exc
    return claims


# --------------------------------------------------------------------------- dependencies
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def row_to_user(row: asyncpg.Record) -> CurrentUser:
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role=Role(row["role"]),
        is_active=row["is_active"],
        token_version=row["token_version"],
    )


async def resolve_access_token(conn: asyncpg.Connection, token: str) -> tuple[CurrentUser, dict[str, Any]]:
    """Validate an access token against the live user record. Raises TokenError."""
    claims = decode_token(token, "access")
    row = await conn.fetchrow(
        "SELECT id, email::text AS email, full_name, role::text AS role, is_active, token_version "
        "FROM users WHERE id = $1",
        uuid.UUID(claims["sub"]),
    )
    if row is None:
        raise TokenError("unknown subject")
    user = row_to_user(row)
    if user.token_version != claims.get("ver"):
        raise TokenError("token revoked")
    if not user.is_active:
        raise TokenError("account disabled")
    return user, claims


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    conn: asyncpg.Connection = Depends(get_db),
) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise _unauthorized()
    try:
        user, _ = await resolve_access_token(conn, creds.credentials)
    except TokenError as exc:
        raise _unauthorized(str(exc)) from None
    return user


async def get_current_active_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    return user


def require_roles(*roles: Role) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    allowed = frozenset(roles)

    async def _dep(user: CurrentUser = Depends(get_current_active_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _dep
