---
name: openclaw-doc-update
description: Use when the OpenClaw spike falsifies an assumption from docs/openclaw-bridge.md or when an Architecture Decision (AD-1..AD-21) needs to be amended due to empirical findings. Updates the bridge documentation, marks affected ADs as superseded, and adds new ADs at the end. Also triggers ADR-0012/0013 creation if the change is structural enough.
---

# OpenClaw-Bridge Doc Update

This skill updates `docs/openclaw-bridge.md` when the spike falsifies assumptions or when an architecture decision needs to be amended.

## When to use

- Spike findings in `docs/spike-results-openclaw.md` contradict an AD from `docs/openclaw-bridge.md` (e.g. AD-1 says one-shot, but the spike shows that `agent --message` always requires a daemon connect).
- During bridge implementation it turns out that an AD is not practical (e.g. AD-9 full trust is too risky after an MCP tool accepted absolute paths).
- New Phase-6/Phase-7 requirements extend the bridge contract.
- After Wave 3 or Wave 4 is done: create ADR-0012 + ADR-0013 (see bridge doc §9).

## Steps

1. **Read the existing state:** all of `docs/openclaw-bridge.md`, plus `docs/spike-results-openclaw.md` if present.

2. **Identify the conflict:** which AD or which AP is affected by the new finding/requirement? List at most 3 affected items.

3. **Choose an update strategy:**
   - **Soft amendment** (assumption was imprecise, architecture stands): refine the AD description, add an "Amendment <date>" note below the AD line.
   - **Hard supersede** (assumption was wrong, new AD needed): strike through the old AD via `~~Text~~` markup, append the new AD at the end with the next number (AD-22, AD-23, ...).
   - **Architecture revision** (multiple ADs are overturned): mark as "Architecture revision <date>" at the top of the doc, a new AD series AD-22..AD-N for the new line, comment out the old ADs instead of deleting them.

4. **Concrete edits:**
   - §2 AD table: edit the affected row or append a new row.
   - §3 architecture picture: adjust the ASCII diagram when the data flow changes.
   - §5 AP-OC list: when an AP is relaxed (e.g. AP-OC9 full trust becomes an MCP whitelist), keep the AP ID + add an amendment note.
   - §6 SP list: set the SP status to `RESOLVED — Befund: <kurz>` when the spike has resolved the question.
   - §10 residual risks: add a new risk if the change opens one up.

5. **Check cross-refs:**
   - `AGENTS.md` section 5 (AP-OC1..OC13) — if an AP was changed, update it there too.
   - `.claude/agents/openclaw-bridge-reviewer.md` — adjust the mandatory-checks list.
   - `.claude/agents/openclaw-bridge-builder.md` — adjust the forbidden patterns.
   - `CLAUDE.md` — if the Phase-6/OpenClaw section is affected.

6. **When Wave 3 or 4 is done: create ADR-0012 + ADR-0013.**
   - **ADR-0012 OpenClaw-Bridge: Subprocess model** — formalizes AD-1 through AD-9.
   - **ADR-0013 OpenClaw-Bridge: User surface** — formalizes AD-10 through AD-21.
   - Style reference from `docs/adr/0009-self-healing-worker-critic.md`.

7. **Audit entry:** at the end of `docs/openclaw-bridge.md`, a `## Änderungs-Historie` block (create it if it does not exist) with the line `<Datum> — <Kürzel> — <Was geändert>`.

## Strictly forbidden

- NO writing code — the skill is docs only.
- NO deleting old ADs — git history is the documentation; old ADs are struck through or marked as superseded.
- NO silent plan drifts: if the master plan or another plan reference deviates from this, it must be stated explicitly in the context block.
- NO softening of the hard caps (time cap 30 min, concurrency cap 3) without an empirical justification in the update body.

## Edge cases

- **Spike finding contradicts multiple ADs at once:** when 4+ ADs are affected, this is an architecture revision — update strategy 3 (hard revision), a new AD line AD-22..AD-N, mark the old ones as "obsolete since <date>".
- **Bridge doc § 11 sources shift:** if an external source (steipete blog, Wikipedia) changes the assumptions, add the new source in §11, leave the old one in place.
- **AGENTS.md / subagent docs still in the pre-update state:** if the doc update makes clear that the subagent mandatory reading is also outdated, update the subagent files in a separate mission — not in the same edit (otherwise you lose sequences).
