# CI/CD pipeline

How a change travels from a coding agent's branch to a user's machine. The
design borrows the strongest ideas of the
[Hermes Agent pipeline](https://github.com/NousResearch/hermes-agent/tree/main/.github/workflows)
and adapts them to this repository: several coding agents working in
parallel, a Python + TypeScript desktop app, three operating systems, and
signed installers.

```
agent branch ──► pull request ──► CI (lanes) ──► CI gate ──► merge train ──► main
                                                                              │
     release-cut (manual) ──► tag ──► release gate ──► PyPI / installers / signatures
                                                   └─► draft assets ──► finalize ──► updater
```

## 1. CI — `.github/workflows/ci.yml`

| Stage | What it does | Script |
| --- | --- | --- |
| `detect` | Classifies the diff into lanes. Fails open: an empty diff, a pipeline change, the nightly run and a manual run turn every lane on. A push to main classifies its lanes too (the concurrent-job limit is shared), but always runs the whole test suite on Linux and Windows. | `scripts/ci/classify_changes.py` |
| `static gates` | ~20 repository gates (keys, bundle, mirrors, privacy, docs, CLI coverage, ratchets, bash 3.2, no new German) in one job. Every gate reports. | `scripts/ci/run_gates.py` |
| `python contracts (fast)` | Import cleanliness on the bare install, the named contract guards, skill-routing precision and recall, plugin auth. Blocking, no baseline. | — |
| `tests linux 1..6` | The whole suite in six shards. Batches of files run in fresh processes with a wall-clock budget; a failed batch is re-run file by file, a failed file once more (a pass there is reported as flaky). | `scripts/ci/run_tests_parallel.py` |
| `tests windows` | Four shards on full runs; on a pull request one runner takes only the tests the diff can reach. | `scripts/ci/select_tests.py` |
| `tests macos 1..3` | Nightly and manual runs only (~10x runner cost). | — |
| `test report + floor` | Proves the six Linux shards cover every discovered file exactly once, enforces the min-passed floor, and on main refreshes the duration cache. Detection freezes one shared duration snapshot for all shards, including partial reruns. | `scripts/ci/ratchet_tests.py` |
| Lanes | `frontend`, `jarvisctl`, `deps`, `realtime` (3 OS + slim container), `updater` (3 OS: in-app update, native handover, restart helper), `dragdrop`, `browser`, `macOS desktop`, `installer smoke` — each only when its paths change. | — |
| `release qualification` | Tag CI requires a full run with macOS and the browser-auth E2E evidence gate: a plugin labeled verified needs a completed journey, every completed journey ships as verified, and every other plugin ships labeled preview. The step summary counts both. Ordinary branch/PR/nightly CI skips this release-only job. | `scripts/ci/check_plugin_auth_contract.py --require-e2e-pass` |
| `CI gate` | Aggregates every job. **The only required check.** A missing or skipped selected lane fails. Unselected lanes may skip; nightly is strict except event-specific jobs. | `scripts/ci/required_results.py` |

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
newest run. A run on main is never cancelled once it started; pushes to main
share one queue slot, so a burst of pushes leaves one pending run that covers
every commit before it instead of a backlog behind the organisation's
concurrent-job limit. The nightly run and manual runs have their own slots.

## 2. Integrating several agents — `.github/workflows/merge-train.yml`

Agents push branches named `codex/…`, `claude/…`, `agent/…`, `gemini/…`,
`cursor/…` or `bot/…`, or add the `auto-merge` label to any pull request. The
merge train runs on every push to main, after every CI run, and every 15
minutes:

1. A pull request that **conflicts** with main (or whose CI failed while it
   was behind) gets main merged into it (`scripts/ci/agent_integrate.py`).
   No force push, so an agent that keeps pushing to its branch is never
   overwritten. A pull request that is merely behind is left alone: with
   several agents pushing to main, re-testing every PR on every push meant
   nothing ever landed.
2. Conflicts resolve by file class: **generated** files (frontend `dist/`,
   agent mirrors, CLI reference docs, lockfiles, timing caches) take main's
   copy and are regenerated; **append-only** files (CHANGELOG, allowlists,
   `.gitignore`, `docs/BUGS.md`) are union-merged; **everything else** goes
   to the optional AI resolver (`ANTHROPIC_API_KEY` → Claude Code,
   `OPENAI_API_KEY` → Codex CLI). Its result must leave no conflict markers
   and must still parse, and CI re-runs on it before anything lands. What
   stays unresolved aborts cleanly, gets the `needs-rebase` label and a
   comment listing the files. `git rerere` replays recorded resolutions.
3. The first green, conflict-free pull request is squash-merged, one per
   tick. Main's full post-merge test run is the backstop for changes that
   pass alone and break together — GitHub's non-strict model, as in Hermes.

Opt out with `no-auto-merge`, `do-not-merge`, `wip` or `needs-human`.
`priority` moves a pull request to the front. Forks and Dependabot never ride
the train.

**Token.** With the optional `INTEGRATION_TOKEN` secret (a fine-grained token
with contents and pull-request write access), the train's pushes and merges
fire the normal events. Without it the train uses `GITHUB_TOKEN`: the
pull-request run its own push triggers waits in "action required" and the
train approves it on the next tick, because GitHub never counts a dispatched
run for a pull request. After each merge it dispatches `ci.yml` for main.

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
  (`scripts/ci/cut_release.py`), lands the candidate through a CI-checked PR,
  tags the resulting merge commit, and dispatches full CI with macOS on that
  immutable tag. It dispatches publishers when using `GITHUB_TOKEN`; a new
  tag pushed with the optional integration token already starts them, so no
  duplicate PyPI publication is dispatched. Resume support accepts an already
  merged version commit. Repository PR policy may require the integration
  token or a maintainer to open the candidate PR.
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
  publisher and `release-finalize.yml` share a per-tag lock. Finalization can
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

PyPI publishing retains a separate OIDC-only job and can run only after tag
admission. Manual branch runs build packages without publishing or signing.
Native macOS signing imports the publisher certificate into a temporary
keychain and cleans it up even when the build fails. Native OS build and
signing proof still requires hosted runners; local syntax checks cannot prove it.

## 4. Adapted from Hermes, and what was left out

| Hermes idea | Here |
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
