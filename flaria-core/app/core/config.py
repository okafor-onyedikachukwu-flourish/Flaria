"""Runtime configuration for flaria-core.

All settings are read from environment variables prefixed with ``FLARIA_``
(or a local ``.env`` file in development). Production refuses to start with
insecure defaults: weak JWT secret, non-Secure cookies or wildcard CORS.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CORE_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLARIA_", env_file=".env", env_file_encoding="utf-8", extra="ignore",
        protected_namespaces=("settings_",),
    )

    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql://flaria:flaria@localhost:5432/flaria"
    db_pool_min: int = Field(2, ge=1)
    db_pool_max: int = Field(20, ge=2)
    auto_migrate: bool = True
    schema_path: Path = CORE_ROOT / "app" / "database" / "schema.sql"

    # --- Auth ---------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me-0000")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "flaria-core"
    jwt_audience: str = "flaria-web"
    access_token_ttl_seconds: int = Field(900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(604800, ge=3600)
    refresh_reuse_grace_seconds: int = Field(10, ge=0, le=60)
    refresh_cookie_name: str = "flaria_rt"
    refresh_cookie_path: str = "/api/v1/auth"
    cookie_secure: bool = True
    login_max_failures: int = Field(5, ge=1)
    login_lockout_seconds: int = Field(900, ge=30)

    # --- Password reset & email ----------------------------------------------
    public_app_url: str = "http://localhost:5173"  # base for links in emails
    password_reset_ttl_minutes: int = Field(30, ge=5, le=240)
    password_reset_max_per_hour: int = Field(3, ge=1, le=20)
    mail_backend: Literal["smtp", "brevo", "console"] = "console"
    brevo_api_key: SecretStr | None = None
    brevo_api_url: str = "https://api.brevo.com/v3/smtp/email"
    mail_from: str = "Flaria <no-reply@flaria.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = 15.0

    # --- Deployment ---------------------------------------------------------
    # Demo mode: results are labelled as demonstrations (no trained, validated model yet).
    demo_mode: bool = False

    # --- Research mode ------------------------------------------------------
    # Turns the deployment into a study: participants must give informed consent
    # before any session, clinicians record each participant's confirmed group,
    # and admins can export de-identified measurements.
    research_mode: bool = False
    study_title: str = "Flaria voice and facial movement study"
    study_institution: str = ""
    study_investigator: str = ""
    study_contact_email: str = ""
    # Approval number from the ethics committee. Empty means not yet approved:
    # the app says so on the consent page and in every export.
    study_ethics_reference: str = ""
    # Serve the built web app (flaria-web/dist) from this process (single-container deploys).
    web_dist_path: Path | None = None
    # First-start admin: created (or promoted) when no admin exists, then e-mailed a link to set a password.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_name: str = "Administrator"

    # --- HTTP ---------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:5173"]
    trusted_hosts: list[str] = ["*"]

    # --- Inference ----------------------------------------------------------
    model_path: Path = CORE_ROOT / "models" / "flaria_core.onnx"
    model_meta_path: Path = CORE_ROOT / "models" / "flaria_core.meta.json"
    inference_threads: int = Field(4, ge=1, le=64)
    ort_intra_op_threads: int = Field(1, ge=1, le=32)
    prefer_cuda: bool = True

    # --- Streaming ----------------------------------------------------------
    tick_hz: float = Field(30.0, gt=0, le=60.0)
    tick_encoding: Literal["json", "binary"] = "json"
    audio_sample_rate: int = 16000
    audio_ring_seconds: float = Field(1.0, ge=0.6, le=5.0)
    audio_window_seconds: float = Field(0.5, ge=0.25, le=1.0)
    audio_hop_seconds: float = Field(0.1, ge=0.02, le=0.5)
    persist_hz: float = Field(1.0, gt=0, le=5.0)
    ws_max_message_bytes: int = 64 * 1024
    ws_max_messages_per_second: int = 120
    ws_reauth_grace_seconds: int = 60
    ws_idle_timeout_seconds: float = 10.0
    session_warmup_seconds: float = Field(10.0, ge=3.0, le=120.0)
    min_baseline_seconds: float = Field(60.0, ge=10.0)
    # Lowest face-tracking rate the sample-count requirements are sized for. Clients on slow
    # devices adapt their detection rate (see LiveCameraHUD), so counts must not assume 30 fps.
    vision_nominal_fps: float = Field(8.0, ge=2.0, le=60.0)
    signal_lost_seconds: float = Field(2.0, ge=0.5, le=30.0)
    stale_session_seconds: int = 120

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        # On Render, default the public URL and allowed origin to the service's own address.
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        if render_url:
            if "public_app_url" not in self.model_fields_set:
                self.public_app_url = render_url
            if "cors_origins" not in self.model_fields_set:
                self.cors_origins = [render_url]
        if self.env == "production":
            secret = self.jwt_secret.get_secret_value()
            if len(secret) < 40 or "change-me" in secret:
                raise ValueError("FLARIA_JWT_SECRET must be a random secret of at least 40 chars in production")
            if not self.cookie_secure:
                raise ValueError("FLARIA_COOKIE_SECURE must be true in production")
            if any(o.strip() == "*" for o in self.cors_origins):
                raise ValueError("Wildcard CORS origins are not allowed with credentialed requests")
            if self.mail_backend == "console":
                raise ValueError("FLARIA_MAIL_BACKEND must be smtp or brevo in production (console logs reset links)")
            if self.mail_backend == "brevo" and not self.brevo_api_key:
                raise ValueError("FLARIA_BREVO_API_KEY is required when FLARIA_MAIL_BACKEND=brevo")
            if not self.public_app_url.startswith("https://"):
                raise ValueError("FLARIA_PUBLIC_APP_URL must be https in production (reset links carry secrets)")
        if self.db_pool_min > self.db_pool_max:
            raise ValueError("db_pool_min must be <= db_pool_max")
        if self.smtp_ssl and self.smtp_starttls:
            raise ValueError("Use either FLARIA_SMTP_SSL (implicit TLS, port 465) or FLARIA_SMTP_STARTTLS, not both")
        if self.audio_window_seconds >= self.audio_ring_seconds:
            raise ValueError("audio_window_seconds must be shorter than audio_ring_seconds")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
