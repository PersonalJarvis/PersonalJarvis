# Frontmatter schema for Jarvis docs

Every Jarvis doc under ``docs/`` (and siblings) carries a YAML
frontmatter between two ``---`` lines at the start of the file. The
required fields are hard — if one is missing, the future doc linter / the
future stale detection fails.

## Example — minimal valid frontmatter

```yaml
---
title: "Concept: Router-Discipline"
slug: router-discipline
diataxis: explanation
status: active
owner: sam
last_reviewed: 2026-04-28
phase: 5
---
```

These are the **7 required fields**. Everything else is optional.

## Example — complete frontmatter

```yaml
---
title: "How-To: Einen neuen Brain-Provider hinzufuegen"
slug: brain-provider-hinzufuegen
diataxis: howto
status: active
owner: sam
last_reviewed: 2026-04-28
phase: 4
audience: developer
tags: [brain, plugin, provider, openrouter]
related:
  - router-discipline
  - adr-0011-router-discipline
  - jarvis-toml-schema
next_review_due: 2026-07-28
deprecates: null
deprecated_by: null
version_min: "0.5.0"
---
```

## Required fields in detail

### ``title`` (string, in quotes)

The **display title** — what the user sees in the DocsView sidebar, in
the search results, in the recent block, and in the prev/next cards. **Not
identical** to the body H1: the body H1 may be longer and more descriptive,
the frontmatter ``title`` is the compact identifier.

**Hard rule (user mandate 2026-04-29):** The complete wording must be
readable in the sidebar. Multi-line wrap depends on the layout — the
**safe corridor is max 50 characters**, the hard ceiling is 60. If the
title would be longer, the explanation belongs in the body H1, not in the
sidebar.

**Rules:**
- In double quotes: ``"How-To: …"`` (otherwise YAML
  makes trouble with colons).
- No emoji, no Markdown formatting in the title.
- **Max 50 characters** (recommendation), absolute ceiling 60.
- Format: ``"<DocType>: <short-slug-in-readable-form>"`` — stick to a
  concise identifier, leave out the subtitle.
- **Relation to the Diataxis pill:** The pill already shows the doc type
  (ADR, Concept, etc.), so don't write "Concept: ..." plus a visual pill
  doubler; on a slug conflict (``concept-router-discipline`` and ``howto-router-
  discipline``) disambiguate in the title anyway.

**Good/bad examples:**

| Bad (too long, truncated) | Good (compact, fully readable) |
|---|---|
| ``"ADR-0001 — IPC zwischen Jarvis-App und Admin-Helper: Named Pipe + HMAC"`` (66) | ``"ADR-0001: IPC via Named Pipe + HMAC"`` (35) |
| ``"ADR-0008 — Computer-Use-Harness laeuft in-process (Ausnahme zum Subprocess-Pattern)"`` (84) | ``"ADR-0008: Computer-Use in-process"`` (33) |
| ``"ADR-0009 — Self-Healing Worker-Critic-Loop mit Action/Observation-Invariante"`` (76) | ``"ADR-0009: Self-Healing Worker-Critic"`` (37) |
| ``"How-To: Einen neuen Brain-Provider zu jarvis.toml hinzufuegen und registrieren"`` (78) | ``"How-To: Neuen Brain-Provider hinzufuegen"`` (40) |

### ``slug`` (string, kebab-case)

URL-stable identifier. Makes the doc referenceable by others
(``related: [router-discipline]``).

**Rules:**
- **Append-only.** Never rename a slug — all cross-links would be dead.
  If a doc must be fundamentally renamed: a new doc with a new
  slug + set ``deprecated_by``, redirect hint in the old doc.
- Lowercase, kebab-case (``router-discipline``, not ``RouterDiscipline``
  or ``router_discipline``).
- English or transliterated German without umlauts (``oeffnen`` instead of
  ``öffnen``, ``brueckenmodul`` instead of ``brückenmodul``).
- Language-free where possible — the slug is a technical identifier, not a UI
  string.

