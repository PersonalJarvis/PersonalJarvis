#!/usr/bin/env bash
# Personal Jarvis — production build for macOS and Linux.
# Mirror of build.bat (Windows). Two steps:
#   1. Frontend bundle via Vite  -> jarvis/ui/web/dist/
#   2. PyInstaller via jarvis.spec -> dist/Jarvis/ (onedir) and, on macOS,
#      dist/Jarvis.app (the BUNDLE step in jarvis.spec).
#
# The native installer is then produced from this bundle by the recipes under
# packaging/ (macOS .dmg, Linux AppImage). See .github/workflows/build-app.yml
# for the per-OS CI matrix that runs this on real macOS/Linux runners.
set -euo pipefail

cd "$(dirname "$0")"

# Use the project venv if present, otherwise the active interpreter.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi
echo "[build] using interpreter: $PY"

echo "[build] 1/2 frontend"
pushd jarvis/ui/web/frontend >/dev/null
if [ ! -d node_modules ]; then
    echo "[build] npm ci..."
    npm ci
fi
npm run build
popd >/dev/null

echo "[build] 2/2 PyInstaller"
if [ ! -f jarvis.spec ]; then
    echo "[build] jarvis.spec missing — aborting." >&2
    exit 1
fi
"$PY" -m PyInstaller jarvis.spec --noconfirm --clean

if [ "$(uname -s)" = "Darwin" ]; then
    echo "[build] done. Bundle: dist/Jarvis.app"
else
    echo "[build] done. Bundle: dist/Jarvis/"
fi
