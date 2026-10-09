# CI/CD audit — 2026-10-02

## Follow-up verification — 2026-10-03

Rechecked on local HEAD `995aedf6dabc7986e872b47a3170895694fa4911`, preserving
the other sessions' newer application changes and all existing pipeline work.
The original 238 CI-script tests still passed. The deeper review found and
fixed four additional gaps:

- Tag workflow dispatches matched the branch cancellation condition. Tag CI
  is now explicitly excluded from branch cancellation and branch concurrency
  grouping.
- A newer ordinary main run at the same SHA could hide successful tag-specific
  release qualification. Admission/finalization now select the requested tag's
  CI runs while still rejecting that tag's newer failed or pending attempt.
- A publisher workflow can succeed with its publication job skipped. The
  coordinator now requires the actual PyPI, desktop-upload and signed-upload
  job to succeed in the selected run attempt. Skipped/missing jobs cannot
  publish a GitHub Release or count as completed work on resume.
- Separately restarted artifact producers could race finalization and set a
  published release back to draft. Producers now use one verified uploader
  that never edits visibility, deletes assets or replaces names. All existing
  digests must match before the first new upload; identical retries are reused,
  conflicting bytes are rejected. SLSA generates only a workflow artifact and
  its attestation joins the signed bundle through that same uploader.

Verification of the resulting patch:

- **266 CI-script tests passed on Python 3.11** in the full directory run,
  including real temporary-Git integration tests. Two additional CLI/policy
  regression cases were added afterward and passed in the focused runs.
- **86 focused audit tests passed on Python 3.11, Python 3.12 and Python 3.14.**
- Fresh isolated environments successfully installed the gate dependencies
  and the pinned packaging tools. A small package fixture produced an sdist
  and wheel and passed `twine check --strict` on both Python 3.11 and the
  publisher's Python 3.12. This tests tooling, not the full Jarvis artifact.
- Actionlint validates all 14 workflows; workflow policy and scoped Ruff
  checks pass. The final workflow bodies also pass syntax-only parsing of
  108 Bash blocks and 4 PowerShell blocks. The normal auth gate passes with the exact light dependency
  set in a clean environment. Strict release qualification still rejects the
  same **46 missing completed E2E PASS records**.

The producer race is covered by simulations of concurrent identical/different
uploads and publication, partial retries and already-public releases. These
prove that no producer issues a visibility edit or replacement request. Real
hosted runner/signing acceptance remains necessary; local tests do not prove
the complete production release path. A partially uploaded draft rebuilt with
different bytes is intentionally rejected, rather than silently mixing builds.

No shared-repository commit, push, remote workflow run, release, or repository
setting change was performed during this follow-up.
Other sessions advanced HEAD during verification, most recently to
`c040a8feb1b0cddf56221d0aee1ff6eb9a464533`. The reviewed implementation paths
and their direct helper imports were unchanged by those commits. An unrelated
change to `scripts/ci/async-routes-baseline.json` was preserved, not rewritten
or incorporated into this audit's fixes.

## Initial audit — 2026-10-02

Status: local fixes verified; hosted build/signing acceptance remains open.
No commit, push, deployment, workflow dispatch, release, or repository-setting
change was performed.

## Coordination and scope

Pipeline audit ownership: `.github/`, `scripts/ci/`, `docs/ci-pipeline.md`, and
focused `tests/unit/ci/` regression checks. Application security and API audits own
their application changes. Concurrent changes in `packaging/linux/`,
`jarvis.spec`, and `install/installer.py` are preserved and reviewed read-only.
There is no direct messaging channel to the independent Claude sessions in
this tool environment; this file records the boundary for shared-tree review.

The local baseline is `7a8ee7b3d579237d1d8fc1aeafec6d53bb2de8b0` plus the
pre-existing shared-tree changes. HEAD remained unchanged during the audit.
GitHub main was independently inspected at
`9d3cc15313b1f507ca71703eca02f5e05f67d4a3`; it is newer than this checkout.
Findings below distinguish the two. The release-cut patch preserves upstream's
candidate-PR and resume behavior, rather than restoring the old direct-main
push. Other upstream changes were inspected without replacing shared files.
Reconcile this patch with current main before proposing a PR.

## Prioritized findings and fixes

### P1 — Test failures could become green without valid evidence

**Root cause:** `ratchet_tests.py` returned success whenever a baseline was
missing. There is no tracked macOS baseline in the audited local tree, so
every macOS failure could pass this path. Whole-file `flaky_files` exemptions
also accepted previously unseen failing test IDs. Empty report directories or
incomplete report payloads could present an empty failure set.

