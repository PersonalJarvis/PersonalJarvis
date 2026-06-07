# Personal Jarvis — verifying one-liner for Windows (Wave 3 supply-chain).
#
# Usage:
#   irm https://github.com/personal-jarvis/personal-jarvis/releases/download/<TAG>/install-verify.ps1 | iex
#
# Wave 3 demands 3-of-3 independent trust axes — all must validate or the
# verifier refuses to hand off to install.ps1:
#
#   Axis A (online, ephemeral): cosign keyless signature against Fulcio,
#                               minted by this repo's GitHub Actions workflow
#                               via OIDC. install.ps1.sig + install.ps1.pem +
#                               install.ps1.bundle (the Wave 1 trio).
#
#   Axis B (offline, long-lived): cosign --key signature against an
#                               Ed25519 public key generated in an
#                               air-gapped ceremony, pinned in this script
#                               AND committed at install/keys/offline-ceremony.pub.
#                               install.ps1.cosign.sig (Wave 2 addition).
#
#   Axis C (build-env, SLSA L3 + in-toto layout): slsa-verifier checks the
#                               SLSA L3 provenance (personal-jarvis.intoto.jsonl)
#                               for the installer artifact AND the verifier
#                               cross-checks the in-toto layout's pinned
#                               functionary identity-regexp against the
#                               Fulcio identity in the provenance. This adds
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
#                               and store-now-decrypt-later. Verified in stage
#                               [13/13] in TRANSITION MODE: if `openssl.exe`
#                               >= 3.5 is on PATH, the PQ signature is
#                               enforced; otherwise it is SKIPPED with an
#                               explicit warning (classical axes A+B+C have
#                               already validated). Never silently skipped.
#
# Any non-zero exit anywhere in stages [0/13]..[13/13] is FAIL-CLOSED: the
# second-stage installer is never executed.
#
# See docs/supply-chain/threat-model.md and install/TRUST_ROOT.md.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ------------------------------------------------------------------- pins
$EXPECTED_REPO            = 'personal-jarvis/personal-jarvis'
$EXPECTED_WORKFLOW_PATH   = '.github/workflows/sign-installer.yml'
$EXPECTED_OIDC_ISSUER     = 'https://token.actions.githubusercontent.com'
$COSIGN_VERSION           = 'v2.4.1'
$COSIGN_SHA256_WINDOWS    = '8d57f8a42a981d27290c4227271fa9f0f62ca6630eb4a21d316bd6b01405b87c'
$REKOR_MAX_AGE_SECONDS    = 86400

# WAVE 2 PINNED OFFLINE KEY — fingerprint 40cdb1b9e255e797909fba4fb5983450ccf7fa26ec17c80f473fe360da5549ee
#
# Ed25519 public key produced in the air-gapped offline-ceremony documented
# in docs/supply-chain/wave2-key-ceremony.md. Fingerprint is sha256(DER(pubkey)).
# Inlined here (not just fetched from the release) so an attacker who controls
# the release-asset store cannot quietly swap the key: stage [3/13] also fetches
# the released copy and refuses to proceed if the two diverge.
$OFFLINE_CEREMONY_PUBKEY_PEM = @'
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAyQ4Jmisu92/f92Os8DRmIwbwN/rjOuLsRTYTaHmNnN0=
-----END PUBLIC KEY-----
'@
$OFFLINE_CEREMONY_PUBKEY_FINGERPRINT = '40cdb1b9e255e797909fba4fb5983450ccf7fa26ec17c80f473fe360da5549ee'

# WAVE 4 PINNED POST-QUANTUM KEY - fingerprint
# 30a634809c19c41abcead8e657bfe19a53f9f4c831a82d2939cb7d5c40efe01a
#
# ML-DSA-65 public key (NIST FIPS 204 category 3). Generated alongside the
# Wave-2 offline-ceremony key using OpenSSL 3.5.6's
# `openssl genpkey -algorithm ML-DSA-65`. Fingerprint is sha256(DER(SPKI)).
# Inlined here (not just fetched from the release) so an attacker who
# controls the release-asset store cannot quietly swap the key: stage
# [12/13] also fetches the released copy and refuses to proceed if the
# two diverge - same defense pattern as the Wave-2 offline key.
$PQ_MLDSA65_PUBKEY_PEM = @'
-----BEGIN PUBLIC KEY-----
MIIHsjALBglghkgBZQMEAxIDggehAAUJoP74UbGXJIuYqNVfNlQsgUhXpI/8qm4J
L6qfWHzHkEZmAuD/VMHgIrm1OX/XRXVRZo7e4ecqsX2rRNei/4HT6Sx612VgRTFg
snJ0B5kEJYSl2bpuPGS418ArsZ0dG6UjkfEHUC8lbrdNasG7ox+gaxxyfswFwL5F
muEFUG+ziAg4OHEqkGHoN/pAaX8Iix6nWrKeiKlx9QdVn3OxCaghFXmn02pR5Cg3
sTpsg2de+ddfntaZiiKdT0R5712MzwM0Nr3PEyMp6YvU0mCujyInOj6qWPofHwTs
/INNtpN3Djga+IuF7Hvh0dmcOrHogquIiF6ezG4cEumpTSUdMeDtZNB1S1cTFGfi
nh0BPXnkvLDTgGLyNBF1Cpg81d2g02400g558PkvwSLlGrgiC7bqFu6VfuAj6oGG
BIk+fyNbiUDt3Kir6YYAh/Q4uOzdbHl0K8IXflZcm6tzS7NBYVEJ2CqIYjxhi6vO
jkuDlWDASTXvimnew1TtPMyXo5QmOgM8c6YZqIwkDQLl+EBQ7c+Gn5F+WspyKtoF
XnO8JGOgN/lvt95Np315qM5oJivJwpXW0jmwhrFhf2tPfkWQtUWqC6TRpqqeDQqC
1QYx3YOt9VjpnaahPHlAYiznnnqTRobukHhfyrZ/VXebC8zKsRdwebQvPt8LH1K3
SS8SesyxOsNJ+4suDmdtunPmRTlIMSvMrjKEUZ4k/0Ytd6G1of7TX5JNgtV1GSwT
gszjZedSfkQvo73KNErGgAlIpVndvAPC3h5lomvswK1bXiR4/UeizrxZozv2zfnN
dNKI1aM/Q1sX+dMM+yIWdz0dxxJxvNED7fvlWFZsjyNFwsKqv8nMdABn5mAwOTnB
vfUI3xzpKad3SBIPaDI1wxe2eva55qkgyfQqAm/JpH3QpPX05YxJ8YAFSeUQAD1g
rD/bZ31Rsabqf6RfNYVXXoMbtaLmLRWqZOU3ee9OEZJw8jbvJXyxGXeQ7piGZsnx
75kKkwEaXscXnFg/dD4ES/NGB5t67GUIKhwKcIWv8V6mKErcSQSg6oogK4wBztUf
YDdGxR6jFz6H5iXwJiTWLgpDpMEXoGOYmqAddz79yi66ZupT7v8GSRjJ2apLuYh6
01SQA6+gJE6ANDNTVvrVF2TkgeU6m9eDCx0MVMfcP7Y7aOsvC7JPgS7pF5DoFsI2
ZfI8LWCj8kLuuEtEDSOOp5e8iSAX/X24lbFKG0jH2dGK9N1MS6ilFhpzbsLGLSGe
exbYK0jIGgtHZONw6oIU6aG2DlsYQrmfMLviPr0p5GABOSGzIds9MB5L3q6w1Z5K
3S27V5xnutuwVxJvJPjbKdZ3bV3cFWtC34rzBcXgXWLoCMtiCr4EI9TJvuUOrJRx
+cbiKdZ9ZTSfNFRcObhMvigByxbEXU5B1PEafRoFJdX7zTK0Xdbbz3QAs72dGMOT
iNlCJGGhs7adYhGl7nbGi1ssBcfJehHb5M20/+lO1s9kLOv8j67pjhVLjZg+0tNr
urTomFIsj/Vxy+0SH6NRXgUwF+RtmVJes3ZJ+6S44bMaaz8J7r8Q2QtA+u2lXidG
PnzhEJoLkuxaKGiwGUvnPjU5FRyxIi/CsvOsLJdoLEs9oSQl0Bpw69TkD8htmWYI
tjFOg7VyNiIOTlYJPDxUzDh7LWDw47WdJ2ve2ob/W6m3uX5PYtitiVZZIDGztKnC
lpz16COx+LXHXbcSjk5z/sD2cbGNrviv+lWHhv5uIcFG7S6Tk/X2g0zowpvYit2m
atBxr3DkPTz5C4wEP6d0UJkKfFO5Q1xzFCpNDqhZNaLJWFskRqxPfGP/odCjpmZR
gUHYwrsSGP2dfb0ynSfaPpvDDshxOhwxZ882mWcoYC6G73tQ8unY3drdf9cjadP4
47ztteFW/+ldi+yrJ2n4ZzD7jg9KlInZezbXd+FYJHUSu3L6dWy5CHdP32YJTbqp
cVWTl21DTdZiBFbdzE9J4WTtOUREbOEG3L1EhIWtdro1rvmpl48hbMRrbTOUc5+k
JGuAOZZiClYVvGMhmqRWbeGZKUx+5ZB1RPAd9GY8ab7M9AlodSx3EcX185fx61a2
KRwJyT7VcjTn5fyoO1pmedSwwjBTyvp8jhCNFcGzUAqF6/92QMVWNw4vwZTR2EZB
xpPn6aG50yoLE0UVGlonvATJG2BTk07R6TPlAQMMevMCmd55/w/RefSj4ahUb0p8
/LhhsR2D60TbySw+0e+iNEU167pdRp1ETSE84Hdx3+XvNnGHp0Ld7FA6MdfMMYpN
3mPb72yjqQL7LG57kQQ+1re/BNQkNmLAKWhfucrrtQ0bVQi4PEg0of81vPVpuQGJ
v6cg6t3bToDnTZrOwKLOCqxFXqFWHhgeXMd+QgMhFxPbEuyGVUsbQWH4kgQuGALz
+ldbVTNOdGACarBmil0ZxMSzOEnFgWUKQZzUo6jwfk9ITR055ubmZcKCb+tf0Quz
+Zd1V1bvds5TA0YQsUlQ2A4FXxp3iep/DzRfWU+/gRMLJ1GTQt/yozKkjgNdmwak
aDpDy2VcCS39Xe2awcsE4ReG2hHwNyOGpgWh+g3KiPBs7nCT6epVpVSTcbtdlduS
cjS9tMxf
-----END PUBLIC KEY-----
'@
$PQ_MLDSA65_PUBKEY_FINGERPRINT = '30a634809c19c41abcead8e657bfe19a53f9f4c831a82d2939cb7d5c40efe01a'
# Released asset name (axis-D pubkey cross-check) - must match the
# workflow's `cp install/keys/pq-mldsa65.pub.pem out/pq-mldsa65.pub.pem`.
$PQ_MLDSA65_PUBKEY_ASSET_NAME = 'pq-mldsa65.pub.pem'
# Suffix the workflow attaches per artifact (e.g. install.ps1.mldsa.sig).
$PQ_MLDSA65_SIG_EXT = '.mldsa.sig'

