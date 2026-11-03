# Single-container image for hosted platforms (Render, Railway, Fly): the API also serves the web app.
# For your own server, docker-compose.yml keeps the two-container nginx + API setup.

# ---- 1. Web app ----------------------------------------------------------------------------
FROM node:20.18-alpine AS web
WORKDIR /web
COPY flaria-web/package.json flaria-web/package-lock.json ./
COPY flaria-web/scripts ./scripts
RUN npm ci --no-audit --no-fund
COPY flaria-web/ ./
# VITE_SITE_URL: the public landing page (canonical and share links). Render passes service env vars as build args.
ARG VITE_SITE_URL
ARG RENDER_EXTERNAL_URL
ARG VITE_DEMO_MODE=true
ENV VITE_SITE_URL=$VITE_SITE_URL RENDER_EXTERNAL_URL=$RENDER_EXTERNAL_URL VITE_DEMO_MODE=$VITE_DEMO_MODE
RUN npm run assets || true
RUN test -s public/mediapipe/face_landmarker.task || (echo "face model download failed" && exit 1)
RUN npm run build

# ---- 2. Analysis model: a contract-compatible stub unless a trained model is committed ------
FROM python:3.11-slim AS model
WORKDIR /m
RUN pip install --no-cache-dir onnx==1.17.0 numpy==1.26.4
COPY flaria-core/scripts/make_stub_model.py ./
COPY flaria-core/models/ ./models/
RUN test -f models/flaria_core.onnx || python make_stub_model.py --out models

# ---- 3. Runtime ------------------------------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NUMBA_CACHE_DIR=/tmp/numba FLARIA_WEB_DIST_PATH=/app/web
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY flaria-core/requirements.txt .
RUN pip install -r requirements.txt
COPY flaria-core/app ./app
COPY flaria-core/scripts ./scripts
COPY --from=model /m/models ./models
COPY --from=web /web/dist ./web
RUN useradd --system --uid 10001 --home /app flaria && chown -R flaria:flaria /app
USER flaria
EXPOSE 10000
# One worker: live sessions are stateful. Render routes traffic to $PORT.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips '*' --ws-max-size 65536 --no-server-header"]