**Fix:** a missing baseline now means zero approved failures; file-wide
exemptions no longer waive new IDs. Empty/malformed/zero-test reports fail.
Existing exact known-failure identities remain supported; no baseline was
expanded. Summary output no longer claims that an unobserved test passed.
The same baseline and flaky-file defects exist in inspected upstream main.

References: [ratchet checks](../scripts/ci/ratchet_tests.py),
[regression tests](../tests/unit/ci/test_pipeline_audit.py).

### P1 — The single required gate accepted skipped selected jobs

**Root cause:** normal `CI gate` evaluation accepted every `skipped` result,
without considering which lanes detection selected. A missing dependency
entry was not detectable from the result object alone.

**Fix:** `--pipeline` validates detection flags and requires every selected
job to succeed. Static workflow policy verifies that the aggregate depends
on every workflow job. Legitimately unselected lanes still skip. Upstream
main retained the original permissive evaluator.

References: [expected jobs](../scripts/ci/required_results.py),
[workflow policy](../scripts/ci/check_workflow_policy.py),
[aggregate](workflows/ci.yml).

### P1 — Release admission did not establish CI provenance or fresh ancestry

**Root cause:** the local script accepted any historical successful check
named `CI gate`. Upstream already selected the latest check ID, but still
trusted the name without binding it to the CI workflow, repository, eligible
event, or run attempt. Both ignored a failed fetch, permitting stale ancestry.
Neither bound the checked-out bytes and the actual tag to the supplied SHA.

**Fix:** inspect the latest eligible CI workflow run for the exact commit,
then its attempt-specific jobs. Reject API errors, failed/pending reruns,
foreign workflows/repos, absent qualification, moved tags and failed fetches.
Release admission requires the existing browser-auth `--require-e2e-pass`
contract through a tag-only CI job. Ordinary PR/branch/nightly CI does not
inherit that release-only requirement.

References: [CI provenance](../scripts/ci/release_admit.py),
[commit binding](../scripts/ci/release_admit.py),
[qualification](workflows/ci.yml).

### P1 — Independent publishers exposed incomplete releases

**Root cause:** release-cut, desktop installers, and installer signing could
each publish/update a public GitHub Release independently. Source publication
waited for CI, not all publishers. The signed-assets job explicitly did not
wait for provenance, even though the installer verification contract consumes
it. The desktop checksum manifest could mention a Debian package that the
release upload omitted. These publication-order gaps also remain upstream.
The Debian upload omission itself has already been corrected upstream; this
patch carries that correction into the older checkout.

**Fix:** producers prepare an unpublished draft and refuse asset replacement
on published releases. Signing waits for provenance. Finalization waits for
all three publishers, including outstanding duplicate runs, validates required
nonempty assets and both checksum manifests against GitHub's stored SHA-256
digests, and verifies the signed payload commit. It uploads the source archive
and checksum, verifies their stored digests, and only then publishes. A per-tag
lock serializes release-cut and the new trusted-main finalizer. Debian assets
listed in the manifest are included in the upload.

References: [draft preparation](../scripts/ci/release_assets.py),
[asset verification](../scripts/ci/release_assets.py),
[finalization](../scripts/ci/release_assets.py),
[finalizer workflow](workflows/release-finalize.yml),
[signing/provenance](workflows/sign-installer.yml).

### P1 — Release-cut could target the wrong commit or fail branch protection

**Root cause:** local release-cut pushed an unchecked version commit directly
to protected main. Upstream had already introduced a candidate PR, but tagged
the PR head after a merge rather than selecting the resulting merge commit.
CI was dispatched against moving `main`, with macOS disabled. A new tag pushed
with an integration token plus unconditional manual publisher dispatch also
produced duplicate publishing attempts, conflicting with immutable PyPI uploads.

**Fix:** retain upstream's PR/resume flow, explicitly checkout the successful
merge commit, tag it, and dispatch full three-OS CI against that immutable tag.
Dispatch publishers only when the token did not already trigger them or when
resuming an existing tag, and do not repeat publishers already running or
successful. The version bump also updates the editable project's
`uv.lock` record and rejects repeated/lower/noncanonical versions.

References: [protected landing and dispatch](workflows/release-cut.yml),
[lock synchronization](../scripts/ci/cut_release.py).

### P1 — Native signing credentials were available too broadly; macOS import was missing

