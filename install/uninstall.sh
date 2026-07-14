#!/usr/bin/env bash
# Personal Jarvis — macOS / Linux uninstaller
#
# Usage (from any POSIX shell), on a machine where Jarvis is installed:
#   bash ~/.personal-jarvis/install/uninstall.sh
#   bash ~/.personal-jarvis/install/uninstall.sh --dry-run   # preview only
#   bash ~/.personal-jarvis/install/uninstall.sh --yes       # no prompt
#
# It removes three things a plain folder-delete would miss:
#   1. the install folder (~/.personal-jarvis)
#   2. the login-autostart entry (~/.config/autostart or ~/Library/LaunchAgents)
#   3. the API keys saved in the OS keychain (service "personal-jarvis")
#
# Heavy logic lives in `python -m jarvis --uninstall` (cross-platform, tested).
# This bootstrap runs that for the autostart + keys (it asks for confirmation),
# then removes the folder itself from OUTSIDE the venv so nothing is locked.

set -euo pipefail

INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/.personal-jarvis}"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"

if [ -t 1 ]; then
    GOLD=$(printf '\033[38;2;255;214;10m'); GREEN=$(printf '\033[38;2;122;200;140m')
    DIM=$(printf '\033[38;2;143;143;143m');  RED=$(printf '\033[38;2;224;122;110m')
    BOLD=$(printf '\033[1m'); RST=$(printf '\033[0m')
else
    GOLD=""; GREEN=""; DIM=""; RED=""; BOLD=""; RST=""
fi
step() { printf '\n%s  ●%s %s%s%s\n' "$GOLD" "$RST" "$BOLD" "$1" "$RST"; }
ok()   { printf '%s    ✓%s %s%s%s\n' "$GREEN" "$RST" "$DIM" "$1" "$RST"; }
note() { printf '%s      %s%s\n' "$DIM" "$1" "$RST"; }
err()  { printf '%s    ✗ %s%s\n' "$RED" "$1" "$RST"; }

step 'Uninstall Personal Jarvis'
note "$INSTALL_DIR"

# Is this a dry run? Then never touch the folder.
DRY_RUN=0
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY_RUN=1; done

if [ ! -d "$INSTALL_DIR" ]; then
    err "No install found at $INSTALL_DIR — nothing to do."
    exit 0
fi

# 1 + 2 + 3: run the tested cleanup (autostart + keys), keeping the folder so we
#            can delete it ourselves below. --keep-folder means the venv is not
#            self-deleted while it is still running.
RC=0
if [ -x "$VENV_PYTHON" ]; then
    if "$VENV_PYTHON" -m jarvis --uninstall --keep-folder "$@"; then RC=0; else RC=$?; fi
else
    err "Python environment missing — skipping autostart/key cleanup."
    note "Removing the folder only; saved API keys may remain in your keychain."
    if [ "$DRY_RUN" -eq 1 ]; then exit 0; fi
    printf 'Type '\''yes'\'' to delete %s: ' "$INSTALL_DIR"
    read -r ans
    [ "$ans" = "yes" ] || { note 'Cancelled.'; exit 1; }
    RC=0
fi

# The Python step returns 1 when the user cancels at its prompt. Respect that.
if [ "$RC" -ne 0 ]; then
    note 'Cancelled — nothing was changed.'
    exit "$RC"
fi

# 1: delete the folder from OUTSIDE the venv (nothing is locked now).
if [ "$DRY_RUN" -eq 0 ]; then
    cd "$HOME"
    rm -rf "$INSTALL_DIR"
    ok "Removed $INSTALL_DIR"
    step 'Done. Personal Jarvis has been uninstalled.'
fi
