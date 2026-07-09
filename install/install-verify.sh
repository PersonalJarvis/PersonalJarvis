#!/usr/bin/env bash
# Personal Jarvis — verifying one-liner (Wave 3 supply-chain).
#
# The user runs:
#   curl -fsSL https://github.com/PersonalJarvis/PersonalJarvis/releases/download/<TAG>/install-verify.sh | bash
#
# Wave 3 demands 3-of-3 independent trust axes — all must validate or the
# verifier refuses to hand off to install.sh:
#
#   Axis A (online, ephemeral): cosign keyless signature against Fulcio,
#                               minted by this repo's GitHub Actions workflow
#                               via OIDC. install.sh.sig + install.sh.pem +
#                               install.sh.bundle (the Wave 1 trio).
#
#   Axis B (offline, long-lived): cosign --key signature against an
#                               Ed25519 public key generated in an
#                               air-gapped ceremony, pinned in this script
#                               AND committed at install/keys/offline-ceremony.pub.
#                               install.sh.cosign.sig (Wave 2 addition).
#
#   Axis C (build-env, SLSA L3 + in-toto layout): slsa-verifier checks the
#                               SLSA L3 provenance (personal-jarvis.intoto.jsonl)
#                               for the installer artifact AND the verifier
#                               cross-checks the in-toto layout's pinned
#                               functionary identity-regexp against the Fulcio
#                               identity in the provenance. This adds an
#                               independent attestation of the BUILD ENVIRONMENT
#                               on top of axis A's artifact-only attestation:
#                               an attacker who compromises the runner but
#                               signs the same content is caught because the
#                               BUILD INPUTS (recorded in the provenance) have
#                               changed.
#
#   Axis D (post-quantum, FIPS 204): ML-DSA-65 (CRYSTALS-Dilithium category 3)
#                               key-bound signature using a private key
#                               generated alongside the Wave-2 offline-ceremony
#                               key. Defense in depth against Shor's algorithm
#                               and store-now-decrypt-later: ECDSA-P256 (axis A)
#                               and Ed25519 (axis B) both fall to a sufficiently
#                               large quantum computer; ML-DSA-65 (NIST IR 8413
#                               classical-equivalent security floor >=192 bits)
#                               survives the transition. Verified in stage
#                               [13/13] in TRANSITION MODE: if the local
#                               openssl is >=3.5 the PQ signature is enforced;
#                               on older toolchains the PQ verify is SKIPPED
#                               WITH AN EXPLICIT WARNING (the classical axes
#                               A+B+C have already validated). Never silently
#                               skipped.
#
# Compromising any single axis is insufficient. The four axes are rooted in
# four different trust assumptions (OIDC token, classical-key custody, build
# provenance integrity, post-quantum-key custody), so an attacker must
# compromise all four to ship poisoned bytes that this verifier accepts.
#
# Any non-zero exit anywhere in stages [0/13]..[13/13] is FAIL-CLOSED: the
# second-stage installer is never executed.
#
# Threat model rationale lives in docs/supply-chain/threat-model.md.
# Trust-root rotation procedure lives in install/TRUST_ROOT.md.

set -euo pipefail

# ----------------------------------------------------------------------- pins
# These constants are the entire bootstrap-trust root. They MUST be kept in
# sync with install/TRUST_ROOT.md. Bumping any of them is a deliberate
# documented event, not a maintenance task.

# Owning repo (for the OIDC identity regex). Users running a fork must
# rebuild the verifier with their fork's slug.
readonly EXPECTED_REPO="PersonalJarvis/PersonalJarvis"

# Path of the signing workflow inside the source repo. Pinned so an
# attacker who adds a *different* workflow that signs with the same OIDC
# scope can't slip through verification.
readonly EXPECTED_WORKFLOW_PATH=".github/workflows/sign-installer.yml"

# OIDC issuer that mints the JWT consumed by Fulcio. Pinning this to the
# GitHub Actions production issuer means an attacker would have to either
# (a) compromise GitHub's OIDC issuer, or (b) sign with an unrelated
# issuer and hope our regex is loose — it isn't.
readonly EXPECTED_OIDC_ISSUER="https://token.actions.githubusercontent.com"

# Cosign release we will download. Bumping requires updating the four
# SHA-256 pins below as well and updating TRUST_ROOT.md with the
# verification provenance (where the new hashes were observed and by whom).
readonly COSIGN_VERSION="v2.4.1"

# SHA-256 of each cosign binary asset for v2.4.1. Source of truth:
#   https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign_checksums.txt
# Independently verifiable by anyone reading this file.
readonly COSIGN_SHA256_LINUX_AMD64="8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b"
readonly COSIGN_SHA256_LINUX_ARM64="3b2e2e3854d0356c45fe6607047526ccd04742d20bd44afb5be91fa2a6e7cb4a"
readonly COSIGN_SHA256_DARWIN_AMD64="666032ca283da92b6f7953965688fd51200fdc891a86c19e05c98b898ea0af4e"
readonly COSIGN_SHA256_DARWIN_ARM64="13343856b69f70388c4fe0b986a31dde5958e444b41be22d785d3dc5e1a9cc62"

# Rekor inclusion freshness window. Anything older than this is rejected,
# defending against the attacker-replays-old-revoked-signature scenario.
# 24 hours is tight; raise this only with a documented justification.
readonly REKOR_MAX_AGE_SECONDS=86400

# WAVE 2 PINNED OFFLINE KEY — fingerprint 1e8f2fa590e6454daff34e88e7bde8ffcf04b1eb235f0ca11ff9ebc65e2d1d3a
#
# Ed25519 public key produced in the air-gapped offline-ceremony documented in
# docs/supply-chain/wave2-key-ceremony.md. Fingerprint is sha256(DER(pubkey)).
# This blob is inlined here (not just fetched from the release) so that an
# attacker who controls the release-asset store cannot quietly swap the key:
# stage [3/13] also fetches the released copy and refuses to proceed if the
# two diverge.
read -r -d '' OFFLINE_CEREMONY_PUBKEY_PEM <<'WAVE2_OFFLINE_KEY_EOF' || true
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEArlfig3ALFBrED+VrNZ8hlrVnRJxDnI8PCkxGB26N4U4=
-----END PUBLIC KEY-----
WAVE2_OFFLINE_KEY_EOF
readonly OFFLINE_CEREMONY_PUBKEY_PEM
readonly OFFLINE_CEREMONY_PUBKEY_FINGERPRINT="1e8f2fa590e6454daff34e88e7bde8ffcf04b1eb235f0ca11ff9ebc65e2d1d3a"

