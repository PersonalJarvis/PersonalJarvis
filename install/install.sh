#!/usr/bin/env bash
# Personal Jarvis — macOS / Linux quick-install bootstrap (Stage 1)
#
# Usage (from any POSIX shell):
#   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
#
# This bootstrap is intentionally small. It:
#   1. Verifies Python 3.11+ and git are available.
#   2. Offers to install either missing prerequisite, then re-checks in place.
#   3. Checks for Node.js 18+ (optional - a missing Node never blocks the install).
#   4. Clones (or updates) personal-jarvis into ~/.personal-jarvis.
#   5. Creates a Python venv, installs `rich` + `packaging`.
#   6. Hands control to install/installer.py (the Stage 2 orchestrator).
#
# All heavy logic lives in installer.py so it can be unit-tested and
# kept cross-platform.

set -euo pipefail

REPO_URL="${JARVIS_INSTALL_REPO:-https://github.com/PersonalJarvis/PersonalJarvis.git}"
BRANCH="${JARVIS_INSTALL_REF:-main}"
INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/.personal-jarvis}"
PREREQUISITE_MODE="${JARVIS_INSTALL_PREREQS:-ask}"

# 24-bit brand palette (docs/BRAND.md): Signal Yellow on matte black, with the
# forged-gold wordmark gradient #FFE552 → #FFD60A → #B8960A. On a real terminal
# we emit ANSI escapes so the banner sweeps gold and the ✓ ticks render; on a
# dumb terminal / pipe we omit them entirely.
if [ -t 1 ]; then
    GOLD_HI=$(printf '\033[38;2;255;229;82m')
    GOLD=$(printf '\033[38;2;255;214;10m')
    GOLD_DEEP=$(printf '\033[38;2;184;150;10m')
    GREEN=$(printf '\033[38;2;122;200;140m')
    DIM=$(printf '\033[38;2;143;143;143m')
    RED=$(printf '\033[38;2;224;122;110m')
    BOLD=$(printf '\033[1m')
    RST=$(printf '\033[0m')
else
    GOLD_HI=""; GOLD=""; GOLD_DEEP=""; GREEN=""; DIM=""; RED=""; BOLD=""; RST=""
fi

# One six-phase journey spans BOTH installer stages: this shell owns phases
# 1-3, installer.py continues with 4-6 — keep the numbering in sync there.
phase() { printf '\n%s  %s%s %s%s%s\n' "$GOLD" "$1" "$RST" "$BOLD" "$2" "$RST"; }
ok()   { printf '%s    ✓%s %s%s%s\n' "$GREEN" "$RST" "$DIM" "$1" "$RST"; }
note() { printf '%s      %s%s\n' "$DIM" "$1" "$RST"; }
err()  { printf '%s    ✗ %s%s\n' "$RED" "$1" "$RST"; }

# Banner glyphs are machine-generated (figlet "ANSI Shadow"); do not hand-edit
# — that is how the historical "Harvis" typo crept in. Rows are colored as a
# vertical gradient (hi → brand → deep) to match the forged-gold wordmark.
cat <<EOF

${GOLD_HI}     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗${RST}
${GOLD_HI}     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝${RST}
${GOLD}     ██║███████║██████╔╝██║   ██║██║███████╗${RST}
${GOLD}██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║${RST}
${GOLD_DEEP}╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║${RST}
${GOLD_DEEP} ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝${RST}

${DIM}     P E R S O N A L  J A R V I S   ·   talk to your computer${RST}
${DIM}     Checks prerequisites · installs the full profile · launches when done${RST}
EOF

