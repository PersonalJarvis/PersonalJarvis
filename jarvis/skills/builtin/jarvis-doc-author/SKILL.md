---
schema_version: "1"
name: jarvis-doc-author
version: "1.0.0"
description: |
  Authoritative skill for writing internal Personal-Jarvis docs under
  ``docs/``, ``Latenz/``, ``Jarvis Long-Term Memory/``, ``SubAgentenSt/`` and
  related directories. Enforces a Diataxis-compliant classification
  (concept/how-to/tutorial/reference/troubleshooting/adr), a uniform
  YAML frontmatter schema, a style canon (active voice, imperative in
  instructions, code identifiers in English, prose in English) and hard
  anti-patterns (no quadrant mixing, no filler intros, no "Click here").
  Activate this skill ALWAYS when the user or a Sub-Jarvis creates a new
  Jarvis doc, fundamentally reworks an existing doc, writes an ADR,
  produces a phase test report, or generally writes a Markdown file
  under ``docs/`` and siblings — even when the user does not explicitly say
  the word "doc" ("dokumentier mal X" (document X), "schreib eine Anleitung"
  (write a guide), "leg ein Konzept-Paper an" (create a concept paper),
  "fass das als ADR zusammen" (summarize as ADR), "schreib mir einen
  Report ueber Phase 7" (write me a Phase 7 report)). The skill does NOT
  trigger on code comments, commit messages, PR descriptions, memory entries,
  skill authoring (the ``skill-creator`` exists for that) or
  voice-output-filter templates.
category: meta
tags: [meta, docs, authoring, diataxis, documentation, style-guide]
author: builtin
license: MIT
triggers: []
requires_tools: []
risk_policy:
  default_tier: monitor
  require_confirmation:
    - write_doc_file
config:
  docs_root: docs
  default_diataxis: explanation
  default_status: draft
  default_owner: harald
  review_cadence_days_default: 180
  review_cadence_days_tutorial: 90
  review_cadence_days_howto: 90
  review_cadence_days_reference: 90
  review_cadence_days_explanation: 180
  review_cadence_days_troubleshooting: 180
  review_cadence_days_adr: 365
token_budget_estimate: 8000
---

# Jarvis Doc Author

When a Jarvis doc is written, the rule is: **classify first, then
structure, then phrase, then self-check.** Not the other way around.

This skill provides the scaffolding. It does not replace thinking — it enforces
the discipline that separates a pile of internal Markdown files from a
*real* doc layer (role models: Kubernetes doc site, Docker manuals,
GitHub Docs).

## When to trigger

Activate this skill when the user (via voice or chat) or a
Sub-Jarvis explicitly or implicitly wants to write a **new doc** or
**structurally** rework an existing doc. Concrete examples:

- "Schreib ein Doc ueber den BrainManager-Routing-Discipline-Mechanismus." (Write a doc about the BrainManager routing discipline mechanism.)
- "Leg ein ADR an fuer die Sub-Jarvis-Hard-Cap-Entscheidung." (Create an ADR for the Sub-Jarvis hard-cap decision.)
- "Dokumentiere wie man einen neuen Brain-Provider hinzufuegt." (Document how to add a new brain provider.)
- "Fass die Phase-6-Ergebnisse als Test-Report zusammen." (Summarize the Phase 6 results as a test report.)
- "Mach mir eine How-To fuer das Voice-Bridge-Setup." (Create a how-to for the voice-bridge setup.)
- A Sub-Jarvis receives the task from the main Jarvis "schreib das in
  ``docs/...`` rein" (write this into ``docs/...``) — then the Sub-Jarvis consults this skill before
  writing.

Do NOT trigger on:

- Code comments, docstrings, type hints — those follow the CLAUDE.md rule
  (code identifiers in English), but need no doc scaffold.
- Commit messages and PR descriptions — those are build artifacts.
- Memory entries — the ``auto-memory`` is responsible for those.
- Skill authoring — the ``skill-creator`` skill exists for that.
- Voice-output-filter templates or TTS prompts.

## Workflow (5 phases)

### 1. Classify — which Diataxis quadrant?

Question: what does the reader who later opens this doc want to **do or
understand**? Exactly one quadrant wins:

| Quadrant | When... | Example doc |
|---|---|---|
| **concept** (explanation) | Reader wants to *understand why/what it is* | "BrainManager-Routing-Discipline" |
| **how-to** | Competent reader wants to *solve a task* | "Adding a new brain provider" |
| **tutorial** | Learning reader needs a *guided tour* | "Bringing up Phase-6 Self-Healing from scratch locally" |
| **reference** | Reader looks for a *fact* | "EventBus event catalog", "jarvis.toml schema" |
| **troubleshooting** | Reader has a *symptom* | "TTS outputs SAPI5 robot voice instead of Cartesia" |
| **adr** | Architecture decision with *consequences* | "ADR-0009 Self-Healing-Worker-Critic" |

**If unsure** between tutorial and how-to: does the reader consult the doc
*while working* (time pressure, problem in mind) → how-to. Does the reader go through it
*away from work* to learn → tutorial. (Diataxis test, see
``references/diataxis-quadrants.md``.)

**Hard Rule:** A doc serves **exactly one** quadrant. If the doc threatens
to serve two, split it into two files with a cross-link. Quadrant mixing
is anti-pattern AP-D-1 (see ``references/templates.md``).

### 2. Determine the path

By quadrant + component. The conventions in the repo (as of 2026-04-28):

| Quadrant | Default path |
|---|---|
| **adr** | ``docs/adr/NNNN-slug.md`` (NNNN = next free 4-digit number) |
| **how-to** | ``docs/{component}/howto-{slug}.md`` |
| **concept** / explanation | ``docs/{component}/concept-{slug}.md`` or ``docs/{component}/{slug}.md`` |
| **reference** | ``docs/{component}/reference-{slug}.md`` or ``docs/reference/{slug}.md`` |
| **tutorial** | ``docs/tutorials/{slug}.md`` |
| **troubleshooting** | ``docs/troubleshooting/{slug}.md`` or ``docs/{component}/troubleshooting-{slug}.md`` |
| **Phase test report** | ``docs/phase{N}-{slug}.md`` (special case, counts as a reference + concept hybrid; an existing convention overrides) |

The slug is always **lowercase, kebab-case, English** (``router-discipline``, not ``Router-Disziplin``).
Never rename a slug — cross-links break otherwise.

For an ADR: check in ``docs/adr/`` which number is next free
(``ls docs/adr/``). Currently ADR-0001 through ADR-0012 are in use; the next free
one is ``ADR-0013``.

### 3. Fill in the frontmatter schema

Every Jarvis doc carries a YAML frontmatter with **7 mandatory fields**:

```yaml
---
title: "Concept: Router-Discipline"        # display title, in quotes
slug: router-discipline                     # URL-stable, kebab-case, append-only
diataxis: explanation                       # tutorial|howto|reference|explanation|troubleshooting|adr
status: draft                               # draft|active|deprecated
owner: harald                               # who is responsible for correctness
last_reviewed: 2026-04-28                   # ISO date YYYY-MM-DD
phase: 5                                    # master-plan phase: 0|1|1a|1c|2|3|4|5|6|7|L+P|A0..A5|-
---
```

**Optional:** ``tags``, ``related`` (cross-refs as a list of slugs),
``deprecates``, ``deprecated_by``, ``audience`` (developer/operator/end-user
— default developer for internal docs), ``next_review_due`` (computed from
``last_reviewed`` + cadence from ``config``).

Full field definitions + rationale + examples:
``references/frontmatter-schema.md``.

### 4. Insert the skeleton + write the content

Load the template for the chosen quadrant from
``references/templates.md`` (all 6 skeletons, copy-paste-ready). Mandatory
and optional sections are marked. Fill in this order:

1. ``# H1`` = ``title`` from frontmatter (no emoji, no Markdown in the title).
2. **1-2-sentence intro** directly under H1, before any H2. Answers: what is
   the doc, who is it for, what does the reader take away? No
   "Welcome", no "This document describes..." — straight to the point.
3. Quadrant-specific mandatory sections (see templates).
4. Code blocks ALWAYS with a language tag (`` ```python ``, `` ```bash ``,
   `` ```toml ``, `` ```yaml ``, `` ```json ``). Never bare
   triple backticks.
5. ``## What's next`` as a footer block, max
   **5 bullets**, each with a cross-link to a follow-up doc (relative path,
   not an absolute URL).

Language rules (excerpt — full set in ``references/templates.md``):

- **Active voice + imperative** in how-tos and tutorials. "Activate the
  voice pipeline with ``set JARVIS_VOICE=1``" — not passive constructions.
- **You** form — no "the user", no "we" when the reader is meant.
- **Code identifiers in English** (`BrainManager`, `jarvis.toml`, `EventBus`),
  **prose in English**. Identifiers in backticks, never italics.
- **Spell out acronyms on first occurrence** ("Speech-to-Text (STT)").
- **No "obviously" / "simply" / "just"** — it humiliates the reader who is
  struggling and is explicitly forbidden in the Microsoft + Google style guides.
- **No "click here" / "see this link"** — the link text describes the
  target: "see ``ADR-0011 Router-Discipline``", not "see here".

### 5. Self-check before saving

Before the file is written, go through the quality checklist from
``references/templates.md`` (15 points). The most critical points:

1. **Quadrant lock**: Does the doc serve exactly one Diataxis quadrant?
2. **Frontmatter complete**: All 7 mandatory fields set?
3. **Title length ≤ 50 characters** (max 60, user mandate 2026-04-29): The
   frontmatter ``title`` is the **display title** in the DocsView sidebar
   and MUST stay fully readable there. A long explanation belongs in
   the body H1, not in the sidebar entry. Example: NOT ``"ADR-0009
   — Self-Healing Worker-Critic-Loop with Action/Observation-Invariant"``
   (76 characters), BUT ``"ADR-0009: Self-Healing Worker-Critic"`` (37).
4. **Intro before H2**: 1-2 sentences, no filler?
5. **Code language tags**: Does every code block have a ``language`` tag?
6. **Cross-links as relative paths**: ``docs/adr/0009-self-healing.md``,
   not ``https://github.com/...``?
7. **What's next ≤ 5 bullets**: Does the footer block respect the K8s cap?
8. **Verification section in how-tos**: Concrete pytest command,
   concrete log marker, concrete Computer-Use step? (Verify-before-ship
   mandate from CLAUDE.md.)
9. **No TODO/FIXME/TBD in the body** when ``status: active``? If it is a stub:
   ``status: draft`` in the frontmatter and mark it in the body.

When all 9 points are green: write the file. If a confirmation is
needed (risk policy ``write_doc_file``), show the user the path +
frontmatter + first 20 lines of body, ask once, then write.

## What the skill deliberately omits

Kept deliberately small — Personal-Jarvis is a single-user project. The following
is **not** the job of this skill:

- **Multi-version docs / i18n.** All new docs are English (project policy); no translation workflow, no version switch.
- **Card sorting / user tests.** Single-user; the information architecture follows the maintainer's own intuition.
- **Editorial review as a separate step.** The self-check replaces that;
  linters catch 80%.
- **Eval framework like ``skill-creator`` has.** Doc quality is
  qualitatively measurable; quantitative benchmarks would be overkill.
- **Auto-migration of all existing docs.** Existing files follow
  different patterns; they are adapted opportunistically on re-review,
  not in a big bang.

## Reference files

The detail definitions live in the sibling files. Read the reference
file whose topic is currently being asked about — not all of them on spec.

- ``references/diataxis-quadrants.md`` — deep definition of the 4 quadrants,
  tutorial-vs-how-to test, reference-vs-explanation test, mapping onto
  Jarvis modules.
- ``references/frontmatter-schema.md`` — all frontmatter fields (mandatory
  and optional), rationale, examples per doc type, slug rules.
- ``references/templates.md`` — complete skeletons for all 6 doc types,
  style canon (10 rules), anti-pattern catalog (12 points),
  quality checklist (15 points).

## Anti-pattern quick view

Full list in ``references/templates.md``. These are the five that
are broken most often:

- **AP-D-1** Quadrant mixing (tutorial with a reference table).
- **AP-D-2** Concept doc with code snippets without a clear purpose.
- **AP-D-5** How-to without a prerequisites section.
- **AP-D-7** Owner field empty (the doc rots).
- **AP-D-12** A concept explained twice in different places (drift
  guaranteed — one place explains, all others link).

## Trace

Skill execution emits ``SkillStarted`` -> ``SkillStepExecuted`` (1 step
per phase in the workflow) -> ``SkillCompleted``. The ``write_doc_file`` step
records path + slug + Diataxis quadrant in the step payload, **not**
the full body (token protection; the body lands on disk and
is readable from there).
