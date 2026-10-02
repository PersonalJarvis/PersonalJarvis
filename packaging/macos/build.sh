#!/usr/bin/env bash
#
# Build the distributable macOS disk image for Personal Jarvis.
#
#   packaging/macos/build.sh
#
# Output (per the release contract):
#   dist/installers/PersonalJarvis-macOS-arm64.dmg   on Apple Silicon
#   dist/installers/PersonalJarvis-macOS-x64.dmg     on Intel
#
# The disk image contains "Personal Jarvis.app" next to a symlink to
# /Applications, which is the drag-to-install layout every Mac user already
# knows. The app is a PyInstaller BUNDLE built from jarvis.spec.
#
# Signing and notarization are driven entirely by the environment, so the same
# script runs unchanged on a maintainer's Mac and on a GitHub runner:
#
#   APPLE_SIGNING_IDENTITY       "Developer ID Application: NAME (TEAMID)"
#   APPLE_ID                     Apple ID used for notarization
#   APPLE_TEAM_ID                10-character team identifier
#   APPLE_APP_SPECIFIC_PASSWORD  app-specific password for that Apple ID
#
# A CI runner has no Developer ID certificate in any keychain, so two more
# optional variables carry it in (a maintainer's own Mac needs neither):
#
#   APPLE_CERTIFICATE_P12_BASE64 base64 of the exported Developer ID .p12
#   APPLE_CERTIFICATE_PASSWORD   the password chosen when exporting it
#
# With those set, APPLE_SIGNING_IDENTITY may be left out: the identity is read
# back from the imported certificate.
#
# With APPLE_SIGNING_IDENTITY unset the script ad-hoc signs instead and skips
# notarization, printing one clear line about what that means for the person
# who downloads the result. Nothing is silently "signed enough".
#
# Extra knobs:
#   DRY_RUN=1     print every command instead of running it (CI-free rehearsal)
#   PYTHON=...    interpreter to build with (default: python3)
#   SKIP_FRONTEND=1  never run npm, even when the web bundle is missing
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DRY_RUN="${DRY_RUN:-0}"
PYTHON="${PYTHON:-python3}"
SKIP_FRONTEND="${SKIP_FRONTEND:-0}"

APP_NAME="Personal Jarvis"
VOLUME_NAME="Personal Jarvis"
DIST_DIR="${REPO_ROOT}/dist"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
OUT_DIR="${DIST_DIR}/installers"
FRONTEND_DIST="${REPO_ROOT}/jarvis/ui/web/dist"
ENTITLEMENTS="${SCRIPT_DIR}/entitlements.plist"
# The copy of ENTITLEMENTS that codesign embeds; see prepare_entitlements().
NORMALIZED_ENTITLEMENTS="${DIST_DIR}/entitlements.normalized.plist"
SIGNING_ENTITLEMENTS="${ENTITLEMENTS}"
ICNS="${REPO_ROOT}/assets/icons/jarvis.icns"

log() { printf '[macos-build] %s\n' "$*"; }
die() { printf '[macos-build] ERROR: %s\n' "$*" >&2; exit 1; }

# Echo the exact command, then run it (or not, under DRY_RUN). Never used for
# anything that carries a secret - see notarize() for that path.
run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [ "${DRY_RUN}" = "1" ]; then
    return 0
  fi
  "$@"
}

# True when a real build is happening; DRY_RUN skips the existence checks that
# would otherwise abort a rehearsal on a machine with no build output.
building() { [ "${DRY_RUN}" != "1" ]; }

# --- 0. Host and target -----------------------------------------------------

if building && [ "$(uname -s)" != "Darwin" ]; then
  die "this script builds a macOS app bundle and only runs on macOS (use DRY_RUN=1 to rehearse elsewhere)"
fi

MACHINE="$(uname -m)"
case "${MACHINE}" in
  arm64)  ARCH_TAG="arm64" ;;
  x86_64) ARCH_TAG="x64" ;;
  *)      die "unsupported macOS architecture: ${MACHINE}" ;;
esac
DMG_PATH="${OUT_DIR}/PersonalJarvis-macOS-${ARCH_TAG}.dmg"
log "host ${MACHINE} -> building ${ARCH_TAG}"

