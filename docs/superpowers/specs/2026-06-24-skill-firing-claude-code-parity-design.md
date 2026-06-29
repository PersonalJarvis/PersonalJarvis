---
title: Skill firing — Claude-Code parity for the router brain
date: 2026-06-24
status: accepted
area: brain / skills
---

# Skill firing — Claude-Code parity

## Problem

Jarvis rarely invokes installed skills. The skill subsystem is fully wired
into the live voice/chat path (registry, `## AVAILABLE SKILLS` listing,
deterministic `TriggerMatcher`, inline injection, `run-skill` tool), and the
builtin skills already carry rich `when_to_use` fields with concrete trigger
phrases (17/19 RICH per the 2026-06-24 audit; `memory-save` is intentionally
deprecated). Despite that, the brain almost never picks `run-skill` on its
own.

Root cause is a routing *stance*, not missing data:

1. The router system prompt's SKILLS paragraph is one weak block buried under
   the dominant `BEI UNSICHERHEIT: MACH ES SELBST` directive, which biases the
   brain toward answering directly instead of calling a tool.
2. The brain therefore only honors a skill on an exact deterministic regex
   trigger (`TriggerMatcher`). The model-judged path — "this request is the
   kind of task skill X is for, even though the phrasing is new" — almost never
   triggers, because nothing pushes the brain to prefer it.

This is the inverse of Claude Code, where the agent is told *"if there is even
a 1% chance a skill applies, invoke it."* Claude Code can afford a per-turn
model judgement because it is a text agent with no sub-second latency budget;
its skill descriptions (often stuffed with explicit trigger phrases) are the
whole matching engine.

## Decision

Adopt the Claude-Code mechanism — **description + prompt-judged invocation** —
without adding any new call or latency. The router already runs every turn and
already carries the skill listing; we only make that same call act on it.

Three levers, in priority order:

1. **Router prompt stance (primary).** Rewrite and reposition the SKILLS
   section in `jarvis/brain/router.py::SYSTEM_PROMPT` so a plausibly-matching
   skill is the brain's *first* move, and add a carve-out so the
   `MACH ES SELBST` rule no longer reads as "skip the skill". Framing mirrors
   Claude Code: a skill is the user's saved preference for *how* a recurring
   task should be done; honoring it is the whole point. When unsure whether a
   skill applies, prefer calling `run-skill` (a wrong skill is cheap to skip; a
   missed skill defeats why the user installed it). Bounded by an explicit
   guard against firing on a plain question that merely mentions a topic, and
   by the existing AD-S9 (explicit heavy-work verb) and local-action gates,
   which still stand a skill down.

2. **Renderer framing (reinforcing).** Strengthen the intro/outro in
   `jarvis/skills/prompt_injection.py::render_available_skills_section` from a
   passive "when a request matches … call run-skill" to an imperative,
   skill-first instruction that names these as the user's installed
   preferences and tells the brain to check the list *before* answering or
   spawning a worker. Per-entry rendering (description + `when_to_use`, capped)
   is unchanged.

3. **Descriptions (no-op for now).** The builtin `when_to_use` fields are
   already rich; no wholesale enrichment. The skill-authoring path should keep
   producing rich `when_to_use` going forward (verified, not changed here).

The deterministic `TriggerMatcher` fast lane is untouched — it stays the
zero-latency path for exact triggers. We are strengthening only the
model-judged fallback that fires when no regex hits.

## Non-goals

- No embedding/vector matcher (explicitly rejected in favor of the pure
  Claude-Code prompt approach).
- No new LLM call, no new latency tier, no change to `ROUTER_TOOLS`.
- No broad "more tool calls in general" change — scope is skills only.
- No change to the risk-tier, lifecycle, or `run-skill` tool internals.

## Risks & mitigations

- **Over-firing** (invoking a skill for a plain question that merely mentions a
  topic): mitigated by an explicit prompt carve-out ("the *kind of task* a
  skill is for, not a topic mention") and by keeping the existing
  router-discipline regression suite (`tests/unit/brain/test_routing.py`)
  green.
- **Language gate**: `router.py` and `jarvis/skills/builtin/*/SKILL.md` are on
  the CI German allowlist, so bilingual trigger phrasing is permitted.

## Verification

- Renderer unit test: the rendered section is imperative and skill-first and
  surfaces `when_to_use`.
- Router-discipline regression (`tests/unit/brain/test_routing.py`) and the
  skills unit suite stay green — the change must not break the
  don't-over-spawn discipline or the deterministic match path.
- A golden-set skill-routing eval (`scripts/skill_routing_eval.py`): a list of
  paraphrased utterances → expected skill, run against the live router brain to
  measure how often `run-skill` is now chosen for a non-exact phrasing. This is
  the "as good as Claude" proof; it needs a configured provider to run.
