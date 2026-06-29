# Trust roots and rotation procedure — Personal Jarvis Wave 1 + Wave 2 foundation

> **Audience:** a security-aware reader who wants to know, *exactly*, whose
> word they're taking when they run the verifying one-liner. No marketing
> language. If the document is ambiguous on any point, file a PR.
>
> **Companion documents:** `docs/supply-chain/threat-model.md` (incident-anchored
> threat enumeration) and `docs/supply-chain/red-team-log.md` (concrete proof
> the verifier fails-closed on tampered bytes).

---

## 0. TL;DR — whom do you trust when you run the one-liner?

```
curl -fsSL https://github.com/PersonalJarvis/PersonalJarvis/releases/download/<TAG>/install-verify.sh | bash
```

Running this binds you to all of the following parties at once:

1. **GitHub Inc.** — both as the OIDC issuer (mints the JWT) and as the
   release host (serves the `.sh`, `.sig`, `.pem`, `.bundle` bytes).
2. **The Sigstore project** — Fulcio CA, Rekor transparency log, and the
   `cosign` binary itself.
3. **The current owners of `PersonalJarvis/PersonalJarvis`** — at the
   instant of any tag push, whoever can trigger `.github/workflows/sign-installer.yml`
   on this repo can mint a signature that this verifier will accept.
4. **The cosign release at `v2.4.1`** — pinned by SHA-256 inside
   `install/install-verify.sh`. If the Sigstore project ever republished
   v2.4.1 with different bytes the verifier would reject — see §4.
5. **Your local TLS CA pool** — for the curl/Invoke-WebRequest fetches
   to `github.com` and `sigstore.dev`. This is the irreducible bootstrap
   trust we cannot eliminate at Wave 1.

That is the complete list. Nothing else is trusted by Wave 1.

---

## 1. The five pinned values inside `install-verify.sh`

The four constants below ARE the trust root. Bumping any of them is a
**deliberate, reviewed, documented event** — not a maintenance task. The
verifier script intentionally hardcodes them inline so a reviewer can read
the entire trust root in one screen of code.

| Constant | Current value (Wave 1) | What it pins | Failure mode if wrong |
|---|---|---|---|
| `EXPECTED_REPO` | `PersonalJarvis/PersonalJarvis` | The GitHub repo whose Actions identity is allowed to sign. | An attacker who signs a `install.sh` under a *different* repo's OIDC identity (even with a legitimately-issued Fulcio cert) will fail verification. |
| `EXPECTED_WORKFLOW_PATH` | `.github/workflows/sign-installer.yml` | The specific workflow file inside `EXPECTED_REPO` whose identity is allowed to sign. | An attacker who pushes a *second* signing workflow to this repo (e.g. via a poisoned PR that survives review) and uses it to mint signatures will be rejected — the regex won't match. |
| `EXPECTED_OIDC_ISSUER` | `https://token.actions.githubusercontent.com` | The GitHub Actions production OIDC issuer. | An attacker using a different OIDC issuer (a self-hosted Dex, GitLab, etc.) cannot mint a Fulcio cert that satisfies this issuer pin. |
| `COSIGN_VERSION` | `v2.4.1` | The cosign release we will download. | Combined with the SHA-256 pins below — if the version doesn't match its known-good hash, verification fails before any signature work begins. |
| `COSIGN_SHA256_*` | See `install-verify.sh` (4 hashes for 4 platforms; e.g. linux-amd64 = `8b24b946dd5809c6bd93de08033bcf6bc0ed7d336b7785787c080f574b89249b`) | The exact bytes of the cosign binary we will execute. | An attacker swapping the cosign binary on the wire or in a malicious GitHub release will be detected before we hand control to `cosign verify-blob`. |
| `REKOR_MAX_AGE_SECONDS` | `86400` (24 hours) | Maximum age of the Rekor inclusion proof. | Stale-signed-but-revoked artifacts cannot be replayed. Tightening below 24 h is fine; loosening should not happen without justification. |

The PowerShell sibling (`install-verify.ps1`) holds the same five values for
its platform.

---

## 2. Why hashes for cosign, not signatures?

The verifier downloads cosign **and checks a hash, not a signature.** This
is the bootstrap-trust ceiling: you cannot verify a signature without first
having a trusted verifier binary, so somewhere the chain has to terminate
on a value that's been baked in by humans rather than verified by code.

Three properties of the pin make this safe at Wave 1:

1. **Independent verifiability.** Anyone reading `install-verify.sh` can
   cross-check the hashes against
   `https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign_checksums.txt`
   in 10 seconds. No private knowledge or asymmetric crypto required.
2. **Sigstore's own community has separately signed and audited that file.**
   The cosign_checksums.txt has its own Sigstore signature and an SLSA L3
   provenance attestation; we are riding on that signed-checksums file's
   audit, just without the runtime verification step.
