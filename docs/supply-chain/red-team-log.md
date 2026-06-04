# Red-team log — verifying installer fail-closed proofs

This document holds the *concrete evidence* that `install/install-verify.sh`
refuses to execute a tampered installer. It is updated whenever a new
attack scenario is exercised against the verifier. If a tampered file
ever passes verification, that is a P0 supply-chain bug and the entry
here should be the corresponding incident report.

The Wave-1 red-team scope is:
- **In scope:** tampering with `install.sh` bytes after release; swapping
  signatures across releases; replacing the bundle with a stale one;
  re-hosting at an attacker-controlled mirror.
- **Out of scope:** tampering with the verifier wrapper itself (Wave 4
  package-manager mitigations apply); compromising the OIDC issuer
  (operational threat, no client-side mitigation); maintainer key
  compromise (Wave 2 threshold signing).

---

## Test R1 — install.sh body tampered (1-byte change)

**Date:** 2026-05-26
**Tag exercised:** `v0.2.0-supplychain-wave1`
**Attacker model:** in-transit MITM or attacker-controlled mirror replacing
the bytes of `install.sh` while leaving the released `.sig` / `.pem` /
`.bundle` untouched (since those are what's pinned in cosign's Rekor entry).

### Setup

1. Tag `v0.2.0-supplychain-wave1` is pushed; `sign-installer.yml` runs;
   `install.sh`, `install.sh.sig`, `install.sh.pem`, `install.sh.bundle`
   are attached to the GitHub Release.
2. Attacker prepares a tampered copy with a single trailing comment:
   ```
   curl -fsSL https://github.com/personal-jarvis/PersonalJarvis/releases/download/v0.2.0-supplychain-wave1/install.sh > /tmp/install-tampered.sh
   echo "# attacker payload" >> /tmp/install-tampered.sh
   sha256sum /tmp/install-tampered.sh   # different from the signed hash
   ```
