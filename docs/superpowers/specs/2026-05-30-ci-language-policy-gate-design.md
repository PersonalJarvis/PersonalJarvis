# CI Language-Policy Gate — "no new German reaches GitHub"

**Date:** 2026-05-30
**Status:** Implemented
**Owner:** maintainer

## Problem

CLAUDE.md declares an Output Language Policy (HIGHEST PRIORITY): every committed
artifact must be English. Until now this was a *documented* rule with no
*technical* enforcement — German text could (and did) slip into commits and reach
GitHub. The maintainer asked to "anchor it in the project so it cannot happen
again."

Constraint: the repo is **mid-migration** (a DE→EN translation of ~148 `.md` +
~213 `.py` files is in flight). A naive "no German anywhere" check would be red
from day one because of the existing, not-yet-translated backlog — exactly the
failure mode the existing `ruff`/`mypy` CI steps avoid via `continue-on-error`.

## Decision

A **diff-based, blocking CI check**: it inspects only the lines a push/PR *adds*
(the `+` lines), never pre-existing German. New German turns the merge red;
the translation backlog is left alone. This was chosen over a local pre-push hook
(per-machine only; the agent swarm in other worktrees would bypass it) and over a
whole-repo scan (red-from-day-one during the migration).

## Components

| File | Purpose |
|---|---|
| `scripts/ci/_german_detect.py` | `looks_german(text)` heuristic: umlaut → German; one "strong" token → German; two distinct "weak" tokens → German. Whole-word matching avoids substring false positives. |
| `scripts/ci/check_no_new_german.py` | The gate: resolves the diff base, parses added lines (`git diff --unified=0 <base>...HEAD`), filters by extension + allowlist + inline escape, reports violations, exits non-zero. |
| `scripts/ci/german-allowlist.txt` | fnmatch globs for paths where German is allowed (persona/soul prompts, translation tooling, `i18n`/locale sources, wiki session logs, the gate's own tests). |
| `.github/workflows/ci.yml` → job `language-policy` | Runs the gate once (not across the OS matrix), `fetch-depth: 0`, stdlib + git only. BLOCKING. |
| branch protection on `main` | `language-policy` added as a required status check so a red gate actually blocks the merge. |

## Diff-base resolution (most specific first)

1. explicit `<base>` CLI arg;
2. GitHub `pull_request` event → `pull_request.base.sha`;
3. GitHub `push` event → event `before` SHA;
4. `origin/$GITHUB_BASE_REF` if set;
5. local fallback `origin/main`.

Using concrete SHAs (2, 3) keeps the gate robust under `actions/checkout` where
`origin/*` tracking refs may be absent.

## Escape hatches (so a heuristic false positive never blocks work)

- **Inline:** a line containing `i18n-allow` is skipped (for a single
  intentionally-German line, e.g. a test fixture).
- **Per file:** add a glob to `scripts/ci/german-allowlist.txt`.

## Non-goals (YAGNI)

- No automatic translation.
- Commit *messages* are not scanned (file contents only).
- Existing German is not touched — pure regression guard.

## Testing

`tests/unit/ci/test_check_no_new_german.py` (21 cases): heuristic
true/false-positives (incl. English false friends `die`/`war`, English code,
ASCII-transliterated German), allowlist glob matching + path normalisation,
extension filtering, unified-diff parsing (added lines + line numbers, deletions
ignored), and end-to-end violation finding (allowlist + inline escape).

## How to extend

- New deliberately-German file → add a glob to the allowlist.
- Heuristic misses/over-flags a word → adjust `_STRONG` / `_WEAK` in
  `_german_detect.py` and add a regression case to the test file.
