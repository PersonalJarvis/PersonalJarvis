---
description: Phase-6 Self-Healing-Worker-Critic implementation status — which sub-phases are done, test state, open follow-ups (B1 lock-holding, B2 event payload).
allowed-tools: Read, Grep, Glob, Bash(git log:*), Bash(pytest:*)
argument-hint: (no args)
---

Create a Phase-6 status report. Proceed in this order:

1. Read the `CLAUDE.md` section "Phase 6 — Self-Healing Worker-Critic" — extract the five sub-phase table (Foundation, Worker-Layer, Critic-Loop, UI/API, Safety+Voice).
2. For each sub-phase, verify whether the files listed in the table exist under `jarvis/missions/` (Glob).
3. Read `docs/phase6-test-report.md` if present — extract the most recent documented test state.
4. Run via Bash: `git log --oneline -20 -- jarvis/missions/` for the most recent activity.
5. Read the status block of `docs/adr/0009-self-healing-worker-critic.md`.
6. Check whether the A2 Codex review items B1 (lock-holding) and B2 (event-payload PII) are already fixed — Grep in `jarvis/awareness/story/` for lock patterns + PrivacyFilter calls.

Deliver a compact Markdown table:

```
## Phase-6-Status

| Sub-Phase | Files | Tests | ADR | Status |
|---|---|---|---|---|
| 1 Foundation | N/M Files present | N/M Tests passing | ADR-0009 §X | DONE/PARTIAL/MISSING |
| ... |

## Letzte Commits
<git log output, max 10 lines>

## Offene Follow-Ups
- B1 Lock-Holding: <FIXED|OPEN>, Beleg `<file:line>`
- B2 Event-Payload-PII: <FIXED|OPEN>, Beleg `<file:line>`

## Recommended next step
<ein Satz>
```

Maximum 300 words. No summaries without `file:line` or a test name.
