# Flaria

Privacy-first multimodal monitoring. Flaria compares a person's voice and facial movement with their own seven-day baseline and flags sustained shifts that can precede neurological or respiratory decline.

> **Intended use.** Flaria is monitoring software. It does not diagnose. The bundled training script uses synthetic data, so a model trained with it only demonstrates the pipeline. Before any clinical use, retrain on consented, labelled recordings under an approved protocol, run a locked clinical validation, and obtain the regulatory clearance that applies to software as a medical device in your market (for example FDA 510(k)/De Novo or EU MDR).

## Repository layout

```
flaria/
├── docker-compose.yml          PostgreSQL + API + nginx/app
├── flaria-core/                Python API, models, scripts
│   ├── app/
│   │   ├── main.py             FastAPI app, middleware, /ws/stream
│   │   ├── schemas.py
│   │   ├── core/               config, logging, security (Argon2id, JWT, RBAC)
│   │   ├── api/                auth.py, sessions.py (clinical + admin endpoints)
│   │   ├── database/           schema.sql, session.py (asyncpg), repositories.py
│   │   ├── ml/model.py         CrossAttentionFusion (PyTorch; training only)
│   │   └── services/           features_audio, features_vision, calibration,
│   │                           inference (ONNX Runtime), protocol, ring_buffer,
│   │                           stream_session, telemetry_writer
│   ├── scripts/
│   │   ├── train_and_export.py synthetic cohort -> training -> ONNX + metadata
│   │   ├── synthetic_patient.py stream a generated patient session (dev/demo data)
│   │   ├── live_hud.py         offline OpenCV/MediaPipe/sounddevice workstation
│   │   ├── create_admin.py     bootstrap or promote an ADMIN (prompt or FLARIA_ADMIN_PASSWORD)
│   │   ├── make_stub_model.py  contract-compatible ONNX graph for CI (not a model)
│   │   └── smoke_e2e.py        end-to-end test against a running stack
│   └── tests/                  unit tests (pytest)
├── tools/ui_screenshots/       make_media.py, capture.py, capture_admin.py (automated UI capture)
└── flaria-web/                 React 18 + TypeScript + Vite
    ├── public/worklets/pcm-capture.worklet.js   anti-aliased 16 kHz resampler
    ├── scripts/                copy-mediapipe.mjs, fetch-assets.mjs
    └── src/
        ├── store/              useAuthStore.ts, useTelemetryStore.ts
        ├── services/api.ts     axios clients + single-flight refresh on 401
        ├── hooks/              useFlariaWebSocket.ts, useAudioCapture.ts
        ├── components/         LiveCameraHUD, BiomarkerCharts, StatusConsole,
        │                       PrivacyBadge, LoginForm, ProtectedRoute, HeroTrace…
        └── pages/              Landing, AuthPage, Dashboard
```

## How a reading flows

1. **Browser.** MediaPipe Face Landmarker (WASM, self-hosted) turns each camera frame into 468 landmarks; the frame never leaves the device. An AudioWorklet low-passes and resamples the microphone to 16 kHz and emits 100 ms chunks. Both go over one WebSocket using a 20-byte binary header (`app/services/protocol.py`, `src/lib/protocol.ts`).
2. **Server memory.** PCM enters a 1 s float32 ring buffer. Every 100 ms the latest 500 ms window yields the 15-D acoustic vector (13 MFCC, spectral centroid, ZCR) plus f0, cycle-level jitter and shimmer, and autocorrelation HNR. Landmarks are reduced on arrival to the 5-D vector (lip-corner asymmetry about the nasal midline, inner-lip aperture, eye aspect ratio, pitch and yaw proxies) and the landmark array is zeroed.
3. **Calibration.** Each vector is z-scored against the patient's Welford baseline: the parallel merge of the last seven daily rows, or a 10 s in-session warm-up until 60 s of baseline exists. Only observations taken while the reading is stable are written back, so a developing shift is not absorbed into "normal".
4. **Fusion.** `CrossAttentionFusion` projects both modalities to 32-D; visual embeddings are queries over acoustic keys and values. The head sees the fused, attention-pooled state and the pooled residual `|v − ctx|`, which is the incoordination signal. ONNX Runtime runs it at the tick rate (default 30 Hz) on CUDA when available, otherwise CPU.
5. **Status.** An EMA (τ = 0.5 s) feeds a hysteretic state machine: Watch after 1.5 s above threshold, Elevated after 3 s, release after 2 s below threshold minus 0.05. Missing modalities are fed as neutral z = 0 and reported as Signal lost, so absent data never raises the index.
6. **Persistence.** 1 Hz aggregates go to a batched COPY writer with backoff and a bounded queue. Telemetry is range-partitioned by month with BRIN and B-tree indexes.

