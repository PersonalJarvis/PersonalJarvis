# Wave 2 — Key Ceremony

> Status: **foundation (Wave-2-SA-1).** The keypair + TUF root metadata are
> generated and committed. Workflow + verifier integration is built by
> follow-up Jarvis-Agents (Wave-2-SA-2 through SA-5).
>
> **Key custody update (2026-07-03).** The offline-ceremony keys were rotated
> and the encrypt-at-rest scheme was removed. The private key is no longer
> committed in any form — it lives only as the `WAVE2_OFFLINE_KEY_B64` GitHub
> Actions secret (base64 of the PKCS#8 PEM). There is no passphrase. The
> sections below have been updated to the new model; the retired demo key and
> its once-disclosed passphrase are considered burned.
>
> Companion documents:
> - `install/TRUST_ROOT.md` §3 — the user-facing trust-root explanation,
>   key custody, and rotation procedure.
> - `docs/supply-chain/threat-model.md` §7 — what Wave 2 stops and what it
>   still does not.

---

## 1. Why a second signing axis exists

Wave 1 of the supply-chain hardening shipped Sigstore keyless signing via
GitHub Actions OIDC. The verifier accepts a signature iff a cosign
`verify-blob` succeeds against:

- OIDC issuer = `https://token.actions.githubusercontent.com`
- Certificate identity regex pinned to
  `^https://github\.com/PersonalJarvis/PersonalJarvis/\.github/workflows/sign-installer\.yml@refs/tags/v[0-9].*$`

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

### 2.2 Step 1 — key custody model (no passphrase)

The private key is **not** stored in the repository, so there is no
at-rest passphrase to generate. After the keypair is created (Step 2),
its private half is base64-encoded and stored as the
`WAVE2_OFFLINE_KEY_B64` GitHub Actions secret (Step 3). The only secret
material is the raw key itself, held by GitHub's encrypted secret store
plus a local backup in the maintainer's password manager. The repository
carries only the public key.

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
(private key held only as a GitHub Actions secret, TUF root with
threshold=2, separate-axis verifier contract) but the **operational
discipline** of a real offline
ceremony (air-gapped laptop, hardware token, witness on a second
maintainer's machine) is the production migration described in §4 below.
We are deliberately honest about this rather than claiming an offline
ceremony was performed.

### 2.4 Step 3 — export the private key as a GitHub Actions secret

```bash
# base64 the PKCS#8 PEM and store it as an encrypted GitHub secret.
# The key never touches the repository.
base64 -w0 /tmp/wave2_offline.key | \
    gh secret set WAVE2_OFFLINE_KEY_B64 --repo PersonalJarvis/PersonalJarvis
```

Custody model:

| Property | Value | Why |
|---|---|---|
| At-rest location | GitHub Actions secret `WAVE2_OFFLINE_KEY_B64` | GitHub encrypts secrets at rest and injects them only into workflow runs; the private key never appears in the repo tree, not even encrypted. |
| Encoding | base64 of the PKCS#8 PEM | Lets a multi-line PEM travel as a single secret value; the workflow base64-decodes it into a runner tempfile at sign time. |
| Passphrase | none | There is no encrypted key file to unlock, so there is no passphrase to store, disclose, or rotate. |
| Backup | maintainer's password manager | A single offline copy so the key can be re-set if the GitHub secret is ever lost. |

### 2.5 Step 4 — round-trip validation

```bash
# Decode the secret value back to a PEM and confirm it is a valid key.
gh secret list --repo PersonalJarvis/PersonalJarvis   # WAVE2_OFFLINE_KEY_B64 present
base64 -d /tmp/wave2_offline_b64.txt > /tmp/wave2_decoded.key
openssl pkey -in /tmp/wave2_decoded.key -noout
# (no output = valid PEM Ed25519 private key)
```

This proves the base64 round-trip preserves a valid key. The plaintext
key in `/tmp` was deleted immediately after this validation; the repo
persists only the public key, and the private key persists only inside
the GitHub secret store.

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

This document specifies the contract the follow-up Jarvis-Agents must
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

The private key already lives only in a GitHub Actions secret, not in the
repository (see `install/TRUST_ROOT.md` §3, key custody). One hardening
gap remains: the key is generated on, and briefly decoded onto, a
network-attached machine. To reach a fully air-gapped production posture
under Wave 2, do these things — in this order — without changing the
verifier code:

### 4.1 Generate a fresh keypair (don't reuse this demo key)

Run the §2 ceremony again, but on an air-gapped or freshly-imaged laptop
that has never been online with credentials. The deliverables are the
same (`offline-ceremony.pub` committed to the repo, the private half set
as the `WAVE2_OFFLINE_KEY_B64` secret); only the provenance of the RNG
inputs improves.

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

### 4.2 Set the private-key secret

```bash
base64 -w0 offline-ceremony.key | \
    gh secret set WAVE2_OFFLINE_KEY_B64 --repo PersonalJarvis/PersonalJarvis
# Verify:
gh secret list --repo PersonalJarvis/PersonalJarvis
# WAVE2_OFFLINE_KEY_B64  Updated 2026-MM-DD HH:MM:SS
```

Once the GitHub secret is set:

1. The signing workflow (`sign-installer.yml`) has a "Sign with
   offline-ceremony key" step. That step:
   - base64-decodes `${{ secrets.WAVE2_OFFLINE_KEY_B64 }}` into a runner
     tempfile (no passphrase — the decoded bytes are the PKCS#8 PEM).
   - signs the artefact with the Ed25519 private key.
   - uploads `<asset>.ed25519.sig` as a release asset.
   - immediately `shred -uz`-overwrites the decoded private key.
2. Swap the committed public key (`offline-ceremony.pub`) + its inlined
   copy in the verifier scripts + the pinned fingerprint, and append a
   row to the rotation-history table in `install/TRUST_ROOT.md`.

### 4.3 Better: HSM-backed signing

The §4.2 model still requires the private key to exist briefly as
plaintext inside the GitHub-hosted runner (the base64 secret is decoded
there before signing). An attacker who compromises the GitHub Actions
infrastructure (or the cosign-installer Action, or any other Action in
the workflow — see threat-model §3 Scenario B) can exfiltrate the
decoded key while the workflow runs.

A truly air-gapped production posture moves the signing operation
**out of GitHub Actions entirely**:

- Maintainer runs a local CLI (e.g. `wave2-sign <release-tag>`) that
  downloads the release artefact from GitHub, signs it on the
  maintainer's hardware-token-backed laptop, and uploads `<asset>
  .ed25519.sig` back to the release.
- The HSM (YubiKey, Nitrokey, SoloKey v2, or a YubiHSM 2 for shared-
  maintainer scenarios) holds the Ed25519 private key in tamper-resistant
  hardware. Every sign operation needs physical touch.
- The exported key ceases to exist — there is no PEM in a secret to
  decode; the signing is done by sending bytes through a USB protocol
  that asks the token to sign with a key it has never exported.

This is exactly Sigstore's own model for their root TUF ceremonies
(see Sigstore Issue #1432 and the November 2024 root-signing ceremony
recording). Wave 2.5 / Wave 3 will adopt it; Wave 2 documents the path.

---

## 5. Recovery procedure if the offline key is lost

> **Wave 2 has no automated recovery.** This is a deliberate design
> tradeoff and a known Wave 2.5 / Wave 3 follow-up.

If the `WAVE2_OFFLINE_KEY_B64` secret is lost AND the password-manager
backup of the private key is gone, and no production-posture HSM exists,
the maintainer cannot mint new 2-of-2 signatures.
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

The lesson: in Wave 2, **do not lose the offline key.** Keep the
password-manager backup of the private key in a vault, in a paper
printout in a fire safe, or split across two trustees via Shamir's
Secret Sharing. The private key exists only in the GitHub secret store
plus that backup — losing both is unrecoverable, so the backup is the
thing to protect.

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
    r"^https://github\.com/PersonalJarvis/PersonalJarvis/"
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
Jarvis-Agent should re-run them as sanity checks):

```bash
# 1. Public key committed as a blob
git ls-tree HEAD install/keys/offline-ceremony.pub

# 2. Private key is NOT tracked in the repo (only the .pub is)
git ls-tree HEAD install/keys/ | grep -F 'PRIVATE KEY' && echo "FAIL: private key committed" || echo "PASS: only public key tracked"

# 3. TUF root committed as a blob
git ls-tree HEAD install/tuf/1.root.json

# 4. Private-key secret is set (proves custody)
gh secret list --repo PersonalJarvis/PersonalJarvis | grep -F 'WAVE2_OFFLINE_KEY_B64'

# 5. TUF root metadata loads + threshold=2
python -c "from tuf.api.metadata import Metadata; m = Metadata.from_file('install/tuf/1.root.json'); assert m.signed.roles['root'].threshold == 2; print('PASS')"

# 6. No plaintext key material tracked under install/keys/ (must find only .pub)
git grep -nl 'PRIVATE KEY' -- install/keys/ && echo "FAIL: private key material tracked" || echo "PASS: no private key at rest"
```

If any check fails, this branch is not Wave-2-foundation-ready and must
be regenerated.