# A half-configured certificate is a mistake worth hearing about before the
# long freeze, not after it.
if [ -n "${APPLE_CERTIFICATE_P12_BASE64:-}" ] && [ -z "${APPLE_CERTIFICATE_PASSWORD:-}" ]; then
  die "APPLE_CERTIFICATE_P12_BASE64 is set but APPLE_CERTIFICATE_PASSWORD is not"
fi

# --- 0b. Developer ID certificate (CI runners) -------------------------------
#
# codesign can only use an identity that lives in a keychain, and a GitHub
# runner starts with none: APPLE_SIGNING_IDENTITY alone made every signed build
# fail with "no identity found" while the workflow passed the certificate
# secrets along to a script that never read them. The certificate is imported
# into a throw-away keychain that is removed (and the search list restored) when
# the script exits, so it never outlives the build. It happens FIRST, before the
# long freeze: a wrong password or a certificate without a Developer ID identity
# should cost seconds, not a finished PyInstaller run. No command that carries a
# secret goes through run(), which echoes its argv.

SIGNING_KEYCHAIN=""
SIGNING_CERT_FILE=""
ORIGINAL_KEYCHAINS=()

decode_base64() {
  # macOS spells the flag --decode on current releases and -D on older ones.
  if base64 --decode </dev/null >/dev/null 2>&1; then base64 --decode; else base64 -D; fi
}

# Runs on EVERY exit - success, `die`, `set -e`, a signal - so a failed import
# cannot leave the decoded certificate (the private key, password-protected) or
# the temporary keychain behind on a machine that is not thrown away afterwards.
cleanup_signing_material() {
  [ -z "${SIGNING_CERT_FILE}" ] || rm -f "${SIGNING_CERT_FILE}"
  [ -n "${SIGNING_KEYCHAIN}" ] || return 0
  if [ "${#ORIGINAL_KEYCHAINS[@]}" -gt 0 ]; then
    security list-keychains -d user -s "${ORIGINAL_KEYCHAINS[@]}" >/dev/null 2>&1 || true
  fi
  security delete-keychain "${SIGNING_KEYCHAIN}" >/dev/null 2>&1 || true
}
trap cleanup_signing_material EXIT

import_signing_certificate() {
  [ -n "${APPLE_CERTIFICATE_P12_BASE64:-}" ] || return 0

  local scratch keychain cert_file keychain_password line identities
  scratch="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  scratch="${scratch%/}"
  keychain="${scratch}/jarvis-signing.keychain-db"
  cert_file="${scratch}/jarvis-signing-certificate.p12"

  log "importing the Developer ID certificate into a temporary keychain"
  if [ "${DRY_RUN}" = "1" ]; then
    printf '+ security list-keychains -d user   # remembered, restored on exit\n'
    printf '+ security create-keychain -p <redacted> %q\n' "${keychain}"
    printf '+ security import <redacted .p12> -k %q -P <redacted> -T /usr/bin/codesign\n' "${keychain}"
    printf '+ security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k <redacted> %q\n' "${keychain}"
    printf '+ security list-keychains -d user -s %q <the existing keychains>\n' "${keychain}"
    APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:-<identity read from the imported certificate>}"
    return 0
  fi

  SIGNING_KEYCHAIN="${keychain}"
  SIGNING_CERT_FILE="${cert_file}"
  keychain_password="$(uuidgen)"

  # Remember the user's search list BEFORE creating anything: `create-keychain`
  # adds the new keychain to it. `security` prints each path indented and
  # quoted; strip both with sed (bash 3.2 treats quotes inside ${var#...}
  # patterns differently from newer shells).
  while IFS= read -r line; do
    [ -z "${line}" ] || ORIGINAL_KEYCHAINS+=("${line}")
  done < <(security list-keychains -d user | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//')

  # A keychain left behind by an interrupted earlier run would fail the create.
  security delete-keychain "${keychain}" >/dev/null 2>&1 || true
  security create-keychain -p "${keychain_password}" "${keychain}"
  # Stay unlocked for the whole build (6 h), even across an idle period.
  security set-keychain-settings -lut 21600 "${keychain}"
  security unlock-keychain -p "${keychain_password}" "${keychain}"

  # Owner-only: the file holds the (password-protected) private key.
  (umask 077 && printf '%s' "${APPLE_CERTIFICATE_P12_BASE64}" | decode_base64 > "${cert_file}")
  security import "${cert_file}" -k "${keychain}" -P "${APPLE_CERTIFICATE_PASSWORD}" \
    -T /usr/bin/codesign -T /usr/bin/security
  rm -f "${cert_file}"
  # Without this macOS asks (via a dialog nobody can answer on a runner) whether
  # codesign may use the private key.
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
    -k "${keychain_password}" "${keychain}" >/dev/null

  # codesign looks identities up through the search list: put ours first.
  security list-keychains -d user -s "${keychain}" \
    ${ORIGINAL_KEYCHAINS[@]+"${ORIGINAL_KEYCHAINS[@]}"}

  if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
    identities="$(security find-identity -v -p codesigning "${keychain}")"
    APPLE_SIGNING_IDENTITY="$(
      printf '%s\n' "${identities}" \
        | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' \
        | head -n 1
    )"
    [ -n "${APPLE_SIGNING_IDENTITY}" ] \
      || die "the imported certificate holds no valid 'Developer ID Application' identity - an Apple Development or App Store certificate cannot sign an app distributed outside the App Store, and the .p12 must be exported together with its certificate chain"
    log "signing identity read from the certificate: ${APPLE_SIGNING_IDENTITY}"
  fi
}

