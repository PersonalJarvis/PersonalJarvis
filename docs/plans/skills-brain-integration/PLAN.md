# Skills-Brain-Integration — Phase Plan

**Master-plan source:** `<USER_HOME>\.claude\plans\okay-ich-w-re-bereit-jolly-gem.md` <!-- i18n-allow -->

## Phase Skills-1 — Pre-Brain-Hook MVP (2.0 days)

**Goal:** TriggerMatcher live in the speech pipeline. Voice/hotkey/cron direct matches fire without a brain round-trip.

**Building blocks:**
- **D — TriggerMatcher Pre-Brain-Hook:** `jarvis/speech/pipeline.py:_handle_utterance` between the STT hallucination guard and `_complete_or_buffer_context`.
- **E — Cron-Scheduler bootstrap:** `TriggerMatcher.run_cron_scheduler()` as an `asyncio.create_task` in `SpeechPipeline.run()`.
- **H slice 1 — Conflict rule:** Direct match → `SkillDirectTriggered` event → brain path not entered. Idempotency window 5s.

**Foundation chunks (in this order):**
1. `jarvis/skills/skill_context.py` — singleton holder (~40 LOC)
2. `SkillDirectTriggered` event in `jarvis/core/events.py`
3. `match_voice()` state filter in `trigger_matcher.py` (DRAFT skip)
4. Pipeline hook in `jarvis/speech/pipeline.py:_handle_utterance`
5. Cron-scheduler task in `SpeechPipeline.run()`
6. Voice-trigger-pattern audit for the 5 builtins (R10 mitigation: imperative-specific)

**Tests:**
- `tests/unit/skills/test_trigger_matcher.py` (bonus: DRAFT skip)
- `tests/unit/speech/test_pipeline_skill_hook.py` (NEW)
- `tests/integration/test_skill_direct_trigger_e2e.py` (NEW)

**Demo:** "guten morgen" (good morning) → `morning-routine` runs → trace shows 0 brain tokens. <!-- i18n-allow -->

---

## Phase Skills-2 — Brain-Discovery Read-only (1.5 days)

**Goal:** The brain *knows* the skills in the system prompt. It cannot call them yet.

**Building blocks:**
- **B — Skill-list injection:** `BrainManager._skill_list_block()` in `manager.py`, invoked from `_build_system_prompt()`. Format: Markdown list `name: description [TIER]`. Stable sort.
- **C — Risk-tier annotation:** Per skill, `risk_policy.default_tier` as a tag.

**Demo:** "welche Skills hast du" (which skills do you have) → brain answers with the live list. <!-- i18n-allow -->

---

## Phase Skills-3 — `run_skill` Meta-Tool (2.5 days)

**Goal:** The brain calls skills actively. Central phase.

**Building blocks:**
- **A — `run_skill` tool:** `jarvis/plugins/tool/run_skill.py` (~120 LOC). DI: `SkillRegistry` + `SkillRunner` via constructor.
- **I — Skill-body injection (progressive disclosure):** On invocation, the body is injected into the brain as an **ephemeral** additional system message for that turn. No cache mix with the router prefix!
- **H final — Idempotency:** `run_skill` checks the `SkillDirectTriggered` event of the last 5s for the same skill → skip.

**Demo:** "Mach mal Deep-Work für 90 Minuten" (do deep work for 90 minutes) → brain calls `run_skill('deep-work-mode', {duration_min:90})`. <!-- i18n-allow -->

---

## Phase Skills-4 — ADR + Hardening (1.5 days)

**Building blocks:**
- ADR-0012 new, ADR-0011 amended, ADR-0005 amended
- Feature flag `[skills.brain_integration]` in `jarvis.toml`
- Smoke suite (30 cases)
- Token-budget telemetry
- Latency gate p95 ≤800ms

---

## Phase Skills-5 — Voice-Confirmation for ASK-Tier (1.5 days)

**Building blocks:**
- End-focus echo template
- Deterministic yes/no pattern (no LLM)
- UI tile in `SkillsView`
- User docs `docs/skills-brain-integration.md`

**Demo:** ASK-tier skill → voice echo "X starten. Bestätigen?" (start X. Confirm?) → "ja" (yes) → run. <!-- i18n-allow -->
