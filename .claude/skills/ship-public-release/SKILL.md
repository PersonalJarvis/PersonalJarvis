---
name: ship-public-release
description: >-
  Ship the complete current working-tree state as a new SemVer version to the
  SEPARATE PUBLIC distribution repo (github.com/PersonalJarvis/PersonalJarvis),
  fully depersonalized — the open-source pattern where the maintainer never ships
  their own credentials, config, Wiki/Vault, or personal data; whoever downloads
  it gets exactly the maintainer's folder minus the personal parts. This is the
  ONE path that puts the working tree onto the public flagship repo — it runs for
  BOTH the everyday DISCREET snapshot (a clean snapshot, NO version bump / tag /
  release) and an explicit VERSIONED release. Runs a layered fail-closed privacy
  gate (export git-tracked files, apply the distribution denylist, deterministic
  PII scrub, MANDATORY sub-agent privacy review, blocking secret/PII scan,
  mandatory human review) before any push; in release mode it also asks whether
  the bump is MAJOR, MINOR, or PATCH. This is the maintainer's standard "get my
  work onto GitHub" path (the public repo is the only project repo). Triggers on:
  "push to GitHub", "Push nach GitHub", "commit and push", "sichere den Stand",
  "ship a public release", "release to the public repo",
  "publish the open-source version", "update PersonalJarvis",
  "push the depersonalized version", "make a public release", "cut a new public
  version", "veröffentliche eine neue Version", "bring den aktuellen Stand ins
  öffentliche Repo", "öffentliches Release", "anonymisiertes / depersonalisiertes
  Release", "schiebe das ins Distributions-Repo", "neue Public-Version raus",
  "shippe meinen Ordner-Stand nach PersonalJarvis". Git cleanup is out of scope
  (that is git-rescue). The public flagship is the ONLY project repo, so a bare
  "push" / "commit and push" / "save to GitHub" IS this skill in DISCREET mode —
  do NOT ask "which repo" and do NOT downgrade it to a plain `git push origin`. A
  bare "ship this" / "mach ein Release" / "Neue Version shippen" means RELEASE mode.
---

# Ship Public Release

Publish the current Personal-Jarvis working tree as a new, depersonalized SemVer
release to the **separate public distribution repo**.

## Why this skill exists (mental model)

There is **ONE project repo — the public flagship** — plus a silent local backup
that this skill only ever reads:

| | Public flagship (the project) | Silent backup (origin) |
|---|---|---|
| Remote | `github.com/PersonalJarvis/**PersonalJarvis**` (capital P+J) | `github.com/personal-jarvis/personal-jarvis` (lowercase) |
| Role | the ONE repo everything ships to | raw local safety net only — never "the project" |
| Contains | depersonalized snapshots; a clean commit per push, a tag only in release mode | everything, incl. the maintainer's WIP, branches, raw history |
| This skill | the **only** thing that pushes to it | reads it (source), never writes to it |

The user wants downstream users to get **exactly their folder state** — including
today's uncommitted feature work — **minus** personal data (config, API keys,
Wiki/Vault, internal dev docs, signing keys, PII). This is the OpenClaw / Peter-
Steinberger pattern: ship a clean codebase everyone can self-configure, never the
maintainer's own credentials.

## Two modes (same privacy gate, different ceremony)

The gate (steps 1–8) is **identical** in both modes. They differ only at the end:

| | Discreet (DEFAULT) | Release (explicit) |
|---|---|---|
| Trigger | "push", "Push nach GitHub", "commit and push", "save to GitHub", "sichere den Stand" | "Neue Version shippen", "mach ein Release", "publish release", "veröffentliche eine neue Version" |
| Version bump (step 4/7) | **skipped** — keep the snapshot's existing version | choose MAJOR/MINOR/PATCH |
| Git tag + CHANGELOG | **none** | `vX.Y.Z` tag + CHANGELOG entry |
| Commit (step 9) | `release: snapshot <YYYY-MM-DD> — <summary>`, push branch only | `release: vX.Y.Z — <summary>`, push branch + tag |

When the mode is ambiguous, default to **discreet**. Everything else — the
clean-snapshot build, the denylist, the scrub, the **mandatory sub-agent privacy
review**, the deterministic scan, the human review — runs the same either way.
<!-- i18n-allow: quoted German maintainer trigger phrases -->

