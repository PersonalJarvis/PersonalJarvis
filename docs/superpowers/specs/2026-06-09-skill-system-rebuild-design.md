# Skill System Rebuild — Design

**Date:** 2026-06-09
**Status:** Approved direction (4 user decisions captured below), autonomous execution
**Scope:** `jarvis/skills/`, `jarvis/plugins/tool/run_skill.py`, `jarvis/brain/manager.py` (routing guard + prompt), `jarvis/speech/pipeline.py` (direct-trigger path), builtin skill content migration, tests.

---

## 1. Problem statement

The maintainer has **never observed a single skill invocation** in real usage, despite 18 skills loading correctly at boot. Live-log forensics confirms this: the current `data/jarvis_desktop.log` contains zero `run-skill` executions and zero direct-trigger hits. Skills are a flagship feature ("we need skills for every plugin and for many tasks") and are currently effectively dead.

## 2. Diagnosis (evidence-backed)

A 17-gate trace of the utterance→skill path identified three root causes plus one design-level flaw:

**RC1 — Force-spawn intercepts skill utterances (primary).**
`BrainManager._should_force_spawn` (`jarvis/brain/manager.py:2785-2798`) evaluates *before* the brain ever sees the system prompt. Action-verb utterances ("starte die Morgenroutine") match the spawn heuristic / `_is_generic_subagent_work` and are dispatched to a Jarvis-Agents mission. The brain — and therefore the `run-skill` tool — never gets a chance. Non-plugin builtins (morning-routine, deep-work-mode) register no capability at all, so the capability registry classifies them as "action with no capability" → generic Jarvis-Agent work → force-spawn.

**RC2 — Boot race silently removes the AVAILABLE SKILLS section.**
`set_skill_context()` runs late (`desktop_app.py:1509`, inside `_start_speech_and_orb`). If the first utterance arrives before that — or if context wiring fails (broad `except` at `desktop_app.py:1459-1521`) — `try_get_skill_context()` returns `None` and `_build_system_prompt` (`manager.py:1268-1280`) silently omits the skills section. The LLM then has no reason to ever choose `run-skill`.

**RC3 — Skill "execution" silently does nothing and reports success.**
`SkillRunner` (`jarvis/skills/runner.py:203-374`) is a macro player: it Jinja-renders the body and executes `TOOL: name {json}` lines against a mini tool registry built from no-arg-constructible `jarvis.tool` entry points (`desktop_app.py:1491-1502`). Builtin skills reference MCP-style tools (`gmail-mcp/list_unread`, `fetch-mcp/fetch_weather`) that can never exist in that registry. Every step is silently skipped (`continue` at `runner.py:303`), the result is `success=True, steps_count=0`, and the voice path speaks the first 400 chars of raw Markdown (`pipeline.py:1705-1706`).

**Design flaw — skills are macros, not model instructions.**
The skill body's prose never reaches the LLM. The professional standard (Anthropic Agent Skills) treats SKILL.md as *instructions the model loads and follows with its own tools*; deterministic scripts are bundled resources the model chooses to run. Our system inverts this and loses all of the model's intelligence.

Secondary findings: rigid anchored trigger regexes (`^...$`) that natural speech never matches; 12 of 18 skills are thin plugin-pairing shells; bundle resources (`references/`, `scripts/`) are never loaded at runtime (no progressive disclosure); no user-visible feedback that a skill fired; `data/skill_prefs.json` absent (defaults apply — not itself a bug).

## 3. Target standard (Anthropic Agent Skills, condensed)

- **SKILL.md** = YAML frontmatter (`name` ≤64 chars `[a-z0-9-]`, `description` ≤1024 chars) + Markdown body of instructions (≤500 lines), imperative voice, third-person description containing *what it does* **and** *when to use it* ("Use when…"). Under-triggering is the dominant failure mode → descriptions are deliberately "pushy".
- **Progressive disclosure, 3 levels:** (L1) name+description always in the system prompt (~100 tokens/skill, listing budget-capped); (L2) full body loaded only on invocation (<5k tokens); (L3) bundled resources (`references/`, `scripts/`, `assets/`) read/executed on demand, zero cost until accessed.
- **Invocation mechanism is the model itself:** it reads the listing and decides; a `Skill` tool loads the body. No regex/embedding gatekeeper.
- **Scripts vs instructions:** scripts for fragile/deterministic sequences (executed, source never enters context); instructions where judgment is needed.
- **Security:** skills are trusted code; `allowed-tools` style scoping; audit before install.

