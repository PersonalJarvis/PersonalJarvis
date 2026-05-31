# Templates, style canon, anti-patterns, quality checklist

Consolidated reference file. Contains:

1. **Templates** for all 6 doc types (concept, how-to, tutorial,
   reference, troubleshooting, adr) — copy-paste-ready skeletons.
2. **Style canon** — the 10 universal writing rules.
3. **Anti-patterns** AP-D-1..AP-D-12 — what a doc must NEVER do.
4. **Quality checklist** — 15-point self-check before saving.

---

## 1. Templates

Every template carries required sections (marked ``# REQUIRED``) and
optional sections (marked ``# OPTIONAL``). Delete non-applicable
optional sections entirely.

### Template: concept (explanation)

```markdown
---
title: "Concept: <Noun form>"
slug: <kebab-case-slug>
diataxis: explanation
status: draft
owner: sam
last_reviewed: <YYYY-MM-DD>
phase: <Phase>
audience: developer
related:
  - <related-howto-slug>
  - <related-adr-slug>
---

# Concept: <Noun form>

<1-2 sentences: what is the concept, who is it for, what will the
reader take away?>                                                   # REQUIRED

## When to use this                                                  # REQUIRED

<3-5 bullets: concrete trigger situations in which the reader should
understand this concept.>

## What it is                                                        # REQUIRED

<Definition. Main terms in backticks. No steps, no imperatives.>

## How it works                                                      # REQUIRED

<Mechanism. Diagram if possible (ASCII box diagram is fine).
Evidence code snippets as mini-quotes, never as core content.>

## Why this way and not another                                      # OPTIONAL

<Trade-offs, weighed alternatives. If extensive: move to a dedicated ADR
and only link here.>

## References                                                        # OPTIONAL

- Reference doc for detail fields: <link>
- How-To for applying this: <link>
- ADR for the design decision: <link>

## What's next                                                       # REQUIRED (max 5)

- [<Related follow-up doc>](<relative-path>)
```

### Template: how-to

```markdown
---
title: "How-To: <Imperative verb> <Object>"
slug: <verb-object-kebab>
diataxis: howto
status: draft
owner: sam
last_reviewed: <YYYY-MM-DD>
phase: <Phase>
audience: developer
related:
  - <prerequisite-concept-slug>
  - <further-reference-slug>
---

# How-To: <Imperative verb> <Object>

<1-2 sentences: what problem does this doc solve, for whom?>         # REQUIRED

## Prerequisites                                                     # REQUIRED

- <Software/tool version>
- <Config entry>
- <Prerequisite knowledge — brief, with cross-link to concept doc>

## Steps                                                             # REQUIRED

### 1. <Imperative sentence describing what to do>

<Optional 1-2 sentences of context. Then code block or command.>

```bash
<concrete command>
```

### 2. <Next step>

```python
# Code block with language tag
```

### 3. <…>

## Verification                                                      # REQUIRED

<Concrete test confirming the steps succeeded. Verify-before-ship
mandate from CLAUDE.md §0.>

```bash
pytest tests/<file>::<test_name>
```

Expected output: ``<log marker>`` or UI state: ``<visible effect>``.

## Caveats / Known issues                                            # OPTIONAL

- <BUG reference, workaround, anti-pattern>

## Related documents                                                 # OPTIONAL

- Concept background: <link>
- Reference for detail fields: <link>

## What's next                                                       # REQUIRED (max 5)

- [<Follow-up how-to>](<relative-path>)
```

### Template: tutorial

```markdown
---
title: "Tutorial: <Learning objective as noun phrase>"
slug: tutorial-<learning-objective-kebab>
diataxis: tutorial
status: draft
owner: sam
last_reviewed: <YYYY-MM-DD>
phase: <Phase>
audience: developer
---

# Tutorial: <Learning objective>

<2-3 sentences: what will the reader learn, how long does it take, what
will they have built at the end?>                                    # REQUIRED

## What you will have at the end                                     # REQUIRED

<Concrete end artifact. Screenshot or code snippet of the final state.>

## Prerequisites                                                     # REQUIRED

- <Hardware/software>
- <Prerequisite knowledge — minimal; otherwise it is not a tutorial>

## Phase 1: <Noun phrase for phase>                                  # REQUIRED (≥3 phases)

<Introduction — what happens in this phase and why.>

### Step 1.1: <Imperative>

```bash
<command>
```

### Step 1.2: <Imperative>

<Explanation + code/command>

### Mid-point check

> **You should now see:** <concrete visible change — log marker,
> UI state, file existence>.

## Phase 2: <…>                                                      # REQUIRED

…

## Phase 3: <…>                                                      # REQUIRED

…

## What you learned                                                  # REQUIRED

- <3-5 bullets of what the reader can now do>

## Where to go next                                                  # REQUIRED (max 5)

- [<How-To for deeper application>](<relative-path>)
- [<Concept doc for background>](<relative-path>)
```

### Template: reference

```markdown
---
title: "Reference: <Subsystem or schema>"
slug: reference-<subsystem-kebab>
diataxis: reference
status: draft
owner: sam
last_reviewed: <YYYY-MM-DD>
phase: <Phase>
audience: developer
version_min: "<semver-or-phase-marker>"
---

# Reference: <Subsystem>

<1-2 sentences: what does this doc factually describe — no explanation,
straight to the point.>                                              # REQUIRED

## Content index                                                     # OPTIONAL (required above 300 lines)

- [<Entry 1>](#entry-1)
- [<Entry 2>](#entry-2)
- ...

## <Entry 1>                                                         # REQUIRED (≥1 entry)

| Field | Type | Default | Description |
|---|---|---|---|
| ... | ... | ... | ... |

**Example:**                                                         # REQUIRED per entry

```python
# Mini snippet, 1-3 lines, runnable
```

## <Entry 2>                                                         # REQUIRED (≥1 entry)

…

## Related docs                                                      # OPTIONAL

- Concept background: <link>
- How-To for applying this: <link>
```

### Template: troubleshooting

```markdown
---
title: "Troubleshooting: <Subsystem or symptom area>"
slug: troubleshooting-<area-kebab>
diataxis: troubleshooting
status: draft
owner: sam
last_reviewed: <YYYY-MM-DD>
phase: <Phase or ->
audience: developer
tags: [<symptom-area>]
---

# Troubleshooting: <Area>

<1-2 sentences: which symptoms does this doc cover, when does it not help?>  # REQUIRED

## How to use this doc                                               # OPTIONAL

- Search for the symptom (Ctrl-F).
- Read an entry top to bottom: Symptom → Cause → Fix.
- For symptoms not listed: <note where to look instead>.

## Symptom: <Concrete error message or observable behavior>          # REQUIRED (≥1 entry)

**Cause:** <What is the root cause? 1-3 sentences.>

**Fix:**

1. <Imperative step>
2. <Imperative step>

**Verification:**

```bash
<command confirming the symptom is gone>
```

**Reference:** BUG-XXX (see ``MEMORY.md``) or issue link.

## Symptom: <…>                                                      # REQUIRED (≥1 entry)

…

## When nothing helps                                                # REQUIRED

<Escalation path: which logs to gather, which sub-agent (e.g.
``win32-specialist``), which memory entries are relevant.>
```

### Template: adr

```markdown
---
title: "ADR-NNNN: <Decision as noun phrase>"
slug: adr-NNNN-<decision-kebab>
diataxis: adr
status: draft
owner: sam
last_reviewed: <YYYY-MM-DD>
phase: <Phase>
audience: developer
---

# ADR-NNNN: <Decision>

**Status:** Proposed | Accepted | Superseded by ADR-MMMM | Deprecated

**Date:** YYYY-MM-DD

## Context                                                           # REQUIRED

<What is this about? What problem drives the decision? What constraints
apply (plan phase, anti-patterns, hardware, user preferences)?>

## Decision                                                          # REQUIRED

<The decision taken in 2-5 sentences. Clear, reasoned, without
hedging ("we could" → no: "we do X because Y").>

## Consequences                                                      # REQUIRED

### Positive

- <Bullet>
- <Bullet>

### Negative / Trade-offs

- <Bullet>
- <Bullet>

### Follow-up tasks

- <What must be implemented/changed next?>

## Alternatives Considered                                           # REQUIRED

### Alternative A: <Name>

<2-3 sentences: what would this have been, why was it rejected?>

### Alternative B: <Name>

<…>

## References                                                        # OPTIONAL

- Master plan §X
- Previous ADR: ADR-MMMM
- External sources: <link>
```

---

## 2. Style canon (10 universal rules)

Consolidated from Google + Microsoft + Write the Docs. When 3+ style guides
demand it in agreement, it is here — everything else is taste.

1. **Active voice.** "The ``EventBus`` emits the event" — not "The
   event is emitted".
2. **Imperative in instructions.** "Activate the voice pipeline" — not
   "We activate..." or "The voice pipeline can be activated".
3. **Du form (DE) / you (EN) — no "the user".** When the reader is meant,
   address them. "If you don't set the hotkey..." instead of "If
   the user doesn't set the hotkey...".
4. **Front-load the key takeaway.** First sentence per paragraph =
   conclusion. Justifications follow, not the other way around.
5. **Sentences < 25 words, median ~15.** Split complex sentences.
6. **Lists from 3+ items.** Bullet or numbered lists instead of comma prose.
7. **Code blocks ALWAYS with a language tag.** ``` ```python ```, ``` ```bash ```,
   ``` ```toml ```, ``` ```yaml ```, ``` ```json ```. Never bare
   triple backticks.
8. **Code identifiers in backticks.** ``BrainManager``, ``jarvis.toml``,
   ``EventBus`` — never italic, never unformatted.
9. **Spell out acronyms on first occurrence.** "Speech-to-Text (STT)"
   — STT OK after that.
10. **Consistent terminology.** One concept = one word. "Skill" OR
    "Plugin", not both for the same thing. If the term is already taken in
    Jarvis (see master plan / CLAUDE.md), adopt it.

**Bonus mandate for Jarvis** (not in the external style guides, derived
from user preferences in MEMORY.md):

- **Code identifiers in English, prose in English.** "The ``BrainManager`` builds
  a smart fallback chain." Not: "Der Hirn-Verwalter..." (Germanizing
  identifiers is forbidden). All doc prose is English per the output-language
  policy in ``CLAUDE.md``.

---

## 3. Anti-patterns (AP-D-1 to AP-D-12)

| ID | Anti-pattern | Why it's bad |
|---|---|---|
| **AP-D-1** | **Quadrant mix**: a tutorial contains a reference table of the API fields | Breaks Diataxis. The learner drowns; the fact-seeker overlooks it. |
| **AP-D-2** | **Concept doc with code snippets without context** | Snippets without a tutorial / how-to frame have no purpose. |
| **AP-D-3** | **Tutorial with options** ("you can also use X") | A tutorial must be linear. Options confuse learners — they belong in a how-to. |
| **AP-D-4** | **Reference without examples** | A bare signature is not enough. 1 mini example per entry is mandatory. |
| **AP-D-5** | **How-to without a prerequisites section** | The reader crashes at step 3 because step 0 was implicit. |
| **AP-D-6** | **Stale doc without ``status: deprecated``** | The doc describes a non-existent feature → more trust-damaging than a missing doc. |
| **AP-D-7** | **Owner field empty or ``owner: tbd``** | The doc rots because nobody feels responsible. |
| **AP-D-8** | **Slug changes on rename** | Breaks all cross-refs. The slug is append-only — the title may change. |
| **AP-D-9** | **Frontmatter drift** between sibling docs (different fields per file) | Tooling breaks, the search/filter index becomes unusable. |
| **AP-D-10** | **"Obviously" / "simply" / "just"** | Humiliates failing readers; Microsoft + Apple + Google explicitly forbid it. |
| **AP-D-11** | **"Click here" / "see this link"** as link text | The link text must describe the target: "see ``ADR-0011``", not "see here". |
| **AP-D-12** | **A concept explained twice in different places** | Drift guaranteed. One place explains, all others link. |
| **AP-D-13** | **Frontmatter ``title`` too long (>60 characters)** — gets truncated in sidebar/recent/search results | User mandate 2026-04-29: the complete wording must be readable in the DocsView sidebar. The long explanation belongs in the body H1, not in the display title. Max 50 characters recommended. |

**Plus 5 Jarvis-specific anti-patterns** (derived from the master-plan
mandates):

| ID | Anti-pattern | Why it's bad |
|---|---|---|
| **AP-J-1** | **How-to without a verification section** | Violates the verify-before-ship mandate (CLAUDE.md §0). The doc delivers steps but no test. |
| **AP-J-2** | **Germanized code identifier** ("der Hirn-Verwalter" instead of ``BrainManager``) | Breaks the user preference. Identifiers are standard Python English. |
| **AP-J-3** | **Voice example without a language tag** ("Jarvis, mach X" — without a hint whether DE or EN) | Bilingual default means both are equivalent — the example must be clearly attributed. |
| **AP-J-4** | **TODO/FIXME/TBD in the body when ``status: active``** | If it's a stub: ``status: draft`` in the frontmatter — otherwise the status lies. |
| **AP-J-5** | **Filler intro** ("Welcome to this document about X.") | Breaks the 1-2-sentence intro standard. Get straight to the point. |

---

## 4. Quality checklist (15 points)

Before the file is written, go through all 15 points. ``M`` =
machine-checkable, ``A`` = eye check.

### Structure (Diataxis lock)

1. ``M`` — Frontmatter complete: are all 7 required fields set?
2. ``A`` — Does the doc serve exactly **one** Diataxis quadrant? No
   tutorial-with-reference appendices?
3. ``M`` — H1 = ``title`` from the frontmatter? Exactly one H1 per file?
4. ``M`` — Heading hierarchy consistent? No H4 under H2 without an H3?

### Language (style canon)

5. ``A`` — Active voice in at least 80% of the sentences?
6. ``A`` — Imperative in how-to / tutorial steps? No "we" in a how-to?
7. ``A`` — Sentences predominantly < 25 words?
8. ``A`` — Acronyms spelled out on first occurrence?
9. ``A`` — Code identifiers in backticks, prose in English?

### Content (quadrant-specific)

10. ``A`` — Tutorial: mid-point check after every phase? No options?
11. ``A`` — How-to: prerequisites section at the top? Verification section
    at the bottom?
12. ``A`` — Reference: every entry with ≥ 1 mini example?
13. ``A`` — Explanation: ≥ 1 diagram/table/trade-off section OR an
    explicit "why this way and not another" section?

### Reliability

14. ``M`` — Do code blocks have a language tag (``bash``, ``python``,
    ``toml`` etc.)?
15. ``M`` — Cross-links as relative paths? No ``https://github.com/...``
    links to the own repo?

### Bonus (if available, otherwise skip)

- ``M`` — Readability: Flesch reading ease ≥ 50 (developer audience)?
  (Tool: ``py-readability-metrics``.)
- ``M`` — Dead-link check clean? (Tool: ``lychee`` or
  ``markdown-link-check``.)
- ``M`` — Doc coverage: if a reference covers the public API → is the API
  fully described? (Tool: a custom pytest against ``entry_points``.)

When all 15 required points are green: the file can be written.
If one is red: fix it first, don't make excuses with "almost done".

---

## Application (quick workflow)

When writing a new doc:

1. **Decide the quadrant** (see ``diataxis-quadrants.md``).
2. **Copy the template from this file** (section 1).
3. **Fill in the frontmatter** (see ``frontmatter-schema.md``).
4. **Write the body** observing the style canon (section 2).
5. **Go through the anti-patterns** (section 3) — what did I almost
   commit?
6. **Work through the quality checklist** (section 4) — all 15 green?
7. **Write the file.**

Steps 5 and 6 together take about 2-3 minutes — the cheapest quality lever
in the entire docs pipeline.
