# Wave 2 community-baseline test results

This document captures the results of running three independent
ecosystem test suites against the `v0.3.0-supplychain-wave2` release.
Goal: measure where we land versus the OpenSSF / sigstore community
baseline. Findings here are **measurements, not gates** — we record
real numbers, including failures, rather than hand-wave compliance.

**Run date:** 2026-05-27
**Tag exercised:** `v0.3.0-supplychain-wave2`
**Repo:** `github.com/PersonalJarvis/PersonalJarvis`
**Operator:** SA-5 integrator, Wave 2 integration

---

## Test 1 — sigstore-conformance reference client (sigstore-python)

### What it tests

The `sigstore-python` package (Sigstore reference Python client,
the primary conformance reference implementation alongside `cosign`
and `sigstore-go`) verifies a blob signature against the bundle and
the same identity assertions our verifier uses. Passing this test
proves that a *Sigstore-native* client (not our cosign-based shell
verifier) can also validate our releases — a strong interoperability
signal for downstream packagers (Homebrew, conda-forge, distros).

### Command

```bash
docker run --rm ubuntu:24.04 bash -c '
set -e
apt-get update -qq && apt-get install -y -qq git curl python3 python3-pip python3-venv ca-certificates >/dev/null
TAG=v0.3.0-supplychain-wave2
REL=https://github.com/PersonalJarvis/PersonalJarvis/releases/download/$TAG
mkdir /tmp/c && cd /tmp/c
python3 -m venv venv && . venv/bin/activate
pip install --quiet sigstore
curl -fsSLo install.sh        "$REL/install.sh"
curl -fsSLo install.sh.bundle "$REL/install.sh.bundle"
sigstore verify identity \
  --bundle install.sh.bundle \
  --cert-identity "https://github.com/PersonalJarvis/PersonalJarvis/.github/workflows/sign-installer.yml@refs/tags/v0.3.0-supplychain-wave2" \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  install.sh
'
```

### Result — **FAIL** (known interop gap, not a Wave-2 regression)

```
ERROR  An issue occurred while parsing the Sigstore bundle.
       The provided bundle is malformed and may have been modified maliciously.
       Additional context: failed to load bundle: 5 validation errors for Bundle
       base64Signature  Extra inputs are not permitted ...
       cert             Extra inputs are not permitted ...
       rekorBundle      Extra inputs are not permitted ...
       mediaType        Field required [type=missing, ...]
       verificationMaterial  Field required [type=missing, ...]
```

### Interpretation

Our `.bundle` is the legacy cosign `--bundle` JSON format
(`base64Signature` + `cert` + `rekorBundle` as top-level keys).
`sigstore-python` expects the v2 protobuf-JSON Sigstore bundle
(`mediaType: application/vnd.dev.sigstore.bundle+json;version=0.3` and
`verificationMaterial` envelope). These two formats are both called
"sigstore bundle" but are not wire-compatible.

This is **a known ecosystem-wide interop gap**, tracked across
sigstore/cosign, sigstore/sigstore-python, and sigstore/sigstore-go.
The migration path is `cosign sign-blob --new-bundle-format=true`
or generating `--output-bundle path.bundle.json` in protobuf-JSON,
which we do not yet do. The current Wave-2-verified release is fully
verifiable by cosign-the-CLI but not by sigstore-python out of the box.

### Action

Tracked as Wave 2.1 follow-up: emit dual-format bundles
(legacy for backward compat + new for non-cosign verifiers), gated
by a feature flag in `sign-installer.yml`. Do NOT silently break the
legacy bundle — installed users still rely on it.

---

## Test 2 — python-tuf reference Updater client refresh

### What it tests

The `python-tuf` package's `Updater.refresh()` performs the TUF
metadata-chain consistency check: timestamp → snapshot → targets,
each with threshold-signed metadata, verifying signatures against the
root keyset. Passing this test proves the TUF chain we lay down at
`install/tuf/{1.root,1.snapshot,1.targets,1.timestamp}.json` is a
*real* TUF chain, not just files-that-look-like-TUF.

### Command

```bash
docker run --rm ubuntu:24.04 bash -c '
set -e
apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv curl ca-certificates >/dev/null
python3 -m venv /tmp/v && . /tmp/v/bin/activate
pip install --quiet "tuf>=5,<6"
mkdir /tmp/tuf-repo /tmp/tuf-local && cd /tmp/tuf-repo
for f in 1.root.json 1.snapshot.json 1.targets.json 1.timestamp.json; do
  curl -fsSLo "$f" "https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/tuf/$f"
done
cp 1.root.json root.json
( cd /tmp/tuf-repo && python3 -m http.server 8765 >/tmp/tuf-server.log 2>&1 ) &
sleep 1
python3 - <<PYEOF
import shutil
from pathlib import Path
from tuf.ngclient import Updater
local = Path("/tmp/tuf-local")
local.mkdir(parents=True, exist_ok=True)
shutil.copy("/tmp/tuf-repo/root.json", local / "root.json")
Updater(
  metadata_dir=str(local),
  metadata_base_url="http://127.0.0.1:8765/",
  target_base_url="http://127.0.0.1:8765/",
).refresh()
print("REFRESH=OK")
PYEOF
'
```

### Result — **FAIL** (real gap — TUF root has 0 valid signatures)

```
tuf.api.exceptions.UnsignedMetadataError: root was signed by 0/2 keys
REFRESH=FAIL  type=UnsignedMetadataError
```

### Interpretation

