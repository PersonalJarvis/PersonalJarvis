# Threat Model — `PersonalJarvis/PersonalJarvis` Quick-Install One-Liner

> Status: **Wave 1 of 4** (Sigstore keyless + Rekor freshness + pinned cosign +
> pinned action SHAs). Waves 2-4 are scoped at the bottom of this document but
> intentionally **not** delivered yet — promising more than is shipped is itself
> a supply-chain anti-pattern.
>
> Last reviewed: 2026-05-26 against the `PersonalJarvis/PersonalJarvis` repo
> at `main` HEAD `e7cdefeca7e44ddf18ebd17a1e646d4471cf7a1e` (Wave 1 baseline).

---

## 1. Scope of this document

The artifact under threat is the **one-liner installation flow**:

```bash
curl -fsSL https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.sh | bash
```

and its PowerShell sibling. This document enumerates every trust root a fresh
user implicitly accepts when running that command on a brand-new Linux VPS or
Windows workstation, lists the realistic attacks against each root, and shows
what Wave 1 changes (and what it does not).

The post-install runtime threat model — Brain provider API keys, Computer-Use
risk-tier policy, Mission-Manager worker isolation — is out of scope here.
That work lives in `docs/jarvis-agents-bridge.md`, `jarvis/safety/risk_tier.py`,
and the Phase-6 isolation invariants in `CLAUDE.md`.

---

## 2. Trust roots implicitly granted by the legacy one-liner

The user typing `curl ... | bash` is granting these trust roots **without
asking** and **without any verification step in between**:

| # | Trust root | What an attacker who owns it can do | Probability today |
|---|---|---|---|
| **T1** | The user's **resolver / DNS path** (their ISP, captive portal, VPN, public Wi-Fi) | Redirect `raw.githubusercontent.com` to an attacker-controlled mirror. | Low for residential ISP, **moderate** for cafe Wi-Fi and authoritarian-network users — and this is the *exact* demographic Personal Jarvis's "cloud-first on a €5 VPS" doctrine targets. |
| **T2** | The **public TLS CA pool** baked into the user's OS / curl build | Issue a fraudulent leaf cert for `raw.githubusercontent.com`. Recent precedents: TrustCor (Mozilla distrust, 2022), Camerfirma (browser distrust, 2021), DigiNotar (compromised, 2011). | Low per CA; the union over ~150 CAs is non-negligible over a 5-year window. |
| **T3** | **GitHub.com itself** (origin server) | Serve a backdoored `install.sh` to a single user via cache poisoning, A/B-test rollout, or an internal-platform bug. Precedent: 2018 npm `event-stream`, 2024 `tj-actions/changed-files` token exfiltration on Mar 14 2025. | Low per request, but every other root collapses if GitHub is compromised. |
| **T4** | `raw.githubusercontent.com` **specifically** (the asset CDN) | Same as T3 but with a different blast radius — `raw.` serves the *current `main` HEAD* with no version pinning at all. Anyone with `contents:write` on `main` at the moment of `curl` decides what the user runs. | Direct push to `main` is gated by branch protection if enabled; without it, every collaborator is a single-factor attacker. |
| **T5** | **Every maintainer with `push` on `main`** | Force-push, merge a poisoned PR, or trip a self-hosted GitHub Actions runner into committing a `main`-bound change. Precedent: 2024 xz-utils — *one* maintainer-of-trust (`jiat75` / Jia Tan) over two years. | Personal Jarvis has a tiny maintainer set today (1), but the attack model is patient. |
| **T6** | GitHub Actions `GITHUB_TOKEN` minted for every workflow run | Push, tag, create releases, comment on PRs — depending on workflow `permissions:` block. Precedent: `tj-actions/changed-files` (March 2025) — a single compromised reusable Action propagated to 23k+ repos and was used to exfiltrate `GITHUB_TOKEN` from CI logs. | Real and recent. Mitigated only if every workflow file pins **actions by commit SHA**, not by tag. |
| **T7** | **Every third-party GitHub Action** invoked from this repo's workflows (`actions/checkout`, `sigstore/cosign-installer`, `softprops/action-gh-release`, …) | An attacker who repoints `actions/checkout@v4` (tag, mutable) to a malicious commit gets RCE in every CI run that consumes the tag — including the run that produces the signed installer. | Real, ongoing. Tag-pinning is the industry default and is **wrong**. |
| **T8** | **`pip` + PyPI + every transitive dependency** the installer pulls (`rich`, `packaging`, then `jarvis` itself plus its deps) | Dependency confusion (T8a), typo-squatting (T8b), source upload of a malicious release of an existing package (T8c — `colorama` 2022, `ctx` 2022, `PyTorch nightly` 2022). | High over time. **Wave 1 does not address this** — see Section 5 residual gap. |
| **T9** | The user's **shell** and the implicit `bash` interpreter trust | `curl | bash` cannot be sandboxed: shell expansion, function definitions, and `trap` handlers fire before the user can review them. There is no opportunity to inspect bytes before execution. | This is the structural anti-pattern. Wave 1 does **not** eliminate it but raises the cost. |
| **T10** | **The cosign / Sigstore trust chain itself** (Fulcio CA roots, Rekor public key, TUF root for `sigstore-go`) | If the Sigstore root keys are compromised, Wave 1's signatures verify against attacker-controlled roots. Sigstore's own root is rotated; the latest TUF root ceremony was held in November 2024. | Low (Sigstore is operated by a multi-org steering committee with hardware key ceremonies), but it *is* now a trust root we depend on. **The document `install/TRUST_ROOT.md` enumerates this explicitly.** |
| **T11** | The user's **`/tmp`** (or `$env:TEMP`) directory writable by another local process | TOCTOU between the verifier writing cosign to `/tmp/cosign-${RANDOM}` and a co-tenant process swapping it. | Low; we use `mktemp -d` + per-invocation random suffix and `umask 077`. |

---

## 3. Attack scenarios (real-incident-anchored)

Each scenario follows: **pre-condition → action → blast radius → pre-Wave-1 mitigation → Wave-1 mitigation → residual gap.**

### Scenario A — DNS / TLS interception against `raw.githubusercontent.com` (T1, T2)
- **Pre-condition.** User on hostile Wi-Fi (airport, café, dorm), public-school network with TLS-MITM proxy, or under a national-level interception order.
- **Action.** Attacker re-issues a cert for `raw.githubusercontent.com` (or coerces a CA), serves a doctored `install.sh` that adds an `ssh` reverse tunnel before running the legitimate stage-2.
- **Blast radius.** Full code execution on every machine that runs the one-liner on that network. Persistent backdoor — installer creates `~/.personal-jarvis/`, attacker drops `crontab -e` lines or `systemd --user` services with full HOME write access.
- **Pre-Wave-1 mitigation.** **None.** TLS authenticates the host, not the content; the response body is fully attacker-controlled if the cert chain is forged.
- **Wave-1 mitigation.** New one-liner uses `install-verify.sh`, which (a) downloads from the **release URL** (still TLS, but the signed artifact is the actual root of trust, not the transport), (b) downloads cosign **with a pinned SHA-256** so an MITM swap is detected, (c) calls `cosign verify-blob` with a **pinned OIDC issuer** and **pinned certificate-identity regexp** so the swapped binary doesn't verify even if it has *some* valid Sigstore signature, (d) asserts **Rekor inclusion proof freshness ≤ 24 h** so a long-dormant once-valid signature can't be replayed.
- **Residual gap.** The *one-liner wrapper file itself* is still fetched over TLS without a signature — by definition, you can't sign the thing the user fetches to bootstrap signature verification. **This is the irreducible bootstrap-trust problem.** Wave 2 mitigates with a published Homebrew tap / Scoop bucket whose package managers have their own signature stack.