# -------------------------------------------------------------- welcome gate
# One clear question before anything touches the machine. Arrow keys (or
# y / n) choose, Enter confirms — nothing to type. `curl | bash` keeps stdin
# on the pipe, so the keys are read from /dev/tty; without a tty (CI,
# headless automation) the install proceeds — running the command was the
# explicit opt-in there. JARVIS_INSTALL_YES=1 skips the question entirely.
ask_welcome() {
    [ -n "${JARVIS_INSTALL_YES:-}" ] && return 0
    { [ -r /dev/tty ] && [ -w /dev/tty ]; } 2>/dev/null || return 0
    _sel=0  # 0 = yes, 1 = no
    while :; do
        if [ "$_sel" -eq 0 ]; then
            printf '\r  %sWould you like to install Personal Jarvis?%s   %s%s▸ Yes%s      No   ' \
                "$BOLD" "$RST" "$BOLD" "$GOLD" "$RST" > /dev/tty
        else
            printf '\r  %sWould you like to install Personal Jarvis?%s     Yes   %s%s▸ No%s   ' \
                "$BOLD" "$RST" "$BOLD" "$RED" "$RST" > /dev/tty
        fi
        IFS= read -rsn1 _key < /dev/tty || return 0
        case "$_key" in
            $'\x1b')
                IFS= read -rsn2 -t 1 _rest < /dev/tty || _rest=''
                case "$_rest" in
                    '[C'|'[B') _sel=1 ;;
                    '[D'|'[A') _sel=0 ;;
                esac
                ;;
            y|Y) _sel=0; break ;;
            n|N) _sel=1; break ;;
            '') break ;;  # Enter confirms the highlighted choice
        esac
    done
    printf '\n' > /dev/tty
    if [ "$_sel" -eq 1 ]; then
        note 'No problem - nothing was installed. Run the same command any time.'
        exit 0
    fi
}
ask_welcome

# -------------------------------------------------------------- preflight
phase '1/6' 'Prerequisites'

# --- python-detection begin -------------------------------------------------
# Covered by tests/unit/install/test_install_sh_python_detection.py.
#
# A bare PATH lookup is NOT enough on macOS: in a `curl | bash` session the
# profile files that put python.org / Homebrew interpreters on PATH are often
# never sourced, and Homebrew's versioned python@3.x kegs are keg-only (never
# linked onto PATH at all) — so we also probe the well-known install prefixes
# directly. We additionally remember the first too-old interpreter we saw, so
# a failure can say what WAS found: a field report read a bare "not found" as
# a false negative because `python3 --version` printed 3.8.2 (which reads
# "bigger" than 3.11 unless you know Python's version ordering).
#
# Escape hatches:
#   JARVIS_PYTHON              explicit interpreter; authoritative when set
#   JARVIS_PYTHON_SEARCH_DIRS  colon-separated dirs REPLACING the built-in
#                              off-PATH probe list (used by the tests)
PY_MIN_MINOR=11
PYTHON_EXE=""
FOUND_TOO_OLD=""

