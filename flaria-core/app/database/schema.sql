-- =============================================================================
-- Flaria core schema (PostgreSQL 14+ / Supabase compatible)
-- Idempotent: safe to run on every boot (guarded by an advisory lock).
--
-- Privacy contract: no table stores raw audio, raw video, images or facial
-- landmark coordinates. Telemetry is limited to 1 Hz aggregates of the 15-D
-- acoustic vector, the 5-D facial-geometry vector, voice-quality scalars and
-- model anomaly scores.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('PATIENT', 'CLINICIAN', 'ADMIN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE session_status AS ENUM ('ACTIVE', 'COMPLETED', 'ABORTED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ----------------------------------------------------------------------------- users
CREATE TABLE IF NOT EXISTS users (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email               citext NOT NULL UNIQUE CHECK (char_length(email) BETWEEN 3 AND 320),
    password_hash       text NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    full_name           text NOT NULL CHECK (char_length(full_name) BETWEEN 1 AND 200),
    role                user_role NOT NULL DEFAULT 'PATIENT',
    is_active           boolean NOT NULL DEFAULT true,
    token_version       integer NOT NULL DEFAULT 0 CHECK (token_version >= 0),
    failed_login_count  integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until        timestamptz,
    last_login_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_role_idx ON users (role) WHERE is_active;

CREATE OR REPLACE FUNCTION flaria_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS users_touch_updated_at ON users;
CREATE TRIGGER users_touch_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION flaria_touch_updated_at();

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at timestamptz;

-- ----------------------------------------------------------------------------- password reset
-- Single-use reset tokens. Only a SHA-256 of the token is stored, so a database
-- read can't be turned into account takeover; the token itself only ever
-- exists in the e-mail link.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash    text NOT NULL UNIQUE CHECK (char_length(token_hash) = 64),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz,
    requested_by  uuid REFERENCES users (id) ON DELETE SET NULL,
    requested_ip  inet,
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS password_reset_tokens_user_idx ON password_reset_tokens (user_id, created_at DESC);

CREATE OR REPLACE FUNCTION flaria_purge_password_reset_tokens(p_keep_days integer DEFAULT 30)
RETURNS bigint
LANGUAGE sql AS $$
    WITH d AS (
        DELETE FROM password_reset_tokens
        WHERE expires_at < now() - make_interval(days => p_keep_days)
        RETURNING 1
    )
    SELECT count(*) FROM d;
$$;

-- ----------------------------------------------------------------------------- refresh tokens
CREATE TABLE IF NOT EXISTS refresh_tokens (
    jti           uuid PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    family_id     uuid NOT NULL,
    issued_at     timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    rotated_at    timestamptz,
    replaced_by   uuid,
    revoked_at    timestamptz,
    revoke_reason text,
    user_agent    text CHECK (char_length(user_agent) <= 512),
    ip            inet,
    CHECK (expires_at > issued_at)
);

CREATE INDEX IF NOT EXISTS refresh_tokens_family_idx ON refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS refresh_tokens_user_live_idx ON refresh_tokens (user_id)
    WHERE revoked_at IS NULL AND rotated_at IS NULL;
CREATE INDEX IF NOT EXISTS refresh_tokens_expiry_idx ON refresh_tokens (expires_at);

-- ----------------------------------------------------------------------------- care team (RBAC scope)
CREATE TABLE IF NOT EXISTS care_team (
    clinician_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    patient_id   uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    created_by   uuid REFERENCES users (id) ON DELETE SET NULL,
    PRIMARY KEY (clinician_id, patient_id),
    CHECK (clinician_id <> patient_id)
);

CREATE INDEX IF NOT EXISTS care_team_patient_idx ON care_team (patient_id);

-- ----------------------------------------------------------------------------- 7-day baselines
-- One row per patient, modality and UTC day holding Welford sufficient
-- statistics (count, mean, M2). The 7-day baseline is the parallel merge of the
-- last seven rows; it is computed in the application (Chan et al. 1979).
CREATE TABLE IF NOT EXISTS baseline_daily (
    patient_id  uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    modality    text NOT NULL,
    day         date NOT NULL,
    n           bigint NOT NULL CHECK (n >= 0),
    mean        double precision[] NOT NULL,
    m2          double precision[] NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (patient_id, modality, day),
    -- audio: 15-D acoustic vector; vision: 5-D facial geometry; voice: f0, jitter, shimmer, HNR
    CONSTRAINT baseline_daily_modality_v2 CHECK (modality IN ('audio', 'vision', 'voice')),
    CONSTRAINT baseline_daily_dims_v2 CHECK (
        array_length(mean, 1) = CASE modality WHEN 'audio' THEN 15 WHEN 'vision' THEN 5 ELSE 4 END
        AND array_length(m2, 1) = array_length(mean, 1))
);

-- Migration for databases created before the voice modality existed.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'baseline_daily_modality_v2') THEN
        ALTER TABLE baseline_daily DROP CONSTRAINT IF EXISTS baseline_daily_modality_check;
        ALTER TABLE baseline_daily DROP CONSTRAINT IF EXISTS baseline_daily_check;
        ALTER TABLE baseline_daily DROP CONSTRAINT IF EXISTS baseline_daily_check1;
        ALTER TABLE baseline_daily ADD CONSTRAINT baseline_daily_modality_v2
            CHECK (modality IN ('audio', 'vision', 'voice'));
        ALTER TABLE baseline_daily ADD CONSTRAINT baseline_daily_dims_v2 CHECK (
            array_length(mean, 1) = CASE modality WHEN 'audio' THEN 15 WHEN 'vision' THEN 5 ELSE 4 END
            AND array_length(m2, 1) = array_length(mean, 1));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS baseline_daily_recent_idx ON baseline_daily (patient_id, day DESC);

-- ----------------------------------------------------------------------------- diagnostic sessions
CREATE TABLE IF NOT EXISTS diagnostic_sessions (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    started_by        uuid NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    status            session_status NOT NULL DEFAULT 'ACTIVE',
    started_at        timestamptz NOT NULL DEFAULT now(),
    ended_at          timestamptz,
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    end_reason        text CHECK (char_length(end_reason) <= 64),
    model_version     text NOT NULL,
    execution_provider text NOT NULL,
    baseline_samples  bigint NOT NULL DEFAULT 0,
    tick_count        bigint NOT NULL DEFAULT 0,
    peak_anomaly      real CHECK (peak_anomaly BETWEEN 0 AND 1),
    mean_anomaly      real CHECK (mean_anomaly BETWEEN 0 AND 1),
    client_info       jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- At most one live session per patient.
CREATE UNIQUE INDEX IF NOT EXISTS diagnostic_sessions_one_active_idx
    ON diagnostic_sessions (patient_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS diagnostic_sessions_patient_idx
    ON diagnostic_sessions (patient_id, started_at DESC);
CREATE INDEX IF NOT EXISTS diagnostic_sessions_heartbeat_idx
    ON diagnostic_sessions (last_heartbeat_at) WHERE status = 'ACTIVE';

-- ----------------------------------------------------------------------------- scalar telemetry (time-series)
CREATE TABLE IF NOT EXISTS telemetry_samples (
    session_id     uuid NOT NULL REFERENCES diagnostic_sessions (id) ON DELETE CASCADE,
    patient_id     uuid NOT NULL,
    ts             timestamptz NOT NULL,
    -- NULL vectors mean "no valid observation of this modality in the interval".
    audio_vec      real[] CHECK (audio_vec IS NULL OR array_length(audio_vec, 1) = 15),
    vision_vec     real[] CHECK (vision_vec IS NULL OR array_length(vision_vec, 1) = 5),
    f0_hz          real,
    jitter_local   real CHECK (jitter_local IS NULL OR jitter_local >= 0),
    shimmer_local  real CHECK (shimmer_local IS NULL OR shimmer_local >= 0),
    hnr_db         real,
    -- NULL anomaly while the calibrator is still warming up.
    anomaly        real CHECK (anomaly IS NULL OR anomaly BETWEEN 0 AND 1),
    anomaly_peak   real CHECK (anomaly_peak IS NULL OR anomaly_peak BETWEEN 0 AND 1),
    risk_status    text NOT NULL CHECK (risk_status IN ('CALIBRATING', 'STABLE', 'WATCH', 'ELEVATED', 'SIGNAL_LOST')),
    face_coverage  real NOT NULL CHECK (face_coverage BETWEEN 0 AND 1),
    voiced_ratio   real NOT NULL CHECK (voiced_ratio BETWEEN 0 AND 1),
    sample_count   integer NOT NULL CHECK (sample_count > 0)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS telemetry_samples_default PARTITION OF telemetry_samples DEFAULT;

CREATE INDEX IF NOT EXISTS telemetry_samples_session_ts_idx ON telemetry_samples (session_id, ts);
CREATE INDEX IF NOT EXISTS telemetry_samples_patient_ts_idx ON telemetry_samples (patient_id, ts DESC);
CREATE INDEX IF NOT EXISTS telemetry_samples_ts_brin ON telemetry_samples USING brin (ts) WITH (pages_per_range = 32);

-- Creates monthly partitions from the current month forward. Called at boot and
-- should also be scheduled (pg_cron or an external job) monthly.
CREATE OR REPLACE FUNCTION flaria_ensure_telemetry_partitions(p_months_ahead integer DEFAULT 3)
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    v_start  date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
    v_from   date;
    v_to     date;
    v_name   text;
    v_made   integer := 0;
BEGIN
    FOR i IN 0..GREATEST(p_months_ahead, 0) LOOP
        v_from := (v_start + make_interval(months => i))::date;
        v_to   := (v_start + make_interval(months => i + 1))::date;
        v_name := format('telemetry_samples_y%sm%s', to_char(v_from, 'YYYY'), to_char(v_from, 'MM'));
        IF to_regclass(v_name) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF telemetry_samples FOR VALUES FROM (%L) TO (%L)',
                v_name, v_from::timestamptz, v_to::timestamptz
            );
            v_made := v_made + 1;
        END IF;
    END LOOP;
    RETURN v_made;
END $$;

-- ----------------------------------------------------------------------------- session results
-- Written when a session ends. `summary` holds the per-measure mean z-scores and
-- signal coverage; `assessment` is the rule engine output (findings, triage,
-- advice). Self-reported warning signs update `red_flags` and re-run the rules.
CREATE TABLE IF NOT EXISTS session_reports (
    session_id     uuid PRIMARY KEY REFERENCES diagnostic_sessions (id) ON DELETE CASCADE,
    patient_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    triage         text NOT NULL CHECK (triage IN ('EMERGENCY', 'SEE_DOCTOR', 'DISCUSS', 'NO_CHANGE', 'BASELINE', 'INSUFFICIENT')),
    summary        jsonb NOT NULL,
    assessment     jsonb NOT NULL,
    red_flags      text[] NOT NULL DEFAULT '{}',
    rules_version  text NOT NULL
);

CREATE INDEX IF NOT EXISTS session_reports_patient_idx ON session_reports (patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS session_reports_triage_idx ON session_reports (triage, created_at DESC)
    WHERE triage IN ('EMERGENCY', 'SEE_DOCTOR');

-- ----------------------------------------------------------------------------- care team notes
-- Clinician-written guidance shown to the patient. This is the only place
-- medication advice appears in Flaria; the software never generates it.
CREATE TABLE IF NOT EXISTS care_notes (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id  uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    author_id   uuid REFERENCES users (id) ON DELETE SET NULL,
    body        text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 4000),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS care_notes_patient_idx ON care_notes (patient_id, created_at DESC);

-- ----------------------------------------------------------------------------- audit trail
CREATE TABLE IF NOT EXISTS audit_events (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts         timestamptz NOT NULL DEFAULT now(),
    actor_id   uuid REFERENCES users (id) ON DELETE SET NULL,
    action     text NOT NULL CHECK (char_length(action) <= 64),
    target_id  uuid,
    ip         inet,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS audit_events_actor_idx ON audit_events (actor_id, ts DESC);
CREATE INDEX IF NOT EXISTS audit_events_ts_brin ON audit_events USING brin (ts);

-- ----------------------------------------------------------------------------- housekeeping
CREATE OR REPLACE FUNCTION flaria_purge_expired_refresh_tokens(p_keep_days integer DEFAULT 30)
RETURNS bigint
LANGUAGE sql AS $$
    WITH d AS (
        DELETE FROM refresh_tokens
        WHERE expires_at < now() - make_interval(days => p_keep_days)
        RETURNING 1
    )
    SELECT count(*) FROM d;
$$;