**"Public" = the *target repo*, not an action.** This skill pushes content into
the distribution repo. It NEVER changes that repo's visibility (private↔public) —
the maintainer controls that manually and on purpose (see Hard rule 7). If the
distribution repo is private when you run, it stays private; the maintainer flips
it to public themselves, once, when they decide.

The dangerous, deterministic part — *what is allowed to leave the machine* — lives
in the bundled script `scripts/strip_and_scan.py`, not in your judgment. **You may
only ADD findings on top of it; you may never clear what it blocks.** The gate is
**fail-closed**: if anything is uncertain, it stops and nothing is pushed.

## Hard rules (non-negotiable)

1. **Never run a write/commit/push git command with the working repo as cwd or
   `-C` target.** The working repo is opened **read-only** (`git ls-files`,
   `git grep`, `git rev-parse`). All mutation happens in the temp staging dir and
   the temp distribution clone.
2. **The push target must be exactly `https://github.com/PersonalJarvis/PersonalJarvis.git`**
   (case-sensitive). If the resolved remote is the lowercase working repo, STOP.
3. **Never push if `scan` reports any BLOCKING finding.** Exit code ≠ 0 = STOP.
4. **Never bypass the human review checkpoint** (step 8). Pushing to a public repo
   is outward-facing and irreversible — explicit approval every time.
5. **Never `--force` push** and never override the reconcile mass-deletion guard
   without the user explicitly confirming in the same turn.
6. **A secret already in a pushed commit is not "removed" by a new commit.** Git
   history is permanent. If `scan` ever flags a secret in already-public history,
   tell the user plainly that `git filter-repo` + key rotation is required.
7. **NEVER change the repository's visibility.** This skill only ever *pushes
   content* into the distribution repo; it must never make a repo public or
   private. The "public" in the name means "ship into the repo the maintainer
   themselves chose to publish" — not "publish anything". Specifically forbidden:
   `gh repo edit --visibility ...`, `gh repo create --public`, the GitHub API
   `PATCH /repos/...` visibility field, or any equivalent. The maintainer toggles
   private↔public **manually, outside this skill**, and whatever state the repo
   is in when you run must be exactly the state it is in when you finish. If you
   ever believe a visibility change is needed, STOP and tell the user to do it
   themselves.
8. **NEVER skip the sub-agent privacy review (step 8a).** It is mandatory on every
   ship — discreet snapshot and versioned release alike. A non-empty finding STOPS
   the push. The sub-agent may only ADD blocking findings; it can never clear what
   the deterministic gate (layers A–D) blocked.

## Setup (resolve paths first)

```bash
REPO="$(git rev-parse --show-toplevel)"
SKILL_DIR="$REPO/.claude/skills/ship-public-release"
SCRIPT="$SKILL_DIR/scripts/strip_and_scan.py"
# Fresh, uniquely-named work dir each run (no stale reuse — defeats the
# restore-trap, BUG-006 class). Pick a timestamp/slug for <RUN>.
WORK="$REPO/../ship-release-work/<RUN>"          # e.g. .../ship-release-work/20260531-1
mkdir -p "$WORK/staging"
DIST_URL="https://github.com/PersonalJarvis/PersonalJarvis.git"
```

Announce to the user: "Starting a public release to PersonalJarvis (depersonalized).
I'll build a clean snapshot, scan it, show you exactly what ships, and only push
after you approve."

## Workflow

### 1. Identity preflight (Hard rule 2)

```bash
git -C "$REPO" remote get-url origin     # expect .../personal-jarvis (lowercase) = source
```

Confirm the source is the working repo. The target is the fixed `$DIST_URL`. If
either looks wrong, STOP and tell the user.

### 2. Build the clean snapshot (gate layers A + B + C)

```bash
python "$SCRIPT" build --working "$REPO" --staging "$WORK/staging" \
  --skill-dir "$SKILL_DIR" --report "$WORK/build-report.json"
```