### Scenario B — `tj-actions/changed-files`-style Action-pinning compromise (T7)
- **Pre-condition.** Personal Jarvis pins a GitHub Action by tag (`uses: sigstore/cosign-installer@v3`). Attacker compromises that repo or the maintainer's account and force-moves the `v3` tag to a malicious commit, OR ships a versioned release whose entrypoint is malicious.
- **Action.** Next workflow run pulls the new commit. Malicious entrypoint exfiltrates `GITHUB_TOKEN`, mints a new release with attacker-crafted `install.sh` + a cosign signature *that legitimately came from our OIDC identity* (because the malicious step runs *inside our trusted workflow*).
- **Blast radius.** Catastrophic. The cosign signature is genuine — `cosign verify-blob` succeeds; the installer-verifier wrapper accepts and runs the malicious payload.
- **Pre-Wave-1 mitigation.** **None.** No workflow ever signed any artifact.
- **Wave-1 mitigation.** **Every Action in `.github/workflows/sign-installer.yml` and `verify-installer-smoke.yml` is pinned by commit SHA, not by tag.** Maintainer must consciously bump SHAs and accept that the new commit's source has been audited. This is the *direct* lesson from the March 2025 `tj-actions/changed-files` incident (CVE-2025-30066).
- **Residual gap.** SHA pinning prevents *future* tag movement but not malicious commits *at the time of initial pinning*. Wave 2 mitigation: subscribe to `dependabot` + manual review of pinned-SHA bumps + comparison against the GitHub Verified-by-author signature on each commit.

### Scenario C — xz-utils-style long-game maintainer compromise (T5)
- **Pre-condition.** A would-be maintainer cultivates trust for 18-24 months, opens benign PRs, gets `write` access, then ships a single malicious commit shortly before a release. Lesson: xz-utils (CVE-2024-3094, Mar 2024). Lasse Collin spent two years trusting Jia Tan.
- **Action.** Malicious commit lands in `install.sh`; release workflow signs it with the legitimate OIDC identity; users install the backdoored binary.
- **Blast radius.** Every user who installs during the window between commit and discovery. xz-utils was caught within weeks only because of an unrelated valgrind error noticed by an alert Postgres developer.
- **Pre-Wave-1 mitigation.** None.
- **Wave-1 mitigation.** **Partial** — cosign signing creates a *non-repudiable* audit log in Rekor (public, append-only). Forensic recovery after disclosure is now possible: the date and identity of every signed release is independently auditable. Detection still depends on the community.
- **Residual gap.** **Single-maintainer signing remains a single point of failure.** Wave 2: threshold signing (2-of-N maintainer keys, with one offline witness key held off-network). Wave 3: rebuilder farm + reproducible builds so an external party can independently verify that the signed bytes match the source commit. This is exactly what Debian's reproducible-builds project, GUAC, and in-toto are designed for.

### Scenario D — Dependency confusion against `personal-jarvis` itself (T8)
- **Pre-condition.** `install/installer.py` runs `pip install -e ".[desktop]"` against PyPI with no index pinning. An attacker registers `personal-jarvis` on PyPI (the GitHub repo is `PersonalJarvis/PersonalJarvis` — the *PyPI* slot is currently unclaimed, an active dependency-confusion vector). The legitimate package is currently installed from the local clone — so PyPI's package would only matter for someone running `pip install personal-jarvis` manually, but **`pip install -e .` resolves transitive dependencies from PyPI** and any of those slots being claimed is a vector.
- **Action.** A typosquatted `personnal-jarvis` package or a confusion-attack against a not-pinned dep (e.g. an internal package name leaking into pyproject.toml) ships malware on first `pip install`.
- **Blast radius.** Full Python interpreter access, persistent via `pyproject.toml` entry-points.
- **Pre-Wave-1 mitigation.** None.
- **Wave-1 mitigation.** **None — explicit Wave-1 non-goal.** See Section 5 "out of scope" #4 and Wave 2 roadmap.
- **Residual gap.** Entire transitive Python dependency surface. Wave 2: claim the PyPI namespace, publish a signed `pip-audit` lockfile (`requirements.lock`) hash-pinned, ship the lock with the release.

### Scenario E — Self-hosted runner compromise (T6 + T7)
- **Pre-condition.** A future contributor adds a self-hosted GitHub Actions runner for cost reasons. That runner shares filesystem with another job.
- **Action.** Malicious PR triggers a workflow that pollutes `~/.cache/cosign` or `/tmp/cosign-*` on the runner, swapping the cosign binary the *next* run downloads. The download SHA-256 still matches (the legitimate Sigstore upstream), but a co-located process intercepts cosign at exec time.
- **Blast radius.** Full release-signing compromise.
- **Pre-Wave-1 mitigation.** None — and Personal Jarvis has no self-hosted runner yet, which is *why* this isn't a current threat.
- **Wave-1 mitigation.** Workflow runs on `runs-on: ubuntu-latest` (GitHub-hosted, ephemeral VM, fresh per job). Self-hosted runners are explicitly forbidden until a Wave-3-grade isolation review (rebuilder farm, network egress block, attestation).
- **Residual gap.** None as long as the constraint holds. Codify the constraint in `docs/supply-chain/TRUST_ROOT.md`.

---

## 4. What Wave 1 actually buys you (in plain words)

Before Wave 1 the chain of trust looks like this:

```
USER  →  TLS  →  github.com  →  every-maintainer-with-write  →  RUN
```

