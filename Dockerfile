# syntax=docker/dockerfile:1.7
#
# Personal Jarvis — headless server image (the cloud-first / VPS path).
#
# Boots the FastAPI + WebSocket backend and serves the prebuilt browser UI, so a
# user reaches the full Router-Brain experience through any browser without a
# desktop install. NO desktop extras (tray, overlay, hotkey, local voice) — those
# are opt-in and useless in a headless container (see CLAUDE.md, cloud-first
# doctrine). Bring your own provider keys via environment variables.
#
#   docker build -t personal-jarvis .
#   docker run --rm -p 127.0.0.1:8000:8000 \
#       -v jarvis-data:/app/data \
#       -e JARVIS_CONTROL_API_KEY=jctl_change_me \
#       -e ANTHROPIC_API_KEY=sk-ant-... \
#       personal-jarvis
#   # then open http://localhost:8000
#
# Or use the bundled docker-compose.yml.

# --- Stage 1: build the React UI ---------------------------------------------
# The frontend source lives in the repo; build it once here so the runtime image
# carries a ready-to-serve dist/ and needs no Node.
# Mirror the repo layout (frontend dir with a sibling dist/) so vite's
# `--outDir ../dist` resolves to /web/dist, not a surprising /dist.
FROM node:22-slim AS web
WORKDIR /web/frontend
COPY jarvis/ui/web/frontend/package.json jarvis/ui/web/frontend/package-lock.json ./
RUN npm ci
COPY jarvis/ui/web/frontend/ ./
# vite build writes to ../dist relative to the frontend dir → /web/dist here.
RUN npm run build

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential/libffi: a few base deps build from sdist on slim.
# libportaudio2: backs sounddevice (a lazy-imported base dep) so any audio path
# imports cleanly even though the container has no audio hardware.
# curl: the HEALTHCHECK below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential libffi-dev libportaudio2 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the base package only (the headless VPS contract — no [desktop] /
# [local-voice] extras). An editable install keeps PROJECT_ROOT == /app, so the
# runtime data dir resolves to the writable /app/data volume below rather than
# into read-only site-packages.
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
COPY --from=web /web/dist ./jarvis/ui/web/dist
RUN python -m pip install --upgrade pip \
 && python -m pip install -e .

# Non-root. /app/data is the only writable location at runtime.
RUN useradd --system --uid 1000 --shell /usr/sbin/nologin jarvis \
 && mkdir -p /app/data \
 && chown -R jarvis:jarvis /app/data
USER jarvis

EXPOSE 8000

# JARVIS_BIND_HOST=0.0.0.0 makes the headless listener reachable through the
# published port (the control key is the security boundary — see compose).
# JARVIS_NONINTERACTIVE skips the first-run wizard; keys come from the env.
# Config and runtime data both live in the sole writable, persisted directory;
# the application tree remains read-only to the non-root runtime user.
ENV JARVIS_BIND_HOST=0.0.0.0 \
    JARVIS_NONINTERACTIVE=1 \
    JARVIS_CONFIG=/app/data/jarvis.toml \
    JARVIS_DATA_DIR=/app/data

# Generous start-period: first boot builds the brain in the background.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "-m", "jarvis.ui.web.launcher", "--headless", "--port", "8000"]
