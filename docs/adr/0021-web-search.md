---
title: "ADR-021: Web-search skill — architecture and risk classification"
slug: adr-021-web-search
diataxis: adr
status: accepted
date: 2026-05-27
owner: harald
phase: skills
audience: developer
y_statement: >
  In the context of building a Gemini-routed web-search skill that the
  router-tier brain can dispatch from voice and text turns,
  facing the tension between safety-tier mutability (a TOML-driven risk
  tier can be silently downgraded by self-mod or by a parallel-session
  drift) and the need for the skill to live in plain Python so reviewers
  can grep one place,
  we decided to hardcode `risk_tier = "monitor"` as a module-level Final
  constant inside `src/skills/web_search/skill.py` and re-export it from
  the package `__init__.py`,
  to achieve a single, greppable source of truth that cannot be flipped
  at runtime by editing `jarvis.toml`,
  accepting that changing the risk classification now requires a code
  edit + ADR amendment rather than a config tweak.
---

# ADR-021 — Web-search skill (Gemini-routed, monitor-tier)

**Status:** Accepted (2026-05-27, Wave-1)
**Phase:** Skills — first cloud-search skill
**Pairs with:** ADR-0011 (Router pure dispatcher), ADR-0010 (Output filter pattern-based)

## Context

The router-tier brain needs a cheap, cloud-only way to answer factual
"what is X / who is Y / when did Z happen" questions without spawning a
full OpenClaw mission. A dedicated `web_search` skill — backed by
Gemini's grounded-search mode — fills that gap.

Two architectural pressures shaped the design:

1. **Safety classification must be tamper-resistant.** The existing
   risk-tier system (`jarvis/safety/risk_tier.py`) reads tier mappings
   from configuration. For a skill that fans out to the open web,
   classification drift via TOML edit (or via the soft-disabled but
   still-present self-mod allowlist path) is a credible vector. The
   maintainer's history with config-drift (BUG-010 triple-defense,
   BUG-018 BOM corruption) makes a config-only safety surface the wrong
   default for an outbound network skill.
2. **The voice path has a tighter latency budget than the text path.**
   The existing voice contract (ADR-OE1, ADR-0010, `scrub_for_voice`)
   demands sub-second perceived latency and no LLM-side scrubbing. The
   skill must mirror that contract — strip markdown / URLs from the
   spoken summary using pure regex, never via a second model call — and
   apply an asymmetric budget (`VOICE_LATENCY_BUDGET_MS = 2_500`,
   text = 8_000) when a turn arrives from the voice pipeline.

## Decision

The skill is composed of five small modules under
`src/skills/web_search/`:

| Module | Responsibility |
|--------|----------------|
| `_sanitize.py` | NFKC-normalise, strip control chars, reject prompt-injection tokens, cap length at `MAX_QUERY_LEN = 512`. Total over `str`. Property-tested with Hypothesis. |
| `_gemini_client.py` | `GeminiClient` `Protocol` + `DefaultGeminiClient` (lazy SDK import) + `FakeGeminiClient` (deterministic test double with controllable simulated latency). |
| `_voice_override.py` | Frozen `SearchSettings` dataclass + `apply_voice_override` (pure tightening function) + `scrub_for_speech` (regex-only). |
| `skill.py` | `WebSearchSkill` composer + frozen `SkillResult`. **Hardcodes `SKILL_RISK_TIER: Final[str] = "monitor"`** and re-exports it as `WebSearchSkill.risk_tier`. |
| `__init__.py` | Public re-exports — `WebSearchSkill`, `FakeGeminiClient`, `SKILL_RISK_TIER`, etc. |

The risk-tier hardcode is the load-bearing decision. The literal `"monitor"`
appears in exactly one place (`skill.py`) and is verifiable via
`grep -r "risk_tier" src/skills/web_search/`.

## Consequences

**Positive**

- Safety classification cannot be silently flipped via `jarvis.toml`
  edit, self-mod allowlist write, or parallel-session drift.
- One grep gives a complete picture of the skill's safety stance.
- Reviewers can reason about the skill in isolation — no need to also
  audit a config file.
- The skill is fully unit-testable: `FakeGeminiClient` lets the latency
  test bound wall-clock cost without touching the network.

**Negative**

- Changing the tier (e.g. promoting to `ask` after a real-world
  incident) requires a code edit + new ADR rather than a config
  tweak. This is intentional friction.
- Power-users who want to override the tier in their environment must
  fork or subclass the skill.

**Neutral**

- The skill is currently *not* wired into `jarvis/plugins/tool/` or
  `pyproject.toml` entry-points. It lives under `src/skills/` as a
  standalone artifact. Integration into Jarvis's router-tier
  `ROUTER_TOOLS` frozenset is deferred to a Wave-2 follow-up ADR.

## Alternatives considered

1. **Config-driven tier** — rejected: see context, drift surface too
   wide for an outbound network skill.
2. **Decorator-based registration** (`@skill(risk_tier="monitor")`) —
   rejected: hides the constant behind one extra layer of indirection
   and makes `grep` noisier (decorator name + skill name + literal).
3. **Separate `_risk.py` module** — rejected: split for split's sake;
   the tier is part of the skill's identity, not a separable concern.

## Verification

- `grep -r "risk_tier" src/skills/web_search/` returns the single
  hardcoded constant + its re-exports + the class attribute.
- `tests/skills/test_web_search.py::TestSkillIdentity::test_risk_tier_is_monitor_constant`
  asserts the literal.
- Phase-1 report: `docs/reports/web-search-phase-1.md`.
