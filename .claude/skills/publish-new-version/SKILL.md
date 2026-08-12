---
name: publish-new-version
description: >-
  Use when the maintainer wants to cut a new tagged RELEASE of Personal Jarvis —
  a SemVer bump, a CHANGELOG entry, a git tag, and a published GitHub Release.
  Triggers: "veröffentliche eine neue Version", "neue Jarvis-Version raus",
  "mach ein Release", "publish the new version", "cut a release", "neue
  Public-Version". NOT for an ordinary push — "push", "push nach GitHub",
  "sichere den Stand", "commit and push" mean `git push`, no skill, no
  ceremony (CLAUDE.md §2). NOT for untangling git chaos (use git-rescue).
---

# Cut a New Release

## What this is, and what it is not

A release is a **normal push plus four things**: a version bump, a CHANGELOG
entry, a tag, and a published GitHub Release. Nothing else. There is no
snapshot build, no staging tree, no parallel clone, no privacy sub-agent —
that ceremony was retired on 2026-08-05 after it was measured at ~440k tokens
per push (CLAUDE.md §2). There is also no review sub-agent (retired
2026-08-12): a release ships commits that already exist, and that code was
reviewed when it was written — never spawn `code-reviewer` for a version
bump, changelog entry, tag, or push. If you catch yourself preparing a clean
copy of the repo instead of tagging the commit you already have, stop.

**An ordinary push is not this skill.** "Push", "sichere den Stand", "commit
and push" → `git push`. Only an explicit "release" / "neue Version" comes here.
<!-- i18n-allow: quoted German maintainer trigger phrases -->

## Why the privacy machinery is not in this list

Personal data never reaches a release because it is never in the tree:
`.gitignore` withholds `data/`, `.env`, `jarvis.toml`, the Vault, and all key
material, and the pre-commit/pre-push credential gates plus GitHub's own push
protection catch what slips. A release ships the commits that are already on
the branch — it introduces no new content and therefore no new exposure.

## Process — run in order; STOP on any failure

### 1. Pre-flight quality gate (what downloaders get must actually work)

Verify each **with evidence**; report every item as **PASS** or **STOP**:

- **Tests green** — at least `pytest -m "not slow"`. A red suite STOPS the
  release.
- **CI green on the release commit** — `gh run list --branch main --limit 1`.
  Understand every red cause before tagging (AP-28); a tag on a red commit
  ships a known-broken version to every managed install.
- **Completeness** — scan the diff since the last tag for
  `TODO`/`FIXME`/`NotImplementedError`/stub markers in non-test code. No
  half-built user-facing feature ships.
- **Works for an ARBITRARY downloader (CLAUDE.md §3, AP-23)** — the touched
  surface must not be pinned to the maintainer's keys, provider, or OS.
  Confirm by test or honest trace: fresh-install-with-one-key, headless-Linux
  boot, cross-family fallback. If you cannot verify, say so and STOP.
- **Community health files intact** — README, LICENSE, TRADEMARK.md,
  issue/PR templates.

### 2. Choose the bump

Use `AskUserQuestion`, Recommended first, derived from the diff since the last
tag — the maintainer always confirms.

| Bump | When | Example |
|---|---|---|
| MAJOR | breaking change | 1.0.0 → 2.0.0 |
| MINOR | new feature, compatible | 1.1.0 → 1.2.0 |
| PATCH | bugfix / docs / chore | 1.1.0 → 1.1.1 |

### 3. Bump, changelog, commit, tag, push

The version lives in `pyproject.toml` and `jarvis/__init__.py` — they must
agree. Prepend a `## [X.Y.Z] - <YYYY-MM-DD>` section (Added / Changed / Fixed /
Removed) to `CHANGELOG.md`, derived from the diff.

```bash
python scripts/ci/check_release_completeness.py     # pre-tag gate — must pass
git add pyproject.toml jarvis/__init__.py CHANGELOG.md
git commit -m "chore(release): publish vX.Y.Z"
git tag -a "vX.Y.Z" -m "vX.Y.Z — <short summary>"
git push public HEAD:main
git push public "vX.Y.Z"
```

Branch first, then the tag. Never `git push --tags`, never `--force`.

### 4. Publish the GitHub Release + the resumable install asset

A pushed tag without a **published** Release updates no managed install — the
in-app updater only moves between published Releases.

```bash
gh release create "vX.Y.Z" --repo PersonalJarvis/PersonalJarvis \
  --title "vX.Y.Z" --notes-file <changelog-section>
git archive --format=tar.gz --prefix=personal-jarvis/ \
  -o personal-jarvis-src.tar.gz "vX.Y.Z"
gh release upload "vX.Y.Z" personal-jarvis-src.tar.gz \
  --repo PersonalJarvis/PersonalJarvis
```

Both install scripts fall back to
`releases/latest/download/personal-jarvis-src.tar.gz` when a clone stalls
(curl 28 / early EOF) — it is HTTP-range resumable, so a crawling connection
still finishes. A release missing this asset silently degrades that fallback
for every downloader until the next one.

### 5. Proof (never claim success without it)

```bash
python scripts/ci/check_release_completeness.py --verify-release
git ls-remote https://github.com/PersonalJarvis/PersonalJarvis \
  refs/heads/main "refs/tags/vX.Y.Z"
```

Show the live commit hash, the tag, the version number, and the Release URL.
If the remote hash does not match what was pushed, it is **not** live — say so
and fix it.

## Hard rules

- Never flip the repository's visibility. That is the maintainer's manual,
  deliberate call, made outside any skill.
- No "done / shipped" claim without the live `ls-remote` + `--verify-release`
  proof from step 5.
- A secret already in a pushed commit is not removed by a new commit. Git
  history is permanent: it needs `git filter-repo` plus key rotation, and the
  maintainer must be told plainly.
- Branch / worktree cleanup is **git-rescue's** job, not this skill's.
