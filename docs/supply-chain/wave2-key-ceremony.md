# Wave 2 — Key Ceremony

> Status: **foundation (Wave-2-SA-1).** The keypair + TUF root metadata are
> generated and committed. Workflow + verifier integration is built by
> follow-up sub-agents (Wave-2-SA-2 through SA-5).
>
> Companion documents:
> - `install/TRUST_ROOT.md` §3 — the user-facing trust-root explanation,
>   passphrase disclosure, and rotation procedure.
> - `docs/supply-chain/threat-model.md` §7 — what Wave 2 stops and what it
>   still does not.

---

## 1. Why a second signing axis exists

Wave 1 of the supply-chain hardening shipped Sigstore keyless signing via
GitHub Actions OIDC. The verifier accepts a signature iff a cosign
`verify-blob` succeeds against:

- OIDC issuer = `https://token.actions.githubusercontent.com`
- Certificate identity regex pinned to
  `^https://github\.com/personal-jarvis/PersonalJarvis/\.github/workflows/sign-installer\.yml@refs/tags/v[0-9].*$`

That is **one trust axis**: an attacker who controls the GitHub repo (or
the maintainer's account) can run the signing workflow and produce a
signature the verifier accepts.

The **xz-utils incident (CVE-2024-3094, March 2024)** is the canonical
demonstration that "one trusted maintainer" is not enough. Jia Tan
cultivated the trust of Lasse Collin (sole maintainer of xz-utils) over
two years, was granted write access, and shipped a malicious commit in
the 5.6.0 release. Any signing scheme that relies on a single identity —
no matter how strongly that identity is pinned cryptographically — fails
in exactly the same way against the same attack.

**Wave 2 introduces a second, independent signing axis** so that
compromising either axis alone yields nothing. The verifier (built by
Wave-2-SA-2..SA-5) demands 2-of-2: both the Fulcio (online, ephemeral
OIDC) signature *and* the offline-ceremony (long-lived Ed25519) signature
must validate against the bytes the user is about to install. An attacker
who takes over the GitHub account can still mint a Fulcio signature, but
without the offline-ceremony private key the verifier refuses. An attacker
who extracts the offline-ceremony private key still cannot mint a Fulcio
signature (would need the GitHub account too). Both compromises are
required for an exploit to land — the bar is raised to "compromise two
independent organisations / two independent custody chains."

---

## 2. What was generated, exactly

Every command below was executed inside the worktree
`<USER_HOME>\Desktop\quick-install-wt` on 2026-05-26 against the
branch `feat/wave2-foundation`.

### 2.1 Tooling versions

- `openssl version` → LibreSSL 3.x / OpenSSL 3.x via Git-for-Windows MinGW
  (`C:\Program Files\Git\mingw64\bin\openssl.exe`).
- `python --version` → 3.11.x.
- `python -c "import tuf; print(tuf.__version__)"` → 7.0.0 (installed via
  `pip install "tuf>=4"`; the meta-package `tuf>=4` actually resolves to
  `python-tuf` 7.0.0 — the major-version numbering on PyPI is non-linear).

### 2.2 Step 1 — generate a fresh passphrase

```bash
openssl rand -base64 18 > /tmp/wave2_passphrase.txt
cat /tmp/wave2_passphrase.txt
# → <DEMO-PASSPHRASE-ROTATED-OUT>   (24 base64 characters)
```

The passphrase is 18 bytes of CSPRNG output encoded as 24 base64
characters — approximately 144 bits of entropy, well above any feasible
brute-force budget against AES-256-CBC with PBKDF2(SHA-256, 600 000
iterations) salt.

### 2.3 Step 2 — generate the Ed25519 keypair

```bash
openssl genpkey -algorithm Ed25519 -out /tmp/wave2_offline.key
openssl pkey -in /tmp/wave2_offline.key -pubout -out install/keys/offline-ceremony.pub
```

Output: `install/keys/offline-ceremony.pub` (PEM, 116 bytes, committed in
plain). Raw public-key bytes are
`c90e099a2b2ef76fdff763acf034662306f037fae33ae2ec45361368798d9cdd` (32
bytes; Ed25519 public key); the TUF root encodes the PEM under `keyval.public`
for the Wave 2 verifier.

**Deterministic-from-seed disclosure.** This keypair was *not* generated
from a deterministic seed. It was generated from `openssl genpkey`'s
default RNG (system CSPRNG). The Wave-1 ceremony was on a single online
workstation (the maintainer's), so calling this an "offline ceremony" is
aspirational at this foundation step — the *machinery* is in place
(passphrase-encrypted key file, TUF root with threshold=2, separate-axis
verifier contract) but the **operational discipline** of a real offline
ceremony (air-gapped laptop, hardware token, witness on a second
maintainer's machine) is the production migration described in §4 below.
We are deliberately honest about this rather than claiming an offline
ceremony was performed.

### 2.4 Step 3 — encrypt the private key

```bash
PASSPHRASE=$(cat /tmp/wave2_passphrase.txt)
openssl aes-256-cbc -pbkdf2 -iter 600000 -salt \
    -in /tmp/wave2_offline.key \
    -out install/keys/offline-ceremony.key.enc \
    -pass "pass:$PASSPHRASE"
```

Parameters chosen:

| Parameter | Value | Why |
|---|---|---|
| Algorithm | AES-256-CBC | Industry-standard symmetric cipher; CBC is fine here because we authenticate the entire artefact via the TUF root + dual signatures, not via the encryption layer. |
| KDF | PBKDF2-HMAC-SHA256 | OpenSSL's `-pbkdf2` flag. Replaces the legacy `EVP_BytesToKey` derivation which is cryptographically weak. |
| Iterations | 600 000 | OWASP 2023 recommendation for PBKDF2-HMAC-SHA256. Slows a brute-force attempt against the 144-bit passphrase enough that the passphrase entropy is the binding security parameter, not the KDF cost. |
| Salt | 8 random bytes | OpenSSL default; embedded in the ciphertext header. |

### 2.5 Step 4 — round-trip validation

```bash
openssl aes-256-cbc -d -pbkdf2 -iter 600000 \
    -in install/keys/offline-ceremony.key.enc \
    -pass "pass:$PASSPHRASE" \
    -out /tmp/wave2_decrypted.key
openssl pkey -in /tmp/wave2_decrypted.key -noout
# (no output = valid PEM Ed25519 private key)
```

This proves the passphrase + encryption pipeline works. The plaintext key
in `/tmp` was deleted immediately after this validation; only the
encrypted form is persisted.

### 2.6 Step 5 — TUF root metadata

Generated via Python script using `python-tuf` 7.0.0 + `securesystemslib`.
The script source lives inline in the commit message of this branch (and
is reproduced under §6 below for forensics).

The resulting `install/tuf/1.root.json` has these properties (verifiable
by the acceptance gate):

```python
from tuf.api.metadata import Metadata
m = Metadata.from_file("install/tuf/1.root.json")
assert m.signed.roles["root"].threshold == 2
assert set(m.signed.keys.keys()) == {"fulcio_oidc", "offline_ceremony"}
assert m.signed.keys["offline_ceremony"].keytype == "ed25519"
```

> Note on the acceptance-gate API. The task brief used
> `tuf.api.metadata.Root.from_file(...)` — `Root` itself has no `from_file`
> classmethod in python-tuf 7.0.0; only `Metadata` does. The Wave-2-SA-2
> verifier and the integration tests use `Metadata.from_file(...)` (which
> returns a `Metadata` whose `.signed` attribute is the `Root`). The
> assertion is unchanged; only the call site moves up one level.

The Fulcio axis is represented as an `ecdsa-sha2-nistp256` SSlibKey whose
`keyval.public` is the **Sigstore Fulcio v1 intermediate CA** public key
(PEM, pinned for transparency — anyone can cross-check this against the
official Sigstore TUF repository). The OIDC issuer and identity regex
that gate which Fulcio cert is acceptable live as side-channel fields
under that key's `unrecognized_fields` — read by the Wave-2-SA-2 verifier
script when it invokes `cosign verify-blob`.

The offline axis is a vanilla `ed25519` SSlibKey with the raw 32-byte
public key in hex. The Wave-2-SA-2 verifier reads it and verifies a
detached Ed25519 signature using stock `cryptography` or `nacl`.

---

## 3. The verifier contract (built by Wave-2-SA-2)

This document specifies the contract the follow-up sub-agents must
implement. Reproduced here so the foundation is self-contained.

For each released artefact `<asset>`, the Wave 2 install-verify script
MUST:

1. Download `<asset>`, `<asset>.sig`, `<asset>.pem`, `<asset>.bundle`
   (Wave-1 Sigstore artefacts) AND `<asset>.ed25519.sig` (Wave-2
   detached signature over the same bytes).
2. Load `install/tuf/1.root.json` via `Metadata.from_file`.
3. Refuse if `Metadata.signed.roles["targets"].threshold != 2`.
4. For each `keyid` in `roles["targets"].keyids`:
   - If `keyid == "fulcio_oidc"`: invoke `cosign verify-blob` with the
     existing Wave-1 flags (`--certificate-oidc-issuer`,
     `--certificate-identity-regexp`, `--certificate-github-workflow-*`,
     Rekor max-age). Read the issuer + regex from `keys["fulcio_oidc"]
     .unrecognized_fields`. Refuse on non-zero exit.
   - If `keyid == "offline_ceremony"`: load `<asset>.ed25519.sig`,
     load the public key from `keys["offline_ceremony"].keyval.public`
     (hex-decode to 32 bytes), invoke
     `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey
     .from_public_bytes(...).verify(sig, data)`. Refuse on exception.
5. Only after **both** verifications succeed, execute the asset.

The contract is intentionally written without `OR` — `threshold=2` over
two keyids means both, period. The verifier rejects any attempt to satisfy
the threshold with one signature twice.

---

## 4. Production migration path

The demo posture committed in this branch has one explicit gap: the
passphrase is in the repository (see `install/TRUST_ROOT.md` §3.3, with
full disclosure). To run real production releases under Wave 2, do
these things — in this order — and the gap closes without changing the
verifier code:

### 4.1 Generate a fresh keypair (don't reuse this demo key)

Run the §2 ceremony again, but on an air-gapped or freshly-imaged laptop
that has never been online with credentials. The deliverables are the
same (`offline-ceremony.pub`, `offline-ceremony.key.enc`); only the
provenance of the RNG inputs improves.

Ideally:

- Use a hardware random-number generator (e.g. an OpenBSD machine with
  `arc4random`, or a YubiKey FIPS in PIV mode generating the Ed25519 key
  on-device — note that PIV mode on YubiKey supports ECDSA-P256 natively
  but not Ed25519 yet; for true on-device Ed25519 use a Nitrokey 3 or a
  SoloKey v2).
- Verify the laptop has no inbound or outbound network connectivity
  during the ceremony (Wireshark in a separate VM watching the laptop's
  port, or simply unplug the NIC and remove the WiFi card).
- Have a second maintainer present as a witness.
- Photograph the openssl command and its output for the audit trail.

### 4.2 Move the passphrase out of the repository

```bash
gh secret set WAVE2_CEREMONY_PASSPHRASE \
    --repo personal-jarvis/PersonalJarvis \
    --body "<new passphrase from openssl rand -base64 18>"
# Verify:
gh secret list --repo personal-jarvis/PersonalJarvis
# WAVE2_CEREMONY_PASSPHRASE  Updated 2026-MM-DD HH:MM:SS
```

Once the GitHub secret is set:

1. Wave-2-SA-3 (workflow integration) modifies `sign-installer.yml` to
   add a "Sign with offline-ceremony key" step. That step:
   - decrypts `install/keys/offline-ceremony.key.enc` using
     `${{ secrets.WAVE2_CEREMONY_PASSPHRASE }}`.
   - signs the artefact with the Ed25519 private key.
   - uploads `<asset>.ed25519.sig` as a release asset.
   - immediately `shred -uz`-overwrites the decrypted private key.
2. Update `install/TRUST_ROOT.md` §3.3 to **remove** the demo passphrase
   line and replace it with: "Production deployment: passphrase moved to
   `WAVE2_CEREMONY_PASSPHRASE` GitHub Actions secret on 2026-MM-DD."

### 4.3 Better: HSM-backed signing

The §4.2 model still requires the passphrase to exist briefly as
plaintext inside the GitHub-hosted runner. An attacker who compromises
the GitHub Actions infrastructure (or the cosign-installer Action, or
any other Action in the workflow — see threat-model §3 Scenario B) can
exfiltrate the passphrase and the decrypted key while the workflow runs.

A truly air-gapped production posture moves the signing operation
**out of GitHub Actions entirely**:

- Maintainer runs a local CLI (e.g. `wave2-sign <release-tag>`) that
  downloads the release artefact from GitHub, signs it on the
  maintainer's hardware-token-backed laptop, and uploads `<asset>
  .ed25519.sig` back to the release.
- The HSM (YubiKey, Nitrokey, SoloKey v2, or a YubiHSM 2 for shared-
  maintainer scenarios) holds the Ed25519 private key in tamper-resistant
  hardware. Every sign operation needs physical touch.
- The passphrase ceases to exist — there is no PEM file to decrypt; the
  signing is done by sending bytes through a USB protocol that asks the
  token to sign with a key it has never exported.

This is exactly Sigstore's own model for their root TUF ceremonies
(see Sigstore Issue #1432 and the November 2024 root-signing ceremony
recording). Wave 2.5 / Wave 3 will adopt it; Wave 2 documents the path.

---

## 5. Recovery procedure if the offline key is lost

> **Wave 2 has no automated recovery.** This is a deliberate design
> tradeoff and a known Wave 2.5 / Wave 3 follow-up.

If `install/keys/offline-ceremony.key.enc` is lost AND the passphrase is
forgotten (or vice versa — both halves are needed) and no production-
posture HSM exists, the maintainer cannot mint new 2-of-2 signatures.
All future releases would either be unsigned-by-the-offline-axis (which
the Wave-2-SA-2 verifier refuses) or signed under a fresh key the
verifier does not yet trust.

The recovery path is:

1. **Announce the key loss publicly** via GitHub Security Advisory and
   pinned README banner. Users running the current verifier must be
   told *not* to upgrade until a new TUF root version is published.
2. **Run the §2 ceremony fresh** to produce a new Ed25519 keypair.
3. **Publish `install/tuf/2.root.json`** with the new key, marking the
   old keyid as revoked under `unrecognized_fields.wave2_revocation`.
4. **Manually push the new TUF root** to every installed client.
   *Wave 2 has no automated TUF-root rotation*, so this step is a
   bootstrap problem: the client has to fetch `2.root.json` from a
   trusted channel, which it does not have until Wave 3 ships the
   TUF refresh workflow.

The lesson: in Wave 2, **do not lose the offline key.** Store the
passphrase in a 1Password vault, in a paper printout in a fire safe,
or split across two trustees via Shamir's Secret Sharing. The
encrypted-private-key file is committed in the public repo and so is
already replicated worldwide; the passphrase secrecy is the only thing
to protect.

Wave 3 fixes this with a published TUF refresh metadata channel + a
threshold-key recovery key held by an independent project (e.g. the
Sigstore project, or a separate "Personal Jarvis disaster-recovery"
GitHub org owned by different people from the maintainer set).

---

## 6. Reproducibility — the exact script used

The TUF root was generated by the following Python script (copy + run
to reproduce — output must be byte-identical modulo the `expires`
field):

```python
from datetime import datetime, timedelta, timezone
from securesystemslib.signer import SSlibKey
from tuf.api.metadata import Root, Role, Metadata

FULCIO_INTERMEDIATE_V1_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE0ghrh92Lw1Yr3idGV5WqCtMDB8Cx\n"
    "+D8hdC4w2ZLNIplVRoVGLskYa3gheMyOjiJ8kPi15aQ2//7P+oj7UvJPGw==\n"
    "-----END PUBLIC KEY-----\n"
)
OFFLINE_PUB_HEX = "c90e099a2b2ef76fdff763acf034662306f037fae33ae2ec45361368798d9cdd"
FULCIO_IDENTITY_REGEX = (
    r"^https://github\.com/personal-jarvis/PersonalJarvis/"
    r"\.github/workflows/sign-installer\.yml@refs/tags/v[0-9].*$"
)

fulcio_key = SSlibKey(
    keyid="fulcio_oidc",
    keytype="ecdsa",
    scheme="ecdsa-sha2-nistp256",
    keyval={"public": FULCIO_INTERMEDIATE_V1_PEM},
    unrecognized_fields={
        "axis": "fulcio_oidc",
        "oidc_issuer": "https://token.actions.githubusercontent.com",
        "identity_regex": FULCIO_IDENTITY_REGEX,
        "verifier": "cosign-verify-blob",
    },
)
offline_key = SSlibKey(
    keyid="offline_ceremony",
    keytype="ed25519",
    scheme="ed25519",
    keyval={"public": OFFLINE_PUB_HEX},
    unrecognized_fields={"axis": "offline_ceremony", "verifier": "ed25519-raw"},
)

expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=365)
root = Root(
    version=1, spec_version="1.0.31", expires=expires,
    keys={"fulcio_oidc": fulcio_key, "offline_ceremony": offline_key},
    roles={
        name: Role(keyids=["fulcio_oidc", "offline_ceremony"], threshold=2)
        for name in ("root", "targets", "snapshot", "timestamp")
    },
    consistent_snapshot=True,
)
Metadata(signed=root, signatures={}).to_file("install/tuf/1.root.json")
```

To re-validate the committed file:

```python
from tuf.api.metadata import Metadata
m = Metadata.from_file("install/tuf/1.root.json")
assert m.signed.roles["root"].threshold == 2
```

---

## 7. Why two axes is the right number (and not three or N)

Sigstore's own root key ceremony uses **5-of-N** maintainer keys for the
top-level root metadata. Why does Wave 2 only do 2-of-2?

- **Diminishing returns.** Going from one axis to two closes the xz-utils
  gap. Going from two to three closes the case where *two* organisations
  collude (or both maintainers' laptops are popped in the same campaign).
  That is a far rarer attack and substantially more expensive to defend
  (key ceremonies are not free — see the Sigstore Nov 2024 ceremony's
  10-hour video recording).
- **Personal Jarvis maintainer count.** Today the repo has **one**
  maintainer. A 3-of-N threshold cannot be operationally satisfied
  without inviting trustees who do not work on the project. That is
  legitimate (Sigstore does it; the Tor Project does it; Let's Encrypt
  does it), but the operational overhead is non-trivial and is Wave 3
  scope.
- **What Wave 2 actually proves.** That the verifier architecture
  supports threshold > 1. The same machinery generalises to k-of-N
  trivially (extend the `keyids` list in the role definition and bump
  `threshold`). The architecture decision is the expensive one; the
  numeric value is a parameter.

Wave 3 will bump to 3-of-N once a second maintainer onboards with their
own offline ceremony.

---

## 8. Acceptance gate evidence

This document, the keypair, and the TUF root together satisfy these
checks (all run inside this branch's HEAD before commit — the next
sub-agent should re-run them as sanity checks):

> **Public-release honesty note.** The checks below describe the original
> demo branch. In the public release the encrypted private blob
> (`offline-ceremony.key.enc`) is **not tracked** and the demo passphrase has
> been **rotated out** (see `install/TRUST_ROOT.md §3.3`). Steps that assert
> the `.enc` or the literal passphrase are committed therefore no longer apply
> — only the `.pub` key and the TUF root are tracked.

```bash
# 1. Public key committed as a blob
git ls-tree HEAD install/keys/offline-ceremony.pub

# 2. Encrypted private key committed as a blob (demo branch only — NOT in the public release)
git ls-tree HEAD install/keys/offline-ceremony.key.enc

# 3. TUF root committed as a blob
git ls-tree HEAD install/tuf/1.root.json

# 4. Decryption round-trip works (proves passphrase + encryption)
openssl aes-256-cbc -d -pbkdf2 -iter 600000 \
    -in install/keys/offline-ceremony.key.enc \
    -pass "pass:<DEMO-PASSPHRASE-ROTATED-OUT>" \
    -out /tmp/wave2_decrypted.key
openssl pkey -in /tmp/wave2_decrypted.key -noout && echo PASS
rm -f /tmp/wave2_decrypted.key   # scrub immediately

# 5. TUF root metadata loads + threshold=2
python -c "from tuf.api.metadata import Metadata; m = Metadata.from_file('install/tuf/1.root.json'); assert m.signed.roles['root'].threshold == 2; print('PASS')"

# 6. Passphrase committed in TRUST_ROOT.md §3.3 (literal disclosure)
grep -F 'WAVE2_CEREMONY_PASSPHRASE=<DEMO-PASSPHRASE-ROTATED-OUT>' install/TRUST_ROOT.md
```

If any of the six commands fails, this branch is not Wave-2-foundation-
ready and must be regenerated.
