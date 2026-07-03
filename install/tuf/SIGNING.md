# TUF metadata — signing roles, rotation cadence, threat coverage

> Companion to `install/TRUST_ROOT.md`. This document explains the four TUF
> roles (root, targets, snapshot, timestamp), the rotation cadence each one
> obeys, how to re-sign after re-keying, and which attack each role is
> designed to defeat. Written in real TUF spec language — if a sentence is
> ambiguous, the spec at <https://theupdateframework.io/specification/latest/>
> is authoritative.

---

## 1. The four roles, in one paragraph each

### root

Establishes the trust root. Lists the public keys for every other role and
their per-role signature thresholds. In this repository:

- File: `install/tuf/1.root.json` (version 1 — Wave 2 initial deployment).
- Roles map: `root`, `targets`, `snapshot`, `timestamp` each list
  `keyids = ["fulcio_oidc", "offline_ceremony"]` with `threshold = 2`.
- Expiry cadence: **365 days.** Root rotations are deliberate events
  reviewed by a maintainer.
- Signed by: both axes (`fulcio_oidc` + `offline_ceremony`) when in real
  production. In the Wave 2 foundation demo, signatures are bootstrapped
  in the same axis split as the lower roles — see §3 below.

### targets

Lists every artifact the client may download, with its SHA-256 hash and
length. In this repository:

- File: `install/tuf/1.targets.json`.
- Targets: `install.sh`, `install.ps1`, `installer.py`, `install-verify.sh`,
  `install-verify.ps1`. Hashes computed by `hashlib.sha256` over each file's
  current bytes; lengths from `os.path.getsize`.
- Expiry cadence: **90 days** (quarterly). Re-signed whenever any listed
  artifact's bytes change.

### snapshot

Names every targets metadata file and pins its version + hash + length.
Prevents *mix-and-match* attacks where an adversary serves an old
`1.targets.json` (e.g. one that still trusts a now-yanked installer) while
the client is asking for the current version.

- File: `install/tuf/1.snapshot.json`.
- Points at `targets.json` with `version = 1` and the SHA-256 hash of
  `install/tuf/1.targets.json` at generation time.
- Expiry cadence: **7 days.** Re-signed weekly even when no artifact bytes
  changed.

### timestamp

A single file whose only job is to point at the current `snapshot.json`
with a fresh signature and a very short expiry. The rapid-rotation tip of
the iceberg.

- File: `install/tuf/1.timestamp.json`.
- Points at `snapshot.json` with `version = 1` and the SHA-256 hash of
  `install/tuf/1.snapshot.json` at generation time.
- Expiry cadence: **24 hours.** An attacker who replays old metadata has
  exactly this window before the client refuses it.

---

## 2. Rotation cadence — summary table

| Role | Expiry | Re-sign trigger | Frequency in practice |
|---|---|---|---|
| `root` | 365 d | Maintainer review + key rotation; emergency revocation | Yearly, or on incident |
| `targets` | 90 d | Any artifact (install.sh, installer.py, …) bytes change | Per-release, plus quarterly refresh even without releases |
| `snapshot` | 7 d | Any targets.json file changes (incl. version bump) | Weekly, plus per-release |
| `timestamp` | 1 d | Always — even with no other change | Daily (CI cron) |

The four cadences nest. Timestamp's daily refresh is the most aggressive;
it pulls fresh signature material across every other role's expiry window.

---

## 3. The two signing axes (this repo's deviation from textbook TUF)

This deployment uses a **two-axis 2-of-2** signing model encoded in the
root, but with a deliberate division of labour:

1. **`offline_ceremony` (Ed25519; private key held only as a GitHub Actions
   secret)** signs every TUF metadata file (root, targets, snapshot,
   timestamp). This is what gives each metadata file its single satisfied
   signature in the demo posture.
2. **`fulcio_oidc` (Sigstore keyless via GitHub Actions OIDC)** signs the
   actual binary blobs (`install.sh`, `installer.py`, …) out-of-band via
   `cosign verify-blob`, gated by `.github/workflows/sign-installer.yml`.

The verifier therefore demands **both**:

- a valid TUF metadata chain (root → timestamp → snapshot → targets → file
  hash + length match), signed by `offline_ceremony`; **AND**
- a Sigstore signature on each artifact verifiable under the `fulcio_oidc`
  identity regex pinned in `1.root.json`.

Compromising either axis alone yields nothing. Compromising the
maintainer's GitHub account (xz-utils scenario) does not let an attacker
mint a TUF metadata signature without also stealing the offline key.
Compromising the offline key does not let an attacker push a binary that
clears Sigstore's identity check.

This deviation from textbook TUF (where the same N keys sign both metadata
and targets-blob) is disclosed inline as a `_comment` field in
`1.targets.json`.

---

## 4. Re-signing procedure after re-keying

This is the operational recipe a maintainer runs whenever the
`offline_ceremony` key is rotated (suspected compromise, scheduled annual
ceremony, or first production deployment per
`install/TRUST_ROOT.md` §3.5 rotation procedure).

### 4.1 Re-key the offline key

