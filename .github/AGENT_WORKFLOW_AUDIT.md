# Coding-agent workflow audit

Snapshot: 2026-09-27, `PersonalJarvis/PersonalJarvis`, `main`. This records the
live GitHub API state, not a remembered setting. Re-read protection before any
change. GitHub documents the [status-check protection API](https://docs.github.com/en/rest/branches/branch-protection#update-status-check-protection)
and warns that a required workflow skipped by a path filter can remain
[pending](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs).

## Current contract and cost

| Mechanism | Protection | Cost or failure mode | Decision |
| --- | --- | --- | --- |
| Commit hook: knowledge mirrors, added German, private keys, withheld paths, staged bundle | Prevents inconsistent agent guidance, privacy leaks, and a broken shipped WebView | Diff-scoped and cheap; CI repeats the irreversible checks | Keep |
| Push hook: credentials and private keys | Stops a public leak before network transfer | About two seconds; other whole-tree gates were already removed from this hook | Keep; no full-tree test here |
| Required `privacy-gate`, `repo-hygiene`, `language-policy` | Secrets, public identity, keys, bundled assets, English policy, agent mirrors | Fast and tied to shipped safety | Keep |
| Required `frontend (test + production build)` | Tests, production compilation, entry bundle budget | At audit start, main had 19 stale test failures; merged PR #208 repaired them while preserving independent build and bundle-budget checks | Keep strict; run build even if tests fail so both results appear |
| Required `test (ubuntu-latest)` | Full Python suite, import check, routing and voice guards | Around two hours; the old minimum-passed floor let new individual failures disappear inside a red baseline or a green count | Make the suite's own failure visible; require an independent fast four-contract job instead |
| `jarvisctl unit` with nine repository-wide policy scripts before its tests | API metadata, config wiring, silent handlers, async routes, WebGL cleanup, generated references | Existing drift in unrelated route files turns a healthy CLI unit suite red before it runs | Put policy scripts in their own required job; findings in changed files block while unchanged baseline findings remain visible |
| Other CI jobs: dependency portability, realtime, docs privacy, desktop installers | Platform, package, and release evidence | Some are slow or need specialized runners; they remain visible and are mandatory when the change affects their contract | Keep their checks; let the agent select extra local proof |
| PR template and fixed T1/T2/T3 rules | Encourage test evidence and cross-platform care | Blanket commands and automatic escalation turn a small patch into unrelated cleanup | Require a reasoned scope and named evidence instead |

The live branch has **no repository rulesets**, no required approving review,
`strict=false`, conversation resolution enabled, and force pushes/deletions
disabled. Admin enforcement is off. A draft PR is reviewable work, not a merge
request. Agents may merge ordinary authorized PRs after required checks and
scoped review; releases still require explicit authorization.

## Required-check transition

The **five live required check names stay the same**, including GitHub Actions
`app_id=15368` and `strict=false`. No GitHub settings write is needed, so an
in-flight PR without the new workflow cannot be stranded by a missing check.
The workflow changes what the existing `test (ubuntu-latest)` check proves:

| Required check | Before | After this workflow merges |
| --- | --- | --- |
| `repo-hygiene`, `privacy-gate`, `language-policy` | Required | Required, unchanged |
| `frontend (test + production build)` | Required; a test failure skips build | Required; test, build, and bundle budget all report their results |
| `test (ubuntu-latest)` | Two-hour broad suite with a passed-count floor | Fast aggregator; passes only if the four core contracts and exact-base code policy pass |

The two-hour Linux/Windows matrix moves to `broad suite (...)` and retains a
red result for failing tests. The fast jobs have their own visible check
results and feed the existing required context. A failed or skipped dependency
therefore makes `test (ubuntu-latest)` fail. This keeps branch protection
effective throughout the transition and avoids an admin bypass.

The full Python suite becomes a **red advisory signal**, not a silent success:
its pytest step returns failure and the minimum-passed floor runs even after
that failure. The separate policy job fails if a changed file has a static
finding; unchanged findings are logged against the exact base. Before merging,
compare each red advisory job with the exact PR base commit under
the same dependencies and runner. Record failing test identities and causes;
counts or a generic “pre-existing” label do not suffice. New or unexplained
failures block the PR. A required check is never waived because main is red.
Never add path filtering to the required workflow: GitHub may leave its check
pending when the workflow is skipped.

## Representative decisions

- **T1 UI:** a layout fix needs focused component tests, production build,
  light/dark inspection, and a strict bundle budget. The 19 baseline failures
  affecting PR #206 were repaired by merged PR #208; no waiver is proposed here.
- **T2 runtime:** a desktop startup fix needs its focused behavior tests and a
  boot/lifecycle check. Whole-tree async-route drift reports from `code policy`
  without being mislabeled as a CLI unit failure; compare it to exact base.
- **T3 contract:** a shared provider, credential, or OS change needs affected
  contract families and platform proof. The four core guards always run in the
  fast required job. A new key path also needs one-key fresh-install proof;
  unrelated schema edits do not.