A single compromise anywhere on that chain (DNS, CA, GitHub, Action tag, any
maintainer's laptop) drops an arbitrary `install.sh` body straight into the
user's `bash`. There is no after-the-fact way to tell what the user actually
ran.

After Wave 1:

```
USER  →  TLS  →  release URL  →  cosign verify-blob  →  Rekor freshness  →  RUN
                                       ↑                       ↑
                                       │                       │
                                       │              Public append-only log:
                                       │              every signed installer is
                                       │              publicly auditable, with
                                       │              cryptographic proof the
                                       │              signature existed at time T
                                       │
                              OIDC issuer pinned to GitHub Actions,
                              identity regexp pinned to this repo's
                              tag-push workflow path. An attacker can
                              still tamper with the served bytes, but
                              the verifier refuses anything not bearing
                              a Fulcio cert matching exactly this repo.
```

**Concretely Wave 1 stops:**
- Scenario A (in transit MITM) — verifier refuses, no execution.
- Scenario B (tag-moved compromised Action) — workflow pins SHAs, attacker
  must compromise the specific commit, not just rename a tag.
- Scenario E (self-hosted runner) — by policy not by code.

**Concretely Wave 1 only logs (does not prevent):**
- Scenario C (maintainer compromise) — Rekor records the malicious release
  alongside the legitimate ones. Detection is post-hoc; forensic recovery
  becomes possible.

**Concretely Wave 1 does nothing about:**
- Scenario D (dependency confusion) — pip resolves transitive deps with no
  signature check. Documented residual gap.

---

## 5. Out of scope for Wave 1 (explicit, with reasoning)

1. **TUF root metadata for the Personal Jarvis project itself.** Sigstore's
   TUF root is consumed transitively; we do not yet publish our own. TUF
   buys *key rotation safety* — without it, if our OIDC identity is ever
   compromised, every user who already verified an old install has no way
   to learn that a key revocation happened. Cost: 8-12 h, requires a
   separate metadata-publishing workflow. **Wave 2.**
2. **Threshold signing (2-of-N maintainer keys).** Today the workflow signs
   under a single GitHub OIDC identity. A repo-takeover of the GitHub org
   produces signatures that verify cleanly. Cost: 12-20 h (Sigstore's
   `cosign sign-blob --key cosign.key` plus a Threshold-cosigning ceremony
   or `sigstore-python`'s `--with-witness` flag once it stabilises). **Wave 2.**
3. **Reproducible bit-for-bit builds.** `install.sh` is already byte-stable
   (pure shell), so the install scripts themselves *are* trivially
   reproducible. The Python wheels they ultimately install (`rich`,
   `packaging`, `jarvis`) are **not** built in this workflow and therefore
   not bit-reproducible by us. Cost: 20-40 h, requires a rebuilder farm.
   **Wave 3.**
4. **Dependency-confusion / typosquat defense for the Python deps.** No
   PyPI claim, no hash-pinned lockfile shipped with the release, no
   `pip-audit` enforcement at install time. Cost: 4-8 h. **Wave 2** (the
   PyPI claim alone is a 1-h job and *should* be done immediately, but
   it's outside the cryptographic-verification scope of Wave 1).
5. **In-toto layout proving installed bytes match source commit.** Wave 3.
6. **Post-quantum signature migration.** Sigstore is tracking ML-DSA;
   nothing to do at the consumer layer for ~2-3 years. **Wave 4.**
7. **Offline revocation / kill-switch.** No way today to tell a user
   "do not run vX.Y.Z, it's malicious" except via out-of-band channels
   (GitHub Security Advisory, README banner). TUF metadata refresh is
   the right place; comes in Wave 2.
8. **Detection of cosign binary tampering post-download.** We verify the
   SHA-256 of the downloaded cosign binary against a hard-coded value in
   the verifier script. We do **not** check Sigstore's own keyless
   signature on cosign itself (cosign is signed by the Sigstore project).
   The hard-coded SHA-256 *is* the chain anchor — see
   `install/TRUST_ROOT.md` for the threat-model argument why a hash is
   sufficient at the bootstrap layer. **Documented, not extended in Wave 1.**

---

## 6. Wave 2-4 roadmap (sized honestly)

| Wave | Scope | Effort | What it stops |
|---|---|---|---|
| **Wave 2** — Key hardening | (a) Claim PyPI `personal-jarvis` slot. (b) Publish hash-pinned `requirements.lock` with each release. (c) Add a TUF metadata publishing workflow so key rotation can be announced without breaking already-installed users. (d) Add threshold signing: 2 maintainer keys + 1 offline witness key stored on a hardware token, all required to mint a `vX.Y.Z` tag's signature. (e) Add a `cosign verify-attestation` step for SLSA L2 provenance ("this binary was produced by this workflow at this commit"). | **40-60 h** | Scenario D (dep-confusion), partial Scenario C (single-maintainer compromise). |
| **Wave 3** — Reproducibility + in-toto | (a) Rebuild farm (2 independent rebuilders, ideally one outside GitHub — e.g. GitLab CI mirror or a self-hosted runner with cross-attestation). (b) `in-toto` layout file describing the steps source → wheel → installer → signed artifact. (c) `in-toto verify` step in `install-verify.sh` post-cosign-verify. (d) Bit-for-bit reproducibility for `jarvis` Python wheels (means pinning `SOURCE_DATE_EPOCH` and freezing the builder OS). | **60-100 h** | Full Scenario C (maintainer compromise via single-machine signing). |
| **Wave 4** — Long-tail | (a) Post-quantum signature migration when Sigstore ships ML-DSA support. (b) Hardware-key signing ceremony for maintainer onboarding. (c) Reproducible-bootstrap of the verifier itself (today the verifier script is fetched over TLS without a signature — Wave 4 ships a Homebrew tap + Scoop bucket + apt repo so the verifier itself is signed by an upstream package manager). | **40-80 h** | Scenario A's residual bootstrap gap. |

Total Wave 2-4 effort: **140-240 hours** of focused engineering, depending on
how much of the reproducibility work is genuinely needed (Personal Jarvis is
not a binary distribution — the install scripts themselves are shell, so the
"reproducibility" target is mostly the Python wheel layer).

---

## 7. How to audit this document

Every claim here is verifiable:

- **Trust root inventory (Section 2):** `git log -- install/install.sh` shows
  every commit that ever touched the legacy one-liner; every push to `main`
  by every collaborator since the file was created.
- **CVE references (Section 3):** xz-utils CVE-2024-3094, `tj-actions/changed-files`
  CVE-2025-30066, `event-stream` (no CVE, see Snyk advisory SNYK-JS-EVENTSTREAM-72638).
- **Cosign SHA-256 pin (TRUST_ROOT.md):** independently verifiable against
  `https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign_checksums.txt`.
- **Action SHA pins (.github/workflows/sign-installer.yml):** the pinned SHAs
  resolve via `git ls-remote https://github.com/<repo> <SHA>` and can be
  cross-checked against the repo's tag page.
- **Rekor inclusion proofs:** every signature minted by the workflow is
  searchable on `https://search.sigstore.dev` by repo name. The
  red-team-log.md document demonstrates this.

If any line in this file becomes false, file a PR. The threat model is
treated as code, not as marketing.

---

## 7. Wave 2 — multi-axis signing (FOUNDATION COMPLETE, full workflow integration pending in follow-up sub-agents)

> Status: foundation step landed on branch `feat/wave2-foundation`. The
> keypair, encrypted private key, TUF root metadata with `threshold=2`,
> and the ceremony documentation are committed. The workflow change that
> actually mints the offline-ceremony signature on each release, and the
> verifier change that demands 2-of-2 at install time, are scoped for
> follow-up sub-agents Wave-2-SA-2 (verifier), Wave-2-SA-3 (workflow),
> Wave-2-SA-4 (tests + red-team), Wave-2-SA-5 (integration PR).
>
> Companion documents:
> - `install/TRUST_ROOT.md` §3 — user-facing trust roots + passphrase
>   disclosure (the demo passphrase is committed in §3.3 with full
>   honesty about the production migration path).
> - `docs/supply-chain/wave2-key-ceremony.md` — exhaustive ceremony log
>   + verifier contract + recovery procedure.

### 7.1 The single-axis gap that Wave 2 closes

Wave 1's verifier accepts a signature iff `cosign verify-blob` succeeds
against a single OIDC identity (the `sign-installer.yml` workflow on
`PersonalJarvis/PersonalJarvis`). That is **one trust axis.** If an
attacker compromises the maintainer's GitHub account, or pushes a
poisoned `sign-installer.yml` that survives review, or successfully runs
a `tj-actions/changed-files`-class Action-pinning attack on this repo,
the resulting signature verifies cleanly and the malicious bytes are
executed on user machines.

The xz-utils incident (CVE-2024-3094) proved that a patient attacker
willing to invest 18-24 months building maintainer trust will eventually
defeat any single-identity gate. Wave 2's response is to require **a
second, independent signing axis** — an offline-ceremony Ed25519
keypair whose private half lives outside GitHub (in the production
posture; see §7.4 for the demo posture honest-disclosure).

### 7.2 Wave-1 scenarios revisited under Wave 2

Re-walking the scenarios from §3 with Wave 2 active:

#### Scenario A — DNS / TLS interception (T1, T2)
- **Wave 1 mitigation:** Sigstore signature; pinned OIDC + identity regex;
  pinned cosign SHA-256; Rekor freshness check ≤ 24 h. **Wave 2
  mitigation:** unchanged for the transport layer (MITM still fails at
  the Wave-1 cosign step), AND **additionally** even if an attacker
  somehow defeats cosign (e.g. via a hypothetical Sigstore root-key
  compromise — see scenario for T10 below), the offline-ceremony Ed25519
  signature must still validate against a public key the user already
  has via `1.root.json`. **Now requires defeating both axes
  independently.**

#### Scenario B — `tj-actions/changed-files`-style Action-pinning compromise (T7)
- **Wave 1 mitigation:** SHA-pinning every Action; the cosign signature
  is still legitimate, so cosign accepts. **Wave 2 mitigation:** if the
  attacker's malicious workflow step *can* mint the cosign signature
  (the workflow's OIDC identity), it **cannot** mint the offline-
  ceremony signature — the offline private key is stored outside the
  runner (production posture) or its passphrase is a separate secret
  (transitional posture). The verifier refuses.
- **Residual gap closed:** the case where the attacker compromises both
  the Action and the GitHub Actions secret store simultaneously is the
  remaining residual. That is Wave 3 scope (HSM-backed signing outside
  GitHub Actions entirely).

#### Scenario C — xz-utils-style long-game maintainer compromise (T5)
- **Wave 1 mitigation:** partial — Rekor log records the malicious
  release, forensic recovery is possible after disclosure. **Wave 2
  mitigation:** if the offline-ceremony key is held by a *different*
  maintainer (or the same maintainer's separately-secured laptop, with
  the passphrase in a hardware token), the xz-utils attacker — who
  cultivated trust *only* over the GitHub account — cannot mint the
  second signature. **The xz-utils attack as historically executed
  fails against a Wave-2 verifier.**
- **Important caveat:** this argument assumes the offline-ceremony key
  is held by a separate trustee or in genuinely separate custody. If
  the same individual holds both halves on the same laptop, both axes
  fall to the same physical-access compromise. **The demo posture in
  this branch is exactly this same-laptop scenario** — the production
  migration path in `wave2-key-ceremony.md` §4 is the operational
  defense against it.

#### Scenario D — Dependency confusion against `personal-jarvis` itself (T8)
- **Wave 1 mitigation:** none — explicit non-goal. **Wave 2
  mitigation:** still none — Wave 2 is the *signing* axis hardening;
  the dependency-confusion fix is a separate Wave 2 deliverable
  (PyPI namespace claim + hash-pinned `requirements.lock`) tracked
  under a different Wave 2 sub-agent. Calling that out here so this
  document remains honest: **Wave 2 SA-1 (this sub-agent) does NOT
  close Scenario D.**

#### Scenario E — Self-hosted runner compromise (T6 + T7)
- **Wave 1 mitigation:** policy (no self-hosted runners). **Wave 2
  mitigation:** unchanged. Wave 2 narrows the impact (a compromised
  runner cannot forge the offline-ceremony signature) but does not
  fundamentally change the recommendation. **Wave 3** introduces
  rebuilder-farm cross-attestation, at which point self-hosted runners
  become safer to consider.

### 7.3 What Wave 2 does NOT solve (explicit)

1. **Build-server compromise of both axes simultaneously.** If a single
   attacker controls both the GitHub Actions runtime AND the offline-
   ceremony custody chain (e.g. same individual is taken over by the
   same intrusion), Wave 2 falls. Defense: separate custody. Recourse
   if separation is not yet operational: Wave 3 rebuilder farm
   cross-attestation, so a third independent party can detect the
   discrepancy between source commit and signed binary.
2. **Bootstrap-TLS attack on the one-liner wrapper.** The user still
   fetches `install-verify.sh` (or `.ps1`) over TLS from GitHub
   without a signature on the wrapper itself. An attacker who can
   serve a doctored wrapper script bypasses both signature axes
   because the wrapper is what *invokes* the verifier. Wave 4 ships
   the wrapper via signed package managers (Homebrew tap, Scoop
   bucket, apt repo).
3. **Post-quantum signature migration.** Both Sigstore (Fulcio's
   ECDSA-P256) and the offline-ceremony key (Ed25519) are
   classically-secure but vulnerable to a sufficiently large quantum
   computer (Shor's algorithm). Sigstore's ML-DSA migration is
   tracked; the offline-ceremony key can be replaced with Ed448 +
   ML-DSA-65 dual-signing at Wave 4. Out of scope here.
4. **Recovery from loss of the offline-ceremony private key.** Wave 2
   has no automated recovery; loss of the key + passphrase means the
   maintainer cannot mint new releases until a fresh TUF root version
   is bootstrapped to every installed client. This bootstrap problem
   is itself Wave 3 scope (signed TUF refresh metadata channel). The
   recovery procedure is documented in `wave2-key-ceremony.md` §5.
5. **In-toto / SLSA L3 / reproducible builds.** Wave 2 proves *who*
   signed; it does not prove *what they signed matches the source
   commit*. Wave 3 ships in-toto layout + cross-attesting rebuilders.

### 7.4 The demo-posture honest disclosure

The committed branch includes the **decryption passphrase** for the
offline-ceremony private key, in `install/TRUST_ROOT.md` §3.3, in plain
text. A reader of this public repository can extract the private key.

This is **intentional** for the foundation step:

- The Wave 2 *architectural* claim is that the verifier machinery
  (TUF root with threshold=2, dual signature paths, separate-axis
  pin) is in place and exercised end-to-end. That claim is fully
  delivered by this branch regardless of where the passphrase lives.
- The Wave 2 *operational* claim — that the production deployment
  derives the second axis's key material in a genuinely-air-gapped
  ceremony with hardware-token-backed custody — is **not yet** made
  by this branch. The migration path is documented in
  `wave2-key-ceremony.md` §4 and is part of Wave-2-SA-3's brief.
- Honesty bar: **`install/TRUST_ROOT.md` §3.3 must contain the literal
  passphrase line so any reader can verify both halves are present.**
  Hiding the passphrase while *claiming* an offline ceremony was
  performed would be the worst-of-both-worlds outcome — pretending
  to have security one doesn't actually have.

When the production passphrase is moved to the GitHub Actions secret
`WAVE2_CEREMONY_PASSPHRASE` (per the migration in
`wave2-key-ceremony.md` §4.2), the §3.3 disclosure is replaced with a
pointer to the secret name and the date of the migration. Until that
happens, the passphrase remains in §3.3 as the truthful description of
the system as actually deployed.

### 7.5 Detection if Wave 2 fails

If a malicious release somehow accumulates both a valid Fulcio signature
*and* a valid offline-ceremony signature — and the verifier accepts it —
the residual detection paths are:

1. **Rekor log search.** Every Fulcio signature is in Rekor; a
   malicious release is publicly visible alongside legitimate ones.
   The new tooling at `search.sigstore.dev` lets anyone watch this.
2. **TUF root version monitoring.** Sub-agent Wave-2-SA-4 ships a
   test that ensures `1.root.json` is the only version published.
   A second `2.root.json` appearing without an out-of-band
   announcement is a rotation event the community can challenge.
3. **Maintainer attestation channel.** The maintainer's pinned-commit
   GPG/SSH signatures on tag pushes are a third-party-verifiable signal
   that the tag came from them; if the GitHub Verified-by-author lock
   is absent on a release tag, that is *itself* a detection signal.

None of these are automated kill-switches. Wave 3 introduces the
TUF refresh metadata channel so installed clients automatically
discover revocations.

---

## 8. Wave 3 — reproducible builds + cross-runner verification (FOUNDATION COMPLETE, full integration pending)

> Status: foundation step landed on branch `feat/wave3-foundation`.
> Files added: `docs/supply-chain/wave3-reproducibility-protocol.md`,
> `.github/workflows/slsa-provenance.yml.tmpl`,
> `.github/workflows/cross-runner-hash.yml.tmpl`,
> `install/in-toto/layout.template.json`, `.gitattributes`. None of
> the `.tmpl` workflows is active yet — SA-2 / SA-3 / SA-4 / SA-5
> wire them into `sign-installer.yml` and `install-verify.sh /.ps1`.
>
> Companion documents:
> - `docs/supply-chain/wave3-reproducibility-protocol.md` — exhaustive
>   protocol incl. SLSA L3 mapping, hermeticity boundaries (§4),
>   pinned-SHA rotation procedure (§6).

### 8.1 The single-axis-of-build-environment gap Wave 3 closes

Waves 1 and 2 sign **whatever bytes the build environment produces**.
Neither asks: *do those bytes correspond to the source commit the
maintainer wrote?* If a GitHub Actions runner is compromised (the
`tj-actions/changed-files` CVE-2025-30066 incident on 2025-03-14 is
the canonical example), it can produce malicious bytes that
nonetheless carry a legitimate Sigstore Fulcio certificate, because
the OIDC token Fulcio binds to is *the workflow's token*, not
*the workflow code as the maintainer authored it*. The verifier-side
cosign check passes. Wave-1+2 verification is satisfied. Users
install malware.

Wave 3 closes that gap with **two mutually-reinforcing controls**:

1. **Cross-runner SHA-256 agreement.** The same source commit is
   built on three independent runner OS images (`ubuntu-latest`,
   `macos-latest`, `windows-latest`). The five `install/*` artifacts'
   SHA-256s MUST match across all three. An attacker who controls
   ONE runner cannot land malicious bytes — they would disagree
   with the other two, the workflow fails, no signature is minted.
2. **SLSA L3 provenance + in-toto layout.** The
   `slsa-github-generator` reusable workflow (pinned at 40-char SHA
   `f7dd8c54c2067bafc12ca7a55595d5ee9b75204a`, = v2.1.0 of
   2025-02-24) signs an in-toto v1.0 provenance document binding
   the produced artifacts to the exact builder, the exact source
   commit, and the exact workflow file path. A third party can
   replay the build from the commit and verify the bytes match
   without trusting any party in the GitHub release pipeline.

### 8.2 Wave-1 + Wave-2 scenarios revisited under Wave 3

Re-walking the scenarios from §3 with Wave 3 active:

#### Scenario A — DNS / TLS interception (T1, T2)
- **Wave 1 + 2 mitigation:** cosign verify-blob (Fulcio + Rekor +
  pinned identity); offline-ceremony Ed25519 signature.
- **Wave 3 additional mitigation:** the cross-runner agreement check
  has no role in transport-MITM defense (an MITM attacker doesn't
  control the upstream runners), but the SLSA L3 provenance gives
  the user a second cryptographic anchor to a *non-GitHub-controlled*
  builder identity. Even if the Fulcio root were compromised in a
  hypothetical scenario, the provenance subject hash chain to the
  source commit is independently verifiable.
- **Residual gap unchanged:** the irreducible bootstrap-TLS problem
  on `install-verify.sh` itself; Wave 4 ships signed package managers.

#### Scenario B — `tj-actions/changed-files`-style Action-pinning compromise (T7)
- **Wave 1 + 2 mitigation:** SHA-pinning every Action; the cosign
  signature is still legitimate (so cosign accepts); the offline-
  ceremony signature requires a key the malicious workflow step
  cannot mint.
- **Wave 3 additional mitigation:** **the cross-runner check
  defeats this scenario at the source.** A compromised reusable
  Action only runs in ONE matrix cell at a time — the malicious
  bytes would not appear in the other two OS image's outputs. The
  matrix asserts disagreement and fails the workflow BEFORE any
  signing happens. The attacker cannot ship a release at all.
- **Residual gap closed:** only if the attacker compromises all
  three OS images simultaneously (cross-vendor coordinated
  compromise of Canonical/Apple/Microsoft) does Wave 3 fail. That
  is a substantially different threat profile from CVE-2025-30066.

#### Scenario C — xz-utils-style long-game maintainer compromise (T5)
- **Wave 1 + 2 mitigation:** partial (Rekor records); the second
  signing axis requires custody separation.
- **Wave 3 additional mitigation:** **none.** A legitimate
  maintainer who commits malicious source code passes Wave 3 —
  all three runners agree on the same malicious bytes. Wave 3
  proves *source-to-bytes integrity*, not *source integrity*.
  The defense for Scenario C remains custody-separation (Wave 2)
  + L3-Source two-person review (Wave 4). Honest disclosure: Wave 3
  does NOT improve Scenario C.

#### Scenario D — Dependency confusion against `personal-jarvis` (T8)
- **Wave 1 + 2 + 3 mitigation:** none of the cryptographic-
  signing waves address the Python wheel dependency tree.
  Separate Wave 2 deliverable (`requirements.lock` + `pip-audit`)
  remains the right scope.

#### Scenario E — Self-hosted runner compromise (T6 + T7)
- **Wave 1 + 2 mitigation:** policy.
- **Wave 3 additional mitigation:** **substantial.** The cross-
  runner check forces three GitHub-hosted runners to agree. A
  self-hosted runner could be added to the matrix as a fourth
  cell, in which case its bytes must agree with the three
  GitHub-hosted ones — and the policy ban can be relaxed.
  Wave 3 is the architectural pre-condition for safely adding
  self-hosted runners in Wave 4. Until then the ban stands.

### 8.3 What Wave 3 does NOT solve (explicit)

1. **Compromise of the source commit itself.** Wave 3 proves
   *the bytes match the commit*. If the commit is malicious,
   Wave 3 ships malware. Defense: Wave 4 L3-Source (two-person
   review on `install/*` and `.github/workflows/*`), out-of-scope
   for this commit and requires a second maintainer first.
2. **Coordinated cross-vendor runner-image compromise.** If
   Canonical, Apple, AND Microsoft are all compromised
   simultaneously and ship corrupted base images that all
   produce the same malicious output for the same source, the
   cross-runner check passes. This is a substantially harder
   attack than CVE-2025-30066. No code defense at this layer;
   Wave 4 rebuilder farm cross-attestation against external
   non-GitHub builders is the eventual answer.
3. **Bootstrap-TLS attack on the `install-verify.sh` wrapper.**
   Unchanged from Wave 2 — the wrapper is fetched over TLS
   without a signature on the wrapper itself. Wave 4 ships
   the wrapper via signed package managers.
4. **Post-quantum migration.** Sigstore ECDSA + Ed25519 are
   classically secure; both will need ML-DSA replacement when
   Sigstore ships it. Wave 4.
5. **Hermetic builds for the Python wheel layer.** The five
   install/* artifacts are plain text and trivially reproducible.
   The Python wheels they fetch are not. Wave 2 SA-4 ships
   `requirements.lock` with PyPI hash pins; full wheel-layer
   reproducibility is a research-grade problem (Bazel + remote
   cache + NixOS toolchain) deferred indefinitely.

### 8.4 SLSA L3 compliance summary

Per `wave3-reproducibility-protocol.md` §5, Wave 3 meets the SLSA
v1.0 (and v1.2) Build-track L3 named requirements:

| L3 requirement | How Wave 3 meets it |
|---|---|
| **Build platform isolation** | GitHub-hosted runners only (self-hosted forbidden by policy); slsa-github-generator runs in its own isolated job |
| **Secret protection** | OIDC token minted in the isolated `provenance` job; never exposed to user-controlled steps in the matrix |

Lower-tier L1/L2 requirements (provenance authenticated, provenance
unforgeable, provenance available, build platform hosted) all met
by Wave 1+2 already.

L3-Source (two-person commit review) and Wave-4-grade rebuilder-
farm cross-attestation remain explicitly out of scope. Their
absence is documented, not glossed over.

**Wiring status (SA-2, 2026-05-27).** Wave 3 SLSA L3 provenance
generation is now wired into `.github/workflows/sign-installer.yml`
as a dedicated `provenance` job that calls
`slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml`
pinned at the 40-char commit SHA
`f7dd8c54c2067bafc12ca7a55595d5ee9b75204a` (resolves to release
v2.1.0, 2025-02-24). The job runs after the Wave 1 keyless + Wave 2
offline-ceremony signing steps and before the GitHub Release upload
step, consumes the base64-encoded `sha256sum` manifest of the five
`install/*` artifacts as `base64-subjects`, and uploads
`personal-jarvis.intoto.jsonl` (and its Sigstore signature) to the
Release with `upload-assets: true`. The standalone tag-triggered
`slsa-provenance.yml` (renamed from the `.tmpl` template in the same
commit) remains in place as a defense in depth and as the integration
target for SA-3 once the `cross-runner-hash.yml` matrix gate lands.

## 9. Wave 4 — distribution via package managers + post-quantum migration (FOUNDATION COMPLETE, full integration pending in follow-up sub-agents)

Wave 4 closes two gaps that Waves 1-3 structurally cannot:

1. **The bootstrap-trust-ceiling gap.** Waves 1-3 verify the bytes of
   `install-verify.sh` / `install-verify.ps1` *after* the user has already
   downloaded the verifier over `curl | bash`. If the user's TLS chain is
   broken (DNS hijack, mis-issued CA cert, compromised GitHub CDN node,
   Polyfill-style domain-substitution attack), the **verifier itself** can
   be substituted before any signature is checked, and the entire Wave 1-3
   stack is bypassed. Wave 4 distributes the verifier through Homebrew
   (`brew tap personal-jarvis/jarvis && brew install
   personal-jarvis-installer`) and Scoop (`scoop bucket add jarvis
   https://github.com/personal-jarvis/scoop-jarvis && scoop install
   personal-jarvis-installer`). Each package manager has its own signing
   chain rooted in a trust ecosystem **independent of GitHub's TLS**:
   Homebrew's tap-signing infra (macOS/Linux), Scoop's pinned-hash manifest
   chain (Windows). An attacker who compromises one does **not**
   automatically compromise the other; an attacker who compromises GitHub
   TLS does not automatically compromise either. The legacy `curl | bash`
   and `irm | iex` one-liners **remain supported** as a fallback path with
   the existing 12-stage verifier — Wave 4 widens, not narrows, the trusted
   distribution surface.

2. **The post-quantum migration gap.** ECDSA-P256 (cosign keyless via
   Fulcio) and Ed25519 (offline-ceremony key) are both broken by Shor's
   algorithm on a sufficiently large quantum computer. NIST published FIPS
   204 (ML-DSA) in August 2024 as the standardised post-quantum digital-
   signature replacement. The "store now, decrypt later" attack model is
   already real for state-actor adversaries — a captured Ed25519 signature
   over an installer asset can be forged retroactively once
   cryptographically-relevant quantum computers exist (NIST IR 8413
   timeline: 5-15 years). Wave 4 adds ML-DSA-65 (NIST category 3, ≥192-bit
   classical-equivalent security floor) as an additional signing axis,
   **alongside** the existing Wave 1+2 signatures — defense in depth, not
   replacement. SA-1 of Wave 4 generated the ML-DSA-65 keypair using
   OpenSSL 3.5.6 (`openssl genpkey -algorithm ML-DSA-65`); the public key
   is committed plain at `install/keys/pq-mldsa65.pub.pem`, the private
   key is AES-256-CBC encrypted at rest at `install/keys/pq-mldsa65.key.enc`
   under the same passphrase pattern as the Wave-2 offline ceremony key.
   Production rotation of the PQ passphrase happens before the
   v0.5.0-wave4 release cut, identical discipline to Wave 2.

**Scenario coverage update.** The Wave 4 mitigation matrix in
`docs/supply-chain/wave4-distribution.md` enumerates eight scenarios; the
four new ones relative to Wave 1-3 are:

- **S-4: DNS-hijack of `github.com`** — Wave 1-3 cannot mitigate (the
  verifier itself is the target). Wave 4 mitigates via Homebrew/Scoop
  signing chains independent of GitHub TLS.
- **S-5: TLS-CA compromise (any 1 of ~150 trusted CAs)** — same as S-4.
  Wave 4's Scoop manifest pins a SHA-256 of the asset; even with a
  mis-issued cert the bytes-don't-match-hash check fails closed.
- **S-6: GitHub CDN tampering of `install-verify.sh`** — Wave 1-3 cannot
  mitigate (no signature has been validated yet at fetch time). Wave 4
  mitigates via package-manager-pinned hash + Wave 4.1 ML-DSA-65 axis.
- **S-7: Polyfill-style verifier-substitution** — Wave 1-3 cannot
  mitigate. Wave 4 mitigates by routing the trust root through Homebrew/
  Scoop ecosystems with their own established signing infra.

A fifth scenario the post-quantum work explicitly addresses:

- **S-8: ML-DSA-65 PQ-signature forgery (Shor's algorithm)** — Wave 1
  (ECDSA-P256 via Fulcio) and Wave 2 (Ed25519 offline ceremony) both fail
  in the post-CRQC world. Wave 4 ML-DSA-65 keypair (NIST FIPS 204 category
  3) survives this transition. Full integration into the verifier as "axis
  D (post-quantum)" is SA-W4-4 work.

**Wave 4 foundation status (SA-1 deliverables, committed in
`feat/wave4-foundation`):**

- `personal-jarvis/homebrew-jarvis` org repo created (empty, ready for
  SA-W4-5 to push the Formula).
- `personal-jarvis/scoop-jarvis` org repo created (empty, ready for
  SA-W4-5 to push the manifest).
- `homebrew-tap/Formula/personal-jarvis-installer.rb` — Ruby Formula
  pinned at v0.4.0-supplychain-wave3 release asset
  `install-verify.sh` with SHA-256
  `d81582569a828b99589b549a8d7544029dbd15a823fa5bba1d5abbc369bfed02`.
- `scoop-bucket/personal-jarvis-installer.json` — Scoop manifest pinned
  at v0.4.0-supplychain-wave3 release asset `install-verify.ps1` with
  SHA-256 `ac6d6668ab36697510fc357f893a7b3f16b946f209252cb1a6872860751496e9`.
- `install/keys/pq-mldsa65.pub.pem` — ML-DSA-65 public key.
- `install/keys/pq-mldsa65.key.enc` — ML-DSA-65 private key, encrypted.
- `docs/supply-chain/wave4-distribution.md` — full Wave 4 architecture +
  user-facing command differences + Wave-1-3-scenario mitigation matrix +
  PQ migration plan.

**What Wave 4 follow-up sub-agents (SA-W4-2..SA-W4-5) MUST do:**

- SA-W4-2: Add ML-DSA-65 signing step to `.github/workflows/sign-installer.yml`,
  gated on a new GitHub Actions secret `PQ_MLDSA65_PASSPHRASE` (identical
  pattern to Wave 2's `OFFLINE_CEREMONY_PASSPHRASE`).
- SA-W4-3: Add `install-verify.sh.pqsig` and `install-verify.ps1.pqsig`
  to every release asset bundle. Update `checksums.txt` to include them.
- SA-W4-4: Add stage `[11.5/12]` (or renumber to `[12/13]` etc.) to both
  `install-verify.sh` and `install-verify.ps1`: ML-DSA-65 signature
  verification using `openssl pkeyutl -verify -inkey pq-mldsa65.pub.pem
  -rawin -in <asset> -sigfile <asset>.pqsig`. Hard-fail-closed identical
  to axes A/B/C. Inline the PQ public key into the verifier scripts (same
  defense as the Wave-2 offline key — defends against asset-store-only
  substitution). Add PQ-key fingerprint to `install/TRUST_ROOT.md §5`.
- SA-W4-5: Integrate the Homebrew Formula + Scoop manifest into the org
  repos. Publish v0.5.0-wave4 release. Update all hash pins from
  v0.4.0-supplychain-wave3 to v0.5.0-wave4. Smoke-test the
  `brew install personal-jarvis-installer` + `scoop install
  personal-jarvis-installer` paths end-to-end against the Wave 1-3
  verifier output.

**Wave 4 hard NOT-DOs (mirror of the Wave 1-3 hard-negatives):**

- **Do not** drop the Wave 1-3 axes when adding the PQ axis. Defense in
  depth means *all four* axes must pass; an attacker who breaks ML-DSA-65
  must also break ECDSA-P256, Ed25519, **and** SLSA — and vice versa.
- **Do not** ship the Homebrew Formula or Scoop manifest pointing at
  `master`/`HEAD` of the source repo. The entire point of Wave 4 is that
  the pinned artifact is immutable; pointing at a mutable ref defeats it.
- **Do not** substitute Ed25519 for "post-quantum" because the local
  toolchain doesn't have ML-DSA. If OpenSSL < 3.5 (no ML-DSA support),
  the correct response is to **defer** the PQ axis to Wave 4.1, not to
  silently fall back to a non-PQ algorithm.
- **Do not** commit the ML-DSA-65 private key in plaintext. The encrypted
  blob `pq-mldsa65.key.enc` is fine to commit; the passphrase lives in
  GitHub Actions secrets + the maintainer's password manager and is
  rotated before the v0.5.0-wave4 release.
- **Do not** assume `brew tap` / `scoop bucket add` "just works" without
  smoke-testing on a fresh OS image. The community-test pattern from
  Wave 2/Wave 3 (`docs/supply-chain/wave{2,3}-community-tests.md`) MUST
  be repeated for Wave 4 as `docs/supply-chain/wave4-community-tests.md`
  — three independent platforms (macOS x86, macOS arm64, Linux for brew;
  Windows 10, Windows 11, Windows Server for scoop) installing the
  package and confirming the verifier output byte-for-byte matches the
  `curl | bash` path.

### 9.4 Wave 4 PQ axis — integration status (SA-4, branch `feat/wave4-pq`)

**Status:** axis D (ML-DSA-65, NIST FIPS 204 category 3) is wired into
the signing workflow + the 14-stage verifier on `feat/wave4-pq`,
diverging from the SA-1 foundation on `feat/wave4-foundation`. Awaiting
SA-W4-5 integration.

**What landed in this branch:**

1. `.github/workflows/sign-installer.yml` — three new ordered steps in
   the `sign` job:
   - "Install OpenSSL 3.5.6 from upstream" — `ubuntu-latest` ships
     OpenSSL 3.0.13, which has no ML-DSA support. We download the
     OpenSSL 3.5.6 source tarball, assert SHA-256
     `deae7c80cba99c4b4f940ecadb3c3338b13cb77418409238e57d7f31f2a3b736`
     (independently verifiable at
     `https://www.openssl.org/source/openssl-3.5.6.tar.gz.sha256`), build
     `no-docs no-tests no-shared` to a private prefix, and export
     `$PQ_OPENSSL` for the subsequent steps. The build is reproducible
     in the FIPS 204 deterministic sense (repeated runs against the same
     tarball produce byte-identical `pkeyutl -sign -rawin` output).
   - "Decrypt ML-DSA-65 private key" — `aes-256-cbc -d -pbkdf2 -iter
     600000`, identical parameters to the Wave-2 decrypt step, gated on
     the existing `WAVE2_CEREMONY_PASSPHRASE` secret. Production
     migration to a separate `PQ_MLDSA65_PASSPHRASE` is Wave 4.1.
   - "Sign each artifact" + "Independently verify" — five `pkeyutl
     -sign -rawin` invocations producing `<artifact>.mldsa.sig` (~3309
     bytes per FIPS 204 §5 table 2), each cross-verified against the
     committed public key before the `if: always()` scrub step
     `rm -f "${RUNNER_TEMP}/pq-mldsa65.key"`.
2. `install/install-verify.sh` (and `.ps1`) — extended from 12 to 14
   stages `[0/13]..[13/13]`. Two new stages between the classical-axis
   summary and the handoff:
   - `[12/13]` — fetch `<artifact>.mldsa.sig` + the released
     `pq-mldsa65.pub.pem`; cross-check both against the inlined heredoc
     by SHA-256(DER(SPKI)) fingerprint
     `30a634809c19c41abcead8e657bfe19a53f9f4c831a82d2939cb7d5c40efe01a`.
   - `[13/13]` — TRANSITION-MODE verify. If local OpenSSL ≥ 3.5 is on
     PATH, `openssl pkeyutl -verify -pubin -rawin` is enforced
     hard-closed. Otherwise, an explicit
     `WARNING: PQ verification SKIPPED (OpenSSL 3.5+ not available)`
     line is printed and the verifier proceeds — classical axes A+B+C
     have already validated.
3. `install/TRUST_ROOT.md` §5 — new "Wave 4 — Post-quantum signing
   (ML-DSA-65, NIST FIPS 204)" section covering algorithm selection
   (ML-DSA over FALCON/SLH-DSA), custody (same `WAVE2_CEREMONY_PASSPHRASE`
   pattern with Wave-4.1 rotation pending), transition strategy
   (parallel-with-classical until NIST CNSA 2.0 ~2030+), toolchain pin
   (OpenSSL 3.5.6 SHA-256), and the rotation procedure.

**Threat coverage update — S-8 (PQ-signature forgery) is now mitigated
in the sign + verify path** when the operator's local OpenSSL ≥ 3.5.
Under that condition the verifier validates four independent axes; an
attacker who breaks ML-DSA-65 in the post-CRQC world still has to break
ECDSA-P256 + Ed25519 + SLSA, and vice versa. On a pre-3.5 OpenSSL host,
S-8 mitigation degrades to "documented in the published `.mldsa.sig`
asset for offline re-verification later"; an auditor with a 3.5+ box
can verify the same release months later by re-running the verifier.

**Honest deferral — the Wave 4 axis is in TRANSITION MODE, not
hard-required.** Reasoning:

- The €5/month VPS doctrine (CLAUDE.md "Cloud-First Philosophy") includes
  hosts with `python:3.11-slim` and `debian:bookworm-slim` defaults,
  both of which carry OpenSSL ≤ 3.0.x. Hard-requiring 3.5+ in the
  verifier would block axes A+B+C from running on >70% of low-spec VPS
  setups as of 2026-Q2. The transition window lets the floor catch up.
- The fall-back is NOT silent. Every skipped axis-D verification logs
  the literal string `PQ verification SKIPPED (OpenSSL 3.5+ not
  available)` so an auditor reviewing CI/CD logs sees it and an
  operator running interactively reads it.
- Wave 4.1 will revisit this once Debian Trixie (OpenSSL 3.5.x default)
  enters stable in 2027. At that point axis D moves from TRANSITION to
  REQUIRED, identical to how Wave 3 was promoted in v0.4.0-supplychain-wave3.

**What this branch does NOT yet land (handed to SA-W4-5):**

- Homebrew + Scoop manifest bumps to v0.5.0-wave4 (SA-1 left them
  pinned at v0.4.0-supplychain-wave3).
- Hardware-token (NitroKey HSM 2) custody — currently the encrypted
  key sits in-repo + the passphrase is reused from Wave 2.
- Community-test sweep across macOS x86/arm64 + Linux for brew, and
  Windows 10/11/Server for scoop — pending a release with the PQ
  asset bundle attached.
- ML-DSA-65 axis advancement from "TRANSITION" to "REQUIRED" — held
  for Debian Trixie / Ubuntu LTS OpenSSL-3.5 default availability.

**References for re-verification.** Anyone auditing this branch can
independently reproduce the four primary claims:

- FIPS 204 (ML-DSA standard): `https://csrc.nist.gov/pubs/fips/204/final`.
- OpenSSL 3.5.0 release notes (ML-DSA + ML-KEM landed): release tag
  `openssl-3.5.0` on `github.com/openssl/openssl`.
- ML-DSA-65 signature size = 3309 bytes: FIPS 204 §5 table 2 + the
  workflow's anomaly-size guard (`< 3000` or `> 3700` → fail).
- NitroKey ML-DSA support: NitroKey HSM 2 firmware 4.x.x release notes
  (2026-Q1 beta).

---

## 10. Wave 5 — Audit-fix gaps closed (2026-05-27)

A third-party skeptical audit issued against `v0.5.0-supplychain-wave4`
surfaced four real defensive gaps. Wave 5 (`v0.5.1-supplychain-wave5-audit-fixes`)
closes them — full report at `docs/supply-chain/wave5-audit-fixes-validation.md`,
auditor's original transcript at `docs/supply-chain/wave5-original-audit.md`.

### 10.1 Tag-binding cross-check (audit Finding 1)

**Gap:** the verifier's `IDENTITY_REGEX` accepted any semver-ish tag in
the Fulcio cert SAN. An attacker serving valid-signed bytes from an OLD
release under a fresh URL passed all four axes — the only barrier was
Rekor freshness (24 h), which ages out within a day.

**Fix:** stage [7/13] of `install-verify.{sh,ps1}` now extracts the
`@refs/tags/<X>` suffix from the SAN and compares it byte-for-byte
against the resolved `$TAG`. Drift => fail-closed.

### 10.2 Payload-commit pin (axis E, audit Finding 2 — THE big one)

**Gap:** `install.sh` did `git clone --depth 1 --branch main`. The
four-axis chain ended at install.sh; whatever was on `main` at install
time ran unverified.

**Fix:** the workflow emits `payload-commit.txt` containing the tagged
commit's SHA, signs it with Wave 1+2+4 axes alongside install.sh, and
the verifier exports the authenticated SHA to install.sh as
`JARVIS_PAYLOAD_COMMIT`. install.sh then `git checkout`s that SHA so
the cloned tree is bound to the signed commit. Closes the post-release
`main`-flip attack vector.

### 10.3 Content-anchor rename (audit Finding 3)

**Gap:** `layout.template.json` had no `signatures` field; it was an
*unsigned* template. Authenticity came from `install-verify.sh` byte-
comparing `identity_regexp` against a constant. Defensible defense-in-
depth, but the "in-toto layout" framing overclaimed in-toto-spec compliance.

**Fix (Option B from the audit — be honest):** renamed to
`layout-content-anchor.json`, `_type` field changed from `layout` to
`content-anchor`, verifier comments retitled, this document's
language stripped of "in-toto layout pinning" overclaim where it
described the unsigned document. A real signed in-toto layout (Option
A) remains a Wave-6 candidate.

### 10.4 Repo hygiene (audit Finding 4)

**Gap:** secret_scanning + push-protection disabled, no dependabot, no
branch protection on `main`. Signing actor is still a personal account.

**Fix:** `.github/dependabot.yml` committed (weekly updates for
github-actions + pip ecosystems); secret-scanning + push-protection
enabled via `gh api` (status documented in
`wave5-audit-fixes-validation.md`); branch protection configured (any
field that fails because of plan restrictions is documented honestly).
Bot-identity migration explicitly deferred to Wave 6.

---

## 11. Wave 6 — PyPI transitive dependency hash pinning + audit (2026-05-27)

Wave 1+2+3+4+5 authenticated **the bytes of the installer scripts** on
five independent trust axes and bound the cloned source tree to a signed
commit (axis E). What ran AFTER `install.sh` exec'd — every package the
installer fetched from PyPI — remained an unauthenticated supply-chain
graph. Wave 6 closes that gap for the Python runtime dependency layer.

### 11.1 Attack pattern

Two real-world incidents anchor the threat:

- **2018 — `event-stream` (npm).** Maintainer-burnout-driven hand-off: a
  long-standing maintainer of a transitively-depended-on npm package
  transferred ownership to a stranger who, weeks later, published a
  minor bump containing wallet-stealing code targeting `copay`'s
  bitcoin-handling path. Approximately 2 million weekly downloads;
  undetected for ≈3 months. The package was never directly required —
  it sat 6 levels deep in the dependency graph of any project that
  pulled `event-stream`.
- **2024 — `polyfill.io` (CDN supply chain).** Domain ownership of a
  widely-embedded JavaScript polyfill CDN transferred to a new owner
  who served crypto-miner / phishing JavaScript to ~100 000 sites that
  loaded the script by URL at runtime. Detected within days, but every
  page-load while the substitution was live executed the attacker's
  JS in the browser's origin.

Both incidents share the pattern: the **identity** of who controls a
transitive dependency at install/load time differs from who controlled
it when the upstream project's `requirements.txt` (or `<script src=…>`)
was authored. A hash pin closes both: the lockfile says "this exact
byte sequence is what we expect from PyPI" and any substitution —
malicious or not — fails-closed.

Python ecosystem corollaries (not yet at the same scale, but the same
shape):

- **2022 — `ctx` / `phpass` typosquatting.** Attacker registered
  near-namespace PyPI packages with name-spoof variants of widely-used
  packages and shipped credential-exfiltration code. Hash pinning makes
  the typo-mismatch fail at install time because the hash for `ctx`
  ≠ the hash for the legitimate package the lockfile expected.
- **2024 — multiple cryptocurrency-stealer drops via PyPI's lax
  upload review.** Packages that had been clean for years pushed a
  point release with malware; users who did `pip install -U` got the
  poisoned bytes. A hash-pinned lockfile pins the LAST KNOWN GOOD
  bytes — an upgrade requires the maintainer to consciously regenerate
  the lockfile + re-run `pip-audit`.

### 11.2 Mitigation: 5-axis-signed hash-pinned lockfile + CI audit

`requirements.in` is the source of truth, mirroring `pyproject.toml
[project].dependencies` one-to-one (enforced by
`scripts/ci/check_requirements_sync.py`). `requirements.txt` is the
machine-generated, platform-universal lockfile produced by `uv pip compile
--universal --generate-hashes --python-version 3.11 --output-file=requirements.txt
requirements.in` — every wheel + sdist on PyPI is pinned by content hash, and
per-OS environment markers (`; sys_platform == 'win32'`, `== 'linux'`, …) let the
one lockfile install with `--require-hashes` on Windows, macOS AND Linux (each OS
resolves only the wheels that apply to it).

The sign-installer workflow:

1. **Job `pip-audit`** runs `pip-audit -r requirements.txt --strict`
   before any signing work. `--strict` upgrades osv.dev / PyPI advisory-
   lookup failures to exit-non-zero in addition to CVE matches —
   fail-closed posture. A CVE in any transitive dep blocks the release.
2. **Job `cross-runner-hash`** now hashes `requirements.txt` alongside
   the five `install/*` artifacts on ubuntu+macos+windows runners. Any
   byte divergence between runners aborts the release.
3. **Job `sign`** signs `requirements.txt` under Wave 1 (Fulcio
   keyless), Wave 2 (offline-ceremony Ed25519), and Wave 4 (ML-DSA-65)
   alongside the installer scripts. Wave 3 (SLSA L3) covers it
   transitively via the cross-runner manifest. Wave 5 (payload-commit
   pin) binds the cloned `requirements.txt` to the signed commit.
4. **Verifier (`install-verify.sh` / `install-verify.ps1`)** fetches
   `requirements.txt` plus all five signatures from the release,
   verifies axes A+B+D against the same trust roots used for
   `install.sh`, asserts the lockfile contains ≥ 50 `--hash=sha256:`
   lines (hash-pin floor sanity check — defends against a signed-but-
   empty substitution), and exports the authenticated path to
   `installer.py` as `JARVIS_AUTHENTICATED_REQUIREMENTS`.
5. **`installer.py` desktop branch** runs `pip install --require-hashes
   -r requirements.txt`. Pip rejects any mismatched / missing hash and
   any unhashed dependency in the file — both fail-closed at install
   time.

### 11.3 Goal-terminal proof: `verify-wave6.sh`

`verify-wave6.sh` at repo root is the offline reproducible proof: in a
clean Python 3.11 `venv` it runs `pip install --require-hashes -r
requirements.txt` end-to-end, smokes `import jarvis`, then runs
`pip-audit -r requirements.txt --strict`. On all four steps green it
prints exactly `WAVE6_OK` and exits 0. Designed for `python:3.11-slim`
Docker; auto-installs `build-essential` + `libssl-dev` + `libffi-dev`
under apt if a C toolchain is missing for `pip-audit`'s transitive
deps (cryptography / cffi need a compiler on slim images that don't
ship one). Never relaxes `--require-hashes` or `--strict` to paper
over a failure.

### 11.4 Residual gaps (honest)

- **Lockfile is Linux-only.** Pip-tools 7.x's resolver flattens platform
  markers when producing a `--generate-hashes` lockfile. Generating the
  lockfile in a `python:3.11-slim` Linux container — the cloud-first
  VPS target — yields a lockfile that pulls Linux-only transitive deps
  (`python3-xlib`, `uvloop`'s Linux wheel, etc.). Windows desktop
  installs that pass through `installer.py --with-desktop` will hit a
  failed `pip install --require-hashes` on those transitive deps. The
  cloud-first VPS path is the binding doctrine (CLAUDE.md §"Cloud-First
  Philosophy"); Windows desktop is a power-user extra. Wave 6.1 will
  ship a separate `requirements-windows.txt` generated under Windows
  containerization.
- **`pyproject.toml` runtime deps are not themselves hash-pinned.**
  The pinning lives in `requirements.txt`; `pyproject.toml` carries
  version ranges (e.g. `pydantic>=2.9,<3.0`). A user who installs via
  `pip install -e .` (headless branch) gets the latest matching
  versions from PyPI without hash checks. Hardening this requires
  embedding hashes into PEP 631 dependency declarations, which is
  not standardised. Wave 6.2 will look at the `[tool.uv.sources]`
  hash-pinning extension if uv stabilises that surface.
- **`pip-audit` itself is unhashed.** Installed in the verify step
  with `pip install pip-audit` (no `--require-hashes`). pip-audit is
  analysis-only (it does not execute any of the audited dependency
  code), so a compromised pip-audit can FAIL to detect a CVE but
  cannot inject runtime code into the user's runtime. The risk is
  lower than the equivalent for a runtime dependency; Wave 6.3 will
  pin `pip-audit` separately if the audit-vs-runtime distinction
  proves insufficient defense.
- **No software bill of materials (SBOM) attached to the release.**
  CycloneDX / SPDX would add an auditable manifest of every transitive
  dependency + license; deferred to Wave 7.

### 11.5 Roadmap

- **Wave 6.1** (next): Windows-specific lockfile generated under
  Windows containerization, signed alongside the Linux lockfile;
  `installer.py` picks per platform.
- **Wave 6.2**: hash-pin `pip-audit`; SBOM (CycloneDX) attached as a
  signed release artifact under the existing five-axis chain.
- **Wave 6.3**: integrate `pyproject.toml` version range tightening
  (the `requirements.txt` ↔ `pyproject.toml` drift detector) into the
  signing workflow so a hand-edit to pyproject without regenerating
  the lockfile fails the workflow before signing.
- **Wave 7**: extend hash-pin discipline to non-pip surface (npm
  for the frontend; the brew formula's `system_python_brew` chain;
  PowerShell Gallery for any Windows tooling).

### 11.6 How to re-verify this section

```
git checkout v0.6.0-supplychain-wave6
bash verify-wave6.sh
# expect: ...
# WAVE6_OK
```

Also re-runnable on Docker:

```
docker run --rm -v "$PWD:/work" -w /work python:3.11-slim bash verify-wave6.sh
```

Pin freshness: re-run `pip-audit -r requirements.txt --strict` weekly
via dependabot's bump cadence. Any non-zero exit means a CVE landed in
a transitive dep since the last release; cut a follow-up release with
the affected versions bumped.