This exports only git-tracked files (so every `.gitignore` exclusion —
`jarvis.toml`, `.env`, `data/`, Vault content, keys — is enforced for free),
removes the distribution denylist (internal docs, scratch scripts, signing keys,
**and this skill itself**), and applies the deterministic PII scrub. Read the
report; note the scrubbed-file counts for the review.

### 3. Scan the staging tree (gate layer D, fail-closed)

```bash
python "$SCRIPT" scan --tree "$WORK/staging" --skill-dir "$SKILL_DIR" \
  --report "$WORK/scan-staging.json"
echo "exit=$?"     # non-zero => STOP, do not continue
```

If blocking findings appear, STOP and show the user the `scan-staging.json`
blocking list. Do not try to "fix" by editing staging by hand and re-pushing —
investigate the root cause (a new secret, a scrub gap → update
`references/pii-scrub.tsv`, then rebuild).

### 4. Set the release version in the snapshot (RELEASE MODE ONLY — discreet skips this)

Bump **only the snapshot** — the WIP working repo stays untouched (its version
stays `0.1.0`). First-ever release seeds `v0.1.0` (the dist repo has no tags yet).

```bash
python "$SCRIPT" set-version --tree "$WORK/staging" --version <X.Y.Z>
```

(You decide `<X.Y.Z>` in step 7 — run this once that's chosen. The script asserts
`pyproject.toml` and `jarvis/__init__.py` agree.)

### 5. Clone the distribution repo fresh

```bash
gh repo clone PersonalJarvis/PersonalJarvis "$WORK/dist"
git -C "$WORK/dist" remote get-url origin     # MUST equal $DIST_URL exactly
```

Full clone (not shallow) so the tag push is clean. Verify the remote URL string
equals `$DIST_URL` **exactly, case-sensitive** (Hard rule 2). The fresh clone is
deliberate: never reuse the stale sibling clones `../PersonalJarvis*`.

### 6. Reconcile staging → dist

```bash
python "$SCRIPT" reconcile --staging "$WORK/staging" --dist "$WORK/dist" \
  --skill-dir "$SKILL_DIR" --report "$WORK/reconcile-report.json"
# --force is REQUIRED: a plain `git add -A` honours the dist .gitignore and will
# SILENTLY drop files that are tracked in the working repo only because they were
# force-added there (e.g. jarvis/state vs the unanchored `state/` rule). That is
# how a boot-broken release shipped once. Force-add the already-clean staged set.
git -C "$WORK/dist" add --all --force
# Integrity gate (Risk R2) — fail-closed. Confirms every staged file is actually
# committed AND every `import jarvis.x.y` resolves to a file in the shipped tree
# (catches an imported-but-untracked module that would never ship).
python "$SCRIPT" verify --staging "$WORK/staging" --dist "$WORK/dist" \
  --report "$WORK/verify-report.json"
echo "verify-exit=$?"     # non-zero => STOP: the release would be incomplete/broken
git -C "$WORK/dist" status --short
git -C "$WORK/dist" diff --cached --stat | tail -1
```

`reconcile` deletes dist files absent from staging (except the dist-only keep
list, e.g. `AGENTS.md`), copies the clean tree in, and refuses if it would delete
more than ~10 % of the repo (a wrong-build signal). New feature files (today's
work) appear here as adds — that's the point. The `--force` add + `verify` step
guarantee the public commit equals the staged set exactly: nothing the dist
.gitignore happens to match gets silently lost, and nothing imported is missing.
If `verify` flags an unresolved import, the usual cause is an **untracked** file
in the working repo that shipped code depends on — tell the user to `git add` it
(the skill can only ship git-tracked files), then rebuild.

### 7. Choose the version bump (RELEASE MODE ONLY — discreet skips this)

Reuse the github-version UX.

Use `AskUserQuestion` with three options, Recommended first, tag format `vX.Y.Z`:

| Bump | When | Example |
|---|---|---|
| MAJOR | breaking change | 1.0.0 → 2.0.0 |
| MINOR | new feature, compatible | 1.1.0 → 1.2.0 |
| PATCH | bugfix / docs / chore | 1.1.0 → 1.1.1 |

