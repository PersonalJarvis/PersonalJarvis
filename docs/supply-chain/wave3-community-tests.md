# Wave 3 community-baseline test results

This document captures the results of running ecosystem-standard
verifiers and supply-chain audit tools against the
`v0.4.0-supplychain-wave3` release. Goal: measure independent
interoperability with the broader Sigstore + SLSA + OpenSSF ecosystem,
not declare conformance. Findings are recorded as observed, including
gaps.

**Run date:** 2026-05-27
**Tag exercised:** `v0.4.0-supplychain-wave3`
**Commit:** `73bb7b50fb56ec5354fdb48a53cccb5a9018791e`
**Repo:** `github.com/PersonalJarvis/PersonalJarvis`
**Operator:** SA-5 integrator, Wave 3 integration

---

## Test 1 — slsa-verifier (SLSA reference verifier)

### What it tests

`slsa-verifier` is the official reference implementation maintained by
the SLSA project. It checks an in-toto v1.0 provenance bundle against
the four SLSA L3 properties: builder identity, source-uri binding,
source-tag binding, and subject-hash binding. This is the "ground
truth" for the SLSA layer of Wave 3.

### Command

```bash
docker run --rm ubuntu:24.04 bash -c '
  set -e
  apt-get update -qq && apt-get install -y -qq curl
  curl -fsSL https://github.com/slsa-framework/slsa-verifier/releases/download/v2.7.0/slsa-verifier-linux-amd64 \
       -o /usr/local/bin/slsa-verifier
  chmod +x /usr/local/bin/slsa-verifier
  TAG=v0.4.0-supplychain-wave3
  REL=https://github.com/PersonalJarvis/PersonalJarvis/releases/download/$TAG
  mkdir /tmp/v && cd /tmp/v
  for a in install.sh install.ps1 installer.py install-verify.sh install-verify.ps1 \
           personal-jarvis.intoto.jsonl ; do
    curl -fsSLo "$a" "$REL/$a"
  done
  slsa-verifier verify-artifact \
    --provenance-path ./personal-jarvis.intoto.jsonl \
    --source-uri github.com/PersonalJarvis/PersonalJarvis \
    --source-tag v0.4.0-supplychain-wave3 \
    install.sh install.ps1 installer.py install-verify.sh install-verify.ps1
'
```

### Result — PASS

```
Verified build using builder "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@refs/tags/v2.1.0" at commit 73bb7b50fb56ec5354fdb48a53cccb5a9018791e
Verifying artifact install.sh: PASSED

Verified build using builder "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@refs/tags/v2.1.0" at commit 73bb7b50fb56ec5354fdb48a53cccb5a9018791e
Verifying artifact install.ps1: PASSED

[... installer.py, install-verify.sh, install-verify.ps1 also PASSED ...]

PASSED: SLSA verification passed
EXIT: 0
```

All five artifacts verify under the reference SLSA verifier at the
slsa-github-generator v2.1.0 builder identity, at the
`v0.4.0-supplychain-wave3` source-tag. The provenance attests to the
same artifact hashes that the cross-runner matrix agreed on
byte-for-byte (proven by sign-installer.yml's local-recompute-asserts-
cross-runner gate before the SLSA generator subjects are submitted).

---

## Test 2 — cosign verify-blob-attestation (cross-tool)

### What it tests

`cosign` is the Sigstore reference client. Independent verification of
the SLSA in-toto bundle via cosign (rather than slsa-verifier) would
be a meaningful cross-tool interoperability signal — different binary,
different parser, same trust chain. We try `cosign verify-blob` with
`--bundle pointing at personal-jarvis.intoto.jsonl`.

### Command

```bash
cosign verify-blob \
  --bundle /work/personal-jarvis.intoto.jsonl \
  --certificate-identity-regexp '^https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@refs/tags/v2\.1\.0$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  /work/install.sh
```

### Result — INTEROP GAP (honest disclosure)

```
Error: bundle does not contain cert for verification, please provide public key
main.go:74: error during command execution: bundle does not contain cert for verification, please provide public key
EXIT: 1
```

