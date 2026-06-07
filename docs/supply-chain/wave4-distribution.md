# Wave 4 — Distribution via Package Managers + Post-Quantum Migration

**Status:** Foundation complete (SA-1). Integration into the CI/CD workflow + verifier + signing pipeline is the work of SA-W4-2..SA-W4-5 follow-up sub-agents.

**Branch:** `feat/wave4-foundation`.

---

## Why distribution closes the bootstrap-trust gap

Wave 1 (cosign keyless), Wave 2 (offline Ed25519), and Wave 3 (SLSA L3 + in-toto) hardened **what** the installer signs and **how** signatures are verified. But all three waves still depend on the user running:

```bash
curl -fsSL https://github.com/personal-jarvis/personal-jarvis/releases/download/<TAG>/install-verify.sh | bash
```

or the equivalent `irm | iex` on Windows. That bootstrap line trusts:

1. **DNS resolution** of `github.com` and `raw.githubusercontent.com`.
2. **The TLS certificate chain** rooted in whichever CAs the host OS trusts (~150 root CAs on a typical machine — any single compromise unlocks impersonation).
3. **GitHub's edge infrastructure** (release-asset CDN, Pages, raw-content host).

If **any** of those three is broken — DNS hijack on the user's network, a single mis-issued certificate from any of the ~150 trusted CAs, a compromised CDN node, a state-actor MITM — the attacker can substitute **the verifier itself** before stage [0/11] runs. Once the verifier is poisoned, every signature it claims to validate is meaningless: the poisoned verifier will happily report success on attacker-signed bytes.

This is **CAPEC-438 (Modification During Manufacture)** at the bootstrap layer, and it is structurally outside the threat model of Waves 1-3.

**Real-world precedent:** The 2024 **Polyfill.io supply-chain attack** (MITRE-tracked) compromised >100k websites by acquiring an established domain that downstream sites trusted via plain `<script src="https://cdn.polyfill.io/...">`. The TLS chain was intact; the trust assumption ("polyfill.io serves the polyfill we expect") was the failure mode. Personal Jarvis's `curl | bash` has the same structural weakness — TLS does not authenticate the *bytes*, only the *channel*.

Wave 4 distributes the verifier through **already-trusted channels with signing chains independent of github.com TLS**:

- **Homebrew tap** — Homebrew's package-manager signing + GitHub repo content (we sign-pin within the Formula). The user's `brew` install has its own integrity model (the `brew` binary itself is verified at tap-install time).
- **Scoop bucket** — Scoop verifies a SHA-256 hash pinned in the manifest against the downloaded asset. That hash is the anchor independent of TLS; even if TLS is broken, the bytes-don't-match-hash check fails closed.

The two channels root the trust in **different ecosystems** — Homebrew's tap-signing infra (macOS/Linux), Scoop's manifest-hash chain (Windows). An attacker who compromises GitHub's TLS does **not** automatically compromise both `brew` and `scoop`.

---

## User-facing command differences

### Before Wave 4 (still works as fallback)

```bash
# macOS / Linux
curl -fsSL https://github.com/personal-jarvis/personal-jarvis/releases/download/<TAG>/install-verify.sh | bash

# Windows
irm https://github.com/personal-jarvis/personal-jarvis/releases/download/<TAG>/install-verify.ps1 | iex
```

These remain supported. The 12-stage verifier (Wave 1+2+3) still runs. They are now the **fallback path** for users on platforms without Homebrew or Scoop.

### After Wave 4 (preferred path)

**macOS / Linux:**

```bash
brew tap personal-jarvis/jarvis
brew install personal-jarvis-installer
personal-jarvis-installer        # runs the 12-stage verifier, then hands off to install.sh
```

**Windows:**

```powershell
scoop bucket add jarvis https://github.com/personal-jarvis/scoop-jarvis
scoop install personal-jarvis-installer
personal-jarvis-installer        # runs the 12-stage verifier, then hands off to install.ps1
```

The verifier behaviour is **identical** in both paths. Wave 4 only changes the bootstrap-trust root; once `install-verify.sh` or `install-verify.ps1` runs, Waves 1-3 take over.

---

## Wave 4 mitigation matrix

| Scenario | Wave 1 (cosign) | Wave 2 (offline Ed25519) | Wave 3 (SLSA + in-toto) | Wave 4 (this) |
|----------|-----------------|--------------------------|-------------------------|---------------|
| **S-1: Fulcio compromise + valid OIDC token** | FAIL (axis A poisoned) | PASS (axis B independent) | PASS (axis C independent) | n/a — pre-verifier |
| **S-2: Offline key custody compromise** | PASS | FAIL | PASS | n/a — pre-verifier |
| **S-3: SLSA generator compromise** | PASS | PASS | FAIL | n/a — pre-verifier |
| **S-4: DNS-hijack of github.com** | n/a | n/a | n/a | **PASS via Homebrew/Scoop signing chain (Wave 4)** |
| **S-5: TLS-CA compromise (any 1 of 150 CAs)** | n/a | n/a | n/a | **PASS via Homebrew/Scoop pinned hash (Wave 4)** |
| **S-6: GitHub CDN tampering of `install-verify.sh`** | n/a (verifier itself is the target) | n/a | n/a | **PASS via Scoop SHA-256 mismatch (Wave 4)** |
| **S-7: Polyfill-style supply-chain (verifier-substitution)** | n/a | n/a | n/a | **PASS via package-manager isolation (Wave 4)** |
| **S-8: ML-DSA-65 PQ-signature forgery (Shor's algorithm)** | FAIL (ECDSA-P256 broken by quantum) | FAIL (Ed25519 broken by quantum) | n/a | **PASS via ML-DSA-65 — NIST FIPS 204 PQ-secure (Wave 4 follow-up)** |

Scenarios S-1..S-3 are the Wave 1-3 axes; Wave 4 leaves them as-is (the 12-stage verifier still runs). Scenarios S-4..S-7 are the Wave-4-only attack surface. Scenario S-8 is the post-quantum migration motivation.

---

## Post-quantum migration — ML-DSA-65

**Threat:** RSA, ECDSA, and Ed25519 are all broken by Shor's algorithm on a sufficiently large quantum computer. NIST published [FIPS 204 (ML-DSA, August 2024)](https://csrc.nist.gov/pubs/fips/204/final) as the standardised post-quantum digital-signature replacement. ML-DSA-65 is the "category 3" parameter set (≥192-bit classical equivalent, well above ECDSA-P256's ~128-bit security floor).