Derive a recommendation from the `git diff --cached --stat` content, but the user
always confirms. The **first** release is `v0.1.0` (matches the code). Once chosen,
run step 4's `set-version`, re-stage the two version files, and prepend a
`## [X.Y.Z] - <YYYY-MM-DD>` section to the dist `CHANGELOG.md` (Added / Changed /
Removed, derived from the diff). Then re-run `git -C "$WORK/dist" add -A`.

### 8. Final scan + mandatory sub-agent review + human review (gate layers D + R + E)

Scan the **reconciled dist tree** — this is the authoritative gate over exactly
what will ship:

```bash
python "$SCRIPT" scan --tree "$WORK/dist" --skill-dir "$SKILL_DIR" \
  --report "$WORK/scan-dist.json"
echo "exit=$?"     # non-zero => STOP
```

#### 8a. Mandatory sub-agent privacy review (gate layer R — the maintainer's hard requirement)

**This step is non-negotiable and runs on EVERY ship (discreet and release).** The
deterministic scanners (layers A–D) catch known patterns; the sub-agent is the
*semantic* backstop for whatever a regex cannot see — a name in a comment, a
personal anecdote in a docstring, an internal URL, a credential in an unusual
shape, a config example with a real value.

Dispatch a **fresh, read-only sub-agent** (Task tool, `subagent_type: "Explore"`
or `general-purpose`) pointed at the reconciled dist tree `"$WORK/dist"`. Give it
exactly this brief:

> You are a release privacy auditor. Read EVERY file under `<DIST_TREE>` and find
> anything that must NOT appear in a PUBLIC open-source repo: API keys, tokens,
> secrets, passwords, bearer/credentials; personal data (real names, personal
> emails, phone numbers, home paths like `C:\Users\<name>`); private/internal URLs,
> hostnames, or infra; internal-only notes, TODOs naming people, or maintainer
> identity; any config/example carrying a real value rather than a placeholder.
> Return a JSON list of findings: `[{file, line, snippet, why}]`. Empty list = clean.
> Do NOT modify anything. When uncertain, INCLUDE it — false positives are cheap,
> a leak is permanent.

Rules for the result:

- **Any non-empty finding → STOP.** Show the user the findings; do not push.
- The sub-agent may only **ADD** blocking findings. It can **never** clear or
  override what the deterministic gate (layers A–D) already blocked — those are
  authoritative (Hard rule 3 still holds).
- A real finding usually means a denylist/scrub gap → fix the matching reference
  file (`references/distribution-denylist.txt` / `references/pii-scrub.tsv`), then
  **rebuild from step 2** (do not hand-edit staging and re-push).
- Record the sub-agent's verdict (clean / findings) in the consolidated review below.

#### 8b. Human review (gate layer E)

Then present a single consolidated review and **ask for explicit approval** via
`AskUserQuestion` before any network write. Show:

