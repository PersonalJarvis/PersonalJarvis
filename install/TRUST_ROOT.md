# Personal Jarvis installer trust root

This public contract contains no private signing material, passphrases,
workstation details, or operational recovery records.

## 1. Scope

The repository carries only public verification keys under `install/keys/` and
the verifier copies used by both installers. Private keys and passphrases exist
only in protected GitHub Actions secrets and an approved offline backup.

## 2. Fail-closed rule

An unknown key, invalid signature, hash mismatch, expired metadata, rollback,
repository mismatch, or unavailable required proof stops installation. Unsafe
overrides are only for controlled recovery and must never be enabled by default.

## 3. Classical trust axes and key custody

- **Axis A:** the artifact validates against the bundled public key.
- **Axis B:** its name, version, and digest match signed metadata.
- **Axis C:** repository and workflow provenance match the expected release.

### 3.3 Private-key custody

Private keys and passphrases stay in protected release secrets and the approved
offline backup. Never commit a private key, encrypted private-key copy, or
passphrase. Only public keys and fingerprints belong in the repository.

### 3.5 Rotation procedure

Generate a fresh keypair offline, install the private part in protected release
secrets, add only the public part to the repository, publish transition metadata
under the current threshold, update both verifiers and their tests atomically,
then verify a clean release before retiring the old public key.

## 4. Verification provenance

Every inlined verifier key, fingerprint, tool digest, and expected workflow
identity changes as one reviewed trust-root update. A release must prove the
same decisions on Windows and POSIX before these values change.

## 5. Axis D — post-quantum verification

An ML-DSA proof is an additional axis; it never replaces Axes A through C. A
platform that cannot execute it fails closed unless the user deliberately uses
the documented recovery override after independently verifying the release.

## 6. Release history

Signed metadata and release notes record public key identifiers, algorithms,
fingerprints, activation releases, and retirement releases without secret or
operator data.

## 7. Rotation log

Record date, algorithm, public fingerprint, activation release, reviewer result,
and previous-key disposition. Never record secrets, operator identity, machine
details, or secret-store locations.

## 8. Incident response

If a key may be compromised, stop releases, remove the affected secret, rotate,
update public trust material, and publish a security advisory. Never weaken
verification to keep an installer working.

## 9. TUF metadata

Root, targets, snapshot, and timestamp metadata follow `install/tuf/SIGNING.md`.
Metadata rollback or expiry is a hard failure.

## 10. Axis E — repository and source binding

Axis E binds source installation to the expected repository, immutable commit,
and signed release identity. A file hash alone is insufficient when source came
from an untrusted repository or moving branch.

### 10.3 Fresh-source recovery

Obtain a different published release from the official repository and verify it
from a fresh location. Do not reuse a suspect clone or bypass binding unattended.

### 10.4 Layout content anchor

The signed layout anchor binds security-critical verifier paths and contents.
Moving a protected file requires updating the anchor, metadata, installers, and
tests in one release.

### 10.5 Independent recovery identity

Emergency recovery metadata requires the independent identity and threshold in
the active TUF root. A normal release credential cannot approve its own recovery.

See `docs/supply-chain/wave2-key-ceremony.md`,
`docs/supply-chain/wave4-distribution.md`, `docs/supply-chain/threat-model.md`,
and `install/tuf/SIGNING.md`.
