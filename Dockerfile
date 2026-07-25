# syntax=docker/dockerfile:1.7
# ⬢ ARASAKA // GAWAIN ENGINE — Dockerized
# Multi-stage build: frontend (Node) + backend (Python + MS ODBC)
# アラサカ — ドッカー化

# ──────────────────────────────────────────────────────────────
# Stage 1: Build frontend → static/
# ──────────────────────────────────────────────────────────────
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend

ENV CI=true

# Install deps from the lockfile — reproducible, and cached until the lockfile changes
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# Build (outDir is ../static per vite.config.ts → /app/static)
COPY frontend/ ./
RUN npm run build

# ──────────────────────────────────────────────────────────────
# Stage 2: Python backend + MS ODBC Driver 17/18
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS backend

# pipefail: without it, `curl ... | gpg` silently succeeds when curl 404s
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    ACCEPT_EULA=Y \
    PATH="/opt/mssql-tools18/bin:$PATH"

# System deps + Microsoft ODBC drivers in a single layer.
#
# The repo suite is derived from the base image's own Debian release, so this
# keeps working when python:3.11-slim rebases (bookworm → trixie → ...).
#
# Two signing keys are imported deliberately: .../debian/12 is signed by the old
# EB3E94ADBE1229CF key, but .../debian/13 (trixie) is signed by EE4D7792F748182B,
# which microsoft.asc does NOT contain. Importing both survives the rebase in
# either direction. Symptom if you only ship microsoft.asc on trixie:
#   "Missing key EE4D7792F748182B" / "The repository ... is not signed"
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        unixodbc \
    ; \
    . /etc/os-release; \
    { curl -fsSL https://packages.microsoft.com/keys/microsoft.asc; \
      curl -fsSL https://packages.microsoft.com/keys/microsoft-2025.asc; } \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg; \
    echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/${VERSION_ID}/prod ${VERSION_CODENAME} main" \
        > /etc/apt/sources.list.d/mssql-release.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends msodbcsql18 msodbcsql17 mssql-tools18; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/*; \
    odbcinst -q -d

WORKDIR /app

# Python deps — own layer so app code edits don't re-resolve the dependency tree
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

# Entrypoint first: it changes far less often than app code
COPY --chmod=0755 docker-entrypoint.sh /app/docker-entrypoint.sh

# Backend code
COPY main.py ./
COPY config/ ./config/
COPY server/ ./server/
COPY .env.example ./.env.example

# Built frontend from stage 1
COPY --from=frontend-builder /app/static ./static

RUN mkdir -p /app/storage

EXPOSE 8000

# Lenient on purpose: the UI must serve even when SQL Server / Ollama are offline
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=10 \
    CMD curl -fsS http://localhost:8000/ || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
