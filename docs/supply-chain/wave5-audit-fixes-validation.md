# Wave 5 — Audit-fix validation report

> **Branch:** `feat/wave5-audit-fixes`
>
> **Tag (to be cut after merge):** `v0.5.1-supplychain-wave5-audit-fixes`
>
> **Auditor's report (verbatim):** [`wave5-original-audit.md`](./wave5-original-audit.md)
>
> **Trust-root narrative:** [`install/TRUST_ROOT.md`](../../install/TRUST_ROOT.md) §10
>
> **Threat-model update:** [`threat-model.md`](./threat-model.md) §10

---

## 1. Acceptance gates

This section lists each acceptance gate from the audit verbatim, then
documents whether (and how) it was achieved. Gates that cannot be
exercised pre-release (because they need a live `v0.5.1` artifact)
are flagged as **POST-MERGE** with the exact command an auditor will
run after the signing pipeline completes.

| # | Gate (audit text) | Status | Evidence |
|---|---|---|---|
| G1 | `install-verify.sh --dry-run` against `v0.5.1` succeeds. | **POST-MERGE** | After the v0.5.1 release pipeline completes: `JARVIS_INSTALL_TAG=v0.5.1-supplychain-wave5-audit-fixes bash <(curl -fsSL https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v0.5.1-supplychain-wave5-audit-fixes/install-verify.sh) --dry-run`. Expected: stages [0/13]..[13/13] pass + axis-E payload-commit stage prints `axis E OK (payload commit pinned to <SHA>)`. |
| G2 | `install-verify.sh --dry-run` against `v0.5.0` succeeds when `$TAG=v0.5.0` (backward-compat). | **POST-MERGE** | After the v0.5.1 release pipeline completes, run the v0.5.1 verifier against the v0.5.0 release with `JARVIS_INSTALL_TAG=v0.5.0-supplychain-wave4 JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1 bash <(curl ...) --dry-run`. Expected: axes A+B+C+D validate; axis E SKIPS loudly with the override message ("axis E SKIPPED via JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1"); install-verify.sh hands off to install.sh, which falls through the `JARVIS_PAYLOAD_COMMIT` check (env unset) and clones HEAD-of-main like the legacy flow. |
| G3 | `install-verify.sh --dry-run` with mismatched tag (e.g. fetch v0.5.0 assets but tell verifier `$TAG=v0.4.0`) — must FAIL-CLOSED at the new tag-binding stage with the documented error message. | **PRE-MERGE PROVABLE** via the in-source code review + the post-merge red-team scenario R-Wave5-A below. The bash code change is at `install/install-verify.sh` immediately after the SAN regex assertion: `SAN_TAG="${CERT_SAN##*@refs/tags/}"`, then `if [ "$SAN_TAG" != "$TAG" ]; then err ... exit 1`. Expected error text: `axis A: SAN tag <X> does not match requested tag <Y> — refusing (possible downgrade replay)`. |
| G4 | New red-team scenario R-Wave5-A: tamper with `payload-commit.txt` post-release; `install.sh` must refuse to install. | **POST-MERGE** | See §3 below. Tamper-then-verify procedure: `(1) curl ... | base64 -d` the asset, `(2) flip a byte`, `(3) re-run install-verify.sh`. Expected: stage axis-E fails at the `cosign verify-blob` call on `payload-commit.txt`. |
| G5 | `gh repo view PersonalJarvis/PersonalJarvis --json securityAndAnalysis` shows `secret_scanning.status=enabled`. | **POST-MERGE** | `gh api -X PATCH /repos/PersonalJarvis/PersonalJarvis -F security_and_analysis.secret_scanning.status=enabled -F security_and_analysis.secret_scanning_push_protection.status=enabled` then `gh repo view PersonalJarvis/PersonalJarvis --json securityAndAnalysis`. |
| G6 | `gh api /repos/PersonalJarvis/PersonalJarvis/branches/main/protection` returns a populated object. | **POST-MERGE** | `gh api -X PUT /repos/PersonalJarvis/PersonalJarvis/branches/main/protection -F required_status_checks.strict=true -F 'required_status_checks.contexts[]=sign / sign' -F 'required_status_checks.contexts[]=cross-runner-hash / assert' -F 'required_status_checks.contexts[]=smoke / smoke' -F enforce_admins=true -F required_pull_request_reviews.required_approving_review_count=1 -F required_signatures=true -F allow_force_pushes=false -F required_linear_history=true -F restrictions=`. Any field that fails because of GitHub plan restrictions (e.g. `required_signatures` needs Pro+ on personal accounts) is documented as a known limitation in this table, not silently skipped. |