Follow `docs/supply-chain/wave2-key-ceremony.md` to generate a fresh
Ed25519 keypair in an air-gapped environment. Set the new private key as
the `WAVE2_OFFLINE_KEY_B64` GitHub Actions secret
(`base64 -w0 new-offline.key | gh secret set WAVE2_OFFLINE_KEY_B64`) —
never committed — and overwrite the committed **public** key
`install/keys/offline-ceremony.pub` and its inlined fingerprint in the
verifier scripts.

### 4.2 Bump the root

Create `install/tuf/2.root.json`. **Do not delete `1.root.json`** — TUF
clients walk the version chain. The new root MUST:

- Increment `signed.version` to `2`.
- Record the new key under `keys.offline_ceremony` (same keyid; new
  `keyval.public`).
- Add an `unrecognized_fields.wave2_revocation` block naming the previous
  `keyval.public` so the verifier rejects artifacts signed by the
  retired key.
- Reset `signed.expires` to now + 365 days.
- Be signed by **both** the old and the new key (TUF root key-rollover
  rule: the new root must be signed by enough of both quorums to satisfy
  the threshold on both sides).

### 4.3 Re-sign targets / snapshot / timestamp

Regenerate `1.targets.json` (or bump to `2.targets.json` if any artifact
hashes changed at the same time as the key rotation). Then regenerate the
snapshot pointing at the new targets, then the timestamp pointing at the
new snapshot. Each one is signed with the new offline key.

The signing tool is `python-tuf >= 4` (`pip install tuf`). The canonical
pattern this repo follows:

```python
from securesystemslib.signer import CryptoSigner, SSlibKey
from tuf.api.metadata import Metadata
from tuf.api.serialization.json import JSONSerializer

# Load the offline key from the WAVE2_OFFLINE_KEY_B64 secret (base64 PKCS#8 PEM).
pem_bytes = base64.b64decode(os.environ["WAVE2_OFFLINE_KEY_B64"])
priv = serialization.load_pem_private_key(pem_bytes, password=None)
pub  = SSlibKey(keyid="offline_ceremony", keytype="ed25519",
                scheme="ed25519",
                keyval={"public": priv.public_key().public_bytes(...).hex()})
signer = CryptoSigner(private_key=priv, public_key=pub)

md = Metadata(signed=role_object, signatures={})
md.sign(signer)
md.to_file("install/tuf/<N>.targets.json", JSONSerializer())
```

### 4.4 Append a row to `install/TRUST_ROOT.md` §7

Record: date, what changed (e.g. "offline-ceremony key rotated
2027-05-01"), by whom, and the verification evidence
(the new public key's PEM fingerprint, the first tag signed under it).

---

## 5. What each role protects against — threat coverage

| Role | Attack defeated |
|---|---|
| `root` | Long-term trust anchor compromise. A stolen targets/snapshot/timestamp key cannot be used past the next root rotation — the new root revokes it. |
| `targets` | Malicious-binary substitution. The hash + length pins every artifact byte-for-byte; an attacker who replaces `install.sh` on the release host without also updating + signing a new targets.json fails verification. |
| `snapshot` | Mix-and-match attack. An attacker who serves `1.targets.json` (which lists, say, a known-vulnerable older `install.sh` that has since been yanked) cannot also serve a current `snapshot.json` — the snapshot pins which version of targets is current. |
| `timestamp` | Freeze attack. An attacker who blocks the client from seeing new metadata so the client keeps trusting an old (now compromised) targets list. The 24-hour timestamp expiry caps the freeze window. |

All four roles must validate. A client that finds an expired timestamp
refuses to install. A client whose snapshot points at a targets version
the targets file does not match refuses to install. A client whose
targets file does not match the hash + length the client computed of the
downloaded artifact refuses to install. Each layer fails closed.

---

## 6. CI / release wiring (Wave 2 SA-5 will integrate)

The metadata files in this directory are committed at version 1 by
`feat/wave2-tuf` (Wave 2 SA-4). The signing workflow that:

- recomputes `1.targets.json` hashes on every release tag,
- bumps the snapshot version when artifact bytes change,
- runs the daily timestamp cron,

is wired in by Wave 2 SA-5 in
`.github/workflows/sign-installer.yml`. SA-5 reads the offline private key
from the `WAVE2_OFFLINE_KEY_B64` GitHub Actions secret described in
`install/TRUST_ROOT.md` §3.3.

---

## 7. Verifying a fresh checkout

A maintainer can sanity-check the three signatures with:

```bash
python - <<'PY'
import json
from tuf.api.metadata import Metadata
from securesystemslib.signer import SSlibKey

with open("install/tuf/1.root.json") as f:
    root = json.load(f)
k = root["signed"]["keys"]["offline_ceremony"]
key = SSlibKey(keyid="offline_ceremony",
               keytype=k["keytype"],
               scheme=k["scheme"],
               keyval=k["keyval"])
for name in ("targets", "snapshot", "timestamp"):
    m = Metadata.from_file(f"install/tuf/1.{name}.json")
    key.verify_signature(m.signatures["offline_ceremony"], m.signed_bytes)
    print(f"{name}: VALID, expires {m.signed.expires.isoformat()}")
PY
```

All three lines must print `VALID`. The expiries must be in the future
(targets ≤ 90 d, snapshot ≤ 7 d, timestamp ≤ 24 h from generation time).