### ``diataxis`` (enum)

Classification anchor. Exactly **one** of the following values:

- ``tutorial`` — guided learning-path session.
- ``howto`` — solve a task, competent reader.
- ``reference`` — fact supplier (API, schema, catalog).
- ``explanation`` — understanding (architecture, trade-offs).
- ``troubleshooting`` — symptom → cause → fix.
- ``adr`` — Architecture Decision Record.

Mixed values are forbidden. If the doc threatens to serve two: split it.

### ``status`` (enum)

Life state of the doc. Three values:

- ``draft`` — not yet finished, not in the official index, may
  contain TODO/FIXME.
- ``active`` — finished, correct, in search/index. Default state after
  self-check.
- ``deprecated`` — replaced by another doc or feature removed.
  Banner in the body, set the ``deprecated_by`` field.

(Deliberately collapsed to 3 states. We don't need ``archived`` — old
docs land in history via ``git rm``.)

### ``owner`` (string)

Who is responsible for the correctness of the doc? In Personal Jarvis
currently always ``sam``. Future-proof: later several owners can be set
via a list (``owner: [sam, claude]``); for now a single string.

### ``last_reviewed`` (ISO date YYYY-MM-DD)

When was the doc last reviewed? Anchor for stale detection
(``last_reviewed`` + ``review_cadence_days`` from the skill config →
``next_review_due``).

**Rules:**
- Format ``YYYY-MM-DD`` (ISO 8601, without quotes — YAML parses
  it as a date).
- **Must be updated** on every content edit (typos excepted).
- **Initial value** when creating: today's date.

### ``phase`` (string or int)

The master-plan phase the doc is assigned to. Values:

- ``0`` / ``1`` / ``1a`` / ``1c`` / ``2`` / ``3`` / ``4`` / ``5`` / ``6``
  / ``7`` — plan phases.
- ``L+P`` — voice latency pass + plan mode (see
  ``Latenz/PHASE_L_P_VOICE_LATENCY_AND_PLAN_MODE.md``).
- ``A0`` / ``A1`` / ``A2`` / ``A3`` / ``A4`` / ``A5`` — awareness layer.
- ``-`` — cross-phase / not assignable.