## Results after each session

When a session ends the server writes a result (`app/services/findings.py`, table `session_reports`):

- **What changed:** each measure's session-average z-score against the personal baseline, translated into plain findings ("Your voice is rougher or breathier than usual"), with the measures behind it.
- **Possible causes:** for each finding, the common explanations a clinician would consider, harmless ones included, labelled as possibilities rather than a diagnosis. Voice and face measurements can't tell a stroke from Bell's palsy, or a chest infection from asthma, so the app never names one cause.
- **What to do:** one of six levels: Emergency, See a doctor within 24 hours, Check again tomorrow, No change, Building baseline, or Not enough signal. Strong facial asymmetry combined with a speech or voice change gives "Possible stroke warning signs" with emergency instructions. A warning-symptom checklist (face droop, limb weakness, slurred speech, severe breathlessness, chest pain and so on) always escalates to Emergency, whatever the measurements say.
- **Medicines:** the app doesn't suggest any. Choosing a medicine depends on history, allergies and other medicines, and some reasonable-sounding guesses are dangerous; aspirin during a bleeding stroke is one example. The result instead shows notes from the patient's care team (`care_notes`): only an assigned clinician or an admin can write them, and patients can't.

## Search engines and sharing

What the build produces (`npm run build` runs `scripts/prerender.mjs` after Vite):

- **Prerendered landing page.** `/` ships as complete HTML, so crawlers and link previews see the full content without running JavaScript; React then hydrates it in place. The headline contains the brand name.
- **Head metadata:** a brand-first title, meta description, canonical URL, robots directives, Open Graph and Twitter cards with a 1200×630 share image, icons (SVG, 48 px, 192 px, 512 px, Apple touch) and a web manifest.
- **Structured data** (JSON-LD): Organization (with `sameAs` profiles), WebSite (which tells Google the site name to display) and WebApplication (HealthApplication). No ratings or reviews are invented.
- **Crawler files:** `sitemap.xml` and `robots.txt` built from your domain.
- **Private routes** (sign-in, dashboard, admin, account, reset links) are served from a separate `app.html` shell with `noindex` in both a meta tag and an `X-Robots-Tag` header, so search results only ever show the landing page.
- **Session hint cookie:** anonymous visitors, crawlers included, make no API calls on load.

