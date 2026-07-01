# Skills-Brain-Integration — Project Context

**Branch:** `skills-brain-integration` (from `main`)
**Plan owner:** Claude Opus 4.7
**Status:** 2026-04-30
**Master plan (private):** `<USER_HOME>\.claude\plans\okay-ich-w-re-bereit-jolly-gem.md` <!-- i18n-allow -->

## Problem

The skill system (Phase 1c) is fully built (`SkillRegistry`, `TriggerMatcher`, `SkillRunner`, lifecycle, validator, five builtin skills, 22 UI endpoints), but **dead** in the production path:
- `TriggerMatcher` is not invoked in any production file — only tests, CLI, UI listing.
- The brain (main Jarvis Haiku 4.5 + Jarvis-Agent Opus 4.7) has no skills awareness — neither in `ROUTER_TOOLS`/`SUB_TOOLS` nor in the system prompt.
- Skills can currently only be started via CLI or manual UI buttons.

## Solution (hybrid Anthropic pattern)

1. **TriggerMatcher production wiring** as a pre-brain hook in `jarvis/speech/pipeline.py` — deterministic voice/hotkey/cron direct paths without a brain round-trip (Skills-1).
2. **Skill list in the system prompt** as a `## VERFÜGBARE SKILLS` (available skills) section with name + description + risk-tier tag (Skills-2). <!-- i18n-allow -->
3. **`run_skill` meta-tool** in `ROUTER_TOOLS` — the brain decides semantically via the description (Skills-3).
4. **Skill-body injection (progressive disclosure)** — on a `run_skill` call, the body is injected into the brain as an ephemeral system message for **that turn** (no cache mix).
5. **Voice confirmation for ASK-tier skills** via the end-focus echo pattern (Skills-5).

## Affected surfaces

- `jarvis/speech/pipeline.py` (pre-brain hook)
- `jarvis/brain/factory.py` (`ROUTER_TOOLS`, `_load_tools_for_tier`)
- `jarvis/brain/manager.py` (`_build_system_prompt`)
- `jarvis/skills/trigger_matcher.py` (state-filter bonus fix)
- `jarvis/plugins/tool/run_skill.py` (NEW)
- `jarvis/skills/skill_context.py` (NEW, singleton holder)
- `jarvis/core/events.py` (2 new events)
- `pyproject.toml` (entry-point)
- `docs/adr/0011-router-pure-dispatcher.md` (amendment)
- `docs/adr/0012-skills-brain-integration.md` (NEW)
- `docs/adr/0005-lightweight-scheduler.md` (amendment)

## Important constraints

1. **AP-1 (constraint self-bypass):** Draft skills are structurally blocked from the `run_skill` tool (filter in Python, not in the prompt).
2. **D9 (recursion guard):** `run_skill` is not callable from skill bodies (structurally — `run_skill` not in `SkillRunner.tool_registry`).
3. **Persona mandate:** `scrub_for_voice` remains mandatory for skill TTS. Voice latency <800ms p95.
4. **Awareness-layer compat (A0–A5):** Bus events `SkillStarted/StepExecuted/Completed` keep flowing. New events forward-compatible with the `activation_path` discriminator.

## Branch strategy

New branch from `main` — a parallel agent works on `latency-sprint-2-caching` on tool adapters (`email_list_unread.py`, `calendar_list_today.py`, modified SKILL.md files). This work is secured in the stash `wip: parallel-agent-work-on-latency-sprint-2-caching`.

**IMPORTANT:** The plan is based on a `phase-8-review-pipeline` snapshot (CLAUDE.md, ROUTER_TOOLS, manager.py line numbers). `main` is an older state — file paths and line numbers must be verified per phase against the main state before code is written.

## Effort & phases

| Phase | Delivery surface | Effort |
|-------|----------------|---------|
| Skills-1 | TriggerMatcher live, cron bootstrap | 2.0 days |
| Skills-2 | Brain knows the skill list | 1.5 days |
| Skills-3 | `run_skill` tool + body injection | 2.5 days |
| Skills-4 | ADRs + hardening + smoke suite | 1.5 days |
| Skills-5 | Voice confirmation for ASK-tier | 1.5 days |
| **Total** | | **9 days** |