The slsa-github-generator emits the new Sigstore protobuf bundle
format `application/vnd.dev.sigstore.bundle.v0.3+json` (per first 200
bytes of the file: `{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json", ...}`).
`cosign verify-blob --bundle` in v2.4.1 expects the older
cosign-specific bundle JSON wrapper and does not parse the protobuf
bundle format. `cosign verify-attestation` requires an OCI image
reference (subject) and so cannot consume a file-backed subject either.

This is the documented Sigstore-CLI / Sigstore-bundle-format
generational gap. The format IS readable by `slsa-verifier` (this
document, Test 1, PASS) and by `sigstore-python` (`sigstore` PyPI
package, observed in Wave 2 community tests). Wave 3.1 follow-up:
re-run this cross-tool check once cosign-cli ships full
`bundle.v0.3` support; until then, slsa-verifier is the only
ecosystem CLI that natively verifies our SLSA provenance, which the
threat model accepts (it is the SLSA reference verifier).

---

## Test 3 — OpenSSF Scorecard Signed-Releases check

### What it tests

`scorecard` is the OpenSSF auditing tool that scores a repo's
supply-chain hygiene across 18 checks. The "Signed-Releases" check
scans the last 5 releases for signature + provenance artifacts. Score
moves to 10/10 when 3+ recent releases each have both signatures AND
provenance. Wave 2 had signatures only; Wave 3 adds provenance.

### Command

```bash
docker run --rm -e GITHUB_AUTH_TOKEN="$GITHUB_TOKEN" gcr.io/openssf/scorecard:stable \
  --repo=github.com/PersonalJarvis/PersonalJarvis \
  --checks=Signed-Releases \
  --show-details
```

### Result — 8/10 (Wave 2 was 6/10)

```
Aggregate score: 8.0 / 10

| SCORE  |      NAME       |             REASON             |
| 8 / 10 | Signed-Releases | 3 out of the last 3 releases   |
|        |                 | have a total of 4 signed       |
|        |                 | artifacts.                     |
| ...
Info: signed release artifact: install-verify.ps1.cosign.sig:
   https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v0.4.0-supplychain-wave3
Info: signed release artifact: install-verify.ps1.cosign.sig:
   https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v0.3.0-supplychain-wave2
Info: signed release artifact: install-verify.ps1.sig:
   https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v0.2.0-supplychain-wave1
Info: provenance for release artifact: personal-jarvis.intoto.jsonl:
   https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v0.4.0-supplychain-wave3
Warn: release artifact v0.3.0-supplychain-wave2 does not have provenance:
   https://api.github.com/repos/PersonalJarvis/PersonalJarvis/releases/329969659
Warn: release artifact v0.2.0-supplychain-wave1 does not have provenance:
   https://api.github.com/repos/PersonalJarvis/PersonalJarvis/releases/329675930
```

### Interpretation

- All three recent releases have signed artifacts (cosign signatures).
- Only the Wave 3 release has SLSA provenance — the prior two
  releases (Wave 1 and Wave 2) were cut before the SLSA pipeline
  existed.
- The 2-point deduction (10 → 8) is precisely the missing-provenance
  warnings on the two legacy releases.
- Wave 3.1 prediction: as soon as 2 more provenance-bearing tags
  land in the rolling 5-release window, the score will move to
  10/10 with the Wave 3 path. No code change required; just keep
  cutting releases through the v0.4.x+ pipeline.

### Vs Wave 2 baseline

Wave 2 (recorded in `wave2-community-tests.md`) scored 6/10 on
Signed-Releases. **Wave 3 raised it to 8/10.** The remaining 2 points
are mechanical (release-history rollover), not a structural gap.

---

## Test 4 — install-verify.sh end-to-end Docker smoke

### What it tests

Running the on-release verifier wrapper end-to-end from a fresh
`ubuntu:24.04` container with no Personal Jarvis state. This is the
real consumer path: a user pastes the one-liner, the wrapper
self-bootstraps cosign + slsa-verifier, runs all 12 stages, and only
hands off to install.sh if all 3 axes pass.

