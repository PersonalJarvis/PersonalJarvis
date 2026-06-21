#!/usr/bin/env bash
#
# Personal Jarvis — one-command install (Linux / macOS).
#
#   git clone https://github.com/PersonalJarvis/PersonalJarvis
#   cd PersonalJarvis
#   ./install.sh
#
# Creates an isolated virtual environment and installs the cloud-first BASE:
# no GPU, no local models, no desktop/audio drivers required — it boots on a
# fresh machine (proven on python:3.11-slim). Desktop voice, local Whisper and
# the Orb overlay are opt-in extras you can add later with:
#
#   ./install.sh --desktop
#
# After install, start it with:   . .venv/bin/activate && jarvis
# Headless browser UI (no desktop needed):   jarvis --headless
#
# Requires Python 3.11, 3.12 or 3.13.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

EXTRAS=""
for arg in "$@"; do
  case "$arg" in
    --desktop) EXTRAS=".[desktop]" ;;
    --dev)     EXTRAS=".[dev]" ;;
    *) echo "Unknown option: $arg (use --desktop or --dev)" >&2; exit 2 ;;
  esac
done

# --- 1. Python 3.11+ ---------------------------------------------------------
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "✗ Python 3.11+ is required but '$PY' was not found on PATH." >&2
  echo "  Install Python 3.11–3.13, then re-run ./install.sh" >&2
  exit 1
fi
ver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$ver" in
  3.11|3.12|3.13) ;;
  *) echo "✗ Python 3.11–3.13 required (found $ver). Set PYTHON=/path/to/python3.11 and re-run." >&2; exit 1 ;;
esac
echo "→ Using Python $ver ($("$PY" -c 'import sys; print(sys.executable)'))"

# --- 2. isolated virtual environment ----------------------------------------
if [ ! -d .venv ]; then
  echo "→ Creating virtual environment (.venv)…"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --quiet --upgrade pip

# --- 3. install Personal Jarvis ---------------------------------------------
if [ -n "$EXTRAS" ]; then
  echo "→ Installing Personal Jarvis with extras ($EXTRAS)…"
  pip install --quiet "$EXTRAS"
else
  echo "→ Installing Personal Jarvis (cloud-first base — no GPU/desktop needed)…"
  pip install --quiet .
fi

# --- 4. done -----------------------------------------------------------------
cat <<'DONE'

✓ Personal Jarvis is installed.

  Start it:
      . .venv/bin/activate && jarvis

  No desktop? Use the browser UI (headless server):
      jarvis --headless

The first run walks you through ONE AI-provider key. Without a key it starts in
a zero-config demo mode (mock brain) so you see it running immediately.
DONE
