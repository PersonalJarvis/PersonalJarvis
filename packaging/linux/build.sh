#!/usr/bin/env bash
#
# Build the distributable Linux AppImage for Personal Jarvis.
#
#   packaging/linux/build.sh
#
# Output (per the release contract):
#   dist/installers/PersonalJarvis-Linux-x86_64.AppImage
#   dist/installers/personal-jarvis_<version>_amd64.deb   (when dpkg-deb exists)
#
# The AppImage is one downloadable file that needs no Python, no pip and no
# system packages: double-click it to run the app, or call it like the CLI
# ("./PersonalJarvis-Linux-x86_64.AppImage serve"). Both entry points come out
# of the same PyInstaller onedir freeze.
#
# What this AppImage does NOT contain is a native desktop window - read
# packaging/linux/README.md before changing that; it is a deliberate decision,
# not an oversight.
#
# Knobs:
#   DRY_RUN=1          print every command instead of running it
#   PYTHON=...         interpreter to build with (default: python3)
#   SKIP_FRONTEND=1    never run npm, even when the web bundle is missing
#   SKIP_PYINSTALLER=1 reuse the freeze already in FREEZE_DIR
#   FREEZE_DIR=...     PyInstaller onedir output (default: dist/Jarvis)
#   BUILD_DEB=0        skip the .deb even when dpkg-deb is installed
#   APPIMAGETOOL=...   use this appimagetool binary instead of downloading one
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DRY_RUN="${DRY_RUN:-0}"
PYTHON="${PYTHON:-python3}"
SKIP_FRONTEND="${SKIP_FRONTEND:-0}"
SKIP_PYINSTALLER="${SKIP_PYINSTALLER:-0}"
BUILD_DEB="${BUILD_DEB:-1}"

DIST_DIR="${REPO_ROOT}/dist"
FREEZE_DIR="${FREEZE_DIR:-${DIST_DIR}/Jarvis}"
OUT_DIR="${DIST_DIR}/installers"
APPDIR="${DIST_DIR}/AppDir"
FRONTEND_DIST="${REPO_ROOT}/jarvis/ui/web/dist"
APPIMAGE_PATH="${OUT_DIR}/PersonalJarvis-Linux-x86_64.AppImage"

# Pinned build tool. "continuous" is a moving target and an unpinned build tool
# is an unpinned supply chain, so this is an exact release plus its digest; the
# download is refused when the digest does not match.
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGETOOL_CACHE="${DIST_DIR}/.build-tools/appimagetool-${APPIMAGETOOL_VERSION}-x86_64.AppImage"

ICON_SOURCE="${REPO_ROOT}/assets/icons/jarvis-gigi-256.png"
DESKTOP_FILE_NAME="personal-jarvis.desktop"
ICON_FILE_NAME="personal-jarvis.png"

log() { printf '[linux-build] %s\n' "$*"; }
die() { printf '[linux-build] ERROR: %s\n' "$*" >&2; exit 1; }

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [ "${DRY_RUN}" = "1" ]; then
    return 0
  fi
  "$@"
}

building() { [ "${DRY_RUN}" != "1" ]; }

# --- 0. Host and target -----------------------------------------------------

if building && [ "$(uname -s)" != "Linux" ]; then
  die "this script builds a Linux AppImage and only runs on Linux (use DRY_RUN=1 to rehearse elsewhere)"
fi
if building && [ "$(uname -m)" != "x86_64" ]; then
  die "the release contract covers x86_64 only; this host is $(uname -m)"
fi

# --- 1. Web bundle ----------------------------------------------------------

if [ -d "${FRONTEND_DIST}" ] && [ -f "${FRONTEND_DIST}/index.html" ]; then
  log "web bundle present: ${FRONTEND_DIST}"
elif [ "${SKIP_FRONTEND}" = "1" ]; then
  die "web bundle missing and SKIP_FRONTEND=1 - build it with 'npm run build' in jarvis/ui/web/frontend"
else
  log "web bundle missing - building it"
  command -v npm >/dev/null 2>&1 || die "npm is required to build the web bundle"
  (
    cd "${REPO_ROOT}/jarvis/ui/web/frontend"
    if [ ! -d node_modules ]; then
      run npm ci
    fi
    run npm run build
  )
fi

# --- 2. Freeze --------------------------------------------------------------

command -v "${PYTHON}" >/dev/null 2>&1 || die "interpreter not found: ${PYTHON}"

# jarvis.spec bundles ./jarvis.toml unconditionally, and that file is
# .gitignore'd - a fresh clone (every CI runner) does not have one and
# PyInstaller aborts before it starts. Seed the shipped default so the build
# works from a clean checkout. On a machine that already has a live config the
# spec bundles THAT file as-is, which is a privacy question the build cannot
# answer for the maintainer, so it says so instead of quietly shipping it.
if [ ! -f "${REPO_ROOT}/jarvis.toml" ]; then
  [ -f "${REPO_ROOT}/jarvis.toml.example" ] \
    || die "neither jarvis.toml nor jarvis.toml.example exists; jarvis.spec cannot bundle a default configuration"
  log "seeding jarvis.toml from jarvis.toml.example (clean checkout)"
  run cp "${REPO_ROOT}/jarvis.toml.example" "${REPO_ROOT}/jarvis.toml"
