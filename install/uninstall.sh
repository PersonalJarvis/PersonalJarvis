#!/usr/bin/env bash
# Personal Jarvis — macOS / Linux uninstaller
#
# Usage (from any POSIX shell), on a machine where Jarvis is installed:
#   bash ~/.personal-jarvis/install/uninstall.sh
#   bash ~/.personal-jarvis/install/uninstall.sh --dry-run   # preview only
#   bash ~/.personal-jarvis/install/uninstall.sh --yes       # no prompt
#
# It removes four things a plain folder-delete would miss:
#   1. the install folder (~/.personal-jarvis)
#   2. the macOS app bundle or Linux application-menu entry
#   3. the login-autostart entry (~/.config/autostart or ~/Library/LaunchAgents)
#   4. the API keys saved in the OS keychain (service "personal-jarvis")
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

# Stop every process still executing out of the install dir — the app itself
# (tray, server, worker). On POSIX the delete would succeed anyway, but a
# survivor would keep running from a removed tree (holding its port and mic)
# until reboot. Never touches this shell or its parent. Best effort.
stop_running_instances() {
    root="$1"
    pids=""
    if [ -d /proc ]; then
        # Linux: match the real executable path behind each /proc entry.
        for exe in /proc/[0-9]*/exe; do
            pid="${exe#/proc/}"; pid="${pid%/exe}"
            [ "$pid" = "$$" ] && continue
            [ "$pid" = "${PPID:-0}" ] && continue
            target=$(readlink "$exe" 2>/dev/null) || continue
            case "$target" in ("$root"/*) pids="$pids $pid" ;; esac
        done
    else
        # macOS/BSD: ps reports the full executable path in comm.
        #
        # The leading "(" on the case pattern is REQUIRED, not style: bash 3.2
        # — the version macOS still ships — cannot parse a bare `pattern)` case
        # arm inside a $( ) command substitution. Its parser takes that ")" as
        # the end of the substitution and the whole FILE fails to parse:
        #   uninstall.sh: line 57: syntax error near unexpected token `;;'
        # so not one line of the uninstaller runs. The optional leading "(" is
        # POSIX and works on every shell we target. Both arms carry it so a
        # future move in or out of a substitution stays safe.
        pids=$(ps -axo pid=,comm= 2>/dev/null | while read -r pid comm; do
            [ "$pid" = "$$" ] && continue
            [ "$pid" = "${PPID:-0}" ] && continue
            case "$comm" in ("$root"/*) printf '%s ' "$pid" ;; esac
        done) || true
    fi
    pids=$(printf '%s' "$pids" | tr -s ' ')
    [ -n "${pids# }" ] || return 0

    # shellcheck disable=SC2086 — word-splitting the PID list is intended
    kill $pids 2>/dev/null || true
    alive=""
    for _ in 1 2 3 4 5 6; do
        alive=""
        for pid in $pids; do
            kill -0 "$pid" 2>/dev/null && alive="$alive $pid"
        done
        [ -z "$alive" ] && break
        sleep 1
    done
    if [ -n "$alive" ]; then
        # shellcheck disable=SC2086
        kill -9 $alive 2>/dev/null || true
        sleep 1
    fi
    n=$(echo "$pids" | wc -w | tr -d ' ')
    ok "Stopped the running Jarvis app ($n process(es))."
}

step 'Uninstall Personal Jarvis'
note "$INSTALL_DIR"

# Is this a dry run? Then never touch the folder. --yes/-y skips every prompt
# (required on a headless box where stdin is not a terminal).
DRY_RUN=0
ASSUME_YES=0
for a in "$@"; do
    [ "$a" = "--dry-run" ] && DRY_RUN=1
    { [ "$a" = "--yes" ] || [ "$a" = "-y" ]; } && ASSUME_YES=1
done

if [ ! -d "$INSTALL_DIR" ]; then
    err "No install found at $INSTALL_DIR — nothing to do."
    exit 0
fi

# 2 + 3 + 4: run the tested cleanup (app registration, autostart, and keys),
#            keeping the folder so we can delete it below. --keep-folder means
#            the venv is not self-deleted while it is still running.
RC=0
if [ -x "$VENV_PYTHON" ]; then
    if "$VENV_PYTHON" -m jarvis --uninstall --keep-folder "$@"; then RC=0; else RC=$?; fi
else
    err "Python environment missing — skipping autostart/key cleanup."
    note "The app registration and folder can still be removed; saved API keys may remain."
    if [ "$DRY_RUN" -eq 1 ]; then exit 0; fi
    if [ "$ASSUME_YES" -eq 0 ]; then
        printf 'Type '\''yes'\'' to delete %s: ' "$INSTALL_DIR"
        read -r ans
        [ "$ans" = "yes" ] || { note 'Cancelled.'; exit 1; }
    fi
    case "$(uname -s)" in
        Darwin)
            rm -rf -- "$HOME/Applications/Personal Jarvis.app"
            ;;
        Linux)
            rm -f -- "${XDG_DATA_HOME:-$HOME/.local/share}/applications/personal-jarvis.desktop"
            ;;
    esac
    ok 'Removed the desktop app registration.'
    RC=0
fi

# Map the Python step's exit code HONESTLY. It returns 1 when the user
# declines at its prompt and 2 when the folder is not a Jarvis install - it
# printed the reason itself in both cases. ANY OTHER code means it CRASHED,
# most often a venv whose interpreter stopped resolving after a system Python
# upgrade (on macOS/Homebrew: "dyld: Library not loaded: @rpath/Python3...").
# Reporting a crash as "cancelled" hides a real failure behind a user-choice
# message and leaves the install standing with no clue why - which is exactly
# how a broken uninstall reads as "it just does nothing".
if [ "$RC" -eq 1 ]; then
    note 'Cancelled — nothing was changed.'
    exit 1
elif [ "$RC" -eq 2 ]; then
    exit 2
elif [ "$RC" -ne 0 ]; then
    err "The cleanup step failed (exit $RC) — nothing was changed."
    note 'Its own error is printed above. On macOS the usual cause is a venv'
    note 'broken by a system/Homebrew Python upgrade.'
    note 'To remove Jarvis anyway:'
    note "  rm -rf \"$INSTALL_DIR\""
    note 'Saved API keys then stay in your OS keychain under "personal-jarvis".'
    exit "$RC"
fi

# 1: delete the folder from OUTSIDE the venv. The Python step above already
#    stops the running app; anything (re)started since - and the no-venv
#    fallback path - is caught here again. A failed delete says plainly WHY.
if [ "$DRY_RUN" -eq 0 ]; then
    cd "$HOME"
    stop_running_instances "$INSTALL_DIR"
    if ! rm -rf "$INSTALL_DIR"; then
        err "Could not fully remove $INSTALL_DIR — something is still using files inside it."
        note 'Close every Jarvis window, then run this uninstaller again.'
        exit 3
    fi
    ok "Removed $INSTALL_DIR"
    step 'Done. Personal Jarvis has been uninstalled.'
fi