# WAVE 4 PINNED POST-QUANTUM KEY — fingerprint
# db0073bf5b77d5b0e4e5547bfcf86227031c9a138cb3088a57c270b8fbac4073
#
# ML-DSA-65 public key (NIST FIPS 204 category 3, ≥192-bit classical-
# equivalent security floor). Generated alongside the Wave-2 offline-
# ceremony key using OpenSSL 3.5.6's `openssl genpkey -algorithm ML-DSA-65`
# (documented in docs/supply-chain/wave4-distribution.md). Fingerprint is
# sha256(DER(SubjectPublicKeyInfo)).
#
# Inlined here (not just fetched from the release) so that an attacker who
# controls the release-asset store cannot quietly swap the key: stage
# [12/13] also fetches the released copy and refuses to proceed if the
# two diverge — same defense pattern as the Wave-2 offline key.
#
# An ML-DSA-65 SubjectPublicKeyInfo DER is ~2700 bytes; the base64-armored
# PEM is ~3700 bytes spread over ~42 base64 lines. The size is *not* a
# bug.
read -r -d '' PQ_MLDSA65_PUBKEY_PEM <<'WAVE4_PQ_KEY_EOF' || true
-----BEGIN PUBLIC KEY-----
MIIHsjALBglghkgBZQMEAxIDggehAJfnhjiTmp7Hph2Nn1ksdSfexmqPJm6hWwbF
i9gCq5Of+fi5fa94kpT5IXLsEyYsl5ade4Eh+HRS6kdKK/FA9tNleX0RkFBqUpSu
AlBz2/36UZOTsyJsjXR1B5EATIWon03/sKbDS8H79SpbgLSqg+mSJgDS2Okec5iw
lUEC4bX5mi/6Ws11qwB15ZxEMVRJ49N9RgY3reYZMB4UYasGh40tFk1nlLCXB4WZ
Q5oJL3g7iWQKIcwEVnj1aerQeIwTR5pntMfQkJmKEL28ybE4IMSIJ6v5b/vJAkeG
Hd4OLCNiLtY0YA62BS9sXnPCFa2YLFR8CPCeDMVFFRd43YqO8c93WQxE+pk8/0rF
uuYm6iUvEVMxUkrlQ0AxBmW0S/tL2kpgnZYEp1JPnuTQZgtnaxmZnaI5KskC8vFo
YKF8HcijuXwr8TsBRCHnPtEMaMSIU+wHvQIwlP1MxisFHNeBV+lQdmCrTIWX2OCM
BD2vjN6nNIJU8hK8Lwa0nGIRjfU8gnVAx/gtcPRMoDjHgsjkN5LHLu49/ptYFrMc
zKH8vAKQjxaySwEPvI0h4nCbzI3cQ1OIlLzMLSD8U3EuKSlVkLq833jlFDWZKNWO
DQ4RnIltaIRzjfUWs3i0pdPyDoQJSnw0PJ/toBAJKLB9lIvLc0a9au3UnGgRaWgl
XPsWYdT3mPcVO5ROeLRMRIg0HPcgyoias3JLhreQCTDUsQw3e5E596DXfQKgBc/Y
s/WWzD8LpLFuqg5XRYwkZ7FQ5upxlDg0Ax1ZCNPaXm6RcViNausW91PVyexsiwiT
bcpNQLm/afOpAq+FV3H4r7qSsiPT2uOcQzf9Um5auyaeblLBe/6GUg++cjTP95aG
jABZor19m2To+wI2MaPA1g8tgmiZ/4tfAtZcdELcRIXq59ALBsmYmootjBS7z0KV
3jllRsW03nPss5V8hVuYQo4yzZgqgh1mNs+BAQHIUZegjZkRu4EYaAJoDGTes084
5pGs067VwiB1KBStyHuvpvG8WQ1LsP0rXkAHmfJONNlrYAQvR/k9AyRfWeJpwc/Z
U7RG39R/GAaj2MpzpzTK+nYfy8rq64fmmFX/LSJ6HngmvqWKR+XuZVw5nppjChEe
79CF1K7up2Qj3Nr9VLrFmeZgpbM/9j4sRaqmr4prj+aQSwZAj88hlJTa+SmgQRIq
l0pSPGghFu9tA9ibgemsjXYRMU4cW2mdetz86GnnhLn94kBluBWnDNY1RF/xC+H+
PNF8AQssrB375hou1+lSeCobySMko5RUEJBBMYTZsWAGH88Ml3KasNiP+Ar81Kn6
nOBuMvoRQ8aHS576T9hew24ho1rHTnNqPD/ei45yhdcmb6zXjJqDtt/8mWrhy02v
FXAD1v6yBH4WFOVIJrdIg+EFytpXiMz7HLCNF8YdEr9DtKEnuMu7CB+XoxQ+hbzc
ZhzLO71U5ai5pjMvXA6Nn0SxR2N8GjRTe8GZN18S4rSxt4ESpGWVT+KXONrxgJxJ
yzRs6TlWa9VgTgi6z4E5iWlXh6uZmSs7Y4xl5Y2o++lItRVnoprLwYt1G5rGnN1T
dhXcGoi1RI3m4PK60myzdArVnB/hBop0VxiNJ4Z1vlFdXeqyqhvAf7/3CLAxRNVu
hzoVLKgw9gL3CWSKcGVopSwEdnrN1U4aiQILBAW9Dj8Q0JHQInmSoFo9HR06vXK3
psF2oKH/w4GV18pg+Oc2M+ePITf9E6XNn4jk6VYCGcMEx7D8YSdXmoYbD7bcSKCg
LaSDf5vVfDqGbWb8i96nvK5MhC82LcSZGA1wQOAu7KdVGBr8jbd7cUNkZZGoobrV
3cGCOxSbzfo1cGVLyKtyxfeht81wVMY8G/37vVOWybROvU2Ohokeu+LsEm0nNxeq
OQ0hspr+FDBlycaN9itAZBx71qPBdABVAhT33mgHfZ2cp7QYKahZtiMpopYMtlHS
oLFtWeWk1tUCE8FgqWsM+8Qvc+NxxIrZUBvjB8OsQymj8oWHtZfur4TEyD52H17x
LdLm2RDD9RHC1ND8diH8jauhBuDhFn9QgBV6aGvHBafkq36gwM9kG+6trKr7Tnj1
QjcP/ygmHyyAbGgkWURACMNEWpbeQi8rXcDek73etqTKkzmubhejBwGAmBG7xnP3
kY2hEaGnEoNAmdtV/ZCjlIDlih4ThL8Mj534/iUUAbq53xe4G25DfMW2nSXZ3kd/
m44926SSX9P88TGGOG3YFxR0bCyB0cz7tN+LIoKAqRInNJDN25Ycjhfg17/gOsdd
AzOtNJQtb2RSGSaWO83RtQhcY0ZS05b2lY0lYDy4rLWQAypw19XsL0hYPucfm2YF
1RB2YpTqiZSTYKQvEonT3Vk6O4tIDWXfLBxIffQeLp9QJQIjcN36cuzkHKzSd249
+h3y177YOIuSmQurLVNibRqajhKHmIDOE0CSGXRwvp15P0+weC5yYT4O0iWj/efW
vv81Qlf/DYRCFyq3swIL5LBOyFPQOfh1eFJ7bGpkR8L3C9LePmjk8piMwxP6IyYo
bEwacFTIaZ/bENBu+9FSsrhibyy3+O7V17xfVq3fDrTzVMv2j8S3vobm3CM1WcyU
h5pIHhiz
-----END PUBLIC KEY-----
WAVE4_PQ_KEY_EOF
readonly PQ_MLDSA65_PUBKEY_PEM
readonly PQ_MLDSA65_PUBKEY_FINGERPRINT="db0073bf5b77d5b0e4e5547bfcf86227031c9a138cb3088a57c270b8fbac4073"
# Released asset name (axis-D pubkey cross-check) — must match the
# workflow's `cp install/keys/pq-mldsa65.pub.pem out/pq-mldsa65.pub.pem`.
readonly PQ_MLDSA65_PUBKEY_ASSET_NAME="pq-mldsa65.pub.pem"
# Suffix the workflow attaches per artifact (e.g. install.sh.mldsa.sig).
readonly PQ_MLDSA65_SIG_EXT=".mldsa.sig"
# Minimum OpenSSL version required to verify ML-DSA signatures locally
# (ML-DSA support landed in OpenSSL 3.5.0). If the host openssl is older
# (or missing), stage [13/13] prints an explicit SKIPPED warning and
# proceeds — the classical axes A+B+C have already validated. This is
# TRANSITION MODE, never silent.
readonly PQ_MLDSA65_MIN_OPENSSL_REGEX='OpenSSL[[:space:]]+(3\.([5-9]|[1-9][0-9])|[4-9]\.|[1-9][0-9]+\.)'

# WAVE 3 — slsa-verifier release pin. Same discipline as cosign: we download
# the slsa-verifier binary for the host platform and refuse to execute it
# unless its SHA-256 matches the pin below. Bumping requires updating these
# pins AND TRUST_ROOT.md §4 with the verification provenance.
#
# Source of truth for these hashes:
#   https://github.com/slsa-framework/slsa-verifier/blob/main/SHA256SUM.md
# (the README publishes per-asset SHA-256 hashes for every release; cross-
# checkable by anyone reading this file from at least two networks).
#
# Pinned version: v2.7.0 — the slsa-github-generator v2.1.0 in our
# CI (sign-installer.yml `provenance` job) emits provenance bundles with
# tlog entry type `dsse:0.0.1`. slsa-verifier v2.6.0 was pinned at the
# Wave-3 spec but rejects that entry type at runtime with:
#   FAILED: matching bundle entry with content: unexpected tlog entry
#   type: expected intoto:0.0.2, got dsse:0.0.1
# slsa-verifier v2.7.0 accepts BOTH `intoto:0.0.2` and `dsse:0.0.1`,
# matching the format produced by the v2.1.0 generator we are pinned to.
# This is the version slsa-github-generator v2.1.0 itself uses in its
# generate-builder.sh (VERIFIER_RELEASE=v2.7.0), so it is the upstream-
# blessed pairing.
#
# The SLSA project's own release workflow signs the binaries with
# Fulcio and records them in Rekor, but we deliberately pin by SHA-256
# rather than re-verify the signatures: like cosign in [2/13], this is
# the bootstrap-trust ceiling and the hash is independently verifiable
# from the upstream Releases page.
readonly SLSA_VERIFIER_VERSION="v2.7.0"
readonly SLSA_VERIFIER_SHA256_LINUX_AMD64="499befb675efcca9001afe6e5156891b91e71f9c07ab120a8943979f85cc82e6"
readonly SLSA_VERIFIER_SHA256_LINUX_ARM64="dc3845d7605f666a0938389c1c5735230e50b32a547867ffd351fb14df928167"
readonly SLSA_VERIFIER_SHA256_DARWIN_AMD64="36694b43ab23be234add09272e5faf77349d7e267bf65c01dc9bcdf58c4f496e"
readonly SLSA_VERIFIER_SHA256_DARWIN_ARM64="84d9122ce12e0c79080844285fd5c4976407ed3463e434a1b21b0979c46b1e55"

# Expected source URI passed to `slsa-verifier verify-artifact --source-uri`.
# This must match the repository whose Actions identity built+attested the
# release. An attacker who builds + attests under a different repo's OIDC
# identity (even with a valid SLSA L3 provenance) will fail verification.
readonly EXPECTED_SLSA_SOURCE_URI="github.com/${EXPECTED_REPO}"

# Expected Fulcio identity_regexp inside install/in-toto/layout-content-anchor.json.
# Wave-5 audit Finding 3: the layout doc is UNSIGNED — it is a content-
# anchor only. The constant below is the actual source of truth, baked
# into this signed verifier. We byte-compare the layout's regexp against
# this constant on every release; drift means either the layout was
# modified or the pin is stale; both are fail-closed.
readonly EXPECTED_INTOTO_IDENTITY_REGEXP='^https://github\.com/PersonalJarvis/PersonalJarvis/\.github/workflows/sign-installer\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._-]+)?$'

# Filename of the SLSA L3 provenance attestation emitted by the workflow.
# Decoupled from the artifact name so the verifier survives a workflow
# rename; the release uploads the file under exactly this name.
readonly SLSA_PROVENANCE_FILENAME="personal-jarvis.intoto.jsonl"

# Filename of the content-anchor layout assertion uploaded to the
# release. Wave-5 audit Finding 3 renamed this from `layout.template.json`
# to `layout-content-anchor.json` to remove the implicit "in-toto signed
# layout" overclaim — the document is in-toto-shaped but UNSIGNED; the
# authenticity comes from the verifier byte-comparing the constant
# against the asserted identity_regexp. SA-2 owns the workflow contract.
readonly INTOTO_LAYOUT_FILENAME="layout-content-anchor.json"

# ----------------------------------------------------------------------- ui
if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); CYAN=$(printf '\033[36m')
    YELLOW=$(printf '\033[33m'); GREEN=$(printf '\033[32m')
    RED=$(printf '\033[31m'); RESET=$(printf '\033[0m')
    # Brand palette (docs/BRAND.md): forged-gold gradient, matching install.sh.
    GOLD_HI=$(printf '\033[38;2;255;229;82m'); GOLD=$(printf '\033[38;2;255;214;10m')
    GOLD_DEEP=$(printf '\033[38;2;184;150;10m'); DIM=$(printf '\033[38;2;143;143;143m')
else
    BOLD=""; CYAN=""; YELLOW=""; GREEN=""; RED=""; RESET=""
    GOLD_HI=""; GOLD=""; GOLD_DEEP=""; DIM=""
fi