elif ! cmp -s "${REPO_ROOT}/jarvis.toml" "${REPO_ROOT}/jarvis.toml.example"; then
  log "WARNING: jarvis.spec bundles this machine's jarvis.toml into the package, and it differs from jarvis.toml.example. Check it carries nothing personal before publishing the build."
fi

if [ "${SKIP_PYINSTALLER}" = "1" ]; then
  log "SKIP_PYINSTALLER=1 - reusing ${FREEZE_DIR}"
else
  if building; then
    "${PYTHON}" -c "import PyInstaller" >/dev/null 2>&1 \
      || die "PyInstaller is not installed for ${PYTHON} (pip install pyinstaller)"
  fi
  log "running PyInstaller"
  (
    cd "${REPO_ROOT}"
    run "${PYTHON}" -m PyInstaller jarvis.spec --noconfirm --clean
  )
fi

if building; then
  [ -x "${FREEZE_DIR}/Jarvis" ] \
    || die "PyInstaller did not produce '${FREEZE_DIR}/Jarvis' (the GUI launcher target in jarvis.spec)"
  if [ ! -e "${FREEZE_DIR}/jarvis" ]; then
    # The contract asks for two executables sharing one COLLECT. On Linux they
    # are the same program (PyInstaller's console flag has no meaning here), so
    # a link keeps the AppImage correct while the spec catches up - but say so
    # loudly, because on Windows and macOS the missing target is a real gap.
    log "WARNING: '${FREEZE_DIR}/jarvis' is missing - jarvis.spec has no second, console CLI EXE target. Linking it to the GUI executable so the CLI entry exists."
    run ln -s Jarvis "${FREEZE_DIR}/jarvis"
  fi
fi

# --- 3. AppDir --------------------------------------------------------------

log "assembling the AppDir"
run rm -rf "${APPDIR}"
run mkdir -p "${APPDIR}/usr/bin" \
  "${APPDIR}/usr/share/applications" \
  "${APPDIR}/usr/share/icons/hicolor/256x256/apps" \
  "${APPDIR}/usr/share/personal-jarvis"

if building; then
  # The whole onedir tree lands in usr/bin so PyInstaller's "_internal next to
  # the executable" layout keeps working without a wrapper. cp -a preserves the
  # executable bits and the symlinks inside the freeze.
  cp -a "${FREEZE_DIR}/." "${APPDIR}/usr/bin/"
else
  log "[dry-run] would copy ${FREEZE_DIR}/. into ${APPDIR}/usr/bin/"
fi

run "${PYTHON}" "${SCRIPT_DIR}/make_desktop_entry.py" \
  "${APPDIR}/usr/share/applications/${DESKTOP_FILE_NAME}" --exec Jarvis

[ -f "${ICON_SOURCE}" ] || die "icon master missing: ${ICON_SOURCE}"
run cp "${ICON_SOURCE}" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/${ICON_FILE_NAME}"

# The AppImage specification wants the desktop entry, the icon and .DirIcon in
# the AppDir ROOT; appimagetool refuses to build without them.
run cp "${APPDIR}/usr/share/applications/${DESKTOP_FILE_NAME}" "${APPDIR}/${DESKTOP_FILE_NAME}"
run cp "${ICON_SOURCE}" "${APPDIR}/${ICON_FILE_NAME}"
run cp "${ICON_SOURCE}" "${APPDIR}/.DirIcon"

run cp "${SCRIPT_DIR}/AppRun" "${APPDIR}/AppRun"
run chmod +x "${APPDIR}/AppRun"

# Does this freeze carry a pywebview window backend? Without PyGObject there is
# no GTK/WebKit window and the app serves its interface over HTTP instead; the
# marker is what tells AppRun to open a browser on a bare double-click. Writing
# it from an actual inspection means a future build that DOES bundle gi stops
# opening the extra tab on its own, with no second place to remember.
if building && [ -d "${APPDIR}/usr/bin/_internal/gi" ]; then
  log "freeze contains PyGObject - native window backend available, no browser hand-off"
  run rm -f "${APPDIR}/usr/share/personal-jarvis/browser-ui"
else
  log "freeze has no PyGObject - the app will serve its interface over HTTP and AppRun opens it in the default browser"
  if building; then
    printf '%s\n' "This build has no bundled GTK/WebKit window backend; AppRun opens the UI in the default browser." \
      > "${APPDIR}/usr/share/personal-jarvis/browser-ui"
  fi
fi

# --- 4. appimagetool --------------------------------------------------------

if [ -n "${APPIMAGETOOL:-}" ]; then
  log "using APPIMAGETOOL=${APPIMAGETOOL}"
