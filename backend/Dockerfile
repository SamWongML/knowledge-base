# syntax=docker/dockerfile:1.7
#
# kb application image: FastAPI API (`kb-api`) and ingestion worker (`kb-worker`) plus the
# built React SPA, served by the API from $KB_STATIC_DIR.
#
# Build context is the REPO ROOT:
#   docker build -f backend/Dockerfile -t kb:local .
#
# Model servers (llama.cpp, Ollama) are NOT in this image: on macOS they must run natively to
# use Metal. The containers reach them via host.docker.internal.

ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=22
ARG UV_VERSION=0.11.32

# --- uv binary ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --- frontend: node + pnpm -> /src/frontend/dist ----------------------------------------
# Built on the build platform: the output is plain static files, so no emulation is needed.
FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-bookworm-slim AS frontend
ENV PNPM_HOME=/pnpm \
    CI=1
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable
WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store \
 && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# --- backend: resolve and install into /opt/venv ---------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
# Dependencies first (cached layer), then the project itself as a non-editable wheel.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project --extra parsing
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra parsing

# --- runtime ------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime
LABEL org.opencontainers.image.title="kb" \
      org.opencontainers.image.description="Self-hosted hybrid-search knowledge base (API + worker + SPA)" \
      org.opencontainers.image.source="https://github.com/senwong/knowledge-base"

RUN groupadd --system --gid 10001 kb \
 && useradd --system --uid 10001 --gid kb --home-dir /app --no-create-home --shell /usr/sbin/nologin kb \
 && mkdir -p /app /data \
 && chown kb:kb /app /data

COPY --from=builder /opt/venv /opt/venv
COPY --from=frontend /src/frontend/dist /app/static

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KB_ENV=prod \
    KB_HOST=0.0.0.0 \
    KB_PORT=8787 \
    KB_STATIC_DIR=/app/static \
    KB_LOG_JSON=true

WORKDIR /app
USER kb:kb
EXPOSE 8787

# Liveness for the API. /api/health answers 200 when the DB is reachable (model servers being
# down only degrades it). The worker service disables this in compose.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('KB_PORT', '8787'), timeout=4)"]

# `kb-api` (default) or `kb-worker`.
CMD ["kb-api"]