log()  { printf '%s\n' "$*"; }
note() { printf '%s%s%s\n' "${YELLOW}" "$*" "${RESET}"; }
ok()   { printf '%s%s%s\n' "${GREEN}"  "$*" "${RESET}"; }
err()  { printf '%s%s%s\n' "${RED}"    "$*" "${RESET}" >&2; }

# Banner art is machine-generated (figlet "ANSI Shadow"); do not hand-edit —
# that is how the historical "Harvis" typo crept in.
cat <<EOF

${GOLD_HI}     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗${RESET}
${GOLD_HI}     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝${RESET}
${GOLD}     ██║███████║██████╔╝██║   ██║██║███████╗${RESET}
${GOLD}██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║${RESET}
${GOLD_DEEP}╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║${RESET}
${GOLD_DEEP} ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝${RESET}

${DIM}     P E R S O N A L  J A R V I S   ·   talk to your computer${RESET}

${GOLD}  ●${RESET} ${BOLD}Verifying installer · macOS / Linux${RESET}
${DIM}  Sigstore + offline ceremony + SLSA L3 + ML-DSA-65 (Wave 3)${RESET}

EOF

# ----------------------------------------------------------------------- tag resolution
# The verifier wrapper lives inside a specific tagged release. We can recover
# the tag from $JARVIS_INSTALL_TAG, or fall back to "latest" — but "latest"
# is a moving target, so we prefer an explicit pin.
note "[0/13] Resolving release tag..."
TAG="${JARVIS_INSTALL_TAG:-}"
if [ -z "$TAG" ]; then
    log "      JARVIS_INSTALL_TAG not set — resolving latest release..."
    # GitHub's "latest release" endpoint replies with a 302 redirect to
    # /releases/tag/<vX.Y.Z>. We HEAD it without following so we can
    # capture the Location header. (Using `-L`+`-w '%{redirect_url}'`
    # would report the empty next-hop after the redirect chain ended.)
    LATEST_LOCATION=$(curl -fsS -o /dev/null -D - "https://github.com/${EXPECTED_REPO}/releases/latest" \
                       | tr -d '\r' | awk 'BEGIN{IGNORECASE=1} /^location:/ {print $2; exit}')
    if [ -n "$LATEST_LOCATION" ]; then
        TAG="${LATEST_LOCATION##*/tag/}"
    fi
    if [ -z "${TAG:-}" ]; then
        err "  GitHub returned no tag — has the project ever published a release?"
        err "  set JARVIS_INSTALL_TAG explicitly, e.g. JARVIS_INSTALL_TAG=v0.2.0-supplychain-wave1"
        exit 1
    fi
fi
ok "      Tag pinned: ${BOLD}$TAG${RESET}"

# ----------------------------------------------------------------------- staging area
STAGING="$(mktemp -d -t jarvis-install-verify.XXXXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
umask 077
log "      Staging: $STAGING"

# ----------------------------------------------------------------------- platform detect
note ""
note "[1/13] Detecting platform..."

UNAME_S=$(uname -s)
UNAME_M=$(uname -m)
case "$UNAME_S/$UNAME_M" in
    Linux/x86_64)
        COSIGN_ASSET="cosign-linux-amd64";  COSIGN_SHA256="$COSIGN_SHA256_LINUX_AMD64"
        SLSA_VERIFIER_ASSET="slsa-verifier-linux-amd64"; SLSA_VERIFIER_SHA256="$SLSA_VERIFIER_SHA256_LINUX_AMD64"
        ;;
    Linux/aarch64|Linux/arm64)
        COSIGN_ASSET="cosign-linux-arm64"; COSIGN_SHA256="$COSIGN_SHA256_LINUX_ARM64"
        SLSA_VERIFIER_ASSET="slsa-verifier-linux-arm64"; SLSA_VERIFIER_SHA256="$SLSA_VERIFIER_SHA256_LINUX_ARM64"
        ;;
    Darwin/x86_64)
        COSIGN_ASSET="cosign-darwin-amd64"; COSIGN_SHA256="$COSIGN_SHA256_DARWIN_AMD64"
        SLSA_VERIFIER_ASSET="slsa-verifier-darwin-amd64"; SLSA_VERIFIER_SHA256="$SLSA_VERIFIER_SHA256_DARWIN_AMD64"
        ;;
    Darwin/arm64)
        COSIGN_ASSET="cosign-darwin-arm64"; COSIGN_SHA256="$COSIGN_SHA256_DARWIN_ARM64"
        SLSA_VERIFIER_ASSET="slsa-verifier-darwin-arm64"; SLSA_VERIFIER_SHA256="$SLSA_VERIFIER_SHA256_DARWIN_ARM64"
        ;;
    *)
        err "  unsupported platform: $UNAME_S/$UNAME_M"
        err "  this verifier supports linux-amd64, linux-arm64, darwin-amd64, darwin-arm64"
        exit 1
        ;;
esac
ok "      Platform: $UNAME_S/$UNAME_M → cosign=$COSIGN_ASSET, slsa-verifier=$SLSA_VERIFIER_ASSET"

# ----------------------------------------------------------------------- fetch cosign
note ""
note "[2/13] Bootstrapping cosign $COSIGN_VERSION (SHA-256 pinned)..."

COSIGN_BIN="$STAGING/cosign"
COSIGN_URL="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/${COSIGN_ASSET}"

if ! curl -fsSL --retry 3 --retry-delay 2 -o "$COSIGN_BIN" "$COSIGN_URL"; then
    err "  failed to download cosign from $COSIGN_URL"
    exit 1
fi

# Pinned-hash check. This is the bootstrap anchor of the entire trust chain;
# without it everything below is theatre.
ACTUAL_SHA=$(sha256sum "$COSIGN_BIN" | awk '{print $1}')
if [ "$ACTUAL_SHA" != "$COSIGN_SHA256" ]; then
    err "  cosign SHA-256 mismatch!"
    err "    expected: $COSIGN_SHA256"
    err "    actual:   $ACTUAL_SHA"
    err "  abort — the downloaded cosign is NOT the version this verifier was rooted against."
    err "  if this is reproducible, your network or GitHub release is compromised."
    exit 1
fi
chmod +x "$COSIGN_BIN"
ok "      cosign SHA-256 OK ($COSIGN_SHA256)"

# ----------------------------------------------------------------------- fetch artifact + signatures
note ""
note "[3/13] Fetching install.sh + Fulcio trio + offline-ceremony signature from release $TAG..."

ARTIFACT="$STAGING/install.sh"
SIG="$STAGING/install.sh.sig"
PEM="$STAGING/install.sh.pem"
BUNDLE="$STAGING/install.sh.bundle"
COSIGN_SIG="$STAGING/install.sh.cosign.sig"
RELEASED_PUBKEY="$STAGING/offline-ceremony.pub.released"
INLINED_PUBKEY="$STAGING/offline-ceremony.pub.inlined"

# Write the inlined pubkey to disk; cosign --key wants a file path.
printf '%s\n' "$OFFLINE_CEREMONY_PUBKEY_PEM" > "$INLINED_PUBKEY"

REL_BASE="https://github.com/${EXPECTED_REPO}/releases/download/${TAG}"
# install.sh.cosign.sig is the Wave-2 axis-B signature. offline-ceremony.pub
# is published as a release asset so users can cross-check the inlined copy.
for filename in install.sh install.sh.sig install.sh.pem install.sh.bundle install.sh.cosign.sig; do
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/$filename" "$REL_BASE/$filename"; then
        err "  failed to fetch $REL_BASE/$filename"
        err "  is the tag '$TAG' actually a Wave-2 signed release? See:"
        err "    https://github.com/${EXPECTED_REPO}/releases/tag/$TAG"
        exit 1
    fi
done

# Fetch the released copy of the offline-ceremony public key and assert it
# matches the inlined one byte-for-byte (DER-form SHA-256). This is the FIRST
# tamper detection — if someone swaps the published .pub for a different one,
# this catches it BEFORE any signature math.
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$RELEASED_PUBKEY" "$REL_BASE/offline-ceremony.pub"; then
    err "  failed to fetch $REL_BASE/offline-ceremony.pub"
    err "  the release MUST publish the offline-ceremony public key as a cross-check asset."
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    err "  openssl is required for public-key fingerprint comparison — please install openssl and retry."
    exit 1
fi

INLINED_FP=$(openssl pkey -in "$INLINED_PUBKEY"  -pubin -outform DER 2>/dev/null | openssl dgst -sha256 | awk '{print $NF}')
RELEASED_FP=$(openssl pkey -in "$RELEASED_PUBKEY" -pubin -outform DER 2>/dev/null | openssl dgst -sha256 | awk '{print $NF}')

if [ -z "${INLINED_FP:-}" ] || [ -z "${RELEASED_FP:-}" ]; then
    err "  could not compute fingerprint of inlined or released offline-ceremony.pub"
    err "  inlined:  ${INLINED_FP:-<empty>}"
    err "  released: ${RELEASED_FP:-<empty>}"
    exit 1
fi
if [ "$INLINED_FP" != "$OFFLINE_CEREMONY_PUBKEY_FINGERPRINT" ]; then
    err "  inlined offline-ceremony pubkey fingerprint mismatch!"
    err "    expected (pinned in verifier): $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT"
    err "    actual   (heredoc):            $INLINED_FP"
    err "  this script has been tampered with — refusing."
    exit 1
fi
if [ "$RELEASED_FP" != "$OFFLINE_CEREMONY_PUBKEY_FINGERPRINT" ]; then
    err "  released offline-ceremony pubkey fingerprint mismatch!"
    err "    expected (pinned in verifier): $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT"
    err "    actual   (release asset):      $RELEASED_FP"
    err "  the published offline-ceremony.pub does NOT match the verifier's pin —"
    err "  refusing. If the key was legitimately rotated, this verifier must be"
    err "  re-released with the new fingerprint before users will accept it."
    exit 1
fi
ok "      install.sh + .sig + .pem + .bundle + .cosign.sig downloaded"
ok "      offline-ceremony pubkey fingerprint OK ($OFFLINE_CEREMONY_PUBKEY_FINGERPRINT)"

# ----------------------------------------------------------------------- verify Fulcio (axis A)
note ""
note "[4/13] Verifying Fulcio keyless signature (axis A — GitHub Actions OIDC)..."

