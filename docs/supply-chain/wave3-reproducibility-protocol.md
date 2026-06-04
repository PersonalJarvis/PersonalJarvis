# Wave 3 — Reproducibility Protocol

> **WAVE-5 ADDENDUM (2026-05-27):** the document below refers to
> `install/in-toto/layout.template.json`. That file was RENAMED to
> `install/in-toto/layout-content-anchor.json` in Wave 5 (audit
> Finding 3) — the layout is in-toto-shaped but UNSIGNED, so calling
> it "in-toto layout" overclaimed compliance with the in-toto v1.0
> spec. The body text below is preserved verbatim for historical
> integrity; the actual on-disk filename + `_type` field follow the
> Wave-5 rename. See `docs/supply-chain/wave5-audit-fixes-validation.md`
> §2 Finding 3 and `install/TRUST_ROOT.md` §10.4.
>
> Status: **Foundation step.** This document defines the bar. The
> scaffolding (`slsa-provenance.yml.tmpl`, `cross-runner-hash.yml.tmpl`,
> `install/in-toto/layout.template.json`) is checked in and ready for
> follow-up sub-agents SA-2 / SA-3 / SA-4 / SA-5 to wire into
> `.github/workflows/sign-installer.yml`. No production workflow runs
> Wave 3 logic yet.
>
> Last reviewed: 2026-05-27 against `personal-jarvis/PersonalJarvis`
> `main` HEAD `8d68b31` (Wave 2 baseline). Companion documents:
> `docs/supply-chain/threat-model.md` §8, `install/in-toto/layout.template.json`,
> `.github/workflows/cross-runner-hash.yml.tmpl`,
> `.github/workflows/slsa-provenance.yml.tmpl`.

---

## 1. Why Wave 3 exists

Wave 1 (Sigstore keyless) and Wave 2 (offline-ceremony Ed25519) both
sign whatever bytes the build environment produces. **Neither verifies
that those bytes are the bytes the source commit said to produce.**

That is the SolarWinds-class gap. A March 2025 incident on
`tj-actions/changed-files` (CVE-2025-30066) demonstrated the gap is not
theoretical: an attacker who controls one GitHub Actions runtime can
produce malicious binaries that nonetheless carry legitimate Sigstore
signatures, because the OIDC token Fulcio binds to is *the workflow's
token*, not *the workflow code that the maintainer wrote*. The
verifier-side cosign check succeeds. The user installs malware.

Wave 3 closes that gap two ways:

1. **Cross-runner hash agreement.** The same source commit, built on
   three independent runner OS images (`ubuntu-latest`,
   `macos-latest`, `windows-latest`), MUST produce byte-identical
   SHA-256 for each of the five install/* artifacts. If any pair
   disagrees, the workflow fails before any signing happens. An
   attacker who controls ONE runner (or the upstream image for ONE
   OS) cannot land malicious bytes through the pipeline.
2. **SLSA L3 provenance + in-toto attestation.** The slsa-github-
   generator reusable workflow (pinned by 40-char SHA, not by tag —
   see §6) emits a signed provenance document binding the produced
   artifacts to the exact builder, commit SHA, and workflow file
   path. An external auditor can replay the build from the source
   commit, hash the result, and compare to the provenance subject —
   without trusting any party in the GitHub release pipeline.

The cross-runner check answers *"do three independent build platforms
agree on what the source commit produces?"* The SLSA provenance answers
*"can a third party prove the bytes match the commit, by themselves,
without our cooperation?"*

---

## 2. What "reproducible" means for THIS repo (don't fake the bar)

The five artifacts under signature are plain-text files committed in
the repository's working tree:

```
install/install.sh         (133 lines, POSIX shell, LF-only)
install/install.ps1        (154 lines, PowerShell, LF-only)
install/installer.py       (268 lines, Python, LF-only)
install/install-verify.sh  (448 lines, POSIX shell, LF-only)
install/install-verify.ps1 (366 lines, PowerShell, LF-only)
```

**Content reproducibility is free.** `actions/checkout` materializes
the same blob from the same commit SHA on every runner. The
non-trivial reproducibility property for this repo is:

> The **SHA-256 of each file as it lands on the runner filesystem
> after checkout** must be byte-identical across all three runner OS
> images. Any divergence is a Wave-3 failure and is treated as a
> supply-chain integrity event.

The two known failure modes are line-endings and BOM handling:

- **Line endings.** Git's `core.autocrlf=true` is the default on
  Windows runners. Without an explicit `.gitattributes` policy, `.sh`
  and `.py` files would be checked out as CRLF on `windows-latest`,
  silently changing every byte that contains a newline. Wave 3
  ships `.gitattributes` pinning all five install/* artifacts and
  the in-toto layout / supply-chain docs / workflow files to
  `text eol=lf`.
- **UTF-8 BOM.** No artifact contains a BOM today; CI lints would
  catch one if it ever crept in. The `cross-runner-hash.yml.tmpl`
  workflow's hash assertion catches both classes of regression
  pre-signing.

**What we explicitly do NOT claim:**

- We do not claim binary-reproducibility of any Python wheel the
  installer ultimately fetches from PyPI. Wave 3 is about the install
  scripts, not about `rich`, `packaging`, `jarvis`, or any transitive
  dependency. The dependency tree's hash pinning is Wave 2 SA-4's
  scope (`requirements.lock` + `pip-audit`).
- We do not claim runtime-reproducibility (same script, same flags,
  same Python interpreter version → identical filesystem effect).
  That is a much harder property and not part of the SLSA L3 bar.
- We do not claim the GitHub Actions runner images themselves are
  reproducible. They are not (microsoft/runner-images publishes new
  images monthly with kernel/glibc/libc++ drift). What we DO assert
  is that the *output bytes* match across them — which is the
  tractable property for plain-text artifacts.

---

## 3. The cross-runner check, in detail

Three jobs run in matrix on `[ubuntu-latest, macos-latest,
windows-latest]`. Each:

1. Checks out the tag at the same 40-char commit SHA.
2. Computes SHA-256 of each of the five `install/*` files using the
   OS-native tool (`sha256sum` on Linux, `shasum -a 256` on macOS,
   `Get-FileHash -Algorithm SHA256` on Windows). The hashes are
   normalized to lowercase hex, no leading whitespace, sorted by
   filename.
3. Uploads the manifest as a workflow artifact.

A fourth job downloads all three manifests, asserts every line
matches across all three, and either:

- exits 0 if the three sets are identical (the green-light condition
  for the downstream signing job), or
- exits 1 and emits an annotated diff so the maintainer can see which
  artifact disagreed on which OS.

**No exceptions for line-ending differences.** That is the integrity
bar. If Windows produces a different SHA-256 for `install.sh`, the
fix is either (a) `.gitattributes` pinning the file to LF
unconditionally — already done in this commit — or (b) document the
divergence as a Wave 3 limitation in this file. Papering over the
difference with platform-specific normalization in the hash step is
forbidden: the whole point of the cross-runner check is that no
runner can normalize away an attacker-inserted byte difference.

The downstream signing job depends on the cross-runner job succeeding.
If it fails, no signature is produced. There is no `continue-on-error`
escape hatch. SA-2's integration commit adds that dependency edge.

---

## 4. Hermeticity boundaries (disclose honestly)

A truly hermetic build runs offline against a fully pinned dependency
graph. Wave 3 is **not** fully hermetic. Honest disclosure of what
is pinned and what is not:

### 4.1 Pinned

| Input | Pin mechanism | Verification |
|---|---|---|
| Source commit | `actions/checkout` is invoked with the tag's 40-char SHA via `${{ github.sha }}` | `git rev-parse HEAD` in each runner step, asserted equal across matrix |
| Third-party Action versions | All Actions in `.github/workflows/*.yml` pinned by 40-char commit SHA (CVE-2025-30066 lesson). Dependabot tracks SHA bumps. | grep audit in CI: any `uses:` line not matching `@[0-9a-f]{40}` fails lint |
| cosign binary version | Pinned to `cosign-release: v2.4.1` in `sigstore/cosign-installer`; the installer Action itself verifies cosign's Sigstore signature | `cosign version` plain-text grep in workflow (existing Wave 1 logic) |
| GitHub Actions runner OS image label | `runs-on: ubuntu-latest` resolves to a *specific* image SHA per the [runner-images registry](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md). The label is mutable; the image SHA at any moment is not. | The image SHA is recorded in the workflow run log (`Runner Image: <name>, <sha>`). The `cross-runner-hash.yml.tmpl` job captures it as a build-id artifact. |
| Build tool versions | `sha256sum`, `shasum -a 256`, `Get-FileHash` are runner-supplied. Versions are recorded but not pinned. The hash function (SHA-256) is the same; the *implementation* is not. We assert empirically that all three produce the same hex digest for the same byte stream — that assertion is the cross-runner check itself. | If a future runner-image swap broke SHA-256 output equivalence (extraordinarily unlikely; the algorithm is RFC 6234), the cross-runner check would fire. |
| in-toto layout authority | The layout's `keys` map pins the Fulcio identity by issuer URL + certificate-subject regex. Only the `sign-installer.yml` workflow at the tagged commit can produce a step-signature that the verifier accepts. | `cosign verify-attestation --certificate-identity-regexp` in the verifier (SA-3 wires in). |
| slsa-github-generator | Pinned by 40-char commit SHA `f7dd8c54c2067bafc12ca7a55595d5ee9b75204a` (= v2.1.0 release commit, 2025-02-24). Tag-pin is forbidden per CVE-2025-30066. | `git ls-tree` audit in the signing workflow's preflight step. |

### 4.2 Not pinned (disclose, do not paper over)

| Unpinned input | Why not | Containment |
|---|---|---|
| Runner OS system libraries (libc, libssl, libcurl, kernel) | The runner images are updated monthly by GitHub. We do not run a private rebuilder farm. | The cross-runner check fails closed if any of these affect output bytes. Plain-text artifacts are not sensitive to any of them. |
| Runner image kernel revision | Same as above. | Same as above. |
| Network DNS resolution | `actions/checkout` resolves `github.com` via the runner's resolver. A poisoned DNS would deliver a modified tree. | `actions/checkout` validates the commit SHA against the Git object hash, which is collision-resistant; a poisoned tree would produce a different SHA and fail the workflow before the matrix even runs. |
| GitHub Actions OIDC token issuer | `https://token.actions.githubusercontent.com` is GitHub-controlled infrastructure. | Sigstore Fulcio additionally validates the OIDC token's audience / claim set; a forged token would not produce a valid Fulcio cert. |
| Sigstore Fulcio / Rekor root keys | Operated by the Sigstore steering committee. Wave 4 may swap in a private rekor mirror. | The verifier (Wave 1) hardcodes the OIDC issuer and identity regex — if Fulcio is compromised, the user still requires a Wave-2 offline-ceremony signature to validate, per `install-verify.sh` two-axis logic. |
| SOURCE_DATE_EPOCH | Not set. The five artifacts contain no timestamps — they're hand-edited shell/Python/PowerShell files. If a future artifact embeds a build timestamp, SOURCE_DATE_EPOCH must be set to the tag's commit time in the matrix workflow. | The cross-runner check catches any timestamp leakage as a non-matching hash. |

### 4.3 What hermeticity would buy that Wave 3 doesn't yet

A fully hermetic build (e.g. Bazel + remote cache + a NixOS-pinned
toolchain) would make Wave 3 stronger but is **not** part of the
plain-text-artifact scope. The relevant pointer for when Wave 3
expands to compiled artifacts (post-Wave-4) is the
[reproducible-builds.org documentation](https://reproducible-builds.org/docs/)
on `SOURCE_DATE_EPOCH`, deterministic locale/timezone, sorted-input,
and parallelism-determinism. None of those apply to the five
plain-text install scripts today.

---

## 5. SLSA L3 mapping (per spec v1.0; v1.2 is current, see §5.4)

Reference: https://slsa.dev/spec/v1.0/levels (status: Retired, but
v1.2 has not materially changed the Build-track L3 bar). The L3
requirements decompose into two explicit named items:

### 5.1 Build platform isolation (L3, BUILD-level)

> "Each build runs in an isolated environment, free of influence from
> other builds, including those of other tenants of the platform."

**Status under Wave 3 foundation: MET (inherited from GitHub-hosted
runner architecture).** GitHub-hosted runners are ephemeral VMs that
are destroyed after each job. The `cross-runner-hash.yml.tmpl`
matrix uses `runs-on: ubuntu-latest`, `macos-latest`,
`windows-latest`, all GitHub-hosted. Self-hosted runners are
forbidden by policy (see `threat-model.md` §3 Scenario E). The
`slsa-github-generator` reusable workflow itself is GitHub-hosted.

**Evidence:** workflow files; absence of `runs-on: self-hosted` lines
in `.github/workflows/*.yml`.

### 5.2 Secret protection (L3, BUILD-level)

> "Secret material used to sign the provenance cannot be exfiltrated
> by user-controlled build steps."

**Status under Wave 3 foundation: MET (provided by `slsa-github-
generator`).** The reusable workflow at `f7dd8c54...` performs the
Sigstore signing in a *separate, isolated job* (`generator_generic_slsa3`)
that the user's workflow steps cannot access. The job mints the OIDC
token in its own runner, never exposes the token to a step the
calling workflow controls, and uploads the signed provenance as a
workflow output. This is the specific architectural property that
distinguishes L3 from L2.

**Evidence:** the reusable workflow source at the pinned SHA.

### 5.3 Lower-tier requirements (L1/L2) we also meet

- **Provenance authenticated** (L2): Sigstore Fulcio cert binds the
  provenance to a GitHub OIDC identity. Wave 1 already meets this.
- **Provenance unforgeable** (L2): same. Plus the cross-runner check
  adds three-OS-cross-attestation, which exceeds L2's bar.
- **Provenance available** (L1): published as a GitHub Release asset
  alongside the signed artifacts (SA-3 wires in).
- **Build platform hosted** (L2): GitHub-hosted runners.

### 5.4 Where SLSA L3 stops and we don't yet go further

SLSA L3 does **not** require:

- Reproducible builds (this is a property we add voluntarily because
  plain-text artifacts make it free, but L3 does not demand it).
- Two-person review on every commit (this would be L3-Source, a
  separate track; we are addressing the Build track here).
- Hermetic builds (L4 territory under earlier SLSA versions; L4 is
  deprecated in v1.0+ in favor of the Source track).

Wave 3 deliberately stops at L3-Build. Going to L3-Source (two-person
review on every commit touching install/* and .github/workflows/*)
is a maintainer-process change, not a code change, and is queued for
Wave 4 alongside the post-quantum migration. The repo currently has
a single maintainer; L3-Source requires a second.

### 5.5 SLSA v1.2 deltas (not material to this commit)

SLSA v1.0 was retired; v1.2 is current. The Build L3 bar is
unchanged. v1.2 adds a *VSA (Verification Summary Attestation)*
optional layer (an L3 verifier can emit a VSA stating "I verified
the L3 provenance and got this result"). Wave 4 will ship VSA
emission. No code change needed in Wave 3 foundation.

---

## 6. Why the 40-char SHA pin matters (and the SHA we used)

The slsa-github-generator reusable workflow is referenced via:

```yaml
uses: slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@f7dd8c54c2067bafc12ca7a55595d5ee9b75204a
```

That SHA is the `v2.1.0` release commit (2025-02-24, "update the ref
in the pre-submit", signed-off by Ramon Petgrave). Tag-pin
(`@v2.1.0`) would also resolve to that commit *today* — but tags are
mutable. The `tj-actions/changed-files` CVE-2025-30066 incident in
March 2025 demonstrated that an attacker who compromises the
upstream repo can move a tag to point at a different commit, and
every downstream workflow consuming the tag pulls the new commit on
its next run. Pinning by 40-char SHA makes that attack class
ineffective: the tag can move all it wants; our workflow keeps
pulling the SHA we audited.

The verification chain a maintainer would run before bumping the
pin:

```bash
# 1. Resolve the new tag to a SHA.
gh api repos/slsa-framework/slsa-github-generator/git/ref/tags/<new-tag> \
   --jq '.object.sha'

# 2. Inspect the diff between the current pinned SHA and the new one.
gh api repos/slsa-framework/slsa-github-generator/compare/<current-sha>...<new-sha>

# 3. Confirm the diff matches the release notes; review every change
#    to .github/workflows/generator_generic_slsa3.yml and to
#    internal/builders/generic/*.

# 4. Update .github/workflows/slsa-provenance.yml.tmpl with the new SHA.
```

That four-step process is the contract for SA-2 (the workflow
integrator) when slsa-github-generator next releases.

---

## 7. Verifier contract (SA-3 scope, documented here for foreknowledge)

The post-Wave-3 verifier (`install-verify.sh` / `install-verify.ps1`,
modified by SA-3) requires three signature axes — Wave 1 Fulcio,
Wave 2 offline-ceremony, **and Wave 3 SLSA L3 provenance + in-toto
layout**:

```
USER  →  TLS  →  release URL  →  fetch artifact, .sig, .pem, .bundle,
                                    .cosign.sig, .intoto.jsonl,
                                    in-toto/layout.template.json
                                 ↓
                         (1) cosign verify-blob (Wave 1, Fulcio + Rekor)
                                 ↓
                         (2) cosign verify-blob (Wave 2, offline pubkey)
                                 ↓
                         (3) slsa-verifier verify-artifact
                                 --provenance-path <artifact>.intoto.jsonl
                                 --source-uri github.com/personal-jarvis/PersonalJarvis
                                 --source-tag <release-tag>
                                 ↓
                         (4) in-toto layout verification
                                 (functionary = the workflow's Fulcio cert,
                                  step = "build", products = the five
                                  install/* files; verifier consults the
                                  signed layout shipped with the release)
                                 ↓
                                RUN
```

If any axis fails, no payload is executed. The verifier prints which
axis failed and why. The current Wave 1+2 verifier (`install-verify.sh`)
is the integration point; SA-3 adds axes (3) and (4).

---

## 8. Self-check the auditor can run

Every claim in this document is verifiable from the repo state alone:

```bash
# (a) The pinned SHA is what we said it is and resolves to the release commit.
gh api repos/slsa-framework/slsa-github-generator/git/ref/tags/v2.1.0 \
   --jq '.object.sha' \
   | grep -qx 'f7dd8c54c2067bafc12ca7a55595d5ee9b75204a' \
   && echo "PIN VERIFIED"

# (b) Every Action in the workflows is SHA-pinned (no tag pins).
grep -rEn 'uses: [^@]+@[^[:space:]]+' .github/workflows/ \
   | grep -vE '@[0-9a-f]{40}([[:space:]]|$)' \
   && echo "FAIL: tag pin found" \
   || echo "ALL ACTIONS SHA-PINNED"

# (c) The in-toto layout is valid JSON and pins a functionary identity.
python -c "import json; d = json.load(open('install/in-toto/layout.template.json')); \
   assert d['_type'] == 'layout' and d['steps'] and d['steps'][0]['pubkeys']"

# (d) The cross-runner matrix lists all three OS images.
grep -E '\bubuntu-latest\b|\bmacos-latest\b|\bwindows-latest\b' \
   .github/workflows/cross-runner-hash.yml.tmpl \
   | wc -l \
   | grep -qx 3 \
   && echo "MATRIX OK"
```

If any of these fail, the foundation step itself is broken; do not
proceed with integration until they pass.

---

## 9. What this document does NOT promise

- Wave 3 does not solve the bootstrap-TLS gap — the user still
  fetches `install-verify.sh` over TLS without a signature on the
  wrapper itself. That is Wave 4 (Homebrew tap / Scoop bucket /
  apt repo with their own signature stack).
- Wave 3 does not solve post-quantum signature migration. Sigstore
  ML-DSA tracking is ongoing; Wave 4.
- Wave 3 does not address dependency-confusion / typosquat on the
  Python wheel layer — `pip install` still pulls from PyPI without
  hash-pinning. That is Wave 2 SA-4 (`requirements.lock`).
- Wave 3 does not validate the *behavior* of the install scripts;
  it only validates that the bytes shipped match the bytes the
  commit produced. A maintainer who legitimately commits a
  malicious change still produces a release that passes Wave 3
  verification. The defense for that case is Wave 4 L3-Source
  (two-person review on the install/* directory) + community
  bug-bounty.

Honest scope, honest verifier. That is the bar.