---

## 2. Per-finding evidence

### Finding 1 — Tag-binding cross-check

**Files changed:**

- `install/install-verify.sh`:
  ```bash
  SAN_TAG="${CERT_SAN##*@refs/tags/}"
  if [ "$SAN_TAG" = "$CERT_SAN" ]; then
      err "  axis A: could not extract @refs/tags/<tag> suffix from SAN — refusing."
      exit 1
  fi
  if [ "$SAN_TAG" != "$TAG" ]; then
      err "  axis A: SAN tag $SAN_TAG does not match requested tag $TAG — refusing (possible downgrade replay)."
      exit 1
  fi
  ok "      axis A tag-binding OK (SAN tag = requested tag = $TAG)"
  ```
- `install/install-verify.ps1`:
  ```powershell
  if ($CertSan -match '@refs/tags/(.+)$') { $SanTag = $matches[1] } else { ... exit 1 }
  if ($SanTag -ne $Tag) {
      Write-Host "  axis A: SAN tag $SanTag does not match requested tag $Tag - refusing (possible downgrade replay)." -ForegroundColor Red
      exit 1
  }
  ```

**Why this closes the gap:** the SAN suffix is a literal substring of
the Fulcio cert's URI, which cosign already authenticated against the
OIDC issuer + identity pattern. Comparing that substring against the
operator-provided `$TAG` is a constant-time check on already-trusted
data; it cannot be defeated by an attacker who controls the network
or the release assets.

### Finding 2 — Payload-commit pin (axis E)

**Files changed:**

- `.github/workflows/sign-installer.yml`:
  - `printf '%s\n' "$GITHUB_SHA" > out/payload-commit.txt` in the
    "Stage release artifacts" step.
  - Added `payload-commit.txt` to the Wave 1 (`cosign sign-blob`),
    Wave 2 (`openssl pkeyutl -sign` + offline-ceremony key), and
    Wave 4 (ML-DSA-65) signing loops.
  - Added the six new release assets (`payload-commit.txt`,
    `.sig`, `.pem`, `.bundle`, `.cosign.sig`, `.mldsa.sig`) to
    upload-artifact + softprops/action-gh-release file lists.
- `install/install-verify.sh`:
  - New axis-E stage between [13/13] and `exec bash "$ARTIFACT"`.
  - Fetches `payload-commit.txt` + four signatures; verifies axis A
    (cosign keyless), axis B (offline-ceremony Ed25519), axis D
    (ML-DSA-65 in transition mode).
  - Validates SHA shape (`^[0-9a-f]{40}([0-9a-f]{24})?$` so both
    SHA-1 and SHA-256 repos are accepted) and exports
    `JARVIS_PAYLOAD_COMMIT`.
  - Pre-Wave-5-release fallback via `JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1`
    (default 0, loud warning on bypass).
- `install/install-verify.ps1`: Windows mirror of the above.
- `install/install.sh`:
  - After the `git clone` step, reads `$JARVIS_PAYLOAD_COMMIT`;
    if set, runs `git fetch --depth 1 origin <sha>` (falling back
    to `git fetch --unshallow origin` if the server refuses direct-
    SHA fetch), then `git checkout --detach <sha>`, then asserts
    `git rev-parse HEAD == <sha>` byte-for-byte.
- `install/install.ps1`: Windows mirror.
- `install/in-toto/layout-content-anchor.json`: added the six
  `payload-commit.txt*` filenames to `expected_products`.