- **Mode**: discreet (snapshot, no tag) or release (`vX.Y.Z` + tag + CHANGELOG).
- **Target**: `$DIST_URL`, branch `main` (+ tag `vX.Y.Z` in release mode only).
- **What ships**: the `git diff --cached --stat` summary (adds / mods / deletes).
- **What was withheld**: denylist count from `build-report.json` (collapse the list).
- **What was scrubbed**: per-file substitution counts from `build-report.json`.
- **Scan result**: 0 blocking; the warnings (incl. the `.mailmap` note — the real
  emails live there by the maintainer's documented decision); the allowlisted
  fakes that were intentionally let through.
- **Sub-agent privacy review** (step 8a): clean, or the findings list (must be clean to proceed).

If the user does not clearly approve, abort and leave `$WORK` for inspection.

### 9. Commit, tag, push (dist repo only)

Force the commit author to the public noreply identity (Hard rule 1: `-C` is the
**dist** clone, never the working repo).

**Release mode** (commit + tag + push branch and tag):

```bash
git -C "$WORK/dist" \
  -c user.name="Personal Jarvis Maintainer" \
  -c user.email="226271791+rubenluetke10-beep@users.noreply.github.com" \
  commit -m "release: vX.Y.Z — <short summary>"

git -C "$WORK/dist" tag -a "vX.Y.Z" -m "vX.Y.Z — <short summary>"
git -C "$WORK/dist" push origin HEAD:main
git -C "$WORK/dist" push origin "vX.Y.Z"
```

**Discreet mode** (commit + push branch only — NO tag, NO version bump):

```bash
git -C "$WORK/dist" \
  -c user.name="Personal Jarvis Maintainer" \
  -c user.email="226271791+rubenluetke10-beep@users.noreply.github.com" \
  commit -m "snapshot: <YYYY-MM-DD> — <short summary>"

git -C "$WORK/dist" push origin HEAD:main
```

In release mode push the branch first, then the tag (never `git push --tags`,
never `--force`). These are the **only** remote-changing actions allowed. Do not
run any `gh repo edit`, settings, or visibility command (Hard rule 7).

### 10. Summary

Report, with clickable links:

- Compare/commit URL: `https://github.com/PersonalJarvis/PersonalJarvis/commit/<sha>`
- Tag/release URL: `https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/vX.Y.Z`
- Note that the dist repo's own CI will run; suggest waiting for it before
  treating the release as final (it may sign the installer on a `v*.*.*` tag).
- Rollback: `git -C <dist> push origin :vX.Y.Z` to delete the tag;
  `git -C <dist> revert <sha>` + push to undo the commit.
- Confirm the working repo is untouched: `git -C "$REPO" status` unchanged.
- Confirm the distribution repo's visibility is **unchanged** — this skill never
  alters private↔public; that stays the maintainer's manual decision.

Then remove `$WORK` (leave it on any failure for inspection).

## The privacy gate (six layers, deny-by-default)

Each layer can independently stop the ship; the script enforces A–D, you enforce R + E:

- **A — tracked-files-only export.** Gitignore exclusions are free: no
  `jarvis.toml`, no `.env`, no `data/`, no Vault content, no keys.
- **B — distribution denylist** (`references/distribution-denylist.txt`). Internal
  dev docs, scratch scripts, encrypted signing keys, and this skill's own files.
- **C — deterministic PII scrub** (`references/pii-scrub.tsv`). Real name, personal
  paths, project ids. `.mailmap` is **exempt** (`references/scrub-exempt.txt`):
  its real emails are the mapping keys that produce the noreply form — scrubbing
  them would break the very protection they provide.
- **D — blocking secret/PII scan**, fail-closed. Real-credential-length regexes
  (so deliberately-short test fakes don't false-trip); a re-run of the PII
  patterns must be 0 in non-exempt files; the 3-fake allowlist
  (`references/secret-allowlist.tsv`) is matched by exact value **and** path.
- **R — mandatory sub-agent privacy review** (step 8a). A fresh read-only sub-agent
  reads the whole reconciled tree and semantically hunts for anything personal or
  secret a regex would miss. ADD-only: it can block, never clear. Runs on every
  ship, discreet and release.
- **E — human review** (step 8b). Nothing leaves the machine without an explicit go.

## Risk guardrails (recognize the signal)

| Risk | Guard |
|---|---|
| Leak via stale clone | Always `gh repo clone` fresh; never reuse `../PersonalJarvis*`. |
| Ship a broken release | `--force` add + `verify` (every staged file committed, internal imports resolve); version triple-consistency; dist CI on the tag. |
| Clobber the working repo | Working repo read-only; all writes in `$WORK`. |
| Push to the wrong (lowercase) repo | Exact case-sensitive `$DIST_URL` check (steps 1, 5). |
| Secret-in-history permanence | New commit ≠ removal — say so; needs `git filter-repo` + rotation. |
| Scan fatigue / false fakes | Real-length patterns + value+path allowlist; warnings never block. |

## Reference files

- `scripts/strip_and_scan.py` — the deterministic gate (`build`, `scan`,
  `reconcile`, `verify`, `set-version`). stdlib-only; runs on a bare Linux container.
- `references/distribution-denylist.txt` — paths that never ship.
- `references/pii-scrub.tsv` — PII patterns + actions (scrub / block-only / warn).
- `references/secret-allowlist.tsv` — intentional fakes, pinned by value + path.
- `references/scrub-exempt.txt` — paths exempt from scrub (`.mailmap`).
- `references/dist-only-keep.txt` — dist-only files preserved on reconcile.

When a new internal-doc category, a new intentional fake, or a new PII variant
appears, update the matching reference file — that is the maintenance surface; the
workflow above does not change.
