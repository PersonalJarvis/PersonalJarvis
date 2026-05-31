---
name: phase6-adr-update
description: Use when a Phase-6 architecture decision needs to be amended or a new Phase-6 ADR needs to be written (e.g. MAX_CRITIC_LOOPS bumped, Worker-Sandbox swapped, Critic-Prompt changed). Generates a new ADR file under docs/adr/ following the existing 0001-0011 style — Context, Decision, Consequences, Alternatives — and updates ADR-0009 if amendment.
---

# Phase-6 ADR Update / Amendment

This skill writes a new ADR or amends an existing ADR in the Phase-6 family. Style and structure follow `docs/adr/0001-0011*` — concise, with decision and consequences.

## When to use

- A new Phase-6 architecture decision is due (e.g. a new worker-backend type, a new sandbox strategy, a Critic-Verdict schema extension).
- An existing decision must be revised (e.g. the ADR-0009 hard cap MAX_CRITIC_LOOPS=3 is changed to 5 after empirical evidence).
- After a successful Phase-6 increment that is not covered by an existing ADR.

## Steps

1. **Scan the existing ADRs:** `ls docs/adr/` — determine the highest number; the new ADR gets next-N.

2. **Read the style reference:** `docs/adr/0009-self-healing-worker-critic.md` is the Phase-6 style reference. Format:
   ```
   # ADR-NNNN — Short Title

   **Status:** Accepted | Superseded by ADR-XXXX | Amended <Date>
   **Date:** YYYY-MM-DD
   **Reference:** <Phases / Plan-Section / Predecessor-ADR>

   ## Context
   <why this decision is needed now>

   ## Decision
   <exactly what is being decided, in 3-5 sentences>

   ## Consequences
   - <positive outcome>
   - <negative outcome / trade-off>
   - <what we can no longer do>

   ## Alternatives considered
   - <Option A>: why rejected
   - <Option B>: why rejected

   ## Follow-up items
   - <what must change in the code>
   - <which tests must be created>
   - <which documentation must be updated>
   ```

3. **If an amendment** (no new ADR, instead ADR-0009 or another is extended):
   - Set the status line to `Amended YYYY-MM-DD`.
   - Append a new section `## Amendment <Date>` at the end with the update content.
   - Do not delete any existing sections — git history stays clean.

4. **If a new ADR:** numerically next, filename `docs/adr/NNNN-short-title.md`. Examples of Phase-6 topics:
   - ADR-0014 Critic-Verdict-Schema-V2
   - ADR-0015 Worker-Backend-Containerized
   - ADR-0016 Mission-Reattach-Strategy

5. **Update CLAUDE.md:** the Phase-6 section in `CLAUDE.md` references the current ADRs. If the new ADR is Phase-6-relevant: add it.

6. **Verification:**
   - `markdown-lint` if available (otherwise visually).
   - Consistency check: ADR number identical everywhere, date correct, status correct.

## Strictly forbidden

- NO writing code — an ADR is docs.
- DO NOT conceal plan drift: if the new ADR deviates from the master plan, it must be stated explicitly in the context block.
- NO hard-cap loosening without empirical verification: MAX_CRITIC_LOOPS=3 is hardcoded per ADR-0009 Decision §2 — anyone who wants to change it must bring empirical data + justification, not just a gut feeling.

## Edge cases

- **Conflict with the master plan:** the plan at `<your-home>\.claude\plans\also-er-muss-auch-lexical-pond.md` is binding. On a conflict between the plan and a new ADR: quote the plan section and justify why the ADR deviates from it.
- **Multiple ADRs at once:** if a larger block (e.g. a new sub-phase) needs multiple ADRs, create them sequentially with cross-refs.
- **Retiring an ADR:** set the status to `Superseded by ADR-NNNN`, keep the content. Never delete.
