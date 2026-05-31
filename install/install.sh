#!/usr/bin/env bash
# Personal Jarvis — macOS / Linux quick-install bootstrap (Stage 1)
#
# Usage (from any POSIX shell):
#   curl -fsSL https://raw.githubusercontent.com/personal-jarvis/personal-jarvis/main/install/install.sh | bash
#
# This bootstrap is intentionally small. It:
#   1. Verifies Python 3.11+ is available.
#   2. Verifies git is available.
#   3. Clones (or updates) personal-jarvis into ~/.personal-jarvis.
#   4. Creates a Python venv, installs `rich` + `packaging`.
#   5. Hands control to install/installer.py (the Stage 2 orchestrator).
#
# All heavy logic lives in installer.py so it can be unit-tested and
# kept cross-platform.

set -euo pipefail

REPO_URL="${JARVIS_INSTALL_REPO:-https://github.com/personal-jarvis/personal-jarvis.git}"
BRANCH="${JARVIS_INSTALL_REF:-main}"
INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/.personal-jarvis}"

# ANSI colors (best-effort; safe to omit on dumb terminals)
if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); CYAN=$(printf '\033[36m')
    YELLOW=$(printf '\033[33m'); GREEN=$(printf '\033[32m')
    RED=$(printf '\033[31m'); RESET=$(printf '\033[0m')
else
    BOLD=""; CYAN=""; YELLOW=""; GREEN=""; RED=""; RESET=""
fi

cat <<EOF

${CYAN} ____                                  _   _                  _
|  _ \\ ___ _ __ ___  ___  _ __   __ _ | | | | __ _ _ ____   _(_)___
| |_) / _ \\ '__/ __|/ _ \\| '_ \\ / _\` || |_| |/ _\` | '__\\ \\ / / / __|
|  __/  __/ |  \\__ \\ (_) | | | | (_| ||  _  | (_| | |   \\ V /| \\__ \\
|_|   \\___|_|  |___/\\___/|_| |_|\\__,_||_| |_|\\__,_|_|    \\_/ |_|___/${RESET}

  ${BOLD}Quick install (macOS / Linux)${RESET}

EOF

# -------------------------------------------------------------- preflight
echo "${YELLOW}[1/5] Checking prerequisites...${RESET}"

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
    echo ""
    echo "${RED}  Python 3.11+ not found.${RESET}"
    echo "${RED}  Install it from:"
    echo "    - macOS:  https://www.python.org/downloads/ or 'brew install python@3.12'"
    echo "    - Linux:  use your distro package (apt install python3.12, dnf install python3.12, ...)"
    echo "${RESET}"
    exit 1
fi
echo "      Python OK (${GREEN}$PYTHON_EXE${RESET})"

if ! command -v git >/dev/null 2>&1; then
    echo ""
    echo "${RED}  git not found.${RESET}"
    echo "    - macOS:  install Xcode CLT ('xcode-select --install') or 'brew install git'"
    echo "    - Linux:  use your distro package (apt install git, ...)"
    exit 1
fi
echo "      ${GREEN}git OK${RESET}"

# -------------------------------------------------------------- clone / update
echo ""
echo "${YELLOW}[2/5] Preparing repo at $INSTALL_DIR ...${RESET}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "      existing checkout found — pulling latest..."
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
elif [ -e "$INSTALL_DIR" ]; then
    echo "${RED}  $INSTALL_DIR exists but is not a git repo. Aborting to avoid clobbering your files.${RESET}"
    echo "${RED}  Remove or move that directory, then re-run.${RESET}"
    exit 1
else
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
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
        echo "${RED}  JARVIS_PAYLOAD_COMMIT is not a well-formed git SHA: '$JARVIS_PAYLOAD_COMMIT' — refusing.${RESET}"
        exit 1
    fi
    echo "      pinning clone to signed commit ${JARVIS_PAYLOAD_COMMIT}..."
    # Shallow clones don't carry the full history; deepen to retrieve the
    # target SHA explicitly. `fetch <sha>` succeeds on most modern
    # github.com hosts (allowReachableSHA1InWant + uploadpack.allowAnySHA1InWant
    # are server defaults). Falls back to unshallow if direct-SHA fetch fails.
    if ! git -C "$INSTALL_DIR" fetch --depth 1 origin "$JARVIS_PAYLOAD_COMMIT" 2>/dev/null; then
        git -C "$INSTALL_DIR" fetch --unshallow origin || git -C "$INSTALL_DIR" fetch origin
    fi
    if ! git -C "$INSTALL_DIR" checkout --detach "$JARVIS_PAYLOAD_COMMIT"; then
        echo "${RED}  failed to checkout payload-commit ${JARVIS_PAYLOAD_COMMIT} — refusing.${RESET}"
        echo "${RED}  the cloned tree does not contain the signed commit; release may be inconsistent.${RESET}"
        exit 1
    fi
    # Defensive verify: assert HEAD is exactly the signed SHA byte-for-byte.
    ACTUAL_HEAD=$(git -C "$INSTALL_DIR" rev-parse HEAD)
    if [ "$ACTUAL_HEAD" != "$JARVIS_PAYLOAD_COMMIT" ]; then
        echo "${RED}  HEAD drift detected: pinned=${JARVIS_PAYLOAD_COMMIT}, actual=${ACTUAL_HEAD} — refusing.${RESET}"
        exit 1
    fi
    echo "      ${GREEN}clone pinned to ${JARVIS_PAYLOAD_COMMIT}${RESET}"
fi
echo "      ${GREEN}repo ready${RESET}"

# -------------------------------------------------------------- venv
echo ""
echo "${YELLOW}[3/5] Creating Python virtual environment...${RESET}"

VENV_PATH="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_PATH/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    "$PYTHON_EXE" -m venv "$VENV_PATH"
fi
echo "      ${GREEN}venv OK${RESET}"

# -------------------------------------------------------------- bootstrap deps
echo ""
echo "${YELLOW}[4/5] Installing bootstrap dependencies (rich, packaging)...${RESET}"
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet rich packaging
echo "      ${GREEN}bootstrap deps OK${RESET}"

# -------------------------------------------------------------- hand off
echo ""
echo "${YELLOW}[5/5] Handing off to the Python installer...${RESET}"
echo ""

INSTALLER_PY="$INSTALL_DIR/install/installer.py"
if [ ! -f "$INSTALLER_PY" ]; then
    echo "${RED}  $INSTALLER_PY not found in the clone.${RESET}"
    echo "${RED}  The repo seems incomplete. File a bug.${RESET}"
    exit 1
fi

cd "$INSTALL_DIR"
exec "$VENV_PYTHON" "$INSTALLER_PY" "$@"
