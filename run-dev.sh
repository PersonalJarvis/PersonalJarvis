#!/usr/bin/env sh
# Personal Jarvis - DEV INSTANCE launcher (macOS + Linux). Parity twin of run-dev.bat.
#
# Starts a SECOND desktop app beside the regular one, from this same checkout:
# "Personal Jarvis Dev" - own data dir (data-dev/), own ports (+100), DEV-badged
# icon, no wake word / global hotkeys / chat channels / autostart (those stay
# with the regular app). Restart it as often as you like; the regular app and
# the coding sessions inside it are never touched.
#
# Usage: ./run-dev.sh [--debug|--headless]
# Same as: JARVIS_INSTANCE=dev ./run.sh
set -u
cd "$(dirname "$0")" || exit 1

if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . ".venv/bin/activate"
fi

if command -v python >/dev/null 2>&1; then
    PY=python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "error: no Python interpreter found on PATH" >&2
    exit 1
fi

case "${1:-}" in
    --debug)
        JARVIS_DEBUG=1 exec "$PY" -m jarvis.ui.web.launcher --instance dev
        ;;
    --headless)
        exec "$PY" -m jarvis.ui.web.launcher --instance dev --headless
        ;;
    *)
        exec "$PY" -m jarvis.ui.web.launcher --instance dev
        ;;
esac
