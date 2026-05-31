# Diataxis Quadrants — Deep Reference

Source: https://diataxis.fr/ (Daniele Procida).
Adopted by Canonical/Ubuntu, Cloudflare, Gatsby, among others. In the Jarvis
documentation it is the hard classification anchor per doc.

## The two axes

- **Action ↔ Cognition** — what to do vs. what to know.
- **Acquisition / Study ↔ Application / Work** — learning vs. working.

The intersections yield the four quadrants. Every doc lives in **exactly
one** of them. Mixed docs are anti-pattern AP-D-1 — split and cross-link them.

## Quadrant 1: Tutorial (Action × Study)

**Purpose:** The reader is a learner. Through guided doing, they should build
skills and confidence — not solve a task.

**Language:** Imperative, the "we" form is OK ("Now we build..."), 2nd
person, concrete steps with expected output after each step.

**Structure:** Linear. Clear beginning-middle-end. Mid-point checks
("You should now see..."). Guaranteed to work.

**Forbidden:**
- Offering options ("you can also use X").
- Drifting into depth ("under the hood the system...").
- Documenting failure cases / edge cases — tutorials must *run
  successfully through*.
- Explaining concepts — that is the explanation quadrant.
- Becoming abstract — everything is concrete and executable.

**Example titles in the Jarvis context:**
- "Bring up Phase 6 locally from scratch"
- "Get the first voice session running"
- "A new skill from idea to active"

## Quadrant 2: How-To Guide (Action × Work)

**Purpose:** A competent reader has a **concrete problem** and wants to
solve it. They know what they are doing — they just need the path.

