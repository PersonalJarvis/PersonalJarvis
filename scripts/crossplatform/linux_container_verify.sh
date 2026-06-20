#!/usr/bin/env bash
# Verify the cloud-first BASE install + the B2 / headless / logic layer on REAL
# Linux, inside a throwaway `python:3.11-slim` Docker container — no CI, no Linux
# hardware required. Run from anywhere in the repo:
#
#     bash scripts/crossplatform/linux_container_verify.sh
#
# The host working tree is NEVER mounted or modified: the source subset is piped
# in via `tar` over stdin and installed inside the container only. This proves the
# HEADLESS / IMPORT / LOGIC layer on real Linux (base-install boot with the
# [desktop] extras absent, sounddevice-absent degrade paths, the import-clean
# gate, the B2 backend). It does NOT verify Linux GUI/permission behaviour
# (AX/AT-SPI tree, Orb, hotkey, elevation) — those need a real desktop session.
# See docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md (§0).
#
# Requires: Docker. macOS cannot be checked this way (Docker-on-Windows/Linux
# cannot run a macOS image).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tar -cf - jarvis tests scripts/ci pyproject.toml README.md \
  | docker run --rm -i -e PYTHONDONTWRITEBYTECODE=1 python:3.11-slim bash -c '
      set -e
      mkdir -p /build && cd /build && tar -xf -
      echo "=== base install on slim Linux (pyproject deps, NO [desktop] extras) ==="
      pip install -q . pytest pytest-asyncio
      echo "=== import-cleanliness gate ==="
      python scripts/ci/check_import_clean.py
      echo "=== imports with sounddevice absent ==="
      PYTHONPATH=/build python -c "import jarvis.browser_voice.session, jarvis.browser_voice.route, jarvis.telephony.audio, jarvis.audio.player, jarvis.audio.capture; print(\"imports OK on Linux\")"
      echo "=== B2 backend + headless-import test suites ==="
      PYTHONPATH=/build python -m pytest \
        tests/unit/browser_voice \
        tests/unit/audio/test_headless_import.py \
        -q -o cache_dir=/tmp/pytest-cache
    '

echo
echo "Linux container verification complete — headless/import/logic layer green."
echo "GUI/permission layer (AX, Orb, hotkey, elevation) + macOS remain unverified;"
echo "see docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md."