# WAVE 3 - slsa-verifier release pin. Same SHA-256 trust pattern as cosign:
# we download the slsa-verifier binary for Windows/AMD64 and refuse to
# execute it unless its hash matches the pin. Source of truth for the
# hash:
#   https://github.com/slsa-framework/slsa-verifier/blob/main/SHA256SUM.md
# Pinned version: v2.7.0 — required for compatibility with the
# slsa-github-generator v2.1.0 we use in sign-installer.yml. That
# generator emits provenance bundles with tlog entry type `dsse:0.0.1`,
# which v2.6.0 verifier rejects ("expected intoto:0.0.2, got dsse:0.0.1").
# v2.7.0 accepts BOTH formats and is the version the generator's own
# generate-builder.sh bundles internally (VERIFIER_RELEASE=v2.7.0).
# Bumping this further requires updating TRUST_ROOT.md section 4 with
# verification provenance.
$SLSA_VERIFIER_VERSION        = 'v2.7.0'
$SLSA_VERIFIER_SHA256_WINDOWS = '61ff8b1cca6ac0012b0ba906367836f64a389444766be437df2a69f71285f43b'

# Expected source URI passed to `slsa-verifier verify-artifact --source-uri`.
# Must match the repo whose Actions identity built+attested the release.
$EXPECTED_SLSA_SOURCE_URI = "github.com/$EXPECTED_REPO"

# Expected Fulcio identity_regexp inside install/in-toto/layout-content-anchor.json.
# Wave-5 audit Finding 3: the layout doc is UNSIGNED - a content-anchor only.
# The constant below is the actual source of truth, baked into this signed
# verifier. Drift means either the layout was modified or the pin is stale -
# both are fail-closed.
$EXPECTED_INTOTO_IDENTITY_REGEXP = '^https://github\.com/personal-jarvis/personal-jarvis/\.github/workflows/sign-installer\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._-]+)?$'

# Filename of the SLSA L3 provenance attestation. The release uploads
# this file under exactly this name; SA-2 owns the workflow contract.
$SLSA_PROVENANCE_FILENAME = 'personal-jarvis.intoto.jsonl'

# Filename of the content-anchor layout assertion uploaded to the release.
# Wave-5 audit Finding 3 renamed this from `layout.template.json` to
# `layout-content-anchor.json` to remove the implicit "in-toto signed
# layout" overclaim - the document is in-toto-shaped but UNSIGNED;
# authenticity comes from this signed verifier byte-comparing the
# constant against the asserted identity_regexp.
$INTOTO_LAYOUT_FILENAME = 'layout-content-anchor.json'

# -- helper: SHA-256 fingerprint of a PEM-encoded SubjectPublicKeyInfo DER.
# We deliberately avoid relying on a local openssl.exe (not always present
# on Windows boxes); instead we strip the PEM armor, base64-decode the body,
# and SHA-256 the resulting DER. That is exactly what
# `openssl pkey -pubin -outform DER | openssl dgst -sha256` computes.
function Get-PubkeyFingerprint([string]$PemText) {
    $lines = $PemText -split "`r?`n" | Where-Object {
        $_ -and ($_ -notmatch '^-----')
    }
    $b64 = ($lines -join '').Trim()
    if (-not $b64) { return $null }
    try {
        $der = [Convert]::FromBase64String($b64)
    } catch {
        return $null
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($der)
    } finally {
        $sha.Dispose()
    }
    return ([System.BitConverter]::ToString($hash) -replace '-', '').ToLower()
}

$banner = @'

 ____                                  _   _                  _
