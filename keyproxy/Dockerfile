# syntax=docker/dockerfile:1.7
#
# keyproxy — a small vendor-aware LLM key reverse proxy.
# Single-stage slim image: no compiler toolchain is needed (pure-Python deps).

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is used by the HEALTHCHECK only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user; /data is the only writable directory (the SQLite store).
RUN useradd --system --uid 1000 --shell /usr/sbin/nologin keyproxy \
 && mkdir -p /data \
 && chown -R keyproxy:keyproxy /data

WORKDIR /app

# Install deps first for layer caching, then the package source.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements.txt

# The package is copied into /app/keyproxy so `keyproxy.app:app` is importable.
COPY . /app/keyproxy

USER keyproxy
EXPOSE 8080

# DB lives on the persistent /data volume. TLS is terminated by the platform /
# reverse proxy in front of this container, asserted via KEYPROXY_TLS_TERMINATED.
ENV KEYPROXY_DB_PATH=/data/keyproxy.sqlite \
    KEYPROXY_TLS_TERMINATED=1

# Real vendor keys + the admin key are injected at run time as env vars, e.g.:
#   docker run -e KEYPROXY_ADMIN_KEY=... -e KEYPROXY_OPENAI_KEY=sk-... \
#              -e KEYPROXY_CLAUDE_API_KEY=sk-ant-... -p 8080:8080 keyproxy
# They are NEVER baked into the image.

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["python", "-m", "uvicorn", "keyproxy.app:app", \
     "--host", "0.0.0.0", "--port", "8080"]
