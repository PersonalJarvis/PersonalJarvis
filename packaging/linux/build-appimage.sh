#!/usr/bin/env bash
# Personal Jarvis — Linux AppImage builder.
#
# Assembles an AppDir from the PyInstaller onedir bundle (dist/Jarvis/) and
# packs it into a single double-click PersonalJarvis-x86_64.AppImage using
# appimagetool. Run AFTER build.sh has produced dist/Jarvis/.
#
# Usage:  packaging/linux/build-appimage.sh [version]
# Output: dist/PersonalJarvis-<version>-x86_64.AppImage
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root
VERSION="${1:-0.1.0}"
HERE="packaging/linux"
APPDIR="build/AppDir"
BUNDLE="dist/Jarvis"

if [ ! -d "$BUNDLE" ]; then
    echo "[appimage] $BUNDLE not found — run build.sh first." >&2
    exit 1
fi

echo "[appimage] assembling AppDir..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a "$BUNDLE/." "$APPDIR/usr/bin/"

# Desktop entry + icon at the AppDir root (AppImage convention).
cp "$HERE/jarvis.desktop" "$APPDIR/jarvis.desktop"
cp "assets/icons/jarvis-gigi-256.png" "$APPDIR/jarvis.png"
cp "$HERE/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# Fetch appimagetool if not already cached.
TOOL="build/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    echo "[appimage] downloading appimagetool..."
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi

OUT="dist/PersonalJarvis-${VERSION}-x86_64.AppImage"
echo "[appimage] packing -> $OUT"
# ARCH is required by appimagetool; --appimage-extract-and-run avoids needing
# FUSE on CI runners.
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"

echo "[appimage] done: $OUT"