|  _ \ ___ _ __ ___  ___  _ __   __ _ | | | | __ _ _ ____   _(_)___
| |_) / _ \ '__/ __|/ _ \| '_ \ / _` || |_| |/ _` | '__\ \ / / / __|
|  __/  __/ |  \__ \ (_) | | | | (_| ||  _  | (_| | |   \ V /| \__ \
|_|   \___|_|  |___/\___/|_| |_|\__,_||_| |_|\__,_|_|    \_/ |_|___/

  Verifying installer (Sigstore + offline ceremony + SLSA L3, Wave 3)
'@
Write-Host $banner -ForegroundColor Cyan

# ------------------------------------------------------------------- tag
Write-Host ''
Write-Host '[0/13] Resolving release tag...' -ForegroundColor Yellow
$Tag = $env:JARVIS_INSTALL_TAG
if (-not $Tag) {
    Write-Host '      JARVIS_INSTALL_TAG not set - resolving latest release...'
    # 'latest' redirects to /tag/<vX.Y.Z>; we capture the redirect.
    try {
        $resp = Invoke-WebRequest -Uri "https://github.com/$EXPECTED_REPO/releases/latest" -MaximumRedirection 0 -ErrorAction SilentlyContinue
    } catch {
        # On redirect, .NET throws; the Location header is inside $_.Exception.Response.Headers.
        $resp = $_.Exception.Response
    }
    if ($resp -and $resp.Headers.Location) {
        $Tag = ($resp.Headers.Location -split '/tag/')[-1]
    } elseif ($resp -and $resp.Headers['Location']) {
        $Tag = ($resp.Headers['Location'] -split '/tag/')[-1]
    }
    if (-not $Tag) {
        Write-Host '  failed to resolve latest release tag - set $env:JARVIS_INSTALL_TAG explicitly.' -ForegroundColor Red
        exit 1
    }
}
Write-Host "      Tag pinned: $Tag" -ForegroundColor Green

# ------------------------------------------------------------------- staging
$Staging = Join-Path $env:TEMP ("jarvis-install-verify-" + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $Staging | Out-Null
Write-Host "      Staging: $Staging"

try {
    # --------------------------------------------------------------- platform
    Write-Host ''
    Write-Host '[1/13] Detecting platform...' -ForegroundColor Yellow
    # Windows-only verifier; we still log architecture for diagnostics and to
    # make a future arm64 bump explicit.
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -ne 'AMD64') {
        Write-Host "  unsupported platform: Windows/$arch (this verifier targets Windows/AMD64)" -ForegroundColor Red
        exit 1
    }
    Write-Host "      Platform: Windows/$arch -> cosign-windows-amd64.exe + slsa-verifier-windows-amd64.exe" -ForegroundColor Green

    # --------------------------------------------------------------- cosign
    Write-Host ''
    Write-Host "[2/13] Bootstrapping cosign $COSIGN_VERSION (SHA-256 pinned)..." -ForegroundColor Yellow

    $CosignBin = Join-Path $Staging 'cosign.exe'
    $CosignUrl = "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-windows-amd64.exe"
    Invoke-WebRequest -Uri $CosignUrl -OutFile $CosignBin -UseBasicParsing | Out-Null

    $ActualSha = (Get-FileHash -Path $CosignBin -Algorithm SHA256).Hash.ToLower()
    if ($ActualSha -ne $COSIGN_SHA256_WINDOWS) {
        Write-Host '  cosign SHA-256 mismatch!' -ForegroundColor Red
        Write-Host "    expected: $COSIGN_SHA256_WINDOWS" -ForegroundColor Red
        Write-Host "    actual:   $ActualSha" -ForegroundColor Red
        Write-Host '  abort - the downloaded cosign is NOT the version this verifier was rooted against.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      cosign SHA-256 OK ($COSIGN_SHA256_WINDOWS)" -ForegroundColor Green

    # --------------------------------------------------------------- artifact + signatures
    Write-Host ''
    Write-Host "[3/13] Fetching install.ps1 + Fulcio trio + offline-ceremony signature from release $Tag..." -ForegroundColor Yellow

    $RelBase = "https://github.com/$EXPECTED_REPO/releases/download/$Tag"
    foreach ($filename in 'install.ps1','install.ps1.sig','install.ps1.pem','install.ps1.bundle','install.ps1.cosign.sig') {
        Invoke-WebRequest -Uri "$RelBase/$filename" -OutFile (Join-Path $Staging $filename) -UseBasicParsing | Out-Null
    }
    $Artifact  = Join-Path $Staging 'install.ps1'
    $Sig       = Join-Path $Staging 'install.ps1.sig'
    $Pem       = Join-Path $Staging 'install.ps1.pem'
    $Bundle    = Join-Path $Staging 'install.ps1.bundle'
    $CosignSig = Join-Path $Staging 'install.ps1.cosign.sig'

    # Write the inlined pubkey to disk; cosign --key wants a file path.
    $InlinedPubkey  = Join-Path $Staging 'offline-ceremony.pub.inlined'
    $ReleasedPubkey = Join-Path $Staging 'offline-ceremony.pub.released'
    Set-Content -Path $InlinedPubkey -Value $OFFLINE_CEREMONY_PUBKEY_PEM -Encoding ascii -NoNewline

    # Fetch the released copy of the offline-ceremony public key and assert
    # it matches the inlined one by SHA-256 of the DER-form SPKI. FIRST tamper
    # detection - catches a swapped published .pub BEFORE any signature math.
    try {
        Invoke-WebRequest -Uri "$RelBase/offline-ceremony.pub" -OutFile $ReleasedPubkey -UseBasicParsing | Out-Null
    } catch {
        Write-Host "  failed to fetch $RelBase/offline-ceremony.pub" -ForegroundColor Red
        Write-Host '  the release MUST publish the offline-ceremony public key as a cross-check asset.' -ForegroundColor Red
        exit 1
    }

    $InlinedFp  = Get-PubkeyFingerprint $OFFLINE_CEREMONY_PUBKEY_PEM
    $ReleasedFp = Get-PubkeyFingerprint (Get-Content -Raw -Path $ReleasedPubkey)

    if (-not $InlinedFp -or -not $ReleasedFp) {
        Write-Host '  could not compute fingerprint of inlined or released offline-ceremony.pub' -ForegroundColor Red
        Write-Host "  inlined:  $InlinedFp" -ForegroundColor Red
        Write-Host "  released: $ReleasedFp" -ForegroundColor Red
        exit 1
    }
    if ($InlinedFp -ne $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT) {
        Write-Host '  inlined offline-ceremony pubkey fingerprint mismatch!' -ForegroundColor Red
        Write-Host "    expected (pinned in verifier): $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT" -ForegroundColor Red
        Write-Host "    actual   (heredoc):            $InlinedFp" -ForegroundColor Red
        Write-Host '  this script has been tampered with - refusing.' -ForegroundColor Red
        exit 1
    }
    if ($ReleasedFp -ne $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT) {
        Write-Host '  released offline-ceremony pubkey fingerprint mismatch!' -ForegroundColor Red
        Write-Host "    expected (pinned in verifier): $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT" -ForegroundColor Red
        Write-Host "    actual   (release asset):      $ReleasedFp" -ForegroundColor Red
        Write-Host '  the published offline-ceremony.pub does NOT match the verifier pin - refusing.' -ForegroundColor Red
        exit 1
    }
    Write-Host '      install.ps1 + .sig + .pem + .bundle + .cosign.sig downloaded' -ForegroundColor Green
    Write-Host "      offline-ceremony pubkey fingerprint OK ($OFFLINE_CEREMONY_PUBKEY_FINGERPRINT)" -ForegroundColor Green

    # --------------------------------------------------------------- verify Fulcio (axis A)
    Write-Host ''
    Write-Host '[4/13] Verifying Fulcio keyless signature (axis A - GitHub Actions OIDC)...' -ForegroundColor Yellow

    $IdentityRegex = "^https://github.com/$EXPECTED_REPO/$([regex]::Escape($EXPECTED_WORKFLOW_PATH))@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._-]+)?`$"

    # AXIS-A INVOCATION: cosign verify-blob (Fulcio keyless, with bundle + tlog)
    & $CosignBin verify-blob `
        --certificate                  $Pem `
        --signature                    $Sig `
        --bundle                       $Bundle `
        --certificate-identity-regexp  $IdentityRegex `
        --certificate-oidc-issuer      $EXPECTED_OIDC_ISSUER `
        --insecure-ignore-tlog=false `
        $Artifact
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  axis A: cosign verification FAILED.' -ForegroundColor Red
        Write-Host "  the downloaded install.ps1 is NOT signed by $EXPECTED_REPO's release workflow." -ForegroundColor Red
        Write-Host '  refusing to execute.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      axis A OK (identity=$EXPECTED_REPO / $EXPECTED_WORKFLOW_PATH, issuer=$EXPECTED_OIDC_ISSUER)" -ForegroundColor Green

    # --------------------------------------------------------------- verify offline ceremony (axis B)
    Write-Host ''
    Write-Host '[5/13] Verifying offline-ceremony signature (axis B - Ed25519, air-gapped)...' -ForegroundColor Yellow

    # cosign verification in --key mode does NOT consult Rekor: this is a pure
    # detached-signature check against the pinned Ed25519 pubkey. Rekor
    # inclusion is enforced once, via axis A's bundle.
    # AXIS-B INVOCATION: cosign verify-blob (offline ceremony, --key Ed25519, no tlog)
    # --insecure-ignore-tlog is intentional here: key-based cosign signatures
    # are NOT uploaded to Rekor (no Fulcio cert to bind them to). Without
    # this flag, cosign tries to look the signature up in Rekor and Rekor
    # rejects with "unsupported hash algorithm: SHA-256 not in [SHA-512]"
    # because Ed25519 mandates SHA-512 internally (RFC 8032).
    & $CosignBin verify-blob `
        --key       $InlinedPubkey `
        --signature $CosignSig `
        --insecure-ignore-tlog `
        $Artifact
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  axis B: offline-ceremony signature check FAILED.' -ForegroundColor Red
        Write-Host '  install.ps1.cosign.sig does NOT validate against the pinned Ed25519 pubkey' -ForegroundColor Red
        Write-Host "  (fingerprint $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT)." -ForegroundColor Red
        Write-Host '  refusing to execute - Wave 3 demands ALL THREE axes to pass.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      axis B OK (Ed25519, key fingerprint=$OFFLINE_CEREMONY_PUBKEY_FINGERPRINT)" -ForegroundColor Green

    # --------------------------------------------------------------- freshness
    Write-Host ''
    Write-Host "[6/13] Checking Rekor inclusion proof freshness (<= ${REKOR_MAX_AGE_SECONDS}s)..." -ForegroundColor Yellow

    # Freshness applies to axis A (Fulcio + Rekor). Axis B is a detached
    # signature with no transparency log; replay defence comes from the fact
    # that an old install.ps1.cosign.sig still needs to match the CURRENT
    # install.ps1 bytes - any tamper invalidates Ed25519.
    $BundleJson = Get-Content -Raw -Path $Bundle | ConvertFrom-Json
    $IntegratedTime = $null
    if ($BundleJson.verificationMaterial -and $BundleJson.verificationMaterial.tlogEntries -and $BundleJson.verificationMaterial.tlogEntries[0].integratedTime) {
        $IntegratedTime = [int64]$BundleJson.verificationMaterial.tlogEntries[0].integratedTime
    } elseif ($BundleJson.rekorBundle -and $BundleJson.rekorBundle.Payload -and $BundleJson.rekorBundle.Payload.integratedTime) {
        $IntegratedTime = [int64]$BundleJson.rekorBundle.Payload.integratedTime
    }
    if (-not $IntegratedTime) {
        Write-Host '  could not parse Rekor integrated time from bundle - refusing.' -ForegroundColor Red
        exit 1
    }
    $Now = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
    $Age = $Now - $IntegratedTime
    if ($Age -lt 0) {
        Write-Host "  Rekor integrated time is in the FUTURE (now=$Now, integrated=$IntegratedTime) - refusing." -ForegroundColor Red
        exit 1
    }
    if ($Age -gt $REKOR_MAX_AGE_SECONDS) {
        Write-Host "  Rekor inclusion proof too old: ${Age}s > ${REKOR_MAX_AGE_SECONDS}s" -ForegroundColor Red
        if ($env:JARVIS_INSTALL_ALLOW_STALE -ne '1') {
            Write-Host '  set $env:JARVIS_INSTALL_ALLOW_STALE=1 to override (read TRUST_ROOT.md first).' -ForegroundColor Red
            exit 1
        }
        Write-Host '  proceeding under JARVIS_INSTALL_ALLOW_STALE=1 (override acknowledged).' -ForegroundColor Red
    }
    Write-Host "      Rekor inclusion proof age: ${Age}s (limit ${REKOR_MAX_AGE_SECONDS}s)" -ForegroundColor Green

    # --------------------------------------------------------------- identity cross-check
    Write-Host ''
    Write-Host '[7/13] Cross-checking identity assertions on both axes...' -ForegroundColor Yellow

    # Axis A: re-extract SAN from the Fulcio cert and assert it matches the
    # pinned identity regex. cosign already checks this, but we re-assert
    # for paranoia (defence against future cosign behaviour drift).
    # cosign uploads the Fulcio cert as a single-line base64 blob (the raw
    # PEM is base64-wrapped again on disk). X509Certificate2 reads raw PEM
    # but not base64-of-PEM. Detect and decode if needed.
    $PemForLoad = $Pem
    $PemHead = (Get-Content -Path $Pem -TotalCount 1) -as [string]
    if ($PemHead -and -not $PemHead.StartsWith('-----BEGIN')) {
        try {
            $PemForLoad = Join-Path $Staging 'install.ps1.pem.decoded'
            $b64 = (Get-Content -Raw -Path $Pem).Trim()
            [IO.File]::WriteAllBytes($PemForLoad, [Convert]::FromBase64String($b64))
        } catch {
            Write-Host '  Fulcio cert is neither raw PEM nor base64-of-PEM - refusing.' -ForegroundColor Red
            exit 1
        }
    }
    $CertObj = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $PemForLoad
    $SanExt  = $CertObj.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' } | Select-Object -First 1
    if (-not $SanExt) {
        Write-Host '  could not extract SAN extension from Fulcio cert - refusing.' -ForegroundColor Red
        exit 1
    }
    $SanText = $SanExt.Format($true)
    # Format($true) emits one entry per line, prefixed e.g. "URL=https://..."
    # or "URI=...". We accept either.
    $CertSan = $null
    foreach ($line in ($SanText -split "`r?`n")) {
        if ($line -match '^(?:URI|URL)\s*=\s*(.+?)\s*$') {
            $CertSan = $matches[1]
            break
        }
    }
    if (-not $CertSan) {
        Write-Host '  could not parse URI from SAN - refusing.' -ForegroundColor Red
        Write-Host "    raw SAN: $SanText" -ForegroundColor Red
        exit 1
    }
    if ($CertSan -notmatch $IdentityRegex) {
        Write-Host '  axis A SAN cross-check FAILED.' -ForegroundColor Red
        Write-Host "    SAN:    $CertSan" -ForegroundColor Red
        Write-Host "    regex:  $IdentityRegex" -ForegroundColor Red
        Write-Host '  refusing to execute.' -ForegroundColor Red
        exit 1
    }

    # WAVE 5 TAG-BINDING - Wave-5 audit Finding 1 (downgrade-replay defense).
    #
    # The Fulcio cert SAN carries the exact tag the workflow ran against (e.g.
    # ".../sign-installer.yml@refs/tags/v0.5.1-supplychain-wave5"). The
    # $IdentityRegex above only checks that SOME semver-ish tag is present in
    # the SAN - not that the SAN tag matches the tag the operator asked us to
    # install. Without this cross-check, an attacker who serves the (valid-
    # signed) install.ps1 from a PRIOR release under a fresh URL would pass
    # axes A+B+C+D - the freshness gate (Rekor integratedTime <= 24h) is the
    # only barrier and ages out within a day.
    #
    # The defense: extract the @refs/tags/<X> suffix from the SAN, compare
    # BYTE-FOR-BYTE against the resolved $Tag. Drift => fail-closed.
    if ($CertSan -match '@refs/tags/(.+)$') {
        $SanTag = $matches[1]
    } else {
        Write-Host '  axis A: could not extract @refs/tags/<tag> suffix from SAN - refusing.' -ForegroundColor Red
        Write-Host "    SAN: $CertSan" -ForegroundColor Red
        exit 1
    }
    if ($SanTag -ne $Tag) {
        Write-Host "  axis A: SAN tag $SanTag does not match requested tag $Tag - refusing (possible downgrade replay)." -ForegroundColor Red
        Write-Host "    SAN:           $CertSan" -ForegroundColor Red
        Write-Host "    SAN tag:       $SanTag" -ForegroundColor Red
        Write-Host "    requested tag: $Tag" -ForegroundColor Red
        Write-Host '  this defends against an attacker serving valid-signed bytes from a' -ForegroundColor Red
        Write-Host '  different release at a fresh URL - see TRUST_ROOT.md axis E.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      axis A tag-binding OK (SAN tag = requested tag = $Tag)" -ForegroundColor Green

    # Axis B: re-assert the inlined-pubkey fingerprint against the on-disk
    # file cosign actually consumed in [5/13]. Defence against race-condition
    # swaps of $InlinedPubkey between stages.
    $FinalFp = Get-PubkeyFingerprint (Get-Content -Raw -Path $InlinedPubkey)
    if ($FinalFp -ne $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT) {
        Write-Host '  axis B fingerprint drifted between stages!' -ForegroundColor Red
        Write-Host "    pinned:       $OFFLINE_CEREMONY_PUBKEY_FINGERPRINT" -ForegroundColor Red
        Write-Host "    on-disk now:  $FinalFp" -ForegroundColor Red
        Write-Host '  refusing.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      axis A SAN matches pinned regex: $CertSan" -ForegroundColor Green
    Write-Host "      axis B key fingerprint stable:    $FinalFp" -ForegroundColor Green

    # --------------------------------------------------------------- bootstrap slsa-verifier
    Write-Host ''
    Write-Host "[8/13] Bootstrapping slsa-verifier $SLSA_VERIFIER_VERSION (SHA-256 pinned)..." -ForegroundColor Yellow

    # Same trust pattern as cosign in [2/13]: SHA-256-pinned binary download.
    # slsa-verifier is the reference implementation that knows how to verify
    # SLSA L3 build provenance against a release. We refuse to execute the
    # downloaded binary unless its hash matches the pin in TRUST_ROOT.md
    # section 4.
    $SlsaVerifierBin = Join-Path $Staging 'slsa-verifier.exe'
    $SlsaVerifierUrl = "https://github.com/slsa-framework/slsa-verifier/releases/download/$SLSA_VERIFIER_VERSION/slsa-verifier-windows-amd64.exe"

    try {
        Invoke-WebRequest -Uri $SlsaVerifierUrl -OutFile $SlsaVerifierBin -UseBasicParsing | Out-Null
    } catch {
        Write-Host "  failed to download slsa-verifier from $SlsaVerifierUrl" -ForegroundColor Red
        exit 1
    }

    $ActualSlsaSha = (Get-FileHash -Path $SlsaVerifierBin -Algorithm SHA256).Hash.ToLower()
    if ($ActualSlsaSha -ne $SLSA_VERIFIER_SHA256_WINDOWS) {
        Write-Host '  slsa-verifier SHA-256 mismatch!' -ForegroundColor Red
        Write-Host "    expected: $SLSA_VERIFIER_SHA256_WINDOWS" -ForegroundColor Red
        Write-Host "    actual:   $ActualSlsaSha" -ForegroundColor Red
        Write-Host '  abort - the downloaded slsa-verifier is NOT the version this verifier was rooted against.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      slsa-verifier SHA-256 OK ($SLSA_VERIFIER_SHA256_WINDOWS)" -ForegroundColor Green

    # --------------------------------------------------------------- SLSA L3 provenance (axis C, part 1)
    Write-Host ''
    Write-Host '[9/13] Verifying SLSA L3 build provenance (axis C - independent attestation of build environment)...' -ForegroundColor Yellow

    # The workflow uploads a SLSA L3 in-toto provenance attestation alongside
    # the artifacts under the well-known name $SLSA_PROVENANCE_FILENAME. It is
    # generated by the SLSA GitHub generator (slsa-framework/slsa-github-generator)
    # which itself runs in a hardened reusable workflow with a non-falsifiable
    # builder identity - the provenance's "builder.id" cannot be set by the
    # calling repo, so an attacker who poisons sign-installer.yml still cannot
    # emit a provenance that claims a different builder.
    #
    # Axis C catches a class of attacks axis A cannot: an attacker who steals
    # an OIDC token (or the ability to mint Fulcio certs under our identity)
    # can re-sign a tampered binary with the same identity, defeating axis A.
    # But the SLSA provenance records the ENTIRE build environment - sources,
    # inputs, build commands, runner image. Changed inputs => changed digests
    # => slsa-verifier rejects.
    $SlsaProvenancePath = Join-Path $Staging $SLSA_PROVENANCE_FILENAME
    $ProvenanceUrl      = "$RelBase/$SLSA_PROVENANCE_FILENAME"

    try {
        Invoke-WebRequest -Uri $ProvenanceUrl -OutFile $SlsaProvenancePath -UseBasicParsing | Out-Null
    } catch {
        Write-Host "  failed to fetch SLSA provenance from $ProvenanceUrl" -ForegroundColor Red
        Write-Host "  is the tag '$Tag' actually a Wave-3 release with SLSA L3 provenance?" -ForegroundColor Red
        exit 1
    }
    Write-Host "      SLSA provenance downloaded ($SLSA_PROVENANCE_FILENAME)" -ForegroundColor Green

    # AXIS-C INVOCATION (part 1): slsa-verifier verify-artifact pins:
    #   --source-uri:  the repo whose build emitted the provenance (must
    #                  match EXPECTED_REPO)
    #   --source-tag:  the exact release tag the provenance was generated
    #                  for (defends against provenance-from-old-tag replay)
    #   positional:    the artifact whose digest must appear in the
    #                  provenance's `subject` array
    # slsa-verifier internally fetches the SLSA generator's builder identity
    # from the bundled Sigstore cert, cross-checks it against the SLSA
    # generator's own pinned issuer/identity, and rejects any mismatch. A
    # failure here means the artifact was NOT produced by the attested
    # build, OR the provenance is for a different tag, OR the source-repo
    # does not match. All three are fail-closed.
    & $SlsaVerifierBin verify-artifact `
        --provenance-path $SlsaProvenancePath `
        --source-uri      $EXPECTED_SLSA_SOURCE_URI `
        --source-tag      $Tag `
        $Artifact
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  axis C (SLSA L3): slsa-verifier verify-artifact FAILED.' -ForegroundColor Red
        Write-Host '  the SLSA provenance does NOT attest to a build of install.ps1' -ForegroundColor Red
        Write-Host "  from $EXPECTED_SLSA_SOURCE_URI @ tag $Tag." -ForegroundColor Red
        Write-Host '  refusing to execute - Wave 3 demands 3-of-3 axes to pass.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      axis C OK (SLSA L3: source=$EXPECTED_SLSA_SOURCE_URI, tag=$Tag)" -ForegroundColor Green

    # --------------------------------------------------------------- content-anchor layout pin (axis C, part 2)
    Write-Host ''
    Write-Host '[10/13] Verifying content-anchor layout functionary pin (axis C - supply-chain layout match)...' -ForegroundColor Yellow
    # WAVE-5 HONESTY NOTE (audit Finding 3): the layout document is in-toto-
    # shaped but UNSIGNED - authenticity comes from this signed verifier
    # byte-comparing its identity_regexp against EXPECTED_INTOTO_IDENTITY_REGEXP.
    # The document is NOT a spec-compliant in-toto signed layout.

    # Fetch the in-toto layout template uploaded alongside the artifacts. SA-2
    # is responsible for uploading $INTOTO_LAYOUT_FILENAME to the same release.
    # The layout declares which functionary identity is allowed to sign the
    # build step. We cross-check that:
    #
    #   (a) the layout's functionary identity_regexp equals our pinned
    #       expected string (EXPECTED_INTOTO_IDENTITY_REGEXP) - defends
    #       against an attacker who swaps the layout in the release to
    #       widen the regexp;
    #   (b) the regexp is NOT ".*" or any other catch-all (sanity check
    #       that would also catch a typoed or maliciously-loosened pin);
    #   (c) the issuer URL matches EXPECTED_OIDC_ISSUER, tying the layout's
    #       functionary back to the same OIDC root axis A trusts.
    #
    # A discrepancy between the layout-as-uploaded and the layout-as-pinned
    # means the supply-chain layout has been tampered with - fail-closed.
    $LayoutPath = Join-Path $Staging $INTOTO_LAYOUT_FILENAME
    $LayoutUrl  = "$RelBase/$INTOTO_LAYOUT_FILENAME"

    try {
        Invoke-WebRequest -Uri $LayoutUrl -OutFile $LayoutPath -UseBasicParsing | Out-Null
    } catch {
        Write-Host "  failed to fetch in-toto layout from $LayoutUrl" -ForegroundColor Red
        Write-Host '  is the release missing the layout.template.json upload?' -ForegroundColor Red
        exit 1
    }

    $LayoutJson = Get-Content -Raw -Path $LayoutPath | ConvertFrom-Json

    # Wave-5 audit Finding 3: document renamed from "_type":"layout" (which
    # implied a signed in-toto layout) to "_type":"content-anchor" (an
    # honest description). Accept either during the v0.5 -> v0.6 transition.
    if (($LayoutJson._type -ne 'content-anchor') -and ($LayoutJson._type -ne 'layout')) {
        Write-Host "  layout._type not in {'content-anchor','layout'} (got '$($LayoutJson._type)') - refusing." -ForegroundColor Red
        exit 1
    }

    # Walk every declared key and find the Sigstore/OIDC functionary entry.
    # We accept exactly one such functionary (more would mean the layout
    # allows multiple build identities, which we did not authorise).
    $OidcEntries = @()
    if (-not $LayoutJson.keys) {
        Write-Host '  layout.keys is empty - no functionary pinned - refusing.' -ForegroundColor Red
        exit 1
    }
    foreach ($keyId in $LayoutJson.keys.PSObject.Properties.Name) {
        $kdef = $LayoutJson.keys.$keyId
        $kval = $kdef.keyval
        if ($kval -and (($kval.PSObject.Properties.Name -contains 'identity_regexp') -or ($kval.PSObject.Properties.Name -contains 'issuer'))) {
            $OidcEntries += [PSCustomObject]@{
                KeyId  = $keyId
                Regexp = $kval.identity_regexp
                Issuer = $kval.issuer
            }
        }
    }

    if ($OidcEntries.Count -eq 0) {
        Write-Host '  layout has no OIDC functionary entry (looking for keyval.identity_regexp) - refusing.' -ForegroundColor Red
        exit 1
    }
    if ($OidcEntries.Count -ne 1) {
        Write-Host "  layout has $($OidcEntries.Count) OIDC functionary entries; exactly 1 required - refusing." -ForegroundColor Red
        exit 1
    }

    $LayoutEntry = $OidcEntries[0]
    if (-not $LayoutEntry.Regexp) {
        Write-Host "  functionary $($LayoutEntry.KeyId) missing keyval.identity_regexp - refusing." -ForegroundColor Red
        exit 1
    }
    if (-not $LayoutEntry.Issuer) {
        Write-Host "  functionary $($LayoutEntry.KeyId) missing keyval.issuer - refusing." -ForegroundColor Red
        exit 1
    }

    # Anti-corner-cutting: reject catch-all regexps explicitly. If a future
    # layout author tries to "simplify" the pin with .* this stage breaks
    # loudly instead of silently accepting any signer.
    $CatchAlls = @('.*', '.+', '^.*$', '^.+$', '')
    if ($CatchAlls -contains $LayoutEntry.Regexp) {
        Write-Host "  functionary identity_regexp is a catch-all ('$($LayoutEntry.Regexp)') - refusing." -ForegroundColor Red
        exit 1
    }

    if ($LayoutEntry.Regexp -ne $EXPECTED_INTOTO_IDENTITY_REGEXP) {
        Write-Host '  identity_regexp mismatch:' -ForegroundColor Red
        Write-Host "    expected: $EXPECTED_INTOTO_IDENTITY_REGEXP" -ForegroundColor Red
        Write-Host "    actual:   $($LayoutEntry.Regexp)" -ForegroundColor Red
        Write-Host '  refusing to execute - supply-chain layout has drifted from the pin.' -ForegroundColor Red
        exit 1
    }
    if ($LayoutEntry.Issuer -ne $EXPECTED_OIDC_ISSUER) {
        Write-Host '  issuer mismatch:' -ForegroundColor Red
        Write-Host "    expected: $EXPECTED_OIDC_ISSUER" -ForegroundColor Red
        Write-Host "    actual:   $($LayoutEntry.Issuer)" -ForegroundColor Red
        Write-Host '  refusing.' -ForegroundColor Red
        exit 1
    }
    Write-Host "      axis C OK (in-toto layout: keyid=$($LayoutEntry.KeyId))" -ForegroundColor Green
    Write-Host "      identity_regexp pin: $EXPECTED_INTOTO_IDENTITY_REGEXP" -ForegroundColor Green

    # --------------------------------------------------------------- classical axes complete
    Write-Host ''
    Write-Host '[11/13] All classical axes passed (axes A+B+C). Entering Wave 4 PQ transition...' -ForegroundColor Yellow
    Write-Host ''

    # --------------------------------------------------------------- post-quantum signature fetch (axis D, part 1)
    Write-Host "[12/13] Fetching ML-DSA-65 post-quantum signature + released pubkey (axis D - FIPS 204)..." -ForegroundColor Yellow

    # Released-asset filenames follow the workflow's upload convention:
    #   install.ps1.mldsa.sig - ML-DSA-65 raw signature (~3309 bytes)
    #   pq-mldsa65.pub.pem    - pubkey asset for fingerprint cross-check
    $MldsaSig          = Join-Path $Staging ('install.ps1' + $PQ_MLDSA65_SIG_EXT)
    $PqInlinedPubkey   = Join-Path $Staging 'pq-mldsa65.pub.inlined'
    $PqReleasedPubkey  = Join-Path $Staging 'pq-mldsa65.pub.released'

    # Write the inlined pubkey to disk; openssl.exe -verify wants a file path.
    Set-Content -Path $PqInlinedPubkey -Value $PQ_MLDSA65_PUBKEY_PEM -Encoding ascii -NoNewline

    # Fetch the signature. Failure here means the release was published WITHOUT
    # the Wave 4 PQ asset; we treat that as a hard failure unless the operator
    # sets $env:JARVIS_INSTALL_ALLOW_NO_PQ=1 (loud-logged so an auditor sees it).
    $PqSigAvailable = $true
    try {
        Invoke-WebRequest -Uri ("$RelBase/install.ps1$PQ_MLDSA65_SIG_EXT") -OutFile $MldsaSig -UseBasicParsing | Out-Null
    } catch {
        Write-Host "  failed to fetch $RelBase/install.ps1$PQ_MLDSA65_SIG_EXT" -ForegroundColor Red
        Write-Host '  this release does NOT publish a Wave 4 ML-DSA-65 signature for install.ps1.' -ForegroundColor Red
        if ($env:JARVIS_INSTALL_ALLOW_NO_PQ -ne '1') {
            Write-Host '  refusing - Wave 4 axis D requires <artifact>.mldsa.sig per release.' -ForegroundColor Red
            Write-Host '  if this is a legacy (pre-Wave-4) tag, set $env:JARVIS_INSTALL_ALLOW_NO_PQ=1 to' -ForegroundColor Red
            Write-Host '  bypass axis D (classical axes A+B+C still enforced); read TRUST_ROOT.md section 5 first.' -ForegroundColor Red
            exit 1
        }
        Write-Host '  proceeding under $env:JARVIS_INSTALL_ALLOW_NO_PQ=1 (override acknowledged; axis D bypassed).' -ForegroundColor Red
        $PqSigAvailable = $false
    }

    # Fetch released pubkey and assert SHA-256(DER(SPKI)) equality vs inlined.
    $PqPubkeyReleasedAvailable = $true
    try {
        Invoke-WebRequest -Uri "$RelBase/$PQ_MLDSA65_PUBKEY_ASSET_NAME" -OutFile $PqReleasedPubkey -UseBasicParsing | Out-Null
    } catch {
        Write-Host "  failed to fetch $RelBase/$PQ_MLDSA65_PUBKEY_ASSET_NAME" -ForegroundColor Red
        Write-Host '  the release MUST publish the ML-DSA-65 public key as a cross-check asset.' -ForegroundColor Red
        if ($env:JARVIS_INSTALL_ALLOW_NO_PQ -ne '1') {
            exit 1
        }
        Write-Host '  proceeding under $env:JARVIS_INSTALL_ALLOW_NO_PQ=1 (override acknowledged).' -ForegroundColor Red
        $PqPubkeyReleasedAvailable = $false
    }

    # Inlined-pubkey fingerprint cross-check (defense vs in-script tamper).
    # The Get-PubkeyFingerprint helper computes SHA-256(DER(SPKI)) without
    # needing openssl.exe on PATH - works on any Windows host.
    $PqInlinedFp = Get-PubkeyFingerprint $PQ_MLDSA65_PUBKEY_PEM
    if (-not $PqInlinedFp) {
        Write-Host '  could not compute fingerprint of inlined ML-DSA-65 pubkey - refusing.' -ForegroundColor Red
        exit 1
    }
    if ($PqInlinedFp -ne $PQ_MLDSA65_PUBKEY_FINGERPRINT) {
        Write-Host '  inlined ML-DSA-65 pubkey fingerprint mismatch!' -ForegroundColor Red
        Write-Host "    expected (pinned in verifier): $PQ_MLDSA65_PUBKEY_FINGERPRINT" -ForegroundColor Red
        Write-Host "    actual   (heredoc):            $PqInlinedFp" -ForegroundColor Red
        Write-Host '  this script has been tampered with - refusing.' -ForegroundColor Red
        exit 1
    }
    if ($PqPubkeyReleasedAvailable) {
        $PqReleasedFp = Get-PubkeyFingerprint (Get-Content -Raw -Path $PqReleasedPubkey)
        if (-not $PqReleasedFp) {
            Write-Host '  could not compute fingerprint of released ML-DSA-65 pubkey - refusing.' -ForegroundColor Red
            exit 1
        }
        if ($PqReleasedFp -ne $PQ_MLDSA65_PUBKEY_FINGERPRINT) {
            Write-Host '  released ML-DSA-65 pubkey fingerprint mismatch!' -ForegroundColor Red
            Write-Host "    expected (pinned in verifier): $PQ_MLDSA65_PUBKEY_FINGERPRINT" -ForegroundColor Red
            Write-Host "    actual   (release asset):      $PqReleasedFp" -ForegroundColor Red
            Write-Host '  the published pq-mldsa65.pub.pem does NOT match the verifier pin - refusing.' -ForegroundColor Red
            exit 1
        }
    }
    if ($PqSigAvailable) {
        Write-Host "      PQ signature fetched ($MldsaSig)" -ForegroundColor Green
    } else {
        Write-Host '      PQ signature fetch BYPASSED via $env:JARVIS_INSTALL_ALLOW_NO_PQ=1' -ForegroundColor Yellow
    }
    Write-Host "      ML-DSA-65 pubkey fingerprint OK ($PQ_MLDSA65_PUBKEY_FINGERPRINT)" -ForegroundColor Green

    # --------------------------------------------------------------- ML-DSA-65 verify (axis D, part 2) + handoff
    Write-Host ''
    Write-Host '[13/13] Verifying ML-DSA-65 post-quantum signature (axis D) and handing off to install.ps1...' -ForegroundColor Yellow

    # TRANSITION MODE gate:
    #   - If openssl.exe >= 3.5 is on PATH: verify the ML-DSA-65 signature
    #     hard-closed; any failure aborts.
    #   - Otherwise: SKIP with a clear WARNING. Classical axes A+B+C have
    #     already validated, so the installer is still authenticated against
    #     three independent trust roots. The warning is loud and explicit so
    #     the operator sees what is happening - never silent.
    $PqVerifyRan = $false
    if ($PqSigAvailable) {
        $OpensslCmd = Get-Command openssl.exe -ErrorAction SilentlyContinue
        $OpensslVersionLine = $null
        if ($OpensslCmd) {
            try {
                $OpensslVersionLine = (& openssl.exe version 2>&1 | Select-Object -First 1) -as [string]
            } catch {
                $OpensslVersionLine = $null
            }
        }
        # Require OpenSSL 3.5 or newer. Pattern matches "OpenSSL 3.5.x",
        # "OpenSSL 3.6+...", "OpenSSL 4.x+", "OpenSSL 10+...". Matches the
        # bash verifier's PQ_MLDSA65_MIN_OPENSSL_REGEX.
        $PqMinOpenSslRegex = 'OpenSSL\s+(3\.([5-9]|[1-9][0-9])|[4-9]\.|[1-9][0-9]+\.)'
        if (-not $OpensslVersionLine) {
            Write-Host '  WARNING: openssl.exe is not on PATH - PQ verification SKIPPED (OpenSSL 3.5+ not available).' -ForegroundColor Yellow
            Write-Host '  axis D (Wave 4 ML-DSA-65) was NOT verified on this host.' -ForegroundColor Yellow
            Write-Host '  classical axes A+B+C have validated; proceeding in TRANSITION MODE.' -ForegroundColor Yellow
        } elseif ($OpensslVersionLine -match $PqMinOpenSslRegex) {
            # AXIS-D INVOCATION: openssl pkeyutl -verify (ML-DSA-65, raw-message).
            # The `-rawin` flag is FIPS 204 compliant: openssl runs SHAKE-256
            # internally per the standard. The signature blob is the raw
            # ~3309-byte FIPS 204 signature - matches the workflow's
            # `openssl pkeyutl -sign -rawin` output byte-for-byte.
            & openssl.exe pkeyutl -verify `
                -pubin -inkey $PqInlinedPubkey `
                -rawin -in $Artifact `
                -sigfile $MldsaSig
            if ($LASTEXITCODE -ne 0) {
                Write-Host '  axis D: ML-DSA-65 post-quantum signature check FAILED.' -ForegroundColor Red
                Write-Host "  install.ps1$PQ_MLDSA65_SIG_EXT does NOT validate against the pinned" -ForegroundColor Red
                Write-Host "  ML-DSA-65 pubkey (fingerprint $PQ_MLDSA65_PUBKEY_FINGERPRINT)." -ForegroundColor Red
                Write-Host '  refusing to execute - Wave 4 demands axis D to validate when openssl >= 3.5.' -ForegroundColor Red
                exit 1
            }
            $PqVerifyRan = $true
            Write-Host "      axis D OK (ML-DSA-65 / FIPS 204, key fingerprint=$PQ_MLDSA65_PUBKEY_FINGERPRINT)" -ForegroundColor Green
        } else {
            Write-Host '  WARNING: PQ verification SKIPPED (OpenSSL 3.5+ not available).' -ForegroundColor Yellow
            Write-Host "    local openssl: $OpensslVersionLine" -ForegroundColor Yellow
            Write-Host '    required for axis D: OpenSSL 3.5 or newer (ML-DSA support landed in 3.5.0).' -ForegroundColor Yellow
            Write-Host '  classical axes A+B+C have validated; proceeding in TRANSITION MODE.' -ForegroundColor Yellow
            Write-Host '  to enforce axis D, install OpenSSL >= 3.5 and re-run.' -ForegroundColor Yellow
        }
    } else {
        Write-Host '  WARNING: PQ verification SKIPPED (no .mldsa.sig fetched; $env:JARVIS_INSTALL_ALLOW_NO_PQ=1 set).' -ForegroundColor Yellow
    }
    if (-not $PqVerifyRan) {
        Write-Host '      Wave 4 axis D status: SKIPPED (transition mode). axes A+B+C validated.' -ForegroundColor Yellow
    }
    Write-Host ''

    # ----------------------------------------------------------------------- payload-commit (axis E, Wave-5 audit Finding 2)
    Write-Host '[axis E] Verifying payload-commit pin (Wave-5 - binds cloned tree to signed commit)...' -ForegroundColor Yellow

    # WAVE 5 PAYLOAD-COMMIT PIN - Wave-5 audit Finding 2.
    #
    # install.ps1 does `git clone --depth 1 --branch main`, and installer.py
    # does zero signature checks on the cloned tree. The four-axis chain
    # ends at the bootstrap - whatever is on `main` at install time runs
    # UNVERIFIED. The workflow emits `payload-commit.txt` containing the
    # exact commit SHA of the tagged release, signed with Wave 1+2+4 axes.
    # We authenticate it, extract the SHA, and export it to install.ps1
    # via $env:JARVIS_PAYLOAD_COMMIT. install.ps1 then `git checkout`s
    # that SHA so the cloned tree is bound to the commit that existed at
    # sign-time.

    $PayloadCommitFile      = Join-Path $Staging 'payload-commit.txt'
    $PayloadCommitSig       = Join-Path $Staging 'payload-commit.txt.sig'
    $PayloadCommitPem       = Join-Path $Staging 'payload-commit.txt.pem'
    $PayloadCommitBundle    = Join-Path $Staging 'payload-commit.txt.bundle'
    $PayloadCommitCosignSig = Join-Path $Staging 'payload-commit.txt.cosign.sig'
    $PayloadCommitMldsaSig  = Join-Path $Staging 'payload-commit.txt.mldsa.sig'

    $PayloadCommitAvailable = $true
    try {
        Invoke-WebRequest -Uri "$RelBase/payload-commit.txt" -OutFile $PayloadCommitFile -UseBasicParsing -ErrorAction Stop | Out-Null
    } catch {
        Write-Host "  payload-commit.txt not present in release $Tag (likely a pre-Wave-5 tag)." -ForegroundColor Yellow
        if ($env:JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN -ne '1') {
            Write-Host '  Wave-5 axis E requires payload-commit.txt in the release.' -ForegroundColor Red
            Write-Host '  if this is a legacy (pre-Wave-5) tag, set $env:JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1' -ForegroundColor Red
            Write-Host '  to bypass axis E. The classical axes still enforce install.ps1 authenticity,' -ForegroundColor Red
            Write-Host '  but the cloned tree is NOT bound to a signed commit. Read TRUST_ROOT.md section 10.' -ForegroundColor Red
            exit 1
        }
        Write-Host '  proceeding under $env:JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1 (override acknowledged; axis E bypassed).' -ForegroundColor Yellow
        $PayloadCommitAvailable = $false
    }

    if ($PayloadCommitAvailable) {
        foreach ($name in 'payload-commit.txt.sig','payload-commit.txt.pem','payload-commit.txt.bundle','payload-commit.txt.cosign.sig') {
            try {
                Invoke-WebRequest -Uri "$RelBase/$name" -OutFile (Join-Path $Staging $name) -UseBasicParsing -ErrorAction Stop | Out-Null
            } catch {
                Write-Host "  failed to fetch $RelBase/$name - refusing." -ForegroundColor Red
                Write-Host '  Wave-5 axis E requires complete signing trio + offline-ceremony sig for payload-commit.txt.' -ForegroundColor Red
                exit 1
            }
        }

        # AXIS-A on payload-commit.txt
        & $CosignBin verify-blob `
            --certificate                  $PayloadCommitPem `
            --signature                    $PayloadCommitSig `
            --bundle                       $PayloadCommitBundle `
            --certificate-identity-regexp  $IdentityRegex `
            --certificate-oidc-issuer      $EXPECTED_OIDC_ISSUER `
            --insecure-ignore-tlog=false `
            $PayloadCommitFile
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  axis E (payload-commit): axis A (cosign keyless) verification FAILED.' -ForegroundColor Red
            Write-Host '  payload-commit.txt is NOT signed by the same workflow that signed install.ps1.' -ForegroundColor Red
            Write-Host '  refusing - possible attacker-substituted commit pin.' -ForegroundColor Red
            exit 1
        }

        # AXIS-B on payload-commit.txt
        & $CosignBin verify-blob `
            --key       $InlinedPubkey `
            --signature $PayloadCommitCosignSig `
            --insecure-ignore-tlog `
            $PayloadCommitFile
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  axis E (payload-commit): axis B (offline-ceremony Ed25519) verification FAILED.' -ForegroundColor Red
            Write-Host '  refusing - possible attacker-substituted commit pin bypassing axis B.' -ForegroundColor Red
            exit 1
        }

        # AXIS-D on payload-commit.txt (transition-mode)
        $PqOk = $false
        try {
            Invoke-WebRequest -Uri "$RelBase/payload-commit.txt.mldsa.sig" -OutFile $PayloadCommitMldsaSig -UseBasicParsing -ErrorAction Stop | Out-Null
            $PqOk = $true
        } catch {
            Write-Host '  WARNING: payload-commit.txt.mldsa.sig not present (likely a pre-Wave-5.1 release).' -ForegroundColor Yellow
        }
        if ($PqOk) {
            $OpensslCmd2 = Get-Command openssl.exe -ErrorAction SilentlyContinue
            $OpensslVer2 = $null
            if ($OpensslCmd2) {
                try { $OpensslVer2 = (& openssl.exe version 2>&1 | Select-Object -First 1) -as [string] } catch { $OpensslVer2 = $null }
            }
            $PqMinRegex2 = 'OpenSSL\s+(3\.([5-9]|[1-9][0-9])|[4-9]\.|[1-9][0-9]+\.)'
            if ($OpensslVer2 -and ($OpensslVer2 -match $PqMinRegex2)) {
                & openssl.exe pkeyutl -verify `
                    -pubin -inkey $PqInlinedPubkey `
                    -rawin -in $PayloadCommitFile `
                    -sigfile $PayloadCommitMldsaSig
                if ($LASTEXITCODE -ne 0) {
                    Write-Host '  axis E (payload-commit): axis D (ML-DSA-65) verification FAILED.' -ForegroundColor Red
                    Write-Host '  refusing - possible attacker-substituted commit pin bypassing axis D.' -ForegroundColor Red
                    exit 1
                }
                Write-Host '      axis E PQ-verify OK (ML-DSA-65)' -ForegroundColor Green
            } else {
                Write-Host '  WARNING: axis E PQ verification SKIPPED on payload-commit.txt (OpenSSL 3.5+ not available).' -ForegroundColor Yellow
            }
        }

        $PayloadCommit = (Get-Content -Raw -Path $PayloadCommitFile).Trim()
        if ($PayloadCommit -notmatch '^[0-9a-f]{40}([0-9a-f]{24})?$') {
            Write-Host '  axis E: payload-commit.txt content is NOT a well-formed git SHA (40 hex or 64 hex).' -ForegroundColor Red
            Write-Host "    got: $PayloadCommit" -ForegroundColor Red
            Write-Host '  refusing - possible tamper or generation bug.' -ForegroundColor Red
            exit 1
        }
        $env:JARVIS_PAYLOAD_COMMIT = $PayloadCommit
        Write-Host "      axis E OK (payload commit pinned to $PayloadCommit)" -ForegroundColor Green
    } else {
        Write-Host '      axis E SKIPPED via $env:JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1 - install.ps1 will NOT pin the clone to a signed commit.' -ForegroundColor Yellow
    }
    Write-Host ''

    # ------------------------------------------------------------------- requirements.txt (Wave 6 - PyPI transitive hash pin)
    Write-Host '[wave 6] Fetching + authenticating requirements.txt (PyPI transitive hash pin)...' -ForegroundColor Yellow
    # See install-verify.sh for the full rationale. Defends against the
    # 2018 event-stream npm pattern + 2024 polyfill.io CDN pattern.
    $ReqFile         = Join-Path $Staging 'requirements.txt'
    $ReqSig          = Join-Path $Staging 'requirements.txt.sig'
    $ReqPem          = Join-Path $Staging 'requirements.txt.pem'
    $ReqBundle       = Join-Path $Staging 'requirements.txt.bundle'
    $ReqCosignSig    = Join-Path $Staging 'requirements.txt.cosign.sig'
    $ReqMldsaSig     = Join-Path $Staging 'requirements.txt.mldsa.sig'

    $ReqAvailable = $true
    try {
        Invoke-WebRequest -Uri "$RelBase/requirements.txt" -OutFile $ReqFile -UseBasicParsing -ErrorAction Stop | Out-Null
    } catch {
        Write-Host "  requirements.txt not present in release $Tag (likely a pre-Wave-6 tag)." -ForegroundColor Yellow
        if ($env:JARVIS_INSTALL_ALLOW_NO_PIP_HASHES -ne '1') {
            Write-Host '  Wave 6 requires the hash-pinned lockfile in the release.' -ForegroundColor Red
            Write-Host '  if this is a legacy (pre-Wave-6) tag, set' -ForegroundColor Red
            Write-Host '  $env:JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 to bypass.' -ForegroundColor Red
            Write-Host '  read docs/supply-chain/threat-model.md section 11 first.' -ForegroundColor Red
            exit 1
        }
        Write-Host '  proceeding under $env:JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 (override acknowledged).' -ForegroundColor Yellow
        $ReqAvailable = $false
    }

    if ($ReqAvailable) {
        foreach ($name in 'requirements.txt.sig','requirements.txt.pem','requirements.txt.bundle','requirements.txt.cosign.sig') {
            try {
                Invoke-WebRequest -Uri "$RelBase/$name" -OutFile (Join-Path $Staging $name) -UseBasicParsing -ErrorAction Stop | Out-Null
            } catch {
                Write-Host "  failed to fetch $RelBase/$name - refusing." -ForegroundColor Red
                Write-Host '  Wave 6 requires the complete signing trio + offline-ceremony sig for requirements.txt.' -ForegroundColor Red
                exit 1
            }
        }

        # AXIS-A on requirements.txt (Fulcio keyless)
        & $CosignBin verify-blob `
            --certificate                   $ReqPem `
            --signature                     $ReqSig `
            --bundle                        $ReqBundle `
            --certificate-identity-regexp   $IdentityRegex `
            --certificate-oidc-issuer       $EXPECTED_OIDC_ISSUER `
            --insecure-ignore-tlog=false `
            $ReqFile
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  Wave 6: axis A (cosign keyless) verification on requirements.txt FAILED.' -ForegroundColor Red
            Write-Host '  requirements.txt is NOT signed by the same workflow that signed install.ps1.' -ForegroundColor Red
            Write-Host '  refusing - possible attacker-substituted dependency lockfile.' -ForegroundColor Red
            exit 1
        }
        Write-Host '      Wave 6 axis A OK (Fulcio keyless on requirements.txt)' -ForegroundColor Green

        # AXIS-B on requirements.txt (offline-ceremony Ed25519)
        & $CosignBin verify-blob `
            --key                           $InlinedPubkey `
            --signature                     $ReqCosignSig `
            --insecure-ignore-tlog `
            $ReqFile
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  Wave 6: axis B (offline-ceremony Ed25519) verification on requirements.txt FAILED.' -ForegroundColor Red
            Write-Host '  refusing - possible attacker-substituted lockfile bypassing axis B.' -ForegroundColor Red
            exit 1
        }
        Write-Host '      Wave 6 axis B OK (Ed25519 offline-ceremony on requirements.txt)' -ForegroundColor Green

        # AXIS-D on requirements.txt (ML-DSA-65, transition mode)
        $ReqMldsaAvailable = $true
        try {
            Invoke-WebRequest -Uri "$RelBase/requirements.txt.mldsa.sig" -OutFile $ReqMldsaSig -UseBasicParsing -ErrorAction Stop | Out-Null
        } catch {
            Write-Host '  WARNING: requirements.txt.mldsa.sig not present (likely a pre-Wave-6.1 release).' -ForegroundColor Yellow
            $ReqMldsaAvailable = $false
        }
        if ($ReqMldsaAvailable) {
            $OpensslVersionLine2 = $null
            try { $OpensslVersionLine2 = (& openssl.exe version 2>$null | Out-String).Trim() } catch {}
            $PqMinOpenSslRegex2 = 'OpenSSL\s+(3\.([5-9]|[1-9][0-9])|[4-9]\.|[1-9][0-9]+\.)'
            if ($OpensslVersionLine2 -and ($OpensslVersionLine2 -match $PqMinOpenSslRegex2)) {
                & openssl.exe pkeyutl -verify `
                    -pubin -inkey $PqInlinedPubkey `
                    -rawin -in $ReqFile `
                    -sigfile $ReqMldsaSig
                if ($LASTEXITCODE -ne 0) {
                    Write-Host '  Wave 6: axis D (ML-DSA-65) verification on requirements.txt FAILED.' -ForegroundColor Red
                    Write-Host '  refusing - possible attacker-substituted lockfile bypassing axis D.' -ForegroundColor Red
                    exit 1
                }
                Write-Host '      Wave 6 axis D OK (ML-DSA-65 / FIPS 204 on requirements.txt)' -ForegroundColor Green
            } else {
                Write-Host '  WARNING: Wave 6 axis D verification SKIPPED on requirements.txt (OpenSSL 3.5+ not available).' -ForegroundColor Yellow
            }
        }

        # Hash-pin floor sanity check (Wave 6 DoD: >= 50 '--hash=sha256:' lines).
        $HashLineCount = (Select-String -Path $ReqFile -Pattern '^\s*--hash=sha256:' -AllMatches | Measure-Object).Count
        if ($HashLineCount -lt 50) {
            Write-Host "  Wave 6: requirements.txt only carries $HashLineCount '--hash=sha256:' lines (< 50 required)." -ForegroundColor Red
            Write-Host '  refusing - lockfile does not satisfy the Wave 6 hash-pin floor.' -ForegroundColor Red
            exit 1
        }
        Write-Host "      Wave 6 hash-pin floor OK ($HashLineCount '--hash=sha256:' lines)" -ForegroundColor Green

        $env:JARVIS_AUTHENTICATED_REQUIREMENTS = $ReqFile
        Write-Host '      Wave 6 lockfile authenticated and exported as $env:JARVIS_AUTHENTICATED_REQUIREMENTS' -ForegroundColor Green
    } else {
        Write-Host '      Wave 6 SKIPPED via $env:JARVIS_INSTALL_ALLOW_NO_PIP_HASHES=1 - install.ps1 will NOT install with --require-hashes.' -ForegroundColor Yellow
    }
    Write-Host ''

    # PowerShell args propagation is awkward when invoked via `iex`; we
    # forward $args verbatim. The bytes have been authenticated by the
    # CLASSICAL THREE axes (Fulcio keyless A, offline ceremony B, SLSA L3
    # + in-toto C), the POST-QUANTUM fourth axis D (ML-DSA-65, when openssl
    # >= 3.5), AND the Wave-5 payload-commit pin (axis E) which install.ps1
    # consumes via $env:JARVIS_PAYLOAD_COMMIT to bind the cloned tree to
    # the signed commit. When axis D or E degrade to TRANSITION MODE, the
    # remaining axes still guarantee the bytes - every degradation is
    # loud-logged so an auditor sees it in the transcript.
    & powershell -ExecutionPolicy Bypass -File $Artifact @args
    exit $LASTEXITCODE
}
finally {
    Remove-Item -Recurse -Force -Path $Staging -ErrorAction SilentlyContinue
}