import_signing_certificate

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
if building; then
  "${PYTHON}" -c "import PyInstaller" >/dev/null 2>&1 \
    || die "PyInstaller is not installed for ${PYTHON} (pip install pyinstaller)"
fi

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
  log "WARNING: jarvis.spec bundles this machine's jarvis.toml into the app, and it differs from jarvis.toml.example. Check it carries nothing personal before publishing the build."
fi

if [ -f "${ICNS}" ] || [ "${DRY_RUN}" = "1" ]; then
  :
else
  log "regenerating the app icon (${ICNS} is missing)"
  run "${PYTHON}" "${SCRIPT_DIR}/make_icns.py"
fi

log "preparing compiler-free browser dependencies"
run "${PYTHON}" "${REPO_ROOT}/scripts/prepare_browser_wheelhouse.py"

log "running PyInstaller"
(
  cd "${REPO_ROOT}"
  run "${PYTHON}" -m PyInstaller jarvis.spec --noconfirm --clean
)

if building; then
  [ -d "${APP_BUNDLE}" ] || die "PyInstaller did not produce '${APP_BUNDLE}'. jarvis.spec must contain a darwin BUNDLE(...) block that names the app '${APP_NAME}'."
  [ -x "${APP_BUNDLE}/Contents/MacOS/jarvis" ] \
    || die "'${APP_BUNDLE}/Contents/MacOS/jarvis' is missing. The bundle must ship the console CLI entry so 'jarvis serve' works from a native install."
  # CFBundleExecutable must exist or the app cannot launch at all, and Finder
  # reports it as damaged rather than naming the missing file.
  BUNDLE_EXECUTABLE="$(
    /usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' \
      "${APP_BUNDLE}/Contents/Info.plist" 2>/dev/null || true
  )"
  [ -n "${BUNDLE_EXECUTABLE}" ] \
    || die "'${APP_BUNDLE}/Contents/Info.plist' has no CFBundleExecutable"
  [ -x "${APP_BUNDLE}/Contents/MacOS/${BUNDLE_EXECUTABLE}" ] \
    || die "Info.plist names '${BUNDLE_EXECUTABLE}' as CFBundleExecutable, but Contents/MacOS/${BUNDLE_EXECUTABLE} does not exist"
  log "bundle executable: ${BUNDLE_EXECUTABLE}"
  # plutil ships with macOS only. A malformed Info.plist would make Finder call
  # the app damaged, so say so here, before signing and notarizing it.
  if command -v plutil >/dev/null 2>&1; then
    run plutil -lint "${APP_BUNDLE}/Contents/Info.plist"
  fi
fi
log "app bundle: ${APP_BUNDLE}"

# --- 3. Code signing --------------------------------------------------------

