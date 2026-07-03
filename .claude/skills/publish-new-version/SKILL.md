---
name: publish-new-version
description: >-
  Use when the maintainer wants to publish / ship a new PUBLIC version of
  Personal Jarvis to GitHub — the periodic (~biweekly) release of the current
  folder state to the one public repo. Triggers: "veröffentliche eine neue
  Version", "neue Jarvis-Version raus", "mach ein Release", "push nach GitHub",
  "publish the new version", "cut a release", "neue Public-Version". NOT for
  untangling git chaos (use git-rescue) and NOT a quick local-only commit.
---

# Publish a New Public Version

## Overview

One public repo, infrequent releases. This is the maintainer's **single entry
point** to put the CURRENT folder state — **all features, none of the personal
data** — onto the public GitHub repo as a clean, versioned update, and to **prove
it actually landed**. It does NOT reimplement the privacy machinery; it runs the
quality checks the maintainer cares about, then drives the local
`security-github` skill (the depersonalized public-release privacy gate), then
verifies.

## Context you must hold (the mental model)

- **There is ONE project repo: the public flagship** `github.com/PersonalJarvis/PersonalJarvis`.
  The lowercase `personal-jarvis` (`origin`) is a silent private backup only — never the deliverable.
- The local working tree **mixes shippable code with the maintainer's personal data**
  (config, API keys, Wiki/Vault, real name, `C:\Users\...` paths, machine ids).
  Personal data must **NEVER** reach the public repo — its history is world-readable forever.
- Releases are **infrequent** (≈ every 2 weeks). Each one is a **complete snapshot
  since the last release**, not continuous pushing. The downloader gets the
  maintainer's whole folder minus the personal parts.

## Process — run in order; STOP on any failure

### 1. Pre-flight quality gate (what downloaders get must actually work)
Verify each, **with evidence**, before anything is shipped:
- **Completeness** — no half-built features in what ships. Scan the diff since the
  last release for `TODO`/`FIXME`/`NotImplementedError`/stub markers in non-test
  code; every new user-facing feature (voice, chat, Jarvis-UI, sub-agents, plugins)
  has a real working path, not a placeholder.
- **Tests green** — run at least `pytest -m "not slow"`; a red suite STOPS the release.
- **Works for an ARBITRARY downloader (CLAUDE.md §3)** — the touched surface must not be
  pinned to the maintainer's keys / provider / OS. Confirm (test or honest trace):
  fresh-install-with-one-key, headless-Linux boot, cross-family fallback all reach a
  working path. If you cannot verify, say so and STOP — do not assume.
- **Community health files present & intact** — README, LICENSE, CODE_OF_CONDUCT.md,
  TRADEMARK.md, issue/PR templates.

Report every item as **PASS** or **STOP** with the evidence. No silent skips.

### 2. Privacy gate + push (the engine — do NOT duplicate it here)
**REQUIRED:** run the local `security-github` skill in **RELEASE** mode. It performs the
full fail-closed privacy gate (tracked-files-only export → distribution denylist →
deterministic PII scrub → **mandatory sub-agent privacy review** → secret/PII scan →
human review) plus the SemVer bump + git tag + CHANGELOG entry, from a separate clean
clone. Never raw-push the working tree; never bypass the pre-push guard with
`--no-verify`. A single privacy finding STOPS the push.

### 3. Proof (never claim success without it)
After the push, prove it is live:
- `git ls-remote https://github.com/PersonalJarvis/PersonalJarvis refs/heads/main refs/tags/vX.Y.Z`
  and show the live commit hash + the tag.
- State the new version number and the clickable URL `https://github.com/PersonalJarvis/PersonalJarvis`.
- If the remote hash does NOT match what was pushed, it is NOT live — say so and fix it.

## First release only
The public repo is currently **PRIVATE ("unsichtbar")**. Flipping it to public is the
maintainer's deliberate, one-time decision — do it **once, together with them, never
silently**. Every later release is just steps 1–3.

## Hard rules
- Personal data never ships — the privacy gate is non-negotiable.
- No raw `git push` to the public repo; the pre-push guard blocks it. Do not bypass.
- No "done / shipped" claim without the live `ls-remote` proof from step 3.
- Branch / worktree cleanup is **git-rescue's** job, not this skill's.