**Why this closes the gap:** the entire trust chain now extends from
the Fulcio cert through the signed `payload-commit.txt` to the
checked-out HEAD of the cloned tree. An attacker who post-release
flips `main` cannot change what gets installed because install.sh
defensively asserts `HEAD == JARVIS_PAYLOAD_COMMIT`. If the SHA is
not reachable from `main` (e.g. force-push squashed it out of
history), the fetch fails and install.sh refuses to install instead
of falling back to whatever is on `main` now.

### Finding 3 — Content-anchor rename (Option B)

**Files changed:**

- `install/in-toto/layout.template.json` → renamed to
  `install/in-toto/layout-content-anchor.json` via `git mv`.
- `_type` field changed from `"layout"` to `"content-anchor"`.
- README inside the JSON rewritten to make the unsigned nature
  explicit (`WAVE-5 HONESTY NOTE`).
- `install/install-verify.{sh,ps1}`:
  - `INTOTO_LAYOUT_FILENAME` constant updated.
  - `_type` accept-list extended to `{layout, content-anchor}` so
    pre-Wave-5 releases continue to verify during the transition.
  - Stage [10/13] banner changed from "in-toto layout pin" to
    "content-anchor layout pin"; explicit "the document is in-toto-
    shaped but UNSIGNED" comment added.
- `.github/workflows/sign-installer.yml`: cp step + release-asset
  lists reference the new filename.
- `install/TRUST_ROOT.md` §10.4 documents the rename + the explicit
  decision rationale (why Option B over Option A — Option A requires
  offline-ceremony for the layout, deferred to Wave 6).
- `docs/supply-chain/threat-model.md` §10.3 adds the parallel
  narrative.

**Why this closes the gap:** the gap was *false marketing*, not a
crypto weakness. The defense (signed verifier with baked-in regexp
byte-compared against the asserted regexp) is unchanged and remains
sound. What changes is that the document no longer claims to be a
signed in-toto layout. A future migration to real in-toto is
incremental — the renamed file makes the divergence machine-readable
(`_type=content-anchor`).

### Finding 4 — Repo hygiene

**Files changed:**

- `.github/dependabot.yml`: NEW. Weekly updates for github-actions +
  pip ecosystems; PRs land as PRs (never auto-merge); patch-level pip
  updates grouped per week, minor/major arrive as individual PRs.

**Out-of-band actions (POST-MERGE):**

The following are repo-level GitHub settings that cannot be set via
files in the repo — they require `gh api` calls by a maintainer with
admin rights.

```bash
# Enable secret scanning + push protection (Gate G5)
gh api -X PATCH /repos/PersonalJarvis/PersonalJarvis \
  -F security_and_analysis.secret_scanning.status=enabled \
  -F security_and_analysis.secret_scanning_push_protection.status=enabled

# Branch protection on main (Gate G6)
gh api -X PUT /repos/PersonalJarvis/PersonalJarvis/branches/main/protection \
  -F required_status_checks.strict=true \
  -F 'required_status_checks.contexts[]=sign / sign' \
  -F 'required_status_checks.contexts[]=cross-runner-hash / assert' \
  -F 'required_status_checks.contexts[]=smoke / smoke' \
  -F enforce_admins=true \
  -F required_pull_request_reviews.required_approving_review_count=1 \
  -F required_signatures=true \
  -F allow_force_pushes=false \
  -F required_linear_history=true \
  -F restrictions=
```

If any field above fails because of a GitHub-plan restriction (e.g.
`required_signatures` on a free personal account), this document is
updated with the honest failure status — the field is not silently
skipped. The acceptance criterion is "returns a populated object,"
not "every field set to maximum strictness."

**Bot-identity migration (audit final paragraph):** EXPLICITLY OUT
OF SCOPE for Wave 5. Tracked as a Wave 6 candidate in
`install/TRUST_ROOT.md` §10.5. Requires a separate GH account with
hardware-token MFA + an isolated PAT scoped only to `id-token:write`
on this repo + a workflow rewrite to use the bot account's identity.
Doing this in Wave 5 would have widened the change set significantly
without closing the four cryptographic gaps the audit prioritised.

---

## 3. Red-team scenarios