# Apple documents no comment syntax for an entitlements file, so
# entitlements.plist carries none (why each entry exists is written down in
# packaging/macos/README.md). Where plutil exists - on macOS - the file is
# validated and rewritten as canonical XML into a copy under dist/, and codesign
# embeds that copy: a hand edit that is not a valid property list then fails
# here, in seconds, not as an opaque signing error after the long freeze.
# Elsewhere (a DRY_RUN rehearsal on another OS) the file is used as it is.
prepare_entitlements() {
  SIGNING_ENTITLEMENTS="${ENTITLEMENTS}"
  if ! command -v plutil >/dev/null 2>&1; then
    log "plutil not found - signing with ${ENTITLEMENTS} as it is"
    return 0
  fi
  run mkdir -p "${DIST_DIR}"
  run plutil -lint "${ENTITLEMENTS}"
  run plutil -convert xml1 -o "${NORMALIZED_ENTITLEMENTS}" "${ENTITLEMENTS}"
  SIGNING_ENTITLEMENTS="${NORMALIZED_ENTITLEMENTS}"
}

SIGNED_FOR_DISTRIBUTION=0
if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  SIGNED_FOR_DISTRIBUTION=1
  log "signing with Developer ID identity: ${APPLE_SIGNING_IDENTITY}"
  [ -f "${ENTITLEMENTS}" ] || die "entitlements file missing: ${ENTITLEMENTS}"
  prepare_entitlements

  # Inside-out first: codesign refuses to seal a bundle whose nested Mach-O
  # objects are unsigned or stale, and a PyInstaller bundle carries hundreds of
  # them. Apple's own guidance is to sign nested code before the container;
  # --deep on the outer seal below is the safety net, not the strategy.
  if building; then
    while IFS= read -r -d '' object; do
      run codesign --force --timestamp --options runtime \
        --entitlements "${SIGNING_ENTITLEMENTS}" \
        --sign "${APPLE_SIGNING_IDENTITY}" "${object}"
    done < <(
      find "${APP_BUNDLE}/Contents" -type f \
        \( -name '*.dylib' -o -name '*.so' -o -perm -u+x \) -print0 \
        | while IFS= read -r -d '' candidate; do
            if file -b "${candidate}" | grep -q 'Mach-O'; then
              printf '%s\0' "${candidate}"
            fi
          done
    )
  else
    log "[dry-run] would sign every Mach-O object under ${APP_BUNDLE}/Contents"
  fi

  run codesign --force --deep --timestamp --options runtime \
    --entitlements "${SIGNING_ENTITLEMENTS}" \
    --sign "${APPLE_SIGNING_IDENTITY}" "${APP_BUNDLE}"
  run codesign --verify --strict --verbose=2 "${APP_BUNDLE}"
else
  log "NOTICE: APPLE_SIGNING_IDENTITY is not set - ad-hoc signing this build. It is NOT notarized; macOS will refuse the first double-click and the user must right-click the app once and choose Open."
  run codesign --force --deep -s - "${APP_BUNDLE}"
fi

# --- 4. Notarization --------------------------------------------------------

NOTARIZED=0
notary_credentials_present() {
  [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]
}

# Submit one artifact and wait for Apple's verdict. The password never reaches
# the log: the command line is printed with the secret replaced, and the real
# invocation is not echoed.
notarize() {
  local artifact="$1"
  printf '+ xcrun notarytool submit %q --apple-id %q --team-id %q --password <redacted> --wait\n' \
    "${artifact}" "${APPLE_ID:-}" "${APPLE_TEAM_ID:-}"
  if [ "${DRY_RUN}" = "1" ]; then
    return 0
  fi
  xcrun notarytool submit "${artifact}" \
    --apple-id "${APPLE_ID}" \
    --team-id "${APPLE_TEAM_ID}" \
    --password "${APPLE_APP_SPECIFIC_PASSWORD}" \
    --wait
}

if [ "${SIGNED_FOR_DISTRIBUTION}" = "1" ] && notary_credentials_present; then
  NOTARIZED=1
  # The app is notarized on its own before the disk image so the ticket can be
  # stapled INTO the bundle. A ticket stapled only to the .dmg disappears the
  # moment the user drags the app out of it, which is the one thing every user
  # does - the app would then need a network round trip on first launch.
  ZIP_PATH="${DIST_DIR}/PersonalJarvis-macOS-${ARCH_TAG}-notarize.zip"
  run rm -f "${ZIP_PATH}"
  run ditto -c -k --keepParent "${APP_BUNDLE}" "${ZIP_PATH}"
  notarize "${ZIP_PATH}"
  run xcrun stapler staple "${APP_BUNDLE}"
  run rm -f "${ZIP_PATH}"