**Why now:** The "store now, decrypt later" attack model is already real for state-actor adversaries — captured Ed25519 signatures over installer assets could be forged retroactively once cryptographically-relevant quantum computers (CRQCs) exist (estimated 5-15 years horizon, NIST IR 8413). The migration path is to **add ML-DSA-65 in parallel**, not to replace Ed25519: defense in depth.

**Wave 4 PQ scaffolding (SA-1 work):**

- `install/keys/pq-mldsa65.pub.pem` — ML-DSA-65 public key, plain.
- `install/keys/pq-mldsa65.key.enc` — ML-DSA-65 private key, AES-256-CBC encrypted with PBKDF2-derived key from the Wave-2 passphrase pattern (`env++ci2NDWCOLeLfgTTZRks`). Encryption round-trip verified at generation time.
- Tooling: OpenSSL 3.5.6 (ML-DSA support added in OpenSSL 3.5.0 via the OQS provider integration).

**What this Wave does NOT do:**

- It does **not** wire the PQ key into the signing workflow (`.github/workflows/sign-installer.yml`).
- It does **not** add a `*.pq-sig` asset to releases.
- It does **not** add an "axis D (post-quantum)" verification stage to `install-verify.sh` / `install-verify.ps1`.
- It does **not** rotate the existing Ed25519 ceremony key.

**What Wave 4.1 (follow-up sub-agents) MUST do:**

- SA-W4-2: Add ML-DSA-65 signing step to the GitHub Actions workflow, gated by a GitHub Actions secret `PQ_MLDSA65_PASSPHRASE` (identical pattern to Wave 2's offline-ceremony secret).
- SA-W4-3: Add `install-verify.sh.pqsig` and `install-verify.ps1.pqsig` to every release asset bundle.
- SA-W4-4: Add stage `[11.5/12]` to both verifier scripts: ML-DSA-65 signature verification using `openssl pkeyutl -verify -inkey pq-mldsa65.pub.pem -rawin -in <asset> -sigfile <asset>.pqsig`. Hard-fail-closed identical to the other axes.
- SA-W4-5: Integrate the Homebrew Formula + Scoop manifest into the org repos (`personal-jarvis/homebrew-jarvis`, `personal-jarvis/scoop-jarvis`), publish the v0.5.0-wave4 release, update SHA-256 pins, smoke-test the new install path.

**PQ key storage strategy:**

- Public key: committed plain in `install/keys/pq-mldsa65.pub.pem` and **inlined into the verifier scripts** (same pattern as the Wave-2 offline key — defends against asset-store-only substitution).
- Private key: encrypted at rest in `install/keys/pq-mldsa65.key.enc`. The passphrase is **never committed**; it lives in GitHub Actions secrets (`PQ_MLDSA65_PASSPHRASE`) and in the maintainer's password manager. The Wave-2 passphrase pattern (`env++ci2NDWCOLeLfgTTZRks`) is reused at SA-1 only for *encrypt-at-rest of the test material*; production rotation happens before the v0.5.0-wave4 cut.
- Fingerprint: see `install/keys/pq-mldsa65.pub.pem` (SHA-256 of DER-encoded SubjectPublicKeyInfo). Will be pinned in `TRUST_ROOT.md §5` (Wave 4.1).

---

## Org-repo creation status

Probed at SA-1 time on `2026-05-27`:

```
gh repo create personal-jarvis/homebrew-jarvis ... → SUCCESS
gh repo create personal-jarvis/scoop-jarvis     ... → SUCCESS
```

Both org repos exist as empty public repositories. SA-1 placed the Formula/manifest scaffolding **in this repo** under `homebrew-tap/Formula/personal-jarvis-installer.rb` and `scoop-bucket/personal-jarvis-installer.json` — those files are the canonical source of truth. SA-W4-5 will copy them into the org repos at v0.5.0-wave4 release time (the org-repo path is the user-facing distribution channel; the in-repo path is the maintainer-visible source).

---

## References

- NIST FIPS 204 (ML-DSA): https://csrc.nist.gov/pubs/fips/204/final
- NIST IR 8413 (PQ migration timeline): https://csrc.nist.gov/pubs/ir/8413/final
- CAPEC-438 (Modification During Manufacture): https://capec.mitre.org/data/definitions/438.html
- Polyfill.io supply-chain incident (2024): https://sansec.io/research/polyfill-supply-chain-attack
- Homebrew Formula Cookbook: https://docs.brew.sh/Formula-Cookbook
- Scoop App Manifests: https://github.com/ScoopInstaller/Scoop/wiki/App-Manifests
- OpenSSL 3.5 ML-DSA support: https://github.com/openssl/openssl/blob/master/CHANGES.md