**Language:** Imperative, 2nd person, **problem in the title**: "How do I
deploy X via SSH", "Add a new brain provider". Prerequisites stated
explicitly at the top. Focused step sequence. Options + variants OK ("if X,
do Y").

**Structure:** A sequence, but not necessarily linear. Required prior
knowledge clearly marked. End state defined.

**Forbidden:**
- Trying to teach ("for that you first have to understand what X is...").
- Covering every eventuality — a how-to solves *one* problem, not all.
- Explaining concepts at length — a cross-link to a concept doc is enough.
- Drifting away from the main question.
- Code without a clear end state ("then everything is finished").

**Example titles in the Jarvis context:**
- "Add a brain provider"
- "Promote a skill from draft to active"
- "Switch the TTS provider by voice"
- "Switch the vision provider to multi-monitor"

## Quadrant 3: Reference (Cognition × Work)

**Purpose:** A fact supplier while working. API spec, config schema,
flag lists, event catalog.

**Language:** Descriptive, neutral, **3rd person** ("the function
returns...", "the EventBus emits..."). Structured like a map:
index, tables, sorted alphabetically or logically.

**Structure:** Exhaustive, tight against the code. Every entry with a mini
example (1-3 lines is enough). Grouped alphabetically or logically.

**Forbidden:**
- Telling stories ("this endpoint was introduced in Phase 3 because...").
- Guiding tutorial-style ("first you call X, then...").
- Opinions ("this is the recommended method") — that belongs in explanation.
- Leaving gaps — if a field exists, it belongs in the reference.
- Omitting examples — 1 mini snippet per entry is mandatory.

**Example titles in the Jarvis context:**
- "EventBus event catalog"
- "jarvis.toml schema"
- "ROUTER_TOOLS / SUB_TOOLS tool list"
- "Plugin group registry"

## Quadrant 4: Explanation / Concept (Cognition × Study)

**Purpose:** The reader wants to *understand why*. Architecture, design
decisions, trade-offs, alternatives, the context of a subsystem.

**Language:** Reflective, contextualizing. Diagrams, comparisons,
trade-offs, alternatives. Longer prose is allowed, but every paragraph is
front-loaded (conclusion first).

**Structure:** Argumentative. "What is it" → "How does it work" →
"Why this way and not another" → "Which alternatives were on the table".

**Forbidden:**
- Becoming step-by-step — that is tutorial / how-to territory.
- Making code snippets central — snippets only as evidence, not as
  content.
- Reference lists — if you need a table of all EventBus events,
  that is a reference doc.
- Having the reader solve tasks ("to achieve X, do Y").

**Example titles in the Jarvis context:**
- "BrainManager routing discipline"
- "Risk-tier system"
- "Why sub-Jarvis spawn instead of tool call"
- "Self-healing worker-critic architecture"

## ADR — the special fifth case

An ADR (Architecture Decision Record) is a **structured explanation**
with a fixed format: Context → Decision → Consequences → Alternatives.
Home: ``docs/adr/NNNN-slug.md``.

An ADR shares the quadrant (Cognition × Study) with explanation, but
differs through:

- **Fixed sections** (Context / Decision / Consequences / Alternatives).
- **Status lifecycle** (proposed → accepted → deprecated → superseded).
- **Numbered identity** (ADR-0009, append-only).
- **Cross-links to previous ADRs** when the ADR supersedes an earlier
  decision.

Treat ADR as its own doc type in the frontmatter (``diataxis: adr``), even
if it theoretically falls under explanation.

## Troubleshooting — the special sixth case

Troubleshooting docs are a hybrid form: symptom → cause → fix,
entry by entry. They have:

- **Reference character** (entry-based, searchable, exhaustive).
- **How-to character** (each entry contains an action to fix the problem).

Treat troubleshooting as its own doc type in the frontmatter
(``diataxis: troubleshooting``) — not as reference, because the reader is
not looking for a fact but for a fix.

In the Jarvis repo: the model is the ``BUG-001..BUG-005`` list in
``MEMORY.md`` (each bug = one entry with symptom, cause, fix). A
troubleshooting doc bundles several such entries.

## Tutorial-vs-How-To test

If you are unsure what you are currently writing:

**Test 1 — When does the reader read this doc?**
- *While working* (problem in mind, time pressure) → **How-To**.
- *Away from work* (apprentice mode, expecting a safety net from the
  author) → **Tutorial**.

**Test 2 — What happens if a step fails?**
- A tutorial must be guaranteed to work — *no* "if/else", *no*
  "in case this doesn't work". If failure is possible: how-to.
- A how-to may document failure — the reader is competent.

**Test 3 — How is success defined?**
- Tutorial: the reader has *learned something* (confidence score rises).
- How-To: the reader has *solved the task* (end state reached).

## Reference-vs-Explanation test

**Test 1 — What is the reader looking for?**
- A *fact* ("which default port?", "which fields does event X have?") →
  **Reference**.
- *Understanding* ("why do we use ChromaDB?", "how does the
  risk-tier system work conceptually?") → **Explanation**.

**Test 2 — How does the reader read the doc?**
- Scanning, Ctrl-F, targeted jump → **Reference**.
- Linearly from top to bottom → **Explanation**.

**Test 3 — What happens when the code changes?**
- Reference must update *automatically* along with it (ideally CI-tested) —
  any drift is a bug.
- Explanation ages more slowly and is checked manually when the
  architecture drifts.

## Quadrant mapping for Jarvis modules (short list)

| Jarvis module | Probably needs... |
|---|---|
| ``BrainManager`` (routing logic) | concept (why a smart fallback chain) + reference (tier list, routing patterns) |
| ``EventBus`` | reference (event catalog) + concept (patterns: subscribe_all, _safe_dispatch) |
| Skill system | concept (lifecycle) + how-to (create a skill) + reference (frontmatter schema) |
| Voice pipeline (Phase 1) | tutorial (from scratch to the first session) + how-to (switch the TTS provider) + concept (wake-word pattern) |
| Risk-tier executor | concept (4 tiers) + reference (tool→tier mapping) |
| Self-mod (Phase 7) | concept (allowlist instead of denylist) + how-to (mutate a new setting by voice) + adr (ADR-7.x per decision) |
| Phase-6 self-healing | concept (worker-critic loop) + reference (mission states) + adr (ADR-0009) |

If a module needs several quadrants: write several files,
cross-link them. Never cram everything into one.
