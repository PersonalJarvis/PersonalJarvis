#!/usr/bin/env bash
# Personal Jarvis — macOS / Linux quick-install bootstrap (Stage 1)
#
# Usage (from any POSIX shell):
#   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
#
# This bootstrap is intentionally small. It:
#   1. Verifies Python 3.11+ is available.
#   2. Verifies git is available.
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
${DIM}     Installs the full profile · asks nothing · launches when done${RST}
EOF

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
    for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if _py_try "$candidate"; then return 0; fi
    done
    # Off-PATH probe: python.org framework installs and Homebrew prefixes
    # (Apple Silicon + Intel), including keg-only python@3.x kegs. Unmatched
    # globs stay literal and are filtered by the -x test; on Linux these
    # directories simply don't exist and the loop is a no-op.
    if [ -n "${JARVIS_PYTHON_SEARCH_DIRS:-}" ]; then
        # Colon-split without `read` (the never-prompts guard bans that token).
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

if ! find_python; then
    err 'Python 3.11+ not found.'
    if [ -n "$FOUND_TOO_OLD" ]; then
        note "Closest match: $FOUND_TOO_OLD - too old: Jarvis needs 3.11+,"
        note 'and Python versions count 3.8 < 3.9 < 3.10 < 3.11.'
    fi
    note 'Install it from:'
    note '  - macOS:  https://www.python.org/downloads/ or "brew install python"'
    note '  - Linux:  your distro package (apt install python3.12, dnf install python3.12, ...)'
    note 'Then open a NEW terminal window and re-run this command.'
    note 'Python installed somewhere unusual? Pin it explicitly:'
    note '  curl -fsSL <this url> | JARVIS_PYTHON=/path/to/python3.12 bash'
    exit 1
fi
PY_VER=$("$PYTHON_EXE" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "$PYTHON_EXE")
ok "$PY_VER ($PYTHON_EXE)"

if ! command -v git >/dev/null 2>&1; then
    err 'git not found.'
    note '  - macOS:  install Xcode CLT ("xcode-select --install") or "brew install git"'
    note '  - Linux:  your distro package (apt install git, ...)'
    exit 1
fi
ok 'git'

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