_py_try() {
    # Accept $1 if it runs and is Python >= 3.PY_MIN_MINOR: store its resolved
    # path in PYTHON_EXE and return 0. Otherwise remember the first too-old
    # version for the failure message and return 1.
    _exe="$1"
    _ver=$("$_exe" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || return 1
    case "$_ver" in ''|*[!0-9.]*) return 1 ;; esac
    _major=${_ver%%.*}
    _minor=${_ver#*.}; _minor=${_minor%%.*}
    if [ "$_major" -gt 3 ] || { [ "$_major" -eq 3 ] && [ "$_minor" -ge "$PY_MIN_MINOR" ]; }; then
        PYTHON_EXE=$(command -v "$_exe" 2>/dev/null || printf '%s' "$_exe")
        return 0
    fi
    if [ -z "$FOUND_TOO_OLD" ]; then
        _path=$(command -v "$_exe" 2>/dev/null || printf '%s' "$_exe")
        FOUND_TOO_OLD="Python $_ver at $_path"
    fi
    return 1
}

find_python() {
    if [ -n "${JARVIS_PYTHON:-}" ]; then
        # An explicit pin is authoritative: never silently substitute another
        # interpreter for the one the user asked for.
        _py_try "$JARVIS_PYTHON"
        return $?
    fi
    # 3.13/3.12/3.11 before 3.14 (BUG-059): the local-voice native stack
    # (av / ctranslate2 / onnxruntime) publishes no cp314 wheels yet, so a
    # 3.14 venv boots fine but cannot install the local speech pack. 3.14
    # stays as a working core fallback; move it forward once wheels exist.
    for candidate in python3.13 python3.12 python3.11 python3.14 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if _py_try "$candidate"; then return 0; fi
    done
    # Off-PATH probe: python.org framework installs and Homebrew prefixes
    # (Apple Silicon + Intel), including keg-only python@3.x kegs. Unmatched
    # globs stay literal and are filtered by the -x test; on Linux these
    # directories simply don't exist and the loop is a no-op.
    if [ -n "${JARVIS_PYTHON_SEARCH_DIRS:-}" ]; then
        # Colon-split without reading stdin, which still carries the piped
        # installer source in the public `curl | bash` path.
        _old_ifs=$IFS
        IFS=':'
        # shellcheck disable=SC2086 -- colon-splitting the override is the point
        set -- $JARVIS_PYTHON_SEARCH_DIRS
        IFS=$_old_ifs
        _probe_dirs=("$@")
    else
        _probe_dirs=(
            /opt/homebrew/bin
            /usr/local/bin
            /opt/homebrew/opt/python@3.*/bin
            /usr/local/opt/python@3.*/bin
            /Library/Frameworks/Python.framework/Versions/3.*/bin
        )
    fi
    for _dir in "${_probe_dirs[@]:-}"; do
        for _cand in "$_dir"/python3.1[1-9] "$_dir"/python3 "$_dir"/python; do
            [ -x "$_cand" ] || continue
            if _py_try "$_cand"; then return 0; fi
        done
    done
    return 1
}
# --- python-detection end ---------------------------------------------------

# --- prerequisite-bootstrap begin ------------------------------------------
# This block stays shell-native because Python may not exist yet. Tests extract
# it directly and exercise the retry/continuation state machine with fakes.
git_available() {
    _git_candidates=()
    _git_on_path=$(command -v git 2>/dev/null || true)
    if [ -n "$_git_on_path" ]; then _git_candidates+=("$_git_on_path"); fi
    _git_candidates+=(/opt/homebrew/bin/git /usr/local/bin/git /usr/bin/git)

    for _git_path in "${_git_candidates[@]}"; do
        [ -x "$_git_path" ] || continue
        # macOS ships a /usr/bin/git launcher even before the Command Line
        # Tools exist. Calling that launcher may open an unrelated system
        # dialog, so skip it and keep probing Homebrew's off-PATH locations.
        if [ "$(uname -s 2>/dev/null || true)" = 'Darwin' ] &&
           [ "$_git_path" = '/usr/bin/git' ] && command -v xcode-select >/dev/null 2>&1 &&
           ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        "$_git_path" --version >/dev/null 2>&1 || continue
        _git_dir=${_git_path%/*}
        case ":$PATH:" in
            *":$_git_dir:"*) ;;
            *) PATH="$_git_dir:$PATH"; export PATH ;;
        esac
        return 0
    done
    return 1
}

# --- full-support Python bootstrap (maintainer mandate 2026-07-14) -----------
# The one-liner must leave NOTHING to install afterwards: when the host only
# offers a Python the native local-voice wheels do not cover yet (3.14+),
# fetch a self-contained CPython 3.13 via uv (per-user, no sudo) and use it
# for the venv — the speech pack then installs during THIS run.
_PY_BOOTSTRAP_TRIED=0

_bootstrap_target_version() {
    # Intel Macs: the native voice stack's last x86_64 wheels end at cp312
    # (ctranslate2/av; onnxruntime dropped Intel macOS entirely, BUG-061),
    # so 3.12 is the newest FULLY supported Python there. Everywhere else
    # 3.13 has complete wheel coverage.
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ] && [ "$(uname -m 2>/dev/null)" = "x86_64" ]; then
        printf '3.12'
    else
        printf '3.13'
    fi
}

_py_full_support() {
    _mm=$("$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || return 1
    if [ "$(_bootstrap_target_version)" = "3.12" ]; then
        case "$_mm" in
            3.11|3.12) return 0 ;;
        esac
        return 1
    fi
    case "$_mm" in
        3.11|3.12|3.13) return 0 ;;
    esac
    return 1
}

bootstrap_full_support_python() {
    [ -n "${JARVIS_NO_PYTHON_BOOTSTRAP:-}" ] && return 1
    [ "$_PY_BOOTSTRAP_TRIED" -eq 1 ] && return 1
    _PY_BOOTSTRAP_TRIED=1
    _uv=$(command -v uv 2>/dev/null || true)
    [ -z "$_uv" ] && [ -x "$HOME/.local/bin/uv" ] && _uv="$HOME/.local/bin/uv"
    if [ -z "$_uv" ]; then
        note "downloading uv to fetch a self-contained Python $(_bootstrap_target_version) (per-user, no sudo)"
        curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || return 1
        _uv="$HOME/.local/bin/uv"
        [ -x "$_uv" ] || return 1
    fi
    _target=$(_bootstrap_target_version)
    "$_uv" python install "$_target" >/dev/null 2>&1 || return 1
    _bp=$("$_uv" python find "$_target" 2>/dev/null) || return 1
    [ -x "$_bp" ] || return 1
    PYTHON_EXE="$_bp"
    return 0
}

refresh_prerequisite_state() {
    PYTHON_EXE=""
    if find_python; then PYTHON_READY=1; else PYTHON_READY=0; fi
    # An explicit JARVIS_PYTHON pin is authoritative — never substituted.
    if [ "$PYTHON_READY" -eq 1 ] && [ -z "${JARVIS_PYTHON:-}" ] \
        && ! _py_full_support "$PYTHON_EXE"; then
        if bootstrap_full_support_python; then
            ok "fetched self-contained Python $(_bootstrap_target_version) (local voice fully supported)"
        else
            note 'no prebuilt local-voice packages for this Python yet - core works;'
            note 'the speech pack needs Python 3.11-3.13 (3.12 on Intel Macs).'
        fi
    fi
    if git_available; then GIT_READY=1; else GIT_READY=0; fi
    if [ "$PYTHON_READY" -eq 1 ] && [ "$GIT_READY" -eq 1 ]; then
        PREREQUISITES_READY=1
    else
        PREREQUISITES_READY=0
    fi
}

write_prerequisite_state() {
    _show_missing="${1:-0}"
    if [ "$PYTHON_READY" -eq 1 ]; then
        _py_ver=$("$PYTHON_EXE" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])' 2>/dev/null || printf '%s' "$PYTHON_EXE")
        ok "$_py_ver ($PYTHON_EXE)"
    elif [ "$_show_missing" -eq 1 ]; then
        err 'Python 3.11+ not found.'
        if [ -n "$FOUND_TOO_OLD" ]; then
            note "Closest match: $FOUND_TOO_OLD - too old: Jarvis needs 3.11+."
            note 'Python versions count 3.8 < 3.9 < 3.10 < 3.11.'
        fi
    fi
    if [ "$GIT_READY" -eq 1 ]; then
        _git_ver=$(git --version 2>/dev/null || printf 'git')
        ok "$_git_ver"
    elif [ "$_show_missing" -eq 1 ]; then
        err 'git not found.'
    fi
}

missing_prerequisite_labels() {
    _missing=""
    if [ "$PYTHON_READY" -eq 0 ]; then _missing='Python 3.11+'; fi
    if [ "$GIT_READY" -eq 0 ]; then
        if [ -n "$_missing" ]; then _missing="$_missing, Git"; else _missing='Git'; fi
    fi
    printf '%s' "$_missing"
}

has_install_tty() {
    { : </dev/tty; } 2>/dev/null && { : >/dev/tty; } 2>/dev/null
}

detect_prerequisite_manager() {
    PREREQ_MANAGER=""
    PREREQ_MANAGER_CMD=""
    case "$(uname -s 2>/dev/null || printf unknown)" in
        Darwin)
            for _brew in "$(command -v brew 2>/dev/null || true)" /opt/homebrew/bin/brew /usr/local/bin/brew; do
                if [ -n "$_brew" ] && [ -x "$_brew" ]; then
                    PREREQ_MANAGER='Homebrew'
                    PREREQ_MANAGER_CMD="$_brew"
                    return 0
                fi
            done
            ;;
        *)
            for _manager in apt-get dnf yum zypper pacman apk; do
                if command -v "$_manager" >/dev/null 2>&1; then
                    PREREQ_MANAGER="$_manager"
                    PREREQ_MANAGER_CMD="$_manager"
                    return 0
                fi
            done
            ;;
    esac
    return 1
}

request_prerequisite_consent() {
    _missing="$1"
    case "$PREREQUISITE_MODE" in
        auto)
            note 'Automatic prerequisite installation was enabled by JARVIS_INSTALL_PREREQS=auto.'
            return 0
            ;;
        never) return 1 ;;
        ask) ;;
        *)
            err "Invalid JARVIS_INSTALL_PREREQS value '$PREREQUISITE_MODE'. Use ask, auto, or never."
            return 1
            ;;
    esac
    if ! has_install_tty; then
        note 'This shell cannot ask for consent. Re-run interactively or set JARVIS_INSTALL_PREREQS=auto.'
        return 1
    fi

    note "Missing required software: $_missing."
    if [ -n "$PREREQ_MANAGER" ]; then
        note "Jarvis can install it with $PREREQ_MANAGER, wait, and continue this same run."
        note 'Continuing authorizes the package manager to accept the relevant package agreements.'
        _prompt='  Install the missing prerequisites now? [Y/n] '
    else
        note 'No supported package manager was found; Jarvis can keep this run open while you install it.'
        _prompt='  Show the manual path and keep checking this run? [Y/n] '
    fi
    printf '%s' "$_prompt" >/dev/tty
    IFS= read -r _answer </dev/tty || return 1
    case "$_answer" in ''|y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

run_privileged() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        note 'Administrator access is required, but sudo is unavailable.'
        return 126
    fi
}

install_with_prerequisite_manager() {
    _python_ready="$1"
    _git_ready="$2"
    _log=$(mktemp "${TMPDIR:-/tmp}/jarvis-prerequisites.XXXXXX") || return 1
    _result=1

    case "$PREREQ_MANAGER" in
        Homebrew)
            _packages=()
            if [ "$_python_ready" -eq 0 ]; then _packages+=(python); fi
            if [ "$_git_ready" -eq 0 ]; then _packages+=(git); fi
            if "$PREREQ_MANAGER_CMD" install "${_packages[@]}" >"$_log" 2>&1; then _result=0; fi
            ;;
        apt-get)
            _packages=()
            if [ "$_python_ready" -eq 0 ]; then _packages+=(python3 python3-venv); fi
            if [ "$_git_ready" -eq 0 ]; then _packages+=(git); fi
            if run_privileged env DEBIAN_FRONTEND=noninteractive apt-get update -qq >"$_log" 2>&1 &&
               run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${_packages[@]}" >>"$_log" 2>&1; then
                _result=0
            fi
            ;;
        dnf|yum)
            _packages=()
            if [ "$_python_ready" -eq 0 ]; then _packages+=(python3); fi
            if [ "$_git_ready" -eq 0 ]; then _packages+=(git); fi
            if run_privileged "$PREREQ_MANAGER_CMD" -q -y install "${_packages[@]}" >"$_log" 2>&1; then _result=0; fi
            ;;
        zypper)
            _packages=()
            if [ "$_python_ready" -eq 0 ]; then _packages+=(python3); fi
            if [ "$_git_ready" -eq 0 ]; then _packages+=(git); fi
            if run_privileged zypper --non-interactive --quiet install "${_packages[@]}" >"$_log" 2>&1; then _result=0; fi
            ;;
        pacman)
            _packages=()
            if [ "$_python_ready" -eq 0 ]; then _packages+=(python); fi
            if [ "$_git_ready" -eq 0 ]; then _packages+=(git); fi
            if run_privileged pacman -Sy --noconfirm --needed "${_packages[@]}" >"$_log" 2>&1; then _result=0; fi
            ;;
        apk)
            _packages=()
            if [ "$_python_ready" -eq 0 ]; then _packages+=(python3 py3-pip); fi
            if [ "$_git_ready" -eq 0 ]; then _packages+=(git); fi
            if run_privileged apk add --no-progress "${_packages[@]}" >"$_log" 2>&1; then _result=0; fi
            ;;
    esac

    if [ "$_result" -eq 0 ]; then
        ok "$PREREQ_MANAGER finished installing prerequisites"
    else
        err "$PREREQ_MANAGER did not complete the prerequisite installation."
        tail -n 12 "$_log" 2>/dev/null || true
    fi
    rm -f "$_log"
    return "$_result"
}

start_manual_prerequisite_path() {
    write_manual_prerequisite_help
    if [ "$PYTHON_READY" -eq 0 ] &&
       [ "$(uname -s 2>/dev/null || true)" = 'Darwin' ] && command -v open >/dev/null 2>&1; then
        open 'https://www.python.org/downloads/macos/' >/dev/null 2>&1 || true
    fi
    if [ "$GIT_READY" -eq 0 ] &&
       [ "$(uname -s 2>/dev/null || true)" = 'Darwin' ] && command -v xcode-select >/dev/null 2>&1; then
        xcode-select --install >/dev/null 2>&1 || true
    fi
}

write_manual_prerequisite_help() {
    if [ "$PYTHON_READY" -eq 0 ]; then
        note 'Python: https://www.python.org/downloads/'
        note 'Linux: install Python 3.11+ plus its venv package from your distribution.'
        note 'Already installed somewhere unusual? Pin it for this run:'
        note '  curl -fsSL <this url> | JARVIS_PYTHON=/path/to/python3.12 bash'
    fi
    if [ "$GIT_READY" -eq 0 ]; then
        note 'Git:    https://git-scm.com/downloads'
    fi
}

install_missing_prerequisites() {
    detect_prerequisite_manager || true
    if [ -z "$PREREQ_MANAGER" ]; then
        start_manual_prerequisite_path
        return 1
    fi
    note "installing prerequisites with $PREREQ_MANAGER"
    install_with_prerequisite_manager "$PYTHON_READY" "$GIT_READY"
}

wait_for_prerequisites() {
    _attempt=0
    while [ "$_attempt" -lt 5 ]; do
        hash -r
        refresh_prerequisite_state
        if [ "$PREREQUISITES_READY" -eq 1 ]; then return 0; fi
        _attempt=$((_attempt + 1))
        if [ "$_attempt" -lt 5 ]; then sleep 2; fi
    done
    return 1
}

ensure_prerequisites() {
    refresh_prerequisite_state
    write_prerequisite_state 1
    if [ "$PREREQUISITES_READY" -eq 1 ]; then return 0; fi
    if [ -n "${JARVIS_PYTHON:-}" ] && [ "$PYTHON_READY" -eq 0 ]; then
        note "JARVIS_PYTHON is pinned to '$JARVIS_PYTHON' and is not a compatible interpreter."
        note 'Update or unset that pin before prerequisite installation.'
        return 1
    fi

    detect_prerequisite_manager || true
    _missing=$(missing_prerequisite_labels)
    if ! request_prerequisite_consent "$_missing"; then
        write_manual_prerequisite_help
        note 'Nothing was installed. Run this command again after adding the prerequisites.'
        return 1
    fi

    install_missing_prerequisites || true
    wait_for_prerequisites || true

    while [ "$PREREQUISITES_READY" -eq 0 ]; do
        err 'The required commands are still unavailable in this terminal.'
        write_manual_prerequisite_help
        if [ "$PREREQUISITE_MODE" = 'auto' ] || ! has_install_tty; then return 1; fi
        printf '%s' '  Finish any manual installer, then press Enter to re-check; R retries, Q stops: ' >/dev/tty
        IFS= read -r _answer </dev/tty || return 1
        case "$_answer" in
            q|Q|quit|QUIT|Quit) return 1 ;;
            r|R|retry|RETRY|Retry) install_missing_prerequisites || true ;;
        esac
        wait_for_prerequisites || true
    done

    write_prerequisite_state 0
    return 0
}
# --- prerequisite-bootstrap end --------------------------------------------

if ! ensure_prerequisites; then
    err 'Prerequisite setup was not completed.'
    exit 1
fi

# Node.js 18+ — powers only the OPTIONAL Jarvis-Agent worker CLIs (Claude Code
# / Codex) that heavy missions delegate to, plus the Node-based marketplace
# integrations. Everything else in Jarvis runs without it, so a missing Node
# must NEVER turn a new user away at the door: we note it and continue — the
# worker CLI can be added later in-app once Node is installed. Skipped
# entirely on the headless / tiny-VPS path (--headless): a cloud-only base
# install that never spawns a local CLI worker.
skip_node=0
for arg in "$@"; do
    if [ "$arg" = "--headless" ]; then skip_node=1; break; fi
done
if [ "$skip_node" -eq 1 ]; then
    note 'Node.js check skipped (--headless): the cloud-only base install does not use it.'
else
    node_ok=0
    if command -v node >/dev/null 2>&1; then
        node_major=$(node --version 2>/dev/null | sed -E 's/^v?([0-9]+).*/\1/')
        if [ -n "$node_major" ] && [ "$node_major" -ge 18 ] 2>/dev/null; then
            node_ok=1
        fi
    fi
    if [ "$node_ok" -eq 1 ]; then
        ok "Node.js $(node --version)"
    else
        note 'Node.js 18+ not found - continuing, Jarvis runs fine without it.'
        note 'It only powers the optional coding-agent worker (Claude Code / Codex).'
        note 'Install it any time ("brew install node" / https://nodejs.org/) and'
        note 'add the worker later in-app.'
    fi
fi

# -------------------------------------------------------------- clone / update
phase '2/6' 'Fetching Personal Jarvis'
note "$INSTALL_DIR"

if [ -d "$INSTALL_DIR/.git" ]; then
    # --quiet keeps the noisy "Receiving objects: NN%" churn out of the clean
    # transcript; real errors still surface on stderr.
    git -C "$INSTALL_DIR" fetch --quiet --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
    git -C "$INSTALL_DIR" reset --quiet --hard "origin/$BRANCH"
    ok 'updated existing checkout to latest'
elif [ -e "$INSTALL_DIR" ]; then
    err "$INSTALL_DIR exists but is not a git repo."
    note 'Aborting to avoid clobbering your files. Remove or move that directory, then re-run.'
    exit 1
else
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    ok 'downloaded'
fi

# WAVE 5 — payload-commit pin (axis E, Wave-5 audit Finding 2).
#
# install-verify.sh exports JARVIS_PAYLOAD_COMMIT containing the signed
# payload commit SHA (Wave 1+2+4-authenticated). If set, we bind the
# cloned tree to that exact commit so an attacker who flips `main` post-
# release cannot influence what we install. The signed SHA may be 40-char
# (git SHA-1) or 64-char (git SHA-256 repos).
if [ -n "${JARVIS_PAYLOAD_COMMIT:-}" ]; then
    if ! printf '%s' "$JARVIS_PAYLOAD_COMMIT" | grep -Eq '^[0-9a-f]{40}([0-9a-f]{24})?$'; then
        err "JARVIS_PAYLOAD_COMMIT is not a well-formed git SHA: '$JARVIS_PAYLOAD_COMMIT' — refusing."
        exit 1
    fi
    # Shallow clones don't carry the full history; deepen to retrieve the
    # target SHA explicitly. `fetch <sha>` succeeds on most modern
    # github.com hosts (allowReachableSHA1InWant + uploadpack.allowAnySHA1InWant
    # are server defaults). Falls back to unshallow if direct-SHA fetch fails.
    if ! git -C "$INSTALL_DIR" fetch --quiet --depth 1 origin "$JARVIS_PAYLOAD_COMMIT" 2>/dev/null; then
        git -C "$INSTALL_DIR" fetch --quiet --unshallow origin || git -C "$INSTALL_DIR" fetch --quiet origin
    fi
    if ! git -C "$INSTALL_DIR" checkout --quiet --detach "$JARVIS_PAYLOAD_COMMIT"; then
        err "failed to checkout payload-commit ${JARVIS_PAYLOAD_COMMIT} — refusing."
        note 'the cloned tree does not contain the signed commit; release may be inconsistent.'
        exit 1
    fi
    # Defensive verify: assert HEAD is exactly the signed SHA byte-for-byte.
    ACTUAL_HEAD=$(git -C "$INSTALL_DIR" rev-parse HEAD)
    if [ "$ACTUAL_HEAD" != "$JARVIS_PAYLOAD_COMMIT" ]; then
        err "HEAD drift detected: pinned=${JARVIS_PAYLOAD_COMMIT}, actual=${ACTUAL_HEAD} — refusing."
        exit 1
    fi
    ok "pinned to signed commit ${JARVIS_PAYLOAD_COMMIT:0:12}…"
fi

# -------------------------------------------------------------- venv + bootstrap deps
phase '3/6' 'Python environment'

VENV_PATH="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_PATH/bin/python"

# Update runs: stop any Jarvis still running out of THIS install before we
# touch its environment. A live app (often revived by the login autostart)
# keeps serving stale, half-updated features while pip rewrites the venv
# under it — the "app is already open but nothing works yet" field report
# (2026-07-14). The installer relaunches a fresh instance as its last step.
if [ -x "$VENV_PYTHON" ]; then
    if pkill -f "$VENV_PATH" 2>/dev/null; then
        note 'stopped the running Jarvis app for the update'
    fi
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
        launchctl unload "$HOME/Library/LaunchAgents/com.personal-jarvis.autostart.plist" >/dev/null 2>&1 || true
    fi
fi

# Rebuild the venv when the selected interpreter's major.minor changed
# (BUG-059 follow-up): the 3.13-first preference is useless for an EXISTING
# install whose venv is pinned to 3.14 — a stale venv keeps the local-voice
# wheel gap forever. Packages are reinstalled by the installer right after,
# so dropping the env loses nothing.
if [ -x "$VENV_PYTHON" ]; then
    _venv_mm=$("$VENV_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || printf 'unknown')
    _sel_mm=$("$PYTHON_EXE" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || printf 'selected')
    if [ "$_venv_mm" != "$_sel_mm" ]; then
        note "rebuilding the Python environment (Python $_venv_mm -> $_sel_mm)"
        rm -rf "$VENV_PATH"
    fi
fi
if [ ! -x "$VENV_PYTHON" ]; then
    "$PYTHON_EXE" -m venv "$VENV_PATH"
fi
ok 'virtual environment ready'

note 'installing bootstrap dependencies (rich, packaging)'
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet rich packaging
ok 'bootstrap dependencies ready'

# -------------------------------------------------------------- hand off
INSTALLER_PY="$INSTALL_DIR/install/installer.py"
if [ ! -f "$INSTALLER_PY" ]; then
    err "$INSTALLER_PY not found in the clone."
    note 'The repo seems incomplete. File a bug.'
    exit 1
fi

note 'handing over to the Python installer (phases 4-6)'

cd "$INSTALL_DIR"
exec "$VENV_PYTHON" "$INSTALLER_PY" "$@"