# The identity regex pins exactly: <repo>/<workflow path>@refs/tags/<some semver-ish tag>.
# Anchored with ^...$ to refuse near-matches.
IDENTITY_REGEX="^https://github.com/${EXPECTED_REPO}/${EXPECTED_WORKFLOW_PATH}@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._-]+)?$"

# We pass --insecure-ignore-tlog=false explicitly to make the dependency on
# Rekor inclusion *explicit*. If cosign ever flips its default, we don't
# silently weaken.
# AXIS-A INVOCATION: cosign verify-blob (Fulcio keyless, with bundle + tlog)
if ! "$COSIGN_BIN" verify-blob \
        --certificate                  "$PEM" \
        --signature                    "$SIG" \
        --bundle                       "$BUNDLE" \
        --certificate-identity-regexp  "$IDENTITY_REGEX" \
        --certificate-oidc-issuer      "$EXPECTED_OIDC_ISSUER" \
        --insecure-ignore-tlog=false \
        "$ARTIFACT"; then
    err "  axis A: cosign verification FAILED."
    err "  the downloaded install.sh is NOT signed by ${EXPECTED_REPO}'s release workflow."
    err "  refusing to execute."
    exit 1
fi
ok "      axis A OK (identity=${EXPECTED_REPO} / ${EXPECTED_WORKFLOW_PATH}, issuer=$EXPECTED_OIDC_ISSUER)"

# ----------------------------------------------------------------------- verify offline ceremony (axis B)
note ""
note "[5/13] Verifying offline-ceremony signature (axis B — Ed25519, air-gapped)..."

# cosign verification in --key mode does NOT consult Rekor — this is a pure
# detached-signature check against the pinned Ed25519 public key. That is
# correct: Rekor inclusion is enforced once, via axis A's bundle. Axis B's
# job is to provide an independent trust path, not to duplicate transparency.
# AXIS-B INVOCATION: cosign verify-blob (offline ceremony, --key Ed25519, no tlog)
# --insecure-ignore-tlog is intentional here: key-based cosign signatures
# are NOT uploaded to Rekor (no Fulcio cert to bind them to). Without
# this flag, cosign tries to look the signature up in Rekor and Rekor
# rejects with "unsupported hash algorithm: SHA-256 not in [SHA-512]"
# because Ed25519 mandates SHA-512 internally (RFC 8032) — see the
# matching --insecure-ignore-tlog in the signing workflow's
# "Independently verify Wave 2" step.
if ! "$COSIGN_BIN" verify-blob \
        --key       "$INLINED_PUBKEY" \
        --signature "$COSIGN_SIG" \
        --insecure-ignore-tlog \
        "$ARTIFACT"; then
    err "  axis B: offline-ceremony signature check FAILED."
    err "  install.sh.cosign.sig does NOT validate against the pinned Ed25519 pubkey"
    err "  (fingerprint $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT)."
    err "  refusing to execute — Wave 3 demands ALL THREE axes to pass."
    exit 1
fi
ok "      axis B OK (Ed25519, key fingerprint=$OFFLINE_CEREMONY_PUBKEY_FINGERPRINT)"

# ----------------------------------------------------------------------- freshness
note ""
note "[6/13] Checking Rekor inclusion proof freshness (≤ ${REKOR_MAX_AGE_SECONDS}s)..."

# The Sigstore bundle carries the Rekor inclusion proof + integrated time.
# We parse the bundle JSON for the integrated timestamp and reject anything
# older than REKOR_MAX_AGE_SECONDS. This defends against the "old signature
# replay" attack (signed binary served from an attacker-controlled mirror
# even though the maintainer rotated keys / revoked the release).
#
# Freshness applies to axis A (Fulcio + Rekor). Axis B is a detached
# signature with no transparency log; its replay defence comes from the
# fact that an old install.sh.cosign.sig still needs to match the CURRENT
# install.sh bytes — any tamper invalidates the Ed25519 signature.
#
# The bundle schema is well-defined: $.verificationMaterial.tlogEntries[0].integratedTime
# is the Rekor-side timestamp in Unix seconds (string).
if ! command -v python3 >/dev/null 2>&1; then
    err "  python3 is required for freshness check — please install python3 (>= 3.6) and retry."
    exit 1
fi

INTEGRATED_TIME=$(python3 - "$BUNDLE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as fh:
    bundle = json.load(fh)
entries = bundle.get("verificationMaterial", {}).get("tlogEntries", [])
if not entries:
    # Older cosign bundles store the log info differently — try the
    # rekorBundle / Payload shape as a fallback.
    rekor = bundle.get("rekorBundle") or {}
    payload = rekor.get("Payload") or {}
    t = payload.get("integratedTime")
    if t is None:
        sys.exit("no Rekor inclusion proof found in bundle (entries empty AND no rekorBundle.Payload.integratedTime)")
    print(int(t))
else:
    t = entries[0].get("integratedTime")
    if t is None:
        sys.exit("tlogEntries[0].integratedTime missing")
    print(int(t))
PYEOF
)
if [ -z "${INTEGRATED_TIME:-}" ]; then
    err "  could not parse Rekor integrated time from bundle — refusing to proceed."
    exit 1
fi
NOW=$(date -u +%s)
AGE=$(( NOW - INTEGRATED_TIME ))
if [ "$AGE" -lt 0 ]; then
    err "  Rekor integrated time is in the FUTURE (now=$NOW, integrated=$INTEGRATED_TIME)."
    err "  this is either a clock-skew problem or a clear forgery — refusing."
    exit 1
fi
if [ "$AGE" -gt "$REKOR_MAX_AGE_SECONDS" ]; then
    err "  Rekor inclusion proof is too old: ${AGE}s > ${REKOR_MAX_AGE_SECONDS}s"
    err "  this signature might be a replay of an old, revoked release."
    err "  if this is a legitimately-old release you want to install anyway,"
    err "  set JARVIS_INSTALL_ALLOW_STALE=1 and re-run — but read TRUST_ROOT.md first."
    if [ "${JARVIS_INSTALL_ALLOW_STALE:-0}" != "1" ]; then
        exit 1
    fi
    err "  proceeding under JARVIS_INSTALL_ALLOW_STALE=1 (override acknowledged)."
fi
ok "      Rekor inclusion proof age: ${AGE}s (limit ${REKOR_MAX_AGE_SECONDS}s)"

# ----------------------------------------------------------------------- identity cross-check
note ""
note "[7/13] Cross-checking identity assertions on both axes..."

# Axis A: re-extract the Subject Alternative Name from the Fulcio cert and
# assert it matches the same pinned identity regex. The cosign tool
# already checks this, but we re-assert here to make the contract explicit
# and to catch the (paranoid) case where cosign exits 0 yet the SAN drifted
# from what the regex would have accepted at a different cosign version.
if ! command -v openssl >/dev/null 2>&1; then
    err "  openssl is required for identity cross-check — please install openssl and retry."
    exit 1
fi

# cosign uploads the Fulcio cert as a single-line base64 blob (the
# raw PEM is base64-wrapped again on disk). cosign verify-blob accepts
# either form transparently, but `openssl x509 -in` only reads real
# PEM. Detect the encoding: if the file starts with `-----BEGIN`,
# it's already PEM; otherwise it's base64-of-PEM and we decode first.
PEM_FOR_OPENSSL="$PEM"
if ! head -c 11 "$PEM" | grep -q '^-----BEGIN'; then
    PEM_FOR_OPENSSL="$STAGING/install.sh.pem.decoded"
    if ! base64 -d "$PEM" > "$PEM_FOR_OPENSSL" 2>/dev/null; then
        err "  Fulcio cert is neither raw PEM nor base64-of-PEM — refusing."
        exit 1
    fi
fi
CERT_SAN=$(openssl x509 -in "$PEM_FOR_OPENSSL" -noout -ext subjectAltName 2>/dev/null \
           | awk '/URI:/ {for(i=1;i<=NF;i++) if($i ~ /^URI:/){sub(/^URI:/,"",$i); sub(/,$/,"",$i); print $i; exit}}')
if [ -z "${CERT_SAN:-}" ]; then
    err "  could not extract SAN URI from Fulcio cert — refusing."
    exit 1
fi
if ! printf '%s\n' "$CERT_SAN" | grep -Eq "$IDENTITY_REGEX"; then
    err "  axis A SAN cross-check FAILED."
    err "    SAN:    $CERT_SAN"
    err "    regex:  $IDENTITY_REGEX"
    err "  refusing to execute."
    exit 1
fi

# WAVE 5 TAG-BINDING — Wave-5 audit Finding 1 (downgrade-replay defense).
#
# The Fulcio cert SAN carries the exact tag the workflow ran against (e.g.
# ".../sign-installer.yml@refs/tags/v0.5.1-supplychain-wave5"). The IDENTITY_REGEX
# above only checks that SOME semver-ish tag is present in the SAN — not that
# the SAN tag matches the tag the operator asked us to install. Without this
# cross-check, an attacker who serves the (valid-signed) install.sh from a
# PRIOR release under a fresh URL would pass axes A+B+C+D — the freshness
# gate (Rekor integratedTime <= 24h) is the only barrier and ages out within
# a day.
#
# The defense: extract the @refs/tags/<X> suffix from the SAN, compare BYTE-
# FOR-BYTE against the resolved $TAG. Drift => fail-closed with a clear
# diagnostic. Pulling install-verify.sh.sig + .pem + .bundle from a different
# tag than requested is treated as a downgrade-replay attempt.
SAN_TAG="${CERT_SAN##*@refs/tags/}"
if [ "$SAN_TAG" = "$CERT_SAN" ]; then
    err "  axis A: could not extract @refs/tags/<tag> suffix from SAN — refusing."
    err "    SAN: $CERT_SAN"
    exit 1
fi
if [ "$SAN_TAG" != "$TAG" ]; then
    err "  axis A: SAN tag $SAN_TAG does not match requested tag $TAG — refusing (possible downgrade replay)."
    err "    SAN:           $CERT_SAN"
    err "    SAN tag:       $SAN_TAG"
    err "    requested tag: $TAG"
    err "  this defends against an attacker serving valid-signed bytes from a"
    err "  different release at a fresh URL — see TRUST_ROOT.md axis E."
    exit 1