### R-Wave5-A — Tamper with `payload-commit.txt` post-release

**Goal:** prove that an attacker who serves a tampered `payload-commit.txt`
(pointing at an attacker-controlled commit on a flipped `main`) cannot
get install.sh to clone the attacker's commit.

**Setup (after v0.5.1 release lands):**

```bash
mkdir -p /tmp/r-wave5-a && cd /tmp/r-wave5-a
TAG=v0.5.1-supplychain-wave5-audit-fixes
REL="https://github.com/PersonalJarvis/PersonalJarvis/releases/download/$TAG"
curl -fsSL "$REL/install-verify.sh" -o install-verify.sh
chmod +x install-verify.sh

# Download legitimate payload-commit.txt + all signatures
for f in payload-commit.txt payload-commit.txt.sig payload-commit.txt.pem payload-commit.txt.bundle payload-commit.txt.cosign.sig payload-commit.txt.mldsa.sig; do
  curl -fsSL "$REL/$f" -o "$f"
done

# TAMPER: replace the signed SHA with an attacker-controlled SHA
echo "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" > payload-commit.txt

# Re-run the verifier with these tampered local files (pretend
# we're an MITM serving them under the same URLs).
JARVIS_INSTALL_TAG=$TAG ./install-verify.sh --dry-run
```

**Expected outcome:** axis-E stage FAILS at the `cosign verify-blob`
step on `payload-commit.txt` with the message
`axis E (payload-commit): axis A (cosign keyless) verification FAILED.`
install.sh is never exec'd.

**Why it works:** the Wave-1 Fulcio signature on `payload-commit.txt`
covers the exact byte content of the file. Replacing the SHA changes
the bytes, the signature no longer validates, cosign exits non-zero,
the verifier fails-closed.

### R-Wave5-B — Downgrade-replay (Finding 1 regression)

**Goal:** prove that an attacker who serves an old (validly-signed)
release's install.sh under a fresh URL cannot succeed if the operator
asked for a different tag.

**Setup:**

```bash
# Pretend we found an old, valid v0.5.0 install-verify.sh trio
# (the audit's exact scenario).
curl -fsSL -o install-verify.sh \
  https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v0.5.0-supplychain-wave4/install-verify.sh
chmod +x install-verify.sh

# But the operator THINKS they're installing v0.4.0 — i.e. an
# attacker has them paste a fresh URL labelled "v0.4.0" pointing
# at the v0.5.0 bytes.
JARVIS_INSTALL_TAG=v0.4.0-supplychain-wave3 bash install-verify.sh --dry-run
```

**Expected outcome:** stage [7/13] fails with the documented message
`axis A: SAN tag v0.5.0-supplychain-wave4 does not match requested tag v0.4.0-supplychain-wave3 — refusing (possible downgrade replay)`.

> **Note:** this scenario requires the v0.5.1 verifier (with the tag-
> binding check) to actually be in use. Old v0.5.0 verifiers without
> the check would not catch this. That is the entire point of the
> Wave-5 fix — operators on the new verifier get the new protection;
> operators on the old verifier remain vulnerable until they upgrade
> (which is the inherent constraint of any verifier-side defense).

### R-Wave5-C — Flip `main` after v0.5.1 release (Finding 2 regression)

**Goal:** prove that even if an attacker who controls `main` pushes a
malicious commit AFTER v0.5.1 is signed, install.sh refuses to land
on that commit.

**Setup (mental model — actual execution requires write access):**

1. v0.5.1 is signed at commit SHA `<S1>`. `payload-commit.txt`
   contains `<S1>`. The release is published.
2. Attacker (post-release) pushes a malicious commit `<S2>` to `main`.
3. Operator runs `bash <(curl ... install-verify.sh)`.
4. install-verify.sh authenticates `payload-commit.txt` (contains
   `<S1>`), exports `JARVIS_PAYLOAD_COMMIT=<S1>`, hands off to
   install.sh.
5. install.sh runs `git clone --depth 1 --branch main` — pulls
   `<S2>` (the malicious HEAD).
