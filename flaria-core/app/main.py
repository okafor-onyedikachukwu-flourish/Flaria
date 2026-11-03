"""Flaria core API server.

Run (development):   uvicorn app.main:app --reload
Run (production):    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
                         --proxy-headers --forwarded-allow-ips <proxy-ip> --ws-max-size 65536

One worker per container: stream sessions are stateful and CPU-bound work is
already parallelised through the inference thread pool. Scale horizontally
behind a load balancer with sticky WebSocket routing.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import admin as admin_api
from app.api import auth as auth_api
from app.bootstrap import bootstrap_admin
from app.api import sessions as sessions_api
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.security import CurrentUser, Role, TokenError, resolve_access_token
from app.database import repositories as repo
from app.database.session import db
from app.services.inference import InferenceEngine, ModelUnavailableError
from app.services.mailer import Mailer
from app.services.stream_session import CloseCode, SessionRegistry, StreamSession
from app.services.telemetry_writer import TelemetryWriter

settings = get_settings()
logger = logging.getLogger("flaria.main")

SUBPROTOCOL = "flaria.v1"
BEARER_PREFIX = "bearer."


async def resolve_token(token: str) -> tuple[CurrentUser, dict]:
    async with db.pool.acquire() as conn:
        return await resolve_access_token(conn, token)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    await db.connect(settings.database_url, settings.db_pool_min, settings.db_pool_max)
    if settings.auto_migrate:
        await db.apply_schema(settings.schema_path)
    async with db.pool.acquire() as conn:
        made = await repo.ensure_partitions(conn, 3)
        aborted = await repo.abort_stale_sessions(conn, settings.stale_session_seconds)
    logger.info("startup maintenance", extra={"fields": {"partitions_created": made, "stale_sessions_aborted": aborted}})

    app.state.started_at = time.time()
    app.state.mailer = Mailer(settings)
    try:
        await bootstrap_admin(db, settings, app.state.mailer)
    except Exception:  # noqa: BLE001 - a mail or config problem must not keep the service down
        logger.exception("admin bootstrap failed")
    app.state.engine = None
    app.state.engine_error = None
    try:
        app.state.engine = await asyncio.to_thread(
            InferenceEngine, settings.model_path, settings.model_meta_path,
            intra_op_threads=settings.ort_intra_op_threads, prefer_cuda=settings.prefer_cuda,
        )
    except ModelUnavailableError as exc:
        # Auth and history keep working; streaming is refused and readiness reports why.
        app.state.engine_error = str(exc)
        logger.error("inference engine unavailable", extra={"fields": {"error": str(exc)}})

    app.state.executor = ThreadPoolExecutor(max_workers=settings.inference_threads, thread_name_prefix="flaria-compute")
    app.state.writer = TelemetryWriter(db)
    app.state.writer.start()
    app.state.sessions = SessionRegistry()
    try:
        yield
    finally:
        await app.state.sessions.shutdown()
        await app.state.writer.stop()
        app.state.executor.shutdown(wait=True, cancel_futures=True)
        await db.close()
        logger.info("shutdown complete")


app = FastAPI(
    title="Flaria Core",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.env == "production" else "/docs",
    redoc_url=None,
    openapi_url=None if settings.env == "production" else "/openapi.json",
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    max_age=600,
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_ctx.set(rid[:64])
    start = time.perf_counter()
    try:
        response: Response = await call_next(request)
    except Exception:
        logger.exception("unhandled error", extra={"fields": {"path": request.url.path}})
        response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    finally:
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if settings.env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - start) * 1000:.1f}"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Never echo submitted values (passwords) back to the client.
    errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


app.include_router(auth_api.router)
app.include_router(sessions_api.router)
app.include_router(admin_api.router)


def effective_demo_mode(engine: InferenceEngine | None) -> bool:
    """Demo when configured, and always when the loaded model is the CI stub (or none is loaded)."""
    return settings.demo_mode or engine is None or engine.meta.version.startswith("STUB")


@app.get("/api/v1/config", include_in_schema=False)
async def public_config() -> dict[str, bool]:
    """Non-secret settings the web app needs before sign-in."""
    return {"demo_mode": effective_demo_mode(app.state.engine)}


@app.get("/health/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    db_ok = await db.ping()
    engine = app.state.engine
    writer: TelemetryWriter = app.state.writer
    body = {
        "database": db_ok,
        "model": None if engine is None else {"version": engine.meta.version, "provider": engine.provider},
        "model_error": app.state.engine_error,
        "active_sessions": len(app.state.sessions),
        "telemetry_backlog": writer.backlog,
        "telemetry_dropped": writer.dropped,
    }
    ok = db_ok and engine is not None and writer.consecutive_failures < 5
    return JSONResponse(body, status_code=200 if ok else 503)


def _extract_ws_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Token from the ``bearer.<jwt>`` subprotocol (preferred) or ``?token=`` fallback."""
    offered: list[str] = list(websocket.scope.get("subprotocols") or [])
    token = None
    for proto in offered:
        if proto.startswith(BEARER_PREFIX):
            token = proto[len(BEARER_PREFIX):]
            break
    if token is None:
        token = websocket.query_params.get("token")
    chosen = SUBPROTOCOL if SUBPROTOCOL in offered else None
    return token, chosen


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        await websocket.close(code=CloseCode.POLICY)  # pre-accept close => HTTP 403 (CSWSH guard)
        return
    token, subprotocol = _extract_ws_token(websocket)
    await websocket.accept(subprotocol=subprotocol)

    if not token or len(token) > 4096:
        await websocket.close(code=CloseCode.UNAUTHORIZED, reason="missing_token")
        return
    try:
        user, claims = await resolve_token(token)
    except TokenError as exc:
        await websocket.close(code=CloseCode.UNAUTHORIZED, reason=str(exc)[:120])
        return

    engine: InferenceEngine | None = websocket.app.state.engine
    if engine is None:
        await websocket.close(code=CloseCode.TRY_AGAIN_LATER, reason="model_unavailable")
        return

    patient_param = websocket.query_params.get("patient_id")
    try:
        patient_id = uuid.UUID(patient_param) if patient_param else user.id
    except ValueError:
        await websocket.close(code=CloseCode.POLICY, reason="invalid_patient_id")
        return
    async with db.pool.acquire() as conn:
        if user.role is Role.PATIENT and patient_id != user.id:
            allowed = False
        elif user.role is Role.CLINICIAN and patient_id != user.id:
            allowed = await repo.is_clinician_of(conn, user.id, patient_id)
        else:
            allowed = user.role is Role.ADMIN or patient_id == user.id
        if allowed and patient_id != user.id:
            target = await repo.get_user_by_id(conn, patient_id)
            allowed = target is not None and target["role"] == Role.PATIENT.value and target["is_active"]
    if not allowed:
        await websocket.close(code=CloseCode.FORBIDDEN, reason="patient_access_denied")
        return

    client_info = {
        "user_agent": (websocket.headers.get("user-agent") or "")[:256],
        "ip": websocket.client.host if websocket.client else None,
    }
    try:
        session = await StreamSession.open(
            ws=websocket, user=user, patient_id=patient_id, auth_exp=float(claims["exp"]), settings=settings,
            engine=engine, executor=websocket.app.state.executor, writer=websocket.app.state.writer, pool=db.pool,
            auth_resolver=resolve_token, client_info=client_info,
        )
    except repo.ActiveSessionConflict:
        await websocket.close(code=CloseCode.CONFLICT, reason="session_already_active")
        return
    except Exception:
        logger.exception("session open failed")
        await websocket.close(code=CloseCode.INTERNAL, reason="session_open_failed")
        return

    registry: SessionRegistry = websocket.app.state.sessions
    registry.add(session)
    try:
        await session.run()
    finally:
        registry.discard(session)


# Must come last: the web app's catch-all route would otherwise shadow API routes.
if settings.web_dist_path is not None:
    from app.web import mount_web

    mount_web(app, settings.web_dist_path)
