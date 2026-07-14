---
description: Generate a condensed Phase-6 handoff doc for continuation in a new session (what was built, what runs live, what is open, which subagents/tools are relevant).
allowed-tools: Read, Grep, Glob, Bash(git log:*)
argument-hint: (no args)
---

Create a compact handoff doc at `docs/phase6-handoff-$(date +%Y%m%d).md` so a new session can continue without losing context.

Contents of the handoff doc:

1. **Sources** — the canonical documents a new agent must read first (the `CLAUDE.md` Phase-6 section, `docs/openclaw-bridge.md`, `docs/adr/0009-self-healing-worker-critic.md`, `AGENTS.md`).

2. **What is in place (modules + LOC)** — table of the modules under `jarvis/missions/` with Glob + `wc -l`. Which sub-phase each module covers.

3. **Test state** — last documented state from `docs/phase6-test-report.md` plus the most recent commits via `git log --oneline -10`.

4. **Wiring points** — where Phase 6 hooks into the rest of the system: `jarvis/ui/web/server.py` `_init_mission_stack`, `jarvis/brain/manager.py` spawn path, `jarvis/missions/voice/` Kontrollierer.

5. **Open items** — A2-B1 (lock-holding), A2-B2 (event-payload PII), any drift against the plan doc.

6. **OpenClaw bridge relation** — Phase 6 is used by the OpenClaw bridge. The worker internals are replaced, the skeleton stays. Reference `docs/openclaw-bridge.md`.

7. **Relevant subagents** — `jarvis-architect-explorer` (Phase 0-7 research), `jarvis-test-runner` (Phase-6 pytest), `jarvis-critic-design-reviewer` (Critic-Loop design), `jarvis-reviewer` (worker-output adversarial). Reference the `.claude/agents/INDEX.md` decision tree.

8. **Slash commands** — `/p6-status`, `/p6-explore`, plus the skills named in CLAUDE.md, `phase6-smoke-test`, `phase6-adr-update`.

9. **First steps of a continuation session** — three concrete actions the next agent can take (e.g. "read docs/openclaw-bridge.md §4-§5", "spawn /p6-status for the current state", "check whether B1+B2 are still open").

Write the doc in English, without emojis, with tables where sensible. Maximum 800 lines.
