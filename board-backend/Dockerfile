# syntax=docker/dockerfile:1.7

# --- Build-Stage --------------------------------------------------------
FROM --platform=$BUILDPLATFORM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Wheels in eigenes Verzeichnis bauen, damit der Final-Image keine
# Compiler-Toolchain enthaelt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY board_backend ./board_backend

RUN python -m pip install --upgrade pip wheel \
 && python -m pip wheel --wheel-dir /wheels .


# --- Runtime-Stage ------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl wird vom HEALTHCHECK gebraucht.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root-User. /data ist das einzige writable-Verzeichnis.
RUN useradd --system --uid 1000 --shell /usr/sbin/nologin board \
 && mkdir -p /data \
 && chown -R board:board /data

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels jarvis-board-backend \
 && rm -rf /wheels

USER board
EXPOSE 8765

ENV JARVIS_BOARD_BIND_HOST=0.0.0.0 \
    JARVIS_BOARD_BIND_PORT=8765 \
    JARVIS_BOARD_DB_PATH=/data/board.db

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

CMD ["python", "-m", "uvicorn", "board_backend.main:app", \
     "--host", "0.0.0.0", "--port", "8765"]