Sources: anthropic.com engineering blog "Equipping agents for the real world with Agent Skills"; platform.claude.com agent-skills docs (overview, best-practices, skills-guide); code.claude.com/docs/en/skills; github.com/anthropics/skills; agentskills.io.

## 4. User decisions (binding for this rebuild)

1. **Invocation:** the brain decides via skill descriptions (Anthropic model); fixed trigger phrases remain as a guaranteed fast direct path alongside.
2. **Format:** adopt the Anthropic SKILL.md standard incl. progressive disclosure; migrate existing builtins.
3. **Execution:** light skills run inline in the brain (instructions + existing tools); long/multi-step skills are handed to the background worker/mission system.
4. **Done-bar:** full rebuild until the automated suite is green, plus an honest live-verification checklist for voice-only checks.

## 5. Approaches considered

- **A — Patch the gates only** (keep macro runner, fix force-spawn/boot-race/honesty). Cheapest, but skills remain dumb macros referencing tools that don't exist; rejected — does not reach professional standard.
- **B — Instruction-skill rebuild (chosen):** skill body becomes model instructions; `run-skill` returns the rendered body for the brain to follow with its own tool surface; routing guard makes skills win over force-spawn; direct triggers route through the brain with the skill preloaded (guaranteed execution, uniform voice output).
- **C — Hybrid macro+instruction:** keep `TOOL:` macro execution as a parallel first-class mode. Rejected as a *primary* mode (two competing execution semantics, recurring drift), but deterministic needs survive as L3 `scripts/` resources, and legacy `TOOL:` bodies get an honest deprecation path (see §6.7).

## 6. Architecture decisions

### AD-S1 — Skill body is model instructions (instruction-skill model)
`run-skill` no longer macro-executes. It resolves the skill, Jinja-renders the body (existing `render()` with `config`/time context), and returns it as the tool result with explicit framing: *"These are the skill instructions; follow them now using your available tools."* The brain executes them in the same turn (router-tier tool loop). `SkillResult`/runner macro path is retired from the invocation flow (kept only for the deprecation path in §6.7).

### AD-S2 — Progressive disclosure
- **L1:** `render_available_skills_section` keeps injecting name + description (+ new optional `when_to_use`), with the existing `max_skills` cap plus a per-entry char cap (1,536) and a total char budget; least-recently-modified skills drop first when over budget. Listing must state that `run-skill` loads the instructions.
- **L2:** the body arrives only as the `run-skill` tool result.
- **L3:** `run-skill` gains an optional `resource` argument (`{"skill_name": "x", "resource": "references/foo.md"}`) that returns a bundled file's content instead of the body. Path-traversal-safe (must resolve inside the skill root, must be a registered resource). Scripts are *not* auto-executed; the body may instruct the brain to run one via an existing execution tool, subject to normal risk tiers.

### AD-S3 — Skill-aware routing guard (fixes RC1)
In `BrainManager.generate`, *before* `_should_force_spawn` and before the smalltalk tool override: if the utterance matches an active skill — via (a) the TriggerMatcher (tolerant pass included) or (b) a paired-skill capability hit — then force-spawn is skipped for this turn, `run-skill` is guaranteed in the turn's tool set, and a one-line steering hint is appended to the turn context: *"The user's request matches installed skill `<name>` — invoke it via run-skill unless clearly wrong."* The smalltalk override must not strip `run-skill` when this guard fired. This is deterministic code, not a prompt-only hope.

### AD-S4 — Direct trigger path routes through the brain (fixes RC3 for voice)
The pre-brain hook (`pipeline.py:_try_skill_direct_trigger`) and the desktop chat hook stop calling `SkillRunner.run()` and speaking raw Markdown. On a trigger match they mark the turn as *forced-skill* (skill name + captured arg) and let the normal brain turn proceed; the brain receives the steering hint from AD-S3 plus the skill body preloaded (the `run-skill` round-trip is short-circuited by injecting the rendered body directly), and produces a real, scrubbed voice answer. Guarantee: a trigger match MUST result in the skill instructions entering the model context that turn — covered by tests. Latency note: this replaces a macro no-op with one normal brain turn; the brain turn was happening anyway on the non-match path, so worst-case added latency ≈ 0 versus a working baseline.

### AD-S5 — Light inline / heavy mission execution
New optional frontmatter field `execution: inline | mission` (default `inline`). For `mission` skills, `run-skill` does not return the body to the router LLM; instead `BrainManager` intercepts the tool result (deterministic code) and dispatches the existing `spawn_worker` path with the rendered skill body as the mission brief, returning the standard optimistic ACK. No spawn tool is ever added to a worker tool set (AP-5/D9 intact); the worker receives the body as *task text*, not as a tool. Python-side `Literal["inline","mission"]`; not yet surfaced to TS/UI (no five-layer enum needed until it crosses the wire — revisit when SkillsView displays it).