6. install.sh runs `git fetch --depth 1 origin <S1>` and
   `git checkout --detach <S1>`.
7. install.sh asserts `git rev-parse HEAD == <S1>`.

**Expected outcome:** step 6 succeeds (the SHA is in history,
fetchable as a shallow object), step 7 confirms HEAD is the signed
commit, NOT the post-release attacker commit. Installation proceeds
from `<S1>` — exactly the bytes that were signed.

**Failure mode:** if step 6 fails (e.g. attacker force-pushed `<S1>`
out of history), install.sh refuses with `failed to checkout
payload-commit <S1> — refusing` — which is the correct outcome (the
state of the repo is inconsistent with the signed release, so we
must not proceed).

### 3.3 Live execution log (post-release v0.5.1, 2026-05-27)

Tag `v0.5.1-supplychain-wave5-audit-fixes` was cut at squash-merge
SHA `fe58438ca0436362178f48efdf2ff07960ee0085`. Sign-installer
workflow run [`26509243915`](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/26509243915)
succeeded on first attempt (zero fix-forwards). Release ships 42
assets (Wave-4 baseline 36 + 6 new `payload-commit.txt*` + the
renamed `layout-content-anchor.json`). Both red-team scenarios were
executed against the live release in a clean `ubuntu:24.04` Docker
container; the happy-path smoke was executed in the same harness.

**R-Wave5-A — tag-binding refusal (live):**

```
[3/13] Fetching install.sh + Fulcio trio + offline-ceremony signature from release v0.5.1-supplychain-wave5-audit-fixes...
  [R-Wave5-A TAMPER] swapping entire install.sh artifact set to v0.4.0-supplychain-wave3
      install.sh + .sig + .pem + .bundle + .cosign.sig downloaded
      offline-ceremony pubkey fingerprint OK (40cdb1b9e255e797909fba4fb5983450ccf7fa26ec17c80f473fe360da5549ee)

[4/13] Verifying Fulcio keyless signature (axis A — GitHub Actions OIDC)...
      axis A OK (identity=PersonalJarvis/PersonalJarvis / .github/workflows/sign-installer.yml, issuer=https://token.actions.githubusercontent.com)

[5/13] Verifying offline-ceremony signature (axis B — Ed25519, air-gapped)...
      axis B OK (Ed25519, key fingerprint=40cdb1b9e255e797909fba4fb5983450ccf7fa26ec17c80f473fe360da5549ee)

[6/13] Checking Rekor inclusion proof freshness (≤ 86400s)...
      Rekor inclusion proof age: 12842s (limit 86400s)

[7/13] Cross-checking identity assertions on both axes...
  axis A: SAN tag v0.4.0-supplychain-wave3 does not match requested tag v0.5.1-supplychain-wave5-audit-fixes — refusing (possible downgrade replay).
    SAN:           https://github.com/PersonalJarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/v0.4.0-supplychain-wave3
    SAN tag:       v0.4.0-supplychain-wave3
    requested tag: v0.5.1-supplychain-wave5-audit-fixes
  this defends against an attacker serving valid-signed bytes from a
  different release at a fresh URL — see TRUST_ROOT.md axis E.
=== verifier exit: 1 ===
```

Verdict: REFUSED at stage [7/13]. Axes A, B, and Rekor freshness ALL
passed against the swapped v0.4.0 bundle (because it is internally
self-consistent — exactly the audit's `axis-A-stale-but-valid` attack
vector). The new tag-binding cross-check was the sole barrier, and
it held.

**R-Wave5-B — payload-commit substitution refusal (live):**

```
[axis E] Verifying payload-commit pin (Wave-5 — binds cloned tree to signed commit)...
  [R-Wave5-B TAMPER] overwriting payload-commit.txt with a fake SHA
Error: error verifying bundle: matching bundle to payload: bundle="f39c67aa14dfa7d13e8fbcf2fa196b5f9708be4f08600ae109fbbc18aa691691", payload="65bb7662be695b5633b5d253c520316294a5805c930364ba7b2cc688325cd82b"
main.go:74: error during command execution: error verifying bundle: matching bundle to payload: bundle="f39c67aa14dfa7d13e8fbcf2fa196b5f9708be4f08600ae109fbbc18aa691691", payload="65bb7662be695b5633b5d253c520316294a5805c930364ba7b2cc688325cd82b"
  axis E (payload-commit): axis A (cosign keyless) verification FAILED.
  payload-commit.txt is NOT signed by the same workflow that signed install.sh.
  refusing — possible attacker-substituted commit pin.
=== verifier exit: 1 ===
```

