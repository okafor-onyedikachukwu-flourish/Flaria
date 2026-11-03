"""/api/v1/auth - registration, login, refresh-token rotation and logout."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status

from app.core.config import get_settings
from app.core.security import (
    CurrentUser, Role, TokenError, create_access_token, create_refresh_token, decode_token,
    get_current_active_user, hash_password, verify_password,
)
from app.database import repositories as repo
from app.database.session import get_db
from app.schemas import (
    AccountOut, ChangePasswordRequest, LoginRequest, PasswordResetConfirm, PasswordResetRequest, RegisterRequest,
    TokenResponse, UserOut,
)
from app.services.mailer import Mail, Mailer, password_changed_mail, reset_mail

logger = logging.getLogger("flaria.auth")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


SESSION_HINT_COOKIE = "flaria_has_session"


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.refresh_cookie_name,
        value=token,
        max_age=s.refresh_token_ttl_seconds,
        path=s.refresh_cookie_path,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
    )
    # Readable hint (no secret) so the web app can skip the refresh call for visitors
    # who have never signed in, including search-engine crawlers.
    response.set_cookie(
        key=SESSION_HINT_COOKIE, value="1", max_age=s.refresh_token_ttl_seconds, path="/",
        httponly=False, secure=s.cookie_secure, samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=s.refresh_cookie_name, path=s.refresh_cookie_path, httponly=True, secure=s.cookie_secure, samesite="strict"
    )
    response.delete_cookie(key=SESSION_HINT_COOKIE, path="/", secure=s.cookie_secure, samesite="strict")


def _revoked(detail: str) -> HTTPException:
    """401 that also expires the refresh cookie (headers survive HTTPException, the injected Response does not)."""
    s = get_settings()
    cookie = (f"{s.refresh_cookie_name}=; Max-Age=0; Path={s.refresh_cookie_path}; HttpOnly; SameSite=Strict"
              + ("; Secure" if s.cookie_secure else ""))
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"Set-Cookie": cookie, "Cache-Control": "no-store"})


def _check_origin(request: Request) -> None:
    """Defence in depth on cookie-authenticated endpoints (SameSite=Strict is the primary control)."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().cors_origins:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin not allowed")