fi
ok "      axis A tag-binding OK (SAN tag = requested tag = $TAG)"

# Axis B: the inlined-pubkey fingerprint was already pinned-checked in [3/13].
# Here we re-assert against the on-disk file cosign actually used in [5/13]
# — defense against an attacker who somehow swapped $INLINED_PUBKEY between
# stages (race conditions, tmpfs games, etc.).
FINAL_FP=$(openssl pkey -in "$INLINED_PUBKEY" -pubin -outform DER 2>/dev/null | openssl dgst -sha256 | awk '{print $NF}')
if [ "$FINAL_FP" != "$OFFLINE_CEREMONY_PUBKEY_FINGERPRINT" ]; then
    err "  axis B fingerprint drifted between stages!"
    err "    pinned:       $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT"
    err "    on-disk now:  $FINAL_FP"
    err "  refusing."
    exit 1
fi
ok "      axis A SAN matches pinned regex: $CERT_SAN"
ok "      axis B key fingerprint stable:    $FINAL_FP"

# ----------------------------------------------------------------------- bootstrap slsa-verifier
note ""
note "[8/13] Bootstrapping slsa-verifier $SLSA_VERIFIER_VERSION (SHA-256 pinned)..."

# Same trust pattern as cosign in [2/13]: SHA-256-pinned binary download.
# slsa-verifier is the reference implementation that knows how to verify
# SLSA L3 build provenance against a release. We refuse to execute the
# downloaded binary unless its hash matches the pin recorded in
# TRUST_ROOT.md §4.
SLSA_VERIFIER_BIN="$STAGING/slsa-verifier"
SLSA_VERIFIER_URL="https://github.com/slsa-framework/slsa-verifier/releases/download/${SLSA_VERIFIER_VERSION}/${SLSA_VERIFIER_ASSET}"

if ! curl -fsSL --retry 3 --retry-delay 2 -o "$SLSA_VERIFIER_BIN" "$SLSA_VERIFIER_URL"; then
    err "  failed to download slsa-verifier from $SLSA_VERIFIER_URL"
    exit 1
fi

ACTUAL_SLSA_SHA=$(sha256sum "$SLSA_VERIFIER_BIN" | awk '{print $1}')
if [ "$ACTUAL_SLSA_SHA" != "$SLSA_VERIFIER_SHA256" ]; then
    err "  slsa-verifier SHA-256 mismatch!"
    err "    expected: $SLSA_VERIFIER_SHA256"
    err "    actual:   $ACTUAL_SLSA_SHA"
    err "  abort — the downloaded slsa-verifier is NOT the version this verifier was rooted against."
    err "  if this is reproducible, your network or the slsa-framework release is compromised."
    exit 1
fi
chmod +x "$SLSA_VERIFIER_BIN"
ok "      slsa-verifier SHA-256 OK ($SLSA_VERIFIER_SHA256)"

# ----------------------------------------------------------------------- SLSA L3 provenance (axis C, part 1)
note ""
note "[9/13] Verifying SLSA L3 build provenance (axis C — independent attestation of build environment)..."

# The workflow uploads a SLSA L3 in-toto provenance attestation alongside
# the artifacts under the well-known name $SLSA_PROVENANCE_FILENAME. It is
# generated by the SLSA GitHub generator (slsa-framework/slsa-github-generator)
# which itself runs in a hardened reusable workflow with a non-falsifiable
# builder identity — the provenance's "builder.id" cannot be set by the
# calling repo, so an attacker who poisons sign-installer.yml still cannot
# emit a provenance that claims a different builder.
#
# Axis C catches a class of attacks axis A cannot: an attacker who steals
# an OIDC token (or the ability to mint Fulcio certs under our identity)
# can re-sign a tampered binary with the same identity, defeating axis A.
# But the SLSA provenance records the ENTIRE build environment — sources,
# inputs, build commands, runner image. Changed inputs => changed digests
# => slsa-verifier rejects.
SLSA_PROVENANCE_PATH="$STAGING/$SLSA_PROVENANCE_FILENAME"
PROVENANCE_URL="$REL_BASE/$SLSA_PROVENANCE_FILENAME"

if ! curl -fsSL --retry 3 --retry-delay 2 -o "$SLSA_PROVENANCE_PATH" "$PROVENANCE_URL"; then
    err "  failed to fetch SLSA provenance from $PROVENANCE_URL"
    err "  is the tag '$TAG' actually a Wave-3 release with SLSA L3 provenance?"
    exit 1
fi
ok "      SLSA provenance downloaded ($SLSA_PROVENANCE_FILENAME)"

# AXIS-C INVOCATION (part 1): slsa-verifier verify-artifact pins:
#   --source-uri:  the repo whose build emitted the provenance (must match
#                  our EXPECTED_REPO)
#   --source-tag:  the exact release tag the provenance was generated for
#                  (defends against provenance-from-old-tag replay)
#   positional:    the artifact whose digest must appear in the provenance's
#                  `subject` array
# slsa-verifier internally fetches the SLSA generator's builder identity
# from the bundled Sigstore cert, cross-checks it against the SLSA
# generator's own pinned issuer/identity, and rejects any mismatch. A
# failure here means the artifact was NOT produced by the attested build,
# OR the provenance is for a different tag, OR the source-repo does not
# match. All three are fail-closed.
if ! "$SLSA_VERIFIER_BIN" verify-artifact \
        --provenance-path "$SLSA_PROVENANCE_PATH" \
        --source-uri      "$EXPECTED_SLSA_SOURCE_URI" \
        --source-tag      "$TAG" \
        "$ARTIFACT"; then
    err "  axis C (SLSA L3): slsa-verifier verify-artifact FAILED."
    err "  the SLSA provenance does NOT attest to a build of install.sh"
    err "  from $EXPECTED_SLSA_SOURCE_URI @ tag $TAG."
    err "  refusing to execute — Wave 3 demands 3-of-3 axes to pass."
    exit 1
fi
ok "      axis C OK (SLSA L3: source=$EXPECTED_SLSA_SOURCE_URI, tag=$TAG)"

# ----------------------------------------------------------------------- content-anchor layout pin (axis C, part 2)
note ""
note "[10/13] Verifying content-anchor layout functionary pin (axis C — supply-chain layout match)..."
# WAVE-5 HONESTY NOTE (audit Finding 3): the layout document is in-toto-
# shaped but UNSIGNED — authenticity comes from this signed verifier
# byte-comparing its identity_regexp against EXPECTED_INTOTO_IDENTITY_REGEXP
# below. The document is NOT a spec-compliant in-toto signed layout.
# Renamed from `in-toto layout pin` to remove the implicit overclaim.

# Fetch the in-toto layout template uploaded alongside the artifacts. SA-2
# is responsible for uploading $INTOTO_LAYOUT_FILENAME to the same release.
# The layout declares which functionary identity is allowed to sign the
# build step. We cross-check that:
#
#   (a) the layout's functionary identity_regexp equals our pinned expected
#       string (EXPECTED_INTOTO_IDENTITY_REGEXP) — defends against the
#       attacker who swaps the layout in the release to widen the regexp;
#   (b) the regexp is NOT ".*" or any other catch-all (sanity check that
#       would also catch a typoed or maliciously-loosened pin);
#   (c) the issuer URL matches EXPECTED_OIDC_ISSUER, tying the layout's
#       functionary back to the same OIDC root axis A trusts.
#
# A discrepancy between the layout-as-uploaded and the layout-as-pinned
# means the supply-chain layout has been tampered with — fail-closed.
LAYOUT_PATH="$STAGING/$INTOTO_LAYOUT_FILENAME"
LAYOUT_URL="$REL_BASE/$INTOTO_LAYOUT_FILENAME"

if ! curl -fsSL --retry 3 --retry-delay 2 -o "$LAYOUT_PATH" "$LAYOUT_URL"; then
    err "  failed to fetch in-toto layout from $LAYOUT_URL"
    err "  is the release missing the layout.template.json upload?"
    exit 1
fi

# Parse the layout JSON. We use python3 (already required by [6/13]) for
# robust JSON parsing rather than grep — a regex would be fragile against
# whitespace, key-ordering, and JSON-encoded special characters.
if ! command -v python3 >/dev/null 2>&1; then
    err "  python3 is required for in-toto layout parsing — please install python3 (>= 3.6) and retry."
    exit 1
fi

LAYOUT_RC=0
LAYOUT_INSPECTION=$(python3 - "$LAYOUT_PATH" "$EXPECTED_INTOTO_IDENTITY_REGEXP" "$EXPECTED_OIDC_ISSUER" <<'PYEOF'
import json, sys
layout_path = sys.argv[1]
expected_regexp = sys.argv[2]
expected_issuer = sys.argv[3]

with open(layout_path, "r", encoding="utf-8") as fh:
    layout = json.load(fh)

# Wave-5 audit Finding 3: the document is renamed `_type` from "layout"
# (which implies a signed in-toto layout per the spec) to "content-anchor"
# (an honest description of what it actually is: an unsigned regexp-pin
# that is byte-compared against the signed verifier's hard-coded constant).
# Accept either value during the v0.5 → v0.6 transition; require
# content-anchor going forward.
_t = layout.get("_type")
if _t not in ("content-anchor", "layout"):
    sys.exit("layout._type not in {'content-anchor','layout'} (got " + repr(_t) + ")")

keys = layout.get("keys") or {}
if not keys:
    sys.exit("layout.keys is empty - no functionary pinned")

# Walk every declared key and find the Sigstore/OIDC functionary entry.
# We accept exactly one such functionary (more would mean the layout
# allows multiple build identities, which we did not authorise).
oidc_entries = []
for keyid, kdef in keys.items():
    keyval = kdef.get("keyval") or {}
    if "identity_regexp" in keyval or "issuer" in keyval:
        oidc_entries.append((keyid, keyval))

if not oidc_entries:
    sys.exit("layout has no OIDC functionary entry (looking for keyval.identity_regexp)")
if len(oidc_entries) != 1:
    sys.exit("layout has " + str(len(oidc_entries)) + " OIDC functionary entries; exactly 1 required")

keyid, keyval = oidc_entries[0]
actual_regexp = keyval.get("identity_regexp")
actual_issuer = keyval.get("issuer")

if actual_regexp is None:
    sys.exit("functionary " + keyid + " missing keyval.identity_regexp")
if actual_issuer is None:
    sys.exit("functionary " + keyid + " missing keyval.issuer")

# Anti-corner-cutting: reject catch-all regexps explicitly. If a future
# layout author tries to "simplify" the pin with .* this stage breaks
# loudly instead of silently accepting any signer.
catchalls = (".*", ".+", "^.*$", "^.+$", "")
if actual_regexp in catchalls:
    sys.exit("functionary identity_regexp is a catch-all (" + repr(actual_regexp) + ") - refusing")

if actual_regexp != expected_regexp:
    sys.exit("identity_regexp mismatch:\n  expected: " + expected_regexp + "\n  actual:   " + actual_regexp)
if actual_issuer != expected_issuer:
    sys.exit("issuer mismatch:\n  expected: " + expected_issuer + "\n  actual:   " + actual_issuer)

print("OK keyid=" + keyid)
PYEOF
) || LAYOUT_RC=$?
if [ "$LAYOUT_RC" -ne 0 ]; then
    err "  axis C (in-toto layout pin): FAILED."
    err "  the layout fetched from the release does NOT match the pinned functionary."
    err "  details: ${LAYOUT_INSPECTION:-<no output from parser>}"
    err "  refusing to execute — supply-chain layout has drifted from the pin."
    exit 1
