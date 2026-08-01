# Signed release distribution and rollback protection

Personal Jarvis distributes managed updates only through published GitHub
Releases in `PersonalJarvis/PersonalJarvis`. A pushed tag without a published
release is not an installable update.

## Release contract

Each release must provide:

- an immutable semantic-version tag;
- a published GitHub Release associated with that tag;
- source archives generated from the reviewed public tree;
- signed metadata containing version, artifact hashes, sizes, and key ids;
- signatures that satisfy the current threshold policy.

Private signing keys remain outside the repository. Only public verification
keys and fingerprints ship with the installers.

## Client verification order

Clients verify metadata before trusting an artifact:

1. repository identity and release channel;
2. metadata signature and threshold;
3. metadata expiry and version monotonicity;
4. artifact name, size, and SHA-256 hash;
5. platform-specific package signature when present.

Any mismatch fails closed. A network failure does not turn an unverified
artifact into a trusted one.

## Rollback and freeze resistance

The updater persists the newest trusted metadata version and rejects older
metadata unless the user explicitly enters a documented recovery flow. Expiry
limits freeze attacks. Release automation must never overwrite an existing tag
or silently replace an already-published asset.

## Key transition

A key rotation is published as an explicit trust transition. Update verifier
copies, public keys, fingerprints, metadata, and tests in one reviewed change.
Where the current policy requires it, both old and new trust roots sign the
transition before the old key is retired.

## Recovery

If release verification fails:

1. keep the installed version running;
2. report the exact failed check without exposing secret values;
3. fetch metadata again from the canonical release;
4. require a new signed release for repair.

Unsafe development overrides are never enabled automatically and are not a
production recovery mechanism.

See `install/TRUST_ROOT.md`,
`docs/supply-chain/wave2-key-ceremony.md`, and
`docs/supply-chain/threat-model.md` for the related contracts.
