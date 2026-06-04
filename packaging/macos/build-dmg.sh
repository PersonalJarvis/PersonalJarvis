#!/usr/bin/env bash
# Personal Jarvis — macOS .dmg builder.
#
# Wraps the PyInstaller .app bundle (dist/Jarvis.app, produced by the BUNDLE
# step in jarvis.spec) into a drag-to-Applications disk image. Run AFTER
# build.sh on a macOS machine/runner.
#
# Usage:  packaging/macos/build-dmg.sh [version]
# Output: dist/PersonalJarvis-<version>.dmg
#
# Note: this produces an UNSIGNED, un-notarized .dmg. On a real Mac the user
# must right-click -> Open the first time (Gatekeeper). Signing + notarization
# require an Apple Developer ID and are tracked as future work in
# docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md.
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root
VERSION="${1:-0.1.0}"
APP="dist/Jarvis.app"
STAGE="build/dmg"
OUT="dist/PersonalJarvis-${VERSION}.dmg"

if [ ! -d "$APP" ]; then
    echo "[dmg] $APP not found — run build.sh on macOS first." >&2
    exit 1
fi

echo "[dmg] staging..."
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

echo "[dmg] creating $OUT"
rm -f "$OUT"
hdiutil create -volname "Personal Jarvis" -srcfolder "$STAGE" \
    -ov -format UDZO "$OUT"

echo "[dmg] done: $OUT"