### AD-S6 — Boot-race fix + honesty + observability (fixes RC2)
- `set_skill_context()` moves to brain-build time (`factory.build_default_brain`) so the context exists before the first turn; the late desktop wiring becomes idempotent re-wiring (runner/bus upgrade), never the first registration. Paired-capability registration keeps working via the existing `set_skill_context` hook.
- If the skills section is omitted at prompt build, log one `WARNING` (rate-limited) instead of silently passing.
- New frozen `SkillInvoked` event (trace_id, skill_name, source: `model|trigger|hotkey|cron|chat`) published on every invocation path; existing `SkillStarted/Completed/Failed` stay for the legacy runner.
- Honesty rule: any legacy macro run that skips steps reports `success=False` with a clear error — never "success with 0 steps".

### AD-S7 — Builtin content migration (Anthropic format)
All 18 builtins get: pushy third-person `description` with "Use when…" trigger terms (English, per output-language policy), optional `when_to_use`, body rewritten as imperative instructions for the brain referencing only *real* router-tier tools and real connected integrations (no fictional `gmail-mcp/*` names), ≤500 lines. `morning-routine` and `deep-work-mode` are rewritten as full instruction skills; the 12 plugin-pairing skills keep their pairing frontmatter (`plugin_id`, `intent_verbs/objects`, voice patterns) and get short instruction bodies ("check the plugin's tools, then …"). `memory-save` stays disabled (B5 wiki owns that path). Existing schema fields are kept additively (`schema_version: "1"` unchanged); new fields are optional → old user skills keep parsing.

### AD-S8 — Bootstrap re-sync
The user-dir copies under `%LOCALAPPDATA%\Jarvis\skills` were copied once and never updated when builtins change (`bootstrap.py` only fills gaps). Bump `.bootstrap-version` to `3` and add a content-refresh rule: a builtin's user copy is overwritten **only if** its `SKILL.md` hash matches a known previously-shipped hash (i.e., the user never edited it); user-edited copies are left alone and logged. Mechanism: bootstrap writes a `.shipped-hashes.json` manifest (skill name → SHA-256 of the SKILL.md it shipped) on every copy/refresh; for the one-time v2→v3 migration, a static map of the v2 builtin hashes (computed from the pre-migration repo state) is embedded in `bootstrap.py`.

### AD-S9 — Explicit heavy-work trigger outranks the skill match (2026-06-10)
AD-S3's "skills win over force-spawn" was aimed at the verb/marker *heuristic*, not at the user explicitly naming the execution vehicle. When the utterance contains an explicit heavy-work trigger phrase (`force_spawn_phrases`: "Sub-Agent", "OpenClaw", "spawne", "deep dive", …), the mission path owns the turn: `generate()` clears `_skill_turn_match` (both the TriggerMatcher probe and the pending-trigger path) and `_should_force_spawn` checks the trigger pattern *before* the skill guard, in every `force_spawn_mode`. Live bug 2026-06-10 14:34: "Ich möchte, dass du für mich einen Sub-Agent spawnst … Gmail …" matched the `plugin-gmail` pairing skill, AD-S3 disarmed force-spawn, and the turn ran as a mute inline skill turn (no mission, no ACK, idle hang-up). <!-- i18n-allow: quoted German voice command from the live incident -->
Guards: `tests/unit/brain/test_skill_routing_guard.py::{test_explicit_spawn_trigger_beats_skill_match, test_generate_drops_skill_match_on_explicit_spawn_trigger}`.

## 7. Component changes (summary)

| Area | Change |
|---|---|
| `jarvis/skills/schema.py` | + `when_to_use: str \| None`, + `execution: Literal["inline","mission"]` (default inline); fields additive |
| `jarvis/plugins/tool/run_skill.py` | Returns rendered instructions (AD-S1), `resource` arg (AD-S2 L3), mission flag passthrough (AD-S5), `SkillInvoked` event |
| `jarvis/skills/prompt_injection.py` | when_to_use in bullets, per-entry + total char budget, updated framing text |
| `jarvis/brain/manager.py` | Skill-aware routing guard before force-spawn + smalltalk override (AD-S3); forced-skill turn injection (AD-S4); mission interception (AD-S5); WARNING on omitted section (AD-S6) |
| `jarvis/speech/pipeline.py` + `jarvis/ui/desktop_app.py` | Direct-trigger hooks mark forced-skill turn instead of macro-running (AD-S4) |
| `jarvis/brain/factory.py` | `set_skill_context` at build time (AD-S6) |
| `jarvis/skills/runner.py` | Honesty fix for legacy macro path; render() reused for body rendering |
| `jarvis/skills/bootstrap.py` | Version-3 refresh rule (AD-S8) |
| `jarvis/skills/builtin/**` | Content migration (AD-S7) |
| `jarvis/core/events.py` or `skills/schema.py` | `SkillInvoked` frozen event |

