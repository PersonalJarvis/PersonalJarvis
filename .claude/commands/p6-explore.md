---
description: Spawns jarvis-architect-explorer for a Phase-6 research question (Mission-Manager, Critic-Loop, Worker-Layer, Worktree isolation, Kontrollierer). Read-only, file:line evidence.
allowed-tools: Task
argument-hint: <research question>
---

Spawn the subagent `jarvis-architect-explorer` with the following task:

**Research question:** $ARGUMENTS

**Scope:** Phase 6 (`jarvis/missions/`) plus the connection points in `jarvis/brain/manager.py` (spawn path), `jarvis/ui/web/server.py` (_init_mission_stack), `jarvis/missions/voice/` (Kontrollierer).

**Mandatory reading:** `AGENTS.md`, the `CLAUDE.md` Phase-6 section, `docs/adr/0009-self-healing-worker-critic.md`, `docs/phase6-test-report.md`. For OpenClaw-related questions, additionally `docs/openclaw-bridge.md`.

**Output:** Architecture report in the format from the subagent front-matter — components, interfaces, data flow, risks-on-change. Maximum 400 words. Every claim with a `file:line` reference.