fi
if [ -z "${LAYOUT_INSPECTION:-}" ]; then
    err "  axis C (in-toto layout pin): parser produced no output — refusing."
    exit 1
fi
ok "      axis C OK (in-toto layout: $LAYOUT_INSPECTION)"
ok "      identity_regexp pin: $EXPECTED_INTOTO_IDENTITY_REGEXP"

# ----------------------------------------------------------------------- classical axes complete
note ""
note "[11/13] All classical axes passed (axes A+B+C). Entering Wave 4 PQ transition..."
log ""

# ----------------------------------------------------------------------- post-quantum signature fetch (axis D, part 1)
note "[12/13] Fetching ML-DSA-65 post-quantum signature + released pubkey (axis D — FIPS 204)..."

# Released-asset filenames follow the workflow's upload convention:
#   install.sh.mldsa.sig       — ML-DSA-65 raw signature (~3309 bytes)
#   pq-mldsa65.pub.pem         — pubkey asset for fingerprint cross-check
MLDSA_SIG="$STAGING/install.sh${PQ_MLDSA65_SIG_EXT}"
PQ_INLINED_PUBKEY="$STAGING/pq-mldsa65.pub.inlined"
PQ_RELEASED_PUBKEY="$STAGING/pq-mldsa65.pub.released"

# Write the inlined pubkey to disk; `openssl pkeyutl -verify` wants a
# file path.
printf '%s\n' "$PQ_MLDSA65_PUBKEY_PEM" > "$PQ_INLINED_PUBKEY"

# Fetch the signature. Failure here means the release was published WITHOUT
# the Wave 4 PQ asset; we treat that as a hard failure (the release notes
# claim Wave 4 coverage) but expose JARVIS_INSTALL_ALLOW_NO_PQ=1 as an
# operator override for tags cut before Wave 4 wiring landed — the override
# is loud-logged so an auditor sees it in the transcript.
PQ_SIG_AVAILABLE=1
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$MLDSA_SIG" "$REL_BASE/install.sh${PQ_MLDSA65_SIG_EXT}"; then
    err "  failed to fetch $REL_BASE/install.sh${PQ_MLDSA65_SIG_EXT}"
    err "  this release does NOT publish a Wave 4 ML-DSA-65 signature for install.sh."
    if [ "${JARVIS_INSTALL_ALLOW_NO_PQ:-0}" != "1" ]; then
        err "  refusing — Wave 4 axis D requires <artifact>.mldsa.sig per release."
        err "  if this is a legacy (pre-Wave-4) tag, set JARVIS_INSTALL_ALLOW_NO_PQ=1 to bypass"
        err "  axis D (classical axes A+B+C still enforced); read TRUST_ROOT.md §5 first."
        exit 1
    fi
    err "  proceeding under JARVIS_INSTALL_ALLOW_NO_PQ=1 (override acknowledged; axis D bypassed)."
    PQ_SIG_AVAILABLE=0
fi

# Fetch released pubkey and assert fingerprint equality vs inlined.
PQ_PUBKEY_RELEASED_AVAILABLE=1
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$PQ_RELEASED_PUBKEY" "$REL_BASE/${PQ_MLDSA65_PUBKEY_ASSET_NAME}"; then
    err "  failed to fetch $REL_BASE/${PQ_MLDSA65_PUBKEY_ASSET_NAME}"
    err "  the release MUST publish the ML-DSA-65 public key as a cross-check asset."
    if [ "${JARVIS_INSTALL_ALLOW_NO_PQ:-0}" != "1" ]; then
        exit 1
    fi
    err "  proceeding under JARVIS_INSTALL_ALLOW_NO_PQ=1 (override acknowledged)."
    PQ_PUBKEY_RELEASED_AVAILABLE=0
fi

# Inlined-pubkey fingerprint cross-check (defense vs in-script tamper).
# openssl was already asserted present at stage [3/13] via the offline-
# ceremony cross-check, so we re-use it here unconditionally.
#
# NOTE (SA-5 fix-forward #3+#4, 2026-05-27): On OpenSSL < 3.5 the
# `openssl pkey -pubin` parse fails (binary doesn't know the ML-DSA-65
# SPKI OID 2.16.840.1.101.3.4.3.18). Two bugs were in flight here:
#   #3 (set -e killed the script silently)  — fixed with `|| true`.
#   #4 (an empty DER stream piped into `openssl dgst -sha256` produces
#       e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
#       — the SHA of zero bytes — which then doesn't match the pinned
#       fingerprint and the *tamper detection* fires instead of the
#       *transition-mode handler*).
# Fix #4: split the pipeline so we can inspect whether `openssl pkey`
# itself succeeded. If it failed → transition mode. If it succeeded →
# compute the SHA of the DER output and compare.
PQ_INLINED_DER_FILE="$STAGING/pq-mldsa65.inlined.der"
if openssl pkey -in "$PQ_INLINED_PUBKEY" -pubin -outform DER -out "$PQ_INLINED_DER_FILE" 2>/dev/null && [ -s "$PQ_INLINED_DER_FILE" ]; then
    PQ_INLINED_FP=$(openssl dgst -sha256 < "$PQ_INLINED_DER_FILE" 2>/dev/null | awk '{print $NF}' || true)
else
    err "  could not parse inlined ML-DSA-65 pubkey with local openssl."
    err "  the local openssl may not understand the ML-DSA-65 SPKI OID"
    err "  (2.16.840.1.101.3.4.3.18). Fingerprint cross-check requires"
    err "  openssl >= 3.5; on older toolchains this verifier degrades"
    err "  to TRANSITION MODE — axis D is SKIPPED with a warning."
    PQ_INLINED_FP=""
fi
if [ -n "${PQ_INLINED_FP:-}" ] && [ "$PQ_INLINED_FP" != "$PQ_MLDSA65_PUBKEY_FINGERPRINT" ]; then
    err "  inlined ML-DSA-65 pubkey fingerprint mismatch!"
    err "    expected (pinned in verifier): $PQ_MLDSA65_PUBKEY_FINGERPRINT"
    err "    actual   (heredoc):            $PQ_INLINED_FP"
    err "  this script has been tampered with — refusing."
    exit 1
fi
if [ "$PQ_PUBKEY_RELEASED_AVAILABLE" -eq 1 ] && [ -n "${PQ_INLINED_FP:-}" ]; then
    PQ_RELEASED_DER_FILE="$STAGING/pq-mldsa65.released.der"
    if openssl pkey -in "$PQ_RELEASED_PUBKEY" -pubin -outform DER -out "$PQ_RELEASED_DER_FILE" 2>/dev/null && [ -s "$PQ_RELEASED_DER_FILE" ]; then
        PQ_RELEASED_FP=$(openssl dgst -sha256 < "$PQ_RELEASED_DER_FILE" 2>/dev/null | awk '{print $NF}' || true)
    else
        PQ_RELEASED_FP=""
    fi
    if [ -z "${PQ_RELEASED_FP:-}" ]; then
        err "  could not compute fingerprint of released ML-DSA-65 pubkey (openssl too old?)."
        err "  axis D cross-check incomplete — refusing unless JARVIS_INSTALL_ALLOW_NO_PQ=1."
        if [ "${JARVIS_INSTALL_ALLOW_NO_PQ:-0}" != "1" ]; then
            exit 1
        fi
    elif [ "$PQ_RELEASED_FP" != "$PQ_MLDSA65_PUBKEY_FINGERPRINT" ]; then
        err "  released ML-DSA-65 pubkey fingerprint mismatch!"
        err "    expected (pinned in verifier): $PQ_MLDSA65_PUBKEY_FINGERPRINT"
        err "    actual   (release asset):      $PQ_RELEASED_FP"
        err "  the published pq-mldsa65.pub.pem does NOT match the verifier's pin — refusing."
        exit 1
    fi
fi
ok "      PQ signature fetched ($([ "$PQ_SIG_AVAILABLE" -eq 1 ] && echo "$MLDSA_SIG" || echo "BYPASSED via JARVIS_INSTALL_ALLOW_NO_PQ=1"))"
if [ -n "${PQ_INLINED_FP:-}" ]; then
    ok "      ML-DSA-65 pubkey fingerprint OK ($PQ_MLDSA65_PUBKEY_FINGERPRINT)"
fi

# ----------------------------------------------------------------------- ML-DSA-65 signature verify (axis D, part 2) + handoff
note ""
note "[13/13] Verifying ML-DSA-65 post-quantum signature (axis D) and handing off to install.sh..."

