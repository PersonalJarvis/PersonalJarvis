# CI/CD pipeline

How a change travels from a coding agent's branch to a user's machine. The
design fits this repository: several coding agents working in parallel, a
Python + TypeScript desktop app, three operating systems, and signed
installers.

```
agent branch ──► pull request ──► CI (lanes) ──► CI gate ──► merge train ──► merge queue
                                                                                  │
                                          main ◄── CI gate (merge_group, full) ◄──┘
                                            │
     release-cut (manual) ──► tag ──► release gate ──► PyPI / installers / signatures
                                                   └─► draft assets ──► finalize ──► updater
```

## 1. CI — `.github/workflows/ci.yml`

| Stage | What it does | Script |
| --- | --- | --- |
| `detect` | Classifies the diff into lanes. Fails open: an empty diff, a pipeline change, the nightly run and a manual run turn every lane on. A merge-queue group (`merge_group`, classified against the queue base) and a push to main classify their lanes too (the concurrent-job limit is shared), but always run the whole test suite on Linux and Windows. | `scripts/ci/classify_changes.py` |
| `static gates` | ~20 repository gates (keys, bundle, mirrors, privacy, docs, CLI coverage, ratchets, bash 3.2, no new German) in one job. Every gate reports. | `scripts/ci/run_gates.py` |
| `zizmor` / `dependency review` | Workflow security audit on every event (merge queue included, exceptions in `.github/zizmor.yml`); dependency review on pull requests only, failing on an added dependency with a high or critical advisory. `security.yml` keeps the Security-tab uploads. | — |
| `live third-party sites` | Nightly only and non-blocking: the opt-in live checks of pages the app parses (`JARVIS_LIVE_NETWORK_TESTS=1`, ollama.com's library). A site redesign shows as a failed step and a run warning, never as a red gate. | — |
| `python contracts (fast)` | Import cleanliness on the bare install, the named contract guards, skill-routing precision and recall, plugin auth. Blocking, no baseline. | — |
| `tests linux 1..6` | The whole suite in six shards. Batches of files run in fresh processes with a wall-clock budget; a failed batch is re-run file by file, a failed file once more (a pass there is reported as flaky). | `scripts/ci/run_tests_parallel.py` |
| `tests windows` | The whole suite in four shards on every run with the python lane, pull requests included, so Windows-only failures block before the merge. | — |
| `tests macos 1..3` | Nightly and manual runs only (~10x runner cost). | — |
| `test report + floor` | Proves the six Linux shards cover every discovered file exactly once, enforces the min-passed floor, and on main refreshes the duration cache. Detection freezes one shared duration snapshot for all shards, including partial reruns. | `scripts/ci/ratchet_tests.py` |
| Lanes | `frontend`, `jarvisctl`, `deps`, `realtime` (3 OS + slim container), `updater` (3 OS: in-app update, native handover, restart helper), `dragdrop`, `browser`, `macOS desktop`, `installer smoke` — each only when its paths change. | — |
| `release qualification` | Tag CI requires a full run with macOS and the browser-auth E2E evidence gate: a plugin labeled verified needs a completed journey, every completed journey ships as verified, and every other plugin ships labeled preview. The step summary counts both. Ordinary branch/PR/nightly CI skips this release-only job. | `scripts/ci/check_plugin_auth_contract.py --require-e2e-pass` |
| `CI gate` | Aggregates every job. **The only required check**, on the pull request and again on its merge-queue group. A missing or skipped selected lane fails. Unselected lanes may skip; nightly is strict except event-specific jobs. | `scripts/ci/required_results.py` |

The realtime lane runs the subscription authentication, direct reasoning,
voice transport, session orchestration, native login provisioning and Live catalog contracts on Windows,
macOS and Linux, including the slim container without system audio. These
focused tests are strict: they do not use the broad-suite failure baseline.
Their fake credentials and transports also prove that a selected subscription
cannot fall through to a retained API-key provider when its account is absent
or unavailable. They make no paid inference calls. Real-account voice tests
remain separate acceptance evidence.

### Known failures: the ratchet

The suite carries a backlog of failures, mostly platform-specific. They are
listed per OS in `scripts/ci/test-baseline-<os>.json`. A
failure in the list is reported and never blocks; any other failure blocks
the change that introduced it. Whole-file flaky metadata cannot waive a new
test failure. The list only shrinks: entries not observed in a report need
proof that the test passed rather than skipped before removal. Regenerate a
list from a full run with:

```bash
gh run download <run-id> -p 'tests-linux-*' -D reports
python scripts/ci/ratchet_tests.py update --out scripts/ci/test-baseline-linux.json reports
```

Shrink a list with proof instead of by hand: download the `tests-<os>-*`
artifacts of several full runs on main, one directory per run, and drop every
entry that passed (not skipped) in all of them:

```bash
for id in <run-1> <run-2> <run-3>; do gh run download "$id" -p 'tests-linux-*' -D "runs/$id"; done
python scripts/ci/ratchet_tests.py prune --baseline scripts/ci/test-baseline-linux.json --os linux runs/*
```

A missing list means no failures have been approved for that OS; new failures
block immediately. Missing, empty or malformed reports also block.

### Flaky tests

A file that fails inside its batch and passes when re-run alone is reported
as flaky. That almost always means test-order dependence, and the ratchet does
not block on it, so it is tracked instead. `test report + floor` lists flaky
files and new versus known failures in its step summary on every run. After
each push, nightly and manual CI run on main, `.github/workflows/flaky-tests.yml`
does the same for every OS and keeps one issue, "Flaky tests on main", updated
with a rolling table (test file, OS, number of runs it was flaky in, last seen
run). Rows age out after 30 days without a sighting. That workflow is the only
place with `issues: write`; it never runs for pull requests and refuses any CI
run that is not a finished run of this repository's main branch. Record an
older run by hand with
`gh workflow run flaky-tests.yml -f run_id=<ci-run-id>`; a run already
recorded is never counted twice. Logic and tests: `scripts/ci/flaky_report.py`,
`tests/unit/ci/test_flaky_report.py`.

Static gates include repository workflow policy (immutable action pins, the
complete aggregate dependency graph, tag-only PyPI publication and draft-only
asset producers). CI additionally runs pinned Actionlint for workflow syntax,
expressions and action inputs.

### Dispatch-only evidence runs

`.github/workflows/macos-hotkey-spike.yml` runs only on `workflow_dispatch`: it is
in no lane, no schedule and not in the `CI gate`. It runs
`scripts/ci/macos_carbon_hotkey_spike.py` on an Intel and an Apple Silicon macOS runner
(optionally `macos-26`, whose label for this repository is unverified). The script
records the TCC context first, then runs each risky Carbon `RegisterEventHotKey`
variant in a child process so a native crash becomes data, not a failed job. It is
runner evidence only: runners pre-grant TCC to their tools, show no dialog and have no
physical keyboard, so its result never decides a default on its own (the flip rule is
in `macos-permissions.md`, section 4.15). It ran once from the feature branch as run
`36954304202` at commit `59749f859` on `macos-15` (arm64) and `macos-15-intel` (harness green;
the recorded result and its limits are in `macos-permissions.md`, section 4.15).

### Concurrency

A pull request keeps only its
newest run. Every merge-queue entry has its own slot and is never cancelled:
the queue drops entries it no longer needs itself, and a dropped pending run
would leave `CI gate` unreported until the queue times out. A run on main is
never cancelled once it started; pushes to main share one queue slot, so a
burst of pushes leaves one pending run that covers every commit before it
instead of a backlog behind the organisation's concurrent-job limit. The
nightly run and manual runs have their own slots.

## 2. Integrating several agents — merge queue + `.github/workflows/merge-train.yml`

`main` requires a **merge queue** (repository ruleset "main merge queue":
squash, `CI gate` required). A pull request's own CI is its admission ticket;
the queue then builds each entry on top of the newest main plus every entry
ahead of it, runs `ci.yml` on that exact tree (`merge_group` event, whole
Linux + Windows suite), and squash-merges only when `CI gate` is green there.
Changes that pass alone and break together stop in the queue instead of on
main. A failed entry leaves the queue; the entries behind it are rebuilt
without it.

Agents push branches named `codex/…`, `claude/…`, `agent/…`, `gemini/…`,
`cursor/…` or `bot/…`, or add the `auto-merge` label to any pull request. The
merge train runs on every push to main, after every CI run (pull request or
merge queue), and hourly as a safety net:

1. A pull request that **conflicts** with main (or whose CI, or merge-queue
   run, failed while it was behind) gets main merged into it
   (`scripts/ci/agent_integrate.py`). No force push, so an agent that keeps
   pushing to its branch is never overwritten. A pull request that is merely
   behind is left alone: the queue re-tests it on the newest main anyway.
2. Conflicts resolve by file class: **generated** files (frontend `dist/`,
   agent mirrors, CLI reference docs, lockfiles, timing caches) take main's
   copy and are regenerated; **append-only** files (CHANGELOG, allowlists,
   `.gitignore`, `docs/BUGS.md`) are union-merged; **everything else** goes
   to the optional AI resolver (`ANTHROPIC_API_KEY` → Claude Code,
   `OPENAI_API_KEY` → Codex CLI, both pinned to exact versions). Its result
   must leave no conflict markers and must still parse, and CI re-runs on it
   before anything lands. What stays unresolved aborts cleanly, gets the
   `needs-rebase` label and a comment listing the files. `git rerere`
   replays recorded resolutions.
3. Every green, conflict-free pull request joins the merge queue, pinned to
   the head commit its CI tested. A head the queue already failed is held
   until a new push or a train update. Without a queue on main (the
   rollback path: delete or disable the ruleset) the train squash-merges one
   green pull request per tick instead.

Opt out with `no-auto-merge`, `do-not-merge`, `wip` or `needs-human`.
`priority` moves a pull request to the front. Forks and Dependabot never ride
the train.

**Trust.** The train is three jobs with three privilege levels. `plan` has a
read-only token and no secrets and runs no pull-request code. `integrate` has
a read-only token: it merges main into each branch in its own worktree, gives
the AI resolver its API keys in one step that runs before any pull-request
code on that machine, then runs the regenerators (pull-request code) and
hands the result on as git bundles. `apply` holds the write token, runs no
pull-request code, and only pushes bundles that build on the planned head and
main, labels, comments, approves parked runs and enqueues. No checkout
persists a token.

**Token.** With the optional `INTEGRATION_TOKEN` secret (a fine-grained token
with contents and pull-request write access), the train's pushes fire the
normal events. Without it the train uses `GITHUB_TOKEN`: the pull-request run
its own push triggers waits in "action required" and the train approves it
on the next tick, because GitHub never counts a dispatched run for a pull
request. The merge queue's own merges fire the normal push to main.

**Rollback.** If the queue stalls, disable the "main merge queue" ruleset
(Settings → Rules → Rulesets, or `gh api -X PUT
repos/<owner>/<repo>/rulesets/<id> -f enforcement=disabled`). The train sees
no queue on its next tick and merges directly again.

### Locally: `scripts/agent_land.py`

An agent in its own worktree runs one command when its work is done:

```bash
python scripts/agent_land.py            # feature branch -> PR labelled auto-merge
python scripts/agent_land.py --direct   # fast-forward main itself
```

It rebases onto the latest main with the same conflict engine, runs the
static gates, runs the tests the diff can reach, and pushes. It refuses a
dirty worktree and never stashes or force-pushes.

## 3. Releases, installers and updates

A release happens **only** when the maintainer asks for one.

* **`release-cut.yml`** (manual): refuses unless main is green, bumps
  `pyproject.toml` + `jarvis/__init__.py` + the root package in `uv.lock`, moves the `[Unreleased]` notes (or
  the Conventional Commits since the last tag) into a dated CHANGELOG section
  (`scripts/ci/cut_release.py`), lands the candidate through a CI-checked PR
  and the merge queue, tags the landed commit, and dispatches full CI with macOS on that
  immutable tag. Its `cut` job runs in the `release-cut` environment, which
  only `main` may deploy to and which holds the private key of the release
  bot GitHub App (secret `RELEASE_APP_PRIVATE_KEY`, variable
  `RELEASE_APP_ID`). Right before each write (candidate push and PR, merge,
  tag push) the job mints a fresh installation token with
  `actions/create-github-app-token`; CI waits use `GITHUB_TOKEN`, because an
  installation token lasts one hour. Bot pushes fire the normal events: the
  candidate PR runs CI by itself and the new tag starts the publishers, so no
  duplicate PyPI publication is dispatched. Without the bot
  (`RELEASE_APP_ID` unset) the job falls back to `GITHUB_TOKEN`, dispatches
  the publishers itself, and a maintainer must open the candidate PR, since
  `GITHUB_TOKEN` may not open pull requests here. Resume support accepts an
  already merged version commit.
* **`release-gate.yml`** is the first job of `release.yml` (PyPI),
  `desktop-installers.yml` and `sign-installer.yml`. It admits a tag only
  when tag, versions and CHANGELOG agree, the commit is on main, and
  the latest trusted CI workflow run for that tag passed on that exact commit, including
  `CI gate` and `release qualification` (`scripts/ci/release_admit.py`, waiting
  while CI still runs). A separate main run cannot replace this tag's evidence.
  Tag CI is excluded from branch-run cancellation. The checkout, tag and SHA must identify the same commit;
  failure to fetch main cannot fall back to stale ancestry.
* The GitHub Release (notes + the resumable `personal-jarvis-src.tar.gz`) is
  staged as a draft. `release_assets.py` waits for all three publishers and
  verifies each actual publication job in its latest attempt; workflow success
  with a skipped publication job is insufficient. It
  verifies required assets and GitHub's stored digests against both checksum
  manifests, checks the signed payload commit, uploads the source archive and
  its checksum, and verifies those uploads before publishing. The release-cut
  `publish` job is the normal finalizer. `release-finalize.yml` shares its
  per-tag lock but only fires on its own (`workflow_run`) when a person or the
  release bot started the publishers: publisher runs dispatched with
  `GITHUB_TOKEN` raise no `workflow_run` event, so without the bot it
  stays a manual retry path. Finalization can
  also be retried manually from main with an existing draft tag. Producers
  never edit release visibility or replace existing asset names. Before an
  upload, the complete staged set must match any files already present;
  identical files are reused, conflicting bytes fail before new writes.
  A same-name upload conflict is accepted only after verifying identical
  stored bytes. Provenance generation produces a workflow artifact and uses
  this same uploader, so a publisher retry cannot revert visibility to draft.
  Re-run failed jobs with their original build artifacts to recover a partial
  upload; a full rebuild with different bytes requires an explicit decision.
  The in-app updater follows `releases/latest`; draft releases remain hidden.
* **Build provenance.** On a tag, `desktop-installers.yml` attests every
  native installer listed in `installers-SHA256SUMS.txt` (`.exe`, both
  `.dmg`, `.AppImage`, `.deb`) with `actions/attest-build-provenance` before
  the upload. Anyone can check a download:

  ```bash
  gh attestation verify PersonalJarvis-Setup-x64.exe -R PersonalJarvis/PersonalJarvis
  ```

  Releases up to and including v2.9.0 predate this and have no attestation.
* **`release-smoke.yml`** (read-only) checks a published release the way a
  user meets it: the complete asset set and all manifests against GitHub's
  stored digests, each OS's installers downloaded and matched against
  `installers-SHA256SUMS.txt` plus their attestation, a container check
  (`dpkg-deb`, ELF header, `hdiutil verify`; Authenticode and Gatekeeper are
  reported, not required), and the released `install-verify.sh` /
  `install-verify.ps1` in dry-run, no-launch mode on Linux, Windows and
  macOS (`release-wrapper-smoke.yml`, which `installer-smoke.yml` also runs on
  Linux). Release cut calls it after publishing and requires attestations; a
  release published with `GITHUB_TOKEN` fires no `release` event. An
  attestation lookup that keeps getting a server error (HTTP 5xx, retried
  twice) fails only when attestations are required; otherwise it is a
  warning, like a missing attestation on an older release. Re-check any
  tag with `gh workflow run release-smoke.yml -f tag=vX.Y.Z`.

### When a release is bad: roll forward

PyPI versions and published release assets are immutable, so there is no
"undo". A rollback is a new, higher patch release. What the updater does
decides the order of steps:

* Every install path follows GitHub's **Latest** release
  (`releases/latest`): the native installers' updater, the one-line
  installer's managed checkout, and the website download buttons
  (`releases/latest/download/<name>`). A running app caches the answer for
  30 minutes.
* An update is offered only when Latest is **strictly newer** than the running
  version (`_is_newer` in `jarvis/ui/web/update_routes.py`). Nothing ever
  downgrades. A user already on the bad version leaves it only when a newer
  version is published.
* A native update downloads the installer from that same release and refuses
  it unless it matches that release's `installers-SHA256SUMS.txt`
  (`jarvis/core/installer_update.py`).

Steps:

1. **Confirm.** Reproduce the problem and run
   `gh workflow run release-smoke.yml -f tag=vX.Y.Z` to see whether the
   artifacts themselves are broken.
2. **Stop the spread (optional, minutes).**
   `gh release edit vX.Y.Z -R PersonalJarvis/PersonalJarvis --prerelease`
   takes the release out of Latest, so Latest falls back to the previous
   release. New downloads and apps on older versions stop receiving the bad
   one (running apps within their 30-minute cache). Users already on it are
   not downgraded. Check the result with
   `gh api repos/PersonalJarvis/PersonalJarvis/releases/latest --jq .tag_name`.
3. **Fix forward.** Land the revert or fix on main through a normal PR, then
   cut a patch: `gh workflow run release-cut.yml -f bump=patch`. The new
   version is newer than both the bad and the previous release, becomes
   Latest, reaches every install path, and is smoke-tested by the cut.
4. **PyPI (optional).** A maintainer may *yank* the bad version on pypi.org.
   pip then skips it unless someone pins that exact version. Never delete it:
   the number can never be reused anyway.
5. **Leave the evidence.** Do not delete the bad release, its assets or its
   tag, and never re-tag. Installs pinned with `JARVIS_INSTALL_TAG`, the
   signed manifests and the attestations all point at them. Add a short note
   to the release body instead.

**Release environments.** Two GitHub environments guard the irreversible
steps: `pypi` (the PyPI upload in `release.yml`) and `release-signing` (the
`sign` job of `sign-installer.yml`, the only job that reads the offline
Ed25519 and ML-DSA-65 private keys, `WAVE2_OFFLINE_KEY_B64` and
`WAVE4_MLDSA65_KEY_B64`). Both admit deployments from `v*` tags only, never
from a branch, and administrators cannot bypass that policy. Neither has
required reviewers: a release runs from `release-cut.yml` to the published
GitHub Release without an approval pause or a **Review deployments** click.
The signing keys and the PyPI upload are therefore reachable only from a
workflow run on a `v*` tag.

**Release tags.** The `release tags` repository ruleset protects
`refs/tags/v*` against creation, update and deletion. Only the release bot
GitHub App (the `release-cut` job's installation token, app id in
`RELEASE_APP_ID`) and the repository admin role may bypass it, so only a
release cut or an administrator can create a `v*` tag. A tag created that way
still passes release admission (`release-gate.yml`) before anything is
published.

PyPI publishing retains a separate OIDC-only job and can run only after tag
admission. Manual branch runs build packages without publishing or signing.
Native macOS signing imports the publisher certificate into a temporary
keychain and cleans it up even when the build fails. Native OS build and
signing proof still requires hosted runners; local syntax checks cannot prove it.

## 4. Design choices, and what was left out

| Idea | Here |
| --- | --- |
| Orchestrator + change classifier, fail-open lanes | `detect` + `classify_changes.py` |
| One aggregate required check (`all-checks-pass`) | `CI gate` |
| Per-file process isolation, duration cache written only by main | `run_tests_parallel.py`, batched to amortise this suite's start-up cost |
| Only PR runs are cancelled; main and release never | `concurrency` block |
| Strict mode where a skipped lane fails | nightly run |
| Unrelated-history check | `history` gate |
| Stable release admits a claim before building | `release-gate.yml` |
| Autofix PRs with a privileged/unprivileged split | not adopted: generated files are regenerated during integration instead |
| 96-core runners, daily canary tags, Docker/Nix lanes | not adopted: standard runners, releases stay manual, no such artefacts |

## 5. Security scans — `security.yml`, `scorecard.yml`

All free for public repositories, all report into the Security tab beside
CodeQL's default setup, and none is part of the required `CI gate`:

* **zizmor** audits the workflow files on every pull request and push to
  main. Accepted exceptions live in `.github/zizmor.yml`, each with its reason;
  run `zizmor --config .github/zizmor.yml .github/workflows` locally before
  touching a workflow. Expressions reach a `run:` block through `env:`, never
  inline, and a checkout keeps its credential only when that job pushes.
* **dependency-review** fails a pull request that adds a dependency with a
  known high or critical advisory.
* **osv-scanner** checks the shipped lockfiles against osv.dev on main and
  weekly. It reports and never blocks.
* **OpenSSF Scorecard** publishes a weekly repository security score.

Dependabot covers npm through security updates only. Version updates for the
frontend stay off for the same reason as pip: a bump also needs a rebuilt
`dist/` bundle, which Dependabot cannot produce.