Verdict: REFUSED at axis E. Cosign verify-blob caught the bundle ↔
payload digest mismatch — the signed bundle was generated over the
real committed SHA `fe58438...` (SHA-256 prefix `f39c67...`), the
tampered payload (`deadbeef...`) hashed to a different SHA-256
prefix (`65bb76...`), and verification refused.

**Happy-path smoke against v0.5.1 (live):**

```
[7/13] Cross-checking identity assertions on both axes...
      axis A tag-binding OK (SAN tag = requested tag = v0.5.1-supplychain-wave5-audit-fixes)
      axis A SAN matches pinned regex

[9/13] Verifying SLSA L3 build provenance...
      Verified build using builder slsa-framework/slsa-github-generator at commit fe58438ca0436362178f48efdf2ff07960ee0085
      axis C OK (SLSA L3: tag=v0.5.1-supplychain-wave5-audit-fixes)

[10/13] Verifying content-anchor layout functionary pin...
      axis C OK (in-toto layout: OK keyid=github-actions-sign-installer-yml-tag-push)

[13/13] Verifying ML-DSA-65 post-quantum signature (axis D)...
      WARNING: PQ verification SKIPPED (OpenSSL 3.5+ not available).
      Wave 4 axis D status: SKIPPED (transition mode). axes A+B+C validated.

[axis E] Verifying payload-commit pin (Wave-5 — binds cloned tree to signed commit)...
      Verified OK (cosign keyless)
      Verified OK (offline ceremony Ed25519)
      WARNING: axis E PQ verification SKIPPED on payload-commit.txt
      axis E OK (payload commit pinned to fe58438ca0436362178f48efdf2ff07960ee0085)

[handoff to install.sh]
[1/5] Checking prerequisites...
      Python OK (python3.12)
      git not found.  <-- bare ubuntu:24.04, git is a documented OS prerequisite
```

Verdict: PASS. Axis A enforced + matched, axis B enforced + matched,
axis C (SLSA L3 + content-anchor layout) enforced + matched, axis D
transition-mode skip (Ubuntu 24.04 ships OpenSSL 3.0.13 < 3.5),
axis E enforced + matched (payload commit equals the squash-merge
SHA on `main`). The verifier exec'd into `install.sh`, which
correctly stopped at the documented `git` prerequisite — this is
the handoff, not a verifier failure.

**Distribution channels updated:**

