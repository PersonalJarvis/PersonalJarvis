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
                                                   └─► GitHub Release ──► in-app updater
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
| `test report + floor` | Sums all Linux shards, enforces the min-passed floor, lists baselined failures that now pass, and on main refreshes the per-file duration cache that balances the shards. | `scripts/ci/ratchet_tests.py` |
| Lanes | `frontend`, `jarvisctl`, `deps`, `realtime` (3 OS + slim container), `dragdrop`, `browser`, `macOS desktop`, `installer smoke` — each only when its paths change. | — |
| `CI gate` | Aggregates every job. **The only required check.** Skipped lanes pass; the nightly run is strict and fails on any skip. | `scripts/ci/required_results.py` |

### Known failures: the ratchet

The suite carries a backlog of failures, mostly platform-specific. They are
listed per OS in `scripts/ci/test-baseline-{linux,windows,macos}.json`. A
failure in the list is reported and never blocks; any other failure blocks
the change that introduced it. The list only shrinks: `test report` names the
entries that pass again. Regenerate a list from a full run with:

```bash
gh run download <run-id> -p 'tests-linux-*' -D reports
python scripts/ci/ratchet_tests.py update --out scripts/ci/test-baseline-linux.json reports
```

A missing list puts that OS in report-only mode until the first full run
produces one.

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
  `pyproject.toml` + `jarvis/__init__.py`, moves the `[Unreleased]` notes (or
  the Conventional Commits since the last tag) into a dated CHANGELOG section
  (`scripts/ci/cut_release.py`), commits, dispatches CI, tags, and dispatches
  the three publishing workflows on the tag.
* **`release-gate.yml`** is the first job of `release.yml` (PyPI),
  `desktop-installers.yml` and `sign-installer.yml`. It admits a tag only
  when tag, versions and CHANGELOG agree, the commit is on main, and
  `CI gate` passed on that exact commit (`scripts/ci/release_admit.py`,
  waiting while CI still runs).
* The GitHub Release (notes + the resumable `personal-jarvis-src.tar.gz`) is
  published after the same admission. The in-app updater follows
  `releases/latest`, so a release reaches managed installs and installer
  users only after main's CI proved it.

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