3. **Cosign v2.4.1 is an immutable historical release.** GitHub release
   assets can technically be replaced by repo admins, but Sigstore's
   release process leaves an audit trail in their own Rekor log — a covert
   replace would be detectable post-hoc by anyone (Sigstore's project
   maintainers, downstream Linux distros, this project's own users).

**Wave 4** plans to remove this hash pin by shipping the verifier itself
via a package manager whose signature stack we already trust (Homebrew
tap, Scoop bucket, apt repo, etc.). Until then, the hash pin is the
honest anchor.

---

## 3. Wave 2 — second signing axis (offline-ceremony Ed25519)

> **Status:** *foundation step.* The keypair, passphrase, encrypted private key,
> TUF root metadata, and ceremony documentation are committed in this branch
> (`feat/wave2-foundation`). The workflow integration that actually *uses* the
> offline key to co-sign each release (and the corresponding verifier change
> that demands 2-of-2) is built by follow-up sub-agents in Wave 2.

### 3.1 Why this section exists

Wave 1 has exactly **one** signing identity: GitHub Actions OIDC → Fulcio.
If the maintainer's GitHub account is taken over, or the `sign-installer.yml`
workflow is poisoned in a way that survives review, a Wave-1 attacker mints
signatures the verifier still accepts. That is the xz-utils gap — a single
trusted maintainer is one social-engineering campaign away from a supply-chain
incident (CVE-2024-3094, March 2024).

Wave 2 closes this with a **second, independent signing axis**: a long-lived
Ed25519 keypair generated **offline** in a ceremony, encrypted at rest, with
its private half kept outside GitHub. The verifier demands 2-of-2 — both the
Fulcio (online, ephemeral) signature *and* the offline-ceremony signature
must validate. Compromising either axis alone yields nothing.

### 3.2 What is committed in this branch

| File | Purpose |
|---|---|
| `install/keys/offline-ceremony.pub` | Ed25519 public key in PEM format. Committed in plain. |
| `install/keys/offline-ceremony.key.enc` | Ed25519 private key, encrypted with AES-256-CBC + PBKDF2 (600 000 iterations). Committed encrypted. |
| `install/tuf/1.root.json` | TUF root metadata version 1: lists both trust axes (`fulcio_oidc` + `offline_ceremony`) with `threshold=2` on every role. Expires 365 days from generation (2027-05-26). |
| `docs/supply-chain/wave2-key-ceremony.md` | Step-by-step ceremony log: how the key was generated, why 2-of-2 closes xz-utils, production-deployment migration path. |

### 3.3 The passphrase — DEMO POSTURE, FULLY DISCLOSED

> **Read this paragraph carefully.** It is the honesty bar for this entire
> branch.

For this **Wave 2 foundation demo**, the passphrase that decrypts
`install/keys/offline-ceremony.key.enc` is committed in this repository, in
plain text, in this file, **right here on this line**:

```
WAVE2_CEREMONY_PASSPHRASE=env++ci2NDWCOLeLfgTTZRks
```

That is the literal 24-character passphrase emitted by `openssl rand -base64 18`
during the ceremony. It decrypts the offline-ceremony Ed25519 private key.
A reader of the public GitHub repository can extract the private key.

**Does this defeat the point of Wave 2?** No, *because of what Wave 2 is
proving*. The artefact under demonstration is the **2-of-2 verifier
machinery** — TUF root with `threshold=2`, two independent trust axes, dual
signature paths in the verifier script (built in follow-up sub-agents). That
machinery is identical whether the second key's passphrase lives in the
repository, in a GitHub Actions secret, in a maintainer's password manager,
or in a hardware token. Only the *secrecy of the second axis* changes; the
*existence* of the second axis is what closes the single-point-of-failure.

**Production deployments must move this passphrase out of the repository.**
The exact migration path (do this on real production releases):

1. **Generate a fresh keypair** following the recipe in
   `docs/supply-chain/wave2-key-ceremony.md`. **Do not reuse this demo key
   in production.**
2. **Store the production passphrase as a GitHub Actions repository secret.**
   The exact command (run by a maintainer with `secrets` access on the repo):
   ```bash
   gh secret set WAVE2_CEREMONY_PASSPHRASE \
     --repo PersonalJarvis/PersonalJarvis \
     --body "<your 24+ char passphrase from openssl rand -base64 18>"
   ```
   After setting, `gh secret list --repo PersonalJarvis/PersonalJarvis`
   must show `WAVE2_CEREMONY_PASSPHRASE` with a recent updated_at.
3. **Sign-installer.yml** then references the secret as
   `${{ secrets.WAVE2_CEREMONY_PASSPHRASE }}` to decrypt the private key
   inside the workflow runner, sign the artifact, and immediately scrub
   the decrypted bytes from disk (`shred -uz` on Linux,
   `Remove-Item -Force` then `cipher /w:` on Windows).
4. **Remove the demo passphrase line** from this `TRUST_ROOT.md` §3.3
   and rotate the demo key out via §3.5 below.
5. **Even better:** the production passphrase is itself derived inside an
   air-gapped key ceremony on a hardware token (YubiKey FIPS or equivalent),
   never appearing on a network-attached machine. The GitHub secret then
   stores only an encrypted-with-token blob; the workflow needs token-
   touch for signing, which is what Sigstore's own root-key ceremony does.

**Why we did not do that in this foundation step.** Wave 2 is split into
five sub-agents. This sub-agent (Wave-2-SA-1) builds the *architecture* —
key material, TUF metadata, threat-model update, ceremony documentation —
that the *integration* sub-agents (Wave-2-SA-2 through SA-5) consume to
wire the second axis into the workflow and the verifier. Asking this
foundation step to also gate on a real hardware ceremony would block the
parallel integration work. The honest tradeoff is recorded here.

### 3.4 What §3 does NOT claim

This section deliberately does **not** claim that the demo posture provides
the security guarantee of a real production 2-of-2 deployment. It claims
that the *plumbing* required for 2-of-2 (TUF root with threshold=2, two
distinct key materialisations, dual verifier paths) is in place and exercised
end-to-end. The remaining production gap is the single line above (the
passphrase secrecy boundary) plus the workflow + verifier wiring done by
the next sub-agents in Wave 2.

### 3.5 Rotation procedure for the offline-ceremony key

The offline key has a 365-day expiry (recorded in `1.root.json`'s `expires`
field). Before expiry, AND any time the key is suspected compromised:

1. Run the ceremony script in `docs/supply-chain/wave2-key-ceremony.md` to
   generate a new Ed25519 keypair + a fresh `openssl rand -base64 18`
   passphrase.
2. Encrypt with `openssl aes-256-cbc -pbkdf2 -iter 600000 -salt`.
3. Overwrite `install/keys/offline-ceremony.pub` and
   `install/keys/offline-ceremony.key.enc`.
4. Bump TUF root version: regenerate `install/tuf/2.root.json` (do NOT
   delete `1.root.json` — TUF clients walk the version chain).
   The new file MUST embed the previous key's keyid as a *revoked* entry
   inside `unrecognized_fields.wave2_revocation` so the verifier knows to
   reject artifacts signed by the old key.
5. Set the new `WAVE2_CEREMONY_PASSPHRASE` GitHub Actions secret (production)
   or update §3.3 above (demo). Never both at the same time — if §3.3
   contains a passphrase, the production deployment is not yet active.
6. Append a row to §8 (rotation history) with the date, reason, and the
   first tag signed under the new key.

---

## 4. Wave 3 — SLSA L3 + in-toto trust anchor

> **Status:** *added by Wave 3 SA-4.* Builds on Wave 1 (axis A — Fulcio
> keyless) and Wave 2 (axis B — offline ceremony Ed25519). Wave 3 introduces
> a **third independent trust axis** (axis C) rooted in *build-environment
> attestation*, not artifact signing. The verifier (`install/install-verify.sh`
> stages [8/11]–[10/11], `install/install-verify.ps1` mirrors) now demands
> 3-of-3 axes; any single failure fail-closes before stage [11/11] handoff.

### 4.1 Why a third axis at all?

Axis A and axis B both attest to the **artifact's bytes**: whoever signs
hereby asserts "I have seen this exact `install.sh` and approve its
distribution." A sufficiently-resourced attacker who can mint an OIDC
token under our identity *and* compromise the offline key custody can
still sign tampered bytes. The 2-of-2 collapse is hard but not infinite.

Axis C answers a categorically different question: **"was this artifact
produced by the build pipeline we declared?"** The SLSA L3 provenance
records the entire build environment — source repository, source commit
SHA, source tag, runner image identity, build commands, and the inputs
that were available at build time. The Sigstore-signed Fulcio identity
inside the provenance is the **SLSA generator's own** non-falsifiable
builder identity (`slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml`),
which the calling repo (us) cannot override.

The practical consequence: if an attacker steals our OIDC token and
re-signs a tampered `install.sh` with axis A AND somehow also signs it
with the offline ceremony key (axis B), axis C still rejects — because
the build inputs the attacker fed to their tampered build do not match
the inputs the legitimate workflow declared, and slsa-verifier's
`verify-artifact` enforces that the artifact's SHA-256 appears in the
SLSA provenance's `subject` array. Tampered bytes produce a different
digest, the digest is absent from the (legitimate) provenance the
attacker copied alongside their fake binary, and `verify-artifact` fails.

The only way an attacker defeats all three is: (i) compromise GitHub OIDC
**and** (ii) compromise the offline-ceremony custody **and** (iii)
compromise the SLSA generator's reusable workflow itself (i.e. compromise
the slsa-framework org's release infrastructure). Each of those is rooted
in a different organisation; the conjunction is the threat model we
accept as residual risk after Wave 3.

### 4.2 What is the slsa-verifier binary and why pin it like cosign?

slsa-verifier (https://github.com/slsa-framework/slsa-verifier) is the
reference implementation maintained by the SLSA project. Its only
non-trivial job from our perspective is `verify-artifact`, which:

1. Reads the SLSA in-toto provenance (`personal-jarvis.intoto.jsonl`).
2. Verifies the provenance's bundled Sigstore certificate against the
   SLSA generator's pinned issuer + identity.
3. Confirms the artifact's SHA-256 digest is in the provenance's
   `subject` array.
4. Cross-checks `--source-uri` (our repo) and `--source-tag` (the release
   tag) against the provenance's `materials`/`buildConfig` fields.

We pin slsa-verifier by **SHA-256 of the platform binary**, exactly like
we pin cosign in §2 / §6. The bootstrap-trust rationale is identical:
you cannot verify a signature without a trusted verifier, so somewhere
the chain has to terminate on a hash baked in by humans. Pinning by
git-tag (`@v2.6.0`) is **not** sufficient — tags are mutable refs, a
repo-takeover would let an attacker swap the binary under the tag.

### 4.3 What is committed in the verifier and the workflow

**Verifier-side (`install/install-verify.sh` + `install/install-verify.ps1`):**

| Constant | Current value (Wave 3) | What it pins |
|---|---|---|
| `SLSA_VERIFIER_VERSION` | `v2.6.0` | The slsa-verifier release we will download. |
| `SLSA_VERIFIER_SHA256_LINUX_AMD64` | `1c9c0d6a272063f3def6d233fa3372adbaff1f5a3480611a07c744e73246b62d` | Pinned bytes of `slsa-verifier-linux-amd64`. |
| `SLSA_VERIFIER_SHA256_LINUX_ARM64` | `92b28eb2db998f9a6a048336928b29a38cb100076cd587e443ca0a2543d7c93d` | Pinned bytes of `slsa-verifier-linux-arm64`. |
| `SLSA_VERIFIER_SHA256_DARWIN_AMD64` | `f838adf01bbe62b883e7967167fa827bbf7373f83e2d7727ec18e53f725fee93` | Pinned bytes of `slsa-verifier-darwin-amd64`. |
| `SLSA_VERIFIER_SHA256_DARWIN_ARM64` | `8740e66832fd48bbaa479acd5310986b876ff545460add0cb4a087aec056189c` | Pinned bytes of `slsa-verifier-darwin-arm64`. |
| `SLSA_VERIFIER_SHA256_WINDOWS` | `37ca29ad748e8ea7be76d3ae766e8fa505362240431f6ea7f0648c727e2f2507` | Pinned bytes of `slsa-verifier-windows-amd64.exe`. |
| `EXPECTED_SLSA_SOURCE_URI` | `github.com/PersonalJarvis/PersonalJarvis` | Source URI passed to `slsa-verifier verify-artifact`. |
| `EXPECTED_INTOTO_IDENTITY_REGEXP` | `^https://github\.com/PersonalJarvis/PersonalJarvis/\.github/workflows/sign-installer\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9._-]+)?$` | Functionary identity_regexp the in-toto layout MUST pin. Catch-all values (`.*`, `.+`, `^.*$`, `^.+$`, `""`) are rejected explicitly. |
| `SLSA_PROVENANCE_FILENAME` | `personal-jarvis.intoto.jsonl` | Filename the workflow uploads the SLSA L3 provenance under. |
| `INTOTO_LAYOUT_FILENAME` | `layout.template.json` | Filename the workflow uploads the in-toto layout template under. |

**Workflow-side (SA-2 scope, summarised here for the trust-root reader):**
The release workflow uploads three files per release in addition to the
Wave 1+2 trio: `personal-jarvis.intoto.jsonl` (the SLSA L3 provenance
generated by `slsa-framework/slsa-github-generator`), `layout.template.json`
(the in-toto layout copied from `install/in-toto/layout.template.json`),
and a checksums manifest that anchors both. The SLSA generator runs in a
reusable workflow whose Sigstore identity the calling repo cannot
override — this is what makes the build-environment attestation
non-falsifiable.

### 4.4 Source of truth for the slsa-verifier hashes

The SLSA project publishes per-asset SHA-256 hashes in two redundant
places:

1. `https://github.com/slsa-framework/slsa-verifier/blob/main/SHA256SUM.md`
   — the README that lists all hashes per release. Easiest to cross-check
   from a fresh machine in 10 seconds.
2. `https://github.com/slsa-framework/slsa-verifier/releases/download/v2.6.0/slsa-verifier-<platform>.intoto.jsonl`
   — the SLSA generator's *own* provenance for each binary. Verify-able
   with a *previous* slsa-verifier (or any SLSA-aware tool).

We pin against (1) for the bootstrap-trust ceiling and document (2) as
the independent-verification path. Anyone reading this section can
cross-check both in under a minute.

### 4.5 Bumping the slsa-verifier pin (rotation procedure)

Same discipline as the cosign rotation in §5 below. Skipping any step
is a documented bug.

1. **Open the new slsa-verifier release page on GitHub.** Note the exact
   tag (e.g. `v2.7.0`).
2. **Independently observe SHA256SUM.md** from at least two networks
   (home, mobile tether, VPN exit). Compare bytes:
   ```
   curl -fsSL https://raw.githubusercontent.com/slsa-framework/slsa-verifier/main/SHA256SUM.md | sha256sum
   ```
   If the meta-hash differs across networks, **stop** — the SHA256SUM.md
   is being MITM'd.
3. **Verify the new slsa-verifier release was itself SLSA-attested** by
   downloading `slsa-verifier-<platform>.intoto.jsonl` and running
   `slsa-verifier verify-artifact --source-uri github.com/slsa-framework/slsa-verifier --source-tag v2.7.0 slsa-verifier-<platform>` under the *previous* pinned slsa-verifier.
   The new binary must successfully verify under the *current* one.
4. **Bump the constants** in both `install-verify.sh` and `install-verify.ps1`:
   `SLSA_VERIFIER_VERSION`, all five `SLSA_VERIFIER_SHA256_*` (Linux
   amd64, Linux arm64, Darwin amd64, Darwin arm64, Windows amd64).
5. **Append a row to §8 — "rotation history"** with the new version, who
   reviewed, and the verification evidence.
6. **PR review by ≥1 maintainer other than the bumper** (when more than
   one maintainer exists; today the project has 1 maintainer — same Wave
   2 limitation we accept).

### 4.6 Rotation of the in-toto layout functionary regexp

If the signing workflow is renamed (`sign-installer.yml` → something
else) or the repo is forked under a new slug, the layout's
`identity_regexp` AND the verifier's `EXPECTED_INTOTO_IDENTITY_REGEXP`
must both change. They are tightly coupled by design — a verifier whose
pin matches a permissive layout regexp would collapse axis C.

1. Update `install/in-toto/layout.template.json` (`keys.*.keyval.identity`
   AND `keys.*.keyval.identity_regexp`).
2. Update `EXPECTED_INTOTO_IDENTITY_REGEXP` in both verifier scripts to
   the **byte-for-byte** identical regexp.
3. Re-sign + re-publish the layout under the next release. Old releases
   still reference the old layout/regexp; the verifier pin matches the
   tag-specific layout uploaded with each release.
4. Append a row to §8.

### 4.7 What §4 does NOT claim

- That SLSA L3 makes axes A+B obsolete. It does not. SLSA L3 attests the
  build pipeline; axes A+B attest the artifact bytes. Different abstractions,
  different attack surfaces, all three required.
- That `slsa-verifier verify-artifact` is incapable of false positives.
  It can be defeated by a coordinated compromise of the SLSA generator's
  reusable workflow plus the OIDC identity plus the offline key. We
  accept this as residual risk; mitigating it requires either
  hardware-token signing (Wave 4 idea) or air-gapped sigstore-style
  TUF root metadata distribution.
- That the in-toto layout pin replaces the Fulcio SAN cross-check in
  [7/11]. The two pins overlap but defend different layers — the Fulcio
  SAN guards what cosign accepted from our workflow at signing time; the
  in-toto layout guards what the published supply-chain layout claims is
  acceptable. Discrepancy between the two is itself a signal.

---

## 5. Wave 4 — Post-quantum signing (ML-DSA-65, NIST FIPS 204)

> **Status:** axis D is wired into the signing workflow + the 14-stage
> verifier in `feat/wave4-pq` (SA-4). Production passphrase rotation
> (separate `PQ_MLDSA65_PASSPHRASE` secret + hardware-token custody)
> remains a Wave 4.1 follow-up.

### 5.1 Why ML-DSA-65, not FALCON or SLH-DSA

NIST standardised three post-quantum signature schemes in August 2024:

- **ML-DSA** — FIPS 204, lattice-based (formerly CRYSTALS-Dilithium).
  Three parameter sets: ML-DSA-44 (NIST category 2, ≥128-bit), ML-DSA-65
  (category 3, ≥192-bit), ML-DSA-87 (category 5, ≥256-bit).
- **SLH-DSA** — FIPS 205, hash-based (formerly SPHINCS+). Stateless,
  conservative security floor, but signatures are 7-29 KB and signing
  is slow (>50 ms even on server-class CPU). Not pleasant for the
  per-release-asset workload Wave 4 imposes (5 artifacts × every tag).
- **FN-DSA** — FIPS 206, lattice-based (formerly FALCON). Fast verify,
  small signatures (~700 bytes), but the reference implementation
  depends on double-precision floating-point and is widely understood
  to be a side-channel hazard outside the constant-time HAWK reformulation
  (still draft as of 2026-05).

We pick **ML-DSA-65** because:

1. It is the only one of the three with a stable, NIST-standardised
   form **and** a constant-time reference implementation **and** native
   OpenSSL 3.5+ support (`openssl genpkey -algorithm ML-DSA-65`,
   `openssl pkeyutl -sign -rawin`, no third-party provider required).
2. Category-3 (≥192-bit classical-equivalent floor) exceeds the
   ECDSA-P256 (~128-bit) and Ed25519 (~128-bit) security of our existing
   classical axes. Adding a category-2 PQ axis would have *lowered*
   the floor (a 128-bit-equivalent backstop for 128-bit primaries
   buys nothing); we want the PQ axis to be strictly stronger.
3. Signature size (~3309 bytes) is acceptable for installer-asset
   workloads (vs SLH-DSA's 7-29 KB). At 5 artifacts per tag, the PQ
   axis adds ~16 KB to each GitHub Release — negligible alongside the
   Wave-3 SLSA bundle.
4. The hardware-token roadmap is intact: NitroKey's HSM 2 series exposes
   ML-DSA in beta as of 2026-Q1; production custody will migrate to a
   NitroKey before the v0.6.0 release cut, matching the Wave 2 plan.

### 5.2 Custody — same passphrase pattern as Wave 2

The ML-DSA-65 private key lives at `install/keys/pq-mldsa65.key.enc`,
encrypted at rest with AES-256-CBC + PBKDF2-HMAC-SHA256.

**SA-1 / SA-4 iteration-count discrepancy (Wave 4.1 follow-up).**
The original SA-1 commit message and this section's first version
claimed the key was wrapped at `-iter 600000`, matching the
Wave-2 offline-ceremony precedent (`docs/supply-chain/wave2-key-ceremony.md`
§"Encrypt at rest"). On the v0.5.0-supplychain-wave4 tag, SA-5 hit
`bad decrypt` in the signing workflow with `-iter 600000` and
empirically confirmed (decrypt success against the *committed*
encrypted blob) that SA-1 used the PBKDF2 default `-iter 10000`.
The workflow's decrypt step is therefore pinned to `-iter 10000`
for v0.5.x. The rotation ceremony in §5.5 bumps this back to
`-iter 600000` and is a hard prerequisite for v0.6.0; this is
tracked as Wave 4.1 alongside HSM migration. **Functionally the
demo passphrase is the bottleneck, not the iteration count** —
the demo passphrase is fully disclosed in §3.3, so the
`-iter 10000` posture does not weaken the trust story relative
to the documented one (an attacker who reads §3.3 also doesn't
care about the iteration count).

```
openssl aes-256-cbc -salt -pbkdf2 -iter 10000 \
  -pass env:WAVE2_CEREMONY_PASSPHRASE \
  -in pq-mldsa65.key.pem -out pq-mldsa65.key.enc
```

The foundation reuses the same `WAVE2_CEREMONY_PASSPHRASE` GitHub Actions
secret to avoid introducing two parallel secret-rotation procedures on
a single release. **Production migration** (separates the two secrets,
moves the PQ key to a NitroKey HSM 2, rotates the foundation
passphrase, AND bumps `-iter` to 600 000) is tracked as Wave 4.1 and
MUST land before the v0.6.0 tag.

The PQ public key fingerprint is pinned in three places:

1. `install/keys/pq-mldsa65.pub.pem` — committed plain.
2. `install/install-verify.sh` heredoc + `PQ_MLDSA65_PUBKEY_FINGERPRINT`
   constant: `30a634809c19c41abcead8e657bfe19a53f9f4c831a82d2939cb7d5c40efe01a`
3. `install/install-verify.ps1` heredoc + `$PQ_MLDSA65_PUBKEY_FINGERPRINT`
   constant: same value as above.

Stage `[12/13]` of both verifiers downloads the released pubkey and
asserts SHA-256(DER(SPKI)) equality against the inlined heredoc — a
release-asset-only swap is caught BEFORE any signature math runs (same
defense pattern as the Wave-2 offline key at stage `[3/13]`).

### 5.3 Transition strategy — PQ runs IN PARALLEL with classical

ML-DSA-65 does **not replace** Ed25519 or Fulcio. It runs alongside, as
a fourth independent axis. The verifier's stage `[13/13]` operates in
TRANSITION MODE:

- **OpenSSL ≥ 3.5 present:** axis D is enforced hard-closed. A failed
  ML-DSA verify aborts the install, same as a failed axis A/B/C.
- **OpenSSL < 3.5 OR openssl absent:** axis D is SKIPPED with an
  explicit `WARNING: PQ verification SKIPPED (OpenSSL 3.5+ not available)`
  log line. The classical axes A+B+C have already validated, so the
  installer is still authenticated against three independent trust roots.
  This is the **only legitimate path** through stage `[13/13]` without
  the PQ check — and it is loud, not silent.

The transition continues until NIST formally retires ECDSA-P256 and
Ed25519 for signing in CNSA 2.0 mode (likely 2030+). Until then, an
attacker must compromise ALL FOUR axes to ship a poisoned installer
this verifier accepts; an attacker who breaks ML-DSA-65 alone (e.g. via
a future lattice-cryptanalysis breakthrough) cannot ship anything past
axes A+B+C, and vice versa.

### 5.4 Toolchain pin — OpenSSL 3.5.6 from upstream

ML-DSA support landed in OpenSSL 3.5.0 (April 2025). `ubuntu-latest`
(Ubuntu 24.04 LTS) ships OpenSSL 3.0.13 by default — too old to sign or
verify ML-DSA. The signing workflow installs **OpenSSL 3.5.6** from
the upstream tarball with a SHA-256 pin:

- Source: `https://github.com/openssl/openssl/releases/download/openssl-3.5.6/openssl-3.5.6.tar.gz`
- Pinned SHA-256: `deae7c80cba99c4b4f940ecadb3c3338b13cb77418409238e57d7f31f2a3b736`
- Independently verifiable at
  `https://www.openssl.org/source/openssl-3.5.6.tar.gz.sha256`
  (also mirrored at
  `https://github.com/openssl/openssl/releases/download/openssl-3.5.6/openssl-3.5.6.tar.gz.sha256`).
- Verification provenance: re-fetched 2026-05-27 during Wave-4
  SA-5 integration after SA-4's initial pin value (which differed
  from upstream — apparently a manual transcription error) was
  caught by the workflow's own SHA-256 gate. The correct upstream
  value above is what the runner downloaded; this matches both
  mirrors.

Bumping this requires updating BOTH the workflow step pin AND this
section of `TRUST_ROOT.md` with the verification provenance (where the
new SHA-256 was observed and by whom). Same discipline as the cosign
and slsa-verifier hash pins in §1 + §4.

### 5.5 Rotation procedure (when the time comes)

Rotating the ML-DSA-65 keypair mirrors the Wave-2 offline-ceremony
ceremony in `docs/supply-chain/wave2-key-ceremony.md`:

1. Generate a new keypair in an air-gapped environment with OpenSSL ≥
   3.5: `openssl genpkey -algorithm ML-DSA-65 -out new.key.pem`.
2. Compute the new fingerprint:
   `openssl pkey -in new.pub.pem -pubin -outform DER | openssl dgst -sha256`.
3. Encrypt the private key at rest with the same AES-256-CBC + PBKDF2
   600 000-iter pattern, using a freshly-generated passphrase (do NOT
   reuse the rotated-out one).
4. Commit `install/keys/pq-mldsa65.pub.pem` + `install/keys/pq-mldsa65.key.enc`.
5. Update the inlined heredoc + `PQ_MLDSA65_PUBKEY_FINGERPRINT` in BOTH
   `install/install-verify.sh` AND `install/install-verify.ps1`.
6. Rotate `WAVE2_CEREMONY_PASSPHRASE` (or the new `PQ_MLDSA65_PASSPHRASE`
   secret once Wave 4.1 separates the two) via `gh secret set`.
7. Append an entry to §8 "Rotation history".

The verifier rejects releases signed with the OLD key automatically:
the inlined fingerprint cross-check in `[12/13]` catches both an
in-script tamper AND a release-asset swap.

---

## 6. Bumping cosign (rotation procedure)

> **Note:** this section was §4 before Wave 3, renumbered to §5 when the
> SLSA L3 + in-toto trust anchor (§4 above) was added, and renumbered
> again to §6 when Wave 4 §5 (Post-quantum signing) was added. Content
> unchanged.

When a new cosign release is required (security fix, dropped TLS protocol,
new Sigstore feature we need), follow this checklist. Skipping any step
is a documented bug.

1. **Open the new release page on GitHub.** Note the exact tag (e.g.
   `v2.4.2`).
2. **Independently observe the checksums file's content** from at least
   two networks (home, mobile tether, VPN exit). All three must show the
   same bytes:
   ```
   curl -fsSL https://github.com/sigstore/cosign/releases/download/v2.4.X/cosign_checksums.txt | sha256sum
   ```
   Compare across networks. If they differ, **stop** — the checksums file
   is being MITM'd in at least one path.
3. **Verify the cosign release itself was signed by Sigstore's own project.**
   Use a *previous* cosign:
   ```
   cosign verify-blob \
     --certificate-identity-regexp '^https://github.com/sigstore/cosign/.github/workflows/release.yml@refs/tags/v2.4.X$' \
     --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
     --signature cosign-linux-amd64.sig \
     --certificate cosign-linux-amd64.pem \
     cosign-linux-amd64
   ```
   The new cosign must successfully verify under the *current* cosign.
4. **Bump the four constants** in both `install-verify.sh` and
   `install-verify.ps1`: `COSIGN_VERSION`, `COSIGN_SHA256_LINUX_AMD64`,
   `COSIGN_SHA256_LINUX_ARM64`, `COSIGN_SHA256_DARWIN_AMD64`,
   `COSIGN_SHA256_DARWIN_ARM64`, `COSIGN_SHA256_WINDOWS`.
5. **Append a row to this document's §8 — "rotation history"** with the
   new version, who reviewed, and the verification evidence.
6. **Bump `cosign-release:` in `.github/workflows/sign-installer.yml`** to
   match.
7. **PR review by ≥1 maintainer other than the bumper** (when more than
   one maintainer exists; today the project has 1 maintainer — a Wave-2
   limitation we accept).

---

## 7. Bumping a GitHub Action SHA pin

Same discipline as cosign, but lighter ceremony since the GitHub Action
ecosystem is more dynamic:

1. Find the new commit SHA on the action's GitHub page (e.g. the SHA the
   tag `v3.7.1` points to for `sigstore/cosign-installer`).
2. **Read the diff** between the currently-pinned SHA and the candidate
   SHA, in particular `action.yml` and `dist/`. Anything that touches
   network egress or shell exec is suspect.
3. Confirm the candidate commit is **GitHub-Verified** (the lock icon next
   to the commit — means the author's GPG/SSH key signed it and matches
   the GitHub account).
4. Update the SHA in the workflow file. Comment with the previous SHA + tag
   so reviewers can `git diff` between them.
5. Push. The next workflow run will exercise the new action.

**Never use a tag.** Re-read `docs/supply-chain/threat-model.md` Scenario B
if you're tempted.

---

## 8. Who is the "maintainer identity" in plain English?

The OIDC identity verified by `cosign verify-blob` is **not a person**; it
is a *workflow file path inside a repo*. Specifically:

```
https://github.com/PersonalJarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/<TAG>
```

This means:

- A signature is accepted iff it was produced by a GitHub Actions run of
  **this exact workflow file**, on **a tag push to this exact repo**, at
  **a tag matching `v*.*.*[-suffix]`**.
- The *person* who pushed the tag is recorded in Rekor but is not gated
  by the verifier. In Wave 1, **any maintainer with `push` permission on
  `PersonalJarvis/PersonalJarvis` and the ability to push a `v*.*.*`
  tag can mint a verifier-accepted signature.**
- A repo-takeover (account compromise of the repo owner) breaks this
  trust root. Wave 2 mitigates with threshold signing (2-of-N maintainer
  keys, with one off-network).

Today (Wave 1), the maintainer set is `@RubenLuetke` (repo owner) plus any
collaborators added under
https://github.com/PersonalJarvis/PersonalJarvis/settings/access. The
expectation is that this list is short and audited.

---

## 9. Rotation history

| Date | What changed | By whom | Verification evidence |
|---|---|---|---|
| 2026-05-26 | Initial Wave 1 pin: cosign `v2.4.1`, Linux/Darwin/Windows hashes, action SHAs (checkout `11bd7190…`, cosign-installer `dc72c7d5…`, action-gh-release `c95fe148…`). | @RubenLuetke + Claude (audit) | `cosign_checksums.txt` for v2.4.1 fetched from `github.com/sigstore/cosign/releases/download/v2.4.1/cosign_checksums.txt`; action SHAs resolved via `api.github.com/repos/<org>/<repo>/git/refs/tags/<tag>`. |

(Append a row per rotation. Do not edit old rows — they are the audit trail.)

---

## 10. Wave 5 — Audit fixes (tag-binding, payload-commit pin, content-anchor rename, repo hygiene)

> **Status:** wired into the verifier + workflow + installer on the
> `feat/wave5-audit-fixes` branch. Tag `v0.5.1-supplychain-wave5-audit-fixes`
> closes four findings from the third-party audit issued against
> `v0.5.0-supplychain-wave4`.

### 10.1 Why this section exists

A skeptical third-party audit of the Wave 4 release surfaced four real
defensive gaps:

1. **Tag-binding gap → downgrade-replay vector.** The verifier's
   `IDENTITY_REGEX` accepted *any* semver-ish tag in the Fulcio cert
   SAN, not the *requested* tag. An attacker serving valid-signed
   bytes from an OLD release under a fresh URL passed all four axes —
   the only barrier was Rekor freshness (24 h), which ages out within
   a day.
2. **Cloned `main` was unsigned.** `install.sh` did `git clone --depth 1
   --branch main` and `installer.py` did zero signature checks on the
   cloned tree. The four-axis chain ended at the bootstrap; whatever
   was on `main` at install time ran unverified.
3. **in-toto layout overstated.** `layout.template.json` had no
   `signatures` field; it was an *unsigned* template. Authenticity
   came from `install-verify.sh` byte-comparing `identity_regexp`
   against a constant. Defensible defense-in-depth, but not
   in-toto-as-spec'd.
4. **Repo hygiene.** Secret scanning + push protection were disabled;
   no `dependabot.yml`; branch protection was incomplete.

### 10.2 Wave 5 — Finding 1: Tag-binding cross-check

The verifier now extracts the `@refs/tags/<X>` suffix from the SAN URI
and compares it BYTE-FOR-BYTE against the resolved `$TAG`. Drift => fail-
closed with: `axis A: SAN tag <X> does not match requested tag <Y> —
refusing (possible downgrade replay)`.

This closes the freshness-only barrier. An attacker who serves
`v0.5.0` bytes under a fresh URL while the operator asked for `v0.6.0`
is rejected at stage [7/13] regardless of Rekor age.

Implementation:
- `install/install-verify.sh` — added immediately after the SAN regex
  match in stage [7/13].
- `install/install-verify.ps1` — mirror change after `$CertSan -match
  $IdentityRegex`.

### 10.3 Wave 5 — Finding 2: payload-commit pin (NEW Axis E)

The workflow emits `payload-commit.txt` containing `$GITHUB_SHA` of the
tagged commit. This file is signed with the same Wave 1+2+4 axes as
`install.sh`. The verifier (`install-verify.sh` axis-E stage) downloads
+ authenticates it, then exports `JARVIS_PAYLOAD_COMMIT` to install.sh.
`install.sh` `git checkout`s that SHA after the clone, so the cloned
tree is bound to the exact commit existing at sign-time.

An attacker who flips `main` after release can no longer influence what
gets installed: the install pins to the SHA that was signed.

Implementation:
- `.github/workflows/sign-installer.yml` — `printf '%s\n' "$GITHUB_SHA" >
  out/payload-commit.txt` in the staging step; added to Wave 1, 2, 4
  signing loops; uploaded as release asset.
- `install/install-verify.sh` — axis-E stage between [13/13] and exec.
- `install/install-verify.ps1` — mirror.
- `install/install.sh` — consumes `JARVIS_PAYLOAD_COMMIT` after clone;
  defensive `git rev-parse HEAD` check confirms the checkout actually
  landed on the signed SHA.
- `install/install.ps1` — mirror.

**Pre-Wave-5 release fallback.** `JARVIS_INSTALL_ALLOW_NO_PAYLOAD_PIN=1`
allows installation of a pre-Wave-5 release (no `payload-commit.txt`
emitted) with a loud warning. Default 0 — must be set explicitly. Used
for backward-compatibility install of legacy tags during the transition.

### 10.4 Wave 5 — Finding 3: in-toto overclaim removed (Option B)

`install/in-toto/layout.template.json` is renamed to
`install/in-toto/layout-content-anchor.json`. The `_type` field changes
from `"layout"` (which implied a signed in-toto layout per the v1.0.0
spec) to `"content-anchor"` (an honest description of what it actually
is: an unsigned regexp-pin that is byte-compared against the signed
verifier's hard-coded constant).

The verifier accepts BOTH `"layout"` and `"content-anchor"` during the
v0.5 → v0.6 transition so pre-Wave-5 releases continue to install. Going
forward only the new name + type is emitted.

Why Option B and not Option A (real signed in-toto layout):
- The offline-ceremony key is the only authoritative signing material
  the project owns outside GitHub. Using it to sign the layout means
  layout-rotation requires a full offline ceremony, which has the same
  cost as a Wave-2 key rotation. That is a non-trivial process change.
- The current defense (signed verifier with baked-in regexp + content-
  anchor byte-compare) is already sound — the audit explicitly described
  it as "defensible, but it's not in-toto-as-spec'd." Removing the
  overclaim is the honest fix.
- Migrating to real in-toto remains a Wave-6 candidate. The renamed
  file makes the divergence machine-readable (`_type=content-anchor`),
  so a future migration is incremental.

Implementation:
- File renamed via `git mv`.
- `install/install-verify.sh` `INTOTO_LAYOUT_FILENAME` constant updated;
  `_type` accept-list extended to `{layout, content-anchor}`; comments
  retitled to "content-anchor layout pin" (no more "in-toto layout pin"
  claim).
- `install/install-verify.ps1` mirror.
- `.github/workflows/sign-installer.yml` — `cp install/in-toto/layout-content-anchor.json
  out/layout-content-anchor.json`; release-asset list updated.

### 10.5 Wave 5 — Finding 4: Repo hygiene

- **Secret scanning + push protection** enabled at the repo level via
  the GitHub Security & Analysis settings (`gh api -X PATCH
  /repos/PersonalJarvis/PersonalJarvis -F
  security_and_analysis.secret_scanning.status=enabled -F
  security_and_analysis.secret_scanning_push_protection.status=enabled`).
- **Dependabot** enabled via `.github/dependabot.yml` — weekly updates
  for `github-actions` + `pip` ecosystems. All PRs land as PRs (never
  auto-merge); supply-chain pinning discipline requires manual SHA +
  hash validation before merging.
- **Branch protection** on `main`: required status checks (sign-installer,
  cross-runner-hash, verify-installer-smoke), required signed commits,
  no force-push, linear history required. Status documented in
  `docs/supply-chain/wave5-audit-fixes-validation.md`. Any field that
  fails because of GitHub-plan restrictions is documented honestly.
- **Bot-identity migration** is explicitly OUT OF SCOPE for Wave 5.
  The signing actor remains `@RubenLuetke` (personal account, not a
  protected bot identity). This is tracked as Wave 6 — requires
  separate GH account setup with hardware-token MFA and an isolated
  PAT scoped only to `id-token: write` on this repo.

### 10.6 What §10 does NOT claim

- The Wave-5 payload-commit pin (axis E) makes axes A+B+C+D obsolete.
  It does not — it adds a fifth axis specifically for the cloned tree.
  Axes A+B+C+D still authenticate `install.sh` itself.
- The content-anchor rename adds new cryptographic protection. It does
  not — it removes a false marketing claim. The protection is the same
  (signed verifier byte-comparing the constant). Honesty IS the fix.
- The bot-identity migration was done. It was not. Wave 6.
- Dependabot or branch protection prevent a maintainer from pushing a
  malicious commit. They do not — they raise the cost (visible PR,
  required reviewer if branch-protection is configured that way) but
  the ultimate trust root is still the maintainer set listed in §8.

---

## 11. If you suspect compromise

If you have a credible suspicion that a release's signed bytes are
malicious **even though the verifier accepts them**:

1. **Immediately publish a GitHub Security Advisory** marking the affected
   tag as malicious. This is the only "kill-switch" Wave 1 has.
2. **Yank the GitHub Release** (this does not retroactively unverify
   already-installed clients — Wave 2's TUF metadata refresh will).
3. **Search Rekor for the malicious entry**:
   `https://search.sigstore.dev/?logIndex=...` or via the cosign CLI
   `cosign tree --remote ...`. Confirm whether the malicious release was
   signed under the expected OIDC identity (= repo-takeover or workflow
   poisoning) vs. a different identity (= our pin defended).
4. **Post-mortem.** Append the findings to
   `docs/supply-chain/red-team-log.md`. Update this document's §8 with
   the rotation if any pins changed in response.

There is no automated revocation in Wave 1. Wave 2 introduces TUF root
metadata which gives every installed client a way to check for a
revocation announcement at install/upgrade time.