# TRANSITION MODE gate:
#   - If openssl >= 3.5 is on PATH: verify the ML-DSA-65 signature
#     hard-closed; any failure aborts.
#   - If openssl < 3.5 or missing: SKIP with a clear WARNING. The
#     classical axes A+B+C have already validated, so the installer is
#     still authenticated against three independent trust roots. The
#     warning is loud and explicit so the operator sees what is
#     happening — never silent.
PQ_VERIFY_RAN=0
if [ "$PQ_SIG_AVAILABLE" -eq 1 ]; then
    OPENSSL_VERSION_LINE=$(openssl version 2>/dev/null || true)
    if [ -z "${OPENSSL_VERSION_LINE:-}" ]; then
        err "  WARNING: openssl is not on PATH — PQ verification SKIPPED (OpenSSL 3.5+ not available)."
        err "  axis D (Wave 4 ML-DSA-65) was NOT verified on this host."
        err "  classical axes A+B+C have validated; proceeding in TRANSITION MODE."
    elif printf '%s\n' "$OPENSSL_VERSION_LINE" | grep -Eq "$PQ_MLDSA65_MIN_OPENSSL_REGEX"; then
        # AXIS-D INVOCATION: openssl pkeyutl -verify (ML-DSA-65, raw-message).
        # The `-rawin` flag is FIPS 204 compliant: openssl runs SHAKE-256
        # internally per the standard. The signature blob is the raw
        # 3309-byte FIPS 204 signature (no DER envelope, no base64) —
        # matches the workflow's `openssl pkeyutl -sign -rawin` output
        # byte-for-byte.
        if ! openssl pkeyutl -verify \
                -pubin -inkey "$PQ_INLINED_PUBKEY" \
                -rawin -in "$ARTIFACT" \
                -sigfile "$MLDSA_SIG"; then
            err "  axis D: ML-DSA-65 post-quantum signature check FAILED."
            err "  install.sh${PQ_MLDSA65_SIG_EXT} does NOT validate against the pinned"
            err "  ML-DSA-65 pubkey (fingerprint $PQ_MLDSA65_PUBKEY_FINGERPRINT)."
            err "  refusing to execute — Wave 4 demands axis D to validate when openssl >= 3.5."
            exit 1
        fi
        PQ_VERIFY_RAN=1
        ok "      axis D OK (ML-DSA-65 / FIPS 204, key fingerprint=$PQ_MLDSA65_PUBKEY_FINGERPRINT)"
    else
        err "  WARNING: PQ verification SKIPPED (OpenSSL 3.5+ not available)."
        err "    local openssl: $OPENSSL_VERSION_LINE"
        err "    required for axis D: OpenSSL 3.5 or newer (ML-DSA support landed in 3.5.0)."
        err "  classical axes A+B+C have validated; proceeding in TRANSITION MODE."
        err "  to enforce axis D, install OpenSSL >= 3.5 and re-run."
    fi
else
    err "  WARNING: PQ verification SKIPPED (no .mldsa.sig fetched; JARVIS_INSTALL_ALLOW_NO_PQ=1 set)."
fi
if [ "$PQ_VERIFY_RAN" -eq 0 ]; then
    note "      Wave 4 axis D status: SKIPPED (transition mode). axes A+B+C validated."
fi
log ""

# ----------------------------------------------------------------------- payload-commit (axis E, Wave-5 audit Finding 2)
note ""
note "[axis E] Verifying payload-commit pin (Wave-5 — binds cloned tree to signed commit)..."

# WAVE 5 PAYLOAD-COMMIT PIN — Wave-5 audit Finding 2.
#
# Background: install.sh does `git clone --depth 1 --branch main`, and
# `installer.py` does zero signature checks on the cloned tree. The
# four-axis chain ends at the bootstrap — whatever is on `main` at install
# time runs UNVERIFIED. An attacker who flips `main` after the release was
# signed can ship arbitrary payload bytes that the user runs, while still
# passing axes A+B+C+D on install.sh itself.
#
# Defense: the workflow emits `payload-commit.txt` containing the exact
# commit SHA of the tagged release, and signs it with the same Wave 1+2+4
# axes as install.sh. The verifier authenticates it, extracts the SHA, and
# exports it to install.sh via JARVIS_PAYLOAD_COMMIT env var. install.sh
# then `git checkout`s that SHA so the cloned tree is bound to the commit
# that existed at sign-time. An attacker who flips main post-release can no
# longer influence what gets installed.
#
# This is the FIFTH trust axis, documented as Axis E in TRUST_ROOT.md §10.

PAYLOAD_COMMIT_FILE="$STAGING/payload-commit.txt"
PAYLOAD_COMMIT_SIG="$STAGING/payload-commit.txt.sig"
PAYLOAD_COMMIT_PEM="$STAGING/payload-commit.txt.pem"
PAYLOAD_COMMIT_BUNDLE="$STAGING/payload-commit.txt.bundle"
PAYLOAD_COMMIT_COSIGN_SIG="$STAGING/payload-commit.txt.cosign.sig"
PAYLOAD_COMMIT_MLDSA_SIG="$STAGING/payload-commit.txt.mldsa.sig"

# Tag-gated graceful fallback: pre-Wave-5 releases (v0.5.0 and earlier)
# did not emit payload-commit.txt. We treat its absence as "this is a
# legacy release" and SKIP axis E with a loud warning instead of fail-
# closing — matching the JARVIS_INSTALL_ALLOW_NO_PQ pattern for the
# Wave-4 transition. The override is `JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1`
# (default 0, must be set explicitly to bypass on pre-Wave-5 tags).
PAYLOAD_COMMIT_AVAILABLE=1
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$PAYLOAD_COMMIT_FILE" "$REL_BASE/payload-commit.txt" 2>/dev/null; then
    err "  payload-commit.txt not present in release $TAG (likely a pre-Wave-5 tag)."
    if [ "${JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN:-0}" != "1" ]; then
        err "  Wave-5 axis E requires payload-commit.txt in the release."
        err "  if this is a legacy (pre-Wave-5) tag, set JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1"
        err "  to bypass axis E. The classical axes A+B+C+D still enforce install.sh"
        err "  authenticity, but the cloned tree is NOT bound to a signed commit."
        err "  read TRUST_ROOT.md §10 first."
        exit 1
    fi
    err "  proceeding under JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1 (override acknowledged; axis E bypassed)."
    PAYLOAD_COMMIT_AVAILABLE=0
fi

if [ "$PAYLOAD_COMMIT_AVAILABLE" -eq 1 ]; then
    # Fetch the per-axis signatures. Axis A trio (Fulcio) is mandatory;
    # axis B + axis D are mandatory in the same posture as install.sh
    # (axis D may degrade to TRANSITION MODE on pre-OpenSSL-3.5 hosts).
    for filename in payload-commit.txt.sig payload-commit.txt.pem payload-commit.txt.bundle payload-commit.txt.cosign.sig; do
        if ! curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/$filename" "$REL_BASE/$filename"; then
            err "  failed to fetch $REL_BASE/$filename — refusing."
            err "  Wave-5 axis E requires the complete signing trio + offline-ceremony sig for payload-commit.txt."
            exit 1
        fi
    done

    # AXIS-A on payload-commit.txt
    if ! "$COSIGN_BIN" verify-blob \
            --certificate                  "$PAYLOAD_COMMIT_PEM" \
            --signature                    "$PAYLOAD_COMMIT_SIG" \
            --bundle                       "$PAYLOAD_COMMIT_BUNDLE" \
            --certificate-identity-regexp  "$IDENTITY_REGEX" \
            --certificate-oidc-issuer      "$EXPECTED_OIDC_ISSUER" \
            --insecure-ignore-tlog=false \
            "$PAYLOAD_COMMIT_FILE"; then
        err "  axis E (payload-commit): axis A (cosign keyless) verification FAILED."
        err "  payload-commit.txt is NOT signed by the same workflow that signed install.sh."
        err "  refusing — possible attacker-substituted commit pin."
        exit 1
    fi

    # AXIS-B on payload-commit.txt
    if ! "$COSIGN_BIN" verify-blob \
            --key       "$INLINED_PUBKEY" \
            --signature "$PAYLOAD_COMMIT_COSIGN_SIG" \
            --insecure-ignore-tlog \
            "$PAYLOAD_COMMIT_FILE"; then
        err "  axis E (payload-commit): axis B (offline-ceremony Ed25519) verification FAILED."
        err "  payload-commit.txt.cosign.sig does NOT validate against the pinned Ed25519 pubkey."
        err "  refusing — possible attacker-substituted commit pin bypassing axis B."
        exit 1
    fi

    # AXIS-D on payload-commit.txt (transition-mode — same gate as stage [13/13])
    if curl -fsSL --retry 3 --retry-delay 2 -o "$PAYLOAD_COMMIT_MLDSA_SIG" "$REL_BASE/payload-commit.txt.mldsa.sig" 2>/dev/null; then
        OPENSSL_VERSION_LINE=$(openssl version 2>/dev/null || true)
        if [ -n "${OPENSSL_VERSION_LINE:-}" ] && printf '%s\n' "$OPENSSL_VERSION_LINE" | grep -Eq "$PQ_MLDSA65_MIN_OPENSSL_REGEX"; then
            if ! openssl pkeyutl -verify \
                    -pubin -inkey "$PQ_INLINED_PUBKEY" \
                    -rawin -in "$PAYLOAD_COMMIT_FILE" \
                    -sigfile "$PAYLOAD_COMMIT_MLDSA_SIG"; then
                err "  axis E (payload-commit): axis D (ML-DSA-65) verification FAILED."
                err "  refusing — possible attacker-substituted commit pin bypassing axis D."
                exit 1
            fi
            ok "      axis E PQ-verify OK (ML-DSA-65)"
        else
            err "  WARNING: axis E PQ verification SKIPPED on payload-commit.txt (OpenSSL 3.5+ not available)."
            err "  classical axes A+B on payload-commit.txt validated; proceeding in TRANSITION MODE."
        fi
    else
        err "  WARNING: payload-commit.txt.mldsa.sig not present (likely a pre-Wave-5.1 release)."
    fi

    # Validate the SHA format: lowercase hex, 40 chars (git SHA-1) or 64 chars (SHA-256).
    JARVIS_PAYLOAD_COMMIT=$(tr -d '[:space:]' < "$PAYLOAD_COMMIT_FILE")
    if ! printf '%s' "$JARVIS_PAYLOAD_COMMIT" | grep -Eq '^[0-9a-f]{40}([0-9a-f]{24})?$'; then
        err "  axis E: payload-commit.txt content is NOT a well-formed git SHA (40 hex or 64 hex)."
        err "    got: $JARVIS_PAYLOAD_COMMIT"
        err "  refusing — possible tamper or generation bug."
        exit 1
    fi
    export JARVIS_PAYLOAD_COMMIT
    ok "      axis E OK (payload commit pinned to $JARVIS_PAYLOAD_COMMIT)"