### Command

```bash
docker run --rm ubuntu:24.04 bash -c '
  apt-get update -qq && apt-get install -y -qq \
    git curl ca-certificates openssl jq python3 >/dev/null
  curl -fsSL https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v0.4.0-supplychain-wave3/install-verify.sh \
    -o /tmp/iv.sh
  bash /tmp/iv.sh --dry-run --no-wizard --no-launch
'
```

### Result — 12-stage PASS

```
[0/11] Resolving release tag...
      Tag pinned: v0.4.0-supplychain-wave3
[1/11] Detecting platform...
      Platform: Linux/x86_64 -> cosign=cosign-linux-amd64, slsa-verifier=slsa-verifier-linux-amd64
[2/11] Bootstrapping cosign v2.4.1 (SHA-256 pinned)...
      cosign SHA-256 OK (8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b)
[3/11] Fetching install.sh + Fulcio trio + offline-ceremony signature from release v0.4.0-supplychain-wave3...
      install.sh + .sig + .pem + .bundle + .cosign.sig downloaded
      offline-ceremony pubkey fingerprint OK (1e8f2fa590e6454daff34e88e7bde8ffcf04b1eb235f0ca11ff9ebc65e2d1d3a)
[4/11] Verifying Fulcio keyless signature (axis A - GitHub Actions OIDC)...
Verified OK
      axis A OK (identity=PersonalJarvis/PersonalJarvis / .github/workflows/sign-installer.yml, issuer=https://token.actions.githubusercontent.com)
[5/11] Verifying offline-ceremony signature (axis B - Ed25519, air-gapped)...
Verified OK
      axis B OK (Ed25519, key fingerprint=1e8f2fa590e6454daff34e88e7bde8ffcf04b1eb235f0ca11ff9ebc65e2d1d3a)
[6/11] Checking Rekor inclusion proof freshness (<= 86400s)...
      Rekor inclusion proof age: 145s (limit 86400s)
[7/11] Cross-checking identity assertions on both axes...
      axis A SAN matches pinned regex
      axis B key fingerprint stable
[8/11] Bootstrapping slsa-verifier v2.7.0 (SHA-256 pinned)...
      slsa-verifier SHA-256 OK (499befb675efcca9001afe6e5156891b91e71f9c07ab120a8943979f85cc82e6)
[9/11] Verifying SLSA L3 build provenance (axis C - independent attestation of build environment)...
      SLSA provenance downloaded (personal-jarvis.intoto.jsonl)
Verified build using builder "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@refs/tags/v2.1.0" at commit 73bb7b50fb56ec5354fdb48a53cccb5a9018791e
Verifying artifact /tmp/jarvis-install-verify.U5WMKt71/install.sh: PASSED
PASSED: SLSA verification passed
      axis C OK (SLSA L3: source=github.com/PersonalJarvis/PersonalJarvis, tag=v0.4.0-supplychain-wave3)
[10/11] Verifying in-toto layout functionary pin (axis C - supply-chain layout match)...
      axis C OK (in-toto layout: OK keyid=github-actions-sign-installer-yml-tag-push)
      identity_regexp pin: ^https://github\.com/PersonalJarvis/PersonalJarvis/\.github/workflows/sign-installer\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._-]+)?$
[11/11] All checks passed (3-of-3 axes). Handing off to install.sh...
```

All 12 stages traverse in order. The handoff to install.sh proceeds
(in the smoke test the install.sh exits later because the Ubuntu
container has no `python3-venv` package — unrelated to verification).

---

## Summary

| Test | Wave 3 result | Wave 2 result |
|---|---|---|
| slsa-verifier verify-artifact | PASS (5/5 artifacts) | N/A (Wave 2 had no provenance) |
| cosign verify-blob-attestation cross-tool | INTEROP GAP (CLI does not parse bundle.v0.3) | N/A |
| OpenSSF Scorecard Signed-Releases | 8/10 | 6/10 |
| install-verify.sh 12-stage smoke | PASS (all 12 stages) | PASS (8 stages in Wave 2) |