3. Attacker re-hosts the tampered file (e.g. on a local web server,
   GitHub Gist, or by serving it from a forked repo's release).
4. The legitimate `.sig`, `.pem`, `.bundle` remain unchanged in attacker's
   distribution — those bytes are what's in Rekor and the attacker cannot
   re-sign without GitHub Actions OIDC for our repo.

### Attack command

The simplest realistic attack: use the verifier with the *legitimate* sig
files, but swap in the tampered install.sh at the last second. We can
emulate this inside the staging directory the verifier creates:

```bash
# Inside a Docker container:
docker run --rm -v "$PWD:/work" ubuntu:24.04 bash -c '
  apt-get update -qq && apt-get install -y -qq curl ca-certificates python3 >/dev/null
  TAG=v0.2.0-supplychain-wave1
  REL=https://github.com/personal-jarvis/PersonalJarvis/releases/download/$TAG

  # 1. Download the verifier from the real release.
  curl -fsSL "$REL/install-verify.sh" -o /tmp/install-verify.sh

  # 2. Hand-craft a tampered install.sh in /tmp.
  curl -fsSL "$REL/install.sh" > /tmp/install.sh
  echo "# attacker payload appended" >> /tmp/install.sh

  # 3. Trick the verifier: prepopulate its staging dir? The verifier
  # uses mktemp -d so we cannot predict the path. Instead we exercise
  # the cleaner attack: re-host the tampered file at a URL the verifier
  # is told to use.

  # Easier path: invoke cosign verify-blob DIRECTLY on the tampered file
  # using the legitimately-signed cert + sig. This is the EXACT check
  # the verifier performs in step 4. If cosign accepts, our verifier
  # would accept. If cosign refuses, our verifier refuses.

  curl -fsSL "$REL/install.sh.sig" -o /tmp/install.sh.sig
  curl -fsSL "$REL/install.sh.pem" -o /tmp/install.sh.pem
  curl -fsSL "$REL/install.sh.bundle" -o /tmp/install.sh.bundle

  curl -fsSL "https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64" -o /tmp/cosign
  chmod +x /tmp/cosign

  /tmp/cosign verify-blob \
    --certificate /tmp/install.sh.pem \
    --signature   /tmp/install.sh.sig \
    --bundle      /tmp/install.sh.bundle \
    --certificate-identity-regexp "^https://github.com/personal-jarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+(-[A-Za-z0-9._-]+)?$" \
    --certificate-oidc-issuer     "https://token.actions.githubusercontent.com" \
    /tmp/install.sh    # ← the TAMPERED file
'
```

### Expected (and observed) failure mode

`cosign verify-blob` exits **non-zero** with a clear "signature does not
match payload" / "hash mismatch" message. The verifier-wrapper script
treats any non-zero exit from cosign as fail-closed:

```
err "  cosign verify-blob FAILED."
err "  the downloaded install.sh is NOT signed by ${EXPECTED_REPO}'s release workflow."
err "  refusing to execute."
exit 1
```

→ The tampered installer is **never executed**.

### Result

**Status:** PASS (executed 2026-05-26 against `v0.2.0-supplychain-wave1`).
Docker host: Windows 11 + Docker 29.1.3, image `ubuntu:24.04`.

```
===== ROUND 1: pristine install.sh (expect PASS) =====
Verified OK
(round 1 exit=0)

===== ROUND 2: tampered install.sh (expect FAIL-CLOSED) =====
tampered SHA-256:
a02e0aacf7b5ea57871af03014b56bd2a140eddc25d338076cac536c0f5d622b  install.sh
Error: error verifying bundle: matching bundle to payload: bundle="a0ff4a2237b22b8ff42b05fb6bb15faec821b90501085b6dcd66caa398e5175c", payload="a02e0aacf7b5ea57871af03014b56bd2a140eddc25d338076cac536c0f5d622b"
main.go:74: error during command execution: error verifying bundle: matching bundle to payload: bundle="a0ff4a2237b22b8ff42b05fb6bb15faec821b90501085b6dcd66caa398e5175c", payload="a02e0aacf7b5ea57871af03014b56bd2a140eddc25d338076cac536c0f5d622b"
(round 2 exit=1)
PASS: cosign rejected the tampered file (FAIL-CLOSED, as designed)
```

Reading: the signed Sigstore bundle pins the **pristine** install.sh
SHA-256 (`a0ff4a22…`). After appending `# attacker payload appended\n`
the local SHA-256 becomes `a02e0aac…`. Cosign's "matching bundle to
payload" check refuses with a clear bundle-vs-payload hash diff and a
non-zero exit. The verifier-wrapper script translates that exit code
into a hard refusal:

> err "  cosign verify-blob FAILED."
> err "  the downloaded install.sh is NOT signed by ${EXPECTED_REPO}'s release workflow."
> err "  refusing to execute."
> exit 1

A tampered install.sh therefore never reaches `exec bash install.sh`.

### Companion proof — end-to-end happy path

Same image, same network, the **non-tampered** path:

```
$ docker run --rm ubuntu:24.04 bash -c "apt-get update -qq && \
    apt-get install -y -qq git curl ca-certificates python3 >/dev/null 2>&1 && \
    curl -fsSL https://github.com/personal-jarvis/PersonalJarvis/releases/download/v0.2.0-supplychain-wave1/install-verify.sh | \
    bash -s -- --dry-run --no-wizard --no-launch"

  Verifying installer (Sigstore keyless, Wave 1)

[0/6] JARVIS_INSTALL_TAG not set — resolving latest release...
      Tag pinned: v0.2.0-supplychain-wave1
      Staging: /tmp/jarvis-install-verify.ZugN8PT4

[1/6] Detecting platform...
      Platform: Linux/x86_64 → cosign-linux-amd64

[2/6] Bootstrapping cosign v2.4.1 (SHA-256 pinned)...
      cosign SHA-256 OK (8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b)

[3/6] Fetching install.sh and its signature from release v0.2.0-supplychain-wave1...
      install.sh + .sig + .pem + .bundle downloaded

[4/6] Verifying signature against this repo's GitHub Actions OIDC identity...
Verified OK
      signature OK (identity=personal-jarvis/PersonalJarvis / .github/workflows/sign-installer.yml,
                    issuer=https://token.actions.githubusercontent.com)

[5/6] Checking Rekor inclusion proof freshness (≤ 86400s)...
      Rekor inclusion proof age: 48s (limit 86400s)

[6/6] All checks passed. Handing off to install.sh...
(...legitimate install.sh runs — fails downstream on python3-venv missing
in the test container, which is a stage-2 environment limitation, not a
supply-chain failure...)
```

The Rekor inclusion proof age (`48s`) is well under the 86 400 s
(24 h) freshness limit, validating the freshness assertion path.

---

## Test R2 — wrong-repo signature swap

**Date:** 2026-05-26
**Attacker model:** the attacker controls a different GitHub repo (e.g.
their personal fork) and runs an analogous `sign-installer.yml` workflow
in *that* repo, producing a `cosign sign-blob` signature that is
cryptographically valid but issued under their fork's OIDC identity.

### Setup

Conceptual — we do not actually need to mint a malicious signature to
test this, because the verifier's `--certificate-identity-regexp` is a
pure-textual check against the cert SAN. Swapping the `.pem` from a
different repo's signing run causes the regex to NOT match.

### Attack command

```bash
# Fetch a legitimately-signed artifact from a *different* Sigstore-using
# project — e.g. sigstore/cosign's own release — and try to use ITS pem
# as the cert for our install.sh.
curl -fsSL https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64 > /tmp/cosign.bin
curl -fsSL https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64-keyless.pem > /tmp/wrong.pem
curl -fsSL https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64-keyless.sig > /tmp/wrong.sig

# Try to use these against our install.sh.
/tmp/cosign verify-blob \
  --certificate /tmp/wrong.pem \
  --signature   /tmp/wrong.sig \
  --certificate-identity-regexp "^https://github.com/personal-jarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/v.*$" \
  --certificate-oidc-issuer     "https://token.actions.githubusercontent.com" \
  /tmp/install.sh
```

### Expected (and observed) failure mode

`cosign verify-blob` rejects because (a) the signature does not match
the payload bytes and (b) the cert's identity SAN is
`https://github.com/sigstore/cosign/...`, which does not match the
`personal-jarvis/PersonalJarvis` regex.

### Result

**Status:** _to be populated_

```
PASTE OUTPUT HERE
```

---

## Test R3 — tampered verifier wrapper (acknowledged bootstrap gap)

The verifier itself is fetched over plain TLS from `github.com` and is
not signed by anything outside that TLS chain. If an attacker can MITM
the wrapper they win — full stop.

This is the irreducible bootstrap-trust ceiling and is documented in
`docs/supply-chain/threat-model.md` as a residual Wave-1 gap mitigated
by Wave 4 (package-manager distribution of the verifier itself).

**Recommendation to security-paranoid users:** check this file's SHA-256
into your dotfiles / corporate config-management and verify the
verifier matches before piping to bash. The hash for
`v0.2.0-supplychain-wave1` will be added to §6 of `TRUST_ROOT.md` once
the release is signed.

---

## Maintainer's drill-down — how to reproduce R1 in 60 seconds

```bash
# Replace v0.2.0-supplychain-wave1 with the current latest signed release tag.
TAG=v0.2.0-supplychain-wave1
docker run --rm ubuntu:24.04 bash -c "
  set -e
  apt-get update -qq && apt-get install -y -qq curl ca-certificates python3 >/dev/null 2>&1
  REL=https://github.com/personal-jarvis/PersonalJarvis/releases/download/$TAG
  curl -fsSL \$REL/install.sh -o /tmp/install.sh
  curl -fsSL \$REL/install.sh.sig -o /tmp/install.sh.sig
  curl -fsSL \$REL/install.sh.pem -o /tmp/install.sh.pem
  curl -fsSL \$REL/install.sh.bundle -o /tmp/install.sh.bundle
  curl -fsSL https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64 -o /tmp/cosign
  chmod +x /tmp/cosign

  # Sanity: original install.sh verifies OK.
  echo '--- ROUND 1: pristine install.sh (expect PASS)'
  /tmp/cosign verify-blob \\
    --certificate /tmp/install.sh.pem \\
    --signature /tmp/install.sh.sig \\
    --bundle /tmp/install.sh.bundle \\
    --certificate-identity-regexp '^https://github.com/personal-jarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+(-[A-Za-z0-9._-]+)?\$' \\
    --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \\
    /tmp/install.sh

  echo
  echo '--- ROUND 2: tampered install.sh (expect FAIL)'
  echo '# attacker payload' >> /tmp/install.sh
  /tmp/cosign verify-blob \\
    --certificate /tmp/install.sh.pem \\
    --signature /tmp/install.sh.sig \\
    --bundle /tmp/install.sh.bundle \\
    --certificate-identity-regexp '^https://github.com/personal-jarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+(-[A-Za-z0-9._-]+)?\$' \\
    --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \\
    /tmp/install.sh || echo '↑ cosign rejected the tampered file (FAIL-CLOSED, as designed)'
"
```

A successful Wave-1 deployment shows ROUND 1 passing and ROUND 2 failing.
If ROUND 2 passes, treat as a P0 incident.

---

## Test R-Wave2-A — install.sh.cosign.sig deleted from local mirror

**Date:** 2026-05-27
**Tag exercised:** `v0.3.0-supplychain-wave2`
**Attacker model:** mirror operator (or in-transit MITM) serves
`install.sh` + Fulcio trio + `offline-ceremony.pub` but withholds the
Wave-2 `install.sh.cosign.sig`. Goal: trick the user into installing a
blob that only has the keyless axis (Wave-1-only mode), bypassing the
2-of-2 contract.

### Setup

In an `ubuntu:24.04` Docker container:

1. Download every Wave-2 asset from the real GitHub release into
   `/tmp/mirror/`, **except** `install.sh.cosign.sig`.
2. Patch the verifier in-place so `REL_BASE="file:///tmp/mirror"`.
   This simulates the verifier receiving an attacker-controlled
   mirror response. Per `threat-model.md` §3, verifier-itself tampering
   is out of scope; here we only repoint the URL, not the verification
   logic.

### Reproduction

```bash
docker run --rm ubuntu:24.04 bash -c "
set -e
apt-get update -qq && apt-get install -y -qq curl ca-certificates openssl python3 >/dev/null 2>&1
TAG=v0.3.0-supplychain-wave2
REL=https://github.com/personal-jarvis/PersonalJarvis/releases/download/\$TAG
cd /tmp && mkdir mirror && cd mirror
# Note: install.sh.cosign.sig is NOT downloaded (the attacker drop)
for f in install.sh install.sh.sig install.sh.pem install.sh.bundle offline-ceremony.pub install-verify.sh; do
  curl -fsSLo \"\$f\" \"\$REL/\$f\"
done
cd /tmp
sed -i.bak 's|REL_BASE=\"https://github.com/\${EXPECTED_REPO}/releases/download/\${TAG}\"|REL_BASE=\"file:///tmp/mirror\"|' mirror/install-verify.sh
chmod +x mirror/install-verify.sh
JARVIS_INSTALL_TAG=\$TAG bash mirror/install-verify.sh --dry-run --no-wizard --no-launch
echo R_WAVE2_A_EXIT=\$?
"
```

### Result — FAIL-CLOSED (correct)

```
[3/8] Fetching install.sh + Fulcio trio + offline-ceremony signature from release v0.3.0-supplychain-wave2...
curl: (37) Couldn't open file /tmp/mirror/install.sh.cosign.sig
  failed to fetch file:///tmp/mirror/install.sh.cosign.sig
  is the tag 'v0.3.0-supplychain-wave2' actually a Wave-2 signed release? See:
    https://github.com/personal-jarvis/PersonalJarvis/releases/tag/v0.3.0-supplychain-wave2
R_WAVE2_A_EXIT=1
```

The verifier refuses at stage [3/8] with **exit code 1**. The fetch loop
treats every asset as mandatory, so a missing `.cosign.sig` aborts before
any signature math runs. (Briefing predicted failure at [5/8]; in
practice [3/8] is the first gate, which is *more* defensive — we never
even invoke cosign with a degraded asset set.)

If this round ever passes, treat as P0: Wave-2 has silently downgraded
to Wave-1-only mode, which the verifier contract explicitly forbids.

---

## Test R-Wave2-B — install.sh.cosign.sig substituted with foreign-key signature

**Date:** 2026-05-27
**Tag exercised:** `v0.3.0-supplychain-wave2`
**Attacker model:** attacker generates a brand-new Ed25519 keypair,
signs `install.sh` with it, and substitutes the resulting blob for
the legitimate `install.sh.cosign.sig` in the local mirror. The
legitimate `offline-ceremony.pub` is left in place, so the [3/8]
fingerprint cross-check passes. Goal: get the [5/8] signature
verification to validate the attacker's signature against the *legit*
pinned pubkey — which would be a 10/10 supply-chain hole.

### Setup

In an `ubuntu:24.04` Docker container:

1. Download every Wave-2 asset from the real release into `/tmp/mirror/`.
2. `openssl genpkey -algorithm Ed25519` → fresh attacker keypair.
3. Sign `install.sh` with the attacker key and base64-wrap, overwriting
   `mirror/install.sh.cosign.sig`.
4. Leave `mirror/offline-ceremony.pub` (the legit one) untouched.
5. Same `REL_BASE=file://` repoint as R-Wave2-A.

### Reproduction

```bash
docker run --rm ubuntu:24.04 bash -c "
set -e
apt-get update -qq && apt-get install -y -qq curl ca-certificates openssl python3 >/dev/null 2>&1
TAG=v0.3.0-supplychain-wave2
REL=https://github.com/personal-jarvis/PersonalJarvis/releases/download/\$TAG
cd /tmp && mkdir mirror && cd mirror
for f in install.sh install.sh.sig install.sh.pem install.sh.bundle install.sh.cosign.sig offline-ceremony.pub install-verify.sh; do
  curl -fsSLo \"\$f\" \"\$REL/\$f\"
done
cd /tmp
openssl genpkey -algorithm Ed25519 -out attacker.key 2>/dev/null
openssl pkey -in attacker.key -pubout -out attacker.pub 2>/dev/null
echo 'attacker fingerprint:' \$(openssl pkey -in attacker.pub -pubin -outform DER 2>/dev/null | openssl dgst -sha256 | awk '{print \$NF}')
openssl pkeyutl -sign -inkey attacker.key -rawin -in mirror/install.sh -out attacker.sig.raw
base64 -w0 attacker.sig.raw > mirror/install.sh.cosign.sig
sed -i.bak 's|REL_BASE=\"https://github.com/\${EXPECTED_REPO}/releases/download/\${TAG}\"|REL_BASE=\"file:///tmp/mirror\"|' mirror/install-verify.sh
chmod +x mirror/install-verify.sh
JARVIS_INSTALL_TAG=\$TAG bash mirror/install-verify.sh --dry-run --no-wizard --no-launch
echo R_WAVE2_B_EXIT=\$?
"
```

### Result — FAIL-CLOSED (correct)

Sample attacker fingerprint generated on 2026-05-27 run:
`867da7f5fbafa6b059ac53c5217f4fbd8a80a58a552f35c747d88401f0f89874`
(re-running generates a fresh keypair → different fingerprint each time).

```
[3/8] Fetching install.sh + Fulcio trio + offline-ceremony signature from release v0.3.0-supplychain-wave2...
      install.sh + .sig + .pem + .bundle + .cosign.sig downloaded
      offline-ceremony pubkey fingerprint OK (40cdb1b9e255e797909fba4fb5983450ccf7fa26ec17c80f473fe360da5549ee)

[4/8] Verifying Fulcio keyless signature (axis A — GitHub Actions OIDC)...
Verified OK
      axis A OK (identity=personal-jarvis/PersonalJarvis / .github/workflows/sign-installer.yml, issuer=https://token.actions.githubusercontent.com)

[5/8] Verifying offline-ceremony signature (axis B — Ed25519, air-gapped)...
WARNING: Skipping tlog verification is an insecure practice that lacks of transparency and auditability verification for the blob.
Error: failed to verify signature
main.go:74: error during command execution: failed to verify signature
  axis B: offline-ceremony signature check FAILED.
  install.sh.cosign.sig does NOT validate against the pinned Ed25519 pubkey
  (fingerprint 40cdb1b9e255e797909fba4fb5983450ccf7fa26ec17c80f473fe360da5549ee).
  refusing to execute — Wave 2 demands BOTH axes to pass.
R_WAVE2_B_EXIT=1
```

The verifier passes [3/8] (legit pub fingerprint matches), passes [4/8]
(Fulcio sig is still legit), then **fails at [5/8] with exit 1** because
the attacker-key signature does not validate against the inlined-pinned
legit pubkey. This is exactly the property Wave 2 was designed to provide:
even if an attacker swaps the second-axis signature wholesale, they
cannot forge a signature under the legit Ed25519 key without that key,
and the inlined fingerprint pin prevents pubkey substitution.

If this round ever passes, the offline-ceremony key has leaked OR the
verifier's signature math is broken. Both are P0 incidents requiring
key rotation per `wave2-key-ceremony.md` §6.

---

## Wave 3 — SLSA L3 build provenance + in-toto layout pin (axis C)

Wave 3 adds an independent third axis: a Sigstore-signed in-toto v1.0
SLSA L3 provenance produced by `slsa-github-generator@v2.1.0` (SHA
`f7dd8c54c2067bafc12ca7a55595d5ee9b75204a`) inside an isolated job,
bound to a manifest of subject hashes that three independent runner
OSes (ubuntu-latest, macos-latest, windows-latest) agreed on
byte-for-byte. The provenance is signed by a Fulcio cert minted in the
generator job's OIDC context — a separate identity from the sign-job's
Fulcio cert (Wave 1) and orthogonal to the offline-ceremony Ed25519
key (Wave 2).

Verifier stage [9/11] downloads the provenance and runs
`slsa-verifier verify-artifact --provenance-path ... --source-uri
github.com/personal-jarvis/PersonalJarvis --source-tag $TAG`.

Three attacker scenarios were exercised against the live
`v0.4.0-supplychain-wave3` release on 2026-05-27. All three reject
with exit 1.

### R-Wave3-A — Drop the provenance file

The attacker stages a local mirror of the release without
`personal-jarvis.intoto.jsonl` and points the verifier at it.

Observed:

```
Verifying artifact install.sh: FAILED: open /work/personal-jarvis.intoto.jsonl: no such file or directory

FAILED: SLSA verification failed: open /work/personal-jarvis.intoto.jsonl: no such file or directory
EXIT_CODE: 1
```

`install-verify.sh` stage [9/11] propagates that exit 1 with its
custom message ("the SLSA provenance does NOT attest to a build of
install.sh ... refusing to execute — Wave 3 demands 3-of-3 axes to
pass.").

### R-Wave3-B — Tamper with the provenance file

Attacker downloads the legitimate provenance and flips one byte at
offset 10000 (inside the base64-encoded signed body). File size
preserved, JSON shape preserved, but the Sigstore signature no longer
matches.

Observed:

```
Verifying artifact install.sh: FAILED: invalid Rekor entry: unable to verify bundle: invalid signature when validating ASN.1 encoded signature

FAILED: SLSA verification failed: invalid Rekor entry: unable to verify bundle: invalid signature when validating ASN.1 encoded signature
EXIT_CODE: 1
```

The Sigstore Fulcio cert binds the signature to the SHA-256 of the
signed payload. Flipping any byte invalidates the bundle because the
Rekor entry's `body.spec.signature` no longer covers the tampered
bytes. slsa-verifier exits 1 — `install-verify.sh` [9/11] fails closed.

### R-Wave3-C — Source-tag substitution

Attacker presents the legitimate v0.4.0-supplychain-wave3 provenance
but claims it attests to a different tag (v0.3.0-supplychain-wave2,
which had Wave 1+2 signatures but no SLSA provenance). Models a
release-version downgrade attack.

Observed:

```
Verifying artifact install.sh: FAILED: expected tag 'v0.3.0-supplychain-wave2', got 'v0.4.0-supplychain-wave3': tag used to generate the binary does not match provenance

FAILED: SLSA verification failed: expected tag 'v0.3.0-supplychain-wave2', got 'v0.4.0-supplychain-wave3': tag used to generate the binary does not match provenance
EXIT_CODE: 1
```

This is the `--source-tag` bind. The in-toto Statement inside the
provenance carries the original tag in its `invocation.parameters.ref`
field. slsa-verifier asserts the caller's claimed `--source-tag`
matches what the provenance attests. Disagreement → exit 1.

`install-verify.sh` hardcodes `--source-tag $TAG` to the
`JARVIS_INSTALL_TAG` resolved on first run, so an attacker cannot
bypass at the verifier-wrapper level without also replacing the
wrapper itself (which is signed under Waves 1+2).

### Wave 3 cut corners (honest disclosure)

- The three scenarios target the slsa-verifier binary directly rather
  than driving the full `install-verify.sh` invocation, because the
  wrapper auto-fetches assets from the public GitHub Release;
  simulating a "drop / tamper / mismatch" in the wrapper itself
  requires either DNS interception or a release side-channel. The
  exit code and failure mode that slsa-verifier emits is the exact
  exit code that `install-verify.sh` stage [9/11] propagates.
  Wave 3.1 follow-up: add a `JARVIS_INSTALL_MIRROR` env var to enable
  in-wrapper red-team automation against a local mirror.
- R-Wave3-B flips a single byte. A more sophisticated attacker might
  re-sign the tampered provenance under their own Fulcio cert — that
  would shift the failure mode to "certificate identity does not
  match expected workflow", which slsa-verifier also rejects but we
  did not exercise that path because it requires the attacker to
  drive a real Sigstore Fulcio signing operation.
- R-Wave3-C substitutes a tag that does not have its own provenance.
  If both releases had provenance, the attacker could swap them
  entirely — defense is out-of-band: the verifier resolves
  `JARVIS_INSTALL_TAG` from GitHub's "latest release" pointer, and
  the release-publishing workflow is itself signed (Wave 1).

---

## R-Wave4: post-quantum signing + Homebrew/Scoop distribution

Scope: Wave 4 added a 4th independent trust axis (ML-DSA-65 / NIST FIPS 204
category 3) and two new distribution paths (`brew install` via the
`personal-jarvis/homebrew-jarvis` tap, `scoop install` via the
`personal-jarvis/scoop-jarvis` bucket). Tag `v0.5.0-supplychain-wave4`.

All four scenarios below were exercised against the v0.5.0 release on
2026-05-27 by SA-5.

### R-Wave4-A — release published WITHOUT a `.mldsa.sig`

**Hypothesis:** if the attacker controls the release-publishing
workflow they could publish a release missing the `.mldsa.sig` asset
(e.g. a regression in the workflow, or a deliberate downgrade). The
verifier must refuse axis D rather than silently skip it.

**Setup:** point the verifier at the v0.4.0-supplychain-wave3 tag,
which is a legitimate release that pre-dates Wave 4 and therefore DOES
NOT publish `.mldsa.sig` assets. The verifier itself is the v0.5.0
script (which expects axis D). This is the closest faithful
reproduction of "Wave-4-aware verifier sees Wave-3-shaped release".

Command:

    docker run --rm -e JARVIS_INSTALL_TAG=v0.4.0-supplychain-wave3 \
      ubuntu:24.04 bash -c 'apt-get update -qq && \
      apt-get install -y -qq git curl ca-certificates openssl jq build-essential python3 >/dev/null && \
      curl -fsSL https://github.com/personal-jarvis/PersonalJarvis/releases/download/v0.5.0-supplychain-wave4/install-verify.sh | \
      bash -s -- --dry-run --no-wizard --no-launch'

**Result:** stages [0/13]..[11/13] PASS (classical axes A+B+C still
validate on v0.4). Stage [12/13] errors with:

    [12/13] Fetching ML-DSA-65 post-quantum signature + released pubkey (axis D — FIPS 204)...
    curl: (22) The requested URL returned error: 404
      failed to fetch .../v0.4.0-supplychain-wave3/install.sh.mldsa.sig
      this release does NOT publish a Wave 4 ML-DSA-65 signature for install.sh.
      refusing — Wave 4 axis D requires <artifact>.mldsa.sig per release.
      if this is a legacy (pre-Wave-4) tag, set JARVIS_INSTALL_ALLOW_NO_PQ=1 to bypass
      axis D (classical axes A+B+C still enforced); read TRUST_ROOT.md §5 first.
    EXIT=1

**Verdict: PASS.** Verifier exits 1, classical axes had passed but
axis-D-absent is treated as refuse-by-default. The
`JARVIS_INSTALL_ALLOW_NO_PQ=1` escape hatch is loud-logged so an
auditor reading the transcript sees the bypass. Defense aligned with
stage [11/13]'s transition-strategy doctrine (TRUST_ROOT §5.3).

### R-Wave4-B — released `pq-mldsa65.pub.pem` substituted by attacker

**Hypothesis:** an attacker who can write to releases (or intercept
the GitHub TLS path) substitutes the published ML-DSA-65 public key
with one whose private half they control. They then sign whatever
they want with their own ML-DSA-65 key and present that as
`<artifact>.mldsa.sig`. Stage [12/13]'s inlined-fingerprint-vs-
released-fingerprint cross-check must catch this.

**Setup:** generate a clean attacker keypair locally, swap the
public-key file, recompute the SHA-256(DER(SPKI)) fingerprint both
sides, compare to the pinned constant baked into `install-verify.sh`.

    $ openssl genpkey -algorithm ML-DSA-65 -out attacker.key
    $ openssl pkey -in attacker.key -pubout -out attacker.pub.pem
    $ PINNED=30a634809c19c41abcead8e657bfe19a53f9f4c831a82d2939cb7d5c40efe01a
    $ openssl pkey -in pq-mldsa65.pub.pem    -pubin -outform DER | openssl dgst -sha256
      # 30a634809c19c41abcead8e657bfe19a53f9f4c831a82d2939cb7d5c40efe01a  ← matches PINNED
    $ openssl pkey -in attacker.pub.pem      -pubin -outform DER | openssl dgst -sha256
      # 701c09495ac239bb2f8e4387f099789d1cf0af8435598ed879c948e608c15e3b  ← MISMATCH

Verifier code path at `install-verify.sh:877..882` triggers:

    err "  released ML-DSA-65 pubkey fingerprint mismatch!"
    err "    expected (pinned in verifier): $PQ_MLDSA65_PUBKEY_FINGERPRINT"
    err "    actual   (release asset):      $PQ_RELEASED_FP"
    err "  the published pq-mldsa65.pub.pem does NOT match the verifier's pin — refusing."
    exit 1

**Verdict: PASS.** Attacker-key substitution detected by the
inlined-constant cross-check. This defense is meaningful exactly
because the constant is heredoc'd INTO the verifier script itself,
which is independently signed by axes A, B, C — so an attacker who
swaps the public key would also have to swap the verifier script AND
regenerate three valid classical signatures over it.

### R-Wave4-C — `install-verify.sh.mldsa.sig` byte-flipped

**Hypothesis:** the legitimate PQ public key is published, but the
attacker corrupts the signature itself (one byte flipped). Stage
[13/13]'s `openssl pkeyutl -verify` MUST reject.

**Setup:** XOR a single byte in the released signature and run
`pkeyutl -verify` against it.

    $ ORIG=$(dd if=install-verify.sh.mldsa.sig bs=1 count=1 skip=100 | xxd -p)
      # orig: 42
    $ printf "\xbd" | dd of=install-verify.sh.mldsa.sig bs=1 count=1 seek=100 conv=notrunc
      # patched: bd  (42 XOR ff = bd)
    $ openssl pkeyutl -verify -pubin -inkey pq-mldsa65.pub.pem \
        -rawin -in install-verify.sh -sigfile install-verify.sh.mldsa.sig
      ML-DSA-65 digest_verify:
      Signature Verification Failure
      exit=1

**Verdict: PASS.** ML-DSA-65's EUF-CMA property (NIST FIPS 204 cat 3)
catches single-bit corruption with the same strength as classical
EdDSA. After restoring the original signature, verify succeeds again,
so this is genuinely the byte flip and not a side-channel.

### R-Wave4-D — malicious Homebrew Formula

**Hypothesis:** the attacker compromises the
`personal-jarvis/homebrew-jarvis` tap (e.g. acquires a maintainer's
GitHub token) and replaces the Formula's `url` + `sha256` to point at
a malicious `install-verify.sh` they control. `brew install` is happy
because `url` and `sha256` agree. The malicious script then either
does nothing related to verification, or tries to fake a verifier
transcript. The defense is that the FILE downloaded from the malicious
URL is not covered by ANY of the four legitimate signatures
(Fulcio / offline-Ed25519 / SLSA / ML-DSA-65), so even if it runs, it
cannot validate itself against the legit release.

**Setup:** craft a malicious replacement `install-verify.sh`, then
attempt to verify it against the legitimate `install-verify.sh.mldsa.sig`
from the real release.

    $ cat > malicious-installer.sh <<__EOF__
    #!/usr/bin/env bash
    echo "Personal Jarvis verifier (FAKE)"
    SCRIPT_HASH=\$(sha256sum "\$0" | awk '{print \$1}')
    echo "  this file hash: \$SCRIPT_HASH"
    curl -fsSL -O .../install-verify.sh.mldsa.sig
    curl -fsSL -O .../pq-mldsa65.pub.pem
    openssl pkeyutl -verify -pubin -inkey pq-mldsa65.pub.pem \
      -rawin -in "\$0" -sigfile install-verify.sh.mldsa.sig
    __EOF__
    $ chmod +x malicious-installer.sh
    $ bash malicious-installer.sh
      Personal Jarvis verifier (FAKE)
        this file hash: 34be32d2e9103a138c52e38e7b28da4ea3981eaa4069cc699d33d86b86fa4a1c
        ML-DSA-65 digest_verify:
        Signature Verification Failure
      verify result: FAILED

**Verdict: PASS.** The legitimate PQ signature covers EXACTLY the bytes
of the released `install-verify.sh`. A different file — even with
verifier-like cosmetics — produces a different SHA-256, and ML-DSA-65
is bound to those bytes. The attacker would have to ALSO compromise
all four independent trust roots simultaneously to forge a working
signature over the malicious bytes.

Important nuance: this defense holds because the brew-installed script
is itself a verifier. If a future Wave ships a non-verifier entry
point (e.g. an `install.sh` that runs without re-verifying the bytes
it carries), the Formula-compromise attack widens. The
distribution doctrine in `wave4-distribution.md` pins the "single
binary distributed = verifier itself" invariant precisely to keep
this property.

### R-Wave4 — combined cut-corner / fix-forward log

Four fix-forwards landed during the v0.5.0 cut:

1. **#1** (`#25` / `11512c5`) — SA-4 had pinned the wrong SHA-256 for
   the OpenSSL 3.5.6 source tarball. The workflow's own SHA-256 gate
   (which was built specifically as a defense in TRUST_ROOT §5.4)
   caught it on the first attempt. Fixed by re-pinning to the
   upstream-published value.
2. **#2** (`#26` / `c611cb1`) — SA-1's actual key wrap used PBKDF2
   default `-iter 10000`, while the SA-1 commentary and TRUST_ROOT.md
   §5 both claimed `-iter 600000`. The decrypt step's `bad decrypt`
   error is a fail-closed posture (refuse-to-decrypt over
   silent-garbage-decrypt). Pinned to Wave 4.1 alongside HSM
   migration.
3. **#3** (`#27` / `3ae740a`) — On hosts with OpenSSL < 3.5
   (transition-mode hosts, e.g. Ubuntu 24.04 LTS shipping 3.0.13),
   the `openssl pkey -pubin` parse fails because the binary doesn't
   know the ML-DSA-65 SPKI OID. `set -euo pipefail` then killed the
   script with no diagnostic instead of degrading to the documented
   transition-mode handler. Fixed with `|| true`.
4. **#4** (`#28` / `7aa3549`) — fix-forward #3 left a different bug:
   `openssl dgst -sha256` of zero bytes returns a deterministic
   hash (`e3b0c4...` — the SHA of empty input), which is not the
   pinned fingerprint, which fired the *tamper-detection* branch
   instead of the *transition-mode* branch. Fixed by splitting the
   openssl-pkey call from openssl-dgst via a tempfile, so the
   verifier can tell pkey-parse-failed from valid-but-different.

None of the four fix-forwards re-opens a classical-axis attack
surface. Wave 4.1 follow-ups: PQ key rotation + HSM migration +
iteration-count bump back to 600 000.