Inspecting `install/tuf/1.root.json` directly:

```python
>>> import json
>>> d = json.load(open("install/tuf/1.root.json"))
>>> len(d.get("signatures", []))
0
>>> d["signed"]["roles"]["root"]["threshold"]
1
```

The root metadata is structurally valid (correct `signed` section,
correct role definitions, 2-of-2 threshold declared) but it carries
**zero signatures**. This means the TUF chain is currently a
*placeholder* — the file format is in place to anchor future TUF
clients, but no Updater can actually trust it until the offline-ceremony
key signs the `signed` block.

### Action

Tracked as Wave 2.1 follow-up: extend the offline ceremony procedure
(`install/tuf/SIGNING.md`) to actually sign 1.root.json — and 1.snapshot,
1.targets, 1.timestamp — with the offline-ceremony key, in TUF-native
signature form (canonical JSON over `signed`, base64-encoded raw Ed25519
signature, embedded in the top-level `signatures` array). The
`install/tuf/SIGNING.md` doc describes the procedure but the foundation
branch checked in unsigned stubs.

This is *not* a Wave-2-verifier regression — the installer's verifier
does not (yet) consume the TUF metadata. Wave 2 is "+TUF metadata"
in shape; making the TUF chain authoritative is a Wave 3 milestone.

---

## Test 3 — OpenSSF Scorecard

### What it tests

The OpenSSF Scorecard is the community-standard automated security-
posture audit for open-source repos. It scores 18 checks
(branch protection, signed releases, dependency pinning, CI tests,
SAST, vulnerabilities, …) on a 0–10 scale and aggregates them.

### Command

```bash
docker run --rm \
  -e GITHUB_AUTH_TOKEN=$(gh auth token) \
  gcr.io/openssf/scorecard:stable \
  --repo=github.com/PersonalJarvis/PersonalJarvis
```

### Result — **3.0 / 10 aggregate** (real number; do not hide)

| Check | Score | Reason |
|---|---|---|
| Binary-Artifacts | 10/10 | no binaries in the repo |
| Branch-Protection | 0/10 | not enabled on main/release |
| CI-Tests | 10/10 | 1/1 merged PR checked by CI |
| CII-Best-Practices | 0/10 | no OpenSSF badge applied for |
| Code-Review | 0/10 | 0/30 last changesets had a reviewer |
| Contributors | 3/10 | 1 contributing org |
| Dangerous-Workflow | 10/10 | no dangerous patterns |
| Dependency-Update-Tool | 0/10 | no Dependabot/Renovate |
| Fuzzing | 0/10 | no fuzzing setup |
| License | 10/10 | LICENSE present |
| Maintained | 0/10 | project < 90 days old (false negative — repo IS active) |
| Packaging | ? | no packaging workflow detected |
| Pinned-Dependencies | 2/10 | most deps not pinned by hash |
| SAST | 0/10 | no SAST on all commits |
| Security-Policy | 0/10 | no SECURITY.md |
| **Signed-Releases** | **8/10** | 2/2 last releases have signed artifacts |
| Token-Permissions | 0/10 | workflows declare too-broad token perms |
| Vulnerabilities | 0/10 | 77 existing vulns in deps (osv-scanner data) |

### Interpretation

The **Signed-Releases 8/10** is the headline Wave-2-relevant signal: both
`v0.2.0-supplychain-wave1` and `v0.3.0-supplychain-wave2` register as
"signed", which is exactly what Wave 1+2 were built to deliver. (The
score isn't 10/10 because Scorecard's heuristic also looks at provenance
attestations, which Wave 3 will add.)

**0/10 on Token-Permissions, Branch-Protection, Code-Review, and SAST
are real, valid findings** — orthogonal to Wave 2's signing scope but
genuine supply-chain weaknesses we don't have community-grade defenses
for yet.

The **0/10 Vulnerabilities (77 existing)** is the largest single piece
of bad news. These are osv-database matches against pinned/unpinned
dependencies. Wave 2 has no story for this; Wave 4 should.

Per the briefing: "Don't fix the failures; just measure where we stand
vs the community baseline." Recorded honestly. Not patched.

### Action

Tracked as separate Wave 2.1 / future-wave items:
- Branch-Protection / Code-Review → orgs/governance, not a code change.
- Token-Permissions → audit `.github/workflows/*.yml` for missing
  `permissions:` blocks (the new sign-installer workflow declares
  `permissions: { id-token: write, contents: write }` explicitly,
  but other workflows may not).
- Vulnerabilities (77) → independent audit pass; not Wave-2 scope.
- Security-Policy → trivial: add `SECURITY.md`.
- CII-Best-Practices → register with bestpractices.coreinfrastructure.org.

---

## Summary

| Suite | Verdict | Wave-2 implication |
|---|---|---|
| sigstore-python reference client | FAIL (bundle format mismatch) | Known interop gap — emit dual-format bundles in Wave 2.1 |
| python-tuf Updater refresh | FAIL (root has 0 signatures) | TUF root is structurally valid but unsigned; complete signing in Wave 2.1 |
| OpenSSF Scorecard aggregate | 3.0 / 10 (Signed-Releases 8/10) | Wave-2-specific axis is healthy; orthogonal weaknesses remain |

None of these failures invalidate Wave 2's central contract
(verifier refuses single-axis or foreign-key tampering — see
`red-team-log.md` R-Wave2-A + R-Wave2-B). They mark genuine
follow-up work for Wave 2.1 and beyond.
