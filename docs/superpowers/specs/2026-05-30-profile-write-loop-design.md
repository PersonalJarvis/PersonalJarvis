# Profile-write loop — `update_profile` router tool

**Date:** 2026-05-30
**Status:** Implemented
**Goal:** Make the personalized profile section work end-to-end — Jarvis
auto-remembers durable personal facts, loads the profile into its runtime
context, and the Desktop "Knowledge matrix" reflects it.

## Findings (from the understand workflow)

- **Context injection already works.** `BrainManager._build_system_prompt`
  (manager.py:989) injects `UserProfile.render_for_prompt()` into the system
  prompt on **every turn**, unconditionally; the dispatcher (with that prompt)
  is rebuilt per turn (manager.py:2275 → `_build_dispatcher` → 897). So after a
  profile mutation, the next turn sees it — no cache invalidation needed.
- **The matrix reads the same structured profile** (`/api/profile` →
  `brain._user_profile.meta`). After the `dsd` frontmatter fix it shows the
  data again.
- **The write path is the gap.** The legacy `Curator` that auto-wrote the five
  clusters is soft-disabled (`[memory.legacy_curator] enabled=false`, B4) to
  avoid "two diverging notebooks" with the WikiCurator. The active WikiCurator
  only writes free-form wiki prose (its LLM prompt has zero instruction to fill
  structured identity fields). So nothing updates USER.md clusters anymore.

## Decision

Option **B — a deterministic, brain-driven `update-profile` router tool**
(rejected: A re-enable legacy curator = drift + per-turn LLM cost; C extend
WikiCurator = couples structured profile to idle/voice ingest cadence, invasive).

The brain persists a fact only when it consciously calls the tool — the
`wiki-ingest` precedent for the structured profile. No second background
extractor, no drift, cloud-first, immediate effect.

## Implementation

1. **`jarvis/plugins/tool/profile_update.py`** — `UpdateProfileTool` (`name =
   "update_profile"`, `risk_tier = "monitor"`). Validates cluster + canonical
   field allow-list; list fields append+dedupe, scalars set, bools coerced;
   do-not-record privacy gate; mutates the live `UserProfile` (`set`/
   `append_list` + `append_observation` + atomic `save`); emits `ProfileUpdated`.
2. **`jarvis/brain/factory.py`** — `"update-profile"` in `ROUTER_TOOLS` + a
   `_load_tools_for_tier` branch injecting `profile_resolver=lambda: user_profile`
   (the same instance the manager renders from) + `bus`.
3. **`pyproject.toml`** — entry-point `update-profile = …:UpdateProfileTool`
   (requires `pip install -e . --no-deps`).
4. **`jarvis/brain/manager.py`** — system-prompt directive ("PROFIL-PFLEGE …")
   added only when the tool is wired (avoids the "do not invent tools" conflict).
5. **Tests** — `tests/unit/plugins/tool/test_profile_update.py` (9) +
   `tests/unit/brain/test_routing.py` (router membership, factory wiring, exact
   set, directive present/absent).
6. **ADR-0011 amendment** "Profile-Write Router Tool".

## The closed loop

User states a durable personal fact → router-brain (instructed by the directive)
calls `update_profile` → USER.md cluster updated atomically + `ProfileUpdated`
emitted → Desktop matrix live-updates + the next turn's system prompt includes
the new fact (rendered from the same mutated instance).

## Out of scope (follow-ups)
- Facts about OTHER people (PersonStore writer) — this tool is user-only.
- A live LLM end-to-end test (needs a real provider); the mechanism is proven by
  unit + wiring + directive tests + an offline real-data E2E demo.