else
    note "      axis E SKIPPED via JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1 — install.sh will NOT pin the clone to a signed commit."
fi
log ""

# ----------------------------------------------------------------------- requirements.txt (Wave 6 — PyPI transitive hash pin)
note ""
note "[wave 6] Fetching + authenticating requirements.txt (PyPI transitive hash pin)..."

# WAVE 6 — PyPI TRANSITIVE DEPENDENCY HASH PIN.
#
# The five trust axes A+B+C+D+E above authenticate install.sh and bind it
# to a signed commit. install.sh then clones that commit and `installer.py`
# runs `pip install --require-hashes -r requirements.txt`. For
# --require-hashes to be meaningful, the lockfile MUST itself be
# authenticated under the same chain — otherwise an attacker who flips
# requirements.txt between sign-time and install-time bypasses pip's
# guarantee entirely.
#
# Defense: requirements.txt is signed under Wave 1 (Fulcio), Wave 2
# (offline Ed25519), and Wave 4 (ML-DSA-65) by the same workflow that
# signs install.sh. Wave 3 (SLSA L3) covers it transitively — the cross-
# runner hash manifest already includes requirements.txt as a subject.
# Here we fetch + verify all three direct axes; SLSA L3 is verified once
# globally in stage [9/13] for install.sh and the same provenance covers
# requirements.txt's hash by inclusion in the manifest.
#
# Parallel attack pattern this defends against:
#   - 2018 `event-stream` npm: maintainer transferred a transitively-pulled
#     package to a new maintainer who injected wallet-stealing code in a
#     minor bump (≈2 million weekly downloads, undetected ≈3 months).
#   - 2024 polyfill.io CDN: domain sold to a new owner who served crypto-
#     miner JS to ~100k sites that pulled the script by URL at runtime.
# Both incidents would have been caught by hash pins on the dependency
# graph + audit-on-update. Wave 6 lands that defense for the Python side.

REQ_FILE="$STAGING/requirements.txt"
REQ_SIG="$STAGING/requirements.txt.sig"
REQ_PEM="$STAGING/requirements.txt.pem"
REQ_BUNDLE="$STAGING/requirements.txt.bundle"
REQ_COSIGN_SIG="$STAGING/requirements.txt.cosign.sig"
REQ_MLDSA_SIG="$STAGING/requirements.txt.mldsa.sig"

# Tag-gated graceful fallback. Same pattern as axis E: pre-Wave-6 tags
# (v0.5.x and earlier) did not publish requirements.txt as a signed
# artifact, so the verifier degrades to a loud warning instead of
# fail-closed on those tags. JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 is
# the explicit override; default 0 means Wave-6 tags MUST publish it.
REQ_AVAILABLE=1
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$REQ_FILE" "$REL_BASE/requirements.txt" 2>/dev/null; then
    err "  requirements.txt not present in release $TAG (likely a pre-Wave-6 tag)."
    if [ "${JARVIS_INSTALL_ALLOW_NO_PIP_HASHES:-0}" != "1" ]; then
        err "  Wave 6 requires the hash-pinned lockfile in the release."
        err "  if this is a legacy (pre-Wave-6) tag, set"
        err "  JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 to bypass — install.sh will then"
        err "  install runtime deps from pyproject.toml (no transitive hash pin)."
        err "  read docs/supply-chain/threat-model.md §11 first."
        exit 1
    fi
    err "  proceeding under JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 (override acknowledged)."
    REQ_AVAILABLE=0
fi

if [ "$REQ_AVAILABLE" -eq 1 ]; then
    # Fetch the Wave 1 trio + Wave 2 sig. ML-DSA-65 is degrade-graceful
    # below (same transition gate as install.sh's stage [13/13]).
    for filename in requirements.txt.sig requirements.txt.pem requirements.txt.bundle requirements.txt.cosign.sig; do
        if ! curl -fsSL --retry 3 --retry-delay 2 -o "$STAGING/$filename" "$REL_BASE/$filename"; then
            err "  failed to fetch $REL_BASE/$filename — refusing."
            err "  Wave 6 requires the complete signing trio + offline-ceremony sig for requirements.txt."
            exit 1
        fi
    done

    # AXIS A on requirements.txt (Fulcio keyless + Rekor bundle)
    if ! "$COSIGN_BIN" verify-blob \
            --certificate                  "$REQ_PEM" \
            --signature                    "$REQ_SIG" \
            --bundle                       "$REQ_BUNDLE" \
            --certificate-identity-regexp  "$IDENTITY_REGEX" \
            --certificate-oidc-issuer      "$EXPECTED_OIDC_ISSUER" \
            --insecure-ignore-tlog=false \
            "$REQ_FILE"; then
        err "  Wave 6: axis A (cosign keyless) verification on requirements.txt FAILED."
        err "  requirements.txt is NOT signed by the same workflow that signed install.sh."
        err "  refusing — possible attacker-substituted dependency lockfile."
        exit 1
    fi
    ok "      Wave 6 axis A OK (Fulcio keyless on requirements.txt)"

    # AXIS B on requirements.txt (offline-ceremony Ed25519)
    if ! "$COSIGN_BIN" verify-blob \
            --key       "$INLINED_PUBKEY" \
            --signature "$REQ_COSIGN_SIG" \
            --insecure-ignore-tlog \
            "$REQ_FILE"; then
        err "  Wave 6: axis B (offline-ceremony Ed25519) verification on requirements.txt FAILED."
        err "  refusing — possible attacker-substituted lockfile bypassing axis B."
        exit 1
    fi
    ok "      Wave 6 axis B OK (Ed25519 offline-ceremony on requirements.txt)"

    # AXIS D on requirements.txt (ML-DSA-65, transition mode)
    if curl -fsSL --retry 3 --retry-delay 2 -o "$REQ_MLDSA_SIG" "$REL_BASE/requirements.txt.mldsa.sig" 2>/dev/null; then
        OPENSSL_VERSION_LINE=$(openssl version 2>/dev/null || true)
        if [ -n "${OPENSSL_VERSION_LINE:-}" ] && printf '%s\n' "$OPENSSL_VERSION_LINE" | grep -Eq "$PQ_MLDSA65_MIN_OPENSSL_REGEX"; then
            if ! openssl pkeyutl -verify \
                    -pubin -inkey "$PQ_INLINED_PUBKEY" \
                    -rawin -in "$REQ_FILE" \
                    -sigfile "$REQ_MLDSA_SIG"; then
                err "  Wave 6: axis D (ML-DSA-65) verification on requirements.txt FAILED."
                err "  refusing — possible attacker-substituted lockfile bypassing axis D."
                exit 1
            fi
            ok "      Wave 6 axis D OK (ML-DSA-65 / FIPS 204 on requirements.txt)"
        else
            err "  WARNING: Wave 6 axis D verification SKIPPED on requirements.txt (OpenSSL 3.5+ not available)."
            err "  classical axes A+B on requirements.txt validated; proceeding in TRANSITION MODE."
        fi
    else
        err "  WARNING: requirements.txt.mldsa.sig not present (likely a pre-Wave-6.1 release)."
    fi

    # Sanity: re-assert the lockfile actually contains hash pins. An attacker
    # who substitutes a signed-but-empty file would pass the signature checks
    # above; this guard fails-closed on a hash-pin count below the contractual
    # floor (50 lines starting with `--hash=sha256:`, per the Wave 6 DoD).
    HASH_LINE_COUNT=$(grep -c '^[[:space:]]*--hash=sha256:' "$REQ_FILE" || true)
    if [ -z "$HASH_LINE_COUNT" ] || [ "$HASH_LINE_COUNT" -lt 50 ]; then
        err "  Wave 6: requirements.txt only carries ${HASH_LINE_COUNT:-0} '--hash=sha256:' lines (< 50 required)."
        err "  refusing — lockfile does not satisfy the Wave 6 hash-pin floor."
        exit 1
    fi
    ok "      Wave 6 hash-pin floor OK (${HASH_LINE_COUNT} '--hash=sha256:' lines)"

    # Hand the authenticated lockfile path to install.sh + installer.py so the
    # second stage uses THIS authenticated copy rather than the as-cloned
    # version. install.sh checks JARVIS_AUTHENTICATED_REQUIREMENTS and copies
    # over the in-tree file if set; installer.py then runs `pip install
    # --require-hashes -r requirements.txt`. If install.sh doesn't propagate
    # the env var, the in-tree copy is still authenticated by axis E
    # (payload-commit pin) so the integrity chain holds.
    export JARVIS_AUTHENTICATED_REQUIREMENTS="$REQ_FILE"
    ok "      Wave 6 lockfile authenticated and exported as JARVIS_AUTHENTICATED_REQUIREMENTS"
else
    note "      Wave 6 SKIPPED via JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 — install.sh will NOT install with --require-hashes."
fi
log ""

chmod +x "$ARTIFACT"
# Use `exec` so the process tree is clean and signals propagate.
# The bash invocation here is identical to the legacy `curl ... | bash`
# behaviour — the difference is that the bytes have been authenticated by
# the CLASSICAL THREE axes (Fulcio keyless A, offline ceremony B, SLSA L3
# + in-toto C), the POST-QUANTUM fourth axis D (ML-DSA-65, when openssl
# >= 3.5), AND the Wave-5 payload-commit pin (axis E) which install.sh
# consumes via the exported JARVIS_PAYLOAD_COMMIT env var to bind the
# cloned tree to the signed commit. When axis D or E degrade to
# TRANSITION MODE, the remaining axes still guarantee the bytes — every
# degradation is loud-logged so an auditor sees it in the transcript.
exec bash "$ARTIFACT" "$@"