Makes later phase reviews easier ("show me all Phase-6 docs that have
not been reviewed since the last phase test report").

## Optional fields

### ``audience`` (enum, default ``developer``)

Who is the target audience? Values:

- ``developer`` — yourself, sub-Jarvis, other code authors.
- ``operator`` — you when you use the tool (UI docs, voice commands).
- ``end-user`` — future multi-user phase, rare at the moment.

Determines the language depth: developer docs may use code identifiers without
explanation, end-user docs may not.

### ``tags`` (list of strings)

Cross-cut navigation, beyond the Diataxis quadrant and the phase.
Examples: ``[voice]``, ``[brain]``, ``[safety]``, ``[plugin]``,
``[mcp]``, ``[performance]``.

Tags are **freely choosable** but **consistent** — consider whether an
existing tag fits before inventing a new one. Rule of thumb: if
only 1 doc would have this tag, you probably don't need it.

### ``related`` (list of slugs)

Cross-refs to other Jarvis docs. A list of slugs (not paths —
slug resolution allows file renames without breaking links, as long as the
slug-append-only rule holds).

```yaml
related:
  - router-discipline
  - adr-0011-router-discipline
  - jarvis-toml-schema
```

### ``deprecates`` and ``deprecated_by``

Bidirectional deprecation chain. When this doc replaces an old one:

```yaml
deprecates: skill-old-spec
```

Then in the old doc:

```yaml
status: deprecated
deprecated_by: skill-new-spec
```

This way the reader finds the successor, and a tool can verify that
no zombie references to outdated docs exist.

### ``next_review_due`` (ISO date, computed)

``last_reviewed`` + cadence from the skill config (tutorial/how-to/reference: 90
days, explanation/troubleshooting: 180 days, ADR: 365 days). Optional —
if not set, stale detection computes it at runtime.

### ``version_min`` (string, semver or phase marker)

From which code version does the doc apply? Rarely relevant in a single-user
project; mandatory for multi-version docs (post-Phase-7).

```yaml
version_min: "0.5.0"
# or
version_min: "phase-6"
```

## Slug rules (summarized)

1. **Append-only:** Never change an active slug.
2. **Kebab-case:** ``risk-tier-system``, not ``RiskTier`` or
   ``risk_tier_system``.
3. **ASCII-only:** No umlauts, no special characters (except ``-``).
4. **Pre-fix on quadrant conflict:** If a module needs ``concept`` and
   ``howto``, prefix them: ``concept-router-discipline`` and
   ``howto-router-discipline``.
5. **Numbering for ADRs:** ``adr-0009-self-healing`` — four digits,
   sequential, append-only.
6. **Slug ⊆ path:** The slug appears in the file path.
   ``docs/adr/0009-self-healing.md`` → slug ``adr-0009-self-healing``.

## Type-specific frontmatter hints

### Concept / Explanation

```yaml
diataxis: explanation
audience: developer
phase: <phase where the concept lives>
related:
  - <related-howtos>
  - <related-adrs>
```

Concept docs often point to associated how-tos and ADRs. Cross-linking
is more important here than for other types.

### How-To

```yaml
diataxis: howto
audience: developer
phase: <phase>
related:
  - <prerequisite-concepts>
  - <further-references>
```

A how-to slug ideally starts with a verb: ``hinzufuegen`` (add),
``deploy``, ``aktivieren`` (activate). Makes the intent immediately clear.

### Reference

```yaml
diataxis: reference
audience: developer
phase: <phase>
version_min: "<semver-or-phase>"
```

Reference docs couple tightly to code versions. Set ``version_min`` on
larger refactors.

### Tutorial

```yaml
diataxis: tutorial
audience: developer
phase: <phase>
review_cadence_days: 90
```

Tutorials go stale fastest (they test a concrete
setup path). Default cadence 90 days.

### Troubleshooting

```yaml
diataxis: troubleshooting
audience: developer
phase: <phase or ->
tags: [<symptom-area>]
```

Troubleshooting docs collect entries; good tags help with finding them
(``tags: [voice, tts]``, ``tags: [vision, multi-monitor]``).

### ADR

```yaml
title: "ADR-0009: Self-Healing Worker-Critic"
slug: adr-0009-self-healing
diataxis: adr
status: active             # or accepted / superseded / deprecated
owner: sam
last_reviewed: 2026-04-28
phase: 6
related:
  - <referenced-adrs>
```

ADR status values: ``proposed`` / ``accepted`` (= active in the schema) /
``superseded`` (= deprecated) / ``deprecated``. ``accepted`` maps to
``active`` in the standard schema, so the linter doesn't have to maintain
two status sets.

## What does NOT belong in the frontmatter

- **Body content.** No descriptive text over 1-2 sentences, no
  code snippets, no lists of steps. Frontmatter is metadata.
- **Dynamic values.** No "today's date" as a default — ``last_reviewed``
  must be set explicitly on creation or edit.
- **Invented fields.** Only fields from this schema. If you need a new
  one: first update this file, then use it. Otherwise consistency
  falls apart after 30 docs.
- **Version numbers in the body** when ``phase:`` is already in the frontmatter.
  Single source of truth.

## Migrating existing docs

Current Jarvis docs (``docs/adr/0001-..0012-*.md``, ``docs/phase*-*.md``,
``Latenz/*.md``, ``Jarvis Long-Term Memory/*.md``) follow heterogeneous
patterns. **A big-bang migration is an anti-pattern** — existing files
stay as they are until they get touched anyway. From now on:

- **Every new doc** follows this schema.
- **Every edited doc** gets frontmatter during the edit session
  (incremental migration, ~30 seconds of effort).
- **Existing ADRs** get frontmatter at the next re-review (180-
  day cadence).

This way the doc layer grows organically, without drowning in a migration
sprint.
