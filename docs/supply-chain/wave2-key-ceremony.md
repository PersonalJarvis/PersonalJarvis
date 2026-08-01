# Installer signing key ceremony

This public procedure contains no private keys, passphrases, credentials,
machine paths, or operator identity.

## 1. Preconditions

- Use a clean, offline-capable machine and trusted cryptographic tool.
- Confirm the target is `PersonalJarvis/PersonalJarvis`.
- Prepare protected GitHub Actions secrets before changing public trust data.
- Require two independent reviewers for production rotation.

## 2. Generate and store

Generate a fresh keypair with secure randomness. Store the private key and
passphrase only in the approved secret store and encrypted offline backup.
Export only the public key, algorithm, and SHA-256 fingerprint. Set protected
CI secrets without echoing their values.

## 3. Update and validate

Update public material under `install/keys/`, every verifier copy, expected
fingerprints, transition metadata, and tests. From a clean checkout, prove valid,
modified, unknown-key, expired, and rollback cases on Windows and POSIX. Run the
private-key and privacy gates; any finding blocks publication.

## 4. Migration and activation

Publish transition metadata under the current threshold so old clients can
authenticate the new public key. Activate it only after all trust axes validate.
Retire the old public key only after independently re-downloading the transition
release.

## 5. Compromise recovery

Stop releases, remove the affected CI secret, rotate to a fresh keypair, update
trust metadata, and publish a security advisory. Never allowlist, reuse, or
bypass leaked material.

## 6. Public ceremony record

Record only date, algorithms, public fingerprints, activation release, reviewer
result, and previous-key disposition. Never record secrets, operator identity,
workstation details, or secret-store locations.

The binding model is `install/TRUST_ROOT.md`; distribution and rollback behavior
is documented in `docs/supply-chain/wave4-distribution.md`.