**Root cause:** Windows exported Azure signing credentials across the entire
build job, including dependency installation, and manual branch builds could
receive signing secrets. macOS passed a base64 certificate and password to a
script that never imported that certificate into a keychain; configured signing
could therefore fail on a clean runner. Installer signing granted write/OIDC
permissions globally, including dependency-audit jobs. Temporary private-key
cleanup ran only along successful signing paths.

**Fix:** native signing is tag-gated; Azure secrets are scoped to its signing
action. macOS imports the publisher certificate into a restricted temporary
keychain, restores the prior keychain search list and deletes credentials on
failure too. Certificate bytes/password are absent from the build step.
Installer workflow defaults are read-only, OIDC is limited to signing jobs,
and private-key cleanup runs with `always()`.

References: [Windows secret boundary](workflows/desktop-installers.yml),
[macOS certificate lifecycle](workflows/desktop-installers.yml),
[installer signing workflow](workflows/sign-installer.yml).
Native execution remains unverified locally; the tests inspect the workflow
contract and shell syntax, not an actual Apple or Azure signature.

### P2 — Mutable duration caches could silently omit test files

**Root cause:** every staggered shard independently restored the newest
duration cache. Another main run could publish a cache between those restores,
changing the bin-packing partition and causing duplicates and omissions. A
passed-count floor cannot prove complete file coverage.

**Fix:** detection restores once and shares one named artifact with all shards.
The artifact name is a persisted detection output, preserving it on partial
reruns. Reports record assigned files and the discovered-suite digest; the
Linux summary requires all six shards and an exact, duplicate-free partition.

References: [shared snapshot](workflows/ci.yml),
[partition check](../scripts/ci/ratchet_tests.py),
[report evidence](../scripts/ci/run_tests_parallel.py).

### P2 — Pinning and validation were inconsistent

**Root cause:** stargazer-map used floating checkout/setup-node/github-script
tags in a workflow containing a contents-write job. Release builds upgraded
unbounded build/twine tooling and used an independently resolved build backend.
There was no required workflow syntax/policy gate. Local PyPI publication also
lacked an explicit tag/admission guard; upstream already fixed the tag guard.

**Fix:** pin those actions, declare explicit publication conditions, disable
unneeded setup-node caching in the write workflow, pin direct packaging tools
and build without a second isolated backend resolution. Add a separate
Dependabot build-tooling directory, pinned Actionlint, repository workflow
policy and mutation tests. The single documented SLSA generator tag exception
remains because that upstream generator rejects SHA refs.

References: [stargazer workflow](workflows/stargazer-map.yml),
[PyPI publication](workflows/release.yml),
[build tooling](../scripts/ci/requirements-build.txt),
[policy](../scripts/ci/check_workflow_policy.py).

## Inventory and boundaries

The checkout contained 13 workflows; the patch adds `release-finalize.yml`:

| Surface | Workflows / supporting code |
| --- | --- |
| Required CI | `ci.yml`; classification, test selection, process-isolated sharding, baseline ratchet, static gates, aggregate evaluator |
| Reusable platform checks | `browser-runtime.yml`, `installer-smoke.yml`, `macos-desktop.yml`, `cross-runner-hash.yml` |
| Releases | `release-cut.yml`, `release-gate.yml`, `release.yml`, `desktop-installers.yml`, `sign-installer.yml`; release admission, version/changelog editing, completeness script |
| Automation | `merge-train.yml`, `contributors.yml`, `stargazer-map.yml`; `agent_integrate.py`, `agent_land.py`, contributor/feed generators |
| Packaging | `packaging/{windows,macos,linux}/build.*`, `jarvis.spec`, `scripts/build_frontend.py`, `scripts/prepare_browser_wheelhouse.py`, bootstrap/verifier scripts and installer trust roots |

CI covers PRs, main pushes, scheduled runs and manual dispatch. Release
publishers cover tags and manual runs; release-cut is manual only.
Contributors' privileged `pull_request_target` path checks out main after a
merge, not a PR head. The merge train deliberately executes same-repository
branch build code with write credentials; forks are excluded. Live GitHub
reported that train as **disabled_manually**, so this audit does not claim it
is currently landing changes.

Upstream also has `native-crypto-wheels.yml` and `macos-hotkey-spike.yml`, absent
from this checkout. Both were inspected read-only: read-only tokens and pinned
actions; the former builds/tests wheels on native runners, and the latter is
manual diagnostic evidence, explicitly not physical keyboard/TCC acceptance.
GitHub's registered workflow list also includes historical/dynamic workflows;
registration alone does not mean a workflow file exists on current main.

## Verification