async def _issue_session(
    conn: asyncpg.Connection,
    response: Response,
    request: Request,
    *,
    user_id: uuid.UUID,
    email: str,
    full_name: str,
    role: Role,
    token_version: int,
    family_id: uuid.UUID | None = None,
) -> tuple[TokenResponse, uuid.UUID]:
    """Issue an access token + rotated refresh cookie. Returns the response body and new refresh jti."""
    s = get_settings()
    family = family_id or uuid.uuid4()
    access = create_access_token(user_id, role, token_version)
    refresh = create_refresh_token(user_id, family, token_version)
    await repo.insert_refresh_token(
        conn, jti=refresh.jti, user_id=user_id, family_id=family, expires_at=refresh.expires_at,
        user_agent=request.headers.get("user-agent"), ip=_client_ip(request),
    )
    _set_refresh_cookie(response, refresh.token)
    response.headers["Cache-Control"] = "no-store"
    body = TokenResponse(
        access_token=access.token,
        expires_in=s.access_token_ttl_seconds,
        expires_at=access.expires_at,
        user=UserOut(id=user_id, email=email, full_name=full_name, role=role),
    )
    return body, refresh.jti


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, response: Response, conn: asyncpg.Connection = Depends(get_db)
) -> TokenResponse:
    _check_origin(request)
    # Argon2 is CPU-bound (~50-100 ms); keep it off the event loop.
    password_hash = await asyncio.to_thread(hash_password, body.password)
    async with conn.transaction():
        try:
            row = await repo.create_user(
                conn, email=body.email.lower(), password_hash=password_hash, full_name=body.full_name, role="PATIENT"
            )
        except repo.DuplicateEmailError:
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists") from None
        await repo.audit(conn, "auth.register", actor_id=row["id"], target_id=row["id"], ip=_client_ip(request))
        result, _ = await _issue_session(
            conn, response, request, user_id=row["id"], email=row["email"], full_name=row["full_name"],
            role=Role(row["role"]), token_version=row["token_version"],
        )
        return result


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, conn: asyncpg.Connection = Depends(get_db)
) -> TokenResponse:
    _check_origin(request)
    s = get_settings()
    row = await repo.get_user_by_email(conn, body.email.lower())
    now = datetime.now(timezone.utc)
    if row is not None and row["locked_until"] is not None and row["locked_until"] > now:
        # Still spend hashing time so lockout state is not a timing oracle.
        await asyncio.to_thread(verify_password, body.password, None)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later.",
                            headers={"Retry-After": str(int((row["locked_until"] - now).total_seconds()) + 1)})
    valid, new_hash = await asyncio.to_thread(verify_password, body.password, row["password_hash"] if row else None)
    if row is None or not valid:
        if row is not None:
            async with conn.transaction():
                await repo.record_login_failure(conn, row["id"], s.login_max_failures, s.login_lockout_seconds)
                await repo.audit(conn, "auth.login_failed", target_id=row["id"], ip=_client_ip(request))
        raise _INVALID_CREDENTIALS
    if not row["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")
    async with conn.transaction():
        await repo.record_login_success(conn, row["id"], new_hash)
        await repo.audit(conn, "auth.login", actor_id=row["id"], ip=_client_ip(request))
        result, _ = await _issue_session(
            conn, response, request, user_id=row["id"], email=row["email"], full_name=row["full_name"],
            role=Role(row["role"]), token_version=row["token_version"],
        )
        return result


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, conn: asyncpg.Connection = Depends(get_db)) -> TokenResponse:
    _check_origin(request)
    s = get_settings()
    raw = request.cookies.get(s.refresh_cookie_name)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh session")
    try:
        claims = decode_token(raw, "refresh")
        jti = uuid.UUID(claims["jti"])
        family_id = uuid.UUID(claims["fam"])
    except (TokenError, KeyError, ValueError):
        raise _revoked("Refresh session invalid") from None

    # Revocations must COMMIT before we answer 401, so the failure decision is
    # taken inside the transaction and raised only after it closes.
    failure: HTTPException | None = None
    result: TokenResponse | None = None
    async with conn.transaction():
        record = await repo.lock_refresh_token(conn, jti)
        if record is None or record["revoked_at"] is not None or record["expires_at"] <= datetime.now(timezone.utc):
            failure = _revoked("Refresh session revoked")
        elif record["rotated_at"] is not None:
            grace = timedelta(seconds=s.refresh_reuse_grace_seconds)
            if datetime.now(timezone.utc) - record["rotated_at"] <= grace:
                # Benign race (two tabs refreshing at once): the cookie jar already holds the successor.
                failure = HTTPException(status.HTTP_409_CONFLICT, "Refresh already rotated; retry")
            else:
                await repo.revoke_family(conn, family_id, "reuse_detected")
                await repo.audit(conn, "auth.refresh_reuse", target_id=record["user_id"], ip=_client_ip(request),
                                 detail={"family_id": str(family_id)})
                logger.warning("refresh token reuse detected", extra={"fields": {"user_id": str(record["user_id"])}})
                failure = _revoked("Refresh session revoked")
        else:
            user = await repo.get_user_by_id(conn, record["user_id"])
            if user is None or not user["is_active"] or user["token_version"] != claims.get("ver"):
                await repo.revoke_family(conn, family_id, "user_state_changed")
                failure = _revoked("Refresh session revoked")
            else:
                result, new_jti = await _issue_session(
                    conn, response, request, user_id=user["id"], email=user["email"], full_name=user["full_name"],
                    role=Role(user["role"]), token_version=user["token_version"], family_id=family_id,
                )
                await repo.mark_rotated(conn, jti, new_jti)
    if failure is not None:
        raise failure
    assert result is not None
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(request: Request, response: Response, conn: asyncpg.Connection = Depends(get_db)) -> Response:
    _check_origin(request)
    s = get_settings()
    raw = request.cookies.get(s.refresh_cookie_name)
    if raw:
        try:
            claims = decode_token(raw, "refresh")
            async with conn.transaction():
                await repo.revoke_family(conn, uuid.UUID(claims["fam"]), "logout")
                await repo.audit(conn, "auth.logout", actor_id=uuid.UUID(claims["sub"]), ip=_client_ip(request))
        except (TokenError, KeyError, ValueError):
            pass
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_refresh_cookie(response)
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout_all(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    async with conn.transaction():
        await repo.bump_token_version(conn, user.id)
        await repo.revoke_all_for_user(conn, user.id, "logout_all")
        await repo.audit(conn, "auth.logout_all", actor_id=user.id, ip=_client_ip(request))
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(get_current_active_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role)


# ============================================================================ passwords
RESET_ACCEPTED = {"detail": "If an account exists for that email, a reset link is on its way. It expires soon, so use it shortly."}


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def issue_password_reset(
    conn: asyncpg.Connection,
    *,
    user_row: asyncpg.Record,
    requested_by: uuid.UUID | None,
    ip: str | None,
) -> Mail | None:
    """Store a single-use reset token and return the e-mail to send, or None at the hourly limit.

    Callers queue the e-mail with BackgroundTasks *after* their transaction
    commits: the link is never sent for a token that was rolled back, and the
    response time doesn't reveal whether an address has an account.
    """
    s = get_settings()
    if await repo.recent_reset_requests(conn, user_row["id"]) >= s.password_reset_max_per_hour:
        return None
    token = secrets.token_urlsafe(32)
    await repo.create_reset_token(conn, user_id=user_row["id"], token_hash=hash_reset_token(token),
                                  ttl_minutes=s.password_reset_ttl_minutes, requested_by=requested_by, ip=ip)
    # The token travels in the URL fragment, which browsers never send to servers or in Referer headers.
    url = f"{s.public_app_url.rstrip('/')}/reset-password#token={token}"
    return reset_mail(user_row["email"], user_row["full_name"], url, s.password_reset_ttl_minutes,
                      by_admin=requested_by is not None)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    background: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
) -> dict[str, str]:
    _check_origin(request)
    row = await repo.get_user_by_email(conn, body.email.lower())
    if row is not None and row["is_active"]:
        async with conn.transaction():
            mail = await issue_password_reset(conn, user_row=row, requested_by=None, ip=_client_ip(request))
            await repo.audit(conn, "auth.password_reset_requested" if mail else "auth.password_reset_throttled",
                             target_id=row["id"], ip=_client_ip(request))
        if mail is not None:
            background.add_task(mailer.send, mail)
    # Identical answer whether or not the account exists (no account enumeration).
    return RESET_ACCEPTED


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def confirm_password_reset(
    body: PasswordResetConfirm,
    request: Request,
    background: BackgroundTasks,
    conn: asyncpg.Connection = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
) -> Response:
    _check_origin(request)
    new_hash = await asyncio.to_thread(hash_password, body.new_password)  # outside the transaction: Argon2 is slow
    user: asyncpg.Record | None = None
    async with conn.transaction():
        row = await repo.consume_reset_token(conn, hash_reset_token(body.token))
        if row is not None and row["is_active"]:
            user = row
            await repo.update_password(conn, row["id"], new_hash)
            await repo.revoke_all_for_user(conn, row["id"], "password_reset")
            await repo.audit(conn, "auth.password_reset", actor_id=row["id"], target_id=row["id"], ip=_client_ip(request))
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid, already used or expired. Request a new one.")
    background.add_task(mailer.send, password_changed_mail(user["email"], user["full_name"],
                                                           get_settings().public_app_url.rstrip("/"), via_reset=True))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    user: CurrentUser = Depends(get_current_active_user),
    conn: asyncpg.Connection = Depends(get_db),
    mailer: Mailer = Depends(get_mailer),
) -> TokenResponse:
    _check_origin(request)
    s = get_settings()
    row = await repo.get_user_by_id(conn, user.id)
    assert row is not None
    now = datetime.now(timezone.utc)
    if row["locked_until"] is not None and row["locked_until"] > now:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect passwords. Try again later.",
                            headers={"Retry-After": str(int((row["locked_until"] - now).total_seconds()) + 1)})
    valid, _ = await asyncio.to_thread(verify_password, body.current_password, row["password_hash"])
    if not valid:
        async with conn.transaction():
            await repo.record_login_failure(conn, user.id, s.login_max_failures, s.login_lockout_seconds)
            await repo.audit(conn, "auth.password_change_failed", actor_id=user.id, target_id=user.id, ip=_client_ip(request))
        # 400, not 401: a 401 would make the client treat the session itself as expired.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect.")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a new password that’s different from your current one.")

    new_hash = await asyncio.to_thread(hash_password, body.new_password)
    async with conn.transaction():
        version = await repo.update_password(conn, user.id, new_hash)
        await repo.revoke_all_for_user(conn, user.id, "password_changed")
        await repo.audit(conn, "auth.password_changed", actor_id=user.id, target_id=user.id, ip=_client_ip(request))
        # Every other device is signed out; this one gets a fresh session.
        result, _ = await _issue_session(conn, response, request, user_id=user.id, email=user.email,
                                         full_name=user.full_name, role=user.role, token_version=version)
    background.add_task(mailer.send, password_changed_mail(user.email, user.full_name, s.public_app_url.rstrip("/"),
                                                           via_reset=False))
    return result


@router.get("/account", response_model=AccountOut)
async def account(
    user: CurrentUser = Depends(get_current_active_user), conn: asyncpg.Connection = Depends(get_db)
) -> AccountOut:
    row = await repo.get_user_by_id(conn, user.id)
    assert row is not None
    return AccountOut(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role, created_at=row["created_at"],
        last_login_at=row["last_login_at"], password_changed_at=row["password_changed_at"],
        signed_in_devices=await repo.live_device_count(conn, user.id),
    )
