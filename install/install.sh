#!/usr/bin/env bash
# Personal Jarvis — macOS / Linux quick-install bootstrap (Stage 1)
#
# Usage (from any POSIX shell):
#   curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
#
# This bootstrap is intentionally small. It:
#   1. Verifies Python 3.11+ is available.
#   2. Verifies git is available.
#   3. Verifies Node.js 18+ is available (skipped on --headless).
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

# 24-bit brand palette (Charcoal + Gold), best-effort. On a real terminal we
# emit ANSI escapes so the banner sweeps gold and the ✓ ticks render; on a
# dumb terminal / pipe we omit them entirely.
if [ -t 1 ]; then
    GOLD=$(printf '\033[38;2;231;196;110m')
    GREEN=$(printf '\033[38;2;122;200;140m')
    DIM=$(printf '\033[38;2;140;140;140m')
    RED=$(printf '\033[38;2;224;122;110m')
    BOLD=$(printf '\033[1m')
    RST=$(printf '\033[0m')
else
    GOLD=""; GREEN=""; DIM=""; RED=""; BOLD=""; RST=""
fi

step() { printf '\n%s  ●%s %s%s%s\n' "$GOLD" "$RST" "$BOLD" "$1" "$RST"; }
ok()   { printf '%s    ✓%s %s%s%s\n' "$GREEN" "$RST" "$DIM" "$1" "$RST"; }
note() { printf '%s      %s%s\n' "$DIM" "$1" "$RST"; }
err()  { printf '%s    ✗ %s%s\n' "$RED" "$1" "$RST"; }

# Banner glyphs are machine-generated (figlet "ANSI Shadow"); do not hand-edit
# — that is how the historical "Harvis" typo crept in.
cat <<EOF

${GOLD}   P  E  R  S  O  N  A  L
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝${RST}

${GOLD}  ●${RST} ${BOLD}Quick install · macOS / Linux${RST}
EOF

# -------------------------------------------------------------- preflight
step 'Checking prerequisites'

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver=$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")
            if [ -n "$ver" ]; then
                major=${ver%.*}; minor=${ver#*.}
                if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; }; then
                    echo "$candidate"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

if ! PYTHON_EXE=$(find_python); then
    err 'Python 3.11+ not found.'
    note 'Install it from:'
    note '  - macOS:  https://www.python.org/downloads/ or "brew install python@3.12"'
    note '  - Linux:  your distro package (apt install python3.12, dnf install python3.12, ...)'
    exit 1
fi
PY_VER=$("$PYTHON_EXE" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "$PYTHON_EXE")
ok "$PY_VER"

if ! command -v git >/dev/null 2>&1; then
    err 'git not found.'
    note '  - macOS:  install Xcode CLT ("xcode-select --install") or "brew install git"'
    note '  - Linux:  your distro package (apt install git, ...)'
    exit 1
fi
ok 'git'

# Node.js 18+ — required for the Jarvis-Agent worker CLIs (Claude Code / Codex)
# the worker delegates heavy missions to, plus the Node-based marketplace
# integrations. Skipped on the headless / tiny-VPS path (--headless): a
# cloud-only base install that never spawns a local CLI worker, so Node adds no
# capability there.
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
    if [ "$node_ok" -eq 0 ]; then
        err 'Node.js 18+ not found.'
        note '  - macOS:  "brew install node" or https://nodejs.org/'
        note '  - Linux:  your distro package (apt install nodejs, ...) or https://nodejs.org/'
        exit 1
    fi
    ok "Node.js $(node --version)"
fi

# -------------------------------------------------------------- clone / update
step 'Fetching Personal Jarvis'
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

# -------------------------------------------------------------- venv
step 'Creating Python environment'

VENV_PATH="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_PATH/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    "$PYTHON_EXE" -m venv "$VENV_PATH"
fi
ok 'virtual environment ready'

# -------------------------------------------------------------- bootstrap deps
step 'Installing bootstrap dependencies'
note 'rich, packaging'
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet rich packaging
ok 'done'

# -------------------------------------------------------------- hand off
INSTALLER_PY="$INSTALL_DIR/install/installer.py"
if [ ! -f "$INSTALLER_PY" ]; then
    err "$INSTALLER_PY not found in the clone."
    note 'The repo seems incomplete. File a bug.'
    exit 1
fi

step 'Launching the guided installer…'

cd "$INSTALL_DIR"
exec "$VENV_PYTHON" "$INSTALLER_PY" "$@"