else
  APPIMAGETOOL="${APPIMAGETOOL_CACHE}"
  if [ -f "${APPIMAGETOOL}" ]; then
    log "appimagetool ${APPIMAGETOOL_VERSION} already downloaded"
  else
    log "downloading appimagetool ${APPIMAGETOOL_VERSION}"
    run mkdir -p "$(dirname "${APPIMAGETOOL}")"
    if command -v curl >/dev/null 2>&1; then
      run curl -fsSL --retry 3 -o "${APPIMAGETOOL}.part" "${APPIMAGETOOL_URL}"
    elif command -v wget >/dev/null 2>&1; then
      run wget -q -O "${APPIMAGETOOL}.part" "${APPIMAGETOOL_URL}"
    else
      die "curl or wget is required to download appimagetool"
    fi
    if building; then
      echo "${APPIMAGETOOL_SHA256}  ${APPIMAGETOOL}.part" | sha256sum -c - \
        || { rm -f "${APPIMAGETOOL}.part"; die "appimagetool digest mismatch - refusing to build with it"; }
      mv "${APPIMAGETOOL}.part" "${APPIMAGETOOL}"
    fi
  fi
  run chmod +x "${APPIMAGETOOL}"
fi

# --- 5. Build the AppImage --------------------------------------------------

run mkdir -p "${OUT_DIR}"
run rm -f "${APPIMAGE_PATH}"
log "building the AppImage"
# APPIMAGE_EXTRACT_AND_RUN: appimagetool is itself an AppImage and needs FUSE to
# mount itself. CI containers and hardened hosts have no FUSE, and extracting is
# the documented way around that - it costs a few seconds and always works.
# --no-appstream: this project ships no AppStream metainfo file, and the check
# is a hard error rather than a warning without one.
if building; then
  env APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 \
    "${APPIMAGETOOL}" --no-appstream "${APPDIR}" "${APPIMAGE_PATH}"
else
  log "[dry-run] would run: APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 ${APPIMAGETOOL} --no-appstream ${APPDIR} ${APPIMAGE_PATH}"
fi
run chmod +x "${APPIMAGE_PATH}"

# --- 6. Optional Debian package --------------------------------------------

DEB_PATH=""
if [ "${BUILD_DEB}" = "0" ]; then
  log "BUILD_DEB=0 - skipping the Debian package"
elif ! command -v dpkg-deb >/dev/null 2>&1; then
  log "dpkg-deb not installed - skipping the Debian package"
else
  VERSION="$("${PYTHON}" -c "import sys; sys.path.insert(0, '${REPO_ROOT}'); import jarvis; print(jarvis.__version__)")"
  DEB_ROOT="${DIST_DIR}/deb-root"
  DEB_PATH="${OUT_DIR}/personal-jarvis_${VERSION}_amd64.deb"
  log "building ${DEB_PATH}"
  run rm -rf "${DEB_ROOT}"
  run mkdir -p "${DEB_ROOT}/DEBIAN" \
    "${DEB_ROOT}/opt/PersonalJarvis" \
    "${DEB_ROOT}/usr/bin" \
    "${DEB_ROOT}/usr/share/applications" \
    "${DEB_ROOT}/usr/share/icons/hicolor/256x256/apps"
  if building; then
    # The very same AppDir: one tree, two package formats, so a bug can only
    # exist in both or in neither.
    cp -a "${APPDIR}/." "${DEB_ROOT}/opt/PersonalJarvis/"
    cat > "${DEB_ROOT}/DEBIAN/control" <<EOF
Package: personal-jarvis
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Depends: libc6
Recommends: libportaudio2
Maintainer: Personal Jarvis contributors <https://github.com/PersonalJarvis/PersonalJarvis>
Description: Voice-driven meta-orchestrator
 Personal Jarvis turns one spoken request into a fleet of self-checking AI
 agents. This package ships the self-contained desktop build; it needs no
 system Python and no pip installation.
EOF
    ln -sf /opt/PersonalJarvis/usr/bin/jarvis "${DEB_ROOT}/usr/bin/jarvis"
    ln -sf /opt/PersonalJarvis/AppRun "${DEB_ROOT}/usr/bin/personal-jarvis"
    cp "${ICON_SOURCE}" "${DEB_ROOT}/usr/share/icons/hicolor/256x256/apps/${ICON_FILE_NAME}"
  fi
  run "${PYTHON}" "${SCRIPT_DIR}/make_desktop_entry.py" \
    "${DEB_ROOT}/usr/share/applications/${DESKTOP_FILE_NAME}" \
    --exec /opt/PersonalJarvis/AppRun
  run dpkg-deb --build --root-owner-group "${DEB_ROOT}" "${DEB_PATH}"
  run rm -rf "${DEB_ROOT}"
fi

# --- 7. Report --------------------------------------------------------------

log "done"
printf '%s\n' "${APPIMAGE_PATH}"
if [ -n "${DEB_PATH}" ]; then
  printf '%s\n' "${DEB_PATH}"
fi