- **238 tests passed** on Python 3.11: `python -m pytest tests/unit/ci --confcutdir=tests/unit/ci -q --tb=short`. This includes release-completeness, static-gate, sharding, temporary-Git integration and new negative regression tests. Global application fixtures are intentionally excluded from this pipeline-focused run.
- **56 audit regressions passed** on Python 3.14 with the same isolation. Release tests use fake GitHub state and temporary files; no real publisher was invoked.
- **Actionlint 1.7.12 passed** for all 14 local workflows. External ShellCheck/Pyflakes integrations were disabled; **106 Bash and 4 PowerShell run blocks** were independently parsed without execution.
- **Ruff check/format passed** on changed Python files. Workflow policy reported 14 workflows and zero findings. Private-key and agent-mirror gates passed; scoped documentation privacy scans passed.
- Direct packaging-tool requirements resolved universally for Python 3.12. `pip-audit 2.10.0` found no known vulnerabilities in the locally applicable resolved build-tool dependency set. This is not an audit of every application/platform dependency.
- A read-only check of the published **v2.7.1** release verified its 55 asset records against the new required-asset rules and both downloaded checksum manifests: **zero findings**. No large installer binary was downloaded, and no cryptographic-signature verification was claimed from this metadata check.
- The strict OAuth release gate reports **46 missing completed E2E PASS records**. Exact finding identities match the original base commit under the same interpreter; none were added or suppressed by this patch. Releases should remain blocked until those acceptance requirements are satisfied.
- Explicitly running the product-document schema checker against `docs/ci-pipeline.md` reports `frontmatter.missing` on both the exact base and patched file. That developer document is outside the check's default product-doc corpus; no frontmatter policy was changed.
- Shared-tree changes were preserved; no application probe, browser launch, native installer build, paid model call, Git write to the shared repository, remote workflow dispatch or publication was performed.

## Remaining risks / acceptance work

1. **Hosted execution is still required:** candidate PR permissions, fork/read-only token behavior, partial reruns, signing/notarization, PyPI OIDC and final publication need controlled runner acceptance after approval. No native Linux/macOS/Windows artifact was built here. Tag pushes outside the canonical release-cut flow now require full CI dispatched on that tag before admission.
2. **Release blockers are real:** the 46 existing auth-evidence gaps remain. Stricter macOS and flaky-test gates may expose failures that were previously waived. Fix those failures; do not fabricate evidence or enlarge baselines.
3. **Remote policy remains unchanged:** one required Actions `CI gate`, non-strict required checks, no required approving review, admin enforcement off, no rulesets, and a PyPI environment without reviewers or branch/tag restrictions were observed. Tightening those settings requires separate authorization. Integration-token availability was not verified; current repository policy does not allow the default Actions token to create/approve PRs.
4. **Application-security overlap:** `jarvis.spec:98` bundles the local `jarvis.toml`; native build scripts merely warn if it differs from the example. This can leak machine-specific configuration in a developer-built artifact. These already-modified files remain owned by the application audit and were not overwritten. Clean hosted runners seed the example, which narrows but does not remove the local-build risk.
5. **Reproducibility is incomplete:** application/native builds still resolve broad dependency ranges; direct build-tool pins do not pin their entire transitive graph. Runner labels and system package managers move. Cross-runner source hashes prove byte consistency, not independent-vendor compromise resistance or reproducible native binaries. Desktop checksums share GitHub's trust boundary; source-installer provenance does not attest the frozen desktop binaries.
6. **Cross-service atomicity is limited:** PyPI can complete before another publisher fails; immutable PyPI versions cannot be rolled back. The finalizer protects GitHub Release/updater visibility, not a distributed transaction across PyPI and GitHub. Recovery requires completing the same release's remaining publishers.
7. **Upstream reconciliation remains:** do not overwrite newer architecture/platform lanes when landing these local changes. The report names fixes already present upstream so they are not presented as newly discovered live vulnerabilities.

## Primary references checked

- [GitHub workflow-trigger token behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [Workflow run identity and status API](https://docs.github.com/en/rest/actions/workflow-runs)
- [Attempt-specific workflow job evidence](https://docs.github.com/en/rest/actions/workflow-jobs)
- [SLSA v2.1.0 draft-release input](https://github.com/slsa-framework/slsa-github-generator/blob/v2.1.0/.github/workflows/generator_generic_slsa3.yml)
- [Actionlint releases and usage](https://github.com/rhysd/actionlint/releases)
- [GitHub CLI upload replacement semantics](https://cli.github.com/manual/gh_release_upload)