Lighthouse on the built landing page: SEO 100, Accessibility 100, Best Practices 96 (the only miss was images the test sandbox couldn't download), Performance 87.

Build settings: `VITE_SITE_URL` (required, your https origin), plus the optional `FLARIA_SAME_AS` (official profile URLs, comma-separated), `FLARIA_GOOGLE_SITE_VERIFICATION` and `FLARIA_BING_SITE_VERIFICATION`. The Docker image takes them as `--build-arg`. Regenerate the share image and icons with `python3 tools/ui_screenshots/brand_assets.py`.

After deploying, ranking depends on things outside the code:

1. Serve the site over https on its permanent domain. A domain containing the name helps most for brand searches.
2. Add the site to Google Search Console and Bing Webmaster Tools (Bing also feeds DuckDuckGo and Yahoo), submit `/sitemap.xml`, and request indexing of `/`.
3. Link to the site, using the name "Flaria", from the places you control: your portfolio, GitHub, LinkedIn and any write-ups. List those profiles in `FLARIA_SAME_AS`.

## Admin console

`/admin` (ADMIN accounts only; admins land there after signing in). Every section is URL-addressable and every change is audited.

- **Overview:** patients, clinicians, sessions, live streams, urgent results awaiting follow-up and locked accounts; session results per day for 14 days; the follow-up queue; service health.
- **Users:** search and filter by role and status. Change roles (with confirmation; leaving the clinician role clears care-team links), disable or re-enable accounts, unlock after failed sign-ins, and sign a user out of every device. Admins can't demote or disable themselves.
- **Care teams:** assign and remove clinician–patient links, with a warning listing patients who have no clinician.
- **Escalations:** every Emergency and See-a-doctor result, with reported symptoms, the patient's care team, and whether a clinician has followed up with a care note.
- **Audit log:** filter by type and person, with paging into older events.
- **System:** model and execution provider, database size and partitions, telemetry writer health, and streaming, privacy and sign-in security settings.

Create the first admin with `python scripts/create_admin.py admin@example.org "Display Name"`. For automated provisioning, set `FLARIA_ADMIN_PASSWORD` in the environment instead of answering the prompt; the variable is read once and removed.

## Passwords

- **Change password** (`/account`): needs the current password. Wrong attempts count toward the account lockout. Every other device is signed out, this one gets a fresh session, and a confirmation email is sent.
- **Forgot password** (`/forgot-password`):
  - The response is identical whether or not the email has an account, and the email is sent after the response, so neither the message nor the timing reveals who has an account.
  - The link carries a 256-bit single-use token in the URL fragment, which browsers never send to servers. The page strips it from the address bar on arrival.
  - Only a SHA-256 hash of the token is stored. Links expire after 30 minutes, a newer link cancels older ones, and there are at most 3 per account per hour.
  - Completing a reset clears any lockout, signs out every device and sends a confirmation email.
- **Admins** can email a user a reset link from the Users page. They never see or set another person's password.
- **Email delivery** uses SMTP with STARTTLS or implicit TLS and certificate verification. In production the server refuses to start unless `FLARIA_MAIL_BACKEND=smtp` and `FLARIA_PUBLIC_APP_URL` is https. The development `console` backend logs messages instead of sending them.

## Privacy model

| Data | Where it exists | Lifetime |
|---|---|---|
| Camera frames | Browser only | One video frame; a 32×24 luminance thumbnail once per second for the low-light hint |
| Face landmarks | Browser → server RAM | Zeroed immediately after the 5-D reduction |
| Audio PCM | Server RAM ring (1 s, 64 KiB) | Overwritten continuously; zero-filled at session end |
| 15-D + 5-D vectors, voice-quality scalars, anomaly | PostgreSQL | 1-second means only |

Two caveats are worth stating plainly. Per-frame MFCC sequences can be partially inverted into intelligible speech, which is why only 1-second means are stored. And Python `bytes` objects received from the socket are immutable and can't be wiped; they are released immediately but remain in freed memory until reused. If your threat model includes memory forensics on the API host, run it on encrypted, swap-disabled hosts.

## Security model

- **Passwords:** Argon2id (64 MiB, t=3, p=4) with transparent re-hashing; constant-time behaviour for unknown e-mails; per-account lockout after 5 failures.
- **Access tokens:** 15-minute JWTs held in memory only (never localStorage) and checked against the live user row on every request, so a role change or logout-all revokes them at once.
- **Refresh tokens:** 7-day JWTs in an HttpOnly, Secure, SameSite=Strict cookie scoped to `/api/v1/auth`. They rotate on every use. A replayed token revokes its whole family; a 10 s grace window absorbs benign two-tab races, and the client serialises refreshes across tabs with the Web Locks API.
- **WebSocket:** the token travels in the `bearer.<jwt>` subprotocol (a `?token=` fallback exists), never in URLs by default. The server checks Origin before upgrade, re-validates `reauth` messages mid-stream and closes with 4401 when a token lapses. Message size, rate and protocol-error limits close abusive streams.
- **RBAC:** PATIENT sees their own data; CLINICIAN sees patients assigned through `care_team`; ADMIN manages roles and assignments. Every auth, session and admin action is written to `audit_events`.
- **Headers:** CSP (with `wasm-unsafe-eval` for MediaPipe), `frame-ancestors 'none'`, nosniff, no-referrer, Permissions-Policy limited to camera and microphone on self.

## Development

Requirements: Python 3.11, Node 20.11+, PostgreSQL 14+ (Supabase works).

```bash
# API
cd flaria-core
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export FLARIA_DATABASE_URL=postgresql://flaria:flaria@localhost:5432/flaria
export FLARIA_COOKIE_SECURE=false            # plain-http dev only
pytest -q
uvicorn app.main:app --reload

# Model: train for real (needs requirements-ml.txt), or a stub just to exercise the stack
pip install -r requirements-ml.txt && python scripts/train_and_export.py --out models/
#   or: pip install onnx==1.17.0 && python scripts/make_stub_model.py --out models/

# Web
cd ../flaria-web
npm install            # also copies the MediaPipe WASM runtime into public/
npm run assets         # face model + reference images (needs network once)
npm run dev            # http://localhost:5173, proxies /api and /ws to :8000
```

Bootstrap the first administrator with `python scripts/create_admin.py admin@example.org "Your Name"`. Public registration only ever creates patient accounts.

## Deployment

**Free hosting on Render (no domain needed):** follow [DEPLOY-RENDER.md](DEPLOY-RENDER.md). `render.yaml` defines the landing page as a static site and the app as a single-container web service (root `Dockerfile`), with Neon for the database and Brevo for email.

**Your own server:**

```bash
cp .env.example .env                              # POSTGRES_PASSWORD
cp flaria-core/.env.example flaria-core/.env      # JWT secret, origins, hosts
# put flaria_core.onnx + flaria_core.meta.json in flaria-core/models/
docker compose up --build                         # app on :8080; put TLS in front
```

The API refuses to start in production with a weak JWT secret, non-Secure cookies or wildcard CORS. It also refuses a model whose SHA-256 doesn't match its metadata. Use one Uvicorn worker per container (sessions are stateful) and sticky routing for `/ws/` when scaling out. Schedule `SELECT flaria_ensure_telemetry_partitions(3);` monthly and `SELECT flaria_purge_expired_refresh_tokens();` daily.

## Verification record

Done during development:

- **Backend unit tests:** `pytest`, 23 passing. Covers Argon2id and JWT handling, jitter, shimmer, HNR and f0 on synthetic glottal pulse trains, lip-asymmetry geometry, exact Welford/Chan merging, protocol parsing, ring buffers and risk hysteresis.
- **End to end** (`scripts/smoke_e2e.py` against PostgreSQL 16 and the stub model), run both directly and through the Vite dev proxy. It covers registration, refresh rotation, reuse detection revoking the family, WebSocket 4401 rejection, 18 s of synthetic voice and landmark streaming through calibration and inference, f0 tracked at 135 Hz, 1 Hz rows persisted, baseline write-back and a 403 on cross-patient access. Server tick processing measured p50 0.14 ms and p99 2.3 ms.
- **Cross-language protocol checks:** TypeScript-encoded audio and landmark frames parse in Python, and Python binary ticks decode in TypeScript.
- **Audio worklet:** for 48 kHz to 16 kHz, a 1 kHz tone comes out at the correct amplitude, and a 20 kHz tone is suppressed rather than aliasing to 4 kHz.
- **Frontend:** `tsc` strict type-check and the production build both pass. The landing bundle excludes Chart.js and MediaPipe.

- **UI capture** (`tools/ui_screenshots/capture.py`): the production build was driven in headless Chromium with fake camera and microphone devices (NASA's public-domain astronaut portrait plus synthetic voices) through the full pipeline. That includes in-browser face mesh, AudioWorklet, WebSocket, calibration, model and results, plus a clinician account and a second synthetic patient. 27 screens were captured.
- **Slow devices:** that run exposed real weaknesses on hardware where face detection takes hundreds of milliseconds, which were then fixed. The HUD now adapts its detection rate to stay under 40% of the main thread and tells the patient when tracking is slow. The server sizes its face-staleness window from the measured frame interval, and calibration sample counts assume 8 frames per second rather than 30.

Not yet verified (do these before go-live):

- PyTorch training and ONNX export were written against torch 2.5.1 but not executed in the build environment.
- The web UI has not been exercised in a real browser with a camera and microphone. Neither has the CSP against the MediaPipe runtime: if the console reports a `script-src` violation from the WASM loader, adjust `security-headers.conf`.
- Password flows were verified end to end (`scripts/smoke_passwords.py`, 26 checks) with real SMTP delivery into a capturing server. Those checks cover revocation of other devices, single-use and superseded links, the hourly limit, identical answers for unknown emails, and no passwords in emails. Delivery through a production mail provider (SPF/DKIM, deliverability) is untested.
- `npm run assets` was not run (no network access to Google Storage, Wikimedia or Unsplash in the build environment). The URLs and licences were checked by hand.
- The Docker images were written but not built.

## Image credits

- Henry Gray, *Anatomy of the Human Body* (1918), plates 378, 956 and 960, public domain, via Wikimedia Commons.
- Clinicians photograph by Vitaly Gariev on Unsplash (Unsplash License).
- Face Landmarker model © Google, Apache-2.0.