elif [ "${SIGNED_FOR_DISTRIBUTION}" = "1" ]; then
  log "NOTICE: Developer ID signed but NOT notarized - APPLE_ID, APPLE_TEAM_ID and APPLE_APP_SPECIFIC_PASSWORD must all be set for notarization."
fi

# --- 5. Disk image ----------------------------------------------------------

run mkdir -p "${OUT_DIR}"
run rm -f "${DMG_PATH}"

STAGE_DIR="${DIST_DIR}/dmg-stage-${ARCH_TAG}"
run rm -rf "${STAGE_DIR}"
run mkdir -p "${STAGE_DIR}"
# -R: the bundle contains symlinks (Frameworks/Versions/Current); cp -R keeps
# them as symlinks instead of duplicating the targets and doubling the size.
run cp -R "${APP_BUNDLE}" "${STAGE_DIR}/${APP_NAME}.app"
run ln -s /Applications "${STAGE_DIR}/Applications"

log "building the disk image"
# UDZO = zlib-compressed read-only image, the ordinary format for a downloaded
# .dmg. HFS+ rather than APFS so the image also mounts on the older macOS
# versions the app still supports.
run hdiutil create \
  -volname "${VOLUME_NAME}" \
  -srcfolder "${STAGE_DIR}" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "${DMG_PATH}"
run rm -rf "${STAGE_DIR}"

if [ "${SIGNED_FOR_DISTRIBUTION}" = "1" ]; then
  run codesign --force --timestamp --sign "${APPLE_SIGNING_IDENTITY}" "${DMG_PATH}"
fi
if [ "${NOTARIZED}" = "1" ]; then
  # Second submission, this time for the container itself: a downloaded .dmg
  # carries its own quarantine flag and is gatekeeper-checked before anything
  # inside it is read.
  notarize "${DMG_PATH}"
  run xcrun stapler staple "${DMG_PATH}"
fi

log "verifying the disk image"
run hdiutil verify "${DMG_PATH}"
if [ "${NOTARIZED}" = "1" ]; then
  run xcrun stapler validate "${DMG_PATH}"

  # The checks above answer "is the image intact" and "is a ticket attached".
  # Neither answers the only question that matters to a downloader: does
  # Gatekeeper let this open. A ticket can be stapled to an image whose seal
  # Gatekeeper still refuses - a nested object missed by the re-sign pass, a
  # seal broken after signing, an entitlement the hardened runtime rejects -
  # and every check above passes while the first double-click fails.
  #
  # Assess it the way the downloader's Mac will. That means quarantined: an
  # unquarantined file takes a different path through Gatekeeper and is
  # accepted in cases where the downloaded copy is not, so testing the file
  # as it sits here would prove nothing about the file as it arrives.
  if building; then
    log "assessing the image as Gatekeeper will see it (quarantined)"
    xattr -w com.apple.quarantine "0081;00000000;build-verify;" "${DMG_PATH}" \
      || die "could not set the quarantine flag; the assessment below would not reflect a downloaded copy"

    ASSESSMENT="$(spctl -a -t open --context context:primary-signature -vv "${DMG_PATH}" 2>&1 || true)"
    xattr -d com.apple.quarantine "${DMG_PATH}" 2>/dev/null || true

    printf '%s\n' "${ASSESSMENT}" | sed 's/^/    /'
    case "${ASSESSMENT}" in
      *accepted*) log "Gatekeeper accepts this image" ;;
      *) die "Gatekeeper REJECTED this image - publishing it would show users 'damaged and can't be opened'. Fix the signature before releasing." ;;
    esac
  fi
fi

# --- 6. Report --------------------------------------------------------------

if [ "${SIGNED_FOR_DISTRIBUTION}" != "1" ]; then
  log "NOTICE: this build is ad-hoc signed and unnotarized - suitable for local testing, not for public download."
fi
log "done"
printf '%s\n' "${DMG_PATH}"