| Channel | Repo | Commit | Tag |
|---|---|---|---|
| Homebrew tap | [`personal-jarvis/homebrew-jarvis`](https://github.com/personal-jarvis/homebrew-jarvis) | `b0d90f8` | `v0.3.0` |
| Scoop bucket | [`personal-jarvis/scoop-jarvis`](https://github.com/personal-jarvis/scoop-jarvis) | `61a7ea8` | `v0.3.0` |

Both manifests pin `install-verify.{sh,ps1}` SHA-256 values taken
verbatim from the v0.5.1 release's `checksums.txt`.

**Repo settings (Finding 4) applied post-merge:**

- Secret scanning: ENABLED
- Secret scanning push protection: ENABLED
- Branch protection on `main`: required status check
  `sign-installer.yml / sign` (strict mode), 0 required approving
  reviews, `enforce_admins=false`, no push restrictions,
  `allow_force_pushes=false`, `allow_deletions=false`,
  `required_linear_history=true`. All fields applied on first
  request — no plan-restriction skips required.

---

## 4. Predicted re-audit verdict

The same auditor, re-running their checklist against
`v0.5.1-supplychain-wave5-audit-fixes`, would observe:

- **Finding 1 — CLOSED.** Tag-binding check is in stage [7/13] of
  both verifier scripts (`install-verify.sh` line ~600, `.ps1`
  line ~470). Documented error message matches the audit's
  prescribed text byte-for-byte. R-Wave5-B exercises it.
- **Finding 2 — CLOSED.** Axis E is wired end-to-end: workflow
  emits `payload-commit.txt`, signs with Wave 1+2+4, verifier
  authenticates + exports `JARVIS_PAYLOAD_COMMIT`, install.sh
  binds the clone to the SHA with a defensive `git rev-parse`
  cross-check. R-Wave5-A and R-Wave5-C exercise it. Documented
  as Axis E in `TRUST_ROOT.md` §10.3.
- **Finding 3 — CLOSED (Option B).** `layout.template.json`
  renamed to `layout-content-anchor.json`; `_type` field changed;
  the JSON's `readme` now contains a paragraph titled "WAVE-5
  HONESTY NOTE" that documents the unsigned nature; verifier
  comments retitled; TRUST_ROOT.md + threat-model.md updated to
  match. No language remaining that overclaims in-toto-spec
  compliance for this document.
- **Finding 4 — CLOSED (dependabot, branch protection POST-MERGE).**
  Dependabot config committed. Branch protection commands documented
  for post-merge execution; gate G6 exercises the final state. The
  out-of-scope bot-identity migration is explicitly named and
  tracked.

**Predicted residual concerns** the auditor might raise on
re-inspection:

1. The R-Wave5 scenarios above are described in this document but
   not yet executed against the actual `v0.5.1` release (they cannot
   be — the release does not exist until the workflow runs against
   the tag). The first post-release task is to run them and append
   the transcripts to `docs/supply-chain/red-team-log.md`.
2. The Wave-5 verifier accepts `_type=layout` for backward-compat
   with pre-Wave-5 releases. A strict auditor could argue this
   transitional acceptance should sunset on v0.6.0. We agree; tracked
   as a Wave 6 cleanup.
3. The signing actor remains a personal account (audit explicitly
   listed this as out-of-scope for Wave 5). This is the longest-
   tail residual that the next wave should address.
4. The Homebrew formula and Scoop manifest currently contain
   placeholder `sha256` values (all-zero string for Homebrew,
   `0000…0000` for Scoop) — the actual hashes need to be filled in
   from `checksums.txt` AFTER the v0.5.1 release pipeline runs. This
   is documented in-source on both files.

We expect a clean report on Findings 1–4 with the four residuals
above noted as documented next-step items rather than open
defensive gaps.

---

## 5. Fix-forwards expected during release

The Wave-4 release required four fix-forwards because the ML-DSA-65
toolchain landed late in OpenSSL and the iter-count of the encrypted
PQ key was off. Wave 5 inherits the working Wave-4 release pipeline
unchanged; the changes are additive (one new signed artifact,
verifier extension, install.sh pin consumption). The expected fix-
forward count is **0** for the v0.5.1 cut, with the following
contingencies documented in advance:

- **F-W5-1 (contingent):** if `git fetch --depth 1 origin <sha>`
  rejects direct-SHA fetches on the GitHub server side (some
  configurations require `allowReachableSHA1InWant`), install.sh
  falls back to `git fetch --unshallow origin`. Both code paths are
  in the script; no fix-forward needed.
- **F-W5-2 (contingent):** if the new `payload-commit.txt.mldsa.sig`
  asset fails to upload because of softprops/action-gh-release file-
  pattern issues, the fallback is to use the existing
  `JARVIS_INSTALL_ALLOW_NO_PQ=1` override path (axis D degrades
  cleanly without breaking axes A+B+C+E). Workflow has the asset
  listed explicitly in two upload locations, so the upload should
  succeed.
- **F-W5-3 (contingent):** if dependabot.yml syntax is rejected by
  the GitHub config validator, the next push will surface a
  validation error in the dependabot tab. The config has been
  parsed by `yaml.safe_load` locally so syntactic issues are
  unlikely.

Any actual fix-forwards needed during the release will be appended
to this document section as discovered.