Out of scope (explicitly): SkillsView UI redesign, skill marketplace/catalog changes, the `ask`-tier voice confirmation TODO (tracked separately — the outer ToolExecutor confirmation pipeline still applies per tool call), retro-translating committed German content.

Implementation addendum (2026-06-10, completion pass):
- **Cron fires route through the brain** (`SpeechPipeline._handle_cron_skill`): the skill is noted (`note_skill_trigger`, source="cron"), a synthetic scheduled-run turn executes the instructions, and the reply is announced via `AnnouncementRequested` (scrubbed TTS path). Legacy macro runner remains only as the fallback for brains without the handoff (echo/mock).
- **Block-tier gate at the match source**: `_match_skill_for_turn` / `_consume_pending_skill_trigger` / the cron handler all refuse `risk_policy: block` skills — mirroring the run-skill tool gate, so a blocked skill can never capture a turn via injection either.
- **Listing total budget** (AD-S2 follow-through): `render_available_skills_section(total_char_budget=8000)` evicts least-recently-modified skills first when the block exceeds the budget; survivors keep their display order, evicted ones fold into the `… and N more` tail.
- **Hotkey triggers are declared-only (known limitation):** `TriggerMatcher.match_hotkey` has no production consumer — no global-hotkey listener is wired to skills, and building one now would collide with the central keybinds system. The `hotkey` trigger type stays in the schema as forward vocabulary; wiring it is a separate feature behind the keybinds registry.

## 8. Error handling

- Unknown skill / draft / disabled → unchanged structured `ToolResult(success=False, …)`; the truth-duty router rule reports it.
- `resource` arg outside skill root or unregistered → `success=False, error="unknown resource"` (no path traversal).
- Mission dispatch failure → falls back to inline execution of the body in the same turn, plus audited spoken correction (AD-OE6 compliance).
- Trigger match on a skill whose body fails to render → spoken error via the normal brain turn, never raw Markdown.

## 9. Testing strategy (TDD)

- **Routing guard:** utterances like "starte die Morgenroutine", "Jarvis bitte starte die Morgenroutine", "good morning, run my morning routine" must NOT force-spawn and MUST offer `run-skill` (+ steering hint). Hard negatives: e2e force-spawn hard-negatives (schick/trag/sende/bestelle/poste) keep spawning; smalltalk stays smalltalk.
- **Prompt injection:** budget caps, when_to_use rendering, WARNING-on-omit, section present at first turn after factory build (boot-race regression test).
- **run-skill:** returns instructions; resource loading incl. traversal rejection; mission flag; draft/disabled refusal unchanged (AP-15); D9 still structural.
- **Direct trigger:** match → forced-skill turn with body in context (no macro run, no raw-Markdown TTS); no-match → unchanged path.
- **Mission skills:** `execution: mission` → spawn_worker dispatched with body as brief; ACK path; fallback on dispatch failure.
- **Legacy honesty:** macro body with unresolvable tools → `success=False`.
- **Builtin lint test:** every builtin parses, description ≤1024 chars, contains "Use when", body ≤500 lines, no fictional tool names (denylist `-mcp/` pattern).
- Existing suites must stay green: `tests/unit/skills/`, `tests/unit/brain/test_routing.py` (26-case), `test_run_skill_tool.py`, `tests/integration/test_skill_listing_in_prompt.py`, `test_skill_trigger_e2e.py`, `tests/unit/speech/test_pipeline_skill_hook.py`.

## 10. Live verification checklist (user, voice)

1. "Jarvis, starte die Morgenroutine" → real briefing content (not a mission ACK, not Markdown read-aloud).
2. "Guten Morgen Jarvis, wie sieht mein Tag aus?" → brain picks morning-routine via description (model-decided path).
3. "Aktiviere den Fokusmodus" → deep-work-mode runs inline.
4. A long task phrased to hit a `mission` skill → optimistic ACK, then completion announcement.
5. Skills UI: toggling a skill off prevents both paths; toggling on restores them.
6. Logs/flight recorder show `SkillInvoked` for each of the above.
