# ⬢ ARASAKA // GAWAIN ENGINE — Dockerized
# Multi-stage build: frontend (Node) + backend (Python + ODBC 17)
# アラサカ — ドッカー化

# ──────────────────────────────────────────────────────────────
# Stage 1: Build frontend → static/
# ──────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

# Install deps
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --silent

# Build
COPY frontend/ ./
RUN npm run build
# Output goes to ../static per vite.config.ts → /app/static

# ──────────────────────────────────────────────────────────────
# Stage 2: Python backend + ODBC Driver 17
# ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install system deps + ODBC Driver 17 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    apt-transport-https \
    unixodbc \
    unixodbc-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Microsoft repo for msodbcsql17 (Debian 12 / bookworm)
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list | tee /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 mssql-tools18 \
    && echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' >> ~/.bashrc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy backend code
COPY main.py ./
COPY config/ ./config/
COPY server/ ./server/
COPY storage/.gitkeep ./storage/.gitkeep
COPY .env.example ./.env.example

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/static ./static

# Create storage dir and set perms
RUN mkdir -p /app/storage && chmod 777 /app/storage

# Entrypoint script (separate file for compatibility with older Docker)
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

# Healthcheck — lenient, just checks frontend serving (not DB) — DB may be offline for demo/product
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=10 \
  CMD curl -f http://localhost:8000/ || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
