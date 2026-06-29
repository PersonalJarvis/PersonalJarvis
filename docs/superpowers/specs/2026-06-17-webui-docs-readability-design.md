---
title: "Design: webui docs readability pass (Claude-Code-style jargon glossing)"
slug: webui-docs-readability-design
diataxis: explanation
status: draft
owner: sam
date: 2026-06-17
---

# Design — webui docs readability pass

## Problem

The 12 user-facing docs in the **separate** `personal-jarvis-webui` repo
(`src/content/docs/`) are well-written but assume a developer reader. They use
unexplained jargon (`meta-orchestrator`, `harness`, `Supervisor-Agent`,
`Mission`, `Critic`, `git worktree`, `MCP`, `event bus`) and presuppose tooling
knowledge (`venv`, `npx`, "a PR", "headless"). The maintainer wants them to read
like the Claude Code docs: explanatory for developers, yet followable by a
normal person — not dumbed down, not Stanford-PhD-level.

## Goal

Apply one consistent **gentle glossing** pass to all 12 docs: explain jargon in
passing and lead each doc with a plain-language hook, while preserving every bit
of technical depth. A developer loses nothing; a non-developer is no longer left
behind.

## Non-goals

- **No substance removal.** Tables, code blocks, flags, and the deep technical
  sections (especially `architecture.md`) stay complete.
- **No structural rewrite, no new docs, no separate glossary page.** Explanations
  are inline, not in an appendix.
- **No frontmatter changes.** `title` / `description` / `category` / `order`
  stay byte-identical, or the Astro content collection build breaks.
- **No design/CSS/component changes** — prose only.

## The glossing canon (five rules, applied uniformly)

1. **Plain-language hook.** Each doc opens with one everyday-language sentence —
   what this is / why it matters to the reader — *before* the first jargon term.
2. **Gloss jargon on first use**, in a half-sentence, not an appendix. Pattern:
   *"a **harness** — the interchangeable engine that actually runs the work"*.
3. **Catch presupposed tooling** (`venv`, `npx`, "a PR", "headless") with a short
   parenthetical — enough that nobody is stranded, not a tutorial.
4. **Zero substance loss** — see non-goals.
5. **Consistent terms** — one explanation per term on its first occurrence per
   doc; second person, active voice, the Anthropic-docs tone.

## Scope — all 12 docs

`introduction`, `installation`, `first-run`, `configuration`, `brain-providers`,
`voice-pipeline`, `missions`, `computer-use`, `cli`, `harness-dispatch`,
`architecture`, `troubleshooting`. Each doc's exact term list is finalized when
that doc is read during implementation; the canon above is the contract.

## How the work is done (separate repo, safe)

- The target is the **separate** repo `personal-jarvis-webui`, not this working
  tree. Clone it fresh into an isolated working directory, work on a dedicated
  branch, never touch this repo's tree except for this spec.
- Touch **body prose only**; leave YAML frontmatter untouched.
- **Verification:** `npm run build` (or `npm run astro build`) in the clone must
  stay green — that proves no doc's frontmatter/MDX was broken.
- The **push is outward-facing**: commit on the branch, but publishing to the
  GitHub repo is brought back to the maintainer for sign-off (branch/PR vs.
  direct) — never pushed silently.

## Acceptance criteria

- All 12 docs open with a plain-language hook and gloss their jargon on first use.
- No frontmatter field changed; `npm run build` green in the clone.
- A non-developer can follow every doc's opening; a developer still finds the
  full depth (tables, code, flags intact).
- Maintainer sign-off obtained before anything is pushed to the public repo.
