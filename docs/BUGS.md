---
title: Bug Register Jarvis-Agent Pipeline
date: 2026-04-29
scope: Voice → Router → Jarvis-Agent-Spawn → Harness-Dispatch
---

# Bug Register: Jarvis-Agent Pipeline (2026-04-29)

This register documents every root cause found and fixed around the
"Spawn sub-agents." voice failures. Per bug: symptom (what the user hears),
root cause (code path), fix (file:line + test), and regression guard.

**Foundation**: When a bug from this list recurs, the associated test
fires first. Anyone who patches a test backwards without an ADR
update drives the root cause back into production.

## Context: What was broken

The voice command "Spawn sub-agents." (or variations) led to a cascade:
1. Router-Brain (Grok) responds with smalltalk instead of spawning.
2. If the spawn tool is invoked after all, the BrainManager falls through
   all providers (Grok → Gemini → Claude → GPT) because of empty text output.
3. Each provider repeats the spawn attempt — the second/third lands
   in the JARVIS_DEPTH recursion guard ("recursion denied").
4. The last provider attempts `multi_spawn` → 3 parallel harness dispatches
   all crash in <10ms because of a shared-state race.
5. User hears "Die parallele Ausführung der Sub-Agenten ist fehlgeschlagen." (The parallel execution of the sub-agents has failed.) <!-- i18n-allow -->

Token cost per failed spawn: ~40k tokens × 4 providers ≈ $0.13.

---

## Bug #1: `spawn` missing in `spawn_verbs` (CRITICAL)

- **File**: `jarvis/core/config.py:156-168` (BrainRoutingConfig.spawn_verbs)
- **Symptom**: User says "Spawn sub-agents.", Router-Brain responds
  "Hallo Alex, was kann ich für dich tun?" ("Hello Alex, what can I do for you?") — no action. <!-- i18n-allow -->
- **Root cause**: The `_should_force_sub_jarvis` heuristic matches against the
  `spawn_verbs` list (DE+EN). The list contained `umsetz`, `bau`, `oeffne`,
  `deploy`, `read`, `write`, `build`, `open`, `install` — but not
  `spawn`, `starte`, `start`. → Heuristic returned `False` → Brain receives
  the utterance, LLM autonomously chooses smalltalk (persona mandate: a pure
  dispatcher responds to anything non-concrete with a greeting).
- **Fix** (`jarvis/core/config.py:165-170`): list extended with
  `"spawn", "starte", "start", "starten", "startet", "delegier"`.
- **Regression guard**: the existing routing suite
  `tests/unit/brain/test_routing.py` covers pattern matching.
  Manual verification:
  ```python
  from jarvis.brain.manager import _build_verb_pattern
  from jarvis.core.config import BrainRoutingConfig
  re = _build_verb_pattern(BrainRoutingConfig().spawn_verbs)
  assert re.search("spawn sub-agents.")
  ```

## Bug #2: `multi_spawn` 5ms crash via shared-state race (CRITICAL)

- **File**: `jarvis/harness/base.py::SubprocessHarness` (old version)
- **Symptom**: `multi_spawn(harness="openclaw", prompts=[3])` returns
  `success=False, duration_ms=5, error="one or more sections with non-zero exit"`. Production logs show 3× HarnessDispatched, NO
  HarnessProgress, NO HarnessCompleted.
- **Root cause**: `HarnessManager.get(name)` cached ONE
  `JarvisAgentHarness` (that harness class has since been removed;
  `SubprocessHarness` is the current base class) instance. `SubprocessHarness.invoke()` wrote
  `self._process` and `self._cancelled` as instance state. With
  3 parallel `invoke()` calls on the same singleton instance:
  - Call A writes `self._process = procA`.
  - Call B writes `self._process = procB` (overwrites A).
  - Call C writes `self._process = procC`.
  When a `cancel()` or a finally block then accesses `self._process`,
  it kills the wrong subprocess or leaves an orphaned
  process behind. In the failure case the exception propagates uncaught
  through all three `invoke()` generators before they even yield a single
  result — hence the <10ms.
- **Fix** (`jarvis/harness/base.py:39-49 + 83-219`):
  1. `__init__` initializes `self._active_processes: set[Process]`.
  2. `invoke()` uses a **local** `proc` variable (no `self.` write).
  3. `proc` is registered via `_active_processes.add(proc)`,
     and `discard(proc)` in the finally block.
  4. `cancel()` kills everything in `_active_processes`.
  5. `NotImplementedError` is caught (defense-in-depth against
     Windows SelectorEventLoop setups that do not support subprocess).
- **Regression guard**:
  `tests/unit/test_subprocess_harness_concurrency.py` (3 tests, all green):
  - `test_concurrent_invoke_calls_dont_race`: 3 parallel `invoke()`,
    each delivers its own chunks without mixing, `concurrent_peak == 3`.
  - `test_subprocess_harness_init_has_active_processes_set`.
  - `test_cancel_killed_all_active_processes`.

## Bug #3: Empty-response cascade after `suppress_response` tools (CRITICAL)

- **File**: `jarvis/brain/manager.py:1052-1063` (pre-fix)
- **Symptom**: After a successful `spawn_sub_jarvis` call the
  BrainManager cycles through all providers in the fallback chain (Grok → Gemini →
  Claude-Haiku → Opus → GPT-4o). Each one re-invokes `spawn_sub_jarvis`,
  and the second/third calls fail with `recursion denied (depth=1)`. ~40k tokens
  per cascade, about $0.13 per voice spawn.
- **Root cause**: `tool_use_loop.py:415` sets, after a tool call with
  `suppress_response=True`: `final_agg.text = suppress_output` (typically
  an empty string) and `finish_reason = "suppress_response"`. The
  empty-response guard in the BrainManager only checked `agg.text`:
  ```python
  if not (agg.text or "").strip():  # ← True with suppress_response
      provider_errors.append("empty_response")
      continue  # → fallback to the next provider
  ```
- **Fix** (`jarvis/brain/manager.py:1059-1084`): Guard extended to also
  check the `tool_calls` and `finish_reason="suppress_response"` fields:
  ```python
  response_empty = not (agg.text or "").strip()
  tool_calls_executed = bool(agg.tool_calls)
  suppressed = (agg.finish_reason == "suppress_response")
  if response_empty and not tool_calls_executed and not suppressed:
      # ... only NOW fallback
  ```
  Plus: `if not response_text` replaced by `if used_provider is None`
  (line 1118), so that suppress_response with empty text is not
  interpreted as "all providers failed".
- **Regression guard**:
  `tests/integration/test_suppress_response_no_fallback.py` (2 tests):
  - `test_suppress_response_does_not_trigger_fallback`: Brain calls
    spawn_sub_jarvis (suppress=True), assert spawn_tool.calls == 1 and
    fallback.calls == 0.
  - `test_truly_empty_response_still_triggers_fallback`: empty brain
    (no text, no tool calls) correctly falls through.

## Bug #4: `dispatch_to_harness` 16-second TypeError (HIGH)

- **File**: `jarvis/harness/computer_use_loop.py:329-351` (pre-fix)
- **Symptom**: Production log shows
  `ActionExecuted: tool_name=dispatch_to_harness, duration_ms=15929,
  error="TypeError: ToolExecutor.execute() got an unexpected keyword
  argument 'tool_name'"`.
- **Root cause**: The Computer-Use loop's action dispatch had two
  code paths:
  ```python
  tool = (ctx.tools or {}).get(tool_name)
  if tool is not None:
      result = await ctx.tool_executor.execute(tool, tool_args, ...)
  else:
      result = await ctx.tool_executor.execute(tool_name=..., args=..., ...)
  except TypeError:
      result = await ctx.tool_executor.execute(tool_name=..., ...)  # ← same args!
  ```
  When the tool is not in `ctx.tools`, the else branch falls into the
  invalid-kwarg path. The `except TypeError` retries with the SAME
  wrong args → guaranteed TypeError again. 16s latency because the
  brain plan took 16s beforehand.
- **Fix** (`jarvis/harness/computer_use_loop.py:329-393`):
  1. Early exit when the tool is not in the set AND the ToolExecutor has no
     test-double signature: actionable error message instead of an invalid call.
  2. Test-double detection via `inspect.signature` (`_looks_like_kwarg_executor`).
  3. `except Exception` for defensive crash reporting (no retry).
- **Regression guard**: the existing
  `tests/unit/harness/test_computer_use_loop.py` (18 tests) covers both the
  production ToolExecutor path and the test-double path.

## Bug #5: Frontier model IDs hallucinated (MEDIUM, latent)

- **File**: `jarvis/brain/manager.py:130-152` (TIER_DEFAULTS_BY_PROVIDER)
- **Symptom**: Brain calls with models like `gemini-3-flash`, `gpt-5.5`,
  `grok-4.20`, `claude-opus-4-7-20251022` produce 404 errors at the
  provider APIs. Status: not yet verified whether all IDs are valid.
- **Root cause**: The `claude-opus-4-7-20251022` snapshot no longer
  exists (fixed 2026-04-28: now the `claude-opus-4-7` stable alias).
  The other Frontier-2026-Q2 IDs are marked verifiable in `frontier_resolver.py`,
  but there is no automatic health check before use.
- **Fix status**: Partial. The claude-opus-4-7 stable alias is already set.
  Pending: `frontier_autoswitch` must run actively and populate the cache
  before production use.
- **Regression guard**: still outstanding — TODO: `frontier_resolver` tests
  must validate all TIER_DEFAULTS IDs against a probe list.

## Bug #6: pyautogui dependency missing (MEDIUM, dependent)

- **File**: none specific — `jarvis/plugins/tool/type_text.py` or similar
- **Symptom**: `type_text` returns with
  `error="pyautogui not available: No module named 'pyautogui'. Native Windows input failed: [WinError 0] Incorrect parameter."`
- **Root cause**: `pyautogui` is an optional dependency, not installed,
  and the native Win32 fallback has a separate bug.
- **Fix**: `pip install pyautogui` or add it to `requirements.txt`.
  The native fallback is a separate issue (see issue tracker).
- **Status**: not fixed in this audit; planned for a separate phase.

## Bug #7: STT hallucinations → phantom voice sessions (MEDIUM)

- **File**: `jarvis/speech/pipeline.py` (STT path)
- **Symptom**: 50% of voice sessions on 2026-04-29 had single-word
  hallucinations ("Ding." — "Thing.", "Began.", "Fliegen." — "Flying.", "Let's get up.",
  "Hier geht's dir." — "Here it's going well for you."). Confidence < 0.65. Brain responds politely
  ("Was gibt's, Alex?" — "What's up, Alex?") — burns ~7k tokens per phantom session.
- **Root cause**: faster-whisper configuration too permissive, wake-word
  threshold matches on background noise.
- **Fix status**: fixed on 2026-04-29 in the voice hot path. For details see
  Bug #8, because the concrete repro case consisted of a hangup mistranscription
  plus STT-prompt self-suggestion.

## Bug #8: "Auflegen" (hang up) was sent to the brain as `Let's get up` (CRITICAL)

- **File**:
  - `jarvis/speech/pipeline.py` (`HANGUP_PATTERNS`, hangup-before-hallucination filter)
  - `jarvis/plugins/stt/fwhisper.py` (`initial_prompt=None`)
  - `jarvis/speech/rolling_whisper_wake.py` (`min_rms=0.003`)
- **Symptom**: User says "Auflegen". Jarvis does not hang up immediately, but
  waits a long time, shows the final transcript only late, and replies with
  random content such as: "I'd recommend saving the current version to GitHub
  first, Alex."
- **Repro from production log** (`data/jarvis_desktop.log`, 2026-04-29):
  1. Wake is correctly detected: `WAKE detected via whisper:Hey JARVIS`.
  2. User says "Auflegen"; but the final STT text becomes:
     `transcript final: text="Let's get up." language=de confidence=0.625`.
  3. `Let's get up.` does not match the old hangup regex.
  4. Text goes to the brain (`-> Brain ...`).
  5. Provider fallback / tool context produces a nonsensical reply about GitHub.
  6. Only a later, correct `transcript final: text='Auflegen.'`
     ends the session.
- **Root causes**:
  1. `HANGUP_RE` did not know the real Whisper confusion `"Let's get up."`.
  2. The STT hallucination filter ran before the hangup path. As a result,
     short closing mistranscriptions like `"Vielen Dank."` ("Thank you.") could be
     discarded as a hallucination instead of ending the session.
  3. `FasterWhisperProvider.initial_prompt` contained fixed example sentences like
     `"JARVIS, open the browser"` and `"Thank you, JARVIS"`. With very quiet
     audio, exactly these examples showed up as fabricated transcripts in the
     rolling-wake log.
  4. `RollingWhisperWake.min_rms=0.001` was too permissive. Windows with
     `rms=0.001-0.002` produced phantom texts like `"JARVIS."`,
     `"Vielen Dank."` ("Thank you."), `"Okay."`, `"JARVIS, open the browser."`.
- **Fix**:
  1. `HANGUP_PATTERNS` extended with real mistranscriptions:
     `vielen dank`, `auf leg*`, `draufleg*`, `ableg*`,
     `let's get up`, `let us get up`, `just get up`.
  2. The hangup regex is now evaluated before the STT hallucination filter.
     Closing commands must never fall through to the brain.
  3. `FasterWhisperProvider.initial_prompt` is `None` in the hot path. No more
     fixed example sentences that Whisper parrots back on quiet audio.
  4. `RollingWhisperWake.min_rms` raised from `0.001` to `0.003`. This
     blocks the very quiet phantom windows visible in the logs, without blocking
     normally spoken "Hey Jarvis" (typical wake RMS in the repro:
     `0.0118`).
  5. Old `scripts/voice_e2e_probe.py` diagnostic processes terminated, so no
     stale long-runners disturb live operation.
- **Regression guard**:
  - `tests/unit/speech/test_turn_taking.py`
    - `test_hangup_runs_before_hallucination_filter_for_vielen_dank`
    - `test_hangup_accepts_split_auf_leg_transcript`
    - `test_hangup_accepts_lets_get_up_mistranscript`
  - `tests/unit/speech/test_wake_hallucination_guard.py`
    - `test_final_stt_has_no_example_prompt_that_can_be_hallucinated`
    - `test_rolling_wake_ignores_very_low_rms_hallucination_windows`
- **Verification**:
  ```bash
  python -m py_compile jarvis/plugins/stt/fwhisper.py \
      jarvis/speech/rolling_whisper_wake.py \
      jarvis/speech/pipeline.py

  pytest tests/unit/speech/test_turn_taking.py \
      tests/unit/speech/test_wake_hallucination_guard.py \
      tests/unit/speech/test_pipeline_vision_privacy.py -q
  ```
  Expected: `24 passed`.
- **Production restart after fix**: required. STT/wake instances are
  built at startup; a running Jarvis process otherwise keeps the old
  initial prompt and the old wake parameters in memory.

---

## How we prevent these bugs

1. **Tests are the spec**. For every bug fix: a test that reproduces the bug
   BEFORE the fix is touched. Test green ⇨ fix lands ⇨ test stays in.
   Proof: all 4 critical/high bugs have regression tests.

2. **Singleton state in the plugin path is forbidden**. SubprocessHarness was
   an example. Concurrent-use plugins (Tool, Brain, Harness) use
   exclusively invocation-local variables or explicitly thread-safe
   state (`set`, `asyncio.Queue`).

3. **Fallback logic checks all termination reasons**. Empty response is
   not the same as failure — tool calls and `suppress_response` are valid
   terminations.

4. **Heuristics need their match list to be complete**. If a verb
   is in the user's vocabulary (`spawn`, `starte`, `start`), it belongs in the
   match list, not on the brain system-prompt wishlist.

5. **Code paths that "catch a TypeError and retry the same call"
   are lies**. If the signature is wrong, the retry is wrong too.
   Signature inspection is the honest solution.

## Production restart instruction

These fixes are on disk. For them to take effect, the production
Jarvis instance must be restarted:
1. Tray icon → "Beenden" (Exit), or
2. `taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq Jarvis*"`, then
3. `run.bat` (no argument) to boot back up.

## Test-run command

```bash
pytest tests/unit/test_subprocess_harness_concurrency.py \
       tests/integration/test_suppress_response_no_fallback.py \
       tests/integration/test_multi_spawn.py \
       tests/unit/harness/test_computer_use_loop.py \
       tests/unit/brain/test_routing.py \
       tests/unit/test_brain_manager_tier_config.py \
       tests/contract/test_brain_protocol.py \
       tests/contract/test_sub_jarvis_protocol.py
```

Expected: 100+ tests green. (Pre-existing failures in
`tests/integration/test_fallback_chain.py` are older, not part of this
audit.)

---

## Bug API-1: API key / account errors misclassified (CRITICAL, 2026-04-29)

### Symptom (what the user saw in the transcription)

A voice turn produces an **empty** `voice_turns` row (`provider=''`,
`model=''`, `jarvis_text=''`) or a standardized error message
"Provider grok, gemini, claude-api unerreichbar. Netzwerk pruefen." (Provider grok, gemini, claude-api unreachable. Check network.) —
even though the API keys are validly configured and the network is OK.

In the backend log (04-29 17:31, 19:47), three cascading defects
at the same time:

```
Brain claude-api(claude-haiku-4-5-20251001) failed: 400
  'Your credit balance is too low to access the Anthropic API. Please
   go to Plans & Billing.'
Brain grok(grok-4.1-fast) failed: 404
  'The model grok-4.1-fast does not exist or your team does not have
   access to it.'
Brain gemini(gemini-3-flash) fehlgeschlagen: 11 validation errors for  <!-- i18n-allow -->
  GenerateContentConfig
  tools.0.Tool.functionDeclarations.1.parameters.strict
    Extra inputs are not permitted [type=extra_forbidden, ...]
```

Three different root causes, same end effect: **complete
provider-chain failure with no actionable user message**.

---

### Root cause 1: Gemini plugin sends OpenAI-specific schema fields

- **File**: `jarvis/plugins/brain/gemini.py:67-78`
- **Detail**: `_tools_gemini_format(tools)` packs `t.input_schema`
  unchanged into `Tool.functionDeclarations[].parameters`. But Phase-7.3
  self-mod tools set OpenAI tool-use fields (`strict: true`,
  `input_examples: [...]`, `additionalProperties: false`) at the
  schema root — the google-genai SDK validates this via Pydantic with
  `extra="forbid"` and throws 11 validation errors.
- **Fix** (`gemini.py:65-128`):
  1. New constant `_GEMINI_FORBIDDEN_SCHEMA_KEYS` with OpenAI-only
     fields (`strict`, `input_examples`, `additionalProperties`,
     `$schema`, `$id`).
  2. New function `_sanitize_for_gemini(schema)` strips the fields
     out **recursively** (also in nested `properties` and `items`).
  3. `_tools_gemini_format` applies the sanitizer before it sends to
     `Tool.functionDeclarations`.
- **Test**: `tests/unit/test_api_key_error_handling.py::TestGeminiSchemaSanitize`

### Root cause 2: Account block (credit/quota/tier) classified as invalid_model

- **File**: `jarvis/brain/manager.py:1559-1591` (`_classify_provider_error`)
- **Detail**: The xAI 404 error ("model does not exist or your team
  does not have access") matched `_is_invalid_model_exc` (because of "model
  does not exist"). Consequence: `_format_provider_chain_error` writes
  "Ungueltige Model-ID … jarvis.toml und TIER_DEFAULTS pruefen." —
  completely misleading. The TOML is correct, the **account** has no
  access.

  Similarly Anthropic: "credit balance too low" lands as a generic
  `init_fail`, because no heuristic matches it. The provider is **not**
  added to `_dead_providers` and is retried on the next turn →
  every voice turn is rejected for $0.

- **Fix** (`manager.py:1540-1591`):
  1. New heuristic `_is_account_blocked_exc(msg)` — matches "credit
     balance too low", "your team does not have access", "not available
     on your tier", "subscription required", "quota exceeded for", etc.
  2. `_classify_provider_error` checks `account_blocked` **before**
     `invalid_model` (order matters — otherwise invalid_model wins
     on xAI 404 strings).
  3. `_is_invalid_model_exc` returns `False` when the error is already
     recognized as `account_blocked` (prevents a double match).
  4. In the generate loop (`manager.py:1048-1059`): `kind == "account_blocked"`
     adds the provider to `_dead_providers` analogously to `missing_key`.
- **Test**: `tests/unit/test_api_key_error_handling.py::TestAccountBlocked`

### Root cause 3: User message on account block is wrong

- **File**: `jarvis/brain/manager.py:1604-1700` (`_format_provider_chain_error`)
- **Detail**: Previously there were only the classes `missing_key`,
  `invalid_model`, `rate_limit`, `empty_response`, `other_fails`. On an
  account block (now its own class) the default message would be
  "Provider X unerreichbar. Netzwerk pruefen." — the user would debug
  the network instead of topping up credit.
- **Fix** (`manager.py:1620-1672`):
  1. New list bucket `account_blocked` analogous to `missing_keys`.
  2. User-actionable message: "Account problem with {providers}: top up credit, upgrade plan, or unlock model tier. For Anthropic: console.anthropic.com/settings/billing. For xAI: console.x.ai/team/billing."
- **Test**: `tests/unit/test_api_key_error_handling.py::TestChainErrorFormat`

---

### Verification

```bash
pytest tests/unit/test_api_key_error_handling.py -v
# 14 passed
```

**Live verify** (after backend restart):
1. Make a voice turn — on Anthropic-credit-too-low the provider goes
   straight to `_dead_providers`, follow-up turns do not retry it.
2. `voice_turns` DB query → no more empty `provider=''` rows;
   instead a clean provider+model from the successful fallback
   (Bug-C fix from earlier).
3. On a complete chain failure → the user message contains "Account-Problem" (account problem)
   + a URL hint to the billing dashboard, not "Netzwerk pruefen" (check network).

### Regression guard

- `tests/unit/test_api_key_error_handling.py` (14 tests) covers:
  - Gemini sanitizer (recursive, in arrays, with self-mod tools)
  - `_is_account_blocked_exc` for Anthropic/xAI/OpenAI
  - Differentiation against `_is_missing_key_exc` and `_is_invalid_model_exc`
  - User-message format with billing-URL hints
- `tests/unit/brain/test_frontier_resolver.py` (23) and all existing
  brain tests stay green (235 tests in total).

### Status

✅ **Fixed + LIVE-VERIFIED** in `phase-8-review-pipeline` (2026-04-29).

**Live E2E smoke output (2026-04-29 ~21:00):**
```
$ python scripts/smoke_brain_e2e.py

--- Step 1: Verfuegbare API-Keys ---
  anthropic_api_key  -> claude-api   OK
  gemini_api_key     -> gemini       OK
  openai_api_key     -> openai       MISSING
  grok_api_key       -> grok         OK

--- Step 2: BrainManager via from_tier_config ---
  _dead_providers (Pre-Boot-Filter): ['openai', 'openrouter']
  fallback chain (fast): claude-api → claude-api → gemini → grok

--- Step 3: Live-Brain-Call ---
  Question: 'Answer with exactly one word: yes or no.'
  Response: 'Yes.'

--- Step 4: Verdict ---
  [OK] Brain-Call delivered real response
```

**What now happens** when the user has no valid keys:
- The pre-boot filter removes openai+openrouter from the chain
- claude-api crashes with `credit_balance_too_low` → `account_blocked` → dead-list
- grok crashes with `team_does_not_have_access` → `account_blocked` → dead-list
- gemini delivers a valid response (with the Frontier model `gemini-3-flash-preview`)

**What happens when ALL providers fail** (e.g. when the Gemini quota is also empty):
- User message: "Account-Problem bei {providers}: Credit aufladen, Plan upgraden..." (Account problem with {providers}: Top up credit, upgrade plan...) with billing URL hints for Anthropic and xAI.
- NO MORE "Provider unerreichbar. Netzwerk pruefen." (Provider unreachable. Check network.)

**Bonus fix**: the `gemini-3-flash` stable alias is not listed by the Google API (404
NOT_FOUND), `gemini-3-flash-preview` is used instead — synchronized in
`TIER_DEFAULTS_BY_PROVIDER["router"]["gemini"]` and `jarvis.toml`
`[brain.router] fallback_model` and `routing_model`.

---

## Bug UI-1: Sidebar tab `Transkription` falls back to the chat view (HIGH, 2026-04-29)

### Symptom

The sidebar entry **Transkription** is visible and gets marked active,
but the **chat** view appears in the main area:

- Header: `Chat`
- Empty state: `Ready for commands`
- ChatInput at the bottom of the window

The actual transcription view (`SessionsView`) already exists and
works, but is not reliably reached on navigation.

### Root cause

The frontend navigation had several separate sources for
valid section IDs:

- `store/events.ts` knew `SectionId = "sessions"`.
- `Sidebar.tsx` used `{ id: "sessions", label: "Transkription" }`.
- `MainView.tsx` mapped `case "sessions": return <SessionsView />`.
- But `useWebSocket.ts` had an old hardcoded allowlist:

```ts
["chats", "agents", "skills", "mcps", "languages", "apikeys", "settings", "debug"]
```

So `NavigateSidebar(section="sessions")` was discarded as invalid.
Depending on the navigation path, the app stayed on the old/default `chats` state
and therefore showed chat content under the transcription tab.

### Fix

- `jarvis/ui/web/frontend/src/store/events.ts`
  - introduced a central `SECTION_IDS`
  - `isSectionId(value)` as the only runtime validation for sections
  - `SECTION_LABELS` including `sessions: "Transkription"`
- `jarvis/ui/web/frontend/src/hooks/useWebSocket.ts`
  - removed the old local `isValidSection()` allowlist
  - `NavigateSidebar` now uses `isSectionId()`

### Regression guard

Rule: **New sidebar sections must never be entered only locally in Sidebar/MainView.**
Every new section must go through `SECTION_IDS` in
`store/events.ts`; all validations import `isSectionId()`.

Recommended test for the next UI-test pass:

```ts
it("accepts NavigateSidebar to sessions", () => {
  expect(isSectionId("sessions")).toBe(true);
});
```

Additional rule: When a tab in the sidebar is active, the header in the main area must
match the tab semantically. For `Transkription` that is `title="Transkription"`,
not `title="Chat"`.

### Verification

```bash
cd jarvis/ui/web/frontend
npm.cmd run build
```

Expected: TypeScript + Vite build succeeds. After a production build, the
desktop app must be restarted so the WebView does not cache the old bundle.

---

## Bug-Restore-2026-05-01: Restore shows the old state even though git is restored (HIGH)

- **Date:** 2026-05-01 · **Scope:** recovery workflow, desktop app
- **Symptom**: After `git reset --hard` to yesterday's state, the user still
  sees code from several weeks ago (in the IDE, in the editor, in the running
  desktop app). Confusion: "the restore didn't work".
- **Root cause** (three layers, each separate):
  1. **Second worktree stale** — `git worktree list` showed
     `<USER_HOME>/Desktop/jarvis-a0` on `awareness/phase-a5` (4 days
     old). When the user opened this folder instead of `Personal Jarvis/`, they saw
     the old state. Worktrees are **not** updated alongside a `git switch`/`reset`
     in the main folder.
  2. **Frontend build stale** — `jarvis/ui/web/dist/index.html` was from
     `2026-05-01 11:16` (before the restore at 13:00). The pywebview desktop app loads
     **only the build output** from `dist/`, never the source from `src/`. Source
     restored ≠ desktop app shows the new state.
  3. **Running desktop app in RAM** — `pythonw.exe` PID 61800 had run since before
     the restore. The Python modules + React app in the pywebview window are
     frozen in memory, regardless of what happens in the filesystem.
- **Fix** (all three layers):
  1. `git -C jarvis-a0 switch recovery/full-project-2026-05-01` (or another
     up-to-date branch — the worktree must not be on the same branch as the main worktree).
  2. `cd jarvis/ui/web/frontend && npm run build` (~11s, regenerates `dist/`).
  3. `taskkill /PID <pythonw> /F` + `start "" pythonw -m jarvis.ui.web.launcher`.
- **Lesson for future restores** (order):
  1. **Set backup tags** before every destructive git operation
     (`backup/pre-<topic>-<TS>`). Tags survive `reset --hard` and force-push.
  2. **Git restore** (reset/merge/cherry-pick as usual).
  3. **Synchronize all worktrees** (`git worktree list` → switch in each).
  4. **Rebuild the frontend** (`npm run build` in `jarvis/ui/web/frontend`).
  5. **Terminate + restart running app instances** (`taskkill` +
     `pythonw -m jarvis.ui.web.launcher`).
  6. **Verification**: `dist/index.html` timestamp current + `tasklist` shows
     a new PID + FastAPI `http://localhost:47821/api/health` → HTTP 200.
- **Regression guard**: no automated tests possible (workflow bug,
  not a code bug). Instead: this entry is part of the restore runbook.
  The note already present at the end of BUGS.md ("After a production build,
  the desktop app must be restarted so the WebView does not cache the old
  bundle.") is the shorter variant of the same lesson.
- **Audit trail**:
  - `recovery-report.md` (diagnosis, top-3 candidates)
  - `restore-report.md` (phases 0–4 + desktop-app restart)
  - Plan: `<USER_HOME>/.claude/plans/shimmering-zooming-pixel.md`

---

## Bug Voice-Turn-2026-05-02: Jarvis keeps listening after every reply (CRITICAL — REVERTED 2026-05-05)

> **Update 2026-05-05 (user's wish):** The default is flipped back again. Jarvis
> no longer hangs up by itself after a reply. Reason: the user
> reported in the transcription that Jarvis hung up directly after every single
> question (`hangup_reason: "turn_complete"` after 1 turn), which broke the natural
> multi-question interaction. The wake-boundary argument from
> 2026-05-02 is compensated by the existing phantom-turn protection layers:
> `post_tts_listen_suppression_s=0.8` (mic lock after TTS),
> `WAKE_ONLY_RE`/`_is_wake_only` (wake-only turns do not go to the brain),
> `_STT_HALLUCINATION_RE` (YouTube outros blocked), `HANGUP_RE` (pre-brain),
> `idle_timeout_s=30s`, and hotkey hangup. Concrete changes:
>
> - `SpeechPipeline(..., continue_listening_after_response=True)` is now
>   the default. Anyone who wants the old 1-turn-per-wake semantics sets the flag
>   explicitly to `False` (opt-out, regression-guarded by
>   `test_legacy_one_turn_per_wake_mode_still_ends_session`).
> - New default regression test:
>   `test_normal_response_keeps_session_listening_by_default`.
>
> The reasoning below from 2026-05-02 is kept as history
> — the "do not reintroduce" rule is thereby overridden by the
> new user mandate. Please solve future phantom-turn incidents via the
> protection layers above, not via auto-hangup.

- **Date:** 2026-05-02 · **Scope:** speech pipeline, turn-taking,
  Wake/VAD/STT/TTS
- **Symptom:** After a normal Jarvis reply, the orb/mic state stays at
  `LISTENING`. Jarvis keeps listening and then processes room noise,
  reverb, or normal conversation as a new user utterance. The user experiences this
  as "Jarvis is always listening" or "after the sentence it just keeps replying".

### Root cause

The first suspicion, "TTS echo", was only part of the problem. The actual cause
was in `jarvis/speech/pipeline.py`:

- `_active_session()` is a multi-turn loop.
- `_handle_utterance()` explicitly set `TurnTakingState.LISTENING` again
  after every normal reply.
- As a result, the same voice session stayed open until `idle_timeout_s`
  (default: 30s). The wake word was just the entry into a long open
  session, not the boundary for exactly one turn.

The old flow was:

```text
Wake -> LISTENING -> STT -> Brain -> TTS -> LISTENING -> VAD keeps waiting
```

That is tempting for hands-free dialogues, but too dangerous in Jarvis
production mode: it leads to phantom turns and violates the expected wake boundary.

### Fix

Normal replies end the active voice session:

```text
Wake -> LISTENING -> STT -> Brain -> TTS -> IDLE
```

Concrete changes:

- `SpeechPipeline(..., continue_listening_after_response=False)` as the default.
- New `_finish_after_response(barged=False)` decision:
  - `barged=True` -> keep `LISTENING`, because the user actively spoke over it.
  - `continue_listening_after_response=True` -> explicit continuous mode.
  - normal response -> `_session_end_reason = "turn_complete"` and `IDLE`.
- `_active_session()` returns `turn_complete` instead of
  `voice_pattern` on a normal end.
- Streaming TTS now passes the barge-in status through to the turn decision.
- An additional short TTS echo lock (`post_tts_listen_suppression_s=0.8`)
  discards mic frames during/shortly after Jarvis's own speech output.

### Regression guard

Tests in `tests/unit/speech/test_turn_taking.py`:

- `test_normal_response_ends_session_instead_of_listening_forever`
- `test_barge_in_keeps_session_listening`
- `test_continuous_response_mode_can_keep_listening`

Additional echo-guard tests:

- `test_session_input_stream_drops_tts_echo_chunks`
- `test_tts_echo_suppression_drops_only_until_deadline`

### Rule for future changes

**Do not reintroduce:** After a normal TTS reply there must be no
unconditional `await _set_turn_state(TurnTakingState.LISTENING)`.
Anyone who wants continuous conversation must enable it explicitly via
`continue_listening_after_response=True` and test it separately.

On every turn-taking refactor, check:

```bash
pytest tests/unit/speech/test_turn_taking.py -q
pytest tests/unit/speech -q
```

### Verification

In the `main` worktree:

```bash
python -m py_compile jarvis/speech/pipeline.py tests/unit/speech/test_turn_taking.py
pytest tests/unit/speech -q
ruff check --select I,F jarvis/speech/pipeline.py tests/unit/speech/test_turn_taking.py
```

Expected: speech unit suite green; a normal response ends with
`VoiceSessionEnded.hangup_reason == "turn_complete"`.
---

## Bug Voice-Turn-2026-05-31: "keeps listening, never answers" — `grace-on-COMPLETE` recurrence (FIXED)

- **Date:** 2026-05-31 · **Scope:** `jarvis/speech/pipeline.py`
  (`_complete_or_buffer_context`), completion buffer
- **Symptom (user):** "After I finish speaking, Jarvis sometimes keeps the mic
  open instead of processing what I said." A *complete* command is held back;
  occasionally it is never answered at all. Same felt symptom as the
  2026-05-02 bug above, but a *different* code path.
- **Root cause:** The `grace-on-COMPLETE` feature (commit `f0afb672`,
  2026-05-26) made `_complete_or_buffer_context` park **every** COMPLETE
  utterance in the completion buffer (`return None` →
  `WAITING_FOR_COMPLETION`, mic stays open) and dispatch only after a
  `complete_grace_ms` (1500 ms) timer. This directly violated
  `completion.py`'s own contract — *"a complete prompt must NEVER be held
  back"* — and the regression guard
  `tests/unit/speech/test_pipeline_completion.py::test_complete_text_returns_unchanged`
  ("a complete utterance MUST go straight to the brain — no buffering, no
  waiting"). The feature **shipped over a red guard** (5/11 completion tests
  were failing on `wip/cross-platform-cloud-first-20260529`). Worse: during
  the open-mic grace window, room noise / TTS-tail extended the buffer into an
  INCOMPLETE tail, flipping it onto the 15 s `completion_wait_ms` timer whose
  `_completion_timeout_fire` then **silently discards** — so the user's
  finished command was never processed at all (the intermittent "never
  answers" case).
- **Fix:** Restore immediate dispatch — a fresh COMPLETE utterance and a
  continuation that *becomes* complete both return their text straight away
  (no grace-hold). Only genuinely dangling fragments (verdict ≠ `None` from
  `is_incomplete`) still buffer and wait for the continuation. `_buffer_is_complete`
  is no longer set `True` anywhere; the silent-discard INCOMPLETE-timeout
  policy (user mandate 2026-05-26) is unchanged.
- **Regression guard:** `test_pipeline_completion.py` (11 tests) back to green;
  `test_complete_text_returns_unchanged` is the canary. The two stale
  `test_timeout_*_speaks_fallback` tests were realigned to assert the
  documented silent-discard policy.
- **Rule:** A completed prompt is dispatched, never parked. Sentence-merging
  across a pause is a property of the **INCOMPLETE** path only. Do not
  re-introduce a grace-hold on COMPLETE utterances.
---

## BUG-007: Tasks view permanently on HTTP 503 (Phase-5 wiring missing)

- **Date:** 2026-05-02 · **Scope:** Phase-5 task-queue integration in DesktopApp
- **Symptom**: User clicks "Tasks" in the sidebar. The page shows a
  red banner: *"Could not load tasks: HTTP 503"*. Regardless of the
  state filter (All/Active/Done/Problems), regardless of refresh — it stays 503,
  because the polling gets the same answer every 3s. The UI is otherwise
  functional (chats, skills, missions, etc. work normally).
- **Root cause** (wiring gap, not a code bug):
  1. **Backend fully built**: `jarvis/tasks/{schema,store,scheduler,
     runner}.py` + 25 unit tests green, ADR-0003/0005 documented.
  2. **REST layer correct**: `jarvis/ui/web/tasks_routes.py:28` expects
     `app.state.task_store`/`app.state.task_scheduler`. When not set,
     `_require_store` deliberately throws HTTP 503 (`detail="TaskStore not available"`) — defensive, this is not a crash.
  3. **DesktopApp boot never sets it**: `WebServer.start()` called
     `_init_mission_stack()`, `_init_session_stack()`, `_init_channel_stack()`
     — but **no** `_init_task_stack()`. → `app.state.task_store` stayed
     `None` for the entire process lifetime.
  4. Comparison: Phase-6 Missions has `bootstrap_missions()` (`jarvis/missions/
     init.py`); Phase-5 Tasks had no counterpart.
- **Fix** (`jarvis/ui/web/server.py`):
  - Tracking fields in `__init__`: `_task_store`, `_task_scheduler`,
    `_task_runner`, `_task_scheduler_task`, `_task_cancel_token` (lines 92-97).
  - New async method `_init_task_stack()` (analogous to `_init_mission_stack`):
    opens `TaskStore` on `cfg.memory.data_dir/jarvis.db` (additive
    schema, ADR-0003), calls `cleanup_interrupted()` (crash recovery), builds
    `TaskRunner` + `TaskScheduler`, hydrates from the DB, starts the scheduler loop
    as an `asyncio.Task` with a `CancelToken` (line ~1019).
  - Called in `start()` directly after the session-stack init (line ~899).
  - Shutdown path in `stop()`: CancelToken cancels → cancel the
    scheduler-loop task → `scheduler.shutdown()` (waits 2s for runner tasks) →
    `store.close()` → `app.state.task_store = None` (line ~1233).
- **Verification** (2026-05-02 23:15 — performed directly):
  - `pytest tests/unit/tasks/` → 25/25 green.
  - `Invoke-WebRequest http://127.0.0.1:47821/api/tasks` (old instance
    before fix): `STATUS=503 detail="TaskStoreNicht verfuegbar"` (TaskStore not available).  <!-- i18n-allow -->
  - App restart with patched code → the same endpoint:
    `STATUS=200 BODY={tasks:[],total:0}`.
  - Demo task via `POST /api/tasks {trigger:after_delay 30s, action:tool_call
    noop}` → appears immediately in the UI with the correct trigger icon (clock),
    "Delay" label, ID short form, and live countdown.
  - After 30s: the card switches to state `failed` with the expected step
    *"RuntimeError: ToolExecutor or Tool-Registry not configured"* —
    confirms that the runner logs cleanly through the failure path (no
    crash, no stuck state).
- **Regression guard**:
  - **Smoke test** (mandatory on the next Phase-5/6 refactor): after
    app start `GET /api/tasks` must return `200`, not `503`.
  - **Pattern lesson**: When a new FastAPI router expects `app.state.<x>`,
    `WebServer.start()` **must** have a `_init_<x>_stack()`
    AND a corresponding cleanup section in `stop()`. Otherwise the
    user permanently sees 503 without knowing that it is actually just a
    never-plugged-in feature.
  - **Open items** (not a bug, clearly documented):
    - `TaskRunner` initially runs **without** harness/TTS/tool wiring. Consequence:
      `speak`/`tool_call` actions terminate with `failed` + a clear
      error message. `after_delay`/`at_time` triggers work; the
      tasks view is fully functional. Voice/tool wiring comes in the
      Phase-5 brain-tool step (`schedule_task` as a Jarvis-Agent tool).
    - **Brain tool to create one is missing** — the voice command "Erinner mich in 2h" ("Remind me in 2 hours")
      does not work yet; tasks can currently only be created via REST
      (or a future form UI).

---

## Bug #BG-VAD-2026-05-05: VAD never endpoints when speakers play music or another voice

- **Date**: 2026-05-05
- **Severity**: CRITICAL — voice flow blocked whenever speakers / room
  audio / partner-talking-in-the-room is active. Symptom from the user:
  "Jarvis listens forever, doesn't think, doesn't reply." Live transcript
  panel proved that Whisper itself only captured the user's words — so
  the bug had to live in the endpointing layer, not in STT.
- **Files**:
  - `jarvis/audio/vad.py` (`SileroEndpointer`)
  - `jarvis/speech/pipeline.py` (`SpeechPipeline._on_vad_probe`,
    `_stt_probe_async`, VAD construction)
  - `tests/unit/audio/test_vad_turn_taking.py`

### Symptom timeline

1. User says a sentence with a podcast / music / another person talking
   in the background through the speakers.
2. Whisper's final transcript is correct (only the user's words).
3. The pipeline never reaches the final transcript stage — the VAD
   silence endpoint never fires.
4. The user hears nothing. Jarvis only finally responds when the
   Silero `max_utterance_s` hard cap kicks in (originally 30 s) — i.e.
   half a minute of dead air.

### Root cause (deep)

`SileroEndpointer` is a binary speech / non-speech endpointer. It
counts consecutive silent frames after speech started; once
`silent_run >= silence_frames` the utterance is yielded. Two problems
combine when speakers bleed into the mic:

1. **Silero classifies music with vocals as "speech"** — its training
   target is "human voice present", not "the *primary* user is
   talking". The `silent_run` counter therefore never increments while
   any speaker audio is present, so the silence endpoint never fires.
2. **The `relative_silence_rms_ratio = 0.22` energy guard only catches
   the case where background audio is much quieter than the user's
   peak.** When music plays through near-field speakers the bleed is
   often within 30–60 % of the user's peak RMS — well above the 22 %
   cutoff — so the energy-drop path also fails.

### Failed first attempt (same day)

The first commit added an STT-stability probe: while Silero is
recording, run Whisper every 1.5 s on the **entire active buffer**, and
require **two consecutive identical transcripts** before forcing the
endpoint. This appeared to work in the synthetic test (which used a
stub VAD) but failed on real audio for two reasons:

1. **Growing buffer = growing hallucinations.** Each probe fed a
   longer slice of audio (user-speech + ever-more music) into Whisper.
   Whisper hallucinates lyrics from music, and those hallucinations
   shift slightly with every additional second of context. Result:
   probe transcripts were never identical → "stability" condition
   never satisfied → endpoint never forced.
2. **`probe_min_active_ms = 2500` was too long.** The probe didn't
   even start running until 2.5 s of speech had elapsed, then needed
   another 3 s of "stability" — practical reaction time was 5–6 s in
   the best case, far worse with hallucinations.

### Final fix (this entry)

Three architectural changes:

1. **Tail-only probe payload.** `SileroEndpointer` now hands the
   `probe_callback` **only the last `probe_tail_ms` (default 2000 ms)**
   of the active buffer, not the entire growing utterance. This
   anchors the question on "did anything new happen in the last
   2 seconds" — the comparison window stays constant size, so
   Whisper's hallucinations stop drifting and the empty-tail signal
   becomes reliable.
2. **Two-signal endpoint logic.** Pipeline-level `_stt_probe_async`
   now ends the turn on whichever signal fires first:
   - **Empty tail** (`text == ""` OR `len(text) < 4` OR
     `confidence < 0.55`). This is the dominant trigger when only
     music is in the tail — Whisper either returns nothing or a short
     hallucinated phrase with low confidence. Single hit forces
     endpoint immediately (~2.5 s total reaction time from speech
     start).
   - **Identical tail** to the previous probe, used as a safety net
     for cases where the user is genuinely done but Whisper happens
     to emit a stable phrase from the background.
3. **Reduced max-utterance hard cap and probe thresholds.**
   `max_utterance_s = 12` (was 30), `probe_interval_ms = 1000`
   (was 1500), `probe_min_active_ms = 1500` (was 2500). Even if both
   signal paths fail, the user now waits at most 12 s instead of 30.

### Latency budget

- **Without speaker bleed:** unchanged. The normal silence path
  (`vad_silence_ms = 1200`) fires first because the tail probe is
  gated on `probe_min_active_ms = 1500` and the user usually finishes
  before that.
- **With speaker bleed, user stops talking:** ~2.5 s. Flow is
  speech-start → 1.5 s of speaking → tail probe runs (~300 ms on
  RTX 5070 Ti) → empty tail detected → endpoint forced.
- **Hard cap:** 12 s, was 30 s.

### Why this can't break existing flows

- Final transcription still uses the **full** `active_frames` buffer
  on endpoint — only the probe path uses the tail. Whisper output the
  brain sees is unchanged.
- The probe path is opt-in: if `probe_callback=None` (e.g. headless
  unit tests, alternative pipeline assemblies), behaviour matches the
  original VAD.
- The `_probe_in_flight` flag prevents probe pile-up on slow GPUs.
- Probe failures are logged at `debug` and swallowed — they cannot
  break the surrounding VAD stream.

### Regression guards

- `tests/unit/audio/test_vad_turn_taking.py::test_external_endpoint_request_ends_turn_during_continuous_speech`
  — verifies the `request_endpoint()` path actually yields an utterance
  with reason `stt_stable`.
- `tests/unit/audio/test_vad_turn_taking.py::test_probe_callback_fires_only_after_min_active_duration`
  — guards against probe pile-up on short commands.
- `tests/unit/audio/test_vad_turn_taking.py::test_probe_payload_is_tail_only_not_full_buffer`
  — pins the tail-only payload contract; reverting to full-buffer
  probing would re-introduce the hallucination drift.

### Lessons

- **Trust the higher-quality signal.** Silero is a good speech
  detector but a poor *speaker* detector. Whisper, despite its own
  hallucination quirks, has a much stronger near-field bias and is
  the right authority for "is the user still talking".
- **Anchor comparisons on a fixed window.** Comparing transcripts of
  a growing buffer is meaningless if the new audio added between
  probes contains its own (changing) content. The tail trick is a
  general pattern: any "is anything new happening" signal should be
  computed on a sliding fixed-size window, not on cumulative state.
- **Default to the empty signal, not the equality signal.** "Nothing
  here in the last 2 s" is far more robust than "the same thing twice
  in a row" when the underlying transcriber is noisy.

---

## Bug #UI-Pin-2026-05-05: Taskbar shows the Python logo instead of the Jarvis mascot (MEDIUM)

### Symptom

The user looked at the Windows taskbar and saw the blue/yellow Python
logo where the Jarvis mascot should have been. They reported it as
"the Jarvis test app shows my Python logo wrong". From the user's
perspective, the running app was advertising itself as plain Python.

### Why the obvious fix wasn't enough

The launcher already does the standard Win32 dance for the **main
pywebview window** (`jarvis/ui/desktop_app.py`):

- `SetCurrentProcessExplicitAppUserModelID("PersonalJarvis.PersonalJarvis")`
  before `webview.create_window`.
- `set_window_icon_by_title("Personal Jarvis", jarvis.ico)` from the
  `_inject_token` hook **and** from a 5-second polling thread that
  scans for the HWND as soon as it exists.

That code path is correct and was already correct. The Python logo
the user saw was not coming from the main window at all.

### Root causes (three independent ones — that's why this looked weird)

1. **No icon work in the orb (Tk) process.** `ui/orb/overlay.py`
   creates a `tk.Tk()` titled `"JarvisOrb"`. Tk on Windows registers
   a window class **without** a class-icon slot, so Windows falls
   back to the **process icon** (`pythonw.exe` → Python logo). The
   `_hide_tk_window_from_task_switcher` `WS_EX_TOOLWINDOW` trick is
   best-effort and is unreliable under Windows 11 if the orb ever
   becomes momentarily visible (e.g. on first wake-word fire). When
   the toolwindow trick fails, the orb shows up in the taskbar with
   the Python logo and that taskbar association is then **cached**
   for the rest of the session.

2. **No icon work in the Qt overlay subprocess.**
   `OS-Level/src/overlay/main.py` is spawned by `OverlaySupervisor` as
   a separate `pythonw -m overlay` process. It calls `QApplication`
   without setting `AppUserModelID` and without `setWindowIcon`. Its
   `EdgeGlowWindow` / `MascotWindow` therefore inherit the default
   process icon — same Python-logo problem, separate process.

3. **A stale broken pin in the taskbar.**
   `%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Python.lnk`
   pointed at `pythonw.exe` with **no arguments**, no working directory,
   and `IconLocation = ",0"` (= the target's default icon = the Python
   logo). It had nothing to do with Jarvis — it was a leftover pin,
   probably from a `Pin to taskbar` action on a generic `pythonw.exe`
   process at some earlier point. Clicking it would launch a bare
   `pythonw` that exits immediately. Its visual was always the Python
   logo, regardless of what the live Jarvis windows did, because
   Windows uses the **shortcut's** icon for pinned items, not the
   live class icon.

The user couldn't tell these three apart visually — they all rendered
as "the Python logo somewhere in or next to my taskbar".

### Fix

Three changes, one per root cause:

1. **`jarvis/ui/icon_utils.py`** — extracted `_apply_icon_to_hwnd(hwnd,
   ico_path)` plus a public `set_window_icon_by_hwnd(hwnd, ico_path)`
   that sets `WM_SETICON` (big + small) **and** `SetClassLongPtrW`
   (`GCL_HICON` + `GCL_HICONSM`). The class-icon slot is the one that
   actually drives the taskbar render; without it Windows keeps
   falling back to the process icon. The existing
   `set_window_icon_by_title` now delegates to `_apply_icon_to_hwnd`.

2. **`ui/orb/overlay.py`** — added `_apply_jarvis_icon_to_tk_root(root)`
   called immediately after `tk.Tk()` and before
   `_hide_tk_window_from_task_switcher`. It does three things, each
   best-effort:
   - `ensure_windows_app_identity()` — pins the AUMID for this
     process (idempotent across processes).
   - `root.iconbitmap(default=jarvis.ico)` — Tk-level icon for all
     windows in this interpreter.
   - `set_window_icon_by_hwnd(int(root.winfo_id()), jarvis.ico)` —
     Win32-level `WM_SETICON` + class icon override on the actual
     HWND. This is the only call that reliably overrides the
     process-icon fallback for Tk on Windows.

3. **`OS-Level/src/overlay/main.py`** — added
   `_setup_app_identity_and_icon()` that runs **before** `QApplication`
   so the AUMID is pinned when Qt registers its window class, and
   added `app.setWindowIcon(QIcon(str(ico_path)))` immediately after
   `QApplication(sys.argv)`. `QApplication.setWindowIcon` propagates
   to every top-level widget the overlay creates (one EdgeGlowWindow
   per monitor plus the optional MascotWindow), so a single call
   covers all windows in this subprocess.

4. **The broken pin** — overwrote `Python.lnk` with the correct
   target/args/icon (`pythonw -m jarvis.ui.web.launcher`,
   `IconLocation=jarvis.ico`, `Description="Personal Jarvis"`). The
   original was kept as `Python.lnk.bak-<timestamp>` next to it.
   Setting `System.AppUserModel.ID` on the shortcut via `IPropertyStore`
   currently fails with `STG_E_ACCESSDENIED` because Explorer holds a
   handle on the pinned `.lnk`; this is cosmetic (the pin and the
   running app may briefly render as two separate taskbar buttons
   instead of one group) and clears on the next Explorer restart or
   logoff.

### Verification

- `data/_current_jarvis_icon.png` — the live class-icon of the
  running `Personal Jarvis` window, extracted via
  `GetClassLongPtrW + DrawIconEx + GetDIBits`. It is the mascot, not
  the Python logo. Repeating this extraction is the cheapest way to
  prove the live-window path is healthy.
- `data/_taskbar_zoom_after_pinfix.png` — taskbar screenshot after
  the pin rewrite. The Python logo at the previous slot is replaced
  by the mascot.
- `pytest -k "icon or taskbar or orb"` — 27 passed, 1 skipped.

### Why this can't recur silently

- Any future window-spawning subprocess that forgets the icon dance
  will visibly fall back to the Python logo on Windows. The reusable
  call site is `jarvis.ui.icon_utils.set_window_icon_by_hwnd(hwnd,
  project_icon_path())` plus `ensure_windows_app_identity()`. Use
  both — neither alone is sufficient on Windows 11.
- `FindWindowW(NULL, "Personal Jarvis")` returned `NULL` for the
  WinForms-class window even though `EnumWindows` listed it
  (observed live during this debug session). The HWND-based helper
  exists specifically because the title-based one is fragile across
  Tk / Qt / WinForms.
- Pinned `.lnk` files have **independent** icon state from live
  windows. A correct live-window icon does not fix a stale pin and
  vice versa. If the taskbar ever shows the wrong logo again, check
  three places, not one: live class icon (Win32), live AUMID
  (`shell32!SetCurrentProcessExplicitAppUserModelID`), and the
  pinned shortcut under `%APPDATA%\Microsoft\Internet Explorer\
  Quick Launch\User Pinned\TaskBar\`.

### Lessons

- **Three icon surfaces, three independent caches.** Class icon,
  AUMID-icon, and pin-shortcut-icon are stored in three different
  places. Fixing one without checking the others is normal and
  invisible until the user notices.
- **Process icon is the silent default.** Tk and bare Qt give you a
  window with no class icon; Windows then renders the EXE icon. For
  any toolkit on Windows, ship an explicit class-icon override.
- **Pinned shortcuts diverge from the running app over time.** A
  pin made years ago against a generic `pythonw.exe` survives all
  later refactors. Treat stale pins as a separate failure mode and
  inspect them when "the icon is wrong" reports come in.
- **`FindWindowW` is brittle across toolkits.** Prefer
  `EnumWindows` + title-match when probing across Tk / Qt / WinForms,
  or pass the HWND in directly when the toolkit gives it to you.

### Follow-up 2026-06-16: the taskbar *name* still said "Python" (icon was fine)

The icon fix above (class-icon override) was correct and the mascot
rendered, but the user reported the taskbar still **named** the app
"Python" on hover and in the right-click jump-list header. That is a
**fourth, independent surface**: the taskbar *name* is not the window
title and not the icon — for a grouped/pinned button it is resolved
from the AUMID's **registered `DisplayName`**.

`SetCurrentProcessExplicitAppUserModelID` only sets the grouping *key*;
the AUMID `PersonalJarvis.PersonalJarvis` was never **registered** under
`HKCU\Software\Classes\AppUserModelId\<AUMID>`, so the shell fell back to
the launching process's `FileDescription` — `pythonw.exe` → "Python".

**Fix (`jarvis/ui/icon_utils.py`):** new
`register_windows_app_user_model_id()` writes `DisplayName="Personal
Jarvis"` (+ an `IconResource` pointing at `jarvis.ico`) under that HKCU
key. It is folded into `ensure_windows_app_identity()`, which every
window-spawning process (desktop pywebview, orb Tk, OS-Level Qt) already
calls before creating its first window — so the friendly name is in
place the moment Explorer resolves the AUMID. Idempotent, Windows-only,
best-effort; pure no-op off Windows. Covered by
`tests/unit/ui/test_icon_identity.py` (real HKCU round-trip against a
throwaway AUMID, cleaned up after).

**Caveat — an *already-pinned* button is not retroactively renamed.** A
pinned shortcut's taskbar name comes from the **`.lnk` filename**, and
the maintainer's pin was a leftover literally named `Python.lnk` with no
embedded AUMID, so the registered `DisplayName` cannot reach it. New
runs and any *fresh* pin read "Personal Jarvis"; the stale pin must be
replaced (cleanest: unpin → re-pin the running window, no Explorer
restart).

**Now there are FOUR taskbar surfaces, four caches:** class icon
(Win32), AUMID icon, pin-shortcut icon, **and AUMID `DisplayName` (the
name)**. When "the taskbar is wrong" comes in again, check all four.

### Correction 2026-06-16 (same day): the HKCU `DisplayName` does NOT name the taskbar button

The follow-up above was wrong about the *mechanism*. The user re-reported the
running, **unpinned** app button still hovering as "Python" and its right-click
jump-list header reading "Python" — even though the live process (verified:
launched 18:02, after the fix) had both `SetCurrentProcessExplicitAppUserModelID`
**and** the HKCU `DisplayName` in place. Reading the live window's property
store (`SHGetPropertyStoreForWindow`) and the taskbar buttons' UIA `Name`
proved it: the registered HKCU `DisplayName` does **not** drive the taskbar
button name. That key is the **toast-notification** identity, a different
surface.

The name of a grouped taskbar button (and its jump-list header) is resolved by
matching the running window's process AUMID to a **Start-Menu shortcut**
carrying the same `System.AppUserModel.ID`, then using that shortcut's **file
name** + **icon**. There was no such shortcut under
`%APPDATA%\…\Start Menu\Programs\` (only a Desktop shortcut — not scanned — and
a `Startup\Disabled\` one tagged with a stale *personalised* AUMID
(`<username>.PersonalJarvis`-shaped)). With no AUMID-matched shortcut, Explorer fell back
to the process `FileDescription` → "Python".

**Fix (`jarvis/ui/icon_utils.py`):** new `ensure_start_menu_shortcut()` creates
and maintains `…\Start Menu\Programs\Personal Jarvis.lnk`, targeting
`pythonw -m jarvis.ui.web.launcher`, icon `jarvis.ico`, with the
`PersonalJarvis.PersonalJarvis` AUMID embedded via `IPropertyStore`. It is
folded into `ensure_windows_app_identity()` (called by every window-spawning
process before the first window appears), is idempotent (an existing shortcut
already carrying the AUMID is left alone), Windows-only, best-effort. The HKCU
`DisplayName` registration is kept — it correctly governs *toast* identity — but
its docstrings no longer claim it names the taskbar.

**Proof (live, end-to-end):** the AUMID-matched shortcut was deleted, the app
restarted, and the running button's UIA `Name` went `'Python · 1 active window'`
→ `'Personal Jarvis · 1 active window'`; the deleted shortcut was re-created by
the code (AUMID read-back = `PersonalJarvis.PersonalJarvis`). An
**already-grouped** button is *not* retroactively renamed — the shortcut must
exist before the window's button is created, which is why the fix runs at
process import and a restart is required. Covered by
`tests/unit/ui/test_icon_identity.py` (throwaway `programs_dir`, AUMID
round-trip).

**The real surface count is FIVE,** and the one that names the live button is
the **Start-Menu shortcut**, not the HKCU `DisplayName`. Two unrelated "Python"
leftovers survive this fix and are *not* the app's identity: the stale pinned
`Quick Launch\…\TaskBar\Python.lnk` (its name comes from its own file name —
unpin/re-pin to clear) and the mic-privacy flyout (`NVIDIA Broadcast / Python`),
which lists the raw *process* name and cannot be renamed without a signed host.

### Follow-up 2026-07-09: the Python logo came back — via the JarvisBar (drift regression)

A user on a **fresh test machine** saw the blue/yellow Python logo again. The
main pywebview window was healthy (verified live: its class icon is the mascot),
and so was the OLD orb (`ui/orb/overlay.py`, which still carried
`_apply_jarvis_icon_to_tk_root`). The culprit was the **JarvisBar**
(`jarvis/ui/jarvisbar/overlay.py`) — the newer Tk surface that is now the
**DEFAULT** `orb_style` (`jarvis_bar`). It created its `tk.Tk()` root with **no
icon work at all**, so it inherited the process icon (`pythonw.exe` → Python
logo). Enumerating the live process confirmed it: two top-level windows under one
PID — `WindowsForms10` "Personal Jarvis" wearing the mascot, `TkTopLevel`
"JarvisBar" wearing Python. The frameless bar is normally `WS_EX_TOOLWINDOW`
(off the taskbar), but that trick is best-effort on Win11; any leak surfaces the
Python logo, exactly as root cause #1 predicted.

**Root cause: drift.** The icon fix lived only in the orb; the JarvisBar was
added later and never got it — the two Tk surfaces had diverged. **Fix:** one
canonical, cross-platform helper `jarvis.ui.icon_utils.apply_tk_window_icon(root)`
that BOTH the JarvisBar and the orb now call (the orb is now a thin wrapper), so
they cannot drift again:

- **Windows** — `ensure_windows_app_identity()` + `iconbitmap(default=.ico)` +
  the Win32 `WM_SETICON`/`SetClassLongPtrW` class-icon override. `iconphoto` is
  deliberately NOT used on Windows: Tk re-asserts a `PhotoImage`-derived class
  icon on later map/update cycles and raced/overwrote the `SetClassLongPtrW`
  handle, leaving the live window a **blank/greyed** class icon (observed on the
  running app before the path was split). The `.ico` + Win32 path is the proven
  one.
- **Linux / macOS** — `root.iconphoto(True, PhotoImage(jarvis.png))`, which sets
  `_NET_WM_ICON` (what the dock/taskbar reads); the PNG is stashed on the root so
  Python does not GC it. This is the Linux face of the same "shows python3, not
  Jarvis" symptom.

**Verified end-to-end on the live app:** restarted the running desktop app
(editable install picks up the edit) and re-extracted the actual "JarvisBar"
window's class icon via `GetClassLongPtrW + DrawIconEx + GetDIBits` → the mascot,
not Python (and not blank). Guard: `tests/unit/ui/test_tk_icon_applied.py`
(importable helper + source-wiring assertions that fail if either Tk surface
stops calling it + a real-window Windows check).

**Lesson — the anti-drift rule generalizes:** every NEW window-spawning surface
(Tk toplevel, Qt window, subprocess) must route through the ONE
`apply_tk_window_icon` / `ensure_windows_app_identity` call site. A second copy of
the icon dance is a latent Python-logo regression waiting for the next surface.

### Follow-up 2026-07-09 (part 2): the REAL taskbar root cause — the launching EXE's icon

The JarvisBar fix above was necessary but NOT what the reporter saw. On the test
box the **main** WebView2 window's taskbar button stayed the Python logo even
though its window + class icon extracted as the mascot. A full empirical sweep
settled it: **the Windows 11 taskbar button takes its icon from the executable
that OWNS the window — and NOTHING else.** Verified to have ZERO effect on the
button (each set to the mascot, taskbar stayed Python): `WM_SETICON` (big+small),
`SetClassLongPtrW` (GCLP_HICON/HICONSM), an explicit AUMID, a fresh never-poisoned
AUMID, the AUMID-tagged Start-Menu shortcut (mascot icon + valid target + AUMID,
confirmed via `SHGetFileInfo`), the HKCU AUMID `IconResource`, an Explorer restart,
a full `IconCache.db` wipe, `SHChangeNotify(ASSOCCHANGED)`, and setting the icon
before first show. The proof of the mechanism: a window launched from a **copy of
`pythonw.exe` with the mascot stamped as its embedded icon** → mascot taskbar
button. So the whole class-icon machinery only brands the **titlebar + Alt-Tab**;
the taskbar button was never actually verified before (only the window's class
icon was, and the taskbar was ASSUMED to follow it).

**Why one machine and not another (the reporter's exact question).** The taskbar
button = the window-owning exe's icon, so it depends on *which exe owns the
window*:

- **Shipped build (the maintainer's main box):** the PyInstaller `Jarvis.exe`
  embeds the mascot and owns its own window → mascot. Always correct.
- **Source run on a normal python.org install:** the window-owner is the base
  interpreter's `pythonw.exe`, a normal writable file we can copy + brand.
- **Source run on the Microsoft Store Python (the test box):** the venv
  `pythonw.exe` is a thin redirector that re-spawns the **base** Store
  `pythonw3.13.exe`, and THAT process owns the window. The Store base exe is a
  0-byte app-execution alias inside a read-only `WindowsApps` package — it cannot
  be copied, renamed, or branded. So no in-repo code can brand the taskbar button
  under the Store Python; that install must run the shipped build, or use a
  python.org interpreter.

**Fix (works on every brandable install; graceful no-op otherwise).** At the ONE
launcher chokepoint (`main()`, which every entry point funnels through) re-exec
the launcher through `PersonalJarvis.exe` — a copy of the **base** `pythonw`
(`sys.base_prefix`, the true window-owner) placed beside it (so it finds
`pythonXX.dll` + the stdlib) with the mascot `.ico` stamped in via the Win32
`*UpdateResource` API. The venv is re-attached in the child via
`__PYVENV_LAUNCHER__=<venv pythonw>`, so the branded base copy runs the app with
the venv's packages while OWNING the window → mascot on the taskbar. Guarded
(env marker → no relaunch loop; skipped for `--headless`/`JARVIS_DEBUG`), and
best-effort: a 0-byte Store base exe or a read-only base dir returns `None` and
the app boots in-process exactly as before (taskbar keeps the Python logo, no
crash). `jarvis/ui/icon_utils.py`: `ensure_branded_launcher_exe` +
`maybe_reexec_through_branded_launcher` + `_replace_exe_icon`. Guard:
`tests/unit/ui/test_branded_launcher.py`.

**Verified end-to-end on the Store-Python test box** by installing python.org
3.13 + rebuilding the venv against it: launching via bare `pythonw -m
jarvis.ui.web.launcher` now re-execs to `PersonalJarvis.exe`, which OWNS the
"Personal Jarvis" window, and the live taskbar button is the Gigi ghost (captured
screenshot), not the Python logo.

### Follow-up 2026-07-09 (part 3): v1.0.5 reinstall STILL showed Python — the per-window Relaunch properties are the actual universal fix

**Part 2 shipped in v1.0.5 and the maintainer's reinstall still showed the Python
logo.** Root cause of the recurrence: the reinstall rebuilt the venv against the
**MS Store Python** — exactly the environment where the branded-exe re-exec
degrades to a no-op (the Store base exe cannot be copied/branded), which part 2
explicitly accepted as a cosmetic loss. That acceptance was wrong: it left every
Store-Python install (and any install where the base dir is not writable) showing
the Python logo forever. "Works on the maintainer's setup, breaks on the next
machine" — the exact §3 defect class.

**The mechanism part 2 missed** (and the reason its "the button follows the exe
icon and NOTHING else" conclusion was incomplete): the taskbar button follows the
window-owning exe's icon **only when the window carries no explicit identity of
its own**. Windows exposes a per-window property store —
`SHGetPropertyStoreForWindow` — with `System.AppUserModel.RelaunchIconResource` /
`RelaunchDisplayNameResource` / `RelaunchCommand` / `ID`, documented precisely
for apps hosted by a shared interpreter exe. Stamping
`RelaunchIconResource = "<jarvis.ico>,0"` flips the LIVE button to the mascot
instantly — no restart, no exe copy, no admin, works on MS-Store Python.

**Fix:** `jarvis/ui/icon_utils.py::set_window_relaunch_properties(hwnd)` —
stamps AUMID + icon + display name + relaunch command on the window's property
store (session-cached per HWND, COM-initialized for the poll thread,
best-effort). Called from `_apply_icon_to_hwnd`, the one chokepoint every icon
path already funnels through (the desktop icon-setter poll by title/PID, Tk
surfaces via `apply_tk_window_icon`), so every present and future window gets it
for free. The part-2 branded-exe re-exec stays (correct exe identity on
python.org installs); the relaunch properties are the layer that makes the
button correct EVERYWHERE. Guard: `tests/unit/ui/test_relaunch_properties.py`
(wiring + a real-window stamp/read-back test).

**Verified on the Store-Python box that reproduced the report:** cold boot via
bare `pythonw -m jarvis.ui.web.launcher` → taskbar button is the Gigi ghost;
`SHGetPropertyStoreForWindow` read-back on the live window confirms AUMID +
icon + name.

**Collateral regression, same report:** after the v1.0.5 reinstall, Windows
search no longer found "Personal Jarvis" — the Start-Menu shortcut
(`%APPDATA%\...\Start Menu\Programs\Personal Jarvis.lnk`) was missing. The
uninstall/reinstall removed it and the session kept running without it (boot-time
re-ensure only runs at import). Two watchdog-instrumented reboots could NOT
reproduce an active deleter in the app — every fresh boot recreates the shortcut
correctly. Hardening anyway: the desktop icon-setter now re-ensures the shortcut
once the window is up (`_start_icon_setter_thread`), so a shortcut deleted
mid-session heals within the same session instead of at the next boot. (Windows
search indexing may still lag a few minutes after recreation.)

**Lesson:** "the taskbar follows the exe icon" was verified by *changing* the
exe icon and watching the button follow — a true positive that masked the
cheaper, more general mechanism sitting one API away. When a shell surface
misbehaves, enumerate the *documented identity layers* for that surface first
(window property store > shortcut > exe resource), and test the highest-level
one before shipping workarounds at a lower level.

---

## Bug-008 Episode 2: Transcription view empty (HangupReason drift, regressed after restore) — 2026-05-05

- **Files**:
  - `jarvis/sessions/models.py` (Pydantic `HangupReason` Literal)
  - `jarvis/ui/web/frontend/src/components/sessions/types.ts` (TS union)
  - `jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx` (label switch)
  - `jarvis/sessions/schema.sql` (column doc-comment)
- **Symptom**: Transcription tab in the desktop app empty.
  `GET /api/sessions?limit=20` returned HTTP 500. Frontend treated the
  500 as "no sessions" and rendered the empty state.
- **Root cause**: Same as BUG-008 (first episode, fixed 2026-05-03).
  `jarvis/speech/pipeline.py` writes `hangup_reason="turn_complete"` for
  one-shot turns, but the Pydantic Literal had only six values and was
  missing `"turn_complete"`. A single row with that value caused
  `SessionListItem(...)` construction to raise `ValidationError`
  inside `SessionStore.list_sessions`, which propagated up to FastAPI
  as HTTP 500. The earlier fix had been lost during the restore on
  2026-05-01 (BUG-006 Three-Layer-Trap left this regression invisible
  because nobody re-tested the transcription tab).
- **Fix**: Three-layer correction was re-applied (Pydantic Literal, TS
  union, TSX switch case for the new label "Abgeschlossen" (Completed)). This
  alone would invite a third recurrence, so the fix was extended into
  a structural anti-drift pack:

  1. **Single source of truth** — new
     `jarvis/sessions/constants.py` exports `HANGUP_REASONS` plus one
     symbolic constant per value (`HANGUP_TURN_COMPLETE`, etc.).
     `jarvis/speech/pipeline.py` and `jarvis/sessions/init.py` now
     import these symbols instead of hard-coding strings — typos
     fail at import time, not at the API layer.
  2. **Runtime-asserted Pydantic mirror** —
     `jarvis/sessions/models.py` keeps the Pydantic-required inline
     `Literal[...]`, but a module-level assertion compares
     `typing.get_args(HangupReason)` against `HANGUP_REASONS` and
     raises `RuntimeError` on import if they drift.
  3. **Self-defending list endpoint** —
     `jarvis/sessions/store.py::SessionStore.list_sessions` now
     wraps each row's `SessionListItem(...)` construction in a
     try/except. On `ValidationError` it logs a structured warning
     (`hangup_reason_drift_skipped`) and skips the row. The list-API
     thus degrades to "missing some rows" instead of "HTTP 500,
     empty UI".
  4. **Three-layer parity test** —
     `tests/unit/sessions/test_hangup_reason_parity.py` reads
     `HANGUP_REASONS`, the TS union (`types.ts`), the TSX switch
     (`SessionList.tsx`), and the SQL doc-comment (`schema.sql`).
     Asserts all four agree.
  5. **DB-vs-schema integration test** —
     `tests/integration/test_sessions_db_compatibility.py` queries
     `SELECT DISTINCT hangup_reason FROM voice_sessions` against
     `data/sessions.db` (skipped if absent) and asserts every value
     is in `HANGUP_REASONS`. Catches new values introduced by code
     paths that bypass the constants module.
- **Verification (2026-05-05)**:
  - `curl /api/sessions?limit={5,20,100,200}` → HTTP 200 (was 500).
  - 20 sessions returned, including 4× `turn_complete`.
  - Live UI screenshot shows full list with the new "Abgeschlossen" (Completed)
    label badge.
  - Defense smoke: a copy of `data/sessions.db` with one row's
    `hangup_reason` set to `'not_a_real_reason'` returned 19/20
    rows + a single warning, never raising.
  - `pytest tests/unit/sessions/test_hangup_reason_parity.py
    tests/integration/test_sessions_db_compatibility.py` → 5 passed.

### Regression guards

- `tests/unit/sessions/test_hangup_reason_parity.py` —
  four assertions, one per layer; fails immediately if Python,
  TypeScript, TSX, or SQL drifts.
- `tests/integration/test_sessions_db_compatibility.py` —
  fails if a value is on disk that has not been registered in
  `HANGUP_REASONS`.
- Module-level assertion in `jarvis/sessions/models.py` — fails at
  import (test collection time) if the inline Pydantic Literal
  drifts from the tuple.

### Lessons

- **Restore workflows must rerun the regression suite for every UI
  surface they revert.** BUG-006 told us the desktop app has three
  layers (source / build / RAM); BUG-008 Episode 2 adds a fourth:
  *contract layers*. The Pydantic / TS / TSX / SQL quartet needs a
  parity test, otherwise restoring source quietly time-travels the
  Pydantic Literal back to a state that fails on rows the running
  pipeline still produces.
- **A user-visible 500 is a missing fallback.** The list endpoint
  did not need to fail closed — degrading to a partial list with a
  log warning gives the user the data they asked for and gives the
  operator a structured signal to act on. Closed-fail is appropriate
  for *write* paths where partial success would be wrong, never for
  *read* paths over an evolving schema.
- **Symbolic constants beat strings.** `HANGUP_TURN_COMPLETE` is a
  spell-check-able, find-references-able token. The string
  `"turn_complete"` is none of those. Cost is one tiny module; the
  payoff is that every IDE pivots from "good luck typing it
  consistently" to "the symbol is wrong, the file does not exist".

See also: `docs/anti-drift-three-layer.md` for the general pattern.

## BUG-012: Cold start shows flashing console windows and a black UI for 30+ seconds — 2026-05-09

- **Symptom (user-visible)**: opening the desktop app via the taskbar or
  pinned shortcut took 5–10 seconds to do anything visible, then a
  rapid burst of small windows opened and closed (the user described
  it as "many terminals popping up"), then the Jarvis window appeared
  but stayed black for another 20–30 seconds before any UI rendered.
  Sometimes the UI never appeared on that attempt at all.
- **Operator-visible state at the moment of the report**: `Get-Process`
  showed **113 leaked `node.exe`** plus **22 leaked `python.exe`**
  processes from prior runs alongside the live `pythonw.exe`. The
  desktop log showed the overlay supervisor logging
  `Supervisor: heartbeat-timeout (3.0s) -> kill+respawn` six times in
  a row, ending in `Supervisor: Cap-fired (6 Restarts in 300 s)`.
- **Three independent root causes, all firing during cold start**:
  1. **External overlay restart-storm.** `[overlay].enabled = true` in
     `jarvis.toml` told `jarvis.overlay.integration.start_overlay()` to
     spawn an external Qt-based mascot subprocess
     (`pythonw -m overlay --ws-port=7842`). The supervisor expects a
     heartbeat over WebSocket within 3 s
     (`DEFAULT_HEARTBEAT_TIMEOUT_S` in `jarvis/overlay/supervisor.py:47`).
     On this machine the heartbeat handshake reproducibly missed the
     deadline, so the supervisor killed and respawned the subprocess
     six times before the cap fired. Each respawn briefly displayed
     the Qt mascot window — *those* were the "flashing terminals" the
     user saw, not actual cmd.exe consoles. The in-process Tkinter
     `OrbOverlay` started by
     `jarvis/ui/desktop_app.py::_start_speech_and_orb` already
     provides a working mascot, so the external one is redundant on
     this host.
  2. **`CREATE_NO_WINDOW` missing on 16 subprocess callsites.** The
     desktop app runs under `pythonw.exe` (no attached console). When
     a child process is started via
     `asyncio.create_subprocess_exec(...)` or `subprocess.run(...)`
     **without** `creationflags=CREATE_NO_WINDOW`, Windows allocates
     a fresh console window for every child. CLI auth probes
     (`jarvis/clis/prober.py`), CLI installs/calls
     (`jarvis/clis/{auth,installer,tool}.py`), the awareness git
     probe (`jarvis/awareness/probes/git.py`), the harness base
     (`jarvis/harness/base.py`), the admin executor
     (`jarvis/admin/executor.py`), the MCP install probe
     (`jarvis/mcp/bootstrap.py`), the workflow runner shell step
     (`jarvis/workflows/runner.py`), the run-shell tool
     (`jarvis/plugins/tool/run_shell.py`), the codex auth status
     check (`jarvis/codex_auth.py`), the mission cleanup git command
     (`jarvis/missions/cleanup.py`), the mission-isolation worktree
     git commands (`jarvis/missions/isolation/worktree.py`), and the
     hardware detection probe (`jarvis/hardware/detection.py`) all
     spawned children without the flag. The MCP SDK already passed
     `CREATE_NO_WINDOW` internally
     (`mcp/os/win32/utilities.py::create_windows_process`), and the
     mission worker / critic runner / overlay supervisor used
     ad-hoc `_win32_creationflags()` helpers — but those were the
     exception, not the rule.
  3. **Frontier-Autoswitch blocked startup for 5–10 s.**
     `jarvis/ui/desktop_app.py::_run_backend` ran
     `loop.run_until_complete(apply_frontier_resolution(...))`
     **before** `server.start()`. That call makes six HTTP requests
     against Anthropic / Gemini / Grok / OpenRouter to discover
     newer model IDs, all on the critical startup path. WebView2
     opened the URL only after the loop unblocked, so the user saw
     the dark `#0a0e14` background until then. Frontier-Autoswitch
     only takes effect on the *next* restart anyway — there is no
     reason for it to block this start.
- **Process-leak symptom (113 + 22 zombies)**: every run that ended
  uncleanly during the restart-storm or via `Stop-Process` left the
  MCP server children orphaned because their parent (the desktop
  app) died before the MCP SDK's stdio-client teardown could fire.
  Across multiple sessions this accumulated to 100+ orphans. The
  fix is upstream — once the cold start is stable, the JOB-OBJECT
  guarantee on the SDK side keeps children attached to their
  parent's lifetime.
- **Fix**:
  1. New helper: `jarvis/core/process_utils.py` exports
     `NO_WINDOW_CREATIONFLAGS` — `subprocess.CREATE_NO_WINDOW` on
     Windows, `0` elsewhere. All callsites listed above now import
     and pass it via `creationflags=NO_WINDOW_CREATIONFLAGS`.
  2. `jarvis.toml`: `[overlay].enabled = false` with an inline
     comment pointing back to this BUG entry. The in-process
     `OrbOverlay` continues to provide the mascot.
  3. `jarvis/ui/desktop_app.py::_run_backend`: Frontier-Autoswitch
     moved to a `loop.create_task(...)` named `"frontier-autoswitch"`
     started **after** `server.start()`. The previous synchronous
     `loop.run_until_complete(apply_frontier_resolution(...))` block
     is replaced by a small comment that explains why the deferral
     is safe (Frontier-Autoswitch only takes effect on the *next*
     restart).
- **Verification (2026-05-09)**:
  - `curl http://127.0.0.1:47821/api/health` → 200 in 1.7 ms
    (was 36–60+ s before).
  - `curl http://127.0.0.1:47821/assets/index-88LRY03g.js` →
    200, 1473282 bytes (frontend bundle serves cleanly).
  - Log timeline of the new cold start:
    `10:46:31` log sink up → `10:46:35.9` channels live →
    `10:46:36.2` speech pipeline running →
    `10:46:36.8` Frontier-Autoswitch finished in the background →
    `10:46:47.7` wake loop running. Pre-fix the same path took
    `10:30:17` → `10:31:48` (~91 s).
  - `grep "Supervisor: spawned" data/jarvis_desktop.log` after the
    fix-time cutoff → 0 lines. Pre-fix every cold start logged 6
    spawns followed by a cap-fire.
  - User confirmation: "Runs cleanly, UI visible."
- **Regression guards / process for next time**:
  1. **One helper, one rule.** Any new `subprocess.run`,
     `subprocess.Popen`, or `asyncio.create_subprocess_exec` in this
     repo MUST import `NO_WINDOW_CREATIONFLAGS` from
     `jarvis.core.process_utils` and pass it via `creationflags=...`,
     unless the call deliberately needs a visible console (e.g.
     `jarvis/clis/external_terminal.py` opens an *external* terminal
     for the user — exception documented in the file). Reviewers can
     scan a diff with
     `rg "create_subprocess_exec|subprocess\.(Popen|run)" -n` and
     confirm each new occurrence either passes the flag or carries a
     comment explaining why it does not.
  2. **Re-enabling the external overlay requires a heartbeat fix
     first.** Flipping `[overlay].enabled = true` again without
     stabilizing the WebSocket handshake will reintroduce the
     restart-storm exactly as before. The supervisor cap (6 in
     300 s) limits the symptom but does not solve the user-visible
     flicker. If the external overlay is brought back, raise
     `DEFAULT_HEARTBEAT_TIMEOUT_S` from 3 s to 10 s **and** add a
     smoke test that a cold start logs zero `heartbeat-timeout`
     warnings within the first 60 s.
  3. **Cold start must complete in < 10 s to `/api/health` 200.**
     If a future change reintroduces a synchronous block on the
     `_run_backend` critical path, this is the symptom: the WebView
     window opens onto a dark background and stays empty until the
     block releases. Any new "I need to fetch X before brain build"
     code path goes into a `loop.create_task(...)` after
     `server.start()`, never before it.
  4. **Process-leak inspection.** After any session involving MCP
     servers, run
     `Get-Process node,python | Where-Object { $_.StartTime -lt (Get-Date).AddHours(-1) }`.
     A non-empty result is a leak — check whether the parent died
     before the MCP SDK's stdio-client teardown ran. The two
     legitimate cases (long-lived MCP children, debugging
     subprocess) should be rare and obvious.
- **Lessons**:
  - **`pythonw.exe` parents are silent multipliers.** Every missing
    `CREATE_NO_WINDOW` becomes a console window the user *sees*. On
    `python.exe` parents the consoles are reused, so the bug never
    surfaces in dev — it only shows up in the
    pythonw-via-shortcut path the user actually hits. This means the
    smoke test for "no console flicker" cannot run from a dev
    terminal; it has to launch the app the way the user launches it.
  - **Three small blockers compound into one large blocker.** None
    of the three root causes alone would have produced the 30 s
    black-window experience: the overlay restart-storm by itself is
    only ~10 s of mascot popups; missing `CREATE_NO_WINDOW` is only
    visual noise; Frontier-Autoswitch alone is a 5–10 s delay. All
    three at once look catastrophic. When a user reports a complex
    UX symptom, expect to find more than one cause.
  - **Idempotent restart, then check process tables.** Before
    diagnosing the actual code, confirm there are no leaked children
    from earlier runs (`Get-Process node,python`). On this report
    the 113 + 22 zombies were both a symptom (instability) and a
    confounder (they made every fresh metric harder to read).
---

## BUG-012 follow-up: Console-flicker storm returned on a fresh branch (2026-05-09)

- **Symptom**: User clicks the desktop shortcut, fifty-plus black console
  windows flash open and close during boot, the desktop window does not
  appear within ~20 seconds.
- **Root cause**: The original BUG-012 fix
  (`fc416073 fix(subprocess): NO_WINDOW_CREATIONFLAGS-Helper an alle
  Subprocess-Spawns`) lives on `feature/brain-aware-skills`, but the working
  branch `claude/improve-subagents-structure-5094K` was forked from
  `cc03843b` — *before* the helper was committed. Result: the helper
  module `jarvis/core/process_utils.py` and all 14 subprocess-callers'
  `creationflags=NO_WINDOW_CREATIONFLAGS` arguments were missing on this
  branch. Every `npx`/`git`/`uvx`/probe spawn during cold start opened
  a console window for ~100 ms.
- **Fix**:
  1. Cherry-picked `fc416073` into the current branch — restores
     `jarvis/core/process_utils.py` plus the 14 caller-side argument
     additions.
  2. Closed two new gaps that grew in after the original fix:
     - `jarvis/core/paths.py:141` — `subprocess.run([...mklink...])` ran
       without `creationflags` for every Jarvis-Agent-output session.
     - `jarvis/missions/kontrollierer/orchestrator.py:517` —
       `subprocess.run(["git", "diff", "HEAD"])` ran without
       `creationflags` on every mission completion.
  3. Verified that no remaining sync `subprocess.Popen|run` call in
     `jarvis/**` lacks `creationflags` except for the deliberately-visible
     terminal cases (`clis/external_terminal.py` uses `CREATE_NEW_CONSOLE`
     by design; `plugins/tool/open_app.py` opens user-facing apps via
     `start`).
- **Why this can recur**: any branch that forks from `main` before the
  fix lands ships without the helper. Until the fix is merged into `main`,
  every long-lived feature branch needs the cherry-pick. The grep
  `Grep "subprocess\.(Popen|run)\(" jarvis/**/*.py` followed by a
  `creationflags=` check is the regression guard until a unit test exists.
- **Verification**: `python -c "from jarvis.core.process_utils import
  NO_WINDOW_CREATIONFLAGS; print(hex(NO_WINDOW_CREATIONFLAGS))"` →
  `0x8000000`. App now starts on port 47821, single window
  ("Personal Jarvis", visible), no console flashes during boot on the
  patched branch.

---

## BUG-008 Episode 3: Transcription view empty due to `Literal` drift (HIGH, 2026-05-10)

- **Date:** 2026-05-10 · **Scope:** voice-session models / sessions API
- **Symptom:** The sidebar tab "Transcription" shows **"No voice sessions yet"**,
  even though `data/sessions.db` contains 267 sessions. `GET /api/sessions?limit>=6`
  throws 500 Internal Server Error with a Pydantic `ValidationError`:
  `Input should be 'voice_pattern', 'hotkey', 'idle_timeout', 'shutdown',
  'error' or '' [type=literal_error, input_value='turn_complete']`. The frontend
  shows the empty state because the list comes back empty. This bug is the **third recurrence**
  (Episode 1: 2026-05-03, Episode 2: 2026-05-05 after restore).
- **Root cause:**
  1. `jarvis/sessions/models.py` defined `HangupReason` as
     `Literal["voice_pattern","hotkey","idle_timeout","shutdown","error",""]`.
  2. `jarvis/speech/pipeline.py:1391` sets `_session_end_reason = "turn_complete"`
     on a normal turn end — that has been the **default hangup reason**
     for all standard replies since Bug Voice-Turn-2026-05-02.
  3. Pydantic rejects every session row with `hangup_reason="turn_complete"`.
     `list_sessions()` maps every DB row through `SessionListItem.model_validate(...)` —
     on the first row with `turn_complete` the whole request throws 500.
  4. Episodes 1 + 2 were fixed by adding "turn_complete" to the Literal.
     But: during the **restore on 2026-05-01**, `models.py` was reset to the old
     state — the value disappeared from the list again, without
     the restore noticing (three-layer restore trap, BUG-006).
- **Fix** (3 layers, 2026-05-10):
  - **Layer 1 (backend, the actual permanent fix):**
    `jarvis/sessions/models.py` — `HangupReason` and `VoiceTier` switched
    from `Literal[...]` to `TypeAlias = str`. Plus two documenting
    constants `KNOWN_HANGUP_REASONS` and `KNOWN_VOICE_TIERS` (frozenset).
    Pydantic now accepts **any string** as a hangup_reason — drift
    in the speech pipeline can never break the API again. Type
    safety is replaced by the drift-detector test (Layer 3).
  - **Layer 2 (frontend):**
    `jarvis/ui/web/frontend/src/components/sessions/types.ts` — `HangupReason`
    and `VoiceTier` switched from union types to `string`; `KNOWN_*` exported as
    `as const` arrays.
    `jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx` —
    `hangupLabel("turn_complete")` → "Response complete"; the default case
    falls back to the raw string, not to "—".
  - **Layer 3 (drift-detector test):**
    `tests/unit/sessions/test_models_resilience.py` (18 cases) checks three
    properties:
    1. Every value in `KNOWN_HANGUP_REASONS` validates (parametrized).
    2. A **made-up** value (`"vad_silence_long_future_value"`) does NOT crash
       validation — guards against a regression back to `Literal`.
    3. **Live DB crawl**: distinct `hangup_reason` values from
       `data/sessions.db` are compared against `KNOWN_HANGUP_REASONS`,
       and every single value is fed through Pydantic. If
       tomorrow a new pipeline code path introduces `"vad_long_silence"`,
       the test goes red — with a clear message about where to extend.
- **Verification** (2026-05-10 13:48 — performed directly):
  - `pytest tests/unit/sessions/test_models_resilience.py -v` → **18/18 green**
    (incl. live-DB drift detector with the real 5 distinct values incl. `turn_complete`).
  - `pytest tests/unit/sessions/ -v` → 19/19 green (no regression in
    `test_recorder_lifecycle.py`).
  - `python -c "store.list_sessions(limit=500)"` directly against
    `data/sessions.db` → 267 items validated (before: ValidationError from the first
    `turn_complete` row).
  - **Frontend build** (`npm run build`) → passed (had to clean up a dead
    reference to `PluginsView` in `MainView.tsx` along the way, because
    `tsc -b` runs first and otherwise blocks).
  - **Jarvis restart** (PID 37296 → 44016): `pythonw -m jarvis.ui.web.launcher`.
    `Invoke-WebRequest http://127.0.0.1:47821/api/sessions?limit=500` →
    HTTP 200, 268 items, distribution `{idle_timeout: 115, voice_pattern: 106,
    turn_complete: 20, shutdown: 15, hotkey: 12}`.
  - **UI verification** (Chrome screenshot via claude-in-chrome): the tab
    "Transkription" now shows the full list — newest session "läuft" (running),  <!-- i18n-allow -->
    next to it "Geht up" 4 turns / "Was geht ab?" 2 turns with cost,
    detail panel on the right with Markdown/plain-text/JSON export. Empty state
    gone.
- **Rule for future changes:**
  Anyone who migrates `HangupReason` or `VoiceTier` back to `Literal[...]`
  will be stopped by the drift-detector test — before it goes into production.
  Anyone who introduces a new `_session_end_reason` value in the speech pipeline
  (`pipeline.py:1391` and relatives) only has to enter it in
  `KNOWN_HANGUP_REASONS` (backend) and `KNOWN_HANGUP_REASONS` (frontend
  const array) plus optionally in `hangupLabel` — **the API
  keeps working even without this maintenance**, that is the point.
- **Lesson:** A Pydantic `Literal` as a wire-format constraint is a
  **trap** when the values come from a separate code component that
  the author does not oversee. Three drift episodes in ten days
  proved this impressively. Replace `Literal` with `str` +
  a drift-detector test as soon as the value source is a different layer.

---

## BUG-014: Jarvis silent — TTS audio lands on the WDM-KS output (HIGH, 2026-05-10)

- **Date:** 2026-05-10 · **Scope:** audio output / auto-headset resolver
- **Symptom:** The user hears nothing from Jarvis anymore. STT works
  (`turn-state: USER_SPEAKING`), the brain replies
  (`🤖 Jarvis [de] (streamed): ...`), Gemini-TTS returns HTTP 200
  (`POST .../gemini-3.1-flash-tts-preview:generateContent "HTTP/1.1 200 OK"`),
  TTS echo lock is set — but **no sound comes out of the
  headset**. The wake-acknowledge chime is also missing. Clear in the log:
  ```
  WARNING | ACK-Playback failed: Error opening OutputStream:
    Unanticipated host error [PaErrorCode -9999]:
    'Blocking API not supported yet' [Windows WDM-KS error -9999]
  ```
  Occurred repeatedly between 2026-05-10 13:27 and 16:44 (8+ occurrences
  in the same daily log). Secondary symptom: `turn-state: JARVIS_SPEAKING ->
  IDLE` triggers in the same millisecond as `TTS-Echo-Sperre aktiv` —
  the state transition runs speculatively before the first audio sample; the
  `AudioOutFirst` bus event in `play_chunks` normally synchronizes that.
  In the crash path there is never a sample, so no sound despite a "correct"
  state.
- **Root cause** (two chained bugs in `jarvis/audio/player.py`):
  1. **Headset pattern misses the real Logitech device.** `_HEADSET_PRIORITY`
     contained `("Logitech PRO X", "Logitech", ...)` — but sounddevice lists
     the headset on this system as `"Lautsprecher (PRO X)"` without
     the manufacturer name. `sub.lower() in name.lower()` matches neither "logitech
     pro x" nor "logitech" against "lautsprecher (pro x)". → The Logitech
     variant (idx=14, **WASAPI**) is ignored.
  2. **Realtek match lands on WDM-KS.** The next pattern `"Realtek HD
     Audio"` matches `"Speakers (Realtek HD Audio output)"` (idx=19) —
     **the only Realtek device is on `Windows WDM-KS` only**.
     PortAudio's WDM-KS backend does **not** implement the **blocking-stream
     API** (see PortAudio Issue #303); every
     `sd.OutputStream(blocksize=0, ...).write()` attempt crashes with
     PaErrorCode `-9999` *"Blocking API not supported yet"*. The ACK
     playback path (`_play_blob` → `_open_output_stream`) logs the
     `WARNING`. The streaming-TTS path (`play_chunks`) uses the same
     `_open_output_stream` — the exception propagates there, but is
     swallowed by the caller layer (speech pipeline) as a generic "TTS failure",
     hence no separate log line at the actual
     going-silent moment.

  The previous code comment on `_HOSTAPI_PREFERENCE`
  (`"Windows WDM-KS": 1, # low-latency, aber exclusive-mode-risks`) marked
  WDM-KS as the second-best choice — that was a false assumption.
  WDM-KS is not "exclusive-mode risky", but **structurally unusable** for our
  blocking-write architecture.
- **Fix** (`jarvis/audio/player.py`, three surgical changes):
  - **Layer 1 (pattern match):** `_HEADSET_PRIORITY` prepended with `"PRO X"`
    — now matches the user's headset regardless of whether the driver
    supplies the manufacturer name or not.
  - **Layer 2 (hostapi whitelist):** WDM-KS removed from `_HOSTAPI_PREFERENCE`.
    Instead of a penalty position it now gets the default
    rank 99 (effective last). A comment documents the true
    reason (blocking API not supported, PortAudio issue, our own
    architecture).
  - **Layer 3 (defensive double filter):** New constant
    `_FORBIDDEN_OUTPUT_HOSTAPIS = frozenset({"Windows WDM-KS"})` plus
    an active filter loop in `_resolve_output_device`: if the same
    physical device (match on `name`) is available on a non-WDM-KS hostapi,
    the WDM-KS variant is removed entirely from the candidate
    list. If a device exists ONLY on WDM-KS, it stays
    in — better a last-resort device than none. Defense-in-
    depth against future pattern drift.
- **Verification** (2026-05-10 16:50 — performed directly):
  - Live resolver test:
    `_resolve_output_device("auto-headset")` → `idx=14
    Lautsprecher (PRO X) | hostapi=Windows WASAPI | channels=8` ✓
    (before: `idx=19 Speakers (Realtek HD Audio output) | hostapi=
    Windows WDM-KS`)
  - Direct `AudioPlayer.play_pcm` test with a 0.4s 440Hz sine at the
    Gemini sample rate (24 kHz): `PLAYED OK` (the user heard the
    sound). The `-9997 Invalid sample rate` warnings are the
    **expected** sample-rate fallback logic (24kHz → 48kHz →
    device-default → 44.1kHz), not the `-9999` crash.
  - `pytest tests/unit/audio/ --ignore=tests/unit/audio/test_capture_device.py`
    → **4/4 green**. The one failure in `test_capture_device.py::
    test_auto_headset_prefers_wasapi_for_same_microphone_name` is
    **pre-existing** (also red in the stash without the fix, affects the mic
    resolver `capture.py`, not the output player) and unrelated.
  - Jarvis restart (PID 64864 → 62728): `pythonw -m
    jarvis.ui.web.launcher`. A fresh log line confirms:
    `auto-headset → Lautsprecher (PRO X) (idx=14, ch=8, hostapi=
    Windows WASAPI)`. No more `-9999` crashes after the
    restart timestamp.
- **Rule for future changes:**
  Anyone who wants to support a new headset/speaker driver should first
  check with `python -c "import sounddevice as sd;
  [print(d) for d in sd.query_devices()]"` which strings PortAudio
  actually delivers — the Windows Device Manager name is not the
  same. Plus: never add WDM-KS back into `_HOSTAPI_PREFERENCE`
  as long as our player code uses `sd.OutputStream` with a blocking
  `stream.write()`. If WDM-KS is desired (low-latency
  use case), the player has to be rebuilt onto a **callback API**
  (`sd.OutputStream(callback=...)` with a ring buffer) — a complete
  architecture change, not a one-line toggle.
- **Lesson:** Three lessons from this bug.
  1. **Driver naming is not stable.** What is called
     "Logitech PRO X" in the Windows Device Manager can show up in the
     PortAudio layer as "Lautsprecher (PRO X)" — the manufacturer name disappears
     depending on the driver generation. Substring patterns against user devices must
     match the **shortest unique token** (`"PRO X"`), not the
     full marketing name.
  2. **HostAPI penalty ≠ HostAPI filter.** A hostapi with a structural
     incompatibility (blocking API missing) does not belong in the ranking,
     but in a hard ban list. Sorting can let a device
     win if it is the only one — sometimes that is exactly what you
     do not want.
  3. **Silent fail in the audio path.** The streaming-TTS path
     (`play_chunks`) throws the `-9999` exception, the speech-pipeline
     caller catches it as a generic exception. Only the ACK single-shot
     path logs a meaningful `WARNING`. Both paths should
     have the same empty-audio detection / the same meaningful-logging
     discipline — otherwise the louder bug is the more helpful
     diagnostic anchor. Follow-up open: unified error reporting
     in the streaming path (separate entry, once the voice-state
     transition bug is fixed).
- **Secondary finding (open, not in this fix):**
  `JARVIS_SPEAKING → IDLE` triggers before the first audio sample —
  speculative state transition. The `AudioOutFirst` bus event in
  `play_chunks:419` synchronizes that after the fact, but the
  state machine does not wait. Documented in anticipation of a separate voice-
  state audit (no entry in this doc, because the
  sound bug was the trigger and the state bug only a secondary
  symptom).



## BUG-015: Desktop blackscreen — stale editable install of `overlay` (HIGH, 2026-05-10)

- **Symptom:** The pywebview window opened with the title "Personal Jarvis"
  but the contents stayed solid black. The Chrome-served URL
  (`http://127.0.0.1:47821/`) loaded fine for the first ~30s and then
  also started timing out. `/api/health` did not respond despite a TCP
  listener on port 47821, and the `pythonw.exe` process eventually died
  without a Windows Application-Error event.
- **Root cause (third encounter of BUG-006 layer four — editable install
  pin to a stale clone):**
  `pip show overlay` reported the project location as
  `<USER_HOME>\Desktop\Personal Jarvis-main\OS-Level`.
  That directory had been deleted earlier; only the active repo at
  `<USER_HOME>\Desktop\Personal Jarvis\OS-Level` exists now.
  The `__editable__.overlay-0.1.0.pth` shim therefore pointed at a path
  that does not exist, so `import overlay.schema` raised
  `ModuleNotFoundError: No module named 'overlay'`. The Welle-4 import
  graph routes through `jarvis/overlay/schema.py:16`, which re-exports
  symbols from the top-level `overlay` package. The `from
  jarvis.overlay.integration import start_overlay` line in
  `jarvis/ui/desktop_app.py:959` therefore raised at import time, ~5 ms
  after the last successful log line ("Friends-Stack live"). The error
  bubbled into `try/finally` around `loop.run_until_complete`, the
  backend thread closed the loop, and uvicorn stopped accepting new
  connections.  pywebview, blocked in the main thread, was left
  pointing at an HTTP server that no longer responded — the user saw a
  black window and Chrome saw timeouts.
- **Diagnostic chain that misled us first:**
  - Logs showed `OSError: [WinError 64] The specified network name is no longer available` in
    `asyncio/proactor_events.py:843` together with `Asyncio event
    context: Task was destroyed but it is pending!`. That was a
    secondary symptom from the broken accept-coro / GC of pending
    tasks during the crash and not the cause.
  - `CliToolRegistry.bootstrap()` was suspected (parallel
    `asyncio.create_subprocess_exec` on the Windows Proactor loop). A
    semaphore was added experimentally and reverted once the editable
    install was the actual cause. Bootstrap never even ran in the
    failing boots — the import error happened in `_run_backend` before
    `_bootstrap_clis` got scheduled.
  - The "process is gone" symptom was misread as a hard crash;
    actually the backend thread terminated cleanly and pywebview kept
    blocking until we killed the parent.
- **Fix:**
  ```
  pip uninstall overlay -y
  pip install -e "<USER_HOME>/Desktop/Personal Jarvis/OS-Level" --no-deps
  ```
  Verification: `cat
  ~/AppData/Roaming/Python/Python311/site-packages/__editable__.overlay-0.1.0.pth`
  must point at the active repo and `python -c "import overlay.schema"`
  must succeed.
- **Lessons:**
  1. **Layer-four restore-trap recurs.** BUG-006 already documents the
     four-layer pattern (worktree, frontend build, RAM instance,
     editable install). Episode 2 (BUG-014) hit the `jarvis` install;
     this episode hit the `overlay` install. After every clone-rename
     or partial restore, run `pip show <pkg>` for every editable
     project listed in
     `~/AppData/Roaming/Python/Python311/site-packages/__editable__*.pth`,
     not just for `jarvis`.
  2. **Top-level shim re-export is fragile.** `jarvis/overlay/schema.py`
     re-exports from `overlay.schema` (the OS-Level sibling package).
     If the OS-Level package is unavailable, every consumer of
     `jarvis.overlay.*` fails at import time, which is the worst time
     to fail. A defensive `try/except ImportError` plus a fallback
     would have downgraded this to a soft warning instead of a backend
     crash. Worth considering for AD-15 in
     `docs/jarvis-agents-bridge.md` or its overlay equivalent.
  3. **Server-hangs need lifecycle checks.** Inside
     `_run_backend`, the import of `start_overlay` happens between two
     `loop.run_until_complete` calls. Any exception there silently
     ends the backend loop. A `try/except` with an explicit
     `logger.exception` around the post-`server.start()` block would
     have surfaced the import error in the first run instead of
     forcing a 30-minute diagnostic detour.
- **Regression guard (recommended, not yet wired):** add a smoke test
  that imports `jarvis.overlay.integration` at process start — fails
  fast at boot instead of mid-run. File:
  `tests/integration/test_overlay_import_smoke.py`.

## BUG-016: Voice path silent after spawn_openclaw — Kontrollierer never triggered (HIGH, 2026-05-10)

- **Symptom:** Jarvis listens, transcribes correctly, transitions
  THINKING -> LISTENING without ever entering SPEAKING. The user hears
  nothing. Mission Control shows the mission was "dispatched" but never
  ran. After the next app restart the mission appears as
  `state=FAILED, reason=crash_recovery, error_class=OrchestratorCrash`
  even though no crash actually happened.
- **Symptom in `voice_events`:** `BrainTurnCompleted` with
  `finish_reason=suppress_response` and `text_len=0`, immediately
  followed by `ActionExecuted tool_name=spawn_openclaw success=true
  duration_ms=2`, then `SystemStateChanged THINKING -> LISTENING`.
  No `OpenClawBackgroundCompleted` (since renamed `JarvisAgentBackgroundCompleted`)
  event ever fires, so the speech pipeline has nothing to read back.
- **Root cause:** `spawn_openclaw` only called
  `MissionManager.dispatch()`, which persists the mission as PENDING
  and publishes `MissionDispatched`. Nothing in the voice path called
  `Kontrollierer.run_mission(mission_id)`. The REST path
  (`jarvis/ui/web/missions_routes.py:249-252`) had been doing both
  steps explicitly, but the voice tool dropped the second step. The
  Welle-4 lazy-resolver fix (commit `e7eefa2d`, 2026-05-10) wired the
  `MissionManager` setter through, but the matching `Kontrollierer`
  setter was missing, so even after the bootstrap completed there was
  nothing to pick up the dispatched mission. PENDING missions then
  accumulated until the next `MissionManager.start()` recovery sweep
  marked them all as `OrchestratorCrash` — making the failure look
  like a runtime crash when in fact the mission orchestrator simply
  never received the trigger.
- **Diagnostic chain:**
  - `data/sessions.db` showed turns with empty `jarvis_text` and
    matching `voice_events` containing `finish_reason=suppress_response`
    plus `spawn_openclaw success=true duration_ms=2`. The 2 ms
    duration is the giveaway — a real fire-and-forget that hands work
    off to a worker takes longer than that.
  - `data/missions.db` confirmed the missions: every recent dispatch
    was in `state=FAILED` with `iter=0` and `reason=crash_recovery`.
    The `MissionDispatched` events were present; no `MissionPlanReady`
    or `WorkerSpawned` events followed.
  - `grep run_mission` showed only one caller in the entire codebase:
    `jarvis/ui/web/missions_routes.py:252:
    background_tasks.add_task(kontrollierer.run_mission, mission_id)`.
    The voice path had no equivalent.
- **Fix (this commit):**
  1. Add a `Kontrollierer` singleton + setter in `jarvis/brain/factory.py`
     mirroring `set_mission_manager` (`set_kontrollierer`,
     `_resolve_kontrollierer`, `_KONTROLLIERER_REF`).
  2. Plumb a `kontrollierer_resolver` into `SpawnOpenClawTool` (since renamed `SpawnWorkerTool`). The
     background dispatch now calls `kontrollierer.run_mission(mission_id)`
     after the persist step, mirroring the REST path.
  3. Register both setters in `jarvis/ui/web/server.py::_init_mission_stack`
     after `bootstrap_missions()` returns.
  4. New regression tests in
     `tests/integration/test_worker_lazy_bootstrap.py` (renamed from `test_openclaw_lazy_bootstrap.py`):
     - `test_voice_path_triggers_kontrollierer_run_mission` — voice
       path must call both `dispatch` and `run_mission`.
     - `test_voice_path_no_kontrollierer_logs_warning` — graceful
       degradation when the Kontrollierer hasn't bootstrapped yet.
     - `test_voice_path_kontrollierer_crash_publishes_completed_event`
       — orchestrator crashes still publish a failure event so the
       voice listener can speak the error instead of leaving silence.
- **Lessons:**
  1. **Two-step dispatch contracts must be explicit.** The architecture
     deliberately splits "persist + publish" (`MissionManager.dispatch`)
     from "plan + execute" (`Kontrollierer.run_mission`) for replay
     resilience. That's a feature for the event store, but it's a foot-
     gun for callers who only do step one. Either both steps live in a
     single helper (`dispatch_and_run`) or every dispatch site has an
     explicit pickup. The current code chose the latter — fine, as long
     as new callers know about the contract.
  2. **`OrchestratorCrash` is a misleading recovery label when the
     orchestrator was never triggered.** The recovery sweep can't tell
     "crashed mid-run" from "never started". A future improvement
     (BUG-016 follow-up) is to split the reason into `crash_recovery`
     (header was RUNNING/CRITIQUING) vs. `pickup_missing` (header was
     still PENDING, no plan event ever fired) — the second case is a
     wiring bug, not a runtime crash, and the noise in `MissionFailed`
     makes the bug harder to spot.
  3. **Symmetry checks across the wiring layer pay off.** The voice
     path adopted the Lazy-Resolver pattern for the manager but skipped
     the same pattern for the orchestrator. A short ADR-style audit of
     "what objects cross from app.state into the brain factory" would
     have caught this before it shipped.
- **Regression guard:** the three new asserts in
  `tests/integration/test_worker_lazy_bootstrap.py` (renamed from `test_openclaw_lazy_bootstrap.py`) cover the happy
  path, the early-bootstrap fallback, and the orchestrator-crash
  fallback. The full lazy-bootstrap suite is now 9 tests; the broader
  factory-wiring + routing + manager-commands sweep stays green at 84
  tests.

## BUG-017: Transcription view shows only "Auflegen." for every voice session — recorder turn-overwrite + payload-whitelist (HIGH, 2026-05-10)

This is the **fourth episode** of the "Transcription view is empty" class
(after BUG-008 ×3). The previous three were one bug: a Pydantic
``Literal`` shed every time the pipeline learned a new hangup reason.
This is a different bug entirely — the data is **persisted wrong** at
the recorder, which means widening the Literal would never have
helped, and tightening the parity tests around the Literal would never
have caught it.

- **Symptom (the user actually sees):** Every Voice-Session card in
  the Transcription view shows the preview text ``Hangup`` (or
  ``(no user text recorded)`` for sessions where the brain
  emitted ``suppress_response`` early). Opening any session shows a
  single Turn whose User-block reads ``Hangup`` even when the user
  spoke a full conversation. Jarvis-block is empty. Tools list shows
  ``spawn_openclaw`` × N. The actual transcription is gone.
- **Root cause A — multi-utterance turn collapse:**
  ``jarvis/sessions/recorder.py::_on_transcript_final`` always wrote
  ``current_turn.user_text = event.transcript.text`` and never closed
  the turn. Turn boundaries are normally drawn by ``_on_system_state``
  on the SPEAKING→LISTENING transition, but in Jarvis-Agents-routed turns
  the brain returns ``finish_reason="suppress_response"`` and the
  state goes THINKING→LISTENING (no SPEAKING). The auto-turn therefore
  stays open across every utterance in the session, and the text
  field gets overwritten on each ``TranscriptFinal`` — last write
  wins. Last write is ``Auflegen.`` because that is the hangup phrase.
- **Root cause B — TranscriptFinal raw payload empty:**
  ``_payload_for`` had an unwrap branch ``if k == "transcript" and v
  is not None: payload["text"] = ...``, but ``"transcript"`` was
  missing from ``fields_whitelist``. The branch was unreachable and
  every persisted ``TranscriptFinal`` event row carried ``payload =
  {}``. Any future replay layer that reads ``voice_events`` for
  per-utterance text gets nothing.
- **Fix** (``jarvis/sessions/recorder.py``):
  1. ``_on_transcript_final`` finalizes the current turn first if it
     already carries ``user_text`` — pipeline-independent boundary
     that fires regardless of whether SPEAKING was reached.
  2. ``"transcript"`` added to ``_payload_for.fields_whitelist`` so
     the unwrap branch becomes reachable.
- **Regression guard:** two new tests in
  ``tests/unit/sessions/test_recorder_lifecycle.py``:
  - ``test_multiple_transcript_finals_in_suppressed_session_keep_each_utterance``
    — three TranscriptFinals + suppress-style THINKING→LISTENING
    transitions must persist three separate turns with three separate
    user texts (currently passes; would have failed before the fix).
  - ``test_transcript_final_event_payload_contains_text`` — the raw
    event row for a TranscriptFinal must carry ``text`` and ``lang``.
- **Why the existing parity tests didn't catch this:** the parity
  tests in ``test_hangup_reason_parity.py`` enforce vocabulary
  agreement across five layers. They have nothing to say about
  recorder *logic*. The lesson is that schema parity prevents one
  failure mode (HTTP 500 on list) but not the other (silent data
  loss in the writer). Both classes need their own tests.
- **Existing rows are NOT recoverable.** The DB persisted ``"Auflegen."``
  and the empty ``user_text`` strings as-written; no reconstruction
  from the raw ``voice_events`` table is possible because B was also
  active and the raw rows are blank too. The fix only affects voice
  sessions started after the recorder restart at 2026-05-10 ~20:23.
- **Drift maintenance:** ``test_hangup_reason_parity.py`` was already
  red on ``main`` because ``models.py`` migrated from ``Literal[...]``
  to plain ``str`` after BUG-008 episode 3 but the parity test still
  searched for the Literal block. Two parity tests rewritten to read
  the new ``KNOWN_HANGUP_REASONS`` frozenset / TS const tuple
  instead. Same coverage, new shape.

---

## BUG-018: STT stability probe cuts real speech mid-sentence on low Whisper confidence (HIGH, 2026-05-11)

- **Date**: 2026-05-11
- **Severity**: HIGH — user-facing voice quality regression. User cannot
  finish complex sentences. Symptom (user words): "Jarvis always thinks you've already finished speaking … that used to work quite well, then a bug appeared." Concrete production case
  (session ``10000001-0000-4000-8000-000000000001`` at 17:22): user said
  "Can you please spawn a sub-agent that..." — VAD endpointed
  after 160 ms of silence (budget was 1200 ms) and the brain was called
  on a half-question; the rest of the sentence ("...pulls out five research topics for me") became Turn 2 and arrived as a fragment.
- **Files**:
  - `jarvis/speech/pipeline.py` (`SpeechPipeline._stt_probe_async`,
    Signal 1 / "empty tail" classification)
  - `tests/unit/speech/test_turn_taking.py` (four new regression tests)

### Smoking-gun log line

```
17:22:13.870 | STT probe: empty tail (text='spawnen welcher' conf=0.45) → force endpoint
17:22:13.898 | voice activity stop: reason=stt_stable
17:22:13.899 | VAD endpoint: reason=stt_stable duration_ms=3648 speech_ms=1760 silence_ms=160
```

15 characters of real speech, no hallucination pattern — but Whisper
returned ``confidence=0.45`` and the probe treated that alone as
"user is done."

### Root cause: spec / code drift (OR vs AND)

The original BG-VAD-2026-05-05 entry that introduced the probe specified
the empty-tail signal as "Whisper either returns **nothing** or
**a short hallucinated phrase with low confidence**" — note the **AND**
inside the second clause. The implementation collapsed that into a
flat disjunction:

```python
tail_is_empty = (
    not text
    or len(text) < self._probe_min_text_len   # < 4 chars
    or confidence < self._probe_min_confidence  # < 0.55  ← bug
)
```

So **confidence < 0.55 alone** was a valid endpoint trigger, regardless
of how long or how plausible the transcribed text was. Whisper's
average-log-probability is naturally below 0.55 on 2-second tails that
end on a grammatically dangling word (German relative pronouns
``welcher / welche / welches`` (German relative pronouns "which"), subordinating conjunctions, prepositions)
because the language model has no follow-up tokens to anchor the score.
Every time the user paused to think mid-clause, the probe declared the
tail "empty" and cut the turn.

### Fix

Confidence is removed from the empty-tail signal. Instead, the existing
``_STT_HALLUCINATION_RE`` (originally written for the pre-brain filter)
now decides whether the probe text is a known Whisper-on-silence
hallucination. ``_probe_min_confidence`` stays as a field for telemetry
and future use but no longer steers the endpoint by itself.

```python
tail_is_empty = (
    not text
    or len(text) < self._probe_min_text_len
    or _STT_HALLUCINATION_RE.search(text) is not None
)
```

Signal 2 (stable repetition of the same transcribed tail) is unchanged
and continues to catch the residual case where Whisper latches onto a
stable background phrase that escapes the regex.

### Why this preserves speaker-bleed protection (BG-VAD-2026-05-05)

The dominant speaker-bleed phrases that originally motivated the probe
already match ``_STT_HALLUCINATION_RE``: "Vielen Dank." ("Thank you."), "thanks for
watching", "please subscribe", "Untertitel im Auftrag …" (Subtitles on behalf of...), "mediagroup",
"copyright …". These are still forced to endpoint regardless of
confidence. Anything else short enough to be a hallucination (under
``_probe_min_text_len = 4``) is also still caught.

What we lose: pathological speaker-bleed phrases that are (a) longer
than 4 characters, (b) not matched by the hallucination regex, AND
(c) not repeated by Whisper on the next probe. That intersection is
empirically small; if it surfaces in practice, the right response is to
extend the regex, not to re-introduce the confidence cliff.

### Regression guards

Four new tests in ``tests/unit/speech/test_turn_taking.py``:

- ``test_probe_does_not_force_endpoint_on_real_speech_with_low_confidence``
  — feeds the exact production payload (text=``"spawnen welcher"``,
  confidence=0.45) and asserts ``vad.request_endpoint`` is **not**
  called.
- ``test_probe_forces_endpoint_on_empty_tail`` — asserts the
  speaker-bleed branch still fires when Whisper returns nothing.
- ``test_probe_forces_endpoint_on_known_hallucination_phrase`` — asserts
  ``"vielen dank."`` still ends the turn, even with high confidence.
- ``test_probe_forces_endpoint_on_stable_repeating_tail`` — asserts
  Signal 2 still works as the safety net.

### Lessons

1. **OR-vs-AND drift between spec and code is a recurring failure mode.**
   The original BUGS.md entry described the speaker-bleed signal as a
   conjunction; the code implemented a disjunction. Both reviewers
   missed it because each individual term *looked* reasonable in
   isolation.
2. **Confidence is not a binary "did the user say something" signal.**
   Whisper's confidence reflects language-model surprise, not
   acoustic-vs-silence. Use it as a tie-breaker, never as a sole
   classifier.
3. **Regression tests for endpoint logic should always pin a real
   transcript example from the field**, not a synthetic
   "low-confidence string". The bug only became obvious once we wrote
   down the actual payload (``"spawnen welcher"``) — abstract
   "len > min, conf < threshold" tests would have passed both before
   and after the fix.

---

## BUG-027: Orb invisible after accidental drag onto secondary monitor (HIGH, 2026-05-18)

- **Date**: 2026-05-18
- **Severity**: HIGH — user-facing voice-feedback regression. User words:
  "Jarvis, the mascot doesn't spawn anymore when you say Hey Jarvis, it just doesn't spawn anymore." The wake-word still triggers, the speech pipeline
  still transitions IDLE → LISTENING, and OrbBusBridge still calls
  ``orb.show(mode="listen")`` — but the orb pops up at the persisted pin
  on a secondary monitor where the user is not looking. From the user's
  perspective, the mascot disappeared.
- **Reproduce**:
  1. Drag the orb onto a non-primary monitor and release. The orb-drag-and-pin
     feature (merged 2026-05-17 on ``feature/orb-drag``) writes the position
     to ``[overlay.mascot]`` in ``jarvis.toml``.
  2. Restart Jarvis.
  3. Say "Hey Jarvis". Wake-word log shows
     ``OrbBridge._on_state: IDLE → LISTENING`` but the orb is invisible
     because it spawns at the persisted position on the other screen.
- **Diagnose with**:
  - ``python scripts/verify_orb_appears.py`` — prints
    ``geometry=108x108+<x>+<y>``. If x is negative or larger than the
    primary monitor width, the orb is off the primary screen.
  - ``python -c "import ctypes ..."`` (one-liner that dumps
    EnumDisplayMonitors) to confirm the persisted ``position_monitor``
    is actually a secondary monitor.
  - Read the live log: presence of
    ``OrbBridge._on_state: IDLE → LISTENING`` without a visible orb means
    the bus path is healthy; the bug is in the orb's window placement.
- **Root cause**: ``DRAG_THRESHOLD_PX = 5`` was too low — a casual cursor
  twitch during a double-click could commit a drag. The persisted pin
  then survived the restart because ``resolve_placement`` honoured every
  monitor in the live ``EnumDisplayMonitors`` result, including secondary
  monitors that the user could not see. There was no defense layer
  between "stale pin on disconnected monitor" (already covered) and
  "stale pin on a monitor I am not looking at right now".
- **Fix (2026-05-18)**:
  1. ``ui/orb/drag_persistence.py:resolve_placement`` gained a
     ``require_primary: bool = True`` parameter. When True (the safe
     default), a persisted pin on a non-primary monitor is treated like
     a missing monitor and the orb falls back to the primary anchor.
     Power users can opt back into secondary-monitor pinning via
     ``[overlay.mascot] allow_secondary_monitor_pin = true`` in
     ``jarvis.toml``.
  2. ``ui/orb/overlay.py:start`` reads the new flag via
     ``load_allow_secondary_monitor_pin`` and clears the stale pin from
     ``jarvis.toml`` on recovery, so the next boot starts clean.
  3. ``DRAG_THRESHOLD_PX`` raised from 5 → 16 px (manhattan distance).
     A casual mouse twitch during a double-click is now four times less
     likely to commit a position change.
  4. The on-disk pin (DISPLAY2 at x_relative=2428, y_relative=1268) was
     cleared from ``jarvis.toml`` as part of the same change so the user
     sees the orb at the default taskbar anchor immediately after restart.
- **Tests**:
  - ``tests/unit/ui/test_orb_drag_persistence.py`` — four new cases
    covering ``require_primary`` semantics: drop on secondary, honour on
    primary, escape-hatch via ``require_primary=False``, and the default
    contract (omitted parameter = safe behaviour).
  - ``tests/unit/ui/test_orb_drag_handlers.py`` — updated the threshold
    guard to assert ``DRAG_THRESHOLD_PX == 16`` and added an end-to-end
    BUG-027 scenario that reproduces the real-world DISPLAY1/DISPLAY2
    topology.
- **Why writing this down matters**: the orb-drag feature is one day old.
  The complaint pattern ("the mascot doesn't spawn anymore") looks like a
  classic bus-event regression — and that's where my first instinct went.
  The live log proved the bus path was healthy; the bug was geometric
  and only visible by *running* the verify script and reading the live
  ``EnumDisplayMonitors`` topology. Lesson: when a UI element "doesn't
  appear", always print its actual geometry before tracing the event
  bus — the cheap diagnostic answers the question in five seconds.
- **Class-level prevention (2026-05-18, post-fix)**: BUG-027 became the
  trigger event for [ADR-0016 — Visible-Feedback Contract](adr/0016-visible-feedback-contract.md).
  The ADR establishes a `UserVisibleFeedback{surface, expected, observed,
  correlation_id}` event so every UI surface can publish "did the user
  actually receive my feedback?" data the runtime can compare against
  intent. Orb is the first adopter. Five additional defense layers
  shipped under the ADR umbrella:
  - **L0** `UserVisibleFeedback` event + orb adopter (`ui/orb/overlay.py`
    publishes via `_publish_visibility_feedback` after `deiconify`).
  - **L1** Selective boot flash — when an honoured pin lives on a
    secondary monitor, the orb deiconifies on the primary anchor for
    800 ms before migrating to the pin (so the user always *sees* it
    on boot). Skipped in the 99% single-monitor / primary-pin case.
  - **L2** Discovery-independent recovery — voice phrases "Orb zurück", "wo bist du", "reset orb" are matched by <!-- i18n-allow -->
    `jarvis.brain.local_action_gate` and dispatched to the new
    `reset_orb_position` tool (publishes `OrbResetRequested`). Removes
    the chicken-and-egg problem (henne/ei) that the old right-click recovery only worked
    if the orb was already visible.
  - **L3** Post-condition assertion in `resolve_placement` — catches
    future regressions inside the function itself.
  - **L4** Visual-contract test suite
    `tests/unit/ui/test_orb_visibility_contract.py` (28 cases including
    a real-Tk visibility gate on Win32).
  - **L5** `python -m jarvis --orb-doctor` — dry-run diagnostic that
    reads the persisted pin + live `EnumDisplayMonitors` topology and
    reports where the orb *would* spawn, without opening a Tk window.

---

## BUG-028: Capability Hallucination — Jarvis confirms actions it cannot perform (HIGH, 2026-05-20)

- **Date:** 2026-05-20 · **Scope:** Brain, Ack-Brain, Critic, voice path

### Symptom

Jarvis confirms sending emails, creating calendar entries, posting to social
media, and other actions it has no registered capability for. The Ack-Brain
says "wird erledigt", the brain returns a phantom success response, and the TTS  <!-- i18n-allow -->
reads a confirmation to the user. The action never happens. The user is deceived.

Classic trigger example: "Send an email to Sam" → TTS plays "Die Email wurde gesendet." → No email was sent. No error was raised. No log entry indicates a failure. <!-- i18n-allow -->

This is not a single-site regression — it is a structural coupling gap: the
brain layer and the critic layer are both decoupled from the actual executable
surface (the set of registered tools, MCP servers, harness adapters, and
local-action patterns). Any capability class that is absent from the running
process can be hallucinated.

### Root Cause

Three decoupled layers each contribute independently:

1. **Brain layer:** The system prompt contains hardcoded capability claims
   (e.g. `NUTZE: search_web`) regardless of whether those tools are registered.
   The LLM is never told "this tool does not exist" because there is no
   authoritative list of what does exist. Result: the LLM makes up tool calls
   or claims actions it cannot perform.

2. **Ack-Brain layer:** The Ack-Brain persona prompt does not forbid
   action-promise phrases ("mache ich", "wird erledigt", "ist gesendet"). <!-- i18n-allow -->
   The Ack-Brain therefore confirms phantom actions sub-second, before the
   deep brain even runs.

3. **Critic layer:** The Critic currently ratifies empty diffs for non-file
   tasks (AD-9 in `docs/jarvis-agents-bridge.md`). A Jarvis-Agent worker can produce
   `success=True` with no tool-call evidence, purely from a text claim. The
   Critic reads the worker's unverified assertion and signs it off.

See [ADR-0017](adr/0017-capability-coupling.md) for the full architectural
analysis and the three-layer fix.

### Defenses (ADR-0017)

1. **`CapabilityRegistry` — single source of truth.** Every tool, MCP server,
   harness adapter, and local-action pattern registers a `Capability` dataclass
   at boot. No registration = no voice-path invokability.

2. **Pre-generation gate — two insertion points (regex-only, AP-11 preserved).**
   - `jarvis/brain/local_action_gate.py` — if `has_action_intent` and
     `resolve_intent` returns `None`, return `LocalActionMode.UNSUPPORTED`
     with the deterministic response. The brain is never called.
   - `jarvis/brain/manager.py` — sibling `_capability_resolves(text)` check
     alongside `_should_force_spawn`. If action-intent and no matching
     capability and not smalltalk: skip brain + Jarvis-Agent, emit UNSUPPORTED.

3. **Dynamic system prompt.** The hardcoded `NUTZE: search_web` block is
   replaced with `registry.render_for_prompt(lang)`. If a capability is not
   registered, it is not listed. A hard rule is appended: "You must never claim
   to perform an action that is not listed above."

4. **Ack-Brain forbidden vocabulary.** Action-promise phrases are added to the
   forbidden vocabulary in `jarvis/brain/ack_brain/persona_prompt.py`. The
   Ack-Brain may only acknowledge, ask for clarification, or stay silent.

5. **Critic capability-honesty gate.** For capabilities with
   `requires_evidence=True`, `CriticVerdict.success=False` when no tool-call
   evidence is present. `summary_de` is derived from tool-call evidence, not
   from the worker's unverified text claim. For Welle-2 mock Jarvis-Agents (no
   telemetry), the Critic defaults conservative-fail.

### Regression Test

`tests/integration/test_capability_coupling_e2e.py` — covers:

- All 5 hard-negative utterances (mail, calendar, WhatsApp, pizza, X-post):
  each must produce `LocalActionMode.UNSUPPORTED` and zero phantom-success
  TTS calls.
- Hard-positive utterances (open app, read file, smalltalk): must not hit
  UNSUPPORTED (false-negative guard).
- Search-web prompt-claim drift: utterance "Such im Web nach Python 3.13"
  must hit UNSUPPORTED when no `web-search` capability is registered
  (guards manager.py:774 drift).
- Critic regression: `requires_evidence=True` capability + empty diff +
  no tool-call → `verdict.success=False` + `reason="capability_not_executed"`.

```bash
pytest tests/integration/test_capability_coupling_e2e.py -v
```

### Related

- [ADR-0017 — Capability Coupling](adr/0017-capability-coupling.md) — full
  decision record, alternatives considered, extensibility contract.
- [docs/plans/capability-coupling/EXTENSIBILITY.md](plans/capability-coupling/EXTENSIBILITY.md) — contributor guide for adding new capabilities.
- `docs/anti-drift-three-layer.md` — cross-reference section comparing this
  pattern to the anti-drift and visible-feedback patterns.
- AD-9 in `docs/jarvis-agents-bridge.md` — Critic + risk-tier preconditions that
  BUG-028 exposes as insufficient for non-file tasks.

## BUG-029: Long dictation truncated — VAD 8 s max-utterance cut + no downstream accumulation (HIGH, 2026-05-24)

- **Date:** 2026-05-24 · **Scope:** Speech (VAD endpointing, turn handling)

### Symptom

When the user dictates a long, continuous utterance, after roughly 8 seconds
(~18-20 words at a normal speaking rate) the transcript "stops counting" and
restarts — earlier words are forgotten. The user's own example: "If I speak
many 'Hallo's in a row, at some point it forgets all the old Hallos and only
keeps the first one, then starts over."

### Root Cause

Two decoupled layers, confirmed by parallel deep-dive investigation:

1. **VAD hard cut.** `SileroEndpointer` force-ends the utterance once
   `total_frames * VAD_FRAME_SAMPLES >= max_samples`
   (`jarvis/audio/vad.py:218`), emitting `reason="max_utterance"` while the
   user is *still talking* (`silent_run == 0`). The cap is hardcoded to 8 s at
   `jarvis/speech/pipeline.py` (the `SileroEndpointer(max_utterance_s=8)`
   construction) — no config override exists. The yielded segments tile the
   speech with no gap, but each is a *fragment*, not a finished turn.

2. **No downstream accumulation.** The session loop fed every yielded blob into
   `_handle_utterance(pcm)` as a fully independent brain turn. The endpoint
   `reason` flowed on a separate channel (`_on_vad_endpoint`) and was never
   available where the turn was finalized, so a forced mid-speech cut was
   indistinguishable from a natural pause. Each ~8 s chunk became its own STT
   call + brain turn, so the visible/heard transcript restarted at every cut.

### Fix

Reason-driven PCM accumulation (the VAD already labels every endpoint, so the
fix is consumer-side and the `utterances()` byte contract is untouched — the
wake path in `jarvis/speech/whisper_wake.py` also consumes it):

1. **Shared reason vocab — `jarvis/audio/vad_reasons.py`.** Single source of
   truth (`FORCED_CUT_REASONS` / `NATURAL_END_REASONS`) imported by both the
   VAD producer and the pipeline consumer — pre-empts the multi-layer
   enum-drift class (AP-4). `vad.py` now emits the named constants.

2. **Capture the reason — `_on_vad_endpoint` stores `self._last_endpoint_reason`**
   (set synchronously just before the matching blob is yielded).

3. **Accumulate in `_handle_utterance`.** If the previous endpoint was a
   forced cut (`reason in FORCED_CUT_REASONS`), prepend the carried PCM, buffer
   the merged PCM, set `LISTENING`, and return *without* a brain turn. A natural
   endpoint (`silence` / `stt_stable`) finalizes the merged audio as ONE turn —
   STT transcribes the whole dictation once. Runaway guardrails
   (`_MAX_CARRY_PCM_BYTES`, `_MAX_CARRY_SECONDS`) force-finalize a stuck mic so
   accumulation can never grow unbounded. Carry is reset at each session start
   so a mid-dictation hangup/idle never leaks into the next session.

### Regression test

`tests/unit/speech/test_long_dictation_accumulation.py` — six cases (forced cut
buffers without STT; natural end after N forced cuts transcribes the merged PCM
once; `stt_stable` finalizes; natural-end-alone is a single turn; runaway guard
finalizes). Red-green verified: the lead test fails on the pre-fix code with
`stt.calls == [b'AAAA']` (fragment transcribed immediately) and passes after.

---

## BUG-031: Live overlay style swap (bar ↔ mascot) aborts the process — Tcl_AsyncDelete (HIGH, 2026-06-02)

### Symptom

Switching the on-screen overlay style at runtime from one *real* surface to
another (e.g. mascot → bar) via `PUT /api/settings/overlay-style` killed the
whole desktop app: the overlay window vanished, the launcher PID died, and the
FastAPI/WebSocket server went down. stderr ended with:

```
Tcl_AsyncDelete: async handler deleted by the wrong thread
```

(C-level `abort()`; Windows exit code `0x80000003`). The first swap of a session
sometimes "succeeded" (`applied_live=true`); the second swap reliably crashed.

### Root cause

Each overlay surface (`JarvisBarOverlay`, mascot `OrbOverlay`) owns its own
`tk.Tk()` root, created on a short-lived named daemon thread
(`jarvisbar-tk-mainloop` / `orb-tk-mainloop`). A "live swap" tried to tear the
old root down (`stop()` → `root.after(0, root.destroy)` + thread join) and build
a new one. Tkinter / `_tkinter` keeps **process-global, per-thread** Tcl
interpreter state. When the destroyed root's Python wrapper object is later
garbage-collected on a *different* thread than the one that created it (the
worker thread running the route, or the main loop), Tcl fires `Tcl_AsyncDelete`
on the wrong thread and aborts the whole process. There is no Python-level
try/except that can catch this.

The false-positive trap: a throwaway feasibility probe
(`screenshots/live_swap_feasibility.py`) did a single bar → mascot swap and
reported `LIVE_SWAP_OK`, but it ended with `os._exit(0)` — which skips Python's
GC / finalization, the exact step where the cross-thread delete happens. The
multi-cycle probe `screenshots/live_swap_three_cycles.py` reproduces the abort
on the second build.

### Fix

`DesktopApp.swap_overlay` (`jarvis/ui/desktop_app.py`) NEVER creates a second
`tk.Tk()` root at runtime. Only root-free transitions apply live: switching to
`none` (rootless `NullOverlay`) and re-showing an already-built, never-destroyed
surface. A transition that would need a brand-new real surface returns
`applied_live=False` / `restart_required`; the choice is persisted and takes
effect on the next app start (the frontend can offer a one-click self-restart so
the user never closes + reopens by hand). True instant switching would require
unifying both overlays under a SINGLE long-lived Tk root and swapping rendered
content (canvas / widgets) instead of the root — a larger refactor, never the
per-style-root approach.

### Regression test

`tests/unit/ui/test_desktop_swap_overlay.py` pins the contract: `none` +
cached-reuse apply live; an uncached real style returns `restart_required` and
does NOT repoint the bridge (no runtime root build).

### Lesson

A verification probe that ends in `os._exit()` is worthless for teardown / GC
bugs — it jumps past the finalization where that bug class lives. Test the full
lifecycle (multiple cycles + real interpreter shutdown), not the happy single
case with a hard exit.

## BUG-032: "Jarvis listens forever / never speaks" — playback watchdog reads a stale cross-turn progress counter (CRITICAL, 2026-06-08)

### Symptom

The user finishes speaking, Jarvis acknowledges (the ack bubble shows), but no
answer is heard and the session falls back to LISTENING — it looks like Jarvis
"listens forever / does nothing." Intermittent in a deceptive way: a quick
back-and-forth works ("it worked 5 seconds ago"), but any turn where the brain
thinks longer than ~5 s — force-spawn routing, tool use, provider latency,
"create a blog", "spawn a sub-agent mission" — is reliably swallowed. The
giveaway in the log is an abort that fires only ~1.5 s after speaking begins:

```
21:11:18.366  turn-state: PROCESSING -> JARVIS_SPEAKING
21:11:19.847  WARNING TTS playback stalled — no audio frames for 5.0s — aborting device (device-wedge recovery)
21:11:19.848  turn-state: JARVIS_SPEAKING -> LISTENING
21:11:22.719  HTTP 200 … gemini-…-tts-preview      # TTS only RETURNED ~3 s AFTER the abort
```

A "5.0 s no-frames" abort 1.48 s into a turn is physically impossible unless the
counter was already ~5 s stale; there is NO `AudioOutFirst published` before the
abort (no frame was ever produced for this turn); and the device opens fine
moments later. There is no real device wedge — the "device-wedge recovery" label
is a misdiagnosis.

### Root cause

The Wave-1 TTS playback stall watchdog (`jarvis/speech/pipeline.py::_await_playback`
+ `_playback_progress_stalled`) reads `AudioPlayer.last_write_ns`. That counter
was zeroed exactly ONCE in `AudioPlayer._init_progress()` (at construction) and
afterwards only ever bumped after a successful `stream.write` — it was **never
reset at the start of a new playback**. From the second turn onward it carried
the PREVIOUS turn's timestamp. The `if last_write_ns <= 0: return False` guard
(meant to ignore the legitimate "no first frame yet" window) therefore only
worked on the very first playback of the whole process. On every later turn whose
brain+synthesize gap exceeded `_TTS_PLAYBACK_STALL_S` (5 s), the stale timestamp
tripped the watchdog the instant playback was attempted — before the first frame
— and the fully-synthesized answer was silently discarded. **The watchdog was
measuring the idle gap BETWEEN turns, not progress WITHIN the playback.** This is
a regression introduced *by* the Wave-1 device-wedge fix (the watchdog that
replaced the old 120 s ceiling).

### Fix

- `jarvis/audio/player.py::play_chunks` — reset `self.last_write_ns = 0` and
  `self.frames_written = 0` at the START of every playback, BEFORE awaiting
  `_get_play_lock()`. This restores the watchdog's `<=0` "no first frame yet"
  guard for every turn (not just the first), and the pre-lock placement closes a
  lock-wait window (a lock held by a non-frame-writing op such as a slow
  stream-open must not leave a stale value visible to the watchdog).
- `jarvis/speech/pipeline.py::_await_playback` — made progress-aware: while
  `last_write <= 0` only a generous no-first-frame ceiling (`_TTS_PLAYBACK_CEILING_S`)
  applies (covers a provider that never yields any audio); once frames flow, only
  the mid-playback no-progress stall (`_playback_progress_stalled`) applies. The
  old flat total-time ceiling — which also truncated any single answer longer
  than 20 s — was dropped. The original mid-playback device-wedge protection
  (frames started, then froze) is fully preserved.

### Regression test

- `tests/unit/audio/test_player_stall_recovery.py::test_play_chunks_resets_progress_at_start`
  and `::test_play_chunks_resets_progress_before_lock_wait` — pin that
  `play_chunks` zeroes the counter at the start, even while the play lock is held
  by a concurrent op.
- `tests/unit/speech/test_speak_playback_timeout.py::test_await_playback_does_not_abort_long_active_playback`
  (a healthy, actively-progressing long playback past the ceiling must NOT be
  aborted) and `::test_await_playback_still_aborts_genuine_midplayback_wedge`
  (a real frames-then-freeze device wedge MUST still abort in ~`stall_s`).
- Live-verified: a 14 s-gap turn that was previously swallowed emits
  `AudioOutFirst published` and speaks the answer.

### Lesson

**A progress/stall watchdog counter MUST be reset at the start of each unit of
work it guards.** If the counter is process-global and only ever advances, it
stops measuring "progress within this unit" and silently degrades into "time
since the last unit" — so it fires spuriously after any idle gap longer than its
threshold. Any future watchdog that polls a shared counter (`last_write_ns`, a
heartbeat, a `last_progress_ns`) must (a) zero/arm that counter at unit start and
(b) re-arm its "not started yet" guard per unit, not just at construction.
Diagnostic tell: when a watchdog reports a resource wedge but the timestamps are
impossible — the abort fires earlier than the stall threshold, or the resource
responds *after* the abort — suspect a stale cross-unit counter before you
suspect the hardware. Related family: "session never reaches IDLE / answer
silently dropped" (Bug Voice-Turn-2026-05-02, BUG-014, BUG-016).

## BUG-033: Autostart "doesn't start after reboot" — Windows 11 throttles the shell:startup .lnk (HIGH, 2026-06-09)

### Symptom

The user reboots and Jarvis does not appear, concluding the autostart feature is
broken. The cross-platform autostart port (the "7th port") was already shipped:
the `Personal Jarvis.lnk` was present in `shell:startup`, pointed at a valid
`pythonw.exe -m jarvis.ui.web.launcher`, the working dir existed, the editable
install resolved correctly, and the entry was not disabled in Task Manager
(`StartupApproved` empty). Status said "installed and current" — yet it "didn't
start".

### Root cause

Not a missing/broken entry. Windows 11 processes `shell:startup` items through
Explorer's **serialized, throttled** startup queue — roughly one item every
~30 s, Startup-folder items processed *after* Run-key/UWP StartupTask items. On a
machine with ~20+ startup programs (Docker, Ollama, LM Studio, Epic, Discord,
Razer, NVIDIA, Spotify, …) the Jarvis shortcut fired **4-8 minutes after login**,
so the user gave up long before it appeared. Evidence (2026-06-09): auto-login
(explorer) at 14:43:28; first Jarvis log line 14:50:47 (~7 min); the sibling
**Ollama** `.lnk` in the same Startup folder fired 14:52 (~9 min). Across boots:
2026-06-08 boot 17:32 → Jarvis 17:36 (~4 min). The original design (§2/§10) had
explicitly listed "prompt/reliable process start" as a **non-goal** — it
guaranteed only that the entry exists, never that Windows runs it promptly.
Secondary: the `.lnk` `WindowStyle=7` started Jarvis minimized → even when it
finally launched, nothing visibly popped up.

### Fix

Windows autostart upgraded from the throttled `.lnk` to a **per-user logon
Scheduled Task** (Task Scheduler bypasses the Explorer startup throttle → starts
within seconds of login). macOS (`RunAtLoad` LaunchAgent) and Linux (XDG
`.desktop`) already fire promptly at login → **unchanged** (the throttle is
Windows-only). Registering a task needs a one-time UAC prompt (a non-elevated
process is denied — verified on Win 11, even for an Administrator filtered token);
*reading* state does not. So the task is (un)registered only on an **interactive**
call (`AutostartManager.install(spec, *, interactive=False)` keyword; Settings
toggle / wizard pass `True`), runs Jarvis **non-elevated** (`RunLevel=Limited`,
AP-17), and the silent boot reconcile never prompts — it ensures the no-elevation
`.lnk` **fallback** so autostart still works (possibly delayed) and the Settings
panel offers an "enable instant start" upgrade. `start_minimized` now defaults to
False → the autostart launch opens the window **visibly**.

### Defense (bug class)

"Entry exists" ≠ "OS runs it promptly". For any OS-integration that hands work to
a platform scheduler/queue, verify the *observed* launch latency on a real boot,
not just that the artifact is present — and prefer the scheduler subsystem
(Task Scheduler / launchd / systemd-user) over the desktop-shell startup queue
when promptness matters. Guards: `tests/unit/autostart/test_windows_task.py`.

## BUG-035: "Listens forever" #4 — explicit Jarvis-Agent command hijacked by a topical skill match, then a beheaded mute turn ends in silence (HIGH, 2026-06-10)

### Symptom

"Ich möchte, dass du für mich einen Sub-Agent spawnst … Gmail … analysiert" — <!-- i18n-allow: quoted user utterance -->
Jarvis stays in LISTENING, never answers, no mission is created, and the
session idle-hangs-up 50 s later. Looks identical to BUG-034 from the outside,
but the transcript DID arrive complete (the BUG-034 fix worked: two forced
cuts merged + finalized, 562 KB transcribed after a Groq-429 retry).

### Root cause (three stacked, log `data/jarvis_desktop.log` 2026-06-10 14:34)

1. **Routing hijack.** The utterance explicitly names the execution vehicle
   ("Sub-Agent spawnst" — a `force_spawn_phrases` trigger), but "Gmail"
   matched the `plugin-gmail` pairing skill. The AD-S3 skill guard
   ("skills win over force-spawn", built 2026-06-09) ran BEFORE the explicit
   trigger check, disarmed force-spawn ("force-spawn skipped: utterance
   matches an installed skill"), and the non-mission pairing skill fell
   through to a plain inline brain turn — no mission, no optimistic ACK.
2. **Mute brain turn.** The inline Gemini turn streamed no speakable sentence
   for 20 s (progress signals kept the brain-stall guard quiet; plausibly an
   AFC tool loop against the dead Gmail OAuth — see the open reauth item from
   2026-06-07). The no-first-frame TTS ceiling beheaded the turn:
   "TTS produced no audio within 20s — aborting (no first frame)" →
   `🤖 Jarvis (streamed): ` (empty). The sub-second ACK had also been
   discarded (`ack_lang_mismatch_total`), so nothing was ever audible.
3. **Silent ending.** `_handle_silent_brain_turn` fell through to the
   clarify-question gate, which is OFF by user mandate 2026-06-09
   (`clarify_incomplete_enabled` default False) — so the beheaded empty turn
   returned to LISTENING without a sound, violating AD-OE6 (zero silent
   drops). 30 s later: idle hang-up.

### Fix

- **AD-S9** (`jarvis/brain/manager.py` + spec §6): an explicit heavy-work
  trigger phrase outranks the skill match — `generate()` clears
  `_skill_turn_match` and `_should_force_spawn` checks the trigger pattern
  BEFORE the AD-S3 guard (every mode). The mission path owns such turns.
- **Beheaded-turn notice** (`jarvis/speech/pipeline.py`): the no-first-frame
  ceiling abort sets a per-turn `_playback_aborted_no_first_frame` mark
  (reset at every turn finalize — BUG-032 lesson); `_handle_silent_brain_turn`
  speaks the existing `_BRAIN_TIMEOUT_PHRASE` for such a turn, independent of
  the opt-in clarify toggle (an error report, not an interrogating question —
  the 2026-06-09 mandate stays honoured for plain empty turns).

### Defense (bug class)

When a deterministic routing guard ("X wins over Y") is added, enumerate the
signals MORE explicit than X: a topical match must never outrank the user
literally naming the execution vehicle. And every new turn-abort path must
answer "what does the user HEAR when this fires?" — an abort that can end the
turn with empty output needs its own audible exit, not a fall-through into an
optional courtesy feature. Guards:
`tests/unit/brain/test_skill_routing_guard.py::{test_explicit_spawn_trigger_beats_skill_match,
test_generate_drops_skill_match_on_explicit_spawn_trigger}` +
`tests/unit/speech/test_clarify_question.py::{test_beheaded_turn_speaks_timeout_notice_despite_clarify_off,
test_empty_turn_without_beheading_stays_silent_with_clarify_off}` +
`tests/unit/speech/test_speak_playback_timeout.py::test_no_first_frame_ceiling_abort_marks_beheaded_turn` +
`tests/unit/speech/test_long_dictation_accumulation.py::test_finalized_turn_resets_beheaded_mark`.

## BUG-034: "Jarvis listens forever" #3 — forced-cut carry never finalized when the user stops talking inside the capped window (HIGH, 2026-06-09)

### Symptom

Mid-dictation, the overlay stays in LISTENING with a partial transcript frozen
mid-sentence ("und dann bitte für mich eine umfangreiche …") and Jarvis never <!-- i18n-allow: quoted user utterance -->
answers, no matter how long the user waits. Raising `vad_silence_ms` (1.5 s →
2 s) changes nothing. Third episode of the "listens forever" family — each had
a different root cause (ContinuationBuffer without timer 2026-06-08, stale
playback-watchdog counter BUG-032, now this).

### Root cause

Log evidence `data/jarvis_desktop.log` 2026-06-09 22:24:05–22:24:34: a long
sentence hits the VAD `max_utterance` hard cap (8 s) twice. Each cut makes the
pipeline buffer the fragment per BUG-029's accumulation fix (`_carry_pcm`,
"Forced-cut … carry 500 KB, keep listening") and rely on the VAD to deliver
ANOTHER endpoint to finalize the merged turn. But `SileroEndpointer` fully
resets to IDLE after every endpoint, and a silence endpoint only exists
*inside* an active speech phase. The user finished their sentence right inside
the second capped window (448 ms of silence already accumulated at cut time)
→ no new speech ever started → no endpoint ever fired → the buffered ~16 s of
speech were never transcribed and the session sat in LISTENING until manual
hangup. Raising the silence threshold is ineffective because no silence timer
is running at all in that state. Second hole of the same class: a post-cut
noise blip shorter than `min_speech_ms` ends as `false_start`, which yields
nothing — the carry hangs the same way. (BUG-029 closed the truncation but
left this "producer can no longer emit the finalizing event" gap open.)

### Fix

`jarvis/audio/vad.py`: after a `max_utterance` yield the endpointer arms a
`tail_pending` state; `silence_ms` of idle (non-speaking) silence then yields
an **empty** tail with reason `silence` so the consumer finalizes its carry.
Any natural yield clears the state; a `false_start` leaves it armed (the carry
is still waiting). `jarvis/speech/pipeline.py::_handle_utterance`: an empty
finalize with no carry (e.g. the runaway guard already flushed) skips the
zero-byte STT round-trip and keeps listening.

### Defense (bug class)

A producer/consumer handshake where the consumer buffers a fragment "until the
producer reports the next event" must guarantee the producer can still emit
that event from EVERY reachable state — here the VAD could only end a turn
from SPEAKING while the carry waited in IDLE. When adding a "keep collecting"
path, always add the matching "nothing more came" timeout on the producer
side. Guards:
`tests/unit/audio/test_vad_turn_taking.py::{test_forced_cut_then_pure_silence_flushes_tail_endpoint,
test_forced_cut_then_false_start_blip_still_flushes_tail,
test_forced_cut_then_user_resumes_no_extra_tail_flush}` +
`tests/unit/speech/test_long_dictation_accumulation.py::{test_empty_tail_flush_finalizes_carry,
test_empty_flush_without_carry_skips_stt}`.

---

## BUG-036: Custom wake word permanently dead — wedged ctranslate2 transcription (2026-06-29)

- **Date:** 2026-06-29 · **Scope:** `jarvis/plugins/stt/fwhisper.py`,
  `jarvis/speech/rolling_whisper_wake.py` (custom-phrase `stt_match` wake path)

### Symptom (what the user experiences)

A custom wake word ("Hey Nova", any name on the `stt_match` / `RollingWhisperWake`
path) stops working **entirely**: no orb, no bar, no reaction — for HOURS — no
matter how loud or how often the word is spoken, and **even an app restart does
not clear it**. User report: "I have to shout it ten times", then "I restarted and
it's still dead, it doesn't work at all, with any wake word".

### Root cause (code path)

The local faster-whisper wake model (`FasterWhisperProvider`, ctranslate2 backend)
is shared by TWO callers — the `RollingWhisperWake` poll loop AND the VAD
"listening bubble" probe (`pipeline._probe_stt = self._stt` for a custom phrase).
Both call `transcribe_pcm`, which runs `model.transcribe` in a worker thread
(`asyncio.to_thread`). **ctranslate2's `WhisperModel` is NOT thread-safe for
concurrent `transcribe` on one model object** — two overlapping calls corrupt its
internal state and the call HANGS forever. An `asyncio.to_thread` worker cannot be
cancelled, so:

1. Every later `transcribe_pcm` times out at 8 s, is abandoned, re-polled, and
   hangs again — an infinite `Transkription nach 8.0s abgebrochen (hung STT)` loop.
   The heartbeat `transcribed`/`matched` counters FREEZE while `windows` keeps
   climbing. Live forensic (`data/jarvis_desktop.log`): `transcribed=10 matched=2`
   frozen for ~2 h while `windows` climbed to 20889; zero wakes the whole time.
2. The hung, un-killable threads **exhaust the default thread pool**, which also
   starves the in-app Restart endpoint's own `asyncio.to_thread` — so the Restart
   button hangs "Restarting…" forever. That is why a soft restart did NOT clear it;
   only a HARD process kill (Task Manager → end `pythonw.exe`) + relaunch worked.

The 8 s timeout (added earlier) only BOUNDED each hang (re-poll the SAME wedged
engine) — it never RECOVERED the model, so the wake stayed permanently dead.

### Fix (file:line + test)

1. **Prevent the corruption** — `jarvis/plugins/stt/fwhisper.py`
   `FasterWhisperProvider._transcribe_sync`: a NON-BLOCKING per-instance inference
   lock. A second concurrent call raises `TranscribeBusy` and is skipped (the
   caller re-polls) instead of running `model.transcribe` concurrently or piling
   worker threads up behind a hung call.
2. **Self-heal the wedge** — `FasterWhisperProvider.recover()` drops the (possibly
   hung) model + its lock so the next `transcribe_pcm` rebuilds a FRESH engine; the
   hung thread keeps the OLD object/lock alive (orphaned, never blocks the fresh
   path). `RollingWhisperWake.detect` counts consecutive transcribe failures and
   calls `recover()` after `_WEDGE_RECOVER_AFTER_FAILS = 5` (resets on any success)
   — a wedge now self-heals in seconds, NO restart needed.
3. **Unwedge the Restart button** (parallel fix) — the restart endpoint runs on a
   dedicated thread (`_run_off_pool`) so hung wake threads can no longer starve it.
   A hard kill is still required for an ALREADY-wedged old process.

Tests: `tests/unit/plugins/stt/test_fwhisper_concurrency.py`
(`test_concurrent_transcribe_calls_never_overlap`, `test_busy_lock_raises_transcribe_busy`,
`test_recover_drops_model_and_swaps_in_a_fresh_lock`) +
`tests/unit/speech/test_rolling_whisper_wake.py::test_wake_self_heals_a_wedged_model_via_recover`.

### Defense (bug class) — see AP-24

Any shared single-threaded NATIVE inference engine (ctranslate2 / faster-whisper,
and most ONNX / torch sessions) must NEVER be called concurrently — serialize with
a NON-BLOCKING guard (concurrent call → skip, never overlap). And because a hung
native `to_thread` call cannot be cancelled, a wedge must be RECOVERABLE (rebuild a
fresh object), not merely timeout-bounded. **A timeout that re-polls the SAME
wedged engine is a permanent-dead-state in disguise.** Signal: `transcribed` /
`matched` heartbeat counters frozen while `windows` climbs; "hung STT" every
timeout; a restart that does not help (the un-killable threads can even starve
other thread pools, including the Restart button). Production restart after this
fix: required (a HARD kill for an already-wedged old process).

## BUG-037: Custom wake — "fires on silence" and "stops working" are ONE bug (transcript-content ghost filtering) (HIGH, 2026-07-02)

**Symptom, seen as three separate complaints across one session:**
1. Jarvis spawns with nobody speaking — even in complete silence.
2. After a "ghost fix", the wake word (`Hey Fable`, then `Hey Mythos`) stops
   triggering **entirely**, even when spoken loudly and clearly.
3. It takes ~2 s to spawn after the word is said.

**Root cause — one mechanism behind all three.** The `stt_match` wake path
(`RollingWhisperWake`) is a *transcription* detector: it runs a local Whisper
over a rolling window and matches the transcript against the phrase. To lift
recall of a hard proper-noun wake it primes the decoder with
`initial_prompt=<phrase>` (`jarvis/plugins/stt` `build_wake_whisper`). That
bias is a double-edged sword:
- **On silence / steady noise** the primed decoder HALLUCINATES the phrase
  verbatim (live log: `rms=0.0036 text='Hey Fable'`, below idle hiss) → ghost
  activation (#1).
- **On real speech** the *unprimed* base model cannot spell the word — `Mythos`
  → `Mütos` / `Hey, Mut!`, `Fable` → `Farbe`. That is exactly why the bias <!-- i18n-allow: forensic quotes of the German STT-garble tokens under test -->
  exists.

So any ghost fix that tightens **transcript content** — e.g. requiring the
bias-echo confirm's unbiased second pass to reproduce the wake word — rejects
**every** genuine wake (#2). Live 2026-07-02: an unbiased-corroboration check
suppressed 7/7 real `Hey Mythos` utterances at rms 0.03–0.09 while the user
spoke clearly. "Fires on silence" and "never fires" are the SAME root pulled in
opposite directions; no transcript-content rule separates ghost from wake.

**The only word-agnostic discriminator is raw audio ENERGY.** A genuine wake
carries a speech burst; a silence hallucination sits at the noise floor.

**Fix (AP-27).**
- Silence: a match-site RMS gate (`RollingWhisperWake._match_min_rms = 0.006`) —
  observed ghost cluster ≤ 0.0043, quiet-mic recall contract 0.009, idle hiss
  ~0.0046. Suppresses silence hallucinations word-agnostically, zero cost.
- Recognition: keep the bias-echo confirm **permissive** ("unprimed ear heard
  any real speech → genuine", fail-**open**). Never require the wake word in
  the confirm.
- Latency (#3): each 1.8 s-window transcription is ~0.54 s on base/cpu, and an
  exact-phrase candidate paid a SECOND full transcription (0.56 s) for the
  confirm. SKIP the confirm when the matched window is clearly LOUD
  (`_ECHO_CONFIRM_SKIP_RMS = 0.02`) — a real-volume window is genuine speech,
  silence is already handled by the energy gate. Poll 0.2 → 0.12. Measured:
  loud wake fires ~0.6 s vs ~1.1 s. The Sensitivity slider (a no-op on this
  path — it only fed the openWakeWord threshold) now drives the poll interval
  (`sensitivity_to_poll_interval`).

**Guards.** `tests/unit/speech/test_rolling_whisper_wake_silence_ghost.py`:
`test_loud_wake_fires_even_when_unbiased_pass_garbles_the_hard_word` (recall —
must stay green forever), the silence-suppression tests (energy gate), and the
loud-skip latency test. Signal to recognize the regression next time: a wake
"ghost fix" that makes a hard custom word stop triggering — you tightened
content; revert to the energy gate.

**Endgame.** Truly instant + zero-ghost custom wake needs a trained neural KWS
model (openWakeWord `custom_onnx`), which does not transcribe (AP-25).

## BUG-038: Computer-Use stalls AT its goal — false-miss verification on secondary monitors (HIGH, 2026-07-02)

**Symptom.** A mission opens Chrome (guest), clicks land pixel-perfect on the
window — on whatever monitor it sits — then the agent "gets stuck": it clicks
the address bar repeatedly, never types, and finally gives up with "I tried it
on screen but could not do it". Live run 19:05–19:06: steps 2–4 OK (including
negative-X clicks on the left monitor), steps 5+7 `click(address bar) ->
FAILED: the click produced NO visible change`, mission aborted.

**Root causes — THREE stacked verification defects, none of them pointing.**

1. **Idempotent-click false miss.** The flight-recorder frames show the guest
   new-tab's address bar was focused BEFORE the click (blue ring + caret). A
   click on a control that is already in the desired state changes ZERO
   pixels, so the pre/post effect-check judged it a miss; a failed action
   truncates the batch, so the type behind it never ran; the retry hit the
   same false miss; the mission died at a state that was already correct.
2. **Primary-monitor clipping of the accessibility tree.** Every UI-tree
   source clipped its on-screen overlap filter to the PRIMARY monitor
   (Windows `GetSystemMetrics(0/1)`, macOS `CGMainDisplayID`, Linux a
   hardcoded 1920x1080 — which even clipped a single 4K screen). A window on
   a secondary monitor lost its ENTIRE walked tree: no clickable anchors, no
   field-content hints, no focus evidence. (Verified live: a Chrome window at
   x=-2324 produced a 1-node tree.)
3. **Confident-but-wrong type read-back.** `typed_text_landed` returned a
   hard `False` whenever readable editables lacked the text — even when the
   REAL receiver (start-menu/UWP flyout) was outside the enumerated tree or
   had not committed the value yet (18:00 run: `typed 'Spotify' but it did
   NOT land in any editable field` while the text had landed).

**Fix (all three platforms, one seam each).**
- **Focus-evidence click rescue** (`jarvis/cu/verify.py::
  verify_click_focus_point`, consumed by the engine's click-miss path only —
  zero happy-path latency): before declaring a zero-pixel-change click a
  miss, ask (a) the native point hit-test (UIA `ElementFromPoint` / AX
  element-at-position / AT-SPI `getAccessibleAtPoint`; depth- and
  pruning-independent, new `PointerElement.focused` field) and (b) the walked
  tree, whether the click point sits inside the FOCUSED control. Container
  focus (window/pane/document/web-area) NEVER counts — accepting it would
  rescue genuine in-window misses (`_FOCUS_CONTAINER_ROLES`).
- **Virtual-desktop on-screen filter**
  (`jarvis/platform/monitors.py::virtual_desktop_bounds`, union of all
  monitors per platform: `SM_*VIRTUALSCREEN` under the PMv2 declaration /
  `CGGetActiveDisplayList` / X11 root geometry) used by all three tree
  sources; injected bounds and legacy fallbacks preserved.
- **Honest type verdict**: `typed_text_landed` says `False` only when a
  FOCUSED editable was readable (we provably looked at the receiving
  surface), else `None`; the engine re-verifies once after a short settle
  before failing a type (async surfaces commit late).

**Live proof (Windows, 2026-07-02):** fresh Chrome on the LEFT monitor —
hit-test at the omnibox returns `Edit 'Adress- und Suchleiste' focused=True`,
rescue verdict `True`; mid-page point returns `False` (real misses still
fail). Guards: `tests/unit/cu/test_engine_loop.py` (rescued click lets the
batched type run; re-checked type verdict), `tests/unit/cu/
test_conventions_ledger_verify.py` (container trap, focused-editable
evidence), `tests/unit/vision/test_screen_bounds_virtual_desktop.py`
(secondary-monitor tree survives; per-OS bounds helpers).

**Review hardening (same day, three-agent adversarial/macOS/Linux review):**
(1) the rescue's container deny-list was extended with List/Table/Toolbar/
MenuBar-class roles across all three vocabularies plus the AT-SPI
document-canvas family, and an area cap (`_FOCUS_MAX_AREA_FRACTION`) rejects
focus evidence from region-sized elements — a false rescue would also have
skipped the zoom-refine retry; (2) the Linux rescue was structurally INERT:
the AT-SPI point hit-test queried Application objects (which do not
implement Component — it now descends into their frames) and the AT-SPI
flatten never populated `focused`/`is_password` (now read from
STATE_FOCUSED / ROLE_PASSWORD_TEXT and passed through to the nodes);
(3) the macOS point resolver stringified the `AXWindow` ELEMENT into
`window_title` (an `<AXUIElementRef …>` repr fed to the model) — it now
resolves the window's `AXTitle`; (4) `virtual_desktop_bounds` no longer
flips process DPI awareness from a read-only getter (thread-scoped pin,
restored — AP-9 class); (5) `settle_scale=0` is honored instead of being
`or`-coerced to 1.0. Done-latency fixes from the same complaint: the
done-judge reuses the perception frame when no batch action executed (the
model's claimed evidence IS the current screen — faster AND more faithful),
judge replies stop at the first complete JSON, and a discarded VAD false
start now releases the announcement floor (a 96 ms mic blip had deferred
the spoken "done" by 31 s). Known accepted residuals (documented, all
degrade safely): the Windows hit-test may be flaky across COM apartments
(misses a rescue, never fakes one); a successful type into a value-less
rich editor can still read as "did not land"; legacy Zaphod X11 multihead
under-reports the virtual bounds; Linux foreground-follow costs ~6 xdotool
spawns per perceive frame.

## BUG-039: Explicit desktop request hijacked by a topical skill match — "mir fehlt das passende Werkzeug" despite computer_use being available (HIGH, 2026-07-02)

**Symptom.** Voice session 20:28, turn 1: "Kannst du bitte … ein Terminal
öffnen, Cloud-Code öffnen, … und für mich ein Prompt geben, … kompletten <!-- i18n-allow: forensic quote of the live German voice turn -->
Deep-Dive machen … ob es irgendwelche Bugs gibt" (STT-garbled "Claude Code" →
"Cloud-Code") — an unambiguous desktop request. Jarvis spoke the preamble
"Okay, ich starte cloud-debug." and then refused: "Das kann ich gerade nicht <!-- i18n-allow: forensic quote of the live German refusal -->
ausführen — mir fehlt dafür das passende Werkzeug." No terminal was opened; <!-- i18n-allow: forensic quote of the live German refusal -->
nothing happened.

**Forensics (sessions.db, session 62198a59, turn auto-1).** The turn had the
FULL router tool surface (computer_use present; `_looks_like_pc_control`
matches the transcript, so no hide-gate stripped it). `tool_calls_json` shows
exactly one call: `run-skill` → `ActionExecuted success=true` with
`skill_name='cloud-debug', execution='mission'`, returning the mission
directive "Call the spawn_worker tool NOW …". The model (gemini-3.5-flash,
58k-token context) followed neither the directive nor computer_use and
emitted the system-prompt-dictated capability refusal (manager.py "STRENGE
REGEL" sentence). Turn 2 ("Thank you for watching!") was Whisper silence
hallucination.

**Root cause — a precedence gap, the desktop twin of BUG-035.** The router
prompt's SKILLS-FIRST clause ("check skills BEFORE classifying; when in
doubt, call the skill; a matching skill ALWAYS beats the free answer and
spawn_worker") had only ONE counter-rule: an explicitly named heavy vehicle
("Sub-Agent", "deep dive") wins. There was NO rule for the explicitly named
DESKTOP vehicle ("open an app/terminal, click, type"), and none of the
deterministic tool-hide gates (knowledge-question, signalless-turn,
plugin-tool) ever constrained `run-skill`. So a loose CONTENT match ("find
bugs" ≈ cloud-debug's when_to_use) hijacked a turn whose named VEHICLE was
the desktop — and the utterance's own "Deep-Dive" pointed the heavy-vehicle
rule at spawn_worker, doubly away from computer_use. The deterministic layer
already got this right (`_trigger_names_vehicle` partition: a depth marker
must not override a pc-control signal); the LLM-facing layer had no
equivalent.

**Fix (deterministic gate + prompt precedence, provider-agnostic).**
- `jarvis/brain/manager.py::_hide_run_skill_on_pc_control_turn` — on a turn
  with a deterministic pc-control/open-app signal, `run-skill` leaves the
  tool surface so computer_use stays authoritative. Narrow: fires only when
  `computer_use` is actually present (a CU-less host keeps run-skill);
  stands down when the user literally says "Skill" (an explicit skill
  request is its own vehicle, mirrors AD-S9); the AD-S4 inline trigger-match
  path is untouched; any fault returns the tools unchanged.
- `jarvis/brain/router.py` SYSTEM_PROMPT — three amendments: (1) SKILLS-FIRST
  exception: an explicit SCREEN action beats every skill match unless the
  skill is named; (2) "VEHIKEL SCHLAEGT INHALT": open/click/type requests are
  computer_use even when the CONTENT of what gets typed sounds like heavy
  work or a skill ("öffne ein Terminal, starte Claude Code und gib ihm den <!-- i18n-allow: quote of the German router-prompt example under change -->
  Prompt: mach einen Deep-Dive …" → computer_use(goal=verbatim)); (3) "KEIN <!-- i18n-allow: quote of the German router-prompt rule under change -->
  SKILL-DEAD-END": if a called skill's returned instructions do not fit the
  actual request, ignore them and use the other tools — never answer "mir
  fehlt das passende Werkzeug" while a present tool can do the job. <!-- i18n-allow: quotes of the German router-prompt rules under change -->

**Guards.** `tests/unit/brain/test_routing.py::
test_pc_control_turn_hides_run_skill_keeps_computer_use` (includes the live
incident transcript verbatim), `…_non_pc_control_turn_keeps_run_skill`,
`…_explicit_skill_request_keeps_run_skill_even_on_pc_control_turn`,
`…_run_skill_stays_when_computer_use_absent`, and a fault-tolerance test.

**Class rule (generalize, per the maintainer).** The vehicle the user NAMES
outranks any semantic CONTENT match — for skills, spawns, and future
integrations alike. "Open X and have it do Y" means: operate the desktop
(computer_use); Y is the OTHER program's job, not Jarvis's. This is also the
routing foundation for the planned "Jarvis drives CLI coding agents (Claude
Code / OpenCode)" capability: those turns are Computer-Use tasks today, never
"feature not available".

## BUG-040: Real tool refused as "missing" — the model called the OTHER separator spelling of a registered tool (HIGH, 2026-07-06)

**Symptom.** Voice session 2026-07-05 19:47 (session 10000110, screenshot from
the maintainer): after four successful `cli_gh` calls the turn ended with the
canned capability refusal ("Das kann ich gerade nicht ausfuehren — mir fehlt <!-- i18n-allow: forensic quote of the live German refusal -->
dafuer das passende Werkzeug.") although every tool the model needed was <!-- i18n-allow: forensic quote of the live German refusal -->
present and healthy. Stochastic: the same request sometimes works, sometimes
refuses — the maintainer's long-standing "some tools just can't be called
sometimes" complaint.

**Forensics.** `data/jarvis_desktop.2026-07-05_*.log` 19:49:56:
`tool_use_loop: tool 'run-shell' not in the router tool set`. The registered
name is `run_shell`. The advertised tool surface mixes naming conventions —
hyphen (`wiki-recall`, `run-skill`, `contact-lookup`), underscore
(`run_shell`, `search_web`, `computer_use`) and plain (`click`, `gmail`) — so
the model cross-normalizes and invents the OTHER spelling of a real tool.
`ToolUseLoop` looked the name up with an exact dict `.get()`; a miss fed the
AD-OE6 anti-silence refusal. The provider-side sanitizer
(`_openai_base._sanitize_openai_function_name`) only rewrites INVALID
characters (slash, dot) and keeps a reverse map for those — hyphens are valid,
so separator drift sailed through untranslated.

**Root cause.** Mixed separator conventions across the registered tool
surface + an exact-match-only lookup at the single model-facing resolution
site. (The CU engine and manager pre-fetch paths use hardcoded registered
names — only `ToolUseLoop` resolves model-emitted names.)

**Fix.** `jarvis/brain/tool_use_loop.py::_resolve_tool` — exact match first,
then a canonical (hyphen/underscore/case-insensitive) alias resolves to the
registered tool, but ONLY when unambiguous: two registered tools that collide
on the canonical form stay exact-match-only (never guess between twins).
The ack-emitter tool name is normalized the same way so skip-lists keyed on
registered names keep matching. Unknown names still fire the anti-silence
fallback.

**Guards.** `tests/unit/brain/test_tool_use_loop.py::
test_hyphenated_alias_resolves_to_underscore_tool`,
`…::test_underscore_alias_resolves_to_hyphenated_tool`,
`…::test_ambiguous_alias_is_not_guessed`.

**Class rule.** Any site that resolves a MODEL-emitted identifier against a
registry must tolerate separator/case drift (unambiguously) or normalize the
advertised names to one convention. New tools should prefer underscore names
(`snake_case`) — the majority convention — so the mixed-surface confusion
shrinks over time.

## BUG-041: Total silence after a mid-stream provider error on a tool turn (HIGH, 2026-07-06)

**Symptom.** Voice session 2026-07-05 19:48 (session 10000110, turn 2): the
turn executed 10+ tools (cli_gh, search_web, run_shell), then Jarvis said
NOTHING — no answer, no error, no retry. The log signature is the empty
streamed line: `🤖 Jarvis [de] (streamed): ` with nothing after it.

**Forensics.** `voice_events`: `BrainTurnCompleted {finish_reason: "error",
tokens_in: 223942, text_len: 0}` — OpenRouter returned HTTP 200 but sent
`finish_reason="error"` in the SSE stream (upstream abort on the ~224k-token
prompt the tool loop had accumulated). The providers pass that value through
verbatim (`_openai_base.py` yields the raw finish_reason).

**Root cause — a hole between two correct guards.** The manager's
empty-response guard treats empty text as a soft-fail and tries the next
provider, but is (correctly, since 2026-04-29) skipped when the turn has tool
calls — otherwise fire-and-forget spawns would re-run on every provider. A
turn that executed tools and THEN died mid-stream therefore counted as a
SUCCESS with empty text. Downstream, `_handle_silent_brain_turn` only speaks
for all-failed / desktop-action / beheaded-playback turns; the default branch
returns silently (clarify feature off by default). Net effect: the harder the
turn worked, the more silent its death.

**Fix.** `jarvis/brain/manager.py`: after the guards, a turn with
`finish_reason=="error"` + empty text + executed tool calls returns the
localized honest notice (`_MID_ANSWER_ERROR_PHRASES`, de/en/es via
`_resolve_turn_lang`) instead of empty text. Deliberately NO provider
fall-through: the executed tools would re-run their side effects.

**Guards.** `tests/unit/brain/test_stream_error_after_tools.py` (asserts a
non-empty spoken notice, tool ran exactly once, fallback brain untouched).

**Class rule.** Every path that can END a turn must terminate in either text
or an explicitly-decided silence (AD-OE6). When adding a new finish_reason or
guard interaction, trace where the empty turn lands in
`_handle_silent_brain_turn` — the default branch is SILENT by design, so a
new "empty but worked" shape needs its own honest phrase.

## BUG-042: Every mission fails — usage-capped codex re-picked forever, fallback hardcoded to a dead Claude (HIGH, 2026-07-07)

**Symptom.** Every Jarvis-Agent mission ends ERROR after ~80–95 s with zero
files saved (Outputs view full of red ERROR badges). jarvis_desktop.log
15:50–15:52, mission_019f104e-0001: three identical iterations of
`CodexDirectWorker … codex usage/rate limit hit ("You've hit your usage
limit … try again at Jul 31st") — falling back to the Claude Max OAuth
worker` followed by `ClaudeDirectWorker … claude auth is dead ('Not logged
in · Please run /login')`.

**Forensics.** Two provider outages stacked: the codex ChatGPT plan was
usage-capped until its billing reset (while `codex status` still reported
`connected=True` — a login probe, not a quota probe), AND the Claude Max
OAuth login was dead (nothing refreshes `~/.claude` on this host). A healthy
OpenRouter key was configured the whole time — the Brain chatted over it
happily — but no mission ever reached it.

**Root cause — four AP-22 violations in the worker chain** (each next one
only became visible once the previous was fixed and a live verify mission
— 019f104f, then 019f1050 — walked one family further):
1. *No memory of the codex cap.* `claude_quota_state` existed for the Claude
   direction, but a usage-capped codex was deliberately NOT flagged ("the
   next mission retries codex automatically"), so
   `_cross_family_last_resort_worker` re-picked codex on every mission AND
   every retry iteration — each spawn burning ~28 s to re-prove the cap.
2. *Hardcoded cross-worker fallback.* `CodexDirectWorker`'s cap/auth fallback
   spawned `ClaudeDirectWorker` unconditionally — a fallback chain built from
   a provider NAME, not from viability. With Claude auth dead, that nested
   spawn was a guaranteed "Not logged in" terminal error; the orchestrator's
   per-iteration factory re-consult never got a chance to cross families
   because the factory's picks (codex first) never changed.
3. *Key existence counted as key viability.* The API-key family walk
   (`claude-api → gemini → openrouter → openai`) gated each family on
   `get_provider_secret(prov)` truthiness. The stored anthropic credential
   was a stale `sk-ant-oat` OAuth bearer — a shape the worker env builder
   deliberately DROPS (guaranteed 401) and `_claude_cli_auth_viable` refuses
   — yet its mere existence made the walk pick
   `ApiAgentWorker('claude-api')`, which 401'd ("invalid x-api-key") on
   every retry while the healthy openrouter key sat ONE slot further in the
   SAME loop.
4. *No memory of a failing API family.* With defect 3 fixed, the walk
   reached gemini — whose prepaid credits were DEPLETED (429
   RESOURCE_EXHAUSTED, mission 019f1050). `ApiAgentWorker` recorded nothing
   about the failure, so every retry deterministically re-picked gemini;
   openrouter was never reached. The claude/codex directions had quota
   cooldowns, the API families had none.

**Fix.** `jarvis/codex_quota_state.py` (new, mirror of
`claude_quota_state`): a time-based, self-expiring cooldown armed by a
usage-capped codex worker and cleared by a codex success.
`CodexDirectWorker` arms it and gates its nested Claude fallback on
`_claude_cli_auth_viable()` — when Claude cannot authenticate it surfaces the
honest cap error instead (transient ⇒ the orchestrator retries and the
factory, seeing the cooldown, crosses to the user's API-key family).
`init.py` (`_cross_family_last_resort_worker`, `reachable_worker_families`,
the proactive claude-cooldown→codex route) and `ClaudeDirectWorker`'s two
claude→codex fallbacks all skip codex while the cooldown is armed.
`_api_key_family_viable` (init.py) replaces the bare
`get_provider_secret(prov)` gate everywhere the factory walks API-key
families: for `claude-api` an `sk-ant-oat` bearer never counts, and a
classic key a worker fingerprint-flagged dead this session is skipped until
it changes. `jarvis/api_family_quota_state.py` (new, generic per-provider
mirror of the claude/codex cooldowns): `ApiAgentWorker` arms it when a run
dies on a quota/auth provider error and clears it on success;
`_api_key_family_viable` consults it. FINGERPRINT-bound: saving a new key in
the API-Keys view lifts the block instantly (in-app recovery, §3), while the
same dead key stays skipped until the cooldown self-expires.

**Guards.** `tests/missions/workers/test_codex_quota_state.py`;
`tests/missions/workers/test_codex_auth_fallback.py` (cap arms cooldown +
falls back only to a VIABLE Claude; dead Claude ⇒ one spawn, honest error;
success clears cooldown); `tests/missions/test_worker_cross_family_fallback.py::
test_usage_capped_codex_crosses_to_api_key`;
`tests/missions/test_reachable_worker_families.py::test_usage_capped_codex_is_not_listed`.

**Class rule.** "Connected" is not "can run": a subscription CLI's status
probe checks the LOGIN, never the QUOTA. Any worker that can hit a
usage/rate cap needs a process-local, self-expiring cooldown its factory
consults (mirror pair: `claude_quota_state` / `codex_quota_state`), and a
cross-WORKER fallback must probe the target's viability before spawning it —
never jump to a provider by name (AP-22).

## BUG-043: Realtime bar appears but cannot accept speech while session assembly waits for the shared thread pool (HIGH, 2026-07-12)

**Symptom.** Immediately after a confirmed wake, the desktop Jarvis Bar enters
its listening state, but its microphone meter appears inert and the user gets
no response when speaking naturally without a pause. Waiting several seconds
before speaking can make the same session work. The delay varies with machine
load and therefore looks provider-specific even though it affects every
realtime provider.

**Forensics.** In the live incident, wake confirmation and the listening bar
arrived at 10:40:34. The voice session entered LISTENING at 10:40:35, but the
first log line from `RealtimeVoiceSession` did not appear until 10:40:44. The
user closed the still-unready session before any provider handshake began.
The same session retained an open microphone throughout. A fresh-process
benchmark assembled the wrapper in about 0.6 seconds, ruling out the provider
handshake and normal credential resolution as the eight-to-nine-second gap.

**Root cause.** `_active_realtime_session` queued the synchronous session
wrapper build through `asyncio.to_thread`, which uses the process-wide default
executor. Wake detectors and local STT also use that executor for native
inference and recognizer replenishment. Under load, voice startup queued behind
those workers. Capture-first buffering correctly preserved audio, but no
realtime consumer could accept it until the unrelated shared-pool backlog
cleared. This repeated the executor-starvation half of BUG-036 on a different
voice-critical control path.

**Fix.** `jarvis/speech/pipeline.py::_run_voice_critical_thread` runs realtime
session assembly and desktop barge-in warmup on fresh daemon threads, outside
the shared default executor. The existing ordering remains unchanged:
microphone capture starts first, `VoiceSessionStarted` reveals the bar only
after capture is armed, startup audio stays in `_SessionInputBuffer`, and the
selected provider receives the preserved prefix as soon as its handshake
completes. Provider selection and cross-family fallback remain capability- and
credential-driven.

**Guards.** `tests/unit/speech/test_realtime_mode.py` contains
`test_realtime_builder_survives_exhausted_default_thread_pool`, which fills the
default executor beyond its platform maximum and proves that realtime assembly
still starts and completes. The existing
`test_shared_input_keeps_meter_live_while_realtime_build_is_blocked` and
`test_capture_precedes_listening_signal_and_preserves_startup_audio` retain the
metering, ordering, and zero-loss guarantees.

The classic pipeline does not have a session-builder step before VAD: it
consumes the same capture-first buffer immediately. The companion
`test_pipeline_mode_listens_with_default_thread_pool_exhausted` pins that this
path still accepts the opening audio even when wake/local-STT work occupies
every shared executor worker. Weak-CPU endpointing remains covered separately
by `test_capture_overflow` and `test_vad_realtime_gap_credit`.

**Class rule.** A voice-critical recovery or startup control path must never
depend on the same executor used by un-cancellable native inference. Opening
the microphone early is necessary but not sufficient: every prerequisite for
the eventual audio consumer must also remain schedulable while the default
pool is exhausted.

## BUG-044: Desktop window is blank during a cold or busy startup (HIGH, 2026-07-12)

**Symptom.** First observed as a regression again on 2026-07-12: after the PC
starts and the desktop app opens, the entire client area is a featureless dark
surface. No navigation, loading indicator, assistant name, or error message is
visible. The normal React UI eventually appears after the machine finishes its
startup work, but the delay looks like a crashed JavaScript app and is highly
confusing for users. The screenshot captured at 12:04 shows WebView's native
background rather than the product's existing HTML boot shell.

**Root cause.** The serve-first backend waited for
`FastBootstrap.wait_shell_served(timeout=2.0)` before starting its import and
model-prefetch storm. That event fired when the entry JavaScript bytes left the
local HTTP server, not when the browser painted them. On a cold WebView or a
busy/weak CPU, two seconds could expire before the browser had produced its
first frame. OpenWakeWord, TTS, audio, STT, FastAPI, and route imports then
competed for the GIL/import lock while the window was already visible, leaving
only the native background until that work released the browser/server path.
The race was machine-load dependent, so a fast maintainer machine could miss
it while another computer or operating system exposed it.

**Fix.** The dependency-free boot page now sends `POST
/api/ui/shell-painted` only after two `requestAnimationFrame` callbacks. The
bootstrap records that browser-originated paint acknowledgment, and the desktop
backend waits on it (with a bounded failure backstop) before launching any
heavy prefetch or import. The original asset-served event remains available for
transport diagnostics but no longer gates visible readiness. Both the source
and packaged `dist/index.html` carry the same paint handshake, so installed and
development builds behave identically on Windows, macOS, and Linux.

**Guards.** `tests/unit/ui/web/test_fast_bootstrap.py` proves that serving the
entry JavaScript alone cannot release the paint gate, that the warm-up endpoint
does release it with HTTP 204, and that the build-source boot page acknowledges
only after two animation frames. `tests/unit/ui/test_desktop_backend_start_order.py`
keeps the desktop orchestration compatible with the stronger readiness signal.

**Class rule.** Network delivery is not visual readiness. Any startup path that
shows a WebView before heavy process initialization must wait for a signal from
an actually rendered browser frame, never a server-side response, asset-read,
or fixed sleep. The wait must remain bounded so a broken GUI cannot deadlock a
headless or degraded backend.

## BUG-045: Rejected Vosk wake candidates starve the desktop process (HIGH, 2026-07-13)

**Symptom.** The desktop client became a featureless dark surface and Windows
labelled the native window as not responding. Closing and reopening appeared to
fix it, which made the failure look like a rare WebView startup race. The same
failure mode can affect the GTK and Cocoa pywebview backends because the native
window, local HTTP server, overlay, microphone, and wake detector share one
Python process.

**Forensic timeline.** The affected process started at 08:22:04 and served its
health endpoint at 08:22:19. The screenshot was created at 08:29:32, so this was
not the initial paint race from BUG-044. During the 140 seconds surrounding the
capture, the log recorded 99 full Vosk verification-pass results. The later
watchdogs independently reported a 2.5-second JarvisBar frame stall, 6.6 seconds
without a microphone frame, and a local-listener recovery. Those simultaneous
failures identify process-wide starvation rather than a React-only crash.

**Root cause.** Vosk grammar mode intentionally favours recall and can map room
speech onto the configured wake phrase. A clean rejection reset every streaming
recognizer and immediately admitted another candidate. Each candidate could run
an early visual verify, an authoritative grammar/free-decode pair, sibling-model
rescue, and recognizer-stock replenishment. The existing five-second cooldown
applied only after a successful wake, so a stream of rejected candidates had no
load bound. Detailed rejection messages at INFO level amplified the burst.

A follow-up regression exposed two recall losses in that otherwise necessary
hardening. First, the backpressure window paused stage one as well as the costly
verifier, so rapid human retries were discarded. Second, the structured sound
confirm rejected real one-token merges and close core spellings produced by the
free decoder even after a high-confidence grammar re-score.

**Fix.** A rejected candidate opens a two-second, monotonic backpressure window.
Full verification remains paused until re-arm, but stage one immediately gets
one fresh recognizer set and may latch one retry. Once latched, stage one also
pauses while audio continues advancing the ring; the retained candidate is
verified when the existing deadline expires. This preserves the hard verifier
and recognizer-rebuild bound without turning backpressure into a deaf period for
a user's immediate second call. The first candidate after quiet remains
immediate. The early visual verify completes before the authoritative pair
starts, replenishment begins only after the decision, and rejection details are
DEBUG-level. Session stats expose backpressure windows and bounded chunks. The
mechanism uses only asyncio and monotonic time, so Windows, macOS, Linux desktops,
and headless Linux share the same behaviour.

The sound confirm also accepts a narrowly calibrated class of generic ASR
variants: a known prefix followed by a two-thirds-similar core, or a one-token
merge that independently resembles the configured prefix, core, and complete
phrase. The merge path cannot accept the bare core. Calibration against 100
real positive and 500 real negative speech windows improved recall without a
new negative acceptance; the energy, grammar-confidence, localisation, and
full-phrase requirements remain intact.

The desktop startup path also logs whether the browser-originated shell-paint
acknowledgment arrived or whether the bounded 12-second fallback released heavy
initialization. A future screenshot can therefore distinguish paint failure
from post-start process starvation without inference.

**Guards.** `tests/unit/plugins/wake/test_vosk_kws_provider.py` proves that 200
continuous noisy chunks trigger one verification and only one bounded retry
recognizer set, not a candidate/rebuild storm. It also proves that immediate
retries for one-word and multi-word arbitrary phrases are retained during the
window. Sound-confirm guards cover close spellings, merged phrases, bare-core
rejection, and the production false-wake transcripts. The existing wake recall,
sibling rescue, early visual candidate, cooldown, and AP-24
exclusive-recognizer tests remain green.
`tests/unit/ui/web/test_fast_bootstrap.py` and
`tests/unit/ui/test_desktop_backend_start_order.py` retain the cross-platform
paint-before-heavy-start contract.

**Class rule.** A recall-biased candidate source needs rejection backpressure,
not only a post-success cooldown. The suppressed interval must stop both the
expensive verifier and the candidate/recognizer producer; rate-limiting only the
last stage leaves the same resource storm alive one layer earlier.

## BUG-046: Desktop restart closes Jarvis but never brings it back (HIGH, 2026-07-13)

**Symptom.** `POST /api/settings/restart-app` returned success and closed the
desktop, but the detached relauncher could not reliably acquire the
single-instance lock because the old process remained alive without a window.
The restart appeared to be complete while Jarvis was no longer reachable.

**Root cause.** `run_restart_quit_sequence` described its hard exit as a
watchdog, but armed it only after the synchronous `window.destroy()` call
returned. A cross-thread WebView destroy is one of the operations that can
block during teardown. In that exact failure mode, execution never reached the
hard exit, the old process retained its mutex and port, and fresh launchers
bounced off the supposedly active instance.

**Fix.** The hard exit is now armed in an independent daemon thread before the
GUI destroy call. Normal teardown still wins when it completes quickly; a
blocked Cocoa, GTK, or WebView2 destroy can no longer prevent the old process
from releasing the cross-platform single-instance and port resources.

**Guard.** `tests/unit/ui/test_relauncher.py` blocks `destroy_window` on an
event and proves the independent watchdog still reaches the injected process
exit before the destroy call returns.

**Class rule.** A shutdown watchdog must run independently of every operation
it is intended to bound. Code placed after a possibly blocking call is a
fallback, not a watchdog.

## BUG-047: Realtime promises an action, starts nothing, and never returns (HIGH, 2026-07-13)

**Symptom.** This incident happened in an OpenAI Realtime voice session, not in
the classic STT -> Brain -> TTS pipeline. Turn 4 was transcribed as
`Was steht im Mainim drin?` <!-- i18n-allow: exact German forensic STT output -->
after the prior turn had established the user's Wiki as the subject. Jarvis
then spoke:
`Das kann ich gerne für dich nachschauen. Einen Moment, ich werfe einen Blick in dein Wiki und sage dir gleich, was bei dir drinsteht.` <!-- i18n-allow: exact German forensic runtime output -->
It never supplied the promised result.

**Forensic proof.** The recorder identifies the tier as `realtime` and the
provider as `openai-realtime`. The routing record for the failed turn is
`REALTIME_ROUTING_DECISION path=native_realtime;reasons=none`. The terminal
`VoiceTurnCompleted` record has `tool_calls=[]`. There is no later Wiki result,
tool result, mission update, or completion announcement. The provider response
ended normally, so there was no hidden asynchronous continuation waiting to
finish. Jarvis said that it was about to act while the execution state proved
that no action had started.

**Root cause.** Three independent gaps aligned:

1. The deterministic Realtime planner classified only the current transcript.
   ASR had garbled the domain/ownership wording into `Mainim`; the surviving
   word `drin` was an elliptical reference to the preceding Wiki turn. Without
   bounded conversation context, the planner saw neither private data nor a
   local capability and left the response on the native provider path.
2. The former delegate-mode instruction said that the orchestrator handled Wiki
   and private-memory turns automatically and told the provider not to call its
   action function for them. That assumption was false on a planner miss: the
   planner did not dispatch, while the provider had been told not to dispatch.
3. Prompt rules were the only final protection. No runtime invariant compared a
   model's future-action wording with the turn's actual tool/delegate state.
   Once the provider violated the prompt, speculative speech could become the
   terminal user-visible answer.

**Fix.** The repair is deliberately layered because provider compliance alone
cannot be a correctness boundary:

- The shared `turn_planner` now accepts a bounded context window. An explicit
  follow-up reference such as `drin`, `there`, or `what does it say` inherits a
  prior local/private/connected/current evidence domain and routes through the
  orchestrator. Generic unrelated questions, including `What time is it?`, do
  not inherit old Wiki context.
- Delegate-mode instructions now make `jarvis_action` the provider fallback for
  the user's Wiki, private memory, apps, settings, files, connectors, and other
  personal state. They prohibit ending on an action announcement without the
  function call that starts it.
- `RealtimeVoiceSession` maintains a per-turn output probe and per-turn execution
  evidence. If a native provider emits a high-confidence deferred-action claim
  with no tool call, delegate result, or trusted external event, its speculative
  text/audio is drained before delivery. Delegate mode interrupts that response,
  starts the real orchestrator turn, waits for the provider boundary, and injects
  only the trusted result. Direct-tool mode, where no supervisor recovery exists,
  fails closed with a localized statement that no action was started.
- The classic `BrainManager` applies the same execution-state backstop after
  leaked-tool recovery and mandated-evidence enforcement. Potential action turns
  buffer provider chunks until the authoritative final response, preventing TTS
  from speaking a promise that the final guard later replaces.
- The packaged persona no longer asks for a pre-tool "let me check" line. It
  requires the tool call first and treats only selected/running execution as a
  valid basis for interim speech.
- The opt-in speculative Flash-Brain preamble now drops deferred-action claims.
  This is defense in depth: the preamble remains off by default after the earlier
  forensic sample found that it was the only spoken output on 22 percent of its
  turns.

The detector is regex-only, runs off the voice hot path without another model
call, and carries German, English, and Spanish runtime wording. Its fallback
uses the already resolved turn language instead of re-detecting a de/en subset.

**Related output audit.** Every user-facing surface that can plausibly say
"working on it" or otherwise imply future work was checked against actual
execution state:

| Surface | Evidence behind an interim/action claim | Result |
|---|---|---|
| Native Realtime (delegate mode) | Provider function call, deterministic delegate task, or trusted injected result | Recovered at runtime if the provider promises without evidence |
| Native Realtime (direct tools) | A recorded direct tool execution | Fails closed honestly when no execution exists |
| Classic Brain/provider response | Per-turn executed-tool set or successfully recovered leaked tool call | Final response is replaced; streaming action turns are buffered |
| Speculative Flash-Brain preamble | None by design | Off by default and now suppresses action promises when opted in |
| Grounded tool acknowledgement | Emitted only after the router selected a concrete tool call | Kept; the subsequent call still runs through `ToolExecutor` |
| Deterministic local actions | `ToolExecutor` result | Kept; success and failure both receive an immediate readback |
| Computer Use | Background task is armed before progress speech | Kept; completion/failure publishes a terminal announcement |
| Wiki background writes | Tracked ingest task exists before the saving acknowledgement | Kept; every terminal branch publishes success, failure, or timeout |
| Jarvis-Agent missions | Mission task and signed mission state exist before spawn speech | Kept; live heartbeats and terminal mission events ground later updates |
| `dispatch_with_review` | Inputs are validated and the review pipeline exists before its holding phrase | Kept; the phrase cannot be emitted for a rejected/no-op request |
| Realtime out-of-band updates | Typed event from a running action/mission | Kept and explicitly excluded from model-promise detection |
| Canned clarify/error/provider-down/timeout output | Reports current state; does not claim future execution | No action-promise path found |

**Guards.** `tests/unit/brain/test_turn_planner.py` pins the exact garbled
follow-up plus unrelated-question negatives. `tests/unit/realtime/test_session.py`
pins deterministic contextual delegation, provider-promise recovery with no
speculative audio leak, and the honest direct-mode fallback.
`tests/unit/brain/test_action_honesty.py` covers multilingual detection,
negative explanatory/result text, the generic Brain final guard, and the
packaged persona contract. `tests/unit/brain/test_manager_streaming.py` proves
that a contextual action turn cannot leak pre-final chunks, and
`tests/unit/brain/test_ack_brain/test_run_stream.py` pins speculative-preamble
suppression.

**Class rule.** Future tense is not execution state. Any user-facing sentence
that says Jarvis will check, open, save, start, research, or report back must be
backed in the same turn by a tool call, a tracked background task, or a typed
event from work that is already running. If no such evidence exists, Jarvis
must start the real action or say immediately that it did not start one; it may
never end the turn on a promise of an invisible continuation.

## BUG-048: Realtime delegation misses turns the planner cannot see (HIGH, 2026-07-13)

**Symptom.** In realtime delegate mode, three everyday turn shapes silently
stayed on the native provider path, so whether the user's action ever reached
the Jarvis action system (ToolExecutor, MCP/plugin/CLI tools, Wiki, missions)
depended entirely on the realtime model voluntarily calling `jarvis_action` —
prompt compliance as the correctness boundary, the exact BUG-047 class:

1. **The answer to a pending two-turn voice confirmation.** A delegated
   router-brain turn arms an ask-tier confirmation (an MCP/plugin write,
   `call-contact`, a dangerous app command) and asks the user. The bare
   "yes"/"no" answer matches no planner action vocabulary, so nothing forced
   it back to the brain's `_resume_voice_confirm` — a confirmed action could
   simply never execute.
2. **Every German umlaut verb.** `turn_planner._normalize` stripped combining
   marks (NFKD), yielding one ascii form, while the entire German vocabulary
   is written in the transliterated digraph form ("loesch", "aender",
   "fuehr", "pruef", "koennte", "kuerzlich", "faehigkeit"). The two forms
   never meet: no German utterance containing an umlaut verb has ever matched
   the action/lookup/instructional vocabularies.
3. **The short answer to a delegated clarify question.** When the delegated
   brain reply ended in a question ("Which file do you mean?"), the user's
   elliptical answer ("the readme one") carried no planner-visible category
   and stayed native.

**Fix.** (a) `RealtimeVoiceSession` probes `brain.has_pending_voice_confirm()`
during final-transcript handling and forces the deterministic delegate;
(b) `_normalize` transliterates umlauts to their digraph form before the NFKD
strip, and the action-verb fallback gained the missing everyday assistant
verbs in all three languages (switch/turn/play/remember/remind/schedule;
wechseln/schalten/stellen/spielen/merken/notieren/legen; recordar/anotar/
poner/reproducir/apagar/agendar) with stem guards for frequent non-action
words; (c) the session remembers a delegate reply that ended in a question
and pulls the next short (<= 6 token) final transcript back to the
orchestrator, while a longer follow-up stays native as a topic change.

**Guards.** `tests/unit/realtime/test_session.py` (pending-confirm
delegation, clarify-answer delegation, short/long negative cases),
`tests/unit/brain/test_turn_planner.py` (umlaut verbs, new action verbs,
guarded non-action words).

**Class rule.** The deterministic planner is the correctness boundary for
realtime delegation; the provider prompt is only an optimization. Whenever a
multi-turn state machine (confirmation, clarify question) leaves the brain
waiting for the user's next utterance, the session must route that utterance
back deterministically — never rely on the model to do it. And every
vocabulary that matches normalized text must be written in the SAME
normalized form that `_normalize` actually produces; a mismatch is silent
and total, not partial.

## BUG-049: Classic TTS voice speaks into a live realtime call (HIGH, 2026-07-13)

**Symptom.** In a realtime voice session (17:39, delegate mode), the user asked
for a full listing of their Wiki. While the delegated router-brain turn was
thinking (~31 s), Jarvis spoke the interim line "I am searching your wiki" in
the CLASSIC pipeline TTS voice — a sudden second voice/engine inside the live
call. The user read it as "Jarvis switched from realtime to the pipeline",
although the final answer was in fact delivered by the realtime model.

**Root cause.** The ack brain published `AnnouncementRequested(kind=preamble)`
mid-turn. `RealtimeVoiceSession.deliver_announcement` correctly refuses a busy
session (text input would interrupt the provider's response lifecycle), and
`_on_announcement` treated every refusal as "use classic TTS" — it never
distinguished a BUSY live call from a DEAD one.

The 19:59 recurrence exposed a second state bug. OpenAI rejected a raced
`response.create` with `conversation_already_has_active_response`; the receive
pump continued and the realtime call remained usable, but the wrapper set its
sticky `_failed` flag. `is_active` then returned false, so a later delegated
`brain.router.ack` preamble crossed into classic TTS even though the accepted
realtime socket was still carrying the conversation.

**Fix.** Voice ownership now follows the accepted realtime handle, never a
provider-health flag. Until that lifecycle fully unwinds, ephemeral
preamble/progress lines are dropped and owed readbacks are parked; classic TTS
cannot speak into the call. Provider events carry explicit recoverability, so
the OpenAI active-response collision no longer poisons a usable session, while
terminal events end the receive pump. The OpenAI adapter also serializes every
local `response.create` against `response.done`, preventing the collision.
At the time of this fix, realtime tool mode defaulted to `direct`: the live
model called the supervisor safety gateway with its own realtime credential
and did not invoke the classic Brain/TTS pipeline. BUG-052 later restored the
compact `delegate` default after measuring the full dynamic catalog at roughly
26,000 input tokens per response; `direct` remains an explicit diagnostic
opt-in.

**Guards.** `tests/unit/speech/test_realtime_announcement_bridge.py` and
`test_announcement_bridge.py` cover voice ownership; realtime pipeline
isolation, autonomous defaults, error recoverability, and OpenAI response
serialization are covered under `tests/unit/realtime/`.

**Class rule.** A voice surface has ONE voice at a time. Ordinary fallback from
the live realtime voice to classic TTS must be gated on the accepted call
handle being GONE, never merely busy or unhealthy — those states mean wait,
drop, or end the call, not switch voices mid-call. The narrow terminal-turn
exception is BUG-052's `error_spoken` path: after the current provider response
has completed or been cancelled, realtime playback is stopped, and a grounded
or curated result still has zero audio, the surface may render that fixed text
through classic TTS. It may not run another model/tool or overlap provider
audio.

## BUG-050: run_shell on Windows echoes quoted commands instead of executing them (HIGH, 2026-07-13)

**Symptom.** In a realtime session (18:15) the delegated brain turn burned all
15 tool iterations trying to list the user's wiki files and died with
`IterationBudget exhausted` — spoken outcome: a generic failure. The user
experienced it as an endless thinking loop.

**Root cause.** `run_shell` tokenized with `shlex.split(posix=False)` on
Windows, which KEEPS surrounding quotes inside tokens. Three failure shapes,
two of them silent:

1. `powershell -Command "Get-ChildItem -Name"` → PowerShell received a string
   LITERAL and echoed it back — **exit 0, stdout = the command itself**. The
   tool reported success with garbage output, so the model saw no error to
   correct and retried variations until the budget died.
2. `cmd.exe /c "dir /s /b *.md"` → the kept quotes corrupted the payload
   ("Der Befehl ist entweder falsch geschrieben...").  <!-- i18n-allow: quoted German OS error under test -->
3. `dir /s /b` → WinError 2: cmd builtins are not programs and cannot be
   exec'd directly.

**Fix.** On Windows the ORIGINAL command string goes to `cmd.exe` via
`create_subprocess_shell` — cmd parses its own quoting and provides the
builtins. POSIX keeps the historical `shlex.split` + exec contract. The
whitelist/blacklist safety matching runs on the full command string in
`ToolExecutor` BEFORE execution and is unchanged.

**Guards.** `tests/unit/plugins/tool/test_run_shell.py` — quoted PowerShell
payload is executed (not echoed), cmd builtin `dir` works, quoted cmd payload
works, POSIX path pinned.

**Class rule.** A tool that reports success with an output that is merely its
own input is worse than a failing tool: the model gets no error signal and
loops. When wrapping OS process creation, verify on EVERY platform that a
quoted argument arrives as an argument, not as a literal.

## BUG-051: Realtime interim ack speaks only after the router has already decided (HIGH, 2026-07-13)

**Symptom.** Realtime session 18:36 ("Wer ist aktueller Export-Weltmeister?"): <!-- i18n-allow: quoted German user utterance under analysis -->
the user hears NOTHING for the whole wait and hangs up 17.8 s after finishing
the question. The saved transcript shows an interim line ("Ich durchsuche
gerade die aktuellen Daten …") — but its AUDIO only played ~2.7 s AFTER the <!-- i18n-allow: quoted German interim ack under analysis -->
hang-up, via classic TTS into an ended session; live there was only dead air.
A final answer never existed: the web search started ~1 s before the hang-up.

**Timeline** (delegate dispatch 18:37:00.5 = t+0, `data/jarvis_desktop.log`):

- t+0.0 — deterministic delegate dispatches the router-brain turn (OpenRouter).
- t+3.1 — the realtime model tries to speak; correctly deferred (pending-action
  honesty guard — it must not invent an outcome).
- t+10.3 — OpenRouter response HEADERS arrive: ~10 s TTFB for the router hop.
- t+15.9 — first model round fully streamed → tool calls visible →
  `ack_emitter` fires → the readback LLM call starts.
- t+16.6 — interim-ack TEXT ready (readback ~0.7 s); announcement published;
  web search actually starts (Mojeek + DuckDuckGo).
- t+17.8 — user hangs up (hotkey).
- t+20.5 — the ack plays via classic TTS, after the session already ended
  (consistent with the BUG-049 busy-gate: busy live call → no classic voice;
  session gone → classic TTS speaks the parked line).

**Root cause.** The interim ack is structurally serialized BEHIND the very
decision it is meant to cover. `ToolUseLoop.run` awaits `ack_emitter` only on
"the first iteration that has tool calls scheduled" — i.e. after the FULL
first router model round has streamed to completion (~15.8 s here,
`jarvis/brain/tool_use_loop.py`). The ack text is then ANOTHER LLM call
(`ReadbackComposer.compose`, `jarvis/voice/contextual_readback.py`).
Meanwhile the pending-action guard correctly mutes the realtime model, so
nothing bridges the silence: three stacked latencies, and their sum is
user-audible dead air.

**Fix.** A dispatch-time dead-air bridge in the deterministic delegate path
(`jarvis/realtime/session.py`). When the delegated action is still pending
`_DELEGATE_BRIDGE_DELAY_S` (now 6 s) after dispatch, `_run_delegate_bridge`
asks the LIVE model to voice one fixed localized progress template — one voice
per call (BUG-049). The complete bridge transcript and audio stay withheld
until the transcript deterministically matches that exact template. A ready
trusted result preempts an active bridge so it never waits behind interim
speech. Necessity-gated: a result faster than the delay (`result_ready`) skips
the bridge entirely. The bridge task is deliberately NOT a tracked delegate
task, so a sleeping timer can never hold a turn open, defer a VAD edge, or
refuse an announcement. Secondary lever (still open): the ~10 s OpenRouter
TTFB on the router hop itself.

**Guards.** `tests/unit/realtime/test_session.py` —
`test_slow_deterministic_delegate_speaks_a_bridge_line` (bridge precedes the
result, output flows, the turn survives the bridge's own turn_complete and
the result is still delivered live, not as a late follow-up) and
`test_fast_deterministic_delegate_needs_no_bridge_line` (fast results stay
chatter-free).

**Class rule.** An interim ack that waits for the completion of the decision
it is meant to cover is not an ack. A realtime bridge must be late enough not
to serialize an ordinary result behind a second provider response, and it must
be a bystander: a helper task that merely waits must never feed the liveness
signals (turn hold, endpoint protection) that real work feeds.

## BUG-052: Realtime records an empty response as success and returns to listening (HIGH, 2026-07-14)

**Symptom.** A healthy realtime voice session accepted and transcribed five
user turns, but three substantive turns returned to LISTENING after roughly
half a second with no assistant transcript and no audio. The run inspector
reported every turn and the whole session as successful. The silent turns had
zero speak time, no tool calls, no error event, and no `AudioOutFirst` or
`ResponseGenerated`; normal turns in the same call spoke through the same
output device. This was response-generation silence, not microphone, TTS
device, watchdog, or long-thinking latency.

**Root cause.** `response.done` is a transport lifecycle boundary, not proof of
a completed answer. The OpenAI Realtime API emits it for `completed`, `failed`,
`incomplete`, and `cancelled` responses, but the adapter discarded
`response.status` and `status_details` and always emitted `turn_complete`.
`RealtimeVoiceSession` then persisted an empty `VoiceTurnCompleted` as success
and returned the surface to LISTENING without checking for user-visible output.
A nominally `completed` response can also contain no output, so adapter status
checking alone cannot enforce the product contract. Direct tool mode made the
gap much easier to hit because ordinary turns now use the native response path,
but the missing invariant predated that default.

**Fix.** The defense is layered and provider-neutral:

1. The OpenAI adapter now reports failed/incomplete terminal statuses as a
   recoverable provider error before preserving the `turn_complete` boundary.
   Expected barge-in cancellation remains quiet.
2. A content-bearing turn with no text, audio, or tool evidence cannot close.
   It dispatches exactly once through the normal Brain chain, so configured
   key-aware, cross-family fallback remains available.
3. A direct-tool turn never replays the user's request. It retains the existing
   tool result and asks the provider to render that result with tools disabled;
   this prevents duplicate side effects.
4. A transcript with zero PCM, a grounded recovery result with zero PCM, or an
   exhausted Brain fallback is rendered through the surface's classic TTS path
   after realtime playback stops. No model or tool is called again, and the
   persisted turn contains the text the user actually heard.
5. The default tool mode is again compact `delegate`: the realtime provider
   sees only `jarvis_action` plus `end_call`, while the router Brain owns the
   large dynamic catalog. A live reproduction showed 134 direct declarations,
   about 26,000 requested input tokens per response, and a 40,000 TPM limit —
   enough for roughly one turn before rate limiting. `direct` remains available
   only as an explicit operator choice.

**Guards.** `tests/unit/realtime/test_openai_realtime.py` covers failed
`response.done` status propagation. `tests/unit/realtime/test_session.py`
covers no-output conversation recovery, a second empty response, and direct
tool-result recovery without repeating the action.
`tests/unit/speech/test_realtime_mode.py` proves that `error_spoken` reaches
real TTS audio and restores LISTENING only after playback.

**Class rule.** A successful voice turn requires user-visible output evidence:
spoken audio, an explicit lifecycle action such as hang-up, or a safely retained
result that is rendered by a working fallback. `turn_complete`, provider
health, and a zero-error run row are never sufficient evidence by themselves.
Never recover a silent tool turn by replaying the original request; recover
from the retained result so a side effect can occur at most once.

## BUG-053: A normal realtime barge-in ends the call when cancellation loses a response-boundary race (HIGH, LARGELY FIXED 2026-07-14 — correction 3 open)

> **Status update (2026-07-14, afternoon).** Corrections 1 and 2 are
> implemented (see BUG-056 below, which is the same defect fired through the
> scrub-cancel path): `response_cancel_not_active` is now a recoverable
> provider event, and `interrupt()` skips the wire cancel when no response
> lifecycle is active. Correction 3 (preserve and forward the accepted
> barge-in audio into the next turn) remains open.

**Symptom.** During a healthy desktop realtime session, the user began a
follow-up about NotebookLM and its MCP server while the preceding answer was
finishing. Local voice activity detection accepted the interruption, but the
follow-up never became a recorded turn. The call returned to idle roughly 2.7
seconds later. The exported transcript consequently ends after the preceding
two turns and contains none of the follow-up. The user did not issue a hang-up
command and did not press the overlay close control or the global hang-up
hotkey.

**Evidence.** The 09:04 session has a complete causal chain in the desktop log
and `data/sessions.db`:

- At 09:05:43.729, the desktop path logged `Realtime desktop barge-in confirmed
  by local CPU VAD`.
- At that same response boundary, the preceding turn was finalized with
  `reason=barge_in`; no `request_hangup`, voice-pattern match, hotkey event, or
  client-stop event occurred.
- At 09:05:44.229, the provider emitted
  `response_cancel_not_active: Cancellation failed: no active response found`.
- The event was recorded as a non-recoverable `RealtimeProviderError`, and at
  09:05:46.444 the session ended with `hangup_reason=error` and two saved turns.
- There is no third `VoiceTurnStarted` or final `TranscriptionUpdate`. The exact
  follow-up wording cannot be reconstructed from the session store because the
  receive pump stopped before the provider delivered its final input
  transcription.

This rules out the hang-up phrase matcher and both user controls. It also rules
out the 30-second idle timeout: speech was detected immediately, and the stored
reason is `error`, not `idle_timeout`.

**Root cause.** Barge-in and provider response completion run concurrently.
The desktop microphone task saw local output as active and called
`RealtimeVoiceSession._barge_in()`, which called the OpenAI adapter's
`interrupt()`. The provider had already crossed its `response.done` boundary,
so `response.cancel` correctly reported that there was no active response left
to cancel. That is an idempotent no-op race, not a broken realtime connection.

The adapter currently recognizes only
`conversation_already_has_active_response` as a recoverable runtime error.
It therefore labels `response_cancel_not_active` terminal. The session pump
sets `_failed`, publishes `provider_error`, and exits. The desktop pipeline
then correctly refuses to replay already committed audio through classic voice
because doing so could duplicate a tool action; that safety boundary converts
the upstream misclassification into `hangup_reason=error`. The unsafe-replay
guard is not the defect. The cancellation error classification is.

**Required correction.** This incident is diagnosed and recorded; the runtime
repair has not yet been applied. The fix needs all of the following:

1. Treat `response_cancel_not_active` as an expected benign cancellation race
   (or at minimum a recoverable provider event), never as a terminal session
   error.
2. Avoid sending `response.cancel` when the adapter already knows that no
   response lifecycle is active. The benign error handling remains necessary
   because the provider can still finish between a local state check and the
   wire operation.
3. Preserve and forward the accepted barge-in audio so its final transcript
   can start the next turn after the stale cancellation acknowledgement.
4. Add an adapter test for the exact error code and an end-to-end realtime test
   that interleaves `response.done`, local `barge_in`, the benign cancellation
   error, and a second input transcript. The session must remain active and
   persist the second turn without a `provider_error` message.

**Class rule.** Interrupting an operation that completed concurrently is
successful idempotence, not a fatal error. Every duplex provider can race a
barge-in edge against its response-complete edge, so "nothing remained to
cancel" must keep the call alive. A session should end only for an explicit
user lifecycle action, configured inactivity, shutdown, or a genuinely
unrecoverable failure; recoverable control-plane races must never masquerade as
hang-up.

## BUG-054: The realtime dead-air bridge invents a connected-tool result before the tool runs (HIGH, FIXED 2026-07-14)

**Symptom.** In the same 09:04 session as BUG-053, the user asked Jarvis to list
their notebooks. Jarvis spoke five plausible-looking notebook names as though
they came from the user's account. They did not. The background mission had not
yet called the NotebookLM MCP server, and its eventual grounded outcome was
that the NotebookLM login had expired and the notebooks could not be listed.

The false list was not merely transient speech. It became the saved assistant
text for turn two, appeared in the exported transcript, and was subsequently
journaled as a candidate personal fact about the user. A progress sentence
therefore crossed three authority boundaries: spoken answer, session history,
and durable memory.

**Evidence.** Desktop, session, mission, and Wiki-journal timestamps agree:

- At 09:05:24, deterministic delegation dispatched the notebook-list request
  to a background mission.
- At 09:05:29.233, the realtime session logged that its delegate bridge had
  requested an interim line because the action was still running.
- The bridge response then spoke the five-name list and was finalized at
  09:05:43.729. The worker's latest evidence at that moment was only a local
  filename glob and repository text search.
- The worker did not call the NotebookLM notebook-list tool until
  09:06:05.216, more than 21 seconds after the invented list was finalized.
  The call reported expired authentication. A refresh attempt and second list
  call also failed.
- The worker produced the honest blocked outcome at 09:06:26 and the mission
  later ended failed. No successful MCP result ever supported the spoken list.
- At 09:05:48.259, the memory candidate journal derived a fact asserting that
  the user owned the five invented notebooks. That entry came from the
  ungrounded bridge output, not connected-tool evidence.

**Root cause.** `_delegate_bridge_prompt()` tells the live model to produce one
short progress sentence and explicitly forbids outcomes or answer content.
`_run_delegate_bridge()` then sets `bridge_delivery_started`, opens the output
gate, and trusts whatever transcript/audio the model emits. There is no
deterministic bridge-output validator. The general action-honesty guard detects
unsupported future-tense promises, but a fabricated factual answer is not a
promise, so it passes. The bridge test covers a compliant "still checking"
sentence only; it never makes the provider violate the instruction.

This is a direct recurrence of BUG-047's class rule in a newly exempted path:
prompt compliance became a correctness boundary again. Starting a real mission
proves that work exists, but it does not prove any result that the interim model
chooses to state.

**Fix.** The bridge is now structurally incapable of supplying answer content:

1. Bridge audio stays withheld until the full transcript exactly matches a
   fixed localized status template rendered in the resolved turn language. A
   non-conforming bridge is dropped without affecting the running action.
2. Never publish bridge text as `ResponseGenerated` or ordinary assistant turn
   content. It is `SpeechSpoken(progress)` only and must be excluded from fact
   extraction, personal-memory journaling, and answer history.
3. Keep the real answer gate closed until the delegated result is complete and
   its trusted payload is being delivered. Mission-start evidence may authorize
   only a progress status, never a factual result.
4. A hostile-provider regression test makes the bridge ignore its prompt and
   emit a plausible list. It asserts zero leaked PCM, no authoritative response,
   a still-live delegated action, and later delivery of only the grounded tool
   outcome. A separate regression proves that a ready result interrupts an
   active bridge instead of queueing behind it.

The bridge threshold moved from 2 s to 6 s for realtime only. The classic
speech pipeline keeps its existing grounded tool acknowledgement; realtime
delegate calls explicitly suppress that duplicate manager-level event.

**Class rule.** "Work started" and "result known" are different evidence
levels. An interim surface may report only the former, and its data must never
enter an answer or memory channel. Any model-generated bridge that is allowed
through solely because its prompt said "do not invent a result" is an
untrusted answer generator, not a progress indicator.

## BUG-055: Delegated wiki question answered from the schema contract + poisoned memory, 66 s deep (HIGH, FIXED 2026-07-14)

**Symptom.** Voice session 2026-07-14 09:28: "Kannst du mir mal bitte dabei
helfen, zu schauen, was genau alles in meinem Wiki-System steht?" <!-- i18n-allow: quoted German user utterance under forensic analysis -->
took 66 seconds and answered that the wiki holds USER.md, SOUL.md, and a
people folder with profiles for Sam, ExampleRelative, and the user's mother. The real
vault holds none of those files (actual contents: the log/memory/schema
core pages plus a few entity and project pages — none matching a single
spoken name). Every named file in the spoken answer was invented.

**Evidence (desktop log, 09:29:18–09:30:25).** The deterministic delegate
dispatched a local-evidence turn at 09:29:18.5. The router brain then ran
~14 sequential OpenRouter rounds; one round alone waited 32 s for its
response (09:29:33 → 09:30:05). Tool trace: wiki-recall (3 hits) →
wiki-page-read schema.md (served, 8945 bytes) → wiki-page-read index.md
(NOT FOUND) → wiki-recall → … → wiki-page-read SOUL.md (NOT FOUND) → answer.
Speech began 09:30:25 — 66 s after dispatch.

**Root causes (four, compounding).**

1. **No listing tool.** "What is in my wiki" is a LISTING question; the tool
   surface offered only search (wiki-recall) and single-page read
   (wiki-page-read), so no grounded answer was reachable. The model probed
   blindly (each miss = one more LLM round) and then guessed.
2. **The schema contract masqueraded as content.** schema.md (``type: meta``,
   the vault's editing contract) documents an EXAMPLE layout. Served
   verbatim by wiki-page-read, the model presented that example as the
   actual vault — while holding two "not found" results contradicting it.
3. **Poisoned memory closed the loop.** entities/alex.md already carried
   consolidated facts asserting SOUL.md/USER.md/people-profiles exist —
   journaled from EARLIER hallucinated answers (same class as BUG-054: the
   09:05 session's five invented notebook names were consolidated as
   candidate 381). Context injection fed the poison back, the new wrong
   answer was journaled again at 09:31:18 (candidates 382/383, consolidated)
   — a self-reinforcing hallucination loop across turns.
4. **No wall-clock bound.** The tool-use loop's only bound was the 15-round
   iteration budget; rounds are not seconds, and one slow provider round ate
   32 s alone. A voice user is gone long before round 14.

**Fix (2026-07-14).**

- New router tool ``wiki-list`` (ADR-0011 amendment): deterministic ground-
  truth listing (path, size, first heading) in ONE round; ``type: meta``
  pages flagged as system files; overview questions no longer probe or guess.
- ``wiki-page-read`` prepends a deterministic provenance warning to
  ``type: meta`` pages ("contract, not content — use wiki-list").
- ``ToolUseLoop`` gains ``deadline_s``: on expiry it forces exactly ONE
  final tool-less round with an answer-now directive (never silence, never
  more churn). Delegated realtime turns run with max_turns=6 +
  deadline 20 s (``_DELEGATE_MAX_TURNS`` / ``_DELEGATE_DEADLINE_S``).
- Data purge: the three poisoned fact lines removed from entities/alex.md
  (invented notebooks, SOUL.md/USER.md, people profiles).

**Memory-ingest follow-up (fixed 2026-07-15).** Realtime capture is now a
grounded two-stage pipeline. Stage 1 may use assistant replies only to resolve
references, but every candidate must cite an exact user turn and persists a
bounded, secret-redacted USER-only evidence excerpt. Stage 2 sees that excerpt
next to the candidate and must NOOP unsupported or assistant-only claims;
captured legacy rows without user evidence are rejected and can be recreated
from persisted transcripts through the policy-v3 backfill. Landed pages receive
a deterministic session/turn source marker. Follow-up live backfill hardened
three semantic gaps: one-off questions cannot manufacture a lasting user
interest, new numeric claims must occur in evidence/current page, and an
explicit remember/note/add-to-wiki request cannot silently become NOOP unless
the fact is already present unchanged or lacks user evidence. Guards:
``tests/unit/memory/wiki/test_extractor.py``,
``tests/unit/memory/wiki/test_consolidator.py``,
``tests/integration/memory/wiki/test_realtime_to_vault_e2e.py``.

**Class rule.** A question about what EXISTS needs a deterministic
enumeration tool; search + single-read cannot answer it groundedly, and a
schema/contract page served without provenance becomes hallucination fuel.
Voice-facing agentic loops need a TIME bound, not just a round bound.

## BUG-056: First macOS boot aborts natively — pystray NSStatusItem created off the main thread (HIGH, FIXED 2026-07-14)

**Symptom.** Fresh install on a real Mac (install.sh → Python 3.14 venv →
first launch): the app dies during boot with the macOS crash dialog
"Python quit unexpectedly". Terminal shows
``objc.error: NSInternalInconsistencyException — NSWindow drag regions
should only be invalidated on the Main Thread!`` followed by a native
AppKit assertion (``NSViewSetCurrentlyBuildingLayerTreeForDisplay``,
NSView.m:13412) and a hard process abort. "Reopen"/"Ignore" do nothing —
every relaunch dies at the same point. First-boot is bricked on EVERY Mac,
deterministically; not machine-specific.

**Root cause.** ``JarvisTray.start()`` spawns ``_run()`` on a worker thread
(``jarvis-tray``); ``_run()`` constructs ``pystray.Icon`` whose darwin
backend creates an ``NSStatusItem`` in ``Icon.__init__``
(``pystray/_darwin.py:60``). AppKit is main-thread-only: the first objc
exception IS raised into Python (and would be caught by ``_run``'s
try/except), but the half-built status item then trips a **native C-level
assertion inside AppKit — an ``abort()`` below Python that no try/except
can catch**. Windows/Linux tolerate off-main-thread tray icons, so the bug
was invisible on the maintainer's machine and in CI; the cross-platform
plan had classified ``jarvis/ui/tray.py`` as "already cross-platform" and
macOS was ``unverified-on-real-desktop`` (JARVIS-20 CP-13..15). The first
real-Mac run found the gap. Python 3.14 is NOT the cause (install.sh's
candidate order is correct); the crash is purely the thread violation.

**Fix (2026-07-14).** ``JarvisTray.start()`` gates on
``sys.platform == "darwin"`` and degrades to a logged English no-op
(AD-6/AD-11: degrade, never crash) — the single choke point covering all
four call sites (``__main__.py`` tray app, ``desktop_app.py``,
``overlay/tray_surface.py``, ``ui/shell/shell.py``). The desktop window and
Dock icon remain the macOS surface. Guard:
``tests/unit/ui/test_tray.py::test_tray_start_is_noop_on_macos``.

**Follow-up (implemented 2026-07-17).** The real macOS menu-bar icon
shipped exactly along the sketched path: ``JarvisTray.start()`` hosts the
icon on the pywebview ``NSApplication`` via ``pystray.Icon(...,
darwin_nsapplication=...)`` + ``run_detached()``, constructed on the AppKit
main thread; every later mutation from a worker thread (menu rebuild,
tooltip, stop) is marshaled onto the main thread via
``PyObjCTools.AppHelper.callAfter``. The 2026-07-14 logged no-op remains the
degrade path when no main-thread NSApplication is available (e.g. headless).
Tracked in ``docs/plans/cross-platform-mac-linux/FIX-TRACKER.md``.

**Class rule.** ANY AppKit/UI object on macOS (status items, windows,
menus) must be created and driven on the main thread; a worker-thread
violation is a native abort, not a catchable exception. "No platform
marker in the code" does not mean cross-platform — threading contracts
differ per OS, and only a real-device boot proves them.

## BUG-056: A scrub-gate abort's stale cancellation ends the session, and the transcript hides the abort (HIGH, FIXED 2026-07-14)

**Symptom.** Voice session 2026-07-14 15:12: "Welche MCP-Server habe ich
alle?" was answered by a healthy 5.4 s delegated turn <!-- i18n-allow: quoted German user utterance under forensic analysis -->
(the BUG-055 latency fixes working as designed), but the spoken answer cut
off mid-sentence at "Du hast zwei", the session ended with
`hangup_reason=error`, and the exported transcript showed only the truncated
reply — no trace of what failed or why the answer stopped.

**Evidence (desktop log + sessions.db, session c3c1997f).**

- 15:13:09.594 `realtime_delegate_completed` success=True after 5393 ms.
- 15:13:12.468 `scrub gate cancelled output: unsafe output transcript` — the
  ScrubHoldGate flagged a transcript delta as a hard leak while the answer
  was being voiced; the honest fallback line was then spoken via classic TTS
  (audible 15:13:18–15:13:20).
- 15:13:20.536 `terminal provider error: response_cancel_not_active:
  Cancellation failed: no active response found` — recorded with
  `recoverable: false`; the pump set `_failed` and the session ended
  `reason=error` at 15:13:23.918.
- The recorded spoken track contains the progress bridge and the truncated
  reply, but NOT the fallback line and NOT the abort reason.

**Root causes (three).**

1. **BUG-053's misclassification, second firing path.** The scrub cancel
   calls the adapter's `interrupt()`; the provider's response lifecycle was
   already over, so `response.cancel` answered with the benign
   `response_cancel_not_active`. The adapter's recoverable set contained only
   `conversation_already_has_active_response`, so this idempotent no-op race
   was labeled terminal and killed an otherwise healthy session.
2. **Transcript dishonesty.** `_cancel_unsafe_output` spoke a fallback line
   through `error_spoken` but never published it as a `SpeechSpoken` event,
   so the recorder — and therefore the exported transcript — had no trace of
   the abort (the exact gap the maintainer reported).
3. **Undiagnosable trigger.** Only the generic reason string "unsafe output
   transcript" survived; the gate did not surface WHICH scrub detectors
   tripped, so a possible false positive on a technical-but-harmless answer
   (MCP server names) cannot be judged after the fact.

**Fix (2026-07-14).**

- `_RECOVERABLE_ERROR_CODES` gains `response_cancel_not_active` (BUG-053
  correction 1): both sides of the response-boundary race are now benign.
- `interrupt()` skips the wire cancel when `_response_idle` is set (BUG-053
  correction 2); the recoverable classification stays as the backstop for
  the remaining check-to-wire race window.
- `_cancel_unsafe_output` publishes the spoken fallback as
  `SpeechSpoken(spoken_kind="withheld", detail=<reason>)` — new vocabulary
  entry wired through all four parity layers (constants.py, models.py,
  types.ts, TurnCard.tsx) — so the transcript shows the abort, its fallback
  line, and the detector names.
- `ScrubHoldGate.hard_leak_actions()` exposes the tripped detector names
  (safe metadata, never the flagged content) and the session embeds them in
  the cancel reason.

**Still open.** Whether the 15:13 hard leak itself was a false positive is
not reconstructable (transcript deltas are not persisted); the detector-name
diagnosis added here answers that question at the next occurrence. BUG-053
correction 3 (forward accepted barge-in audio into the next turn) also
remains open.

**Class rule.** A cancellation that finds nothing to cancel has already
succeeded — treat provider races idempotently on both boundaries (create and
cancel). And every safety abort that changes what the user hears MUST leave
an honest, user-visible trace in the recorded conversation; a silent abort
reads as a broken answer and is undebuggable afterwards.

Guards: `tests/unit/realtime/test_openai_realtime.py` (recoverable + skip),
`tests/unit/realtime/test_scrub_gate.py` (detector diagnosis),
`tests/unit/realtime/test_session.py` (withheld recording),
`tests/unit/sessions/test_spoken_kind_parity.py`.

## BUG-057: Second macOS first-boot abort — JarvisBar/Orb Tk root created off the main thread (HIGH, FIXED 2026-07-14)

**Symptom.** After the BUG-056 tray fix shipped, a fresh macOS install still
died at first launch with the identical "Python quit unexpectedly" dialog.
The dialog is the generic macOS crash reporter — same look, DIFFERENT crash
site: with the tray gated off, boot now reached the on-screen overlay.

**Root cause.** The desktop backend runs on the ``jarvis-backend`` worker
thread; its boot task builds the default overlay surface via
``DesktopApp._build_overlay_surface`` → ``JarvisBarOverlay.start_in_thread()``
which creates the Tk root + mainloop on the daemon thread
``jarvisbar-tk-mainloop``. Aqua-Tk is AppKit-backed and main-thread-only on
macOS: a worker-thread Tk root aborts the whole process with a native,
uncatchable assertion — exactly the BUG-056 class, one layer further into
boot. Same defect in three siblings: the mascot ``OrbOverlay`` (thread
``orb-tk-mainloop``), the opt-in ``TkVirtualCursor`` (thread
``virtual-cursor``), and ``make_overlay_surface`` which handed darwin a
``TkColorKeyOverlay``. A full boot-path audit confirmed the remaining AppKit
touchpoints are safe (Dock icon + pywebview window run on the main thread).

**Fix (2026-07-14).** darwin gates at every off-main-thread Tk creator, all
degrading to logged English no-ops (AD-6/AD-11):
``_build_overlay_surface`` returns the existing ``NullOverlay`` (bridge
wiring stays intact), ``JarvisBarOverlay.start_in_thread`` /
``OrbOverlay.start_in_thread`` / ``TkVirtualCursor.start`` no-op as
backstops for any other caller, and ``make_overlay_surface`` sends darwin to
the tray floor. macOS keeps the desktop window + Dock icon. Guards:
``tests/unit/ui/test_macos_ui_main_thread_gates.py`` (incl. an AD-7
windows-still-spawns test),
``tests/overlay/test_overlay_surface.py::test_factory_selects_tray_floor_on_macos``.

**Follow-up (implemented 2026-07-17).** The bar/orb got the own-process
host: the mascot/orb now renders inside the jarvisbar subprocess host as
``SubprocessMascotOverlay`` (``jarvis/ui/jarvisbar/subprocess_overlay.py``),
so its Tk root lives on the subprocess's own main thread and never touches a
worker thread in the backend process; ``OrbOverlay`` additionally gained
macOS Aqua-Tk alpha transparency (a ``-transparent`` root instead of the
Windows magenta color key). Tracked in the cross-platform FIX-TRACKER.

**Class rule.** BUG-056 generalizes: on macOS EVERY UI toolkit in the
process (AppKit, Aqua-Tk, pystray) is main-thread-only, and the "Python
quit unexpectedly" dialog looks identical for every native abort — fixing
one crash site just reveals the next one down the boot path. Audit the
WHOLE boot path for off-main-thread UI creation at once (as done here),
never one dialog at a time.

## BUG-058: Third macOS first-boot abort at onboarding start — unserialized PortAudio re-init + ungated Quartz event tap (HIGH, HARDENED 2026-07-14, on-device confirmation pending)

**Symptom.** With BUG-056+057 shipped, a fresh Mac boot now shows the
desktop window and enters first-launch onboarding — then "Python quit
unexpectedly" again, seconds in, before any meaningful interaction. No
terminal log captured yet, so this entry hardens the audited candidates
rather than a log-confirmed line.

**Audit (full onboarding-window trace).** The onboarding routes themselves
are pure state-file writes — zero native code. In the same time window two
UNGATED native touches fire with NO user interaction:

1. **Concurrent PortAudio teardown/re-init.** The boot prefetch
   (``start_audio_device_prefetch``, daemon thread) and the voice pipeline's
   Phase-A ``_stabilize_audio_devices`` both run the settle loop; when the
   pipeline arrives while the prefetch is still polling (it runs up to
   ~8 s), two threads interleave ``sd._terminate()``/``sd._initialize()``.
   Windows WASAPI tolerates that; macOS CoreAudio's HAL can fault natively
   on concurrent teardown/re-init. Timing matches the crash exactly.
2. **pynput Quartz event tap without the Accessibility grant.** The hotkey
   trigger arms unconditionally at ``pipeline.run()``; pynput's darwin
   backend creates a CGEventTap on its own internal thread — Python-level
   try/except around ``Listener.start()`` cannot catch a native abort
   there, and on a fresh Mac the grant never exists.

Known but interaction-gated (NOT hardened here, documented for the next
log): the wake-word step's mic test / wake save opens a CoreAudio INPUT
stream (``sd.InputStream``) — on a TCC-killed process that is a SIGABRT.
Ruled out by audit: notifications, AppKit dialogs, prompting permission
probes, Keychain, the LaunchAgent write (plist + subprocess only), and all
BUG-056/057 gates (verified in place).

**Hardening (2026-07-14).**

- ``device_init._REINIT_LOCK`` serializes every terminate→initialize→query
  sequence (hard safety, all platforms), and
  ``wait_for_stable_audio_devices`` now JOINS an in-flight prefetch
  (single-flight) instead of racing it with a second poll loop.
- ``PynputBackend.start()`` preflights the non-prompting
  ``AXIsProcessTrusted`` probe on darwin and fails CLOSED (None =
  unverifiable → treated as not granted) with an honest English message
  naming the System Settings path; hotkeys re-arm normally once granted.
  Off-darwin behavior is byte-identical (guard test).

Guards: ``tests/unit/audio/test_device_init_single_flight.py``,
``tests/unit/trigger/test_hotkey_backends.py`` (4 new darwin-preflight
tests).

**Class rule.** Native init that "has always been fine" on Windows is not
cross-platform evidence: CoreAudio punishes concurrent re-init, Quartz
punishes ungranted event taps — both below Python. Any native engine
init/teardown must be serialized behind a lock, and any macOS
permission-gated native surface (mic, event tap, screen) must preflight a
non-prompting probe and degrade honestly instead of letting the OS kill
the process.

## BUG-059: Local speech pack install blamed the internet — missing cp314 wheel sent pip into an FFmpeg source build (MEDIUM, FIXED 2026-07-14)

**Symptom.** First real-Mac onboarding (BUG-056..058 fixed, app running):
the wake-word step's "Install the local speech pack" fails with "Install
failed. Check your internet connection and try again." on a perfectly
healthy connection. The pip tail underneath shows the truth: seven FFmpeg
``Package 'libav*' not found`` lines from pkg-config, ending in
``Failed to build 'av' when getting requirements to build wheel``.

**Root cause (three layers).**

1. **No cp314 wheel.** The venv is Python 3.14 (install.sh preferred the
   newest candidate); ``av`` (a faster-whisper dependency) publishes no
   3.14/macOS wheel yet, so pip silently fell back to a SOURCE build that
   needs FFmpeg dev libraries no end user has. Same wheel gap as
   onnxruntime/webrtcvad on darwin+3.14 (pyproject markers exclude them
   there — wake/VAD degrade, related open item).
2. **The installer invited the source build.** ``install_pip_package`` ran
   plain ``pip install`` — nothing told pip that an end-user machine must
   never compile native packages.
3. **The UI lied about the cause.** The ``enable_local_error`` string
   hardcoded "Check your internet connection" for EVERY failure.

**Fix (2026-07-14).**

- ``classify_pip_failure`` (jarvis/setup/dependencies.py): missing-wheel /
  source-build signatures → an honest "No prebuilt package exists for
  Python X.Y on this system yet … Python 3.12 or 3.13 has full prebuilt
  support"; only genuine network signatures name the network; unknown
  stays the raw pip tail. The diagnosis leads the returned message.
- ``install_pip_package(..., only_binary=True)`` adds
  ``--only-binary=:all:``; the enable-local-speech route pins it so pip
  fails fast with the diagnosis instead of attempting a toolchain build.
- ``enable_local_error`` rewritten in en/de/es: the details name the
  cause; only a connection error in the details means a network problem.
- install.sh candidate order: ``python3.13 python3.12 python3.11
  python3.14 …`` — 3.14 stays a working core fallback until the native
  stack ships cp314 wheels (comment marks the revert condition).

Guards: ``tests/unit/setup/test_pip_failure_diagnosis.py``,
``tests/unit/ui/test_wake_local_speech_install.py::test_install_is_wheel_only_for_end_users``,
``tests/unit/install/test_install_sh_python_detection.py::test_prefers_python_with_full_native_wheel_support``.

**Class rule.** An in-app installer on an end-user machine is wheel-only:
a source-build fallback is never satisfiable there, and every failure
message must name the actual failing layer — "check your internet" as a
catch-all turns a version gap into user gaslighting. "Newest Python
first" is wrong while the native wheel ecosystem lags a fresh CPython.

## BUG-060: Closed the macOS desktop app — no way to relaunch it like a normal app (MEDIUM, FIXED 2026-07-14)

**Symptom.** After the first successful macOS run, closing the desktop app
left no path back for a normal user: Spotlight ("Personal Jarvis") found
nothing, Launchpad and /Applications showed nothing — relaunch required a
terminal command. A pip-based install ships no ``.app`` bundle, and macOS
only surfaces bundles.

**Fix (2026-07-14, hardened 2026-07-15).**
``jarvis/setup/macos_app_bundle.py`` installs
``~/Applications/Personal Jarvis.app`` so Spotlight and Launchpad can find
the managed source install. The first implementation used a bash executable
that replaced itself with the venv Python. That was discoverable but did not
provide a reliable ``NSBundle`` identity: TCC could still attach grants to
Python or Terminal. The hardened installer now builds a native Mach-O py2app
alias launcher, ad-hoc signs and verifies it, then launches a short identity
probe through LaunchServices. Ordinary source updates preserve the valid
bundle byte-for-byte so its local TCC identity does not churn. All manual,
restart, updater, and login-autostart paths re-enter through that bundle.
Developer-ID signing and notarization remain requirements for a separately
distributed binary artifact; the source installer does not pretend its local
ad-hoc signature is a notarized release.

Guards: ``tests/unit/setup/test_macos_app_bundle.py`` rejects shell launchers,
checks native structure and privacy metadata, proves idempotent preservation,
and covers the identity-probe contract. ``.github/workflows/macos-desktop.yml``
builds and self-probes the real bundle on Intel and Apple-Silicon runners.

**Class rule.** "Installed" is not "discoverable": every OS needs its
native launch surface (Windows Start-Menu shortcut, macOS ``.app``
bundle, Linux XDG ``.desktop``) or closing the app strands the user. Managed
desktop installs now register and remove all three surfaces.

## BUG-061: Base install bricked on Intel Macs — pinned onnxruntime has no x86_64 macOS wheels (HIGH, FIXED 2026-07-14)

**Symptom.** Fresh install on an Intel MacBook Pro (the Python-3.13
bootstrap worked; wheels resolved as cp313/x86_64): the base
``pip install -r requirements.txt`` aborts at
``onnxruntime==1.23.2 … from versions: none`` — the WHOLE install dies,
core included.

**Root cause.** onnxruntime stopped shipping Intel-macOS wheels; the last
x86_64 builds also require older Pythons. The darwin marker
(``python_version < '3.14'``) still demanded it on every Mac, so a pinned
version that cannot exist for x86_64+cp313 sat in the BASE lockfile —
one dead optional-capability package bricked the entire product (§3 /
AP-22 class, at the packaging layer).

**Fix (2026-07-14).**
- pyproject + requirements.in: darwin onnxruntime marker gains
  ``platform_machine == 'arm64'``; lock regenerated with
  ``uv pip compile --universal`` (sync + universal gates green). Intel
  Macs skip onnxruntime; Silero VAD degrades to WebRTC VAD there
  (existing degrade path — same one darwin+3.14 already exercised).
- install.sh bootstrap is architecture-aware: Darwin x86_64 fetches
  Python **3.12** (the native voice stack's last Intel wheels end at
  cp312: ctranslate2/av), everything else 3.13; the full-support check
  follows the same rule.

**Follow-up (same day).** The direct marker alone was NOT enough: a
platform-resolve probe (``uv pip compile --python-platform
x86_64-apple-darwin``) showed ``openwakeword`` pulling onnxruntime back in
as ITS dependency — the transitive route re-bricked Intel Macs. Its darwin
marker now carries the same ``platform_machine == 'arm64'`` condition
(wake degrades to vosk_kws there). Lesson: after gating a dependency,
re-resolve for the affected platform — direct markers do not stop
transitive pulls.

**Follow-up (2026-07-17).** Two corrections. (a) The "Silero VAD degrades
to WebRTC VAD" claim above was aspirational until now: the runtime imported
``webrtcvad`` nowhere and actually fell straight through to the bare energy
RMS floor. The WebRTC middle tier is now wired in ``jarvis/audio/vad.py``
(Silero ONNX → WebRTC VAD → RMS energy), so an onnxruntime-less Mac gets a
real VAD instead of energy-only endpointing. (b) NO-GO decision recorded on
re-adding an older onnxruntime pin for darwin-x86_64: a pin whose wheel
matrix is frozen in the past is the exact recurrence class this entry
documents, and wake already defaults to ``vosk_kws``, which is fully
functional on Intel Macs. Revisit trigger: field reports that WebRTC-tier
endpointing is insufficient, or concrete demand for ``custom_onnx`` wake on
Intel.

**Class rule.** A pinned dependency whose wheel matrix has DROPPED a
platform must never sit unconditionally in the base lock: gate it with
platform markers and give the capability an honest degrade. Wheel
matrices shrink over time — "it resolved when pinned" is not "it resolves
everywhere forever".

## BUG-062: Realtime speech audibly choppy + answers cut short on a speakers+mic laptop (HIGH, FIXED 2026-07-15)

**Symptom.** First real realtime-voice run (old Intel MacBook, CPU-only,
built-in speakers + mic): the assistant's TRANSCRIPT is complete, but the
audible speech stutters constantly and large parts are never heard.

**Deep-dive findings (full trace in the audit, two compounding causes).**

1. **Self-barge-in via speaker echo.** The desktop realtime path arms the
   Silero barge-in detector during playback but — unlike the classic
   pipeline — never arms any echo-suppression window
   (``_suppress_session_input_after_tts`` is classic-path only). Open
   speakers feed the assistant's own voice into the detector; a false
   confirm truncates the provider response AND aborts/drains playback —
   exactly "complete transcript, mostly unheard".
2. **Event-loop starvation from per-frame ONNX.** ``barge_detector.feed``
   runs ~3 Silero inferences per 100 ms mic block synchronously on the
   voice event loop for the whole answer; on a slow CPU this delays the
   120 ms playback write batches → PortAudio underruns → steady stutter.
   (Resampling ruled out: 24 kHz == 24 kHz. Queue drops ruled out: the
   playback queue blocks, never drops.)

**Fix now (2026-07-14).** Energy pre-gate in
``DesktopRealtimeBargeInDetector.feed`` (``min_frame_rms``, default 0.010,
AP-27-anchored: silence ghosts <= 0.0043, quiet speech ~0.009): quiet
frames never reach the ONNX model — removing most of the per-frame CPU
load AND damping moderate speaker echo. Documented trade-off:
whisper-quiet barge-in no longer triggers. Guards:
``tests/unit/realtime/test_desktop.py`` (gate skips ONNX on quiet frames,
loud speech still confirms; logic tests pin ``min_frame_rms=0.0``).

**Completion (2026-07-15).** A Windows voice-session forensic proved the
remaining boundary: the output device reported 210 ms latency, playback
drained, and the desktop adapter uploaded that physical speaker tail
immediately. OpenAI transcribed it as a new user turn (``Mostly cloudy, with a
high near``), cancelled the real answer, then remained in that phantom turn for
29.4 s. Desktop realtime now keeps the existing local Barge-in/VAD detector
armed for 500 ms after a normal playback drain. Echo remains local, while
confirmed immediate user speech is forwarded with its buffered opening
syllables. Cancellation by a real Barge-in does not arm the tail guard. Guard:
``tests/unit/speech/test_realtime_mode.py::test_post_output_echo_tail_stays_local_and_preserves_immediate_user``.

**Follow-ups (performance only).** (a) Offload the detector off the
audio-critical loop; (b) add a small time-based release floor in
``ScrubHoldGate`` so audio is not strictly transcript-delta-clocked.

**Class rule.** Half-duplex voice on open speakers MUST treat its own
output as hostile input: any interrupt detector needs an energy floor or
echo reference before it may cancel playback, and nothing compute-heavy
belongs on the audio-critical loop per mic frame.

## BUG-063: Realtime Computer-Use task needed 4 dispatches — context-free goals, boundary-timeout refusal, invented capability gaps (HIGH, FIXED 2026-07-15)

**Symptom (voice session 2026-07-15 07:57).** "Open my Discord server, go to
Personal Jarvis, and announce a live event the day after tomorrow" took four
dispatches and two why-questions. In between the user heard a canned "that
didn't work just now" with no action attempt, an invented explanation ("I have
no API access", offering to type via "a script or the keyboard"), and a
premature "Done." while Discord merely showed the Friends view. The final
mission posted a placeholder announcement instead of the requested content.

**Root causes (four, compounding).**

1. **Context-free CU goal.** The deterministic local-action gate ships the RAW
   current utterance as the mission goal (``plan.prompt=original``). A
   correction / follow-up turn ("Ihr macht es doch mit Computer-Use") carries <!-- i18n-allow: quoted German speech input from the forensic -->
   no task of its own, so the loop ran against a vacuous goal and the verifier
   passed on trivial state (Friends view open → "Done").
2. **Boundary-timeout refusal after a promise block.** The unbacked-action
   guard interrupts the response that carried the promise; when that response
   is already complete on the wire, no further ``turn_complete`` arrives, so
   the deterministic delegate's 3 s input-boundary wait timed out and REFUSED
   the action — a canned failure with zero LLM calls, although the final input
   transcript was in hand.
3. **No tool self-knowledge in the delegate directive.** Neither the
   ``jarvis_action`` declaration nor the role directive named on-screen
   control, so the live model invented capability gaps instead of re-calling
   the function with the correction folded in.
4. **History window too small for a correction sequence.** 8 delegate-history
   messages were exhausted by 4 correction turns + 2 background-completion
   notes; the original announce request was trimmed out exactly when the
   recovery turn needed it → placeholder announcement content.

**Fixes (2026-07-15).** (1) ``BrainManager._cu_goal_with_context`` appends a
bounded recent-turns block (``_TURN_HISTORY_OVERRIDE`` → ``self._history``) to
every gate-claimed CU goal. (2) ``_DelegateTurnState.input_final``: the
promise-block recovery marks the input final, and a boundary timeout then
delays the dispatch instead of vetoing it. (3) The delegate declaration +
directive name click/type/navigate screen control and forbid claiming a
missing tool/API/access for anything in the user's world. (4)
``_DELEGATE_HISTORY_MAX_MESSAGES`` 8 → 20.

**Guards.** ``tests/unit/brain/test_computer_use_offload.py`` (goal context:
carried, bare-on-first-turn, live-history fallback);
``tests/unit/realtime/test_session.py`` (promise-block recovery dispatches
after boundary timeout; directive names screen control + forbids capability
denial; history keeps a task five exchanges back).

**Corrected finding (2026-07-15, follow-up forensic).** The first version of
this entry claimed a text-only ``meta/llama-3.3-70b-instruct`` stepped the
mission. Wrong: that came from the per-candidate speed-tune INFO line
("[cu] nvidia: stepping with the fast vision model …"), which fired on every
step for a chain CANDIDATE that never served a single call. The missions
actually stepped on the openrouter Tool Model pin
(``google/gemini-3.5-flash``, vision-capable) — the fast-chain head. Fixed:
candidate swaps now log at DEBUG with "chain candidate" wording, and a
change-triggered INFO line names the brain that actually serves
("[cu] vision calls served by …"). Guard:
``tests/unit/cu/test_brain_call_cu_provider.py::test_serving_brain_logged_once_per_identity``.
Additionally the global Tool Model was never ACTIVATED ([brain.tool_model]
unset → automatic selection); it is now pinned to the vision-capable
``gemini`` provider, which the ``call_vision_brain`` hoist and the delegated
``prefer_tool_model`` turns both lead with — Computer-Use and tool routing
deterministically run on the user's Tool Model, in Realtime and Pipeline
alike.

**Class rule.** A deterministic action dispatched from a conversation must
carry the conversation: any harness goal built from a single utterance is
wrong for every follow-up, correction, and instrument-naming turn. And a
recovery path that already holds the complete user text must never refuse to
act because a provider wire event failed to arrive.

## BUG-064: Grok realtime session goes permanently deaf after a barge-in cancel — server drops the session contract (HIGH, FIXED 2026-07-16)

**Symptom.** Desktop realtime session on `grok-realtime`, 2026-07-16 08:07
(session `3cefaede`, exported as `voice-session-2026-07-16_08-07-constable.md`).
Turn 1 ("Constable.") completed normally. Turn 2 was cut short by server VAD
("Can you please" — the user paused mid-sentence), Jarvis requested a
response, and 362 ms later the user resumed speaking, so the barge-in path
cancelled the in-flight response (`realtime_cancel … reason=barge_in`). From
that moment the user kept talking for over 80 seconds: the taskbar/JarvisBar
audio indicators kept reacting (local mic level + server VAD both alive), but
**no transcription ever appeared again, no turn started, and Jarvis never
answered** until the user hung up via hotkey at 08:09:02. The exported
transcript therefore ends after two turns although substantially more was
said.

**Evidence.** `data/jarvis_desktop.log` + `data/flight_recorder/2026-07-16.jsonl`:

- 08:07:36.878 `LatencySpan realtime_cancel duration_ms=362 reason=barge_in`
  — the only wire `response.cancel` of the session that hit an ACTIVE
  response lifecycle.
- Afterwards, five `OpenAI Realtime suppressed unsolicited response <id>`
  warnings (08:07:57.918, 08:08:19.499, 08:08:26.469, 08:08:29.878,
  08:08:44.740) — the server auto-created a response at each detected end of
  speech, which `create_response: false` forbids.
- Zero `conversation.item.input_audio_transcription.completed` **and** zero
  `.failed` events after turn 2 — the deafness is server-side; the client
  pump was demonstrably alive (it processed and suppressed the five
  responses).
- Contrast: the same suppression on `openai-realtime` (2026-07-15 11:03:03)
  occurred once, was followed by the benign `response_cancel_not_active`
  error, and the session continued normally — the wedge is Grok-specific.

**Root cause.** The barge-in `response.cancel` of an active response left the
xAI server without the session contract Jarvis configured at open: input
transcription (`grok-transcribe`) stopped producing events and manual
response mode (`turn_detection.create_response: false`) reverted to automatic
— both live in the same `audio.input` session block, consistent with the
server dropping/reverting session state. The client correctly cancelled each
unsolicited response (the exactly-one-response invariant held; no stray audio
reached the speaker) but had no path that ever RESTORED the contract, so the
session stayed connected, listening, and deaf until manual hang-up.

**Fix.** In the shared OpenAI-compatible adapter
(`jarvis/plugins/realtime/openai_realtime.py`, inherited by `grok_realtime`):
the session now retains the full session payload sent at open, kept current
by `update_session()` (live instructions/tool changes are folded in, so a
re-arm never reverts newer state). Suppressing an unsolicited response —
impossible in a healthy manual-response session, hence a reliable wedge
signal — now re-sends that full payload (`_rearm_session_contract()`),
restoring input transcription and `create_response: false` in place. The
re-arm is throttled (5 s cooldown per burst) and fail-safe (a rejected
session.update never kills the receive pump). On a healthy server the re-arm
is an idempotent no-op, so the defense is provider-family-wide and free.

Guards: `tests/unit/realtime/test_openai_realtime.py`
(`test_unsolicited_response_rearms_the_full_session_contract`,
`test_contract_rearm_is_throttled_within_a_burst`,
`test_contract_rearm_carries_live_instruction_and_tool_updates`) and
`tests/unit/realtime/test_grok_realtime.py`
(`test_unsolicited_response_rearms_grok_transcription_contract`).

**Class rule.** A duplex provider's session configuration is a CONTRACT, not
an initialization detail: any event that is impossible under the configured
contract (here: an unsolicited response under manual-response mode) is proof
the server no longer holds it, and the client must re-assert the full
contract instead of only suppressing the symptom. Suppression without
restoration turns one server-side hiccup into an unbounded silent outage that
the user experiences as "it hears me (indicators fire) but nothing happens."

**Recurrence 2026-07-16 09:23 — the re-arm alone does NOT heal Grok; transport
rebuild escalation added.** Session `30c532cb`, first live run WITH the re-arm
fix (committed 08:34): turn 0 committed ("Constable?", an English mishearing
of the German sentence opener), the user kept talking, the barge-in cancel
fired (`realtime_cancel reason=barge_in`), and the known wedge followed — one
suppressed unsolicited response at 09:23:45, the re-arm log line proves the
recovery ran, and **still no input transcription event ever arrived again**.
The session sat in LISTENING for 19 more seconds until manual hang-up
(`turn_count=0`). Conclusion: on a wedged Grok server, re-sending the session
contract restores at most the manual-response half; the transcription half
stays dead server-side.

**Escalation fix (same adapter).** The session now tracks a *transcript
deadline*: whenever the server has provably heard a user turn — an
`input_audio_buffer.committed` event, or an auto-created response it is
forbidden to create — an input transcript (completed or failed) is owed under
the contract. If none arrives within `_TRANSCRIPT_OVERDUE_S` (6 s) while no
response lifecycle is active, the adapter **rebuilds the transport in place**:
a fresh WebSocket connection carrying the CURRENT session contract, state
reset, and the receive pump hops onto the new iterator (the orchestrator sees
one recoverable `RealtimeProviderError` warning, not a session end). In-call
conversation history is lost — strictly better than a call that can no longer
hear. A suppressed duplicate arriving <2 s after a transcript (the benign
openai-realtime race, 2026-07-15) never arms the deadline; a failed rebuild
closes the session so the orchestrator reports an honest provider error. The
deaf-wedge watchdog also runs from `send_audio`, because a fully deaf server
emits no events at all — the microphone pump is the only guaranteed heartbeat.

Additional guards: `test_deaf_session_rebuilds_the_transport_and_receive_hops_onto_it`,
`test_committed_turn_arms_and_transcript_clears_the_deadline`,
`test_suppressed_duplicate_right_after_a_transcript_does_not_arm`,
`test_failed_transport_rebuild_closes_the_session` (openai) and
`test_deaf_grok_session_rebuild_carries_the_grok_contract` (grok).

**Recurrence #2, 2026-07-16 10:23 — the deadline path has a blind spot and a
Grok error wording killed the session (session `10000104`, first live run
WITH the transport-rebuild escalation).** Turn 1 fine; turn 2 committed and
transcribed ("Was?" — xAI's VAD again cut the utterance short), then the
known wedge. The rebuild never fired, for a provable reason: the first stray
auto-response arrived 1.9 s after the turn's transcript — inside the 2 s
benign-race quiet window, so no transcript deadline was armed — and the deaf
server then emitted NOTHING for 16 s (no speech_started, no committed, no
transcript), so nothing else could ever arm it. When the server's next stray
finally arrived, the client's `response.cancel` raced the response's own
completion and xAI answered with `invalid_request_error: Cancellation
failed: no active response found` — a wording the recoverable-code set
(`response_cancel_not_active`) does not cover, so a benign no-op error was
labeled terminal and ended the session (`Beendet durch: Fehler`). <!-- i18n-allow: quoted German session-export field under test -->

**Fixes (same adapter).** (1) Benign lifecycle races are also recognized by
message shape (`_RECOVERABLE_ERROR_MESSAGE_MARKERS`: "no active response" /
"already has an active response") because xAI wraps them in the generic
`invalid_request_error` code. (2) Stray-after-unheeded-re-arm escalation: a
FURTHER unsolicited response arriving ≥ the re-arm cooldown after a contract
re-arm that produced no input transcript since (`_transcript_heard_since_rearm`,
a sequence marker — Windows' ~16 ms `time.monotonic()` resolution makes
timestamp ordering lie) rebuilds the transport immediately instead of
re-arming forever. Guards:
`test_grok_generic_cancellation_failed_error_is_recoverable`,
`test_second_stray_after_unheeded_rearm_rebuilds_the_transport`.

**Recurrence #3, 2026-07-16 10:51 — a swallowed `response.done` disarms EVERY
idle-gated defense at once (session `10000103`, first live run WITH the
recurrence-#2 fixes).** Turn 2 ("Hey", again VAD-truncated) was transcribed,
the orchestrator requested its native reply, and 360 ms later the user's
continued speech triggered the local barge-in (`realtime_cancel
reason=barge_in`), which drops provider output without a wire
`response.cancel`. The server never sent that response's `response.done`, so
`_response_idle` stayed CLEAR for the rest of the call — and every deaf-wedge
defense gates on idle ("with a response in flight no transcript is owed"):
the stray auto-response at +3 s was suppressed instead of adopted (idle
check), the transcript deadline never armed (idle check), the rebuild never
fired (idle check). One swallowed lifecycle event silently turned the entire
BUG-064 defense stack off; the session sat mute until manual hang-up.

**Fix (same adapter).** Response-lifecycle liveness: `_last_response_activity`
is stamped by every `response.*` event and every outgoing `response.create`.
While `_response_idle` is clear, total response-event silence for
`_RESPONSE_STALL_S` (8 s — a healthy in-flight response streams events every
few tens of milliseconds) declares the lifecycle dead and rebuilds the
transport in place (microphone pump is the guaranteed trigger). The rebuild
resets `_response_idle`, so a hung `_create_response` waiter also unblocks
onto the fresh transport. Guard:
`test_accepted_response_without_done_stalls_and_rebuilds`.

**Class rule (extends the BUG-064 lesson).** Any defense gated on "a response
is in flight" inherits a new failure mode: the response that never finishes.
A lifecycle flag that only the SERVER can clear needs its own liveness
watchdog, otherwise one swallowed terminal event freezes every dependent
defense simultaneously — and the freeze is invisible because each defense
individually looks correctly disarmed.

**Recurrence #4, 2026-07-16 11:23 — the wedge feeds the watchdogs (session
`a69d2318`, first live run WITH the recurrence-#3 stall watchdog).** Turn 1
("Wie viel Geld hat", VAD-truncated mid-sentence) was cancelled by the local <!-- i18n-allow: quoted German user utterance under forensic analysis -->
barge-in; the requested response's lifecycle hung again (idle clear). This
time BOTH remaining defenses were disarmed by the wedge's own emissions:
(1) the deaf server emitted transcription FAILED events, which
`_note_input_transcript()` counted as "re-arm heeded" — so the
stray-after-unheeded-re-arm escalation saw a healed session and re-armed
forever; (2) the server auto-created stray responses every ~7.8 s — just
under `_RESPONSE_STALL_S` (8 s) — and every stray stamped
`_last_response_activity`, keeping the dead lifecycle looking alive. The
session sat mute 16 s until manual hang-up.

**Fixes (same adapter).** (1) A failed transcript settles the per-turn
contract debt (deadline cleared) but no longer marks a re-arm as heeded —
only a COMPLETED transcript proves the transcription side works
(`_note_input_transcript(restored_hearing=False)` on failed). (2) Only an
ACCEPTED response's events stamp the stall clock; unsolicited strays are
wedge symptoms, not liveness. Accept/adopt paths stamp explicitly. Guards:
`test_failed_transcription_does_not_mark_rearm_as_heeded`,
`test_unsolicited_stray_does_not_feed_the_stall_watchdog`.

**Class rule (final form).** When a server is misbehaving, its OWN emissions
must never count as evidence of health for any watchdog that exists to catch
that misbehavior — classify every inbound signal as symptom or proof-of-cure
FIRST, and let only proof-of-cure reset a defense. A wedge that emits
symptoms on a timer will otherwise starve every timeout-based defense
forever.

**Resolution — grok-realtime REMOVED from the product (maintainer decision
2026-07-16).** Four server-side wedge variants inside one morning (contract
drop after cancel, ignored VAD silence window, swallowed `response.done`,
symptom emissions that starve the watchdogs), plus VAD truncation that no
client can compensate, made the provider unshippable: even perfect self-heal
still loses the truncated turn. Removed: the `grok-realtime` entry point,
`jarvis/plugins/realtime/grok_realtime.py`, its REALTIME_MODELS /
REALTIME_VOICES catalogs, the ProviderSpec card, the credential family, and
the auth alias. The key-aware factory (AP-22) makes the removal self-healing:
a config still naming `grok-realtime` simply resolves to the next installed
credential-ready realtime provider. The four BUG-064 defenses stay in the
shared OpenAI-compatible adapter — they protect `openai-realtime` and any
future compatible provider. Grok Voice TTS (`grok-voice`) and the Grok brain
are unaffected. Guards: `test_grok_realtime_stays_removed`,
`test_grok_realtime_spec_stays_removed`,
`test_get_realtime_options_removed_grok_is_unknown`, catalog assertions in
`test_current_model_catalogs.py`.

## BUG-065: macOS/Linux desktop shows a permanent OFFLINE — WebKit drops the HttpOnly session cookie from WebSocket handshakes (HIGH, FIXED 2026-07-16)

**Symptom (real Mac hardware, 2026-07-15).** During and after boot the macOS
desktop window shows "Assistant OFFLINE", the chat placeholder "Offline", and
the "Ready for commands" hero — while REST-backed surfaces (history, views,
onboarding) all work. On Windows the same boot shows the intended sequence:
"STARTING…" → the "Jarvis is starting up" banner → connected. The user-facing
read on macOS is "Jarvis is broken", although the backend is healthy.

**Root cause (engine split, not a boot split).** The live event channel `/ws`
is authenticated by the `jarvis_session` cookie (`HttpOnly` +
`SameSite=Strict`). Chromium (Windows WebView2) attaches that cookie to the
WebSocket handshake; WebKit engines — WKWebView on macOS, WebKitGTK on Linux,
Safari in the headless/VPS browser case — do NOT attach `HttpOnly` /
`SameSite=Strict` cookies to WS handshakes (long-standing WebKit behavior;
same engine family, same drop). `SurfaceSecurity` then rejected the handshake
**before the accept**, which every browser surfaces as an opaque close code
1006 — indistinguishable from "server down" — so the frontend's
`wsWarming`/`connected` state machine rendered a permanent OFFLINE. Fetch/XHR
requests DO carry the cookie on WebKit, which is why everything REST kept
working and made the failure look like a cosmetic boot-status bug.

**Why no cookie-attribute tweak can fix it.** Dropping `SameSite=Strict`
weakens CSRF posture on every engine, and Apple-forum forensics show the
`HttpOnly` flag alone suppresses the cookie on WS — removing THAT would expose
the session token to any injected script. The transport, not the cookie, had
to change.

**Fix (2026-07-16) — engine-agnostic WS auth + readable close codes.**

1. **Readable rejects.** `SurfaceSecurity._reject` now accept-then-closes
   websockets, so a browser reads the real `4401`/`4403` instead of 1006.
2. **One-time tickets.** `POST /api/ui/ws-ticket` (implemented inside
   `SurfaceSecurity`, like the session exchange, so it exists identically
   behind the fast-boot bootstrap and the full app) mints a single-use,
   60 s ticket for any cookie/bearer-authenticated caller. A websocket may
   present it as `?ticket=` — consumed atomically, Origin required, never an
   HTTP credential, and (mirroring the session exchange's
   `is_secure_or_loopback` rule) both minting and presentation refuse
   sniffable plain-HTTP non-loopback transports. The boundary stamps a
   ticket-authenticated scope (`WS_TICKET_SCOPE_KEY`) so route-level
   defense-in-depth re-checks (`credentials_valid` in `/ws/audio`, workspace
   PTY) recognize the already-consumed ticket instead of closing 4401 on
   exactly the WebKit clients the ticket exists for. The frontend `WSClient`
   reacts to a 4401 by minting over cookie-authenticated HTTP and
   reconnecting fast (capped at 3 consecutive fast retries, then honest
   offline + escalating backoff); `/ws/audio` mints proactively. A failed
   mint (dead session) still reports the honest offline state.
3. **Warming is credential-free.** The fast-boot bootstrap and the headless
   launcher bootstrap answer EVERY warming websocket with accept-then-close
   1013 — the headless path previously HELD the handshake up to 120 s, which
   browsers time out into the same spurious OFFLINE.

**Guards.** `tests/unit/ui_web/test_surface_security.py` (readable 4401,
ticket mint auth/origin, single-use, expiry, WS-only, hostile-origin);
`tests/unit/ui/web/test_fast_bootstrap_ws.py` (cookie-less warming 1013);
frontend `src/__tests__/ws.test.ts` + `src/hooks/useWebSocket.test.tsx`
(4401 → ticket retry keeps the warming state, failed mint drops it) +
`src/lib/realtimeAudio.test.ts`.

**Class rule.** Never authenticate a browser WebSocket with an
HttpOnly/SameSite cookie alone — one whole engine family silently omits it.
Prove the session over plain HTTP and hand the socket a short-lived,
single-use credential; and never close a websocket before accepting it, or
every specific rejection collapses into an unreadable 1006 that the UI can
only mis-render as "server down".

## BUG-066: Realtime voice — live transcript freezes on the first fragment + spoken reply cut short or fully suppressed (HIGH, FIXED 2026-07-16)

**Symptoms (live incidents 2026-07-15/16, maintainer report).** (1) The
desktop sidebar's live-transcript box showed only "Was" while the user had
asked a full question — and kept showing it long after the session ended.
(2) gemini-live: the reply TEXT was fully stored, but the spoken audio
stopped mid-answer and the session ended `reason=error`
(session `dced755b`, 36.4s spoken of a ~40s answer). (3) grok-realtime:
three sessions in one morning produced an EMPTY spoken reply — the turn was
cancelled by barge-in and the server's follow-up answer never played
(sessions `3cefaede`, `30c532cb`, `9fbcb348`).

**Root causes (six, individually verified).**
1. *Store/UI truncation at capture:* xAI's server VAD ignores the
   `silence_duration_ms=1500` we send in the session contract and commits an
   utterance mid-sentence after ~200ms of micro-pause ("Was", "Constable.",
   "Can you please"). Server-side; cannot be fully fixed client-side.
2. *Stale sidebar:* the frontend event store's `transcription` field had NO
   reset path — the last utterance survived into IDLE and the next session,
   masquerading as a frozen live transcript (`useWebSocket.ts`).
3. *Raw-chunk publishing (latent):* `RealtimeVoiceSession` published the raw
   per-chunk transcript instead of its own accumulated `_last_user_text`;
   every 1:1 mirror (Sidebar, TranscriptionView, SessionRecorder) would show
   only the last fragment of a multi-final turn (`session.py:1067`).
4. *Silent server truncation:* `gemini_live.py` discarded
   `turn_complete_reason` — every named enum value except UNSPECIFIED is an
   abnormal stop (safety filter, regeneration limit...), so a
   server-truncated reply looked like a clean `turn_complete`.
5. *Fatal teardown drops speakable audio:* Gemini `go_away` (a courteous
   pre-disconnect notice) was mapped to a terminal error; the terminal-error
   branch never released transcript-cleared audio still held by the scrub
   gate; the pipeline `finally:` hard-cancel()ed the playback queue instead
   of draining already-safe PCM.
6. *Answer suppressed as "unsolicited" (BUG-064 follow-up):* after a barge-in
   cancel cleared `_pending_response_markers`, the server's auto-generated
   answer to the user's real (heard but never transcribed) utterance matched
   no marker and was cancelled — the turn stayed silent until manual hangup.

**Fixes.** Frontend: clear `transcription` at IDLE session boundaries
(`useWebSocket.ts`). Session: publish accumulated snapshots
(`session.py:1067`); finalize + emit the scrub-gate tail on terminal provider
errors (`session.py:1417`). Gemini: `go_away` → `recoverable=True`; log
abnormal `turn_complete_reason` (`gemini_live.py`). OpenAI/Grok: adopt the
FIRST unsolicited response when the server heard the user speak
(speech_started / committed / barge-in) WITHOUT a subsequent input
transcript; an input transcript re-enables suppression so the benign
duplicate race (2026-07-15) stays covered (`openai_realtime.py`). Pipeline:
bounded `finish_turn()` drain before `playback.close()` on non-shutdown
teardown; Orb bridge: accept a FINAL user transcript during THINKING while
no reply text exists (`pipeline.py`, `bus_bridge.py`).

**Guards.** `test_transcript_persistence.py` (snapshots + gate-tail-on-error),
`test_gemini_live.py` (go_away recoverable, abnormal reason logged),
`test_openai_realtime.py` (adopt vs. suppress matrix),
`test_orb_listening_transcript.py` (late-final repaint),
`useWebSocket.test.tsx` (transcript reset at session boundaries).

**Lesson.** A voice turn has three independent tracks — captured transcript,
generated text, spoken audio — and each can silently diverge from the other
two. Persist and display from ONE accumulated source of truth, never from
raw wire chunks; treat every provider "done" signal as carrying a REASON that
must be read; and never let a suppression/safety path discard the only answer
a committed user turn will ever get without a salvage check.

## BUG-067: Computer-Use aborts every mission — a thinking-by-default model eats the 320-token action budget and the JSON reply arrives truncated (HIGH, FIXED 2026-07-16)

**Symptoms (voice session 2026-07-16 10:37 + every CU mission that
morning).** "Bediene meinen Computer → Discord-Announcement" answered <!-- i18n-allow: quoted German maintainer trigger phrase -->
"Ich konnte keine gueltige Bildschirm-Antwort bekommen und habe gestoppt." <!-- i18n-allow: quoted German runtime phrase under forensic analysis -->
Log per step: `[cu] unparseable model reply (step N): unterminated JSON in
the model reply` (or `no JSON object/array`), three strikes → exit 2. Four
missions failed identically between 08:06 and 10:37; the last good run was
2026-07-15 19:55 on the SAME model. The maintainer suspected the newly
shipped CU screen indicator (landed 20:59 that evening) — pure time
correlation, the indicator was innocent (on Windows its windows carry
`WDA_EXCLUDEFROMCAPTURE` and never enter the frame).

**Root cause (reproduced 1:1 against the live API).** The CU step call caps
`max_tokens=320` (`_DECIDE_MAX_TOKENS`) — sized for a small JSON action.
`gemini-3.5-flash` (a PREVIEW alias) turned thinking-by-default server-side
overnight, and Gemini counts internal "thoughts" against `max_output_tokens`:
the repro showed `thoughts=304, candidates=12, finish=MAX_TOKENS`, visible
text `{"action": "open_app", "name": "` — exactly "unterminated JSON". With
`thinking_budget=0` the same call answered cleanly in 15 tokens. No layer
requested minimal reasoning and no layer read `finish_reason`, so a
budget-starved reply was indistinguishable from a garbage reply and burned
the LLM-failure budget.

**Fixes (all provider-agnostic, all OSes).**
1. `BrainRequest.reasoning_effort: "none" | None` — a capability hint, not a
   provider switch (`core/protocols.py`). CU sets it on every vision call
   (`cu/brain_call.py`).
2. `GeminiBrain` maps the hint to `thinking_config(thinking_budget=0)`; an
   explicit constructor budget wins; a thinking-mandatory model that 400s
   ("only works in thinking mode") is retried ONCE without the field —
   capability recovery, no model-name pin (AP-21)
   (`plugins/brain/gemini.py`).
3. Truncation net for every provider WITHOUT a thinking knob: when a reply
   is length-truncated (`is_length_truncated`) and holds no complete JSON,
   `_try` retries ONCE on the same provider with `max(2048, 4×max_tokens)`;
   the early-stop aggregator still cuts at the JSON boundary, so the higher
   ceiling costs nothing on success (`cu/brain_call.py`).
4. Drive-by (found during the deep-dive, macOS/Linux only):
   `indicator/capture_guard.py` resumed its generator into a second `yield`
   when the unblank hook raised — `@contextmanager` turns that into
   `RuntimeError: generator didn't stop`, killing the frame grab the guard
   exists to protect. Rewritten to enter/exit the hook manually, fail-open.

**Guards.** `tests/unit/cu/test_brain_call_truncation_retry.py` (retry
matrix + reasoning hint), `tests/unit/brain/test_gemini_reasoning_effort.py`
(mapping, precedence, 400-recovery), extended
`tests/unit/cu/indicator/test_capture_guard.py` (double-yield regression).

**Lesson.** Any fixed small `max_tokens` on a structured-output call is a
time bomb once the serving model can spend that budget on internal
reasoning — and a PREVIEW model alias can start doing so overnight with no
code change on our side. Structured-output calls must (a) request minimal
reasoning as a capability hint and (b) treat `finish_reason=length` as its
own recoverable failure class (retry with headroom), never as generic model
garbage. When a regression correlates with a feature landing, check what
ELSE changed in the window — including the SERVER side of a pinned alias.


## BUG-068: Realtime delegate turn dies silently — a silent Gemini vetoes the dispatch, then a phantom `interrupted` edge kills the failure readback too (HIGH, FIXED 2026-07-16)

**Symptoms (voice session 2026-07-16 10:26, gemini-live).** Turn 2 ("Was ist <!-- i18n-allow: quoted German user utterance under forensic analysis -->
morgen für ein Tag?") produced no spoken answer at all: the transcript view <!-- i18n-allow: continuation of the quoted German utterance above -->
recorded the canned reply "Das hat gerade nicht geklappt." with spoken time <!-- i18n-allow: quoted German runtime phrase under forensic analysis -->
"--", the bar showed THINKING for ~4 s and then fell back to LISTENING
without a word. The brain never ran (no dispatch logs). The identical
question one turn later worked (the provider natively called
`jarvis_action`).

**Root cause — two independent defects in the deterministic delegate.**
1. *Boundary-timeout veto.* The local-evidence dispatch waits
   `_DELEGATE_INPUT_BOUNDARY_WAIT_S` for the provider to confirm the input
   boundary (held turn_complete / native tool call). Gemini stayed
   completely silent for turn 2 (no response, no tool call, no boundary —
   it had just been barged-in mid-reply), and the timeout fallback read
   `turn_state.input_final`, which the local-evidence path never sets. The
   dispatch was VETOED: the brain never saw a perfectly complete, stable
   transcript, and the canned failure phrase became the turn's reply.
2. *Phantom `interrupted` kills the readback.* 16 ms after the failure
   result was injected (`send_realtime_input(text=...)`), a spontaneous
   `interrupted` edge arrived (Gemini server VAD; its only barge-in signal —
   the adapter never emits `speech_started`). The endpoint protection
   (`_pending_delegate_needs_endpoint_protection`) deferred only
   `speech_started`, so the `interrupted` edge closed the turn immediately:
   ResponseGenerated recorded the never-spoken reply, the turn state fell to
   LISTENING, and `_drop_provider_output_until_new_response = True` swallowed
   the provider readback of that very reply. For Gemini the protection was
   structurally dead.

**Fixes (jarvis/realtime/session.py).**
1. `_await_stable_input_boundary`: a missing provider boundary DELAYS the
   dispatch but can no longer veto it — after a full wait window in which
   the accumulated input transcript did not grow, the utterance is final by
   local evidence and the brain dispatches on the stable snapshot; a still-
   growing transcript re-arms the window (max
   `_DELEGATE_INPUT_BOUNDARY_MAX_ROUNDS`). The canned-refusal branch is gone.
2. The endpoint-protection deferral now covers `interrupted` as well, plus a
   new `_delegate_readback_awaits_first_audio()` window (result delivered,
   zero PCM audible yet): an unconfirmed VAD edge during any silent span of
   a delegated action is deferred and confirms itself only through a final
   input transcript (the existing deferred-split path).
3. Observability for audible mid-reply holes (same session, turn 3 had a
   ~1 s gap mid-sentence with no attributable log line): `_emit_audio` now
   logs stalls >= 400 ms with the scrub-gate hold time (late transcript vs.
   silent provider) and detects embedded-silence runs inside provider PCM;
   `ScrubHoldGate` exposes `pending_audio_ms` / `last_hold_ms`.
4. Readback watchdog (`_verify_delegate_readback`): delivering a delegate
   result does not force the provider to render it — Gemini's
   `send_realtime_input(text=...)` carries no turn-end signal, so an
   injected result prompt may never start a response generation, and a
   transport that died mid-turn renders nothing either. When no readback
   becomes audible within `_DELEGATE_READBACK_WAIT_S` the surface TTS
   speaks the trusted reply itself; `surface_fallback_spoken` withholds a
   late provider rendering so nothing is heard twice, and the turn-complete
   hold ends at delivery (`readback_verification_active`) so the readback's
   own boundary still publishes the turn normally.

**Guards.** `tests/unit/realtime/test_delegate_endpointing.py`
(`test_silent_provider_boundary_timeout_dispatches_instead_of_refusing`,
`test_phantom_interrupted_edge_defers_while_delegate_pending`,
`test_phantom_interrupted_after_delivery_keeps_the_readback`,
`test_undelivered_readback_falls_back_to_surface_tts`).

**Lesson.** A safety wait may DELAY an action on missing evidence, never
convert missing evidence into a guaranteed failure — "provider said nothing"
and "input incomplete" are different states, and only the second may block.
And every provider-edge policy must be checked per adapter vocabulary: a
guard keyed to an event type one adapter never emits is a guard that does
not exist for that adapter.

## BUG-069: Realtime speech chopped mid-word — the scrub gate's one-release-per-transcript-delta credit starves audio (HIGH, FIXED 2026-07-17)

**Symptom (maintainer report + live session 2026-07-17 08:30, gemini-live).**
Spoken replies sound chopped: a word is cut mid-syllable ("Win … ter"), the
voice goes silent for seconds mid-sentence, then the rest of the answer
plays; occasionally an answer aborts entirely into the generic failure
phrase. Worst near the start of a reply. This is the general form of
BUG-068's "Bug 2" (the ~1 s audible hole), whose instrumentation was built
exactly for this recurrence.

**Evidence.** The `_note_audio_flow` probe (added for BUG-068) attributed
both stalls of the 08:30 session on its first real occurrence:
`mid-reply audio stalled 5264 ms … (scrub-gate hold 5250 ms)` and
`stalled 7046 ms … (hold 6953 ms)` — "the transcript needed to clear this
audio arrived late". Not a silent provider, not a playback underrun: OUR
scrub gate held decoded audio while waiting for provider transcription.
A direct Gemini Live probe (scratch script, one text turn, timestamped
events) then showed the provider-side cadence: the ENTIRE 514-char reply
transcript arrived as ONE `output_transcription` delta alongside the first
audio chunk, followed by a pure audio stream. Live sessions have also shown
the opposite skew (output transcription >5 s BEHIND the audio, incident
2026-07-16 11:24). Conclusion: no realtime provider paces its transcript
deltas against its audio deltas — any gate release policy keyed to delta
ARRIVAL COUNT instead of vetted text QUANTITY starves.

**Root cause (jarvis/realtime/scrub_gate.py).** The ADR-0010 audio-hold
gate released audio per-delta: a clean transcript delta set a one-shot
`_cleared` flag which released everything buffered plus exactly ONE
subsequent chunk, then closed again. With Gemini's en-bloc up-front
transcript that meant: first chunk plays, every later chunk buffers until
the next transcript delta — which never comes until turn end (or comes
seconds late). The audible result is precisely the reported word-splitting
(release boundary falls mid-word) and the multi-second holes. The
secondary abort: `_MAX_UNSCRUBBED_AUDIO_MS = 5_000` sat BELOW Gemini's
routine transcription lag, so a healthy answer whose transcript ran >5 s
behind was dropped wholesale and replaced by the failure phrase
("manchmal bricht es einfach ab"). <!-- i18n-allow: quoted German maintainer report -->

**Fixes.**
1. *Coverage budget (scrub_gate.py).* Release accounting now counts vetted
   text QUANTITY: every transcript char that passed `scrub_for_voice` funds
   `_COVERAGE_MS_PER_CHAR = 55.0` ms of audio; a chunk flows immediately
   while cumulative `released_ms + chunk_ms` stays inside that budget. The
   rate is deliberately FASTER than any real voice (~18 chars/s vs a
   measured ~14), so the budget UNDERESTIMATES the spoken duration of the
   vetted text — released audio cannot outrun the span the scrubber
   already cleared. The legacy behavior is preserved verbatim as the floor:
   a clean delta still clears everything buffered before it plus one
   subsequent chunk; residue-only aggregates still fund nothing
   (`_coverage_active`); hard leaks still drop everything; audio with no
   transcript at all still fails closed. Cross-platform pure integer math,
   no LLM (AP-11), no I/O.
2. *Stall abort threshold (session.py).* `_MAX_UNSCRUBBED_AUDIO_MS`
   5 000 → 15 000: covers the observed 5-7 s lag with 2x margin; memory
   cost is trivial (~1 MB PCM). Deliberately not larger — this bound is
   also the ceiling on how much never-transcribed PCM `finalize()` could
   flush at a turn boundary whose transcription died mid-turn, and
   `finalize()` now logs any tail released far beyond the coverage
   estimate so that scenario names itself in the log.
3. *Trusted-reply fallback at turn completion (session.py).* With the 5 s
   trip no longer firing first, a delegate readback whose transcription
   never arrives now reaches the turn-complete fail-closed path — which
   used to speak the generic failure phrase over OUR OWN already-delivered
   brain reply. That path now hands the trusted reply text to the surface
   TTS (same contract as the pending-buffer trip: `surface_fallback_spoken`
   dedupe + withheld late provider rendering). Review hardening: all three
   direct-to-surface fallback sites now pass the raw Brain reply through
   `scrub_for_voice` before it reaches TTS (ADR-0010 — the normal path
   only ever speaks it via the provider re-render through the gate), and
   the delivered-flags are set only when the cancel will actually speak
   (a second cancel in one turn is a logged no-op, not a silent reply
   loss).

**Guards.** `tests/unit/realtime/test_scrub_gate.py`
(`test_en_bloc_upfront_transcript_keeps_audio_flowing`,
`test_budget_exhaustion_buffers_audio_beyond_vetted_text`,
`test_clean_delta_credit_still_releases_one_chunk_beyond_budget`,
`test_residue_only_transcript_never_activates_the_budget`, plus the
updated later-segment-leak and response-boundary tests);
`tests/unit/realtime/test_session.py`
(`test_later_segment_leak_audio_not_emitted`,
`test_scrub_trip_during_delegate_readback_speaks_trusted_reply`).

**Lesson.** A gate between two streams that the producer does not pace
against each other must meter by QUANTITY (how much content has been
vetted), never by event ARRIVAL (how many vetting messages have been
seen). Arrival-keyed credits encode an interleaving assumption no provider
guarantees — and every violation of that assumption is audible. Second
lesson, again: safety bounds sized to "normal" provider behavior
(5 s of pending audio) become answer-killers the day the provider is
routinely slower; bound on resource cost, not on optimism.

## BUG-070: User probes the silent wait ("hello?") — the provider greets like a fresh conversation while the delegated answer is still being computed (HIGH, FIXED 2026-07-17)

**Symptom (maintainer report + live session 2026-07-17 09:21, gemini-live).**
Mid-conversation, the answer to a follow-up question starts and dies after
two words; the user probes the silence with a bare greeting, and the
assistant replies with a fresh-conversation greeting as if the whole
exchange never happened. The user hangs up; the real answer never arrives
in any form. The exported transcript shows: user question → two-word
assistant fragment → user "hello?" probe → context-free greeting.

**Evidence (data/jarvis_desktop.log, session 9ef10423, 09:23:26-09:23:57).**
The provider called `jarvis_action` for the user's question (`delegate
call: dispatching user turn to the router brain`, 09:23:26) and kept
speaking natively; the scrub gate released only the first words because
the provider's output transcription ran >10 s behind its audio (the
BUG-069 lag mode — an earlier reply in the same session logged
`mid-reply audio stalled 14718 ms … scrub-gate hold 14671 ms`). Into that
hole the user said a bare greeting; the barge-in killed the native reply;
the provider answered the probe with a fresh greeting (no delegate
dispatch logged — 5 ms turn). The router-brain answer was still in flight
when the user hung up at 09:23:57; `end()` cancelled the delegate task
with no log line — the answer vanished without a trace.

**Root causes (jarvis/realtime/session.py).**
1. A thin presence probe ("hello?", "are you still there?") spoken while
   an earlier turn's delegated action is still executing was handled by
   the provider like any other turn. `_DELEGATE_PENDING_DIRECTIVE`
   already tells the model to say it is still working, but prompt
   compliance is not a correctness boundary (BUG-047 class rule), and the
   live model demonstrably greeted instead.
2. `end()` cancelled still-running delegate tasks silently: a hangup
   during a slow delegated answer discarded the request with no evidence
   in the log (the queued-late-result and flush paths log their losses;
   the still-running case logged nothing).

**Fixes.**
1. *Deterministic presence-check status line.* A final user transcript
   that (a) matches a closed multilingual presence-probe vocabulary
   (`_is_presence_check`: de/en/es greetings and are-you-there cores,
   ≤ 5 words, a lone filler like "ja"/"yes" deliberately excluded) while
   (b) an earlier turn's delegate task is still running
   (`_has_pending_delegate_from_earlier_turn`) and (c) the turn itself
   requires no orchestrator dispatch, is answered by the ORCHESTRATOR:
   one progress line from the closed bridge pool through the surface TTS
   (`error_spoken`), the provider's freestyle response for that turn is
   dropped (`_drop_provider_output_until_user_turn`), and no provider
   response is requested. The late-result flush still speaks the real
   answer once the session is at rest — its injection path clears both
   drop flags. A probe with no pending action stays fully native.
2. *Named loss on hangup.* `end()` now logs one WARNING per turn whose
   delegate task is still running at session end, naming the user request
   whose answer is being cancelled.

**Guards.** `tests/unit/realtime/test_session.py`
(`test_presence_check_vocabulary_matches_probes_only`,
`test_presence_check_during_pending_action_gets_status_line_not_provider`,
`test_presence_check_without_pending_action_stays_native`,
`test_session_end_names_the_delegated_request_it_cancels`).

**Lesson.** When a slow background action leaves the voice channel
silent, the user WILL speak into that silence to test for life — and that
probe is the one turn where a freestyle model answer is guaranteed to be
wrong (it either invents an outcome or resets the conversation). Every
turn category whose only correct answer is known in advance belongs to
the orchestrator, not the model. And: any path that discards work the
user asked for — even legitimately, on hangup — must say so in the log,
or the next forensic starts from nothing.

## BUG-071: Random auto-hangup mid-call — the provider drops the Live WebSocket and the whole session ends with "error" (HIGH, FIXED 2026-07-17)

**Symptom (maintainer report + live session 2026-07-17 10:42,
gemini-live).** The call hangs up by itself. The user said no hang-up word,
did not close the app, and touched nothing — the session just ends. The
exported transcript shows `Beendet durch: Fehler` <!-- i18n-allow: quoted German session-export field under test -->
with a completely normal conversation above it. Intermittent: most runs are
fine, some die.

**Evidence (data/jarvis_desktop.log, session 5e553e27, 10:42-10:44).**
Turn 2's reply tripped the scrub-gate abort (`output transcript exceeded
safe audio buffer`, 10:43:15) and the trusted answer was correctly re-spoken
through the realtime-scoped surface TTS for ~69 s. During that playback the
half-duplex echo guard uploads no microphone audio and the interrupted
provider streams nothing, so the Live WebSocket sat fully idle both ways —
and Google closed it: `pump ended` + `provider_error: 1006 None. abnormal
closure [internal]` at 10:44:24, the exact second playback finished. The
desktop pipeline then refused the classic-replay fallback (a committed turn
makes replaying the capture buffer unsafe — that guard is correct) and ended
the call with `reason=error`.

**Root cause (jarvis/realtime/session.py).** The receive pump treated EVERY
transport end as terminal: one `async for` pass over `session.receive()`;
an exception or a silent iterator end set `_failed` and finished the pump,
and the desktop surface translates a finished pump after a committed turn
into a hang-up. A provider-side WebSocket drop — which Gemini does on its
own schedule (session limits/GoAway, abrupt 1006 closes, idle timeouts) —
therefore ended the whole call, even though the session object could simply
have opened a new transport. The BUG-064 transport-rebuild lesson existed
only INSIDE the openai adapter; gemini-live (SDK-managed socket, no
in-protocol resume) had no equivalent, and the orchestrator had none either.

**Fix (capability-gated in-place transport rebuild, orchestrator level).**
`RealtimeVoiceSession._pump` is now a reconnect loop over
`_pump_transport_once()`. When a transport dies — receive raising, or the
iterator ending without a boundary, mid-turn or idle — and the dead session
declares `rebuild_on_transport_death = True` (a capability attribute, never
a provider name — AP-21; `_GeminiLiveSession` sets it), the orchestrator
closes the dead session, freezes the open turn into the persisted record,
resets per-turn output state, re-runs `_open()` (the key-aware provider
chain — a dead family crosses to the next, AP-22 for free), and re-announces
`audio_ready` so playback and surface labels follow the possibly-new
provider/rates. Mid-turn deaths still release the transcript-cleared audio
tail first. In-provider conversation history is lost — strictly better than
a dead call; the orchestrator-side delegate history survives. Deliberate
ends never rebuild: session end, voice hangup, and an acknowledged
`end_call` (a death there converts to the requested hangup, not an error).
The budget is rate-based (3 rebuilds per rolling 120 s), so a long call
outliving several provider session limits keeps healing while a flapping
transport fails honestly with one terminal `provider_error`. Sessions
without the capability (openai_realtime self-heals internally and declares
terminal deliberately; test doubles) keep the old terminal semantics
exactly. `handle_audio_frame` additionally drops a microphone frame whose
send hits the just-died socket instead of raising — a raise there killed
the desktop mic pump, which is its own path into `reason=error`.

**Guards.** `tests/unit/realtime/test_session.py`
(`test_transport_death_rebuilds_the_session_in_place`,
`test_transport_death_without_capability_keeps_terminal_semantics`,
`test_transport_rebuild_storm_fails_honestly`,
`test_idle_stream_end_with_capability_rebuilds_instead_of_ending`,
`test_mid_turn_stream_end_with_capability_salvages_then_rebuilds`,
`test_transport_death_after_end_call_converts_to_hangup`).

**Lesson (BUG-064 class rule, final form).** A duplex provider's transport
WILL die mid-call for reasons no client controls; the session and the
transport are different lifetimes, and only the adapter knows whether a
fresh transport can continue the session. Ending the call because a socket
closed is answering the wrong question. And the trigger chain matters: a
long LOCAL fallback playback starves the provider socket of traffic in both
directions — every recovery path that takes the voice surface away from the
provider for tens of seconds must assume the provider connection may not
survive the silence.

## BUG-072: Delegated realtime turns take 20-30 s — thinking on every tool round, per-turn cache churn, and text-leaked tool calls stack up (HIGH, FIXED 2026-07-17)

**Symptom (maintainer report 2026-07-17).** Every routed voice action or
research question ("Was gibt es aktuell für Bugatti Divos in Europa?") <!-- i18n-allow: quoted German maintainer utterance -->
takes 20-30 s to come back, while the same question typed into the model
vendor's own chat UI answers in ~2 s. FlightRecorder ground truth for the
day: delegate span p50 15-18 s, p90 22 s, worst 33.2 s; turns regularly ran
into the 20 s `_DELEGATE_DEADLINE_S` and were force-finalized.

**Evidence (data/flight_recorder/2026-07-17.jsonl + jarvis_desktop.log,
turn 20000012 10:21).** Five sequential tool rounds (wiki-recall ×3 →
wiki-list → wiki-page-read) over a ~53k-token context; a fresh Gemini
context cache created mid-turn (~1.5 s) plus a SECOND cache for the
deadline-forced tools-stripped round; rounds 4-5 arrived as
`tool_use_loop: recovered 1 text-serialized tool call(s)`.

**Root causes.**
1. **Thinking on every round.** The tool loop's per-round `BrainRequest`
   never set `reasoning_effort`, and the router-tier factory caps
   `thinking_budget=0` only on the tier's OWN provider entry — after a live
   provider switch the cap sits on the wrong entry and the hoisted Tool
   Model (thinking-by-default Gemini Flash) reasons for seconds per round.
2. **Single-slot context cache.** The manager legitimately varies the tool
   set per utterance (screen-tool gating) and the deadline round strips
   tools; each flap between the recurring (system, tools) variants
   re-created the server-side cache.
3. **Self-teaching text leak.** `_to_gemini_contents` JSON-dumped assistant
   tool_use turns into plain text, so from round 2 the model saw its own
   prior calls as prose and mimicked the format — every later call went
   through the lossy leak-recovery parser. Native replay in turn requires
   the Gemini 3 `thought_signature` contract (400 without it).
4. **Round-count blindness.** Nothing told the delegated model that rounds
   are seconds: independent lookups ran one per round and near-duplicate
   searches re-ran.

**Fixes (commits ed1f40af, d42bbb43, 174e626e).** Delegated dispatch passes
`reasoning_effort="none"` through dispatcher and loop onto every round
(Gemini maps it to thinking_budget=0 with the thinking-mandatory retry;
OpenRouter maps it to the gateway reasoning parameter, fail-open); the
Gemini context cache keeps a bounded multi-slot map per (system, tools)
variant; tool history replays natively with captured thought signatures
(signature-less calls keep the proven text form); delegated system prompts
append a static speed contract (batch lookups into one round, never repeat,
answer once evidence suffices).

**Measured (end-to-end delegated dispatch, gemini-3.5-flash, live key).**
Research question 17.7 s → 12.5 s brain-side with zero leak recoveries;
weather question 5.2 s → 4.5 s; single delegate-shaped round 3.32 s →
2.13 s. Live per-turn re-verification pending the next voice sessions via
FlightRecorder.

**Guards.** `tests/unit/brain/test_tool_use_loop_reasoning_effort.py`,
`test_openrouter_reasoning_effort.py`, `test_gemini_cache_slots.py`,
`test_delegate_voice_directive.py`, `test_gemini_native_tool_history.py`.

**Lesson.** On a conversational voice path, latency is a stack of per-round
multipliers: internal reasoning × round count × (cache misses + leaked
calls). Cap reasoning at the REQUEST level (config-level caps drift onto
the wrong provider entry), key caches on every legitimately-varying axis,
and never feed a model a serialized imitation of its own native call
format — it will copy it.

## BUG-073: In-app local-speech install always fails with "No module named pip" — uv-created venvs ship without pip (HIGH, FIXED 2026-07-17)

**Symptom (maintainer report 2026-07-17).** Enabling the local speech pack
from the wake-word settings fails every time with
`pip exited 1: <venv>/Scripts/pythonw.exe: No module named pip`, on the
maintainer's Windows box AND on the real-Mac test run. The UI's generic
hint ("usually no prebuilt package for this Python/system") pointed at the
wrong cause.

**Root cause.** `install_pip_package` runs `sys.executable -m pip install`
unconditionally — but environments created by `uv venv` deliberately omit
the pip module (uv installs from outside the env), so the invocation dies
before it can install anything. Both affected machines run uv-created
venvs (`pyvenv.cfg`: `uv = 0.11.19`). The official `install.ps1/.sh` path
uses `python -m venv` (pip included), which is why the wizard never hit it;
any uv-based setup — increasingly the ecosystem default — was structurally
broken for every in-app install, violating the §3 "recoverable in-app"
contract.

**Fix (jarvis/setup/dependencies.py).** The pip attempt stays the primary
path (environments with pip pay zero extra subprocess calls). On the exact
`No module named pip` failure a recovery chain runs: (1)
`<python> -m ensurepip --upgrade` — the stdlib bootstrap installs pip INTO
the environment, a permanent repair — then the pip install is retried;
(2) if ensurepip cannot help, `uv pip install --python <sys.executable>`
with the on-PATH uv binary (near-certain to exist given a uv-created
venv), propagating `--only-binary`; (3) otherwise an actionable failure
message naming the manual `ensurepip` command. `classify_pip_failure`
additionally learned uv's empirically-captured wordings for the no-wheel
("No solution found when resolving", "has no usable wheels") and network
("Failed to fetch", "error sending request") diagnoses so the BUG-059
honest-diagnosis contract holds on the uv path too.

**Guards.** `tests/unit/setup/test_install_without_pip.py` (bootstrap
chain, uv fallback + flag propagation, actionable no-recovery message,
single-subprocess happy path, uv wording classification).

**Lesson.** "python -m pip" is NOT a universal invariant of a Python
environment — uv-created venvs (and stripped system Pythons) don't have
it. Any runtime code that shells out to pip must treat "No module named
pip" as a repairable state (ensurepip / uv fallback), not a terminal
error, or every in-app install silently bricks for the growing uv-managed
install base.

---

## BUG-074: JarvisBar/mascot host died instantly on macOS — pyobjc NSApplication before Tk 9 init (HIGH, FIXED 2026-07-17)

> Shipped in the 2026-07-17 public-repo (Mac session) commits as "BUG-067";
> renumbered on integration — the local register had already assigned BUG-067
> to an unrelated Computer-Use bug.

**Symptom.** On the freshly installed Intel Mac the bar never appeared;
`jarvis_desktop.log` repeated "JarvisBar host not ready within 3.0s" /
"JarvisBar host process is gone". Running the host by hand showed a native
abort (SIGABRT) inside `libtcl9tk9.0`: `-[NSApplication macOSVersion]:
unrecognized selector`.

**Root cause.** The host hid its Dock icon via pyobjc
(`NSApplication.sharedApplication().setActivationPolicy_(1)`) BEFORE creating
the Tk root. Tk 9's aqua backend calls selectors that only exist on Tk's own
`TKApplication` subclass of NSApplication; when pyobjc has already
instantiated a plain `NSApplication` as `NSApp`, `Tk()` aborts natively.
Tk 8.6 tolerated this order — but uv's python-build-standalone CPython (the
interpreter the installer provisions on Intel Macs / Python-3.14 systems)
bundles **Tk 9.0**, so every fresh macOS install hit it. Reproduced
minimally: `AppKit.NSApplication.sharedApplication()` then `tkinter.Tk()`
crashes; `Tk()` first, AppKit second works.

**Fix (2026-07-17).** `_hide_dock_icon()` now creates a withdrawn bootstrap
`tkinter.Tk()` root FIRST (kept alive for the host's lifetime) so Tk owns
`NSApp`, then applies the accessory activation policy. darwin-only code
path; Linux/Windows byte-identical. Verified live: the host reaches
`{"event": "ready"}` and the Tk mainloop keeps running.

**Class rule.** In any process that will run Aqua-Tk, Tk must be the first
framework to touch `NSApplication`. Never call a pyobjc/AppKit API before
the first `Tk()` in Tk-hosting subprocesses — and treat "works with Tk 8.6"
as unproven for Tk 9.

---

## BUG-075: solid grey box around the bar/mascot on macOS — Tk 9 no longer maps systemTransparent to a clear backing (MEDIUM, FIXED 2026-07-17)

> Shipped in the 2026-07-17 public-repo (Mac session) commits as "BUG-069";
> renumbered on integration — the local register had already assigned BUG-069
> to an unrelated realtime-speech bug.

**Symptom.** The JarvisBar rendered inside an opaque grey rectangle on the
freshly installed Mac (screenshot from the maintainer's first live session).

**Root cause.** The Aqua-Tk transparency recipe (`wm attributes -transparent`
+ `systemTransparent` background) is a Tk 8.6 behavior. Tk 9 — bundled by
uv's python-build-standalone, i.e. every fresh macOS install — accepts both
calls without error but paints `systemTransparent` as an opaque appearance
color, so the window backing shows as a grey box around the RGBA artwork.
Silent break: no TclError, so the key-color fallback never fired.

**Fix (2026-07-17).** After the Tk window exists, the overlay clears the
native backing itself via pyobjc (`NSWindow.setOpaque_(False)` +
`clearColor` background + no shadow) — the Tk-version-independent
equivalent of what Tk 8.6 did internally. Applied in the bar
(`jarvis/ui/jarvisbar/overlay.py::_apply_macos_clear_backing`), the orb
main window, and the comment bubble (`ui/orb/overlay.py::
apply_macos_clear_backing`); darwin-only, best-effort, the grey box is the
degrade. Safe post-BUG-074 (Tk owns NSApp before any window exists in the
host).

**Class rule.** Aqua-Tk cosmetics verified on Tk 8.6 are unproven on Tk 9 —
and the failure mode is silent (no TclError). Where the effect matters,
assert it natively via AppKit instead of trusting a Tk color name.

## BUG-076: macOS app bundle unlaunchable on non-framework (uv standalone) Python — installer exit 4 with the real error discarded (HIGH, FIXED 2026-07-17)

<!-- Ported from the public Mac line, where this entry was numbered BUG-064.
     Renumbered: local BUG-064 is the Grok realtime deafness bug (the two
     registers assigned 063..069 independently after the 2026-07-14 cut). -->

**Symptom.** Fresh managed install on an Intel Mac (and on any system where
install.sh's uv bootstrap provisions Python): the installer aborts with a
bare exit code 4 at the desktop-integration step. No error text names what
actually failed — the terminal shows only the failure itself, so the bundle
build looked like an opaque hard stop.

**Root cause (two layers).**

1. **py2app requires a framework Python.** The BUG-060 hardened bundle used
   a py2app alias stub as its native Mach-O launcher; that stub resolves the
   interpreter through the ``Python.framework`` layout at launch time.
   install.sh's uv bootstrap (taken on Intel Macs and on systems whose
   system Python is 3.14) provisions a python-build-standalone CPython —
   a NON-framework interpreter the py2app stub can never launch. The
   LaunchServices identity probe (BUG-060's own guard) correctly detected
   the unlaunchable bundle and rolled it back — the guard worked; the
   foundation under it was wrong for a whole interpreter class.
2. **The real error was discarded.** ``install/installer.py`` ran the
   desktop-integration subprocess and, on failure, surfaced only the exit
   code — the captured stderr carrying the actual probe/build diagnosis was
   thrown away, so the visible symptom was "exit 4" instead of the cause.

**Fix (2026-07-17).**

- The bundle's native launcher is now an in-repo compiled C stub
  (``jarvis/setup/macos_stub_launcher.c``), replacing py2app entirely:
  ``_resolve_runtime_dylib`` locates the exact runtime libpython/framework
  dylib of the active interpreter (framework AND standalone layouts) and
  ``_build_native_bundle`` compiles + links the stub against it, so the
  bundle launches on whatever Python actually runs the install. The py2app
  dependency is removed.
- ``install/installer.py`` writes the full integration output to
  ``data/logs/install-desktop-integration.log`` and prints the stderr tail
  on failure — the diagnosis is never swallowed again.

Guards: new CI matrix row "Intel standalone (uv)" in
``.github/workflows/macos-desktop.yml`` builds + self-probes the bundle on a
uv-provisioned non-framework Python;
``tests/unit/setup/test_macos_app_bundle.py`` covers
``_resolve_runtime_dylib`` (framework, standalone, and unversioned uv-sibling
layouts) and ``_build_native_bundle``;
``tests/unit/install/test_installer_update_contract.py`` covers the log +
stderr-tail surfacing.

**Class rule.** A launcher that hardcodes one interpreter layout is broken
for every other layout the installer itself can provision — build native
stubs against the RUNTIME the install actually uses, probed at build time.
And an installer must never reduce a failed subprocess to its exit code:
persist and print the captured stderr, or the next such bug is again
undiagnosable in the field.

---

## BUG-077: pynput's darwin keyboard listener aborts the whole app with SIGILL on macOS 15 — hotkeys replaced with a TSM-free Quartz tap backend (HIGH, FIXED 2026-07-17)

<!-- Ported from the public Mac line (numbered BUG-065 there). -->

**Symptom.** During the first real Intel-Mac onboarding (macOS 15.7), the
desktop app died twice within seconds of the hotkey trigger arming — a native
``EXC_BAD_INSTRUCTION`` / ``SIGILL`` crash, not a Python traceback. Crash
reports show ``dispatch_assert_queue_fail`` under
``TSMGetInputSourceProperty`` called via ctypes from a worker thread.

**Root cause.** pynput's darwin keyboard listener resolves the keyboard
layout through HIToolbox Text Services Manager calls
(``TISCopyCurrentKeyboardInputSource`` / ``TSMGetInputSourceProperty``)
inside its own listener thread (``pynput/_util/darwin.py::keycode_context``,
entered by ``keyboard/_darwin.py::Listener._run``). Modern macOS asserts
that TSM runs on the main dispatch queue and aborts the process —
uncatchable from Python. The BUG-058 permission preflight gated the tap
correctly, but once Accessibility + Input Monitoring were GRANTED the
listener started and the TSM assertion killed the app. The main thread
belongs to pywebview, so the listener can never be hosted there: pynput's
keyboard listener is structurally unusable in this process on macOS 15.

**Fix (2026-07-17).** New ``jarvis/trigger/backends/quartz.py``
(``QuartzHotkeyBackend``): a listen-only ``CGEventTap`` on a dedicated
CFRunLoop thread (taps are legal off the main thread), matching chords by
PHYSICAL key — a fixed ANSI virtual-keycode table plus the CGEventFlags
modifier word. No TIS/TSM call anywhere. Same combo vocabulary, edge
semantics, permission fail-closed gate, and ``received_any_event()`` as the
pynput backend; ``make_hotkey_backend`` selects it on darwin while Linux-X11
keeps ``PynputBackend`` and Windows stays byte-identical (AD-7). Documented
trade-offs: letters match ANSI key positions on exotic layouts;
``right_control`` folds into ``ctrl``.

Guards: ``tests/unit/trigger/test_quartz_backend.py`` (edge semantics,
fail-closed permission gate, degrade without Quartz, keycode-table coverage)
and the factory-selection test in ``test_hotkey_backends.py``.

**Follow-up — the same TSM hole in ``keyboard.Controller`` (2026-07-17).**
The Quartz backend removed pynput from the darwin *hotkey* path, but
``pynput.keyboard.Controller()`` (Computer-Use keyboard actuation,
``jarvis/cu/actuate/posix.py``) builds its keycode map through the identical
``keycode_context()`` TIS calls on whatever thread constructs it — the
backend thread — so the first CU type/press action could still SIGILL the
app. Closed by ``jarvis/platform/macos_input_source.py``: the desktop boot
chokepoint (``run_window_only``, provably main-thread) snapshots the tiny
immutable ``(keyboard_type, layout_data)`` tuple via raw ctypes
(microseconds, AP-26-clean), and ``ensure_pynput_layout_guard()`` patches
pynput's ``keycode_context`` so off-main callers reuse that snapshot and
never touch TIS; with no snapshot available the patched call raises an
ordinary ``RuntimeError`` — the posix actuator's existing except-clause then
drops to the pyautogui fallback instead of the OS killing the process. The
``PynputBackend`` darwin branch (unreachable via the factory since the
Quartz fix, but constructible directly) now also refuses to start its
listener without a main-thread snapshot. Guards:
``tests/unit/platform/test_macos_input_source.py`` (off-main never touches
TIS, cache reuse, no-snapshot raise, main-thread pass-through) and the
degrade tests in ``test_hotkey_backends.py``.

**Class rule.** On macOS, ANY third-party library that touches AppKit,
HIToolbox, or TSM from a background thread is a process-abort risk that a
permission gate cannot catch — the assertion fires AFTER permissions are
granted. Before hosting such a library off the main thread, read its native
call path; if it needs main-queue services the process cannot provide, build
the narrow native path in-repo (Quartz-only, keycode-level) instead of
wrapping the crash in try/except that can never catch a SIGILL.

---

## BUG-078: fresh macOS install crashed at the final launch step — editable .pth invisible to the already-running installer (MEDIUM, FIXED 2026-07-17)

<!-- Ported from the public Mac line (numbered BUG-066 there). -->

**Symptom.** A fully successful fresh install (all six phases green, bundle
registered) ended in `ModuleNotFoundError: No module named 'jarvis'` from
`step_launch` (`install/installer.py`). Update runs never hit it.

**Root cause.** The installer process starts before phase 4 runs
`pip install -e .`. Editable installs are wired through a `.pth` finder hook
that the interpreter only processes at STARTUP, so the long-running installer
process could not `import jarvis` in-process on a fresh install. On update
runs the hook already existed at startup — which is why the crash was
fresh-install-only and invisible on every developer machine.

**Fix (2026-07-17).** `_rescan_venv_site_packages()` (`site.addsitedir` on
the venv's purelib + `importlib.invalidate_caches`) runs before the darwin
launch imports; if the import still fails the launch degrades to
`/usr/bin/open -a "Personal Jarvis"` (LaunchServices by name — BUG-060
conform) instead of failing a completed install. Guard:
`tests/unit/install/test_installer_update_contract.py::test_macos_launch_survives_missing_editable_import`.

**Class rule.** A long-running installer process must never assume it can
import what it just installed — re-scan site-packages first, and never let a
post-install nicety (auto-launch) turn a completed install into a failure.

---

## BUG-079: wake stack crash-looped on a German macOS — the stub launcher's LC_ALL leaked a de_DE LC_NUMERIC into libvosk (HIGH, FIXED 2026-07-17)

<!-- Ported from the public Mac line, where this entry was numbered BUG-068.
     Renumbered: local BUG-068 is a realtime bug (the two registers assigned
     063..069 independently after the 2026-07-14 cut). The Tk-9 JarvisBar
     entry the Mac line filed as BUG-067 already lives here as BUG-074. -->

**Symptom.** On the freshly installed German-locale Mac, "Wake loop failed:
Expecting property name enclosed in double quotes" every ~20-40 s — wake
effectively deaf. Only reproducible with REAL speech audio; synthetic noise
produced empty results and parsed fine.

**Root cause.** The BUG-076 stub launcher (macOS non-framework launcher)
called ``setlocale(LC_ALL, "")`` (copied from py2app's UTF-8 bootstrap). On a
German macOS that sets ``LC_NUMERIC=de_DE`` for the whole process; libvosk
formats its result JSON with printf-family calls, so every word confidence
became ``"conf" : 1,000000`` — a comma decimal separator, i.e. malformed
JSON. A plain ``python`` binary never does this (CPython only touches
LC_CTYPE), which is why no other launch path ever showed it. Diagnosed via
the parse-guard hardening shipped with this same fix: it logged the raw
payload with the commas.

**Fix (2026-07-17).**
- ``macos_stub_launcher.c`` sets only ``setlocale(LC_CTYPE, "")`` — UTF-8
  path/argv decoding is preserved, LC_NUMERIC stays "C" like a normal
  Python process.
- ``_BUNDLE_FORMAT_VERSION`` bumped to 2 so existing bundles rebuild with
  the corrected stub on their next ensure pass.
- Defense in depth: ``jarvis/plugins/wake/vosk_kws_provider.py`` parses all
  recognizer JSON through ``_parse_recognizer_json`` — a malformed payload
  is a logged no-hit (first occurrence carries the raw payload), never a
  wake-loop kill. Guard: ``tests/unit/plugins/wake/test_vosk_result_parse_hardening.py``.

**Class rule.** Never ``setlocale(LC_ALL, ...)`` in a launcher that embeds
Python — mirror CPython and touch only LC_CTYPE; every native library that
prints numbers breaks under a comma-decimal LC_NUMERIC. And any JSON built
by native code is untrusted input to the Python side: parse it behind a
degrade-to-no-op guard that preserves the raw payload.

---

## BUG-080: Realtime voice freezes mid-word for seconds — unbounded scrub-gate hold while the provider transcription lags its audio (HIGH, FIXED 2026-07-18)

**Symptom.** Mid-sentence, the live realtime voice stops dead for 2-15+ s,
then resumes exactly where it paused (maintainer session 2026-07-17 20:04,
turn 1: "Servus, bei" — 4.9 s hole — "mir passt ois…"). <!-- i18n-allow: quoted runtime voice output under test -->
~25 incidents across 2026-07-16/17, worst observed 17.1 s. Log signature:
`mid-reply audio stalled N ms (scrub-gate hold M ms …) — the transcript
needed to clear this audio arrived late`.

**Root cause.** The BUG-069 coverage budget releases audio only against
vetted transcript chars. Gemini Live does not pace its output transcription
against its audio: transcript deltas routinely fall 3-22 s behind. When the
budget ran dry mid-reply, `ScrubHoldGate` held the audio backlog for the
WHOLE lag — an unbounded hold whose only effect was audible dead air,
because at the turn boundary `finalize()` flushes the very same
never-covered tail anyway (same night: a 9.3 s never-transcribed tail was
released at the boundary). The hold was not buying safety, only silence.

**Fix (2026-07-18).** The mid-reply hold is now time-bounded
(`_LAGGING_TRANSCRIPT_GRACE_MS = 400 ms`, `jarvis/realtime/scrub_gate.py`):
once the turn's aggregate transcript has been vetted clean at least once, a
backlog held past the grace window flows even though its own transcript has
not arrived yet. The fail-open is narrow and deliberate: the turn opening
stays strictly fail-closed (nothing plays before the first clean
transcript; `fail_closed()` and the 15 s `fail_if_pending_exceeds` bound
are untouched), and a hard leak in a later transcript delta still cancels
the remaining output. In the healthy co-timed case (transcript <300 ms
behind its audio) the scrubber still vets text before it becomes audible.
Guards: `tests/unit/realtime/test_scrub_gate.py::
test_lagging_transcript_backlog_flows_after_grace` plus three siblings
(no grace before the first clean transcript, none on residue-only
transcripts, kill switch intact after a grace release).

**Class rule.** A safety gate on a live media stream may buffer, but never
unboundedly: bound every hold by the moment the withheld content would
reach the user anyway (here: the finalize() flush), and let the kill
switch — not the hold — be the actual safety mechanism. An unbounded hold
converts a provider lag into a user-facing outage.

**Amendment (2026-07-18, maintainer mandate).** The 400 ms bounded grace
was still audible: while the transcription lagged, the gate metered audio
out in ~400 ms blocks, which the maintainer heard as rhythmic mid-reply
stutter and rejected. Mid-reply release rationing is now removed entirely
(third scheme to fail against provider transcription lag, after the
per-delta credit and the coverage budget — the class rule generalizes:
ANY mid-reply rationing of a live stream converts provider lag into
audible artifacts). Final model: the turn opening stays fail-closed until
the aggregate transcript has been vetted clean once (nothing is audible
yet, so that hold interrupts nothing); from then on audio flows
unconditionally and the scrubber acts purely as a trailing kill switch —
a hard leak in a later delta drops all unplayed audio and cancels the
response. Accepted trade-off: mid-reply audio can be audible before its
own transcript is vetted. Guards updated:
`test_audio_flows_unconditionally_once_the_opening_is_vetted`,
`test_later_segment_leak_still_cancels_after_a_clean_first_segment`,
`tests/unit/realtime/test_session.py::test_audio_after_a_leak_transcript_is_never_emitted`.

---

## BUG-081: Hanging up a realtime call can hang forever — end() loses its one pump cancel to an asyncio race after a transport rebuild (HIGH, FIXED 2026-07-18)

**Symptom.** The full unit suite froze at ~61 % on
`test_idle_stream_end_with_capability_rebuilds_instead_of_ending` — the test
(and the same code path live) hung in `RealtimeVoiceSession.end()`
indefinitely; per-test timeouts killed the whole run. Live equivalent: after
a provider transport rebuild (BUG-071 path), hanging up the call never
completes.

**Root cause.** `end()` cancelled the pump task exactly once and then
awaited it unbounded. When that single `cancel()` lands while the pump's
current waiter future is ALREADY finished (observed: `end()` arriving just
as `_rebuild_transport`'s `_open()` completed — waiter showed
`<Future finished>`), asyncio absorbs the cancellation without ever raising
`CancelledError` inside the coroutine: the task reports `cancelling()=1`,
`_must_cancel` resets, and the pump keeps waiting on the next provider
event. The bare `await self._pump_task` then waits forever. A second
cancel — e.g. the loop teardown — was proven to deliver fine, which
confirmed delivery, not handling, was the failure.

**Fix (2026-07-18).** `end()` re-cancels on a bounded wait (up to 3 ×
`cancel()` + 2 s `asyncio.wait`), logging and abandoning the task only if it
survives all retries; a retry hits the task in a plain suspended await,
where delivery is reliable. Non-cancelled outcomes have their exception
retrieved so nothing is silently lost. Guard: the previously hanging
rebuild tests in `tests/unit/realtime/test_session.py` (133 green, 10 s).

**Class rule.** Never pair a single `task.cancel()` with an UNBOUNDED
`await task` in teardown: cancellation delivery is not guaranteed on the
first attempt when the target's waiter future has already completed.
Teardown must re-cancel on a bounded wait (or otherwise wake the awaited
resource) so a lost cancel degrades to a retry, never a permanent hang.

## BUG-082: Computer-Use aborts mid-mission as "no progress" after one swallowed scroll — phantom scroll success + silent ledger refusals (HIGH, FIXED 2026-07-18)

**Symptom.** A CU mission that ran fast and clean (Chrome → Gmail → open the
LinkedIn email in 5 steps, 07:47 live run) suddenly stopped with
`fail at step-10: no progress — the screen has not changed despite my
actions`, ~5 s after its last visible action. The voice layer then invented a
cause the mission never reached ("the unsubscribe link did not work" — no
unsubscribe link was ever clicked). Flight recorder: steps 7–9 show
observe+think but NO action, no refusal, no log line — the round dies
invisibly.

**Root cause (three interlocking defects).**
1. *No effect-check for scroll:* the wheel event was dispatched (verified
   move + SendInput), Gmail's reading pane never moved (pre/post screenshots
   pixel-identical), yet the engine recorded `scroll ok` into history AND the
   idempotency ledger — clicks are pre/post effect-checked, scrolls were not.
2. *Direction-only ledger key:* `action_key` reduced every scroll to
   `scroll@down`, so each retry the model sensibly proposed on the unchanged
   frame was refused as a duplicate — regardless of position or amount.
3. *Silent refusal paths:* the ledger/staleness/window-signature refusals
   only appended to the model-facing history — no `log.info`, no progress
   chunk, no flight-recorder event. Three silent refusals in a row satisfied
   the `_STUCK_FRAMES` no-progress guard and the mission aborted with a
   context-free reason the realtime voice model then embroidered.

**Fix (2026-07-18).** (a) Scroll is now effect-checked exactly like clicks
(pre/post `grab_region` + `frames_differ`); an ineffective scroll FAILS with
actionable feedback (click the content area for scroll focus / use
`key(["pagedown"])`/`key(["end"])`) and is bounded by
`_MAX_CONSECUTIVE_FAILURES`. (b) Scroll is exempt from the ledger (it can no
longer run blindly, and re-scrolling a similar-looking long page is
legitimate progress). (c) Every refusal path (`REFUSED`/`SKIPPED`/`done
REJECTED`) now logs and yields a progress chunk. (d) The no-progress abort
names the last real attempts so the voice layer reports the true cause.
Guards: `tests/unit/cu/test_engine_loop.py::
test_ineffective_scroll_fails_with_keyboard_hint`,
`::test_repeated_ineffective_scroll_aborts_with_honest_reason`.

**Class rule.** Every state-changing CU action needs a closed verification
loop — "the OS accepted the input" is not "the app reacted"; an action class
without an effect-check (scroll was one) silently corrupts both the model's
history and the dedup ledger, and the two errors compound. And guard paths
that refuse an action must be OBSERVABLE (log + progress + recorder event):
a guard that only whispers to the model turns every interaction bug into an
unexplainable early abort.

---

## BUG-083: macOS permissions "auto-denied" after an app update, Settings deep links landing on the wrong pane, and a dead second Allow button (HIGH, FIXED 2026-07-18)

**Symptom (live Intel test Mac, macOS 15.7, first run after the v1.0.11
update).** The onboarding permissions view showed Input Monitoring and Input
Control as DENIED although macOS never showed a prompt; Microphone had
reverted to "not asked". "Open Settings" for those rows surfaced System
Settings on an unrelated pane (the last-open Files & Folders pane with the
Documents-folder rows) instead of the requested one. Screen Recording kept
reading NOT ALLOWED after the user granted it, and a second click on Allow
did nothing.

**Root causes (three independent defects).**
1. *Signature churn orphans TCC grants:* the app is ad-hoc signed, so macOS
   pins every TCC grant to the executable's CDHash. The BUG-079 bundle-format
   bump forced a rebuild → new CDHash → macOS treated the updated app as a
   stranger: HID-class services (ListenEvent/PostEvent) report the orphaned
   rows as DENIED — a state in which macOS suppresses every further prompt —
   while AVFoundation reverts to "not determined". Nothing in the app ever
   asked; the denial was inherited from a dead identity.
2. *System Settings ignores pane anchors while running:* the
   `x-apple.systempreferences:...?Privacy_*` URL only raises an already-open
   System Settings window on whatever pane it last showed. Microphone/Screen
   Recording "worked" earlier in the flow only because System Settings was
   not running yet at that point.
3. *Frozen Screen Recording preflight + one-shot prompts:*
   `CGPreflightScreenCaptureAccess` is frozen per process, so a mid-session
   grant stays invisible until relaunch, and macOS never re-prompts after the
   first request — yet the UI kept showing the stale state AND a request
   button that could never do anything again.

**Fix (2026-07-18).** (a) `_install_native_bundle` records the CDHash before
and after a rebuild; on a real signature change it resets the five TCC
services scoped to `com.personal-jarvis.desktop` via `tccutil` so macOS can
prompt fresh instead of inheriting orphaned denials; bundle format bumped to
3 so affected installs go through exactly one healing rebuild. (b)
`_open_settings` terminates a running System Settings (no TCC needed) before
opening the anchor URL, so LaunchServices relaunches it on the requested
pane. (c) While a restart is pending, `can_request` is false (the dead Allow
button disappears), the detail explains the restart, and the frontend shows
"Restart pending" instead of the stale state for the one frozen probe
(screen recording). Guards: `tests/unit/platform/test_permissions.py::
test_open_settings_quits_running_system_settings_before_navigating`,
`::test_screen_capture_restart_pending_hides_the_dead_allow_button`, and the
`_tcc_reset_*` tests in `tests/unit/setup/test_macos_app_bundle.py`.

**Class rule.** An ad-hoc-signed app's TCC identity IS its CDHash: any forced
rebuild silently voids every recorded grant, and the orphaned rows come back
as invisible denials, not as fresh prompts — pair every identity-changing
rebuild with a scoped `tccutil` reset (and long-term, a stable signing
identity). Never surface a permission request control the OS will ignore:
macOS prompts exactly once per TCC state, and reads some probes only at
process start — the UI must model both or it gaslights the user.

## BUG-084: Classic pipeline answers ITSELF on open speakers — false self-barge truncates the reply and its echo becomes the next "user" turn (CRITICAL, FIXED 2026-07-18)

**Symptom (Intel-Mac test machine, v1.0.12, built-in speakers + mic).** The
assistant's speech is severely chopped — mid-sentence pauses of 5-6 s,
skipped words — and the session transcript shows the assistant holding a
conversation WITH ITSELF across multiple turns: its reply tail comes back
(STT-garbled, e.g. "freut mich zu hören" heard as "Misch zu hören") as a <!-- i18n-allow: forensic quote of the garbled echo under test -->
"user" turn, which the brain politely answers, producing a new reply, a new
echo, and so on — an unbounded self-talk loop.

**Root cause — BUG-062 was fixed ONLY in the realtime path.** The classic
pipeline's `_barge_monitor` (`jarvis/speech/pipeline.py`) still had both
BUG-062 failure modes, plus a loop-amplifier of its own:

1. **No energy floor before Silero.** Every mic frame went straight to the
   VAD model, which cannot tell WHOSE voice it hears. On open speakers next
   to the built-in mic the assistant's own voice is loud, sustained and
   perfectly speech-shaped → prob ≥ 0.97 for ≥ 12 frames → false barge-in →
   `player.stop()` mid-sentence (the skipped words).
2. **Per-frame ONNX synchronously on the voice event loop** for the entire
   answer — starved the ~120 ms playback write batches on the slow CPU →
   PortAudio underruns (the stutter); a ≥ 5 s gap additionally tripped the
   `_TTS_PLAYBACK_STALL_S` watchdog and aborted the whole turn.
3. **The loop-amplifier:** a barge keeps the session LISTENING and skips
   `_suppress_session_input_after_tts` entirely (correct for a REAL
   interrupter — their words must not be dropped). After a FALSE barge that
   means: no echo lock, mic live, the room still carrying the assistant's
   own voice → the echo is transcribed → dispatched to the brain → answered.
   One false barge is enough to seed the endless self-conversation.

**Fix (three layers, all cross-platform).**

1. **Shared detector:** `_barge_monitor` now reuses
   `DesktopRealtimeBargeInDetector` (the tuned BUG-062 realtime fix — static
   RMS floor 0.010, grace, 0.97 × 12 frames) instead of its own bare Silero
   loop, and every `feed` runs via `asyncio.to_thread` so inference never
   shares the event loop with live playback writes.
2. **Adaptive echo floor (in the shared detector, so realtime gains it
   too):** a fixed floor cannot cover every speaker/mic coupling — loud
   built-in-speaker echo sails over 0.010. The detector now calibrates a
   per-answer floor from the grace-window frames (pure speaker echo by
   construction) and keeps updating it from a rolling window of frame RMS
   values: floor = clamp(1.4 × P90, static floor, 0.25). The newest ~16
   frames are EXCLUDED from the baseline (lag > confirm run), so a user
   starting to speak is judged against the pure-echo past, never against
   their own rising voice. `min_frame_rms=0.0` still disables all gating.
3. **Self-echo TEXT guard (last line of defense, `_looks_like_self_echo`):**
   inside the post-playback window, an utterance that is (fuzzily, cutoff
   0.8 — STT garbles echo) contained in the assistant's own recently spoken
   words with essentially no novel token is dropped before the brain (log:
   "Own speaker echo suppressed"), keeping the session listening. Fail-open
   by design: < 3 tokens are never judged (short commands always pass), any
   novel content keeps the turn, and outside the 6 s activity window the
   user may quote Jarvis verbatim at will. This breaks the loop even when a
   false barge slips the acoustic gates — and covers the barge path's
   deliberate lack of post-TTS suppression.

**Latent bug exposed and fixed on the way:** when the barge monitor returned
WITHOUT barging while the streamed answer was still playing,
`_brain_streaming` fell through to its `finally` and cancelled the producer +
playback — beheading a healthy answer. Invisible before (the old monitor
slept through its grace before touching anything and practically never ended
early); `_speak` always had the wait-it-out branch. `_brain_streaming` now
mirrors it.

**Guards.** `tests/unit/speech/test_self_echo_guard.py` (garbled echo
flagged, novel-content answers kept, short commands immune, window lapse,
cross-sentence echo); `tests/unit/realtime/test_desktop.py` (adaptive floor
gates echo louder than the static floor, lag keeps sustained user speech
confirmable, `min_frame_rms=0.0` opt-out, `start_output` recalibration,
grace frames calibrate but never become preroll).

**Class rule (sharpens BUG-062's).** Half-duplex voice on open speakers must
treat its own output as hostile input on EVERY path that consumes the mic —
fixing one surface (realtime) while the sibling (classic pipeline) keeps the
bare detector just moves the bug. An interrupt detector needs an energy floor
*derived from the echo actually being measured*, not a hardcoded guess; and
any state transition that deliberately skips echo suppression (real barge-in)
needs a content-level backstop, because it will eventually be entered by
mistake.

*(This class rule fired the very same day: the realtime path itself had NO
text-level backstop and looped in the live Mac test — see BUG-089.)*

## BUG-085: Realtime session goes permanently DEAF after an in-place transport rebuild — the surface's echo guard swallows every microphone frame (CRITICAL, FIXED 2026-07-18)

**Symptom (live 2026-07-18 16:17, gemini-live, 21-turn conversation).** At
the ~10-minute Gemini Live session limit the server sent `GoAway`
(`time_left=50s`) and then aborted the WebSocket with `1008 policy
violation` — right as turn 21's 35-second reply finished draining from the
speaker. The BUG-071 in-place rebuild worked: a fresh transport was open
~2 s later, no error surfaced, the call stayed "alive". But the user then
spoke for 20 more seconds into a session that heard NOTHING — no
transcript, no orb/taskbar reaction, no reply — until they hotkey-killed
the call.

**Root cause — the rebuild froze the turn for the RECORDER but never told
the SURFACE.** A natural turn boundary sends the surface
`{"type": "turn_complete"}` and then publishes the turn-completed event;
`_rebuild_transport` only published the event. The desktop surface's
half-duplex closure (`jarvis/speech/pipeline.py`) leaves its `speaking`
echo-guard state only on session JSON (`turn_complete` / `tts_cancel` /
`thinking` / `hangup`) — and the dead transport can never deliver its own
`turn_complete`. With `speaking` stuck True, `_send_microphone` routes
every frame into `DesktopRealtimeBargeInDetector.feed()` and uploads
nothing. The detector is deliberately conservative (static RMS floor,
0.97 × 12-frame confirm, BUG-084's adaptive echo floor still elevated from
35 s of loud playback), so normal-volume "is anyone there?" speech never
confirms a barge-in: the microphone is swallowed locally, forever. The
rebuilt transport was healthy — it simply never received a byte.

**Fix (both sides, transport-neutral).**

1. **Session side (`jarvis/realtime/session.py::_rebuild_transport`):**
   mirror the frozen turn to the surface exactly like a natural boundary —
   send `{"type": "turn_complete"}` (best-effort) before
   `_publish_turn_completed()` and the fresh `audio_ready`. The surface
   drains any salvaged audio tail, closes the output segment, and returns
   to LISTENING; in single-turn mode this also ends the call exactly as a
   completed turn should.
2. **Desktop surface (`jarvis/speech/pipeline.py`, `audio_ready`
   handler):** defense in depth — a transport that just completed its
   handshake cannot be mid-output, so a still-open output segment at
   `audio_ready` is stale pre-rebuild state and is drained + closed before
   the new sample rate applies.

**Guards.**
`tests/unit/realtime/test_session.py::test_transport_rebuild_mirrors_the_frozen_turn_to_the_surface`
(exactly one rebuild-sourced `turn_complete`, ordered before the second
`audio_ready`).

**Class rule (extends BUG-071's).** An in-place transport rebuild must
resynchronize EVERY party that tracks per-turn state — the provider session
object alone is not the session. Any surface state machine that can only be
released by a message from the (now dead) transport will wedge; whoever
declares the old turn finished must broadcast that fact to all surfaces,
not just to the recorder.

## BUG-086: Realtime voice audibly flips gender between turns while every transcript label reads the same pinned voice (HIGH, MITIGATED 2026-07-18, ESCALATION SHIPPED 2026-07-21 AND REVERTED SAME DAY, provider-side root cause OPEN)

**Symptom (live 2026-07-18 17:12, session `f4e8e93d`, gemini-live,
4 turns).** The audible voice alternated male / female / male /
female across consecutive turns of ONE call. The exported transcript
claims the opposite: every turn carries `voice_name: "Fenrir"`. The same
class was already seen live on 2026-07-17 (08:47: the injected bridge line
came out in a female, distorted voice; 10:04: Fenrir's aborted readback was
re-spoken by Charon).

**What actually spoke, per the run log.** Turns 0-2 were rendered by
Gemini Live's own native-audio generation (session voice pinned to Fenrir
via `PrebuiltVoiceConfig`); turn 3's provider produced no audio for the
grounded Brain result, so the surface TTS fallback spoke it through
`gemini-flash-tts`, also pinned to Fenrir. No second local speaker existed
(the one OpenRouter `/audio/speech` call at 17:12:16 belongs to a
background health probe that also fires outside sessions).

**Root cause, two layers.**

1. **Provider-side (primary, not ours to fix):** Gemini's native audio is
   a *generative* renderer, not a fixed-voice synthesizer. The pinned
   prebuilt voice is a starting point the model can drift from when the
   content reads as a performance cue — a heavy dialect persona (this
   session answered in Bavarian), quoted lines, or tagged content such as
   `<trusted_action_result>`. The 2026-07-17 forensics proved framing
   alone flips the voice; this session shows it happening on plain persona
   turns as well.
2. **Our side (label honesty):** the per-turn `voice_name` the recorder
   stores as "which voice actually spoke" is fed from
   `RealtimeVoiceSession._active_voice` — the voice we REQUESTED at
   session open — never from the audio that was heard. For native-audio
   turns the label is aspirational, so a real flip is invisible in the
   transcript and the register looks self-consistent while the user hears
   four different speakers.

**Mitigation shipped (this commit).** The voice-identity clause that fixed
the 2026-07-17 bridge-line flip ("say it as yourself, in exactly the same
voice … do not imitate, do not dramatize") now covers every native
rendering order, not just the bridge line: `_delegate_result_prompt`,
`_direct_tool_result_retry_prompt`, `_external_update_prompt`, and — for
plain persona turns — a standing session-wide clause in
`_REALTIME_SAFETY_APPENDIX` ("keep one single, consistent voice for the
entire conversation; never switch voice, gender, tone").

**Escalation shipped (2026-07-21, maintainer report: the voice still
flipped mid-call, especially on deep-thinking replies).** Delegate replies
are no longer read back by the generative native renderer at all: the
provider session declares the capability `renders_pinned_voice = False`
(`_GeminiLiveSession`; documented in `jarvis/realtime/protocol.py`,
default True when absent — a capability gate, never a provider-name pin,
AP-21), and `RealtimeVoiceSession._verify_delegate_readback` then claims
the reply for the desktop surface IMMEDIATELY instead of waiting out the
no-readback window: the same-family surface TTS (`gemini-flash-tts`,
one-take, pinned session voice) speaks it, while the trusted result is
still injected so the live model keeps conversational context — its own
(re-)rendering stays withheld via the existing one-reply-one-voice flags.
The browser surface keeps the native readback: it has no server-side
rendering for `error_spoken`, and its Web-Speech fallback would be a THIRD
voice.

**Escalation REVERTED (2026-07-21, maintainer live validation failed).**
The live verdict on the escalation: the `gemini-flash-tts` rendering of
the pinned voice is audibly a DIFFERENT voice than the live model's native
rendering of that same voice name — so the escalation turned "occasional
drift on deep replies" into a GUARANTEED voice flip on every tool-model
turn (live session 2026-07-21 10:33: every delegated turn spoke
`Puck @ gemini-flash-tts`, audibly unlike the native `Puck @ gemini-live`
of plain turns). The maintainer's directive: the session's ONE voice is
the NATIVE realtime voice. The `renders_pinned_voice` capability and the
immediate surface claim were removed; delegate replies render natively
again with the surface TTS strictly as the provider-mute fallback (the
pre-escalation 2.5 s window), and the injected identity clauses remain
the drift mitigation. Do not re-add an immediate surface claim — a
deterministic wrong voice is worse than an occasional drift. Guards:
`tests/unit/realtime/test_session.py::test_generative_voice_provider_delegate_reply_renders_natively_on_desktop`,
`::test_generative_voice_provider_mute_still_falls_back_to_surface_tts`,
`::test_generative_voice_provider_keeps_native_readback_on_the_browser_surface`.

**Label honesty FIXED 2026-08-20.** `SpeechSpoken.voice_verified` is False
for native generative audio (the pin we requested, not a heard speaker)
and True for classic TTS. The recorder, the markdown export, and the
Transcription badge say "requested" when it is only a pin.

**Still OPEN.**

- Plain (non-delegate) native turns can still drift despite the standing
  session clause — inherent to a generative renderer; a full fix would
  abandon native audio entirely. Do not re-add an immediate surface claim
  — a deterministic wrong voice is worse than an occasional drift.

**Class rule.** A generative native-audio renderer holds its voice only as
firmly as EVERY text it is told to speak reminds it to. Any new prompt
that asks the realtime model to deliver content (results, updates, interim
lines) must carry the voice-identity clause — and a transcript label must
never present a requested voice as a heard one.

## BUG-087: 60-second realtime turn felt like "an eternity of pauses" — 9.6 s to first audio, then chunk-starved gaps in a 53 s surface-TTS readback (MEDIUM, ANALYZED 2026-07-18, OPEN)

**Symptom (same session as BUG-086, turn 4 of 4).** A plain knowledge
question ("best and hardest programming language to learn") took 60.9 s
end-to-end: long dead air after the user finished speaking, then an answer
that repeatedly stalled mid-delivery before continuing.

**Measured timeline (run log + latency spans).**

- 17:13:06.0 — turn committed; the planner routes it over the
  orchestrator (`reasons=capability,connected_data` — a pure knowledge
  question needed no tools; over-routing is the first avoidable cost).
- +3.0 s — deterministic delegate waits out the full hardcoded
  provider-input-boundary window before dispatching on the stable local
  transcript.
- +5.0 s — Brain turn (gemini-3.5-flash), including building a ~34k-token
  Gemini context cache mid-turn.
- 17:13:14.1 — provider rendered no audio for the grounded result →
  surface TTS fallback (`gemini-flash-tts`, Fenrir).
- 17:13:15.7 — first audible audio: **9.6 s of silence** after the user
  stopped speaking; the interim bridge line never became audible.
- 17:13:21.5-26.0 — the remaining five/six sentence-chunk synthesis calls
  complete while playback of chunk 1 is already running: playback outran
  synthesis early on, producing audible mid-answer gaps (~52.8 s speaking
  for ~45 s of audio).
- 17:14:07 — one second after playback drained, the idle Live websocket
  died (`keepalive ping timeout` → `1006`); the BUG-071/085 in-place
  rebuild recovered it (1/3), the user then hung up by hotkey.

**Open fix directions (in value order).** (1) Planner: stop routing pure
knowledge questions through the orchestrator when no capability is truly
needed — the native path answers the same question in ~1 s (turns 0-1).
(2) Shorten/parallelize the 3.0 s boundary wait when the local transcript
is already stable. (3) Pipeline the surface-TTS chunk synthesis ahead of
playback (prefetch N+1 while N plays) so playback can never starve.
(4) Keep the Live transport's keepalive fed during long surface-TTS
playback so the session does not die underneath a healthy call.

## BUG-088: Realtime voice answers follow-ups with total amnesia after an in-place transport rebuild — the fresh provider session starts with an empty conversation (HIGH, FIXED 2026-07-18)

**Symptom.** Mid-call, the realtime voice suddenly stops understanding
conversational context: a follow-up that plainly depends on earlier turns is
answered as if the call had just started. Live case (2026-07-18, the same
session as BUG-086/087): turn 4 discussed the "best and hardest programming
language to learn"; at 17:14:07 the idle Live websocket died (`1006`,
keepalive ping timeout) and the BUG-071 in-place rebuild recovered the
transport in ~2 s; the next user turn — "what is the hardest language in the
world?" — was answered with natural languages, because for the model the
question WAS the first turn of a brand-new conversation. The maintainer's
verdict "he just doesn't get the context, Gemini Live by itself wouldn't do
this" is literally accurate: raw Gemini Live keeps context per connection —
it was OUR rebuild that silently replaced the connection.

**Root cause.** Realtime providers hold the conversation server-side, scoped
to one WebSocket connection. The BUG-064/071/085 transport-rebuild stack
(deliberately) reopens a fresh connection mid-call — Gemini's Live sessions
die routinely (GoAway session limits, 1008, idle 1006; four rebuilds in the
2026-07-18 desktop log alone), so mid-call context loss was not an edge
case but the EXPECTED cost of every rebuild ("In-provider conversation
history is lost" was documented as acceptable). The orchestrator-side
bounded call transcript (`_delegate_history`) survived every rebuild — it
just was never given to the fresh provider session.

**Fix (provider-neutral seed, capability-gated — AP-21).**
`RealtimeSessionConfig.history` now carries the bounded call transcript
(oldest-first `{"role", "text"}` mappings, derived from the same
`_delegate_history` that grounds delegated Brain turns, so the native model
and the delegate see ONE consistent view of the call). `_open()` fills it on
every open: empty at call start, populated at every mid-call reopen
(in-place rebuild AND cross-family fallback). Adapters restore it through
their native channel:

- **gemini-live** replays it right after connect via
  `send_client_content(turns=…, turn_complete=False)` — Gemini's documented
  initial-history channel; no response generation is triggered.
- **openai-realtime** recreates it as `conversation.item.create` messages
  after the handshake, and its provider-internal BUG-064 rebuild replays the
  orchestrator's LATEST snapshot, kept current after every completed turn
  through the optional `set_history_snapshot` capability (probed with
  `getattr`, never required — third-party adapters are untouched).

All seeding fails OPEN: a seeding error degrades to exactly the pre-fix
amnesiac-but-alive session, never a dead call.

**Guards.**
`tests/unit/realtime/test_session.py::test_transport_rebuild_seeds_the_call_history_into_the_fresh_session`
(+ `…::test_completed_turns_refresh_the_session_history_snapshot`),
`tests/unit/realtime/test_gemini_live.py::test_open_session_seeds_prior_call_history`
(+ no-history / seeding-failure cases),
`tests/unit/realtime/test_openai_realtime.py::test_open_session_seeds_prior_call_history`
(+ `…::test_transport_rebuild_replays_the_current_history_snapshot`).

## BUG-089: macOS realtime voice converses WITH ITSELF in two voices — echoed canned apologies become "user" turns while the starved brain chain re-speaks them forever (CRITICAL, FIXED 2026-07-19)

**Symptom (live Mac test 2026-07-18, Intel MacBook Pro, built-in
speakers+mic, realtime/duplex default mode).** Turn 1 is always perfect: fast
reply in the female realtime session voice. From turn 2 on, every turn speaks
a canned provider-down apology — in a DIFFERENT, male voice — and the
assistant then ANSWERS its own apologies conversationally ("no problem, I'll
try again shortly"), two voices holding a dialogue with each other in an
unbounded loop. Reproduced across fresh runs; Windows (headset, tuned device
selection) unaffected. Game-breaking.

**Root cause — three interlocking weaknesses, all in the realtime path.**
BUG-084 had fixed exactly this loop for the CLASSIC pipeline hours earlier;
its own class rule ("fixing one surface while the sibling keeps the bare
detector just moves the bug") described the realtime gap precisely:

1. **No text-level echo backstop.** The realtime mic gate was acoustic only
   (0.5 s post-output tail guard + barge detector). On macOS built-in
   speakers next to the built-in mic (no AEC anywhere in the desktop path),
   the assistant's own playback leaked through, was uploaded as "user"
   audio, transcribed provider-side, and answered.
   `_looks_like_self_echo` existed but was never consulted here, and the
   surface-spoken canned phrases were never registered as assistant speech.
2. **The echo loop starves the brain chain.** One 429/quota error put the
   delegate chain's provider into `RateLimitTracker(cooldown_s=30.0)` (or
   the terminal per-session dead list). Echo turns fire every few seconds —
   far inside 30 s — so EVERY subsequent turn found an empty chain and
   returned a `_PROVIDER_DOWN_PHRASES` apology: "turn 1 perfect, turn 2+
   always the phrase". AP-22 corner: realtime (Gemini Live) and the whole
   delegate chain on ONE Gemini credential family collapse together.
3. **The second voice is the surface TTS fallback.** A dead realtime
   provider cannot voice the apology, so `_surface_speech_message` re-renders
   it through the realtime-scoped surface TTS — which hard-defaulted an
   unknown session voice to masculine "Charon". A feminine live session
   suddenly apologizing in a male voice completed the "two assistants
   talking to each other" illusion.

**Fix (defense in depth — any single layer breaks the loop).**

- **Shared guard:** the BUG-084 text guard moved byte-for-byte into
  `jarvis/speech/echo_guard.py` (`SelfEchoGuard`), gaining slot-replacement
  registration and a future-datable activity stamp; the pipeline keeps its
  methods as thin delegates (facade, old tests green unchanged).
- **Realtime interception:** the session registers EVERY text it makes
  audible (all surface-spoken phrases at the `_surface_speech_message` choke
  point, the provider's cumulative per-turn output transcript under one
  replaceable slot, the exact delegate reply at each delivery site) and
  judges each final input transcript at the head of the `input_transcript`
  branch — an echo match is dropped before ANY turn side effect (no barge
  confirm, no turn start, no tool bridge, no delegate, no
  `request_response`, no TranscriptionUpdate). Auto-response adapters get a
  best-effort `interrupt()` + the existing output-withhold flag. Guard
  activity is future-dated to the estimated playback drain
  (`_output_samples_sent` / `output_sample_rate`, capped +120 s, reset on
  barge/cancel) because providers send audio faster than realtime.
- **Desktop surface:** the classic pipeline's own guard now also arms on
  realtime output (`error_spoken` pre-synthesis + assistant-transcript
  segments), so a session teardown into classic fallback cannot answer a
  late echo.
- **Anti-nag cooldown:** ONE outage/recovery notice per 30 s
  (`_OUTAGE_NOTICE_COOLDOWN_S`); a repeat apology (detected via
  `brain._last_turn_all_failed`) completes its turn silently and is NEVER
  written into `_output_transcript` (no fabricated audible record); turns
  with pending native tool calls are always answered (protocol first).
- **Voice continuity:** the surface fallback resolves the session voice's
  curated gender (`voice_gender` + `continuity_voice`) before defaulting to
  Charon — the fallback voice keeps the session's profile.
- **Hardening:** the desktop barge detector's per-frame Silero inference
  moved off the event loop (session-scoped single-worker executor); the
  realtime factory logs an AP-22 warning when the realtime provider and the
  entire configured brain chain share one credential family.

**Deliberate non-changes.** The 0.5 s acoustic tail guard and the adaptive
floor cap stay untouched — headset users must not pay latency for a
Mac-speaker problem; the text guard is the word-agnostic backstop. No
delegate-chain reordering (realtime-scoped credential separation).

**Guards.** `tests/unit/realtime/test_session_self_echo.py` (canned-phrase
echo never becomes a turn; provider-transcript echo dropped; novel-content
fail-open; short commands immune; auto-response interrupt; playback horizon
armed + capped + barge reset), `tests/unit/realtime/test_outage_notice_cooldown.py`
(repeat apology silent + not in the transcript record, speaks again after the
window, healthy replies never suppressed, pending native calls always
answered), `tests/unit/speech/test_echo_guard.py` (slot replacement,
future-dated touch), `tests/unit/realtime/test_factory.py` (AP-22 warning),
`tests/unit/plugins/tts/test_realtime_surface_tts.py` (gender-continuous
fallback voice).

**Class rule (closes BUG-084's).** A voice surface may only make text
audible through a path that (a) REGISTERS that text with the self-echo
guard and (b) judges inbound "user" text against it while playback can
still be heard. Canned error phrases are not exempt — they are the WORST
offenders, because they are spoken exactly when the system is degraded and
repeating. And an error phrase that can repeat MUST carry a cooldown: a
fixed apology re-spoken every few seconds is not honesty, it is fuel for
whatever loop caused it.

## BUG-090: Surface-TTS fallback flips to a different-gender voice mid-answer AND the turn loses its voice label (HIGH, FIXED 2026-07-19)

**Symptom (live 2026-07-19 07:41, session `4123ba4c`, gemini-live, voice
pinned Fenrir).** Turn 3 (a delegated calendar question) was audibly spoken
by a FEMALE voice while every other turn spoke as masculine Fenrir — and the
exported transcript shows NO voice label at all for exactly that turn, so
the flip was invisible in the record.

**What actually happened, per the run log.**

1. 07:42:30.112 — the provider produced no audio for the grounded Brain
   result; the surface TTS fallback (`gemini-flash-tts`) rendered the
   two-sentence answer.
2. The fallback instance was built WITHOUT the voice-consistency profile:
   `build_realtime_surface_tts` passed neither `chunk_by_sentence` (ctor
   default **True** — one generation PER SENTENCE, confirmed by two synth
   calls in the log) nor the `[tts]` `seed`/`temperature` drift knobs the
   pipeline instance has honored since 2026-05-24. Gemini TTS is generative
   (BUG-086): each extra generation re-rolls the delivery, and the Bavarian
   dialect persona is exactly the performance cue that pushes the render
   past the `PrebuiltVoiceConfig` pin — one take came out female.
3. The honest label was then dropped: the surface path publishes its
   reply-kind `SpeechSpoken` (voice + provider) only after playback
   DRAINED (~14 s later) — by then `VoiceTurnCompleted` had finalized the
   turn (correctly claiming no session voice, `_output_samples_sent == 0`),
   and the recorder attached `SpeechSpoken` only to an OPEN turn. Result:
   a spoken turn with an empty `voice_name`.

**Fix (both layers, cross-platform pure Python).**

- **One take + drift knobs:** `build_realtime_surface_tts` now constructs
  the surface `GeminiFlashTTS` with `chunk_by_sentence=False` (the whole
  reply is ONE generation; with `[tts].streaming` the single take still
  streams, so first-audio latency is unchanged) and passes through the
  `[tts]` `seed`/`temperature` values.
- **Late label re-attach:** the recorder keeps the session's most recently
  finalized turn; a reply-kind `SpeechSpoken` that arrives late attaches
  its voice to that turn — only when the turn has NO voice yet and its
  recorded reply text matches the confirmed-audible text (equality, or
  containment past a 16-char floor for scrubbed subsets) and the OPEN turn
  does not own the same reply. `SessionStore.update_turn_voice` persists
  the two voice columns post-finalize.

**Guards (run in the `ci.yml` pytest matrix on Linux/Windows/macOS).**
`tests/unit/plugins/tts/test_realtime_surface_tts.py` (one-take profile
with and without configured knobs),
`tests/unit/sessions/test_turn_voice_label.py` (late label lands on the
finalized turn + persists; never stamps the next open turn; scrubbed-subset
match; never overwrites an honest label; short interjections prefer the
open turn).

**Class rule (extends BUG-086's).** A voice instance whose sole purpose is
IDENTITY (an emergency re-render of a session voice) must be built with the
strictest consistency profile the engine offers — never the engine's
convenience defaults; every extra generation is another dice roll. And the
authoritative "this voice actually spoke" signal must be attachable to the
turn it describes even when playback outlives the turn — a label that can
only land on an open turn silently disappears exactly when the fallback
path (the interesting case) is slow.

---

## BUG-091: The uninstall one-liner is DEAD on every Mac — bash 3.2 cannot parse `install/uninstall.sh` at all (HIGH, FIXED 2026-07-19)

**Symptom (field report 2026-07-19, macOS test machine).** The documented
uninstall command from README.md

```bash
bash ~/.personal-jarvis/install/uninstall.sh
```

prints a syntax error and nothing else. No banner, no prompt, nothing
removed, exit 2:

```
~/.personal-jarvis/install/uninstall.sh: line 57: syntax error near unexpected token `;;'
```

**Root cause — a PARSE failure, so not one line of the script runs.** macOS
still ships GNU bash **3.2.57 (2007)** as `/bin/bash`; Linux and Git Bash
ship bash 4/5. `stop_running_instances()` put a `case` arm inside a `$( )`
command substitution (`install/uninstall.sh:57`):

```bash
pids=$(ps -axo pid=,comm= 2>/dev/null | while read -r pid comm; do
    case "$comm" in "$root"/*) printf '%s ' "$pid" ;; esac
done) || true
```

The bash 3.2 parser reads the `)` that closes the case **pattern** as the end
of the command substitution, then chokes on the `;;`. Because it is a parse
error, the failure is total and happens before the first statement executes —
which is why it reads as "the uninstaller does nothing" rather than "the
uninstaller failed halfway".

Version boundary verified in isolated containers: `bash 3.2.57` → parse error,
`bash 4.4.23` → OK, `bash 5.2.37` → OK. Linux and Windows were never affected.

**Origin and blast radius.** Introduced by 470da3ea (2026-07-18, "stop the
running app before deleting the install folder"). The three earlier versions
of the script parse cleanly on 3.2. Shipped in **v1.1.0 and v1.1.1**, so every
Mac that updated to those releases has a completely dead uninstaller; a Mac
still holding an older `~/.personal-jarvis` is unaffected.

**Fix.** The optional leading parenthesis on the case pattern — POSIX, and
parses on bash 3.2/4/5, zsh, and dash alike:

```bash
case "$comm" in ("$root"/*) printf '%s ' "$pid" ;; esac
```

Both arms in the function carry it, so a future move in or out of a
substitution stays safe.

**Why CI could not catch it (the real defect).** No workflow parsed a single
shell script against bash 3.2. `fresh-install-smoke.yml` never executes
`install.sh` — it reconstructs the venv by hand — and no workflow invokes
`uninstall.sh` at all. Green CI on Linux and Windows said nothing about the
one shell version macOS actually uses.

New gate: `scripts/ci/check_shell_bash32.py` parses **every** tracked `*.sh`
with real bash 3.2 (`bash -n`, never an execution) and is wired into `ci.yml`
as the BLOCKING `shell-portability` job. It picks its engine portably — a
Mac's native `/bin/bash` when that is 3.2, otherwise the `bash:3.2` Docker
image — and skips with an honest message where neither exists, unless
`--require` (what CI passes) turns the skip into a failure. Verified in both
directions: red on the pre-fix file with the exact field error text, green on
the fix and on all 7 tracked scripts.

**Lesson.** "Runs on Linux and Git Bash" proves nothing about macOS for a
shell script: the Mac is a full bash MAJOR VERSION behind, and the failure
mode is not a runtime bug but a dead file. Any shell artifact we ship to end
users must be parse-checked against 3.2 — the same OS-parity discipline
CLAUDE.md section 3 demands of Python and OS-specific backends.

---

## BUG-092: macOS asks for the login-keychain password on every Control API request (HIGH, FIXED 2026-07-19)

**Symptom (live macOS field report).** Launching Jarvis produced ten to thirty
indistinguishable login-keychain dialogs. Each said that `python3.12` wanted to
access the `personal-jarvis` item; **Always Allow** was disabled because macOS
could not verify that executable. Approving one dialog did not stop the next.
The ordinary onboarding permission rows were already complete, so this looked
like one global permission that macOS had forgotten repeatedly.

**Forensic evidence.** No credential values were read or printed during the
investigation.

1. The login Keychain contained three generic-password items for service
   `personal-jarvis`. The two provider items were created during the current
   native-app onboarding. The older `jarvis_control_api_key` item predated that
   app and was the only legacy item.
2. The Python 3.12 executable used by the earlier direct launch was completely
   unsigned (`codesign`: no signature; Gatekeeper: no usable signature), which
   exactly explains both the `python3.12` label and the unavailable persistent
   approval button.
3. The installed app now has the canonical bundle id
   `com.personal-jarvis.desktop` and a valid local ad-hoc signature. Its native
   Mach-O stub embeds Python without replacing the main process, and ordinary
   managed updates preserve that bundle byte-for-byte.
4. `verify_control_key()` called `get_control_key()` for every presented Bearer
   credential. That method decrypted `jarvis_control_api_key` through Python
   `keyring` every time. Startup also called `ensure_control_key()` from several
   paths. There was no cache and no single-flight lock, so concurrent browser,
   CLI, and status requests could each open their own native dialog.
5. The earlier macOS uninstaller prompt-storm fix had already proved the
   decisive distinction: attribute queries and item deletion do not request
   secret data, while a value read produces one authorization dialog per item
   and caller. The runtime path was still doing exactly the dangerous operation
   repeatedly.

**Root cause.** macOS Keychain access is not a TCC-style global toggle. A
generic-password item has its own ACL and normally trusts the code-signing
designated requirement of the process that created it. The legacy Control key
therefore trusted an unverifiable Python interpreter rather than the stable
Jarvis app. Clicking **Allow** authorized one read; it neither changed the
item's creator ACL nor fixed the next read. Jarvis multiplied that one ownership
problem into a dialog storm by reading the same secret on every authentication
check. The permissions screen could report the Keychain backend as available,
but backend availability cannot prove an individual item's ACL.

**Fix (two layers, no ACL weakening).**

- `get_control_key()` now protects the first successful lookup with a process
  `RLock` and caches it with `config.secret_revision()` invalidation. Twenty-four
  concurrent callers in the regression test perform exactly one credential
  read. Rotation and custom-key replacement update the cache immediately.
  Missing values are not cached, so a user-initiated Keychain recovery remains
  visible without a restart.
- Forked children discard both the cached value and inherited lock. The Control
  key is still never exported into the environment, and a worker cannot use the
  parent's Python cache through the supported API.
- After the first successful legacy read, Jarvis verifies that the current
  process really is the canonical app at
  `~/Applications/Personal Jarvis.app`, verifies its code signature, and
  fingerprints the exact `codesign` designated requirement. It also confirms
  the old item exists using `security find-generic-password` **without** `-w`
  (attributes only, never secret data). Only then does it re-save the same value,
  making the stable app the new item creator.
- A private non-secret owner stamp records that designated requirement. A
  Developer-ID build therefore remains stable across signed releases; the local
  ad-hoc build uses its CDHash requirement and re-adopts once after an actual
  bundle rebuild. A direct/unsigned Python launch has no accepted app identity,
  never migrates the item, and clears a stale stamp if it explicitly replaces
  the key.
- The repair does not use `set-generic-password-partition-list`, does not grant
  all applications access, and does not modify or sign the user's Python
  installation. Fresh credentials saved through the native app already receive
  the correct creator ACL and need no repair.

**Distribution boundary.** The managed local bundle is deliberately ad-hoc
signed. Preserving it byte-for-byte makes ordinary source updates stable, but a
rebuild changes its CDHash. The permanent public-distribution solution remains
Developer-ID signing and notarization, whose designated requirement stays
stable across app versions. The repository cannot manufacture or embed the
maintainer's private Apple signing identity; this runtime migration is the safe
local-install bridge, not a claim that ad-hoc signing equals distribution
signing.

**Guards.** `tests/unit/core/test_control_key.py` covers one read per process,
24-way concurrent single-flight, revision invalidation, fork-cache reset,
same-requirement one-time adoption, re-adoption after a requirement change,
direct-Python refusal, and non-promotion of a file seed. The adjacent Control
API, surface-security, CLI-tool, and auth suites cover request semantics and key
rotation end to end.

**Class rule.** Never decrypt an OS credential in a per-request authentication
dependency. Load it once through a serialized path, invalidate it on deliberate
replacement, and clear it across process-boundary inheritance. On macOS, never
model Keychain as one global permission: ownership is per item and anchored to
the caller's designated requirement. Repair legacy ownership only after one
user-approved read and only from the verified canonical app; never solve a
prompt by broadening an ACL to unsigned tools or all applications.

---

## BUG-093: macOS Jarvis Bar has a black rectangle and retains every speaking frame (HIGH, FIXED 2026-07-19)

**Symptom (physical-Mac field report).** Once the missing-image error was fixed,
the idle pill appeared inside a black rectangular window. Entering an active
voice state made every eased pill size remain on screen: old outlines formed
larger concentric red/green/gold capsules around the current frame. The
rectangle was the fixed `83x37` bar surface; the nested sizes exactly matched
the renderer's `36x6 -> 53.5x15.5 -> 62.25x20.25 -> ... -> 71x25` transition.
After the Qt surface removed those visual artifacts, clicks over transparent
padding around the pill were still swallowed instead of reaching the browser
or other window underneath. Worse, the companion became macOS' frontmost
application. Its 500 ms Z-order guard repeatedly reclaimed foreground status,
so ordinary browser/editor clicks were consumed merely to reactivate that app
and appeared globally unreliable even when the pointer was nowhere near the
bar.

**Root cause.** The PIL renderer was correct and returned a complete fresh
frame each tick. The failure was Aqua-Tk 9's layer-backed Canvas composition:
an RGBA `PhotoImage` update used source-over behavior, so source pixels with
alpha zero were no-ops rather than replacements. They could neither erase the
initial opaque Canvas backing nor clear pixels occupied only by the preceding
larger frame. Reusing a PhotoImage, deleting/recreating the Canvas item, and
clearing the `NSWindow` all left the same retained pixels.

BUG-075's AppKit pass was therefore insufficient. The target `NSWindow`, its
`TKContentView`, and its backing layer were already non-opaque with a clear
background; the unwanted pixels lived in the Canvas backing store above that
window background. The separate `master=self._root` fix remains necessary for
BUG-074's two Tcl interpreters, but it only makes the frames visible and cannot
change their composition semantics.

**Fix (platform split).** The companion host now selects
`QtJarvisBarOverlay` on Darwin. It keeps the existing deterministic PIL
renderer, geometry, modes, startup gate, drag/persistence, opacity, and JSON
host protocol, but presents each RGBA image on a `WA_TranslucentBackground`
Qt tool window. Every paint first clears the complete destination under
`QPainter.CompositionMode_Source`, then draws the new frame. Transparent pixels
therefore replace old alpha instead of blending over it. Windows and Linux
still instantiate `JarvisBarOverlay`; their proven Tk color-key/DWM path is not
changed. The Qt application is created before the host enters macOS accessory
mode, so no Tk bootstrap is mixed into its Cocoa lifecycle and no extra Dock
icon is required. Each complete RGBA frame also supplies the top-level Qt
window's native input mask. Only non-transparent pixels participate in hit
testing, so the visible pill remains interactive while its rectangular clear
padding passes mouse events through to the app below. Before `QApplication`
starts, the companion disables Qt's foreground-application transform. The
native window is marked as a non-activating `NSPanel`, and the Z-order guard
uses AppKit's `orderFrontRegardless` instead of `QWidget.raise_()`. This keeps
the bar above normal windows without making the helper process active; if the
native bridge is unavailable, the guard skips its cosmetic raise rather than
stealing focus through the Qt fallback.

**Adjacent subprocess fixes.** The audit found two bugs hidden by the missing
image. Talk/hang-up/mute clicks were resolved in the child, whose
`runtime_refs` can never contain the parent SpeechPipeline; they now cross the
host protocol and execute against the authoritative parent pipeline, including
the stuck-active recovery guard. `level_tap` is process-local too, so the bus
bridge now forwards live parent TTS levels through `set_level`; Jarvis' speaking
bars react to actual output instead of remaining flat.

**Guards.** A headless `QImage` regression starts with an opaque black backing,
paints a larger shape, then a fully transparent frame, asserting both the black
corner and old-only shape pixels return to alpha zero. Input-mask regressions
assert opaque bar pixels remain clickable, clear corners are excluded, and
every newly rendered eased frame updates the window mask. Z-order regressions
prove Darwin uses native non-activating ordering and never falls back to Qt's
focus-stealing raise. A physical-Mac trace activates Finder, waits across
multiple guard intervals, and confirms Finder remains frontmost while the bar
stays onscreen. Host-selection tests prove Darwin uses Qt without calling the
Tk bootstrap and non-Darwin keeps Tk. Interaction tests cover child events
through parent pipeline actions, and a real `QT_QPA_PLATFORM=offscreen`
subprocess smoke covers init, ready, state, level, hide, stop, and clean exit.
The full Jarvis Bar unit suite passes on the physical Mac.

**Class rule.** Window transparency and animated-frame replacement are separate
contracts, and visual transparency is separate again from input transparency.
A clear/non-opaque native window does not prove that a child Canvas will erase
its own backing store or that alpha-zero pixels pass clicks through. For dynamic
RGBA overlays, test a large frame followed by a smaller or transparent one,
assert old-only pixels return to alpha zero, and constrain the native hit-test
region to the visible content. An always-on-top helper must also prove that its
periodic ordering operation does not activate the helper application. Keep
compositor workarounds behind a platform backend instead of changing a
rendering path already proven on another OS.

**Sequel on Windows (2026-07-28).** The same black rectangle was then reported
on Windows, where the colour key measurably works. It has a different cause —
Windows' ghost window over a stalled bar — and its own entry: BUG-118. Per-OS
mechanics and the colour-of-the-box decision table:
[`docs/overlay-transparency.md`](overlay-transparency.md).

---

## BUG-094: macOS Jarvis Bar cannot enter a hidden Dock strip and stays stranded when the Dock changes state (MEDIUM, FIXED 2026-07-19)

**Symptom (physical-Mac field report).** With the Dock visible, keeping the bar
above it is correct. After an app enters a fullscreen Space and macOS hides the
Dock, however, dragging still stops at the old work-area boundary. The blocked
strip is invisible but remains as tall as the Dock. A bar placed near the real
screen edge also needs to retreat automatically when the Dock returns rather
than being covered by it.

**Root cause.** The Qt surface used `QScreen.availableGeometry()` for startup,
saved-position recovery, and drag release. On the affected `1440x900` Intel Mac,
Qt reported `(0, 25, 1440, 818)` even while the Dock was absent from Quartz's
on-screen window catalogue. The resulting 57-pixel bottom reservation was
therefore stale presentation state, not a real obstacle. The surface also held
only one position, so temporarily clamping it for a returning Dock would have
destroyed the user's lower fullscreen preference.

**Fix.** The Darwin Qt backend now compares the complete and available screen
rectangles, infers the reserved Dock edge (bottom, left, or right), and checks
the live on-screen Dock window through the optional Quartz capability. A hidden
Dock restores only its reserved edge; the menu-bar inset is deliberately kept.
Missing Quartz remains fail-safe and uses Qt's conservative work area. The bar
stores the user's preferred position separately from its current safe position.
Its existing 500 ms ordering guard also reconciles geometry: a visible Dock
moves the bar clear without rewriting the preference, and a hidden Dock restores
that preference. Reconciliation pauses during an active drag. Windows and Linux
retain their existing available-work-area behavior.

**Guards.** Pure geometry tests cover bottom, left, and right Dock reservations;
a Quartz catalogue double proves visibility is scoped to the target display;
and surface regressions prove visible-Dock retreat, hidden-Dock restoration,
preferred-position persistence, and drag stability. The physical Mac trace
confirmed both native states: no on-screen Dock window while hidden, followed by
an on-screen layer-20 Dock window spanning the target display when revealed.

**Class rule.** A desktop work area is policy, not necessarily current visual
occupancy. For overlays that may enter fullscreen content, keep user intent
separate from temporary collision avoidance, preserve unrelated safe-area edges,
and detect the obstacle's live presentation state through a capability-gated
native probe. Never persist a transient clamp as the user's new preference.

---

## BUG-095: macOS Jarvis Bar hover effects and controls do not react reliably (HIGH, FIXED 2026-07-19)

**Symptom (physical-Mac field report).** Moving the pointer onto the Qt Jarvis
Bar often produced no visible hover transition. When it did transition, the
expanded state could disappear immediately, leaving the close and microphone
controls unavailable. The failure affected both the idle pill and active voice
states, so macOS did not expose the same clear mouse-out versus mouse-over
presentation as the established desktop surface.

**Root cause.** Two native behaviors compounded. First, the non-activating
`NSPanel` did not explicitly request mouse-move delivery, so Cocoa could omit an
Enter or Move notification while another application remained active. Second,
BUG-093 correctly constrained hit testing to the current non-transparent pixels,
but the input mask changes on every eased pill-size transition. Cocoa can emit a
Leave while replacing that mask even though the physical pointer is still over
the bar. The old Qt handlers trusted every Enter and Leave synchronously, so one
false Leave reversed the hover animation and shrank the hit region beneath the
pointer.

**Fix.** The native panel now accepts mouse-moved events without becoming key or
activating its helper process. A lightweight 32 ms UI timer also reads Qt's real
global cursor position, which covers stationary pointers and omitted native
notifications. The collapsed state's current alpha mask remains the acquisition
region, so transparent padding still passes through and cannot trigger hover.
After acquisition, a deterministic target-pill footprint retains the state while
the pill expands. Leaving that footprint restores the normal presentation. The
same state machine runs in idle, listen, think, and speak modes; hiding the
surface clears hover, and an active drag cannot leave stale controls behind.

**Guards.** Qt surface tests prove the non-activating native panel requests mouse
movement, transparent padding cannot acquire hover, mask churn cannot cancel a
real hover, leaving the visible pill restores the non-hover state, hiding clears
stale state, and all four voice modes follow the same two-state contract. The
renderer suite separately proves that the mouse-over frame differs visually in
idle, listening, thinking, and speaking modes. A physical Cocoa smoke placed a
temporary bar under a stationary cursor and then moved the window away, observing
`idle_over=True`, `speak_over=True`, and `speak_out=False` without activating the
helper application.

**Class rule.** Do not treat toolkit Enter/Leave notifications as authoritative
when the window's native hit-test region changes under the pointer. Acquire only
through visible pixels, retain against a stable intended interaction footprint,
and reconcile with the real cursor. Non-activating overlays must explicitly
prove both states in every visual mode: pointer outside means normal rendering;
pointer on the bar means controls visible and usable.

---

## BUG-096: viable Jarvis-Agent deliverables are reported as generic errors (HIGH, FIXED 2026-07-19)

**Symptom (local macOS mission forensic, platform-neutral path).** The Outputs
view showed every recent Jarvis-Agent mission as an error or cancellation even
though four runs had produced complete HTML documents between 48 and 59 KB. A
fifth worker produced a 10 KB Markdown report that remained in its disposable
worktree but never appeared in Outputs. The active database contained four
`FAILED` missions, one `CANCELLED` mission, and no `MissionApproved` event, so
the frontend was faithfully displaying an upstream lifecycle decision rather
than merely recoloring a successful row.

**Root cause.** Several independent defects compounded:

1. Direct Codex critic output uses a flat strict schema. Its reconstruction
   instantiated `CriticVerdict` directly and bypassed the tolerant validation
   used by full verdicts. A valid decision whose English or localized voice
   summary exceeded 280 characters was discarded solely for
   `string_too_long`. Live logs show approval-shaped fallback output followed
   by an adversarial retry and eventual failure.
2. The critic was told to act as a code reviewer and find at least three bugs,
   regardless of the original goal. Static reports therefore accumulated new
   polish, browser-validation, citation, or accessibility demands on each
   round. Non-blocking suggestions became blocking revisions merely to satisfy
   the quota.
3. Codex and API workers appended every correction attempt to one
   `stream.jsonl`. The current critic round could re-grade stale errors and old
   final answers after the worker had corrected them.
4. A correction prevented by the task time guard fell through to
   `critic_loop_exhausted`, so one local mission claimed three failed attempts
   despite having only two critic verdicts.
5. Shutdown cancellation changed the header to `CANCELLED` without publishing
   the canonical `MissionCancelled` terminal event. Draft artifacts were
   archived only in the task-level `finally`, so a process restart between
   `WorkerDraftReady` and critic completion could leave the canonical output
   directory empty.
6. The Outputs response returned `error: null` and no terminal reason. It could
   not distinguish a content-free failure from unsigned but reviewable output.

**Fix.** Flat Codex decisions now carry grounded per-axis status/evidence and an
explicit blocking flag. The direct prompt describes that exact flat contract,
and reconstruction goes through the same narrowly tolerant validator as every
other provider. Only presentation summaries may be truncated; every
substantive schema error and every empty evidence axis still fails closed. The
critic judges the original mission goal, revises only for a cited blocking
defect, and may approve with low/medium non-blocking suggestions. The defect
quota is removed. Each worker spawn truncates its own stream before any
subprocess setup so a failed spawn cannot expose stale evidence.

Draft files are snapshotted into the persistent mission directory before the
draft event is published. The canonical `files/` snapshot is synchronized to
the latest iteration, so a deleted or renamed first draft cannot replace the
final deliverable; earlier content remains in the per-iteration patches. A
complete sibling snapshot is staged before a portable two-phase directory
promotion, so a copy failure leaves the previous durable output untouched.
Shutdown emits exactly one canonical cancellation event, and cancellation is
rendered separately from failure on the Jarvis-Agent board. A correction
stopped by the time guard records
`review_time_budget_exhausted`, while the mission header records the actual
zero-based critic iteration without rewriting lifecycle state. Execution
failures outrank review outcomes when a multi-task mission aggregates its final
reason. The typed Outputs response exposes terminal event, terminal reason,
artifact count, `has_partial_output`, and `needs_review`. Only exhausted,
unavailable, or time-limited review with retained deliverables becomes
**Needs review**. An explicit critic rejection, setup, worker, safety, or
execution-timeout failure remains visibly failed even when a partial file was
retained.

**Safety boundary.** File existence never equals approval. The forensic set
also contained a historical 286-byte placeholder and reports with real factual
or safety findings. Only a signed `MissionApproved` event is successful. A real
blocking issue remains failed; a retained deliverable without approval remains
reviewable partial output. Historical events are not rewritten.

**Guards.** Regression coverage pins overlong flat approvals, malformed-field
rejection, contradictory blocker/axis rejection, grounded blocking revisions,
all-pass non-blocking normalization, fresh per-spawn worker logs, pre-critic
draft durability, canonical shutdown
cancellation, atomic snapshot copy-failure recovery, rename/delete archive
synchronization, truthful time-budget and multi-task failure precedence,
race-safe mission-header iteration, typed
Python/TypeScript status parity, terminal-reason propagation, narrowed
Needs-review classification, and distinct cancellation rendering. The
lifecycle contains no OS-specific status branch, so the same correction applies
to Windows, macOS, Linux desktop, and headless Linux.

**Class rule.** Treat worker execution, artifact durability, critic approval,
and user-facing outcome as four separate facts. Never infer approval from a
file, never discard a valid decision because a presentation field is verbose,
never review stale attempt logs, and never collapse retained unsigned work into
an unexplained error.

---

## BUG-097: realtime replies cut off after two seconds, resumed as untracked audio, then ended the call (CRITICAL, FIXED IN CODE 2026-07-19; LIVE DEPLOYMENT PENDING)

**Symptom (macOS live forensic, platform-neutral code path).** Long replies were
cut off roughly two seconds after first audio even though the user had not
interrupted. Audio could then resume after the surface had returned to
LISTENING, the user's next speech produced no visible transcript or answer, and
the call ended with `hangup_reason=error`. The immediately preceding call had
the same signature. The affected process was running managed-install commit
`35fda998`, at least 25 commits behind the development line at diagnosis, so
this is also a confirmed device-parity layer-1 version lag rather than a new
macOS-only diagnosis.

**Last five stored turns in session `163fca8b` (18:31-18:34).**

| Turn | Outcome | Evidence |
|---|---|---|
| 4 | Cut off | First audio at 18:32:41.288; local VAD falsely declared barge-in 1.545 s later. Speaker echo was subsequently transcribed as the user's input. |
| 6 | Completed | Surface TTS rendered the full 436-character reply and emitted `SpeechSpoken`. |
| 7 | Completed | Native realtime reply completed; PortAudio reported a first-write underflow. |
| 8 | Completed | The 21.5 s native reply completed; PortAudio again reported only a first-write underflow. |
| 9 | Cut off, resumed, hung up | First audio at 18:34:25.732; false barge-in 1.607 s later; `OutputStream` reopened after cancellation; microphone send timed out; the call ended as an error. |

Session `957b7fce` immediately before it reproduced the same sequence: first
audio, false local barge-in after 1.736 s, post-cancel stream reopen, microphone
send timeout, error hangup. This repetition and the stream timestamps rule out a
random provider sentence boundary.

**Root causes.** Three independent lifecycle defects chained together:

1. `DesktopRealtimeBargeInDetector` began its 1.5 s grace window at the logical
   SPEAKING transition. Surface synthesis and stream setup consumed about one
   second of that window before physical playback existed. The detector then
   had too little real speaker-echo calibration and classified the assistant's
   own output as user speech.
2. Surface fallback TTS called `AudioPlayer.play_chunks()` outside
   `DesktopRealtimePlayback` ownership. `tts_cancel` stopped PortAudio but did
   not cancel the async TTS producer. Its next chunk reopened the stream and
   produced the untracked post-cancel speech.
3. The old managed install treated the later write-only provider stall as a
   terminal microphone error. The development line already contains the
   in-place, key/provider-neutral transport rebuild and stale-timeout guard from
   `b9023134` and `a1657225`; the running process contained neither.

**Fix.** Commit `363be37c` starts grace and adaptive echo calibration from
`level_tap.playback_active()`, so synthesis latency cannot consume it. The
surface fallback now owns its complete playback task, invalidates older renders
with an epoch, cancels the producer before stopping the shared player, and
awaits generator shutdown. `AudioPlayer` independently generation-guards its
worker-thread stream setup: if `stop()` wins while PortAudio is still opening,
the late handle is closed instead of being published as the active stream. A
terminal session teardown invalidates the pre-task synthesis window as well.
The existing in-place transport recovery then keeps a write-only stall and any
overlapping stale timeout from ending an otherwise healthy call.

**Pause attribution.** The last five turns contain no logged mid-reply provider
gap or embedded-silence event at the existing 400 ms threshold, so their
reported half-second pauses cannot be reconstructed honestly from the available
event granularity. Earlier same-day sessions do prove 400-600 ms provider gaps
and embedded silent PCM; those remain the separate BUG-087 audio-flow issue.
This fix removes the deterministic cut/restart/hangup chain but does not claim
that every provider-generated pause is gone.

**Guards and parity.** Detector tests delay synthesis past the old grace window
and prove that a full physical-playback grace still follows. Surface tests
cancel both before the first chunk and between chunks, require generator
closure, reject a spoken receipt, forbid the second chunk, and keep the live
call open. A pre-task cancel test pins the state-callback race. Audio-player
coverage stops playback while a worker thread is opening PortAudio and proves
the late stream is closed without one write. The transport test now forces two
overlapping send deadlines, lets the first rebuild the transport, then delivers
the second as a stale timeout and verifies the fresh session still accepts
audio. These paths are pure Python plus the existing PortAudio abstraction and
contain no OS branch; `stop()` remains an honest no-op without PortAudio on a
headless install. Hardware validation on the updated managed installation is
still required before changing the status to live-verified.

**Class rule.** A cancel boundary owns the producer, buffer, device stream, and
any in-flight stream-open operation. Stopping only the current device handle is
not cancellation: an async producer or worker thread can recreate it after the
surface has already changed state. Playback grace begins at physical playback,
not at intent to play.

---

## BUG-098: one exhausted Google project breaks both Gemini Live and the Tool Model mid-call, then reconnects the same dead account (CRITICAL, FIXED IN CODE 2026-07-20; LIVE DEPLOYMENT PENDING)

**Symptom.** Realtime session `10000002-0000-4000-8000-000000000002`
ran normally for almost four minutes, then two consecutive public-knowledge
questions received improvised connection-error apologies. The API-Keys view
subsequently showed both of these failures:

- Google Gemini REST: HTTP `429`, `RESOURCE_EXHAUSTED`, project monthly
  spending cap exceeded.
- Gemini Live: WebSocket close `1011`, with the same project monthly spending
  cap message.

The two cards did not identify two integration defects. They exercised two
Google transports backed by the same account/quota family after one project
budget became unavailable.

**Reconstruction.** The Live WebSocket opened successfully at 08:51:01 CEST
and remained open until the user ended the call at 08:54:52. The session itself
did not receive a `1011`. Five earlier public-fact turns were unnecessarily
delegated to the Gemini Tool Model and succeeded, consuming 95,957 input tokens
plus 606 output tokens. At 08:54:34 the next delegated REST request returned the
monthly-cap `429`; the Brain chain correctly dead-listed Gemini but had no
second credential-ready, tool-capable API family. At 08:54:44 the retry found
an empty Tool Model chain, and Gemini TTS also returned `429`. A concurrent
API-Keys health sweep then opened a new Gemini Live probe, which Google rejected
with `1011`; that probe produced the second card message.

**Root causes.** Four independent gaps amplified one legitimate provider
account state:

1. The canonical billing classifier recognized qualified limits and quotas but
   not Google's exact `spending cap` wording, especially when the Live close had
   no HTTP status code.
2. Runtime transport recovery treated any exception from a rebuild-capable
   Gemini session as a transient wire death. It reopened the provider chain
   from candidate zero without retiring the failed credential family, so a
   terminal account failure could spend all three reconnect attempts on the
   same account.
3. The realtime model called `jarvis_action` for ordinary public facts despite
   its tool directive. The handler trusted that optional model decision even
   when both the local transcript and normalized request were provably native
   factual questions.
4. A non-empty Brain provider-down apology was counted as a successful
   delegated action result. The realtime model then re-rendered that failure as
   free-form speech, hiding the structured failure state.

**Fix.** `spending cap` and `spend cap` now classify as `no_credits`. First-party
realtime adapters expose optional `credential_family` metadata; the shared
session keeps call-local blocked provider/family sets, overrides misleading
recoverable flags for auth/billing failures, and crosses to the next viable
family without ending the call. Unknown `1006` transport failures retain the
existing same-provider rebuild, while a terminal account failure with no
alternate fails once instead of reconnecting three times. Rebuilds preserve the
existing bounded history seed, output-language decision, surface turn boundary,
and fresh `audio_ready` contract.

The delegate handler now rejects only narrowly proven public factual questions
when both the transcript and the provider-normalized request remain native.
Ambiguous actions, advice, current/private/local evidence, voice confirmations,
and the vague-follow-up recovery path still reach the Brain. Finally,
`_last_turn_all_failed` makes a non-empty outage phrase an honest failed tool
result rather than a successful action.

**Guards and parity.** Tests pin the exact REST `429` and Live `1011` messages,
Gemini-to-OpenAI and OpenAI-to-Gemini mid-call crossings, one terminal failure
when no alternate exists, preservation of generic `1006` reconnect behavior,
the public-knowledge rejection, the vague-Wiki counterexample, and truthful
delegate failure payloads. The logic is transport-neutral Python with no OS
branch. A user still needs an independently funded API key from another family
for automatic crossing; ChatGPT/Codex subscription OAuth remains correctly
limited to Jarvis-Agent workers and is not presented as an OpenAI Realtime or
Tool Model API credential.

**Class rule.** Credential readiness is not account health. A runtime
auth/billing/quota failure retires its explicit credential family for that call
before recovery chooses another candidate; a UI health probe must never be
mistaken for evidence that the active voice transport died.

---

## BUG-099: Jarvis Bar barely reacts to loud speech and sometimes jumps on soft input (MEDIUM, FIXED IN CODE 2026-07-20; HARDWARE VALIDATION PENDING)

**Symptom (desktop field report).** During an active voice session, the gold
equalizer strokes can remain only slightly taller than their resting height
even while the user speaks very loudly. At other times a much softer sound
produces a disproportionately large swing. The field screenshot supplied with
the report shows the loud-speech case: all strokes remain near minimum height.

**Root cause.** The shared `LevelNormalizer` divided each noise-gated RMS frame
by an adaptive peak. Any sample above the prior peak immediately became both
the new numerator and denominator, so even a soft sound could redefine itself
as `1.0`. Conversely, one click, clipped frame, or other transient could raise
the denominator by an order of magnitude. Its `0.997` per-frame decay then
held ordinary speech near the bottom of the meter for seconds. A reproduced
sequence of one `0.5` RMS transient followed by `0.05` RMS speech still read
about `0.10` after 30 speech frames. The renderer's additional easing correctly
smoothed that faulty input; it was not the source of the inversion.

**Fix.** The adaptive quiet-frame noise gate remains, but samples above it now
map through a fixed logarithmic visual range of approximately -72 dBFS to
-12 dBFS. A gentle response curve preserves useful movement on quiet laptop
microphones while retaining a clear ordering between soft, normal, and loud
speech. Fast attack and slower release still prevent flicker. The same mapping
now drives the native microphone channel, TTS output, the standalone mascot
listener, and browser-owned realtime capture, so no surface keeps the faulty
peak behavior.

**Guards and platform parity.** Regression tests require soft input to remain
below full scale, normal and loud speech to occupy successively higher bands,
and ordinary speech to recover within six frames after a clipped impulse. The
Python mapper feeds the Windows and Linux Tk bar directly and crosses the
existing companion IPC to the macOS Qt bar. The TypeScript port covers browser
capture on remote and headless installations. No platform-specific capture API
or device assumption changed. Physical voice validation is still required on
all three desktop platforms before removing the pending qualifier.

**Class rule.** A visual loudness meter may adapt its noise gate, but it must
not normalize every newly observed peak to full scale. Peak auto-gain is useful
for recognition input; it destroys the relative amplitude information that a
user-facing level indicator exists to show.

---

## BUG-100: high-latency speaker drain becomes a phantom user turn, and terminal teardown leaks a PortAudio task (CRITICAL, FIXED IN CODE 2026-07-20; HARDWARE VALIDATION PENDING)

**Symptom.** Realtime voice session
`10000003-0000-4000-8000-000000000003` answered normally, then transcribed the
last words coming from its own speakers as new user input. The assistant reply
ending in `Was ansteht.` was followed by a user-role transcript containing
exactly `Was ansteht.`. Earlier sessions show the same suffix pattern with
`Bescheid.` and `dir?`. <!-- i18n-allow: forensic quotes from runtime voice transcripts -->
The same run also emitted an unobserved task exception while the hotkey ended
playback: `sounddevice.PortAudioError: Internal PortAudio error [PaErrorCode
-9986]`.

**Reconstruction.** The affected Mac used its built-in speakers and microphone.
PortAudio reported `0.869 s` of output latency, while the realtime half-duplex
tail was a fixed `0.5 s`. `DesktopRealtimePlayback.finish_turn()` established
that queued PCM had been accepted by the output stream, but did not establish
that the physical device buffer was silent. The microphone therefore reopened
about `0.369 s` before the reported hardware horizon ended. One- and two-word
suffixes also fall below the text `SelfEchoGuard` minimum of three tokens, so
the later transcript layer correctly did not guess that those short utterances
were echo. Separately, terminal cancellation aborted a blocking native write
while a provider callback was still unwinding. That callback could recreate
the desktop playback task, and Core Audio surfaced the intentional abort as
PortAudio error `-9986` after its owner had already detached.

**Fix.** `AudioPlayer` now exposes the active stream's validated, bounded
PortAudio output latency. Every completed realtime output segment keeps
microphone frames local for that device latency plus the existing acoustic
tail. The local CPU barge-in detector remains active throughout the interval,
so confirmed human speech is forwarded immediately instead of being muted with
the echo. Missing or malformed latency metadata falls back to zero and retains
the conservative fixed tail.

`DesktopRealtimePlayback.close()` is now terminal: it latches the surface
closed before cancellation, rejects every later provider PCM callback, and
observes detached task results. Pipeline teardown closes this boundary before
quiescing the provider and repeats the idempotent close after `session.end()`.
`AudioPlayer` tags native writes with their playback generation and suppresses
a PortAudio write error only when the generation or stream owner proves that
an intentional cancellation already won; unrelated live-device failures still
propagate.

**Guards and platform parity.** Regression coverage reproduces a `150 ms`
device latency against a deliberately expired fixed guard, proves both the
speaker suffix and an immediate real interruption stay on the correct paths,
rejects PCM delivered by `session.end()`, and raises PortAudio `-9986` from a
write concurrently aborted by `stop()`. The output-latency property is part of
PortAudio's cross-platform stream contract and adds no macOS-only import or
device selection. Windows and Linux use the same path; a headless player with
no active stream reports zero and retains the fixed guard. Physical validation
on updated macOS, Windows, and Linux installations remains pending.

**Class rule.** Queue drain is not acoustic drain. A half-duplex boundary must
cover the output device's reported hardware latency, while terminal teardown
must seal every producer callback before aborting its native stream. Expected
abort errors may be suppressed only with positive ownership evidence.

---

## BUG-101: loud speaker echo defeats the barge-in energy floor mid-answer — the assistant interrupts itself and answers its own words (CRITICAL, FIXED IN CODE 2026-07-20; HARDWARE VALIDATION PENDING)

**Symptom.** Realtime voice session `10000004-0000-4000-8000-000000000004`
(Mac built-in speakers + built-in mic, 2026-07-20): while the assistant was
speaking about Thanksgiving, a barge-in fired with user text `Thanksgiving` —
a word only the speakers were saying — truncating the answer. The next
answer's own opening (`...voraus, wofür sie dankbar sind`) <!-- i18n-allow: forensic quote --> then came back as
the "user" turn `Voraus, wo...`, truncating that answer too. <!-- i18n-allow: forensic quotes from runtime voice transcripts -->
Unlike BUG-100 (echo after the turn's speaker drain) this echo confirmed
DURING active playback, through the barge path.

**Reconstruction.** The desktop barge detector's only acoustic echo defense
is a LEVEL discriminator: the adaptive RMS floor (BUG-084) learned during the
1.5 s grace window. Speech has dynamics — on a strongly coupled speaker/mic
pair the answer's louder syllables jump the learned 90th-percentile × 1.4
floor after the grace window closes, Silero (which cannot tell whose voice it
hears) confirms sustained speech, and the confirmed echo is forwarded as a
user barge: playback cancel + phantom turn. The text backstop
(`SelfEchoGuard`) deliberately never judged utterances under three tokens, and
barge captures truncate echo to exactly such fragments (`Thanksgiving` = 1
token, `Voraus, wo` = 2). <!-- i18n-allow: forensic tokens under test -->

**Fix (three layers, all word-, language-, and OS-agnostic).**

1. **Output envelope reference** (`jarvis/audio/echo_reference.py`):
   `AudioPlayer._write_samples` records a timestamped RMS per ~60 ms played
   block — the process's own ground truth of what the speakers emit. No OS
   audio API, no model; identical on Windows/macOS/Linux.
2. **Correlation gate in the barge detector**
   (`DesktopRealtimeBargeInDetector._candidate_matches_output_envelope`):
   before a confirmed candidate may cancel playback, its per-frame RMS series
   is Pearson-correlated against the played envelope across the plausible
   device-latency lag window (≤ 1.75 s, covering the 0.869 s worst case from
   BUG-100). Correlation is scale-invariant — volume, mic gain, and distance
   drop out; only the temporal loudness SHAPE decides. A match ≥ 0.70 is our
   own echo: suppressed, detector stays armed. Every guard fails OPEN (no
   reference, flat envelopes, snapshot error → the barge stands), so a real
   user can always interrupt.
3. **Strict short-echo judgment, barge-scoped** (`SelfEchoGuard`,
   `RealtimeVoiceSession`): only when the input originated from a
   surface-confirmed local barge during playback (`judge_short=True`), sub-3-
   token transcripts are judged with EXACT containment (no fuzzy), a length-4
   floor for single tokens, and a final-token prefix rule for mid-word cuts
   (`wo` ← `wofür`). Ordinary short answers <!-- i18n-allow: forensic token -->
   ("stopp" answering "Soll ich stoppen?") never <!-- i18n-allow: command fixture --> see the strict path — the
   BUG-084 fail-open doctrine stands. <!-- i18n-allow: command fixture -->

**Class rule (extends AP-27's lesson to barge-in).** A level threshold can
never separate two voices sharing one microphone, and a transcript-content
rule alone must stay fail-open — but the process OWNS the output signal, and
correlating against that reference is the word-agnostic discriminator that
needs no threshold tuned per room. When output audio exists, judge suspected
echo against what was actually played, in the signal domain first.

**Guards.** `tests/unit/realtime/test_desktop.py` (gate suppresses tracking
candidates, passes uncorrelated speech, fails open without reference, stays
armed after suppression), `tests/unit/audio/test_echo_reference.py` (tap +
module contract), `tests/unit/speech/test_echo_guard.py` +
`tests/unit/realtime/test_session_self_echo.py` (strict short judgment is
barge-scoped; replay of session 10000004's turns 4/5). Physical validation on
the Mac speakers+mic setup remains pending.

---

## BUG-102: audio devices frozen at process start — plugging or pulling a headset lags and breaks voice until restart (HIGH, FIXED IN CODE 2026-07-20; HARDWARE VALIDATION PENDING)

**Symptom.** Mac live test 2026-07-20: plugging wired headphones into the
MacBook made the whole voice loop lag heavily; unplugging them left voice
broken AND laggy until an app restart. The settings pinned the microphone to
the jack's device by name while output stood on automatic.

**Reconstruction.** PortAudio freezes its device table at initialization —
the process never sees devices arriving or leaving, on ANY OS. Three layers
degrade at once. (1) The persistent output stream keeps writing to the
no-longer-default or vanished device: blocking writes stall for seconds,
watchdogs fire, everything downstream feels laggy. (2) The mic stall
watchdog does reopen after ~3 s of silence — but only against the STALE
table, so it retries the dead endpoint forever and can never find the newly
arrived one. (3) Boot-time device settling (`device_init`) and the Settings
rescan (out-of-process probe) both exist, but NOTHING watched the topology
at runtime.

**Fix (`jarvis/audio/topology.py`, cross-platform by construction).**
A post-ready watcher polls the existing out-of-process device probe (safe
while this process holds live streams) every 5 s and reduces each snapshot
to a name-based signature (devices + default pair — never indices). On a
settled change it performs the ONE safe refresh sequence: discard every
registered capture stream and the player's stream FIRST (re-init with live
streams is the BUG-058 native-fault path), re-initialize PortAudio under the
established re-init lock, then invalidate every resolve/device cache and
re-resolve the configured output spec. Reopening is owned by the existing
self-healers: the mic watchdog fires on its next one-second tick (heartbeat
backdated by `discard_native_stream`) and the player reopens lazily on the
next playback. Every native stream open now holds `stream_open_guard()` so
no stream can be born between terminate and initialize. Headless installs
short-circuit before ever spawning the probe. Nothing runs on the boot
critical path (AP-26).

**Class rule.** PortAudio's device table is immutable per initialization:
any long-running voice process MUST either watch topology and re-init
(coordinated, streams quiesced first) or accept death on the first hot-swap.
A name-pinned device is an automatic choice by contract — when it vanishes,
resolution falls over to a real device and comes back when the name returns.

**Guards.** `tests/unit/audio/test_topology.py`: signature identity (index
shuffles invisible, unplug + default move visible), one refresh per settled
change, probe outage fails open, refresh order (quiesce → re-init →
invalidate → re-resolve), watchdog heartbeat backdating. Physical
plug/unplug validation on macOS/Windows/Linux remains pending.

---

## BUG-103: late action-result readback races the user's next utterance — the same answer is spoken twice in two different voices and doubles in the transcript (HIGH, FIXED 2026-07-20)

**Symptom.** Realtime voice session `10000005-0000-4000-8000-000000000005`
(2026-07-20 17:49, Gemini Live, delegate tool mode): one answer was rendered
by the surface TTS fallback voice (`gemini-flash-tts`, ~30 s) while the rest
of the session used the live provider voice, and the transcript view showed
the identical reply text twice in a row. The recorded `voice_turns` row for
that turn was completely empty (no user text, no reply).

**Reconstruction (event-log forensic).**

1. A barged delegate turn's Brain result outlived its provider turn and was
   correctly queued as a late follow-up
   (`_queue_late_delegate_result`).
2. The flush found the session "at rest" ~1 s after the turn closed and
   injected the readback (`_speak_late_delegate_result`: sets
   `_external_update(spoken_kind="action_result")`, opens a SILENT turn) —
   exactly as the user drew breath to continue talking.
3. The user's next utterance landed INSIDE that readback turn. The provider
   interrupted the still-silent readback, the deterministic delegate answered
   the user, the provider then failed to render that grounded result, and the
   surface TTS fallback spoke it (the first, different voice).
4. At turn completion the STALE `_external_update` still owned the turn:
   `_publish_turn_completed` took the external-readback branch, re-published
   the fallback answer as a second `SpeechSpoken(action_result)` (the
   duplicate transcript line), published NO `ResponseGenerated`, and skipped
   `VoiceTurnCompleted` entirely (the empty recorder row).

**Fix (`jarvis/realtime/session.py`).**

1. **User speech reclaims a silent readback turn.** When a real input
   transcript arrives while `_external_update` is set and no readback audio
   has played (`_output_samples_sent == 0`), the readback is dropped (its
   action already ran; only the spoken confirmation is lost, logged), the
   deferred `VoiceTurnStarted` is published, the turn's response request is
   reset, and — only on adapters that isolate response generations — the
   provider's now-stale readback rendering is withheld until a response for
   the user's turn exists.
2. **Belt-and-braces at completion.** `_publish_turn_completed` treats a turn
   that carries BOTH an external update and a delegate turn state as a user
   turn: the superseded readback track is discarded so the answer is
   published exactly once (`ResponseGenerated` + `VoiceTurnCompleted`),
   never re-surfaced under the readback kind.

**Class rule.** An out-of-band injection into a live duplex session can
always race the user's next utterance — "at rest" is a snapshot, not a
reservation. Any state that lets a non-user prompt own a turn must be
surrendered the moment genuine user input arrives in that turn, and turn
completion must never let a stale out-of-band marker swallow a real user
turn's record.

**Guards.** `tests/unit/realtime/test_session.py::
test_user_speech_during_silent_external_update_reclaims_the_turn` and
`::test_hijacked_external_update_turn_completes_on_the_user_track`.

---

## BUG-103: one macOS Keychain permission dialog PER stored secret — boot triggers a 5-10 dialog storm (HIGH, FIXED 2026-07-20)

**Symptom.** Every boot on the Mac popped a stack of Keychain dialogs
("python3.12 wants to use your confidential information ... cannot verify
authenticity"), one per stored provider key — the pre-boot key check reads
~10 slots, and the unsigned venv python keeps "Always allow" from sticking
durably across binary updates.

**Fix v1 (`jarvis/core/keychain_bundle.py`) — shipped broken, twice over.**
On macOS the platform keyring backend is wrapped in
`DarwinBundleKeyringBackend`: ALL Jarvis secrets live in ONE Keychain item
(`__jarvis_vault__`, JSON map) with a process-local cache. Legacy per-key
items migrate into the vault on first read and are deleted. Two independent
reasons this never stopped the storm:

1. **The wrapper never installed.** It was a plain class, but
   `keyring.set_keyring` type-checks against `keyring.backend.KeyringBackend`
   and raised `TypeError` — which the fail-open boot path swallowed, leaving
   the raw per-item backend silently active. The unit tests called the
   wrapper directly and never went through `set_keyring`, so they stayed
   green while production behavior was unchanged.
2. **One item is still one dialog per PROCESS, forever.** Since macOS Sierra
   a Keychain item carries a partition list: even an "any application may
   read" ACL silently admits only Apple-signed tools (verified empirically —
   an in-process `SecItemCopyMatching` from the unsigned uv/venv python
   blocks on the consent dialog even for a `-A` item). "Always Allow" binds
   to a code signature the interpreter does not have, so the grant can never
   stick: every fresh python process (main app, mission worker, CLI, pytest)
   re-prompts.

**Fix v2 (same module).** `DarwinBundleKeyringBackend` is now a real
`KeyringBackend` subclass (guarded by
`test_wrapper_is_accepted_by_keyring_set_keyring`, which installs it through
the real `keyring` API), and the vault item's I/O goes through the
Apple-signed `/usr/bin/security` CLI (`SecurityCliVault`): items are created
via `security -i add-generic-password -A` (payload streamed over stdin,
never argv, stored as base64 JSON), read via `find-generic-password -w`,
replaced delete-then-add. An Apple-signed tool inside the `apple-tool:`
partition reads/writes a `-A` item with ZERO dialogs, from any process,
regardless of interpreter signing — verified silent end-to-end on the Mac.
Base64 doubles as the format marker: a plain-JSON vault (written by the v1
in-process path) is upgraded to a fresh `-A` item on first CLI read.
Tradeoff, stated honestly: a `-A` item is readable by any local process
running as the user — the same trust level as the project's 0600 file-store
fallback — while staying Keychain-encrypted at rest. Any CLI failure falls
back to the v1 in-process behavior, never losing data. Reading each
still-existing legacy per-key item needs the user's consent ONE last time
(migration), after which it is deleted and never prompts again. Direct
`keyring` users (`jarvis/board/sync.py`, `jarvis/plugins/stt/groq_api.py`)
now run `_ensure_keyring_backend()` first so they cannot bypass the vault.
Windows and Linux paths are untouched (`sys.platform == "darwin"` gates
every wrap, and `darwin_security_cli_vault()` returns `None` elsewhere);
the write/read/delete backend probe runs THROUGH the wrapper, proving the
vault lifecycle. Linux-simulation tests force `sys.platform = "linux"` so
they can never route into the real Mac Keychain. Guards:
`tests/unit/core/test_keychain_bundle.py`.

## BUG-104: Gemini 3.1 rejects the rebuild's conversation seed — every transport rebuild dies with 1007 and the call hangs up mid-sentence (CRITICAL, FIXED 2026-07-21)

**Live incident 2026-07-21 08:35 (Mac, realtime session `8f1ea55c`).** Two
turns in, Gemini's server dropped the Live WebSocket (`1006 abnormal
closure` — the known BUG-071 idle-drop right after a long surface-TTS
fallback). The in-place rebuild (BUG-071) reconnected fine, then the
BUG-088 history seed replayed the call transcript via
`send_client_content(turns=..., turn_complete=False)` — and Gemini killed
the fresh connection with `1007 Request contains an invalid argument`
~70 ms after `ready`. All three rebuilds in the 120 s window failed
identically, `RealtimeTransportDead` fired, and the call ended with
`hangup_reason=error` while the user was mid-sentence; the words spoken
during the death loop were never transcribed (the transport was already
dead, and the committed-turn guard rightly refuses classic-voice replay).

**Root cause — two API-contract violations in the seed, plus a blind
guard.** Gemini 3.1 Live (`gemini-3.1-flash-live-preview`) accepts
`client_content` ONLY as *declared* initial history: (1) the connect config
must set `history_config.initial_history_in_client_content=True`, and
(2) the server processes `client_content` until one arrives with
`turn_complete=True` — only after that boundary is `realtime_input`
(microphone audio) legal. The seed did neither. And because
`send_client_content` succeeds client-side (the rejection arrives as a
server-side close), the seed's fail-open `try/except` never saw the
failure — the rebuild loop re-poisoned every fresh connection until the
budget was gone. The seed path had never run on this install before
(first-ever rebuild); it was only ever validated against fakes.

**Fix (3 layers).**
1. `gemini_live.open_session` declares
   `history_config(initial_history_in_client_content=True)` whenever a
   non-empty history seed is present — probed via the SDK's
   `types.HistoryConfig` capability, never a model-name pin (AP-21); an SDK
   without it skips the seed (amnesiac but alive).
2. `_seed_history` ends the seed with `turn_complete=True` (closing the
   declared history phase so microphone audio becomes legal), also when
   every entry filtered out, and best-effort closes the phase even when
   seed construction fails.
3. `session._rebuild_transport` treats a second rapid death inside the
   rebuild window as seed poisoning and retries WITHOUT the seed — a
   server-side rejection can never again burn the whole rebuild budget,
   for any provider adapter.

Guards: `tests/unit/realtime/test_gemini_live.py` (seed declaration +
terminator + SDK-capability skip + blank-history terminator),
`tests/unit/realtime/test_session.py::test_second_rapid_rebuild_drops_the_poisoned_history_seed`.

## BUG-105: Computer-Use missions start blind to the conversation — a corrective follow-up launches an operator that only executes the correction's literal words (HIGH, FIXED 2026-07-21)

**Symptom (live 2026-07-21 10:33, realtime voice, gemini-live).** Turn 5:
"find the latest post of <person> via computer use" — the mission ran in
Safari and succeeded. Turn 8: "you must do it in my Chrome browser, I'm
logged in there — you're in Safari." The new mission opened Chrome, saw a
stale old tab, and declared success: it never knew the actual task was
opening that post, nor that the previous attempt had used the wrong
browser. Turn 9 ("you should ALSO open the latest post") went back to
Safari and died on a login wall.

**Root cause — context is dropped twice on the way into a mission.**

1. **The goal is written context-free by design.** The router prompt
   (`jarvis/brain/router.py`) mandated `computer_use(goal=<utterance
   VERBATIM>)` and the tool schema (`computer_use_tool.py`) said "Pass the
   user's request verbatim as 'goal'". Verbatim protects multi-step
   requests from paraphrase-loss, but for an ELLIPTICAL follow-up it
   guarantees ellipsis-loss: the tool-brain HAS the bounded conversation
   history (`history_override`, session.py `_dispatch_brain_turn`), and
   was instructed to throw it away.
2. **The mission itself has no memory of prior missions.** The CU engine's
   decide prompt contained only `GOAL:` + its own per-run step history;
   `cu_run_registry` records every mission's goal and outcome for the
   REST/CLI control surface, but nothing on the launch path ever read it.

**Fix (two layers, all launch routes).**

1. **Self-contained goals** — the router prompt gained an explicit
   elliptical-follow-up exception (keep the user's words AND fold in the
   referenced task, target app/browser, and what the previous attempt got
   wrong), and the `computer_use` tool description/schema now demand one
   complete self-contained instruction ("the operator sees only this
   text, never the chat").
2. **Deterministic prior-mission context** —
   `cu_run_registry.recent_runs_context()` renders the recently FINISHED
   runs (≤2, ≤15 min, char-clamped) and `jarvis/cu/engine.py` folds the
   block into the DECIDE prompt as "EARLIER DESKTOP MISSIONS … context
   only — do NOT redo them". Route-agnostic (voice fast path, tool-brain,
   REST/CLI all register runs) and prompt-compliance-free. The done-judge
   deliberately never sees it: a prior mission's outcome must not count
   as proof for the current goal.

Guards:
`tests/unit/harness/test_cu_run_registry.py::test_recent_runs_context_*`,
`tests/unit/cu/test_engine_loop.py::test_recent_missions_reach_the_decide_prompt_but_not_the_judge`.

**Class rule.** Any sub-system that executes a REQUEST derived from a
conversation must either receive the conversation's relevant context
structurally (not by prompt-compliance alone) or have its request
composed as self-contained — an instruction to pass user words
"verbatim" must always carry the elliptical-follow-up exception.

## BUG-106: confident fact fabrication + garbled-entity drift — the voice stack invents niche figures, researches the wrong aircraft, and contradicts its own search results (HIGH, FIXED IN CODE 2026-07-21; LIVE VALIDATION PENDING)

**Symptom (live 2026-07-21 11:36, realtime voice).** "Kann eine Gulfstream 800 in St. Moritz landen?" <!-- i18n-allow: quoted user utterance under test -->
produced three stacked failures in one 90-second call:

1. **Fabricated verdict, native turn.** The realtime model answered a flat
   "no, the runway is far too short for such a large aircraft" and, on the
   follow-up, asserted "about 1,800 meters" as the runway length. Reality:
   Samedan's runway is 1,840 m and Gulfstream's published G800 landing
   distance is ~946 m — a landing is feasible under conditions; the flat
   "no" was invented, delivered with full confidence.
2. **Garbled entity taken literally.** The user's next turn arrived from
   STT as "braucht die Golf braucht die Golf 100 Start- und Landebahn". <!-- i18n-allow: quoted garbled STT transcript under test -->
   The realtime model's own ack correctly resolved this to "die Gulfstream
   achthundert" — but the downstream router received the raw transcript,
   took "Golf 100" literally, and researched the Gulfstream G100, a
   completely different (retired, super-midsize) aircraft.
3. **Conclusion contradicting the fresh data.** The router's delivered
   answer said the G100 needs ~1,644 m of runway "deutlich mehr, als in
   St. Moritz verfügbar — damit kommst du dort also definitiv nicht weg". <!-- i18n-allow: quoted live reply under test -->
   1,644 m is LESS than the 1,840 m available: the model bent its own
   search result to match the fabricated "too short" claim sitting in the
   conversation history.

**Root causes.**

1. The stale-knowledge guard (417248ee) covers only TIME-SENSITIVE facts.
   Nothing told the realtime model that its recall of niche technical
   figures (dimensions, performance data, specs) is unreliable even where
   nothing changes over time, or that categorical feasibility verdicts
   ("cannot land / will not fit") must not rest on remembered numbers.
2. The router prompt had the BUG-105 elliptical-follow-up exception for
   computer_use goals, but no rule that a speech transcript garbles entity
   names: a sound-alike variant of an entity under active discussion
   ("Golf 100" vs the discussed "Gulfstream 800") was treated as a new
   literal entity in the search query and the answer.
3. No rule ranked fresh tool data above the assistant's own earlier claims
   in history, so the earlier fabricated "too short" verdict anchored the
   interpretation of contradicting search numbers.

**Fix (prompt-side, both brains).**

1. `jarvis/realtime/session.py` — a `precision_line` next to the freshness
   guard: never present a remembered niche figure as exact, never rest a
   categorical verdict on one; give a marked estimate, name what the
   answer depends on, offer to check ("check" then counts as an explicit
   action request).
2. `jarvis/brain/router.py` — new SPOKEN-INPUT CONTINUITY section:
   sound-alike entity variants resolve to the entity under active
   discussion (in answers, search_web queries, and every goal handed to a
   tool/worker); switch only on a clearly new entity; ask once when truly
   ambiguous. And: conclusions must follow from fresh tool data, which
   outranks the assistant's own prior statements — contradictions are
   corrected plainly, never bent to match.

**Class rule.** A voice assistant's input is a lossy transcript and its
recall of niche figures is a language model's, not a database's. Every
answer layer must (a) rank fresh tool data above remembered figures and
above its own prior claims, and (b) resolve entities against the live
conversation, never against the literal garble of one turn.

Guards:
`tests/unit/realtime/test_session.py::test_session_instructions_carry_the_precision_guard`,
`tests/unit/brain/test_router_prompt_continuity.py`.

## BUG-107: a delegated knowledge question launches Computer Use — the router googles the answer in the user's live browser (HIGH, FIXED 2026-07-21)

**Symptom (live 2026-07-21 11:36, the same voice session as BUG-106).**
The user asked what runway length the aircraft needs — a pure information
question, no action requested. The realtime layer correctly delegated the
turn to the router brain (tool model). The router then called
`computer_use`: a mission opened Safari on the user's screen, clicked the
address bar, typed "Gulfstream G100 runway requirements" into Google, read
the results and announced the answer — ~35 seconds of hijacked desktop
(`[cu] mission profile: steps=6 total=34.3s`) for a lookup the tool model
can do invisibly via its own knowledge or `search_web`.

**Root cause.** Nothing enforced "web research never drives the live
desktop":

1. The `search_web` freshness doctrine actively discourages it for
   evergreen facts, while the `computer_use` description advertised
   browser operation ("open Chrome and go to gmail") without ever saying
   it is not a lookup path — so a model committed to "searching" reached
   for the browser.
2. Every existing deterministic guard covered a different tool class: the
   spawn gate only `spawn_worker`/`multi_spawn`, the research-intent guard
   only CLI/MCP action tools. `computer_use` had no gate at all.

**Fix — the spawn-gate lesson applied to the desktop (a tool description
is advice, not enforcement).**

1. **Deterministic gate** `jarvis/brain/cu_gate.py`: an LLM-chosen
   `computer_use` call executes ONLY when the user's own turn names the
   desktop vehicle (on-screen action verb or screen/app/browser noun,
   DE/EN/ES input vocabulary — bare "start"/"type" deliberately excluded:
   "Start- und Landebahn" and "what type of X" are question forms), OR a
   Computer-Use mission is active/recently finished
   (`cu_run_registry.has_recent_run`, 15 min) so the BUG-105 corrective
   follow-ups ("try again") keep working. Blocked calls return a
   structured error redirecting the model to answer inline or via
   `search_web`. Enforced in BOTH consumers: `tool_use_loop` (pipeline +
   realtime delegate mode) and `realtime/tools.py` (direct tool mode).
   Empty turns fail OPEN so scheduled/REST desktop missions never brick.
2. **Advice layers aligned** — the `computer_use` tool description now
   states "NOT a research tool" with the explicit-browser exception, and
   the router prompt gained a NEVER-FOR-RESEARCH rule next to the BUG-105
   exception.

Guards: `tests/unit/brain/test_cu_gate.py` (pins the synthetic utterance, the
explicit-ask pass-through, and the follow-up window).

**Class rule.** Every tool that acts on the user's visible world (desktop,
browser, phone, telephony) needs a deterministic explicit-ask gate, not
just prompt discouragement — the model reaches for the most concrete tool
it sees, and a question must never be answered by performing.

## BUG-108: local speaker death (PortAudio -9986) is misclassified as provider transport death — three answers voiced into a dead output, all reported healthy (CRITICAL, FIXED 2026-08-20)

**Symptom (live 2026-07-21 11:53, session `290b610c`, first call after the
1b191b88 deploy/restart at 11:52).** The user asked for the best private
jet currently on the market. The transcript view shows a complete answer
(G700/Global 7500/Phenom 300E) — but the user heard NOTHING and saw
nothing during the call. A repair attempt ("Was?") got a confidently
voiced answer about a **prior question that never existed**: "Du hattest
mich gerade gefragt, was du beachten solltest, wenn du nach Bora Bora <!-- i18n-allow: quoted live runtime voice output under forensic analysis -->
auswandern möchtest" — no recorded session ever mentions Bora Bora. <!-- i18n-allow: quoted live runtime voice output under forensic analysis -->
The user hung up via hotkey at 11:55:11.

**Forensic timeline (desktop log + flight recorder).**

- 11:54:07 turn 0 delegated deterministically (path=orchestrator,
  reasons=current_data). OpenAI dead (429 quota) → Fallback-Hit
  gemini-3.5-flash at 11:54:19 — the answer text was real and arrived.
- 11:54:16 turn 0 completed with `jarvis_text=""`, `hangup_reason=none`
  (empty but "healthy"). The late delegate result was queued as a
  follow-up (correct BUG-104-era behavior).
- 11:54:17 **first playback attempt: `Error opening OutputStream:
  Internal PortAudio error [PaErrorCode -9986]`** — the local output
  device ("Externe Kopfhörer", Bluetooth) had vanished at the CoreAudio <!-- i18n-allow: literal CoreAudio device name from the log -->
  layer. The receive-loop's generic exception handler
  (`jarvis/realtime/session.py:2551` "pump ended") classified this LOCAL
  device failure as PROVIDER transport death and rebuilt the Gemini
  session (1/3).
- 11:54:20 follow-up turn: Gemini voiced the jet answer
  (`realtime_first_audio` 861 ms, transcript streamed to the live UI) —
  into the same dead output stream. Zero audible frames. Turn "completed"
  after 24.8 s, again `hangup_reason=none`.
- 11:54:44 rebuild died instantly with the SAME -9986 → retried
  **without the in-call conversation seed** (2/3).
- 11:54:54 "Was?" took the native_realtime path. The seed-less session
  had no in-call context and **fabricated** the Bora-Bora prior topic,
  voicing it as established conversation history. Playback died again
  (-9986, 3/3).
- After hangup, the MIC side proved the device was gone: input device 0
  failed with the same -9986 three times and capture fell back to
  device 2 (`jarvis/audio/capture.py:854-887`) — the INPUT path has a
  multi-candidate fallback; the OUTPUT path has none.

**Four distinct defects.**

1. **Misclassified recovery target.** An output-device open failure is a
   LOCAL audio problem; rebuilding the provider websocket can never heal
   it. The handler burned the whole 3-rebuild/120 s budget replaying
   audio into a dead pipe. Classify `sd.PortAudioError` raised from the
   playback path separately from transport errors.
2. **No output-device fallback.** Mirror the capture-side candidate
   fallback: on open failure, re-resolve the output device (the BT set
   was gone; built-in speakers were available the whole time).
3. **No honest zero-frame receipt.** Both answer turns reported healthy
   and the transcript shows text the user never heard. Same prescription
   as the 11:01 topology-refresh forensic: a turn whose playback wrote
   zero frames must not report healthy — surface an explicit "I couldn't
   play audio" signal (and re-deliver once the output is back).
4. **Seed-less rebuild fabricates memory.** Dropping the in-call seed is
   a valid last resort (BUG-104), but the model must then be told the
   context was lost — otherwise it invents one ("Bora Bora") and asserts
   it as shared history, which is worse than admitting the gap.

**Fix (2026-08-20).**

1. `is_local_output_error` classifies PortAudio device death. The pump
   does not rebuild the provider for it. `_complete_surface_turn` and
   `_emit_audio` catch it, zero the heard-sample counter, and speak an
   honest "speaker disappeared" line.
2. `AudioPlayer.recover_output_device` walks auto-headset order skipping
   the dead index; `_open_output_stream` retries on that device so the
   current turn can land on built-in speakers.
3. A turn whose playback wrote nothing no longer publishes SpeechSpoken.
4. A seed-less rebuild (`history_lost=True`) injects a standing order:
   this is a fresh session, do not invent prior topics.

**Guards.** `tests/unit/audio/test_player_output_fallback.py`,
`tests/unit/realtime/test_local_output.py`.

Related: BUG-102 (topology refresh), BUG-104 (rebuild seed), BUG-106
(the answer itself also missed the Gulfstream G800, in service since
2025 — fact-quality family, tracked there).

## BUG-109: every dictation app, text expander, and auto-type tool goes dead inside the Jarvis window — the desktop app was running elevated (HIGH, FIXED 2026-07-25)

**Symptom (desktop field report).** Dictation software — one commercial
dictation app in the report, but the class is open: the OS's own accessibility
voice control, other voice-typing tools, text expanders, clipboard managers,
password-manager auto-type — works everywhere on the machine except inside the
Personal Jarvis desktop window.
The user speaks, the dictation app records and reports success, and no text
ever appears in the Jarvis chat box. Nothing logs an error on either side, so
from the outside it reads as "Jarvis breaks my dictation app".

**Root cause.** The desktop process was running **elevated** (HIGH integrity,
`elevated=True`), while every ordinary user application including the dictation
tool runs at MEDIUM integrity. Windows **UIPI** (User Interface Privilege
Isolation) forbids a lower-integrity process from injecting synthetic input
into, or reading the UI-Automation tree of, a higher-integrity window. Both
mechanisms every tool in this category depends on are therefore cut:

* **Field detection.** Measured against the live elevated window, a normal
  automation client saw **1** element and **0** text fields; an ordinary
  window in the same session yielded 418 elements / 80 fields.
* **Text insertion.** Microsoft documents that `SendInput` blocked by UIPI
  reports the failure through *neither* its return value *nor* `GetLastError`
  — which is exactly why the dictation app believes it succeeded.

The WebView engine was ruled out by control experiment: an identical
pywebview/WebView2 window launched **unelevated** accepted both synthetic
unicode typing and a Ctrl+V clipboard paste. Privilege level was the only
differing variable.

Two properties made this durable and invisible:

1. **Elevation is sticky.** `POST /api/settings/restart-app` spawns the
   relauncher as a child, so every in-app restart inherited the elevated
   token. Once elevated, always elevated.
2. **Nothing checked.** The app is *designed* to run unelevated — the
   autostart scheduled task pins `RunLevel=Limited` (see
   `jarvis/autostart/windows.py`) and privileged operations go through the
   separate helper in `jarvis/admin/` — but no code verified the assumption,
   so a single elevated manual start silently persisted across reboots.

**Fix.**
* `jarvis/platform/input_isolation.py` — cross-platform probe reporting
  whether outside input software can reach our window (Windows: token
  elevation; POSIX: euid 0). Fail-open: an unreadable privilege state reports
  `unknown` and warns about nothing, so no user is nagged on a guess.
* `jarvis/platform/deescalate.py` — relaunch through the elevated token's
  filtered companion token (`TokenLinkedToken` → `DuplicateTokenEx` →
  `CreateProcessWithTokenW`), landing the fresh window at ordinary integrity.
  Refuses honestly where no companion token exists (UAC disabled, built-in
  Administrator, SYSTEM) instead of quietly relaunching elevated again.
* `DesktopApp._schedule_restart(drop_elevation=...)` +
  `request_unelevated_restart()` — a failed de-escalation leaves the app UP
  and returns the reason; a restart that never comes back is worse than the
  defect.
* `GET /api/settings/input-isolation` + `POST /api/settings/restart-unelevated`
  (mission-guarded, `x-jarvis-dangerous`), hence CLI-reachable.
* `InputIsolationBanner` — app-wide alert naming the affected software in
  plain language with a one-click "restart without admin rights", localized in
  de/en/es. Renders nothing when the window is reachable.
* Boot-time warning from `_log_input_isolation()` on the background heavy-init
  task (AP-26), so a support log shows the condition.

**Regression guards.** `tests/unit/platform/test_input_isolation.py`,
`tests/unit/platform/test_deescalate.py`,
`tests/unit/ui/test_input_isolation_route.py`,
`src/components/layout/InputIsolationBanner.test.tsx`. All inject the privilege
verdict — none read the host's real state, so they hold on an elevated Windows
box, in a Linux container, and on a Mac.

**Class lesson.** "Works everywhere except in *this one app's window*" is the
signature of a privilege boundary, not of the app's UI framework. Check the
integrity levels of both processes *before* investigating the window toolkit —
the measurement takes seconds and the toolkit hypothesis costs hours.

**Three follow-ups the live check caught — none of which any unit test saw.**

1. **The probe answered "unknown" on every Windows host.** Undeclared `ctypes`
   prototypes default to a 32-bit int, truncating the 64-bit handle from
   `GetCurrentProcess`; `OpenProcessToken` then failed and the probe returned
   `None`. That is *indistinguishable from the deliberate fail-open*, so the
   banner would simply never appear and the entire fix would sit inert. Fixed by
   declaring `argtypes`/`restype` on every call, and guarded by a cross-check
   against `shell32.IsUserAnAdmin()` — a disagreement (including `None` where it
   says False) now fails the suite.
2. **`TokenLinkedToken` is not a usable source** (`DuplicateTokenEx` → 1346,
   `ERROR_BAD_IMPERSONATION_LEVEL`): without `SeTcbPrivilege` Windows hands the
   companion token out at *identification* level, from which no primary token can
   be duplicated. The desktop shell's token is now the primary source (Explorer
   always runs as the plain interactive user, access flows downward, and it also
   works with UAC disabled); the linked token stays as a fallback.
3. **`CreateProcessWithTokenW` rejected the relauncher's flags**
   (`ERROR_INVALID_PARAMETER`, 87): that API implies `CREATE_NEW_CONSOLE`, which
   Windows forbids combining with the `DETACHED_PROCESS` the desktop relauncher
   normally passes. `token_creationflags()` strips it and keeps
   `CREATE_NO_WINDOW`.

**Live verification (2026-07-25).** Boot log wrote `Input isolation ACTIVE
(reason=elevated)`; the repair was refused honestly three times while the app
stayed up (errors 1346 then 87); after the fixes the log wrote `relauncher
spawned WITHOUT administrator rights`. The fresh process measures MEDIUM
integrity, the endpoint reports `blocked: false`, and the automation elements a
normal-privilege client can see in the Jarvis window went from **1 to 22**.

**Meta-lesson.** A fail-open diagnostic can perfectly disguise its own breakage.
Any probe whose "I don't know" path is silent needs a cross-check against an
independent implementation, or it will report "nothing to see here" forever.

## BUG-110: a terminal pane in the Agentic IDE offers "Drop to put it in front of Dana" to a user holding nothing (LOW, FIXED 2026-07-27)

**Symptom (desktop field report, screenshot).** In the Agentic IDE, one pane
suddenly dimmed behind a blurred overlay reading "Drop to put it in front of
**Dana**" with a paperclip icon — while the user was dragging no file, and had
no file anywhere in hand. The overlay cleared on its own moments later. Nothing
was lost and no agent was disturbed; it read as the UI hallucinating a
drag-and-drop that was not happening.

**Root cause — two independent halves, both in the pane's drop arming**
(`jarvis/ui/web/frontend/src/components/agentic/AgenticTerminal.tsx`):

1. **The pane armed on ANY drag, never asking what it carried.** `onDragEnter`
   set `dragging = true` unconditionally and `onDragOver` reported
   `dropEffect = "copy"` to match. A browser fires `dragenter` for every drag
   that crosses an element, and the cheapest way to start one by accident is a
   TEXT selection: brush across terminal output or a header label with the
   mouse held down and the browser lifts the selection into a native drag. An
   internal Outputs mission card tossed across the grid did the same. Every one
   of them lit the file overlay of whichever pane it flew over.
2. **Disarming depended on a `dragleave` that is not owed.** The enter/leave
   counter (`dragDepth`) is the correct answer to child-element flicker, but it
   only balances when every enter gets its leave. A drag released over another
   element, dropped outside the window, or cancelled with Escape sends this pane
   nothing at all — the overlay then sits over a live agent until some later
   drag happens to balance the count.

Half 1 explains what the user saw; half 2 is the same defect's worse outcome —
an overlay that never clears — and was fixed with it.

**Fix.** The arming logic moved into
`jarvis/ui/web/frontend/src/components/agentic/paneFileDrag.ts`:

- `dragCarriesFiles()` (`paneDrop.ts`) gates the overlay on the drag's TYPES —
  the only thing a browser exposes mid-flight, by design. `Files` (Explorer,
  Finder) and `text/uri-list` (some Linux file managers) arm the pane;
  `text/plain` alone does not, and neither does the internal mission MIME.
- `dropEffect` follows the same gate, so the cursor stops promising a copy
  where a drop would do nothing.
- `dragenter`/`dragover` still call `preventDefault()` for payloads the pane
  refuses — otherwise a dropped link would NAVIGATE the window and take every
  agent in the grid with it.
- A window-level backstop (`drop`, `dragend`, and a viewport-edge `dragleave`
  with `relatedTarget === null`) disarms the pane whenever the drag ends
  anywhere else — the same pattern `JarvisDock` already uses.

**Guards.** `paneDrop.test.ts` (types gate: files vs. dragged text vs. mission
card) and `paneFileDrag.test.ts` (arming, child-element flicker, copy-cursor
honesty, and all three disarm paths).

**Class rule.** A drop target may only announce itself for a payload it can
actually accept, and must never rely on a matching `dragleave` to take that
announcement down. `dragenter` is a "something is moving over you" signal, not
a "the user is offering you a file" signal.

## BUG-111: typing into an Agentic-IDE terminal lags 2-3 s and then arrives in a burst (HIGH, FIXED 2026-07-27)

**Symptom (desktop field report).** Characters typed into a terminal pane took
seconds to appear. Users concluded the keystrokes were lost, retyped them, and
the backlog then flushed at once — running the command twice. It got worse the
more panes a workspace held and the busier the agents in them were. Measured on
the live machine at the time: **63 panes open in one workspace**, most of them
idle, the backend burning two cores.

**Root cause — an unbounded fan-in with no pacing, on both sides of the wire.**

1. **Backend: one coroutine and one websocket frame per PTY read**
   (`jarvis/terminal/pty_manager.py`). The reader thread scheduled
   `on_output(...)` through `run_coroutine_threadsafe` for every single
   `read()`, and each one queued behind the socket's send lock. The producer
   runs at PTY speed and the consumer at browser speed, so with enough panes
   the queue grew faster than it drained — and every new chunk, including the
   echo of the key just pressed, joined the END of it. The delay was therefore
   not constant but *compounding*, which is why it presents as "nothing, then
   everything at once" rather than as a steady lag.
2. **Frontend: every pane drew, whether or not anyone could see it**
   (`.../components/agentic/AgenticTerminal.tsx`). A pane scrolled out of the
   grid, or hidden with `display:none` behind a maximized sibling, still ran
   `term.write` for every chunk — parsing the escape stream, updating a cell
   grid, scheduling a repaint. All of it on the one browser main thread that
   also has to notice a keypress, and all of it for pixels nobody could see.

Both halves multiply with the pane count, which is why the feature was fine at
two panes and unusable at sixty.

**What it was NOT.** The PTY read path, the write path, and asyncio dispatch
were all measured at well under a millisecond, and idle panes cost nothing. The
screen emulator (`agentic_ide/screen.py`) runs at ~4 MB/s and was never the
limit. Frame COUNT was the cost, not bytes.

**Fix.**

- `PtyManager` gained a per-session **output pump**: the reader thread appends
  to a bounded buffer and wakes the pump, and the pump forwards everything that
  accumulated while the previous send was in flight as ONE chunk. It is
  self-clocking — an idle pane forwards its first chunk immediately, with no
  timer and no added latency — so a slow viewer costs frame count, never queue
  depth. Past `MAX_PENDING_CHARS` the OLDEST output is dropped, for the reason
  the replay buffer drops it: a terminal UI repaints continuously, so the newest
  bytes are the screen. `on_closed` now fires after the final flush, so a caller
  can no longer be told the agent exited while its last words are still
  buffered.
- An empty non-EOF read now backs off instead of spinning, which the comment
  above it had claimed since it was written.
- The pane writes only when it is on screen (`offscreenBuffer.ts` +
  an `IntersectionObserver` in `AgenticTerminal.tsx`); hidden output is parked
  and replayed in a single `term.write` when the pane comes back. Bounded the
  same way, and every pane write — output, exit banner, trouble notice — goes
  through the same gate so ordering survives.

**Measured, 45 concurrent panes redrawing a full-screen UI (backend):**

| | before | after |
|---|---|---|
| event-loop lag, mean | 45-126 ms | -4.5 ms |
| event-loop lag, p99 | 482-2227 ms | 16-17 ms |
| event-loop lag, max | 496-3344 ms | 21-30 ms |
| keystroke echo, mean | 3.4-3.9 s | 1.1 s |
| keystroke echo, max | 6.0-8.0 s | 1.5-1.6 s |
| output actually delivered | 0.46-0.72 MB/s | 4.1 MB/s |

The last row is the tell: the old path was so far behind that it could not even
carry what the agents produced, in MORE frames than the new one uses.

**Measured, 63 panes in a real browser (frontend):** main-thread scheduling
delay mean 52-60 ms -> 25-30 ms, p99 102-124 ms -> 79-81 ms — i.e. from above
the 100 ms interactivity budget to below it.

**Guards.** `tests/unit/terminal/test_pty_output_pump.py` (first chunk not
delayed, backlog coalesced, oldest dropped under flood, exit after the final
output, a broken viewer does not stop the PTY, an idle PTY does not spin);
`offscreenBuffer.test.ts` and `AgenticTerminal.offscreen.test.tsx` (withhold,
replay in one write, ordering, and no gating where `IntersectionObserver` is
absent).

**Class rule.** Whenever a thread produces into an event loop for a consumer
that can be slower than the producer, forward the BACKLOG, not each item: one
wake-up and one coalesced hand-off, bounded, dropping the stalest. Per-item
scheduling turns a slow consumer into a compounding delay, and the first thing
that delay eats is the user's own keystrokes. The same rule governs the far end
— never render for a surface nobody is looking at, because that thread is
usually the one holding the keyboard.

## BUG-112: the subscription switcher names the wrong account — panes run on a plan the user never selected (HIGH, FIXED 2026-07-27)

**Symptom (desktop field report, with screenshots).** In *Workspace settings →
Your subscriptions*, two Claude rows showed the SAME email, and the row marked
"in use" named a subscription the user was not actually on. The only visible
evidence was arithmetic: the account named in the switcher had ~9 % of its
weekly limit left, while the plan the terminals were really spending had ~1 %.
Nothing in the UI was red — both rows were green, both said "Signed in via
Claude Max", and the user could only tell something was wrong by comparing the
usage page against what the switcher claimed.

**Root cause A — a fixed preference over two identity files.** Claude Code can
keep its signed-in identity in either `~/.claude.json` or
`~/.claude/.claude.json`, depending on release. `claude_account_identity()`
built a candidate list and returned the FIRST one that parsed:

```python
candidates = [config_dir / ".claude.json"]        # ← always won
if config_dir == Path(os.path.expanduser("~/.claude")):
    candidates.append(Path(os.path.expanduser("~/.claude.json")))
for candidate in candidates:
    ...
    if email or name:
        return email, name
```

On the reporting machine `~/.claude/.claude.json` was an abandoned copy from an
earlier release, last written **ten days** before the report; the live
`~/.claude.json` next to it was minutes old and named a different account. The
list order alone decided, so the switcher confidently displayed the previous
occupant of that directory and the default login appeared to be someone it had
not been for a week and a half.

The same ordering bug sat in `ClaudeAuthService._identity_path()`, which
additionally fell back to `~/.claude.json` for a CUSTOM config dir — so an
added account with no identity file of its own borrowed the DEFAULT login's
email and displayed it as its own. That fallback existed to make sure the card
"still shows an email"; showing the wrong one is worse than showing none.

**Root cause B — a second sign-in silently reuses the first account.** The
CLI's OAuth flow is a browser redirect. With a live claude.com session, the
browser approves the new authorization against the account it is already signed
in with, without an account chooser and without ever reaching the "paste this
code" step — the sign-in terminal sits at `Paste code here if prompted >`
while the browser page just says the user is signed in. The result is two
registered accounts holding ONE subscription. Both rows go green, both name a
valid plan, and the only symptom is one plan's usage draining twice as fast:
precisely the feature's whole purpose, inverted, with no signal anywhere.

**Fix.**
- `jarvis/claude_auth.py` — `claude_account_identity()` now picks the
  **freshest readable** candidate by mtime instead of the first in a list: the
  file the installed CLI actually keeps current is the truth, the same
  "most recently refreshed wins" rule `freshest_claude_oauth()` already applies
  to credentials. `_identity_path()` follows the same rule, and a custom config
  dir no longer falls back to the home identity — no email beats a wrong one.
- `_is_default_config_dir()` builds the comparison from `Path.home()` and
  compares through `os.path.normcase`, replacing a hand-written `"~/.claude"`
  string, so the home-level candidate is considered on macOS and Linux too and
  survives a differently-cased Windows path (CLAUDE.md §3).
- `jarvis/agent_accounts.py` — `snapshots()` groups connected accounts by email
  and flags every row after the first as a duplicate of the account it collides
  with, naming it and saying how to avoid it (sign out at claude.com, or use a
  private window). Accounts with no readable email are never grouped: "both
  unknown" is absence of evidence, not evidence of sameness. New display-safe
  `AccountSnapshot.warning`, carried through `to_dict()` and rendered by
  `AgentAccountsPanel`.
- `jarvis/ui/web/agent_accounts_routes.py` — the sign-in response now states
  the browser-session trap up front, at the moment the user is about to hit it.

**Deliberately NOT done.** An "identity older than its credentials is stale"
guard reads like the obvious extra safeguard and is wrong: the CLI refreshes
the bearer in place indefinitely while never rewriting the identity, so on any
long-lived account the identity is legitimately the older file. That rule would
blank the email on exactly the accounts that work. It is written into the
docstring so it does not get "fixed" back in.

**Regression guard.** `tests/unit/test_claude_auth.py` — freshest-wins in both
directions, a custom dir never borrowing the default's email, a custom dir
reporting its own, and `_is_default_config_dir` built without a hardcoded
separator. `tests/unit/test_agent_accounts.py` — two accounts on one
subscription flagged (naming the collision partner), two genuine subscriptions
never flagged, no grouping without an email, and the warning surviving
`to_dict()` while no token does.
`AgentAccountsPanel.test.tsx` — the warning renders on the colliding row and
not on the one it duplicates.

**Class rule.** When two files on disk can answer the same question, never let
declaration order decide — take the freshest, because which one an external
tool keeps current is that tool's choice and changes between its releases. And
an identity read for account A must never be satisfied from account B's file:
a confident wrong name sends work to the wrong plan, where the only feedback is
a number on a billing page nobody is watching.

---

## BUG-113: a workspace of Agentic-IDE terminals freezes on screen while every agent behind it keeps working (HIGH, FIXED 2026-07-27)

**Symptom (desktop field report).** "All the terminals you see here are frozen.
You can't write in them any more — the only one you can still type into is one
you spawn fresh." The panes drew a screen that never changed again; typing into
them appeared to do nothing.

**What was actually true at that moment.** Measured on the reporting machine
while the panes were frozen:

- `GET /api/agentic-ide/state` reported all 16 panes `live`, four of them with
  `idle_seconds` of 0.0-0.2 — agents printing *right now*.
- `netstat` showed 17 established connections to the backend port: the panes'
  WebSockets were open, not dropped.

So neither the agents nor the wire were broken. The keystrokes even arrived —
input is written straight to the PTY and never touches the viewer — but with
nothing coming back, a pane that echoes nothing is indistinguishable from a pane
that ignores you.

**Root cause A — a departing viewer released a slot it no longer owned.** A
pane's output goes to ONE viewer slot (`Terminal.viewer_output`), and viewers
overlap by design: reloading the page, restarting a pane or coming back to the
section closes one socket and opens another for the same pane in the same
breath, while the agent keeps running. `attach()` handed the slot to the new
viewer; the old socket's teardown then ran

```python
finally:
    registry.detach(term.key, pane_workspace)   # cleared the slot unconditionally
```

Whichever of the two the server finished first was a matter of milliseconds, and
with a full grid reconnecting at once some panes always lost the race. The
loser's screen was never fed again: socket open, agent alive, transcript
filling, pane frozen for the rest of the session. Nothing recovered it, because
nothing re-attached — the socket was still connected.

**Root cause B — "not yet" was answered as "not here".** A pane that connects
while the workspace is still being restored got `SessionError("No Agentic-IDE
session is running.")`, which the socket closed with **4404**. The client reads
4404 as "this terminal does not exist here" and stops for good — correctly, for
what that code means. It is exactly the state every pane reconnects into after
an app restart: the log shows eleven panes doing it within nine seconds of the
10:00 boot, all of them permanently dead while their workspace came back twelve
minutes later. On top of that, ANY pane that spent its eight attempts was dead
for the session — a laptop waking up was enough.

**Fix.**
- `jarvis/agentic_ide/session.py` — `detach(key, workspace_id, viewer=...)`
  releases the slot only if it still holds *that* viewer's callback (compared by
  equality, since a bound method is a new object per attribute access). A caller
  that means "nobody is watching this pane" — teardown, tests — passes nothing
  and clears it outright. New `SessionNotReady(SessionError)` separates "the
  workspace is not open yet" from "no such pane".
- `jarvis/ui/web/agentic_ide_routes.py` — the PTY socket passes its own
  `on_output` when detaching, and closes with **4503** on `SessionNotReady`
  instead of 4404.
- `paneSocket.ts` — 4503 is waited out on its own counter (fast while a restart
  plays out, then every 30 s), and a spent attempt budget no longer ends the
  pane: it reports itself unreachable and keeps knocking every 30 s, so a
  backend that comes back finds its terminals waiting. Only 4404 is final.
- `AgenticTerminal.tsx` — at most one line per KIND of trouble, so a pane that
  knocks every half minute does not stamp a red line into the terminal forever.

**Regression guard.** `tests/unit/agentic_ide/test_viewer_handover.py` — the
reload race in the order that used to freeze the workspace, the ordinary release
still working, an exit reaching the viewer that took over, and "not yet" vs.
"not here". `paneSocket.test.ts` — 4503 waited out, and a pane that ran out of
fast attempts still reconnecting half a minute later.

**Class rule.** A shared slot may only be cleared by whoever still holds it —
"I am leaving" is never the same statement as "nobody is here". And a client
that gives up permanently needs a reason that is permanent: everything else is a
delay, so the honest answer to it is a slower retry, not a dead pane in front of
a live process.

---

## BUG-114: a pane on a second subscription runs a stripped version of the CLI — no skills, no plugins, no user instructions (HIGH, FIXED 2026-07-27)

**Symptom (desktop field report).** "The terminals in the Agentic IDE are not
real Claude Code sessions." Opening the same folder in an ordinary terminal
showed 93 user skills, thirteen installed plugins and the user's global
instructions; a pane opened by Jarvis showed the CLI's built-in skills plus the
project's own `.claude/` and nothing else. Same machine, same binary, same
folder, no error anywhere — the pane simply knew less, and the only way to
notice was to compare it against a terminal.

**Root cause.** Multi-subscription switching (BUG-112's feature) points the
CLI's own config-dir override — `CLAUDE_CONFIG_DIR` / `CODEX_HOME` — at a
directory per account. That override does not move a credential: it moves the
CLI's **entire user level**. `skills/`, `agents/`, `commands/`, `hooks/`,
`output-styles/`, `plugins/` (marketplaces = the connectors), the user-level
`CLAUDE.md` / `AGENTS.md`, `rules/`, and `settings.json` / `config.toml` all
resolve from it. An account directory holds none of those, so every pane on an
added subscription ran a quieter, emptier build of the CLI the user installed.

`inherit_default_mode` had already patched the narrowest instance of this — a
pane opened in the CLI's fallback operating mode — one setting at a time,
without recognising that the missing setting was one symptom of a missing
directory.

The Agentic IDE is the ONLY spawn path that passes `env=` to the PTY manager,
which is exactly why the same CLI behaved correctly in every other terminal
Jarvis opens, and why the report read as "the IDE builds its own broken
terminals".

**Fix — `jarvis/agent_config_parity.py`.** Before every pane spawn, the user's
own setup is shared into a redirected directory from an explicit allowlist:

- **directories are linked** — symlink, Windows junction where symlink creation
  needs a privilege the app does not have (`WinError 1314` on a normal
  non-elevated install), copy where neither works;
- **files are mirrored by digest, then merged** — a file the account has
  written to itself is never overwritten; the keys it is MISSING are filled in
  recursively. Without that last step the fix was HALF a fix: the plugin trees
  arrived while `enabledPlugins` stayed absent, so nothing loaded;
- **`plugins/` is shared whole or not at all** — which plugins are active lives
  in state files beside the marketplaces they name;
- **identity is out of reach by construction** — `.credentials.json`,
  `auth.json`, `secrets/`, `.claude.json`, `projects/`, `sessions/` and
  `history.jsonl` are not on the list, and Claude Code's user-scope MCP servers
  are merged out of `.claude.json` one key at a time because that document
  holds the login too;
- **one re-entrant lock per account directory** — panes attach concurrently, and
  the trust entry and the MCP merge are read-modify-write cycles on the same
  file.

Second defect found on the way: `jarvis/workspace/trust.py` seeded folder trust
only into the machine's default config, so a pane on an added account met the
"do you trust this directory?" dialog — which voice and the prompt bar cannot
answer. `ensure_trusted` takes `config_dirs` now, and the registry pre-trusts
the directory each pane actually runs from.

**Regression guard.** `tests/unit/test_agent_config_parity.py` — the setup
arrives and keeps arriving; identity is never shared; an account's own value
survives while its missing keys are filled; symlink host, junction host,
copy-only host and a host that can do none of it; eight threads doing what eight
panes do; and a drift guard that fails when a CLI gains accounts without gaining
an allowlist. `tests/unit/agentic_ide/test_account_spawn.py` — the spawn
provisions and pre-trusts under the lock. `tests/unit/workspace/test_trust.py` —
per-account trust, de-duplicated.

**Class rule.** An environment variable that redirects a tool's "home" moves
everything that lives there, not the one thing you were thinking about. Before
redirecting one, enumerate what else resolves from that directory — and when the
answer is "the user's whole setup", carry it across explicitly instead of
discovering the loss one setting at a time.

## BUG-115: Jarvis pauses for a fraction of a second mid-sentence, then speaks on (MEDIUM, FIXED 2026-07-27)

**Symptom (desktop field report).** "When you talk to Jarvis there are
sometimes pauses of 0.2 or 0.5 seconds in the middle of a sentence, then he
carries on." Intermittent, never reproducible on demand, no error anywhere.

**Root cause — one buffer, three different producers of a gap.** The realtime
voice path fed a device stream opened with a 200 ms PortAudio buffer, and that
buffer was the ONLY thing between a hiccup and silence. The app already
attributed its own holes: `RealtimeSession._note_audio_flow` logs every
mid-reply gap with a cause, and the 2026-07-26/27 logs name all three —

- **this process stalled its own event loop** (5 events, 311-375 ms): "the
  audio likely sat unread in the socket, not missing from the provider". A PTY
  spawn storm from the Agentic IDE, synchronous CLI auth probes behind the
  provider panels, embedding work. While the loop is stalled NOTHING can move
  audio, so only what is already inside PortAudio keeps playing — 200 ms of it;
- **the provider delivered no audio** (3 events, 405-686 ms): gemini-live
  bursts and then goes quiet while it generates the next span. Nothing was
  banked to bridge it, because the writer is throttled to one buffer ahead;
- **the provider rendered its own pause as silent PCM** (many, 400-800 ms) —
  a genuine generation pause inside the audio, not a hole in delivery, and the
  one of the three that is the model speaking rather than Jarvis failing.

Every one of the first two exceeded 200 ms, so every one of them was audible.
The earlier de-click (2026-07-21) had smoothed the EDGES of these gaps without
touching what produced them.

**Fix — give the gap something to eat, at both levels.**

- `jarvis/audio/player.py` — `DEFAULT_OUTPUT_BUFFER_S = 0.4`: the reserved
  device buffer now outlasts the worst measured event-loop stall. This is the
  only layer that helps there, because audio banked in an asyncio queue still
  needs the stalled loop to reach the device. It costs nothing elsewhere:
  barge-in's `Pa_AbortStream` discards the buffer, so a deeper one never speaks
  past an interruption; the realtime echo guard already reads the stream's
  REPORTED latency instead of assuming a value; and a deeper buffer bounds only
  how far AHEAD the writer may run — it does not pre-fill, so the first word
  still starts on the first write.
- The feed-dry de-click window is derived from that buffer instead of being
  fixed at 80 ms. Ramping the waveform down while the device still holds 400 ms
  would carve a dip out of speech that was never going to break — with a deep
  buffer, ordinary network jitter is exactly that case.
- `jarvis/realtime/desktop.py` — a jitter buffer (`DEFAULT_PREBUFFER_MS = 180`)
  banks the opening of each turn before playback starts, so a provider that
  goes quiet mid-reply eats a reserve rather than the speaker. Bounded twice
  over — by `prebuffer_timeout_ms` and by the end-of-turn sentinel — so a short
  reply is never held back and the first word is delayed by at most the
  timeout.
- `jarvis/ui/web/{provider_routes,antigravity_routes,server}.py` — the Codex
  and Google CLI auth probes now run in a worker thread. Each one SPAWNS the
  CLI binary; on the event loop, one poll from an open provider panel was a
  few hundred milliseconds of frozen process. `list_providers` and the Claude
  probe had already been moved off the loop for the same reason; these were
  the ones left behind.

**Regression guard.** `tests/unit/audio/test_player_output_buffer.py` — the
reserved buffer outlasts the measured stall, a requested depth is clamped to an
audible range, the stall window scales with the buffer, a gap the buffer
absorbs reaches the device byte-identical, and a gap past the window is still
de-clicked. `tests/unit/realtime/test_desktop.py` — playback waits for the
reserve, order and content survive it, a short reply is released by the
sentinel, and the wait is bounded by its timeout.
`tests/unit/web/test_cli_auth_probes_off_the_event_loop.py` — a structural walk
of the route modules that fails on any `async def` reaching a blocking CLI probe
inline, which is what regresses the next time a provider CLI is added.

**Class rule.** While audio is streaming, the event loop is a real-time
deadline: anything blocking on it is heard. A buffer sized for a device's own
jitter is not sized for the process that feeds it — pick the depth from the
worst stall the process actually produces, and measure that instead of assuming
the remote end is at fault. What the third cause shows is the other half of the
rule: log an attributable cause at the boundary, or an intermittent gap stays
unfalsifiable forever.

## BUG-116: the Transcription view drops whole utterances and hides entire conversations (HIGH, FIXED 2026-07-27)

**Symptom (desktop field report).** "I don't think my last transcription is
11 minutes old, and I'm fairly sure I had more than just two turns."  <!-- i18n-allow: quoted maintainer report, translated -->
The list looked stale and sessions read short.

**What the recording actually lost.** An audit of the live store (842 sessions,
32 079 events) found three separate holes, all silent:

- **18 turns swallowed a user utterance.** A turn received two DIFFERENT final
  transcripts and only one survived. 07-17 19:21:03 "Wie viel Kilometer sind es  <!-- i18n-allow: quoted user utterance from the forensic session -->
  im Schnitt?" and 07-15 19:56:11 "Ich möchte Business Class fliegen." appear  <!-- i18n-allow: quoted user utterance from the forensic session -->
  nowhere in their transcripts.
- **18 sessions carried duplicate turn indices**, so `get_turns` (ORDER BY idx)
  returned them in arbitrary order and the view numbered the same turn twice.
- **12 finished sessions were invisible in the list** although speech was
  recorded in them — including four whose user utterance had been swallowed by
  the first defect, and eight whose only content was a voiced mission readback
  ("Done. The file is in your Outputs folder.").

**Root cause — a final transcript was bound to `VoiceTurnStarted`.**
`SessionRecorder._on_transcription_update` returned unless an OPEN turn with an
explicit lifecycle existed. Realtime does not always announce its turns: the
layer opens one silently while an out-of-band readback is speaking and
withholds `VoiceTurnStarted` (`_ensure_turn_started` publishes only when
`_external_update is None`), and the BUG-103 recovery only re-announces it
while the readback is still silent. Once the readback had audio out, the user's
next utterance arrived at a recorder whose current turn was already finalized —
and was dropped on the floor, taking the turn count with it. The bus can
produce the same gap on its own: the recorder is a wildcard subscriber, so a
dispatch past `_WILDCARD_HANDLER_TIMEOUT_S` is abandoned (seen once on
2026-07-27 15:21:32). The classic path had solved this long ago —
`_on_transcript_final` invents a turn via `_ensure_turn_open` — realtime simply
never got the same treatment. The duplicate indices were the second half of the
same split: explicit turns numbered from `event.turn_index`, invented ones from
`turn_count`, two counters that collide as soon as both kinds occur in one
session.

**Fix.**

- `jarvis/sessions/recorder.py` — a final transcript arriving with no open turn
  now opens one instead of returning (logged as a recovered turn). An open
  explicit turn still takes the whole-utterance snapshot, and the classic
  pipeline still lets `TranscriptFinal` own `user_text`, gated on a new
  `explicit_turn_lifecycle` flag so a stray STT partial can never invent a turn.
- One monotonic index allocator (`_allocate_idx`) serves both turn kinds:
  provider numbering is honoured while it leads, anything at or behind the
  high-water mark is bumped past it. A turn announced twice keeps its row.
- `jarvis/sessions/store.py` — "has content" now includes the SPOKEN track, and
  a session with no user text previews its first voiced phrase. JSON1 is probed
  once at `open()` (optional before SQLite 3.38) and the lookup degrades to
  turn text where it is missing. `schema.sql` gains
  `idx_events_session_kind`; the list query measures 1-5 ms on the real store.

**Verified on production data.** The recorded events of four real sessions were
replayed through the fixed recorder: 07-17 19:17 went from 13 turn rows (two
blank, two duplicate indices, two utterances missing) to 14 correct ones,
07-15 19:54 regained "Ich möchte Business Class fliegen.", 07-20 08:21 filled  <!-- i18n-allow: quoted user utterance from the forensic session -->
its blank last turn, and the unaffected 07-27 19:08 session came out
byte-identical.

**Regression guard.** `tests/unit/sessions/test_recorder_realtime.py` — an
unannounced final becomes its own turn with its own reply, recovered and
announced turns never share an index, and a classic-pipeline partial still
invents nothing. `tests/unit/sessions/test_empty_session_visibility.py` — a
spoken-only session stays listed and previews its phrase, while a blank spoken
event does not resurrect an empty attempt.

**Class rule.** A recorder must never make the RECORD conditional on a
lifecycle event it does not control. Every producer of turn boundaries
eventually skips one — deliberately (a silent readback turn), or because the
bus abandoned the subscriber — and the honest fallback is to open a turn from
the evidence in hand, not to discard the evidence. The corollary bit the list
query too: "did anything happen here?" must be asked of every track the detail
view renders, or a conversation the user definitely heard becomes unreachable.

## BUG-117: gemini-3.6-flash rejects the forced thinking_budget=0 with a parameterless 400 — every delegated realtime fallback turn bricked (HIGH, FIXED 2026-07-21)

**Symptom (live 2026-07-21 18:27–18:33, sessions `62965e5d`, `dcd9d16a`,
`43ca9ad6`).** In a realtime call, whenever the provider (Gemini Live) went
silent after the user finished speaking, the deterministic-delegate fallback
correctly detected the missing input boundary ("provider input boundary
missing after 1.50s of stable local transcript") and dispatched the Brain
chain — but EVERY gemini attempt died with `400 INVALID_ARGUMENT.
"Request contains an invalid argument."`, OpenAI was quota-dead (429), so
the user heard the canned outage phrase (or a stale/garbled provider line)
instead of an answer. Perceived as: "Jarvis keeps listening after I finish
my sentence and never starts thinking."

**Root cause (reproduced 1:1 against the live API).** The router/tool tier
hard-caps `thinking_budget = 0` (BUG-LATENCY mandate,
`jarvis/brain/manager.py`). `gemini-3.5-flash` accepts that;
`gemini-3.6-flash` — the live install's configured `tool_model` — rejects
`thinking_config(thinking_budget=0)` with the PARAMETERLESS 400 above: no
field name, no "thinking" token. The existing capability recovery
(`_is_thinking_config_rejected_error`) requires "thinking" in the message,
so it never matched, no retry happened, and the tier failed terminally on
every call. Repro matrix: `plain`/`tools`/`system` all OK on 3.6;
`thinking0` and `tools+thinking0` 400 on 3.6; all six OK on 3.5.

**Fix (1299c063 + review follow-up).** In
`jarvis/plugins/brain/gemini.py`: a parameterless 400 INVALID_ARGUMENT with
`thinking_config` on the wire retries once without the field (capability
probe, AP-21 — never a model-name pin). Blame is only remembered (per
instance, per REJECTED BUDGET VALUE — brain instances are cached per
(provider, model) and shared across tiers) once the retried request is
accepted; an unrelated 400 propagates unchanged and disables nothing. If a
`cached_content` reference was also on the wire, the retry clears and
invalidates it too (a differently-worded dead-cache 400 must not survive
the `attempt == 1` gate of the BUG-019 recovery) and then blames nothing —
with two variables changed the accepted retry proves nothing.

**Guards:** `tests/unit/brain/test_gemini_reasoning_effort.py` (
`test_parameterless_400_recovers_and_is_remembered`,
`test_unrelated_400_still_propagates_and_forgets_nothing`,
`test_generic_400_with_live_cache_clears_cache_and_blames_nothing`).

Related: BUG-019 (stale-cache recovery in the same except block), AP-21
(capability probes), AP-22 (the single-provider brick this produced live:
gemini 400 + openai 429 left zero reachable tool-tier families).

---

## BUG-118: black rectangle around the JarvisBar on Windows — a stalled paint loop gets an unlayered ghost window (MEDIUM, FIXED 2026-07-28)

**Symptom (maintainer field report).** An opaque black box around the bar's
pill, surviving a restart. Read as a rendering regression of the macOS defect
BUG-093, but on Windows, where the colour-key path was believed proven.

**Root cause.** The bar's magenta colour key is a property of the WINDOW, not
of the pixels: `-transparentcolor` sets `LWA_COLORKEY` on the layered window
Jarvis owns. When a top-level window stops pumping messages for ~5 s, Windows
hides it and substitutes a stand-in window of class `Ghost`, painted by the DWM
at the identical rectangle. That stand-in carries **no `WS_EX_LAYERED`**, so
the key is never applied to it and the whole window rect lands on screen as
opaque black. Measured with both windows alive at once: `TkTopLevel` (app pid,
`LAYERED=True`, `key=0x00ff00ff`) and `Ghost` (dwm pid, `LAYERED=False`),
`IsHungAppWindow=True` on both, `black=3141` on screen over the rect.

Reaching this needs no crash. The bar paints from an in-process Tk loop, so it
stops pumping whenever ANOTHER thread holds the GIL through a long CPU-bound
stretch. The live trigger was the backend thread sitting `active+gil` in
`load_config()` → `JarvisConfig(**data)`, reached from
`resolve_provider_endpoint()` in `claude_api._ensure_client` on every brain
call — the pydantic half of the freeze whose TOML half `325af16d` cached. So a
STALL presented itself as a RENDERING defect, which is why it was first
mis-triaged (as a screen-capture artifact, on the strength of an isolated probe
whose window was never stalled).

**Fix.** `disable_windows_app_ghosting()` in `jarvis/core/process_utils.py`,
called from both colour-key surfaces: the bar (`jarvis/ui/jarvisbar/overlay.py`)
and the mascot (`ui/orb/overlay.py`). `DisableProcessWindowsGhosting` is
process-wide and irreversible, which is the right trade — a frameless
click-through overlay gains nothing from a ghost (no title bar to grey out, no
close button to offer), while the app keeps its own Restart control, the tray
icon, and Task Manager as ways out of a hang. Windows-only; a no-op returning
False on macOS/Linux, which substitute nothing.

**Proof.** A color-keyed frameless Tk window showing a real renderer frame,
with its UI thread deliberately blocked past the threshold, measured from a
second thread: control `before black=0 / during black=1982` of 2870; with the
fix `before black=0 / during black=0`.

**Guards.** `tests/unit/core/test_process_utils_ghosting.py` — no-op off
Windows without touching ctypes, disables on Windows, idempotent second call
never re-enters user32, a failing API degrades to False instead of taking the
overlay boot down, and the helper stays exported.

**Still open (separate defect).** The stall itself. Ghosting-off stops it
LOOKING like a rendering bug; the bar still freezes while the GIL is held. The
in-process bar is Windows/Linux-only coupling — macOS already runs it in a
companion process (BUG-093), which is immune by construction.

**Class rule.** A compositor-level transparency trick protects only the window
you own, and only while that window is the one on screen. Before concluding
that a transparency path works, check it in the state the report describes —
an unstalled probe cannot falsify a stall-triggered defect. When a UI defect's
rectangle matches a window exactly and its colour is one the renderer never
emits, suspect a substituted window before suspecting the renderer.

---

## BUG-119: a unit run leaves a SECOND, permanently visible JarvisBar on the developer's desktop (MEDIUM, FIXED 2026-07-28)

**Symptom (maintainer field report).** Two bars on screen at once, slightly
offset and of different sizes. The second one belonged to no Jarvis instance:
`python -m jarvis.ui.jarvisbar.host`, parented to
`pytest tests/unit -q --timeout=180`, still visible long after that test had
moved on. Different geometry from the app's own bar (72x30 vs 93x39) because it
booted from a different scale — the tell that it was a foreign process, not a
duplicated window.

**Root cause.** `SubprocessBarOverlay` schedules its bounded auto-respawn as
`threading.Thread(target=self._respawn_after_backoff, ...)`. A **bound method
keeps the surface alive**, so the thread holds the whole overlay across its
5 s backoff. In a test the fake host's scripted stdout EOFs immediately, the
pump reads "host is gone", and a respawn is scheduled — but by the time the
sleep ends, `monkeypatch` has reverted and `subprocess.Popen` is REAL again.
The respawn then starts a genuine host process, which on Windows builds a real
Tk bar (`QT_QPA_PLATFORM=offscreen` binds Qt only, and the Qt host test is
Darwin-gated anyway) and shows it on the developer's desktop, where nothing
ever reaps it.

`test_subprocess_overlay.py` already carried an autouse fixture marking every
live proxy `_stopping` for exactly this reason — but it is per-file, so any
other module that constructs one is unprotected, and it only papers over a
lifetime bug in the production path.

**Fix.** The backoff is served by a module-level
`_respawn_after_backoff_weakly(weakref.ref(surface), attempt, backoff)`, which
sleeps holding only a **weak** reference and abandons the attempt when the
overlay has been released. "Nobody holds this overlay" now means "there is no
bar to restore, so do not spawn a host". The live path is unchanged: the
desktop app holds its surface, so real respawns still fire.

**Guards.** `tests/unit/ui/jarvisbar/test_subprocess_overlay.py` — a released
surface abandons its pending respawn, a live one still respawns, and (the real
regression) a proxy with a respawn pending is still garbage-collectable once
dropped. Verified sharp: with the previous bound-method target that last
assertion fails.

**Class rule.** A thread that sleeps on behalf of an object must not own it.
Any delayed retry/backoff/watchdog that targets a bound method silently extends
its subject's lifetime past the point where anything wants it — and when the
retry's side effect is spawning a process or a window, the leak becomes
user-visible on a machine nobody is testing on. Hold the subject weakly and
treat collection as cancellation.

## BUG-120: a spoken order to brief two coding agents changes a SETTING instead — no prompt, no thought, and a confident report that both are working (HIGH, FIXED 2026-07-29)

**Symptom (maintainer field report, voice session 2026-07-28 20:34).** Coding
mode on, six panes open. The user asked, in one long dictated paragraph, for
two named panes to be given split work. Jarvis answered "Alles klar, ich hab <!-- i18n-allow: verbatim quote of the runtime output under investigation -->
T1 und T5 die Aufgaben gegeben …" ("sure, I gave T1 and T5 their tasks") and <!-- i18n-allow: verbatim quote of the runtime output under investigation -->
named what each would do. Nothing had been written to either pane. No agent was briefed, and — the part that made
this feel different from a delivery failure — nothing had even tried: no
composer call, no fan-out, no brain turn.

**Root cause (measured from the flight recorder, not inferred).** The turn was
routed correctly (`path=orchestrator`, `reasons=…,workspace`) and the
addressed-terminal detector was innocent: replayed against the verbatim
transcript, `agentic_ide.intent.detect_all` returns T1 **and** T5,
`kind='prompt'`. What killed it sits earlier. `BrainManager.generate` runs the
deterministic self-configuration gates BEFORE the Agentic-IDE delivery path,
and `voice_command_gate._match_language_switch` searched the WHOLE utterance
for each ingredient of a language command *independently, with no proximity
requirement*:

| ingredient | matched | offset | what the user actually meant |
|---|---|---|---|
| language alias | the word for "automatically" | 458 | describing a bug — text is NOT inserted automatically |
| `_LANG_OUTPUT_VERB` | the verb in "ask questions" | 866 | the panes should ASK if unsure |
| `_LANG_PREP` | "in" | 398 | "in the text field" |

Three unrelated clauses, up to 468 characters apart, assembled into "switch the
reply language to auto". The gate applied it, persisted it to `jarvis.toml` and
returned — 283 lines above `_run_agentic_ide_fast_path`. Timing confirms it end
to end: `realtime_delegate_completed … success=True` after **519 ms**, where a
real delivery needs 10-21 s for the prompt composer alone. A turn reported as
successful that had done nothing but change a setting nobody asked about.

The spoken confirmation was then handed to the live model, which re-renders
injected text in its own words — so instead of "language set to automatic" the
user heard a detailed account of a fleet briefing that never happened.

**Fix (two layers, because either alone leaves the class open).**

1. `_match_language_switch` binds its ingredients to a **window** around the
   language word (`_command_window`: 60 characters, and never across a sentence
   boundary). Scattered co-occurrence can no longer form a command. The
   earliest QUALIFYING language still wins, so the 2026-06-27 first-language
   rule is untouched; a language word with no command around it is skipped
   instead of deciding the turn. `_language_words` also switched to `finditer`,
   so a language named twice is judged at each place it appears.
2. A turn that NAMES A RUNNING PANE outranks the self-configuration gates
   altogether (`ide_owns_turn` in `generate`), the same precedence the desktop
   gate already honours further down. Briefing an agent and changing a setting
   are not things a user can mean at the same time. Deliberately NOT applied to
   the cancel intercept: stopping work is a safety control and must keep
   working under every phrasing.

**Guards.** `tests/unit/brain/test_config_gate_vs_agentic_ide_routing.py` — the
trimmed live transcript no longer reads as a language switch, both addressed
panes are briefed end to end through `generate`, and a plain "switch to
English" still works with six panes open. The precedence is proven
independently of fix 1 by FORCING the detector to claim a language switch and
asserting it is still overruled. `tests/unit/brain/test_voice_command_gate.py`
adds the clause-proximity case. Verified sharp both ways: the trimmed
transcript resolves to `auto` under the pre-fix matcher, and the forced-match
test fails with the precedence removed.

**Class rule.** A deterministic gate that assembles an intent from ingredients
found ANYWHERE in the utterance will eventually assemble one out of a long
dictated paragraph — the longer users talk, the likelier it is, so this gets
worse exactly as voice input gets more natural. Bind every ingredient to a
window around the anchor word. And when two deterministic gates can both claim
a turn, the one holding the MORE SPECIFIC evidence must win: a named running
agent beats a keyword match, however plausible the keyword looked.

---

## BUG-121: only Claude Code panes come back with their conversation — every other coding CLI resumes empty (HIGH, FIXED 2026-07-29)

**Symptom (maintainer field report).** Resuming a workspace restores the Claude
Code panes with their history intact, while the Codex, OpenCode and Kimi Code
panes come back as fresh agents that know nothing. The panes themselves are
there — right call-sign, right grid position — so the loss is invisible until
somebody asks the agent a follow-up question and gets a blank stare. Nothing in
the log said a word about it.

**Root cause — the handle was never captured, not lost.** A pane resumes through
a *resume handle*, and the two ways of getting one are not equally reliable:

* Claude Code is TOLD its id at launch (`--session-id <uuid>`), so the handle
  exists before the CLI has done anything at all.
* Codex, OpenCode and Kimi Code choose their own, so the id can only be
  DISCOVERED from what they write to disk — and they write nothing until the
  conversation has its first message.

`Registry._schedule_lookup` searched 4 s and 16 s after the pane's process
started, was called from exactly one place (the spawn branch of
`_attach_locked`), and gave up permanently after the second attempt. A pane
opened and prompted a minute later — the normal way a grid is used — therefore
missed both attempts and kept `resume = None` for the rest of its life.
`Terminal.to_snapshot` then stored a pane with no conversation, and on restore
`resume_argv` returned `None`, which is the same code path as "this pane never
had a conversation": a silent fresh start.

**Evidence.** A Codex pane launched 15:17:44 and briefed 15:19:32 wrote its
rollout file at 15:19:32 — 106 s after launch, 90 s after the last lookup — while
both the filename and the record inside it carry the SESSION START time
(15:17:46), which is why the file looks like it was there in time. Across 338
real Codex TUI sessions on the maintainer's machine, 40 % of the files appeared
after the 16 s window (p90: 402 s); `codex exec` runs, which carry their prompt
at launch, landed inside it 98 % of the time. The stored snapshot showed it
plainly: one Codex pane with `prompts_sent: 1` and `resume: null` beside nine
Claude panes that all had handles, including ones never prompted.

**Fix.** The search is now triggered by the event that MAKES a session findable
rather than by the pane's age: `Registry._lookup_after_conversation` runs a
short second schedule (`CONVERSATION_DELAYS_S`) when a prompt is delivered
(`send_prompt`) and when the user submits a line in the pane themselves
(`write`) — so a pane driven only by hand is covered too. `started_at` stays the
pane's LAUNCH time, because a session's recorded timestamp is when it opened.
One round per pane at a time plus a cooldown (`LOOKUP_COOLDOWN_S`) keeps a
leaning-on-Enter user from re-crawling the CLI's history. A restored pane that
was worked in and still has no id now says so in the log instead of starting
over in silence.

**Guards.** `tests/unit/agentic_ide/test_resume_session.py` — a CLI that files
its session only once the conversation has a message is still captured after a
prompt and after a hand-typed submit; the cooldown holds back repeated Enters;
a pane that already has a handle never looks again.

**Class rule.** When a capability depends on an external tool writing something,
time the wait from the EVENT that makes the tool write, never from the moment we
happened to start it — and never let the last attempt be silent. Two of the four
CLIs here were pinned by tests that pre-created the session file and set the
delay to zero, which proved the mechanism and hid the timing entirely.

---

## BUG-122: "Continue interrupted" always reports zero — a restored pane redrawing its own screen is mistaken for an agent already at work (HIGH, FIXED 2026-07-31)

**Symptom (maintainer field report).** After restarting the app and resuming the
workspace, the "Continue interrupted" dialog says *"Nothing was interrupted —
every pane has been given something to do since it started"* and offers
**Continue all (0)**, with a grid of panes visibly sitting at their prompts
holding half-finished jobs behind it. Never once did the control list a pane.
The restore point on disk disagreed: `last_session.json` carried
`continuation_needed: true` for several panes at the very moment the dialog
claimed none.

**Root cause — start-up drawing reads as work.** The evidence chain is:
`ActivityWatcher` observes a pane working, the resume snapshot records
`continuation_needed`, restoring raises `Terminal.continuation_pending`, and the
scan lists every pane still carrying it. That chain was sound up to the restore.
What broke it was the FIRST sweep after the resumed pane's agent came up.

`activity.read_activity` deliberately reads nothing but screen MOVEMENT — the
one property that is not a claim about any particular coding CLI (see the module
docstring, and BUG-037's class of transcript-content traps). A restored CLI
repaints its banner, its model line and its whole old transcript over several
seconds before it settles at its prompt, and that burst is frame-for-frame
identical to an agent at work. Both consumers of the reading then drew the same
wrong conclusion in the same window:

* `notifications._checkpoint_resume_state` cleared `continuation_pending` on
  sight of `working` ("a resumed CLI that is already working carried on by
  itself"). Sweeps run every 2 s, so this landed within two sweeps of every
  resumed pane coming back — before any human could open the dialog.
* `interrupted.scan` filtered the pane out for the same reason, using the
  weaker byte-based fallback (`last_output_at`), which a repainting pane
  refreshes continuously.

Both are unconditional, so the offer could not survive a restart, and the count
behind the button was structurally zero for every user, on every platform.

**Fix — movement only counts once the pane has been SEEN standing still.**
`Terminal.idle_seen` records whether this pane's screen has been observed still
since its CURRENT process started; it is cleared on every spawn (beside
`last_output_at`) and raised by the notification sweep on a settled reading from
a real two-look comparison, never from the first-sight branch that has to ASSUME
stillness. A start-up burst always ends at a prompt, so "has stood still once"
separates painting from working without knowing anything about what any CLI
draws. The checkpoint now retracts the offer only for movement after that point,
and the scan applies its "already working" filter only then too. The question
check moved off `read_activity` onto its own reading (`activity.shows_question`),
because a pane can be repainting AND showing a trust prompt — and that is
precisely a pane not to type into; `read_activity` answers `working` first and
would have hidden the question.

**Guards.** `tests/unit/agentic_ide/test_pane_notifications.py` — a restored
pane painting itself keeps `continuation_pending`, and movement after it settled
still retracts it. `tests/unit/agentic_ide/test_interrupted.py` — a resumed pane
whose screen is moving is still offered while it has never been idle, and is
correctly withheld once it has.

**Class rule.** A detector built on one property must not let a SECOND consumer
apply it to a different question without asking whether that question has the
same blind spot. "Is the screen moving?" answers "is this agent busy?" only for
a process that has already settled once; for a process that just started it
answers "is this CLI drawing itself?", and every caller that conflates the two
fails silently in exactly the seconds after a restart — which is when the
feature is used.

---

## BUG-123: the wake word needs three tries — the SHAPE gate carries its own spelling assumptions (HIGH, FIXED 2026-08-04)

**Symptom (maintainer field report).** "I had to say it very often." Simple
names are no better than invented ones: "even with an easy name like Ben it
often fails." The status line meanwhile showed the raw i18n key
`VOICE_STATE.CONNECTING` instead of a label (separate defect, same screenshot —
the built `dist/` predated the locale key by 33 minutes).

**Evidence — the live log said "heard it, threw it away".** On this install
(2026-08-04 17:01-17:02, phrase "Hey Claude", engine `vosk_kws`, en model):

```
candidates=2  suppressed_confirm=2  fired=0     <- two calls heard and rejected
17:02:05 vosk-kws: verify OK (shape) ... free ear heard 'hey khloe' ... fired=1
```

Three calls, one fire. And not one line said WHY the first two died: the
"neither spelled nor shaped like a call" branch of `_verify_window` was the only
rejection that logged at DEBUG, so the most common suppression in production was
also the one invisible in production.

**Root cause — AP-27, one level below where it was last fixed.** The free-decode
SHAPE gate (`candidate_shape_ok`) exists precisely because a spelling rule cannot
judge an out-of-vocabulary wake word. Two of its four questions turned out to
rest on spelling assumptions of their own. Replaying each phrase through the real
en model with a German and a US voice:

| phrase | spoken (German voice) | free ear heard | verdict |
|---|---|---|---|
| Hey Ben | Hey Ben | `have you been` (3 tokens, conf 1.00) | suppressed |
| Hey Alex | Hey Alex | `have you been` (3 tokens, conf 1.00) | suppressed |
| Hey Atlas | Hey Atlas | `have you at last` (4 tokens, conf 1.00) | suppressed |

* **The token count** assumes the free ear spends ONE token on the wake prefix.
  A short function word in a foreign accent is re-tokenised — `hey` -> `have
  you` — so a genuine two-token call arrives as three or four and is charged
  against the phrase's own budget. Every non-native speaker of the model's
  language hits this, which for an international product is the normal case.
* **The confidence veto** assumes the wake word is out-of-vocabulary, and reads
  the decoder's certainty as proof that some OTHER word was said. Every name
  that IS an ordinary word inverts it: "Ben" -> `been`, "Claude" -> `cloud`,
  both at conf 1.00. That is exactly the maintainer's "even easy names fail" —
  easy names are easy because they are real words.

Neither premise can be repaired by moving a threshold, and a spelling floor low
enough to rescue `hey khloe` (0.55 against "claude") is far below what unrelated
speech scores. Same shape as BUG-037: the rule that suppresses ghosts also
rejects genuine wakes.

**Fix — the two contested questions stop being vetoes.** They now yield
`SHAPE_UNDECIDED` and hand the candidate to the ACOUSTIC COMPETITION that
already gates every shape acceptance (re-score against an explicit
`"<prefix> [unk]"` alternative — a purely acoustic judgement that never reads a
spelling, AP-27-safe). The two duration-based questions — "not spoken longer
than the phrase could be" and "a name-sized body exists where the name belongs"
— stay HARD rejections: they measure how much sound was made, never what it was,
so no accent or vocabulary can invert them. No calibrated threshold moved.

The authoritative confirm now also REPORTS this rejection class through the
rate-limited `_log_suppression`, so "heard and thrown away" is distinguishable
from "never heard" without a debug build.

**Measured, real en model, synthesised German + US voice** (`_verify_window`
end to end, 16 genuine calls / 30 unrelated utterances incl. the recorded live
false-wake transcripts):

| | genuine calls | ordinary speech | near-homophone NAMES |
|---|---|---|---|
| before | 12/16 fire | 0/30 false | 7/22 accepted |
| after | **16/16 fire** | **0/30 false** | 12/22 accepted |

The cost is confined to near-homophone names ("Hey Bell" firing "Hey Ben") — a
class that already leaked and that no offline small model can separate by a
single final consonant. Ordinary speech and bare interjections are unchanged.
This also closed the previously-open one-breath case (BUG-037 follow-up, strict
xfail since 2026-07-25): "Hey Claude, what is the weather today" now fires,
because trailing command words make the candidate contested instead of vetoed.

**Honest limit.** The 2026-07 calibration corpora (250/1650 real captured
windows) are no longer on disk; the numbers above come from a smaller
synthesised corpus. The change deliberately moves no threshold, so the
calibrated bounds remain exactly as measured then.

**Guards.** `tests/unit/plugins/wake/test_vosk_wake_word_agnostic.py`
(contested-vs-hard verdicts, the German prefix split, a contested shape that
LOSES the competition), `tests/unit/plugins/wake/test_vosk_kws_provider.py`
(the fake's competitor grammar now models the measured decoder instead of
answering "phrase" unconditionally).

---

## BUG-124: the self-dialogue guard counted the echo of its OWN cut — a rebuild storm on a call nobody was talking in (HIGH, FIXED 2026-08-06)

**Symptom (maintainer screenshot + `data/jarvis_desktop.log`).** A toast on the
API-Keys screen: the realtime connection is being rebuilt because a response
without locally grounded microphone speech was detected. It fired twice in six
seconds. The call before it had already gone silently dead and forced an app
restart.

**The run.** Two calls on `codex-subscription-realtime` (ChatGPT-Live v3).

Call 1, session `51563c06`:

| time | event |
|---|---|
| 17:39:55.584 | local transcript `'Was geht ab?'` — a real, grounded turn |
| 17:39:57.515 | local transcript `'Thank you for watching!'` — Whisper silence boilerplate, accepted as a turn; the call language flips `de → en` |
| 17:40:00.343 | the far end opens a response 15 ms after the last one → **refused**, `turn/interrupt` sent |
| 17:40:00.444 | server user caption `'Hi! Schön'` — literally the first words of the answer just cut | <!-- i18n-allow: quoted German live caption, the evidence itself -->
| 17:40:00 – 17:40:12 | 8 more refusals, roughly one every 2 s |
| after 17:40:12 | nothing. No rebuild, no error, no message — a mute call, recovered only by restarting the app |

Call 2, session `e814716a`: same opening, then 7 refusals in 3.2 s, ungrounded
captions `'a.m.'`, `'a young woman a blurred gradient a young woman …'` →
**rebuild 1/3** at 17:41:53, the same loop back at 17:41:56, `'this image is'`,
**rebuild 2/3** at 17:41:59. A third would have hit
`_TRANSPORT_REBUILD_MAX_PER_WINDOW` and ended the call `reason=error`. The user
hung up at 17:42:07; the dictation handover that hangup was FOR then failed.

**Root cause.** Refusing an ungrounded response is a LOCAL interrupt — it calls
`_interrupt_active_codex_turn()` — and ChatGPT-Live answers a cut turn with a
caption of its own truncated speech. `_POST_INTERRUPT_GRACE_S` existed for
exactly that fallout, but it was armed only inside the `_interrupt_barrier_moved()`
branch, and only the external `interrupt()` moves that barrier. So the refusal
path produced the very evidence it then counted: two echo captions and the
transport went. The 2026-08-04 fix for the same shape covered barge-in and
delegation and missed the third source.

Four further defects sat in the same 3 minutes:

1. **The rebuild was never checked for effect.** Same cause, same loop,
   seconds later — nothing noticed.
2. **A refusal storm was invisible.** Call 1 never reached the caption
   threshold, so 13 s of refusals produced `log.warning` lines and nothing the
   user could see (AP-30).
3. **Our reaction fed the loop.** Every refusal cut a turn; a cut turn makes
   ChatGPT-Live start another one. The refused audio is dropped before it can
   reach the user either way, so the interrupt bought nothing.
4. **The local recognizer manufactured a turn.** `'Thank you for watching!'`
   over a near-silent microphone grounded a response nobody asked for and
   flipped the output language on its way through.

**Fix.**

* Arm `interrupt_grace_until` on the refusal path too
  (`jarvis/plugins/realtime/codex_subscription.py`).
* Detect the loop on consecutive REFUSALS
  (`_REFUSALS_BEFORE_REBUILD` within `_REFUSAL_STORM_WINDOW_S`) — a signal the
  far end's reaction cannot manufacture — and nudge the receive queue so the
  verdict is reported even when the far end has gone quiet.
* Interrupt only the FIRST refusal of a run; drop the rest silently.
* `_ADVISED_REBUILD_RELAPSE_S`: the same advised-reconnect cause returning
  within 15 s of a rebuild ends the call honestly instead of spending the
  budget (`jarvis/realtime/session.py`). No cross-provider failover — a
  subscription-backed provider must never fall through to metered voice.
* Move the dictation lane's short-recording + whole-utterance silence-boilerplate
  verdict down to `jarvis/speech/wake_constants.py::is_silence_hallucination`
  and apply it to the realtime input recognizer as well (one definition,
  BUG-008 rule). A hit becomes a FAILED transcript, so the far end's own
  caption for the same audio can still stand in.
* `_PROVIDER_CLOSE_BOUND_S = 1.5` (was 5.0, exactly the dictation handover's
  own bound, which is why the key press that caused the hangup was refused).

**Guards.** `tests/unit/realtime/test_codex_subscription.py`
(`test_a_refusals_own_echo_captions_never_tear_the_transport_down`,
`test_a_run_of_refusals_is_what_proves_the_self_dialogue_loop` — the first one
reproduces the live failure exactly when the grace is removed),
`tests/unit/realtime/test_session.py`
(`test_the_same_advice_right_after_a_rebuild_ends_the_call_honestly`,
`test_the_socket_courtesy_never_outlasts_what_waits_for_the_microphone` — the
close bound is pinned as a RELATIONSHIP, because the bug was the two numbers
being equal), `tests/unit/realtime/test_input_transcription.py`
(boilerplate discarded, a real sentence that merely opens like it survives),
`tests/unit/plugins/brain/test_codex_brain.py` (the Node PATH repair below).

**Unrelated defect found in the same log, fixed alongside.** The Codex CLI
brain died with `rc=1` and "node is not recognized": the app PATH carried the
npm global directory but not the Node.js install directory, and the failure
surfaced upstairs as "returned no answer". The child now gets the Node
directory on its PATH, resolved the same way the mission workers already do.

---

## BUG-125: the ChatGPT-Live campaign — self-talk, one-turn death, choppy audio and slow spawn shared five roots (HIGH, FIXED 2026-08-07)

**Symptoms (sessions.db, Aug 4–6, 8 codex-subscription sessions).** 50 % died
after one turn; 6/8 recorded both-sides role-play inside one assistant
response; 6/8 recorded garbage user fragments ("illst.", "Thank you for
watching!"); audio was chopped and spliced; spawn took 5–12 s with a frozen
orb. The 5 openai-realtime control sessions in the same window showed none of
it — the disease was codex-path-specific.

**Five confirmed roots, one campaign (branch `rt-campaign`, ~18 commits):**

1. **An on-loop pywebview probe starved the media path.** The
   ShowWindowRequested subscriber ran `evaluate_js` (bounded ~20 s) ON the
   asyncio loop; two 15 s stalls put the WebRTC mic sender 40 s behind wall
   clock and the provider reset the socket. Fix: the whole show path
   thread-hops (`asyncio.to_thread`), `/api/window/focus` too; the realtime
   loop-lag probe WARNs at 500 ms scheduling lag.
2. **Rules faded; the opening monologue consumed the user's entitlement.**
   The one-speaker rule was delivered once and line-diffed away while the
   per-turn language pin held — repetition is what makes a rule real on this
   channel. `update_session` grew `standing_directive` (re-asserted every
   delivery). The server opens a response the moment it hears a VOICE;
   the call's pre-first-final response is now a bounded GREETING (6 s, 2 s
   while the user is mid-utterance), adopted as the answer if the final
   lands while it speaks, closed without spending otherwise.
3. **No terminal item exists — proven, not guessed.** The notification-dump
   probe recorded the complete v3 vocabulary on a real answered call:
   `thread/realtime/{started,sdp,transcript/delta,transcript/done}`. Nothing
   else. The 1.2 s quiescence backstop IS the turn boundary, so its
   consequences became free: playback resumes prebuffer-free within a 4 s
   grace (every >1.2 s generation pause used to cost an audible 180 ms
   seam), silent-close splices are sequenced behind a local boundary, the
   half-duplex mute releases after ~2 s of provider silence (was 6+), and
   `realtime_stop → realtime_start` on the SAME thread is confirmed to work.
4. **The transcript layer lied.** Non-final partials persisted as user_text,
   re-finals concatenated the utterance into itself, partials flipped the
   call language mid-caption, and a misheard 328 ms fragment set the call
   language for good. user_text is finals-only (explicit preview promotion
   with a log line), parts are item-keyed REPLACE, language resolves on
   finals with a 500 ms voiced-duration floor before the conversation is
   established (duration, never spelling — AP-27 class), and unpinned codex
   opens get a soft language hint on the working channel.
5. **Storms were punished blindly.** A refusal storm on a line NOBODY spoke
   into now stays a quiet refusal (the gate winning) instead of a rebuild
   that could relapse-kill an idle call; a heard-but-discarded 200 ms sound
   keeps a 4 s answer window instead of getting its real answer cut; a dead
   transport's hanging close is bounded in the rebuild too, and the pump
   survives waking mid-swap.

**The missing evidence layer shipped with the fixes:** every session now ends
with a content-free `RealtimeSessionPostmortem` counter event (flight
recorder), RT-SPAWN spans time every bring-up step, and
`scripts/codex_live_probe.py` drives REAL headless ChatGPT-Live calls from
committed speech fixtures (`--dump` for contract discovery; verdicts via
`scripts/diag_voice_sessions.py --harness`, shared with
`jarvis/diagnostics/realtime_forensics.py`). Guards:
`tests/integration/realtime/test_codex_multi_turn.py` (14 session-level
guards), `tests/unit/realtime/test_codex_subscription.py` (84 adapter
guards), `test_session_postmortem.py`, `test_realtime_probe_eval.py`,
`test_realtime_forensics.py`. Probe caveat: the local recognizer rides the
configured cloud STT; an exhausted quota mutes the harness's grounding
half — the transport metrics stay valid.

---

## BUG-126: Computer-Use went blind on a busy server, then crawled — and killed every global hotkey on its way out (HIGH, FIXED 2026-08-10)

**Symptoms (one voice session, 18:39–18:43, goal "open Elon's post").** The
first mission announced *"I can't see the screen right now"* although a funded
vision key was configured. The second and third missions did work, but every
single step took ~9 s, so the user hung up before anything finished. Nothing in
the UI said why, and the Escape cancel key stopped responding afterwards.

**Three independent roots in the same run:**

1. **A capacity blip was treated as a broken credential.** Gemini answered
   `503 … This model is currently experiencing high demand … usually
   temporary`. `_classify_provider_error` has no bucket for infra failures, so
   it fell to the `call_fail` default and `ComputerUsePlannerSelector`
   mission-blocked the candidate. The chain then walked into OpenRouter (402,
   no credits) and OpenAI (429, no credits) and raised
   `CUNoVisionProviderError` — the honest "no eyes" readback, for a provider
   that served a full mission three minutes later. Fix:
   `is_transient_provider_error` (capacity/infra codes and wordings, with
   credential/quota/rate-limit failures explicitly excluded), ONE in-place
   retry after 0.6 s, and a per-candidate transient budget so a first blip is
   forgiven while a real outage still falls out of the chain.
2. **The token headroom was re-discovered on every step.** `_DECIDE_MAX_TOKENS`
   is 320; the vision model cut its JSON at the cap on EVERY decision, so the
   existing one-shot headroom retry fired every time and the first reply was
   always thrown away — ~3 s of each ~9 s step. Truncation is a property of the
   MODEL, not of one call, so `brain_call` now remembers the (provider, model)
   pair in `_NEEDS_HEADROOM` and starts subsequent calls at the headroom. Safe
   only because the early-stop aggregator cuts the stream at the JSON boundary,
   so the raised ceiling costs nothing on a short reply.
3. **Arming the cancel key killed the hotkey poller.** `HotkeyChecker.run`
   binds `id_list = self.hotkeys.keys()` once and iterates that LIVE dict view
   every 20 ms. Arming `cu_cancel=[escape]` from the mission thread resized the
   dict mid-iteration → `RuntimeError: dictionary changed size during
   iteration` inside the poller thread, which died silently and took the Escape
   kill-switch plus the call/dictation shortcuts with it for the rest of the
   process. The library exposes no lock, so `_registry_mutation` pauses the
   shared poller, waits out one loop period, writes, and restarts it.

**Guards:** `tests/unit/cu/test_brain_call_resilience.py` (transient retry,
account failures never retried, per-mission transient budget, learned
headroom), `tests/unit/trigger/test_hotkey_registry_mutation_guard.py` (the
`FakeGlobalHotkeys.strict_poller` knob reproduces the live thread death — the
guard test fails without `_registry_mutation`).

## BUG-127: three false wakes in 94 seconds — flowing dictation walked through every verify gate (HIGH, FIXED 2026-08-10)

**Symptoms (2026-08-10 19:57–19:59).** While the user dictated German to a
coding agent, the Vosk KWS engine fired 'Hey George' three times in 94 s;
each phantom session had to be killed by hotkey. The log shows all three
verify paths leaking in one window: `verify OK (undecided)` with the free
ear hearing unrelated German at the span (twice, de model, competition kept
the phrase at conf 1.0), and `verify OK (spelled)` where the EN model
spelled a literal 'hey george' out of German flow.

**Root.** The verify asked every question about what was said AT the
candidate span, and none about what was said immediately BEFORE it. The
grammar stage happily forces flowing speech onto the phrase; mid-stream, the
acoustic competition measurably does not discriminate (the forced grammar
keeps the phrase at conf 1.0 inside continuous speech), and the spelling
path is accept-only by design — so once dictation supplies enough sound
material, some window eventually satisfies one of the routes. No per-path
tightening can close a class that leaks through ALL paths at once.

**Fix.** A fourth HARD check in `_verify_window` (leading isolation): a wake
call STANDS ALONE. If the free decode carries more than 0.25 s of voiced
lead-in inside the 0.6 s window before the span, the candidate is rejected
ahead of BOTH confirm paths. Word-agnostic and durational (AP-27-safe: word
timings only, never text); mid-speech no-fire is the accepted product
trade-off, and only the LEADING side is gated so the wake+command breath
(2026-07-25) stays intact. Bounds are `__init__`-threaded like every sibling
threshold; a new `suppressed_lead_speech` stat surfaces in the
wake-detectors heartbeat.

**Guards:** `tests/unit/plugins/wake/test_vosk_wake_word_agnostic.py`
("stands alone" section: both live fire classes pinned as rejections, the
isolated call / natural pause / trailing-command breaths pinned as fires,
and the sibling-rescue path pinned as unable to bypass the gate).

**OPEN follow-up.** `jarvis/speech/rolling_whisper_wake.py` (the stt_match
wake path) verifies via a transcript too and has NO equivalent
before-the-phrase isolation gate — the identical false-wake class is
plausibly still open there whenever that engine is active. Port the same
word-agnostic lead-in check to its match site, calibrated on real windows.

## BUG-128: a narrow pane's agent draws two screens into one grid — the "mirrored" terminal on macOS (HIGH, FIXED 2026-08-11)

**Symptoms (2026-08-11).** In the Agentic IDE the maintainer's macOS panes
showed doubled, character-by-character text: output interleaved with itself so
the pane read as corrupted rather than as merely narrow. The same build on
Windows looked perfect, which made it read as a macOS renderer or WebView fault.
It was neither. The distinguishing variable was never the platform: the Windows
window was maximized and the Mac window was not, and only a pane below the
viewer floor triggers it. Restarting a pane cleared it — a fresh PTY is spawned
at the size the pane currently measures, so the two grids agree again until the
next refused resize.

**Root.** A resize is a REQUEST. `Registry.resize` is allowed to turn one down
(a tile under `MIN_VIEWER_COLS`/`MIN_VIEWER_ROWS` keeps the working geometry, a
viewer that no longer holds the pane is ignored) and to clamp one (a pane stuck
below the floor is lifted onto it). The pane, meanwhile, reflows its own xterm
the instant it measures the tile — before anything has agreed to that size — and
the wire had no frame for "not granted". So a refusal left the viewer's grid and
the agent's PTY permanently different widths, with neither side able to notice.
A TUI addresses rows by RELATIVE cursor moves, so from that point the agent's
repaints finished into rows holding other text. Two screens, one grid.

The handshake had the same hole: `attach` may refuse the size a re-joining pane
asks for, and a pane re-joining a running agent is the likeliest place of all to
inherit a geometry it never requested.

**Fix.** `Terminal.pty_cols`/`pty_rows` record the geometry actually handed to
`setwinsize` — the transcript is a display mirror and drifts from the PTY in both
directions, so it cannot answer "what size is the agent drawing in?".
`Registry.pty_geometry` exposes it; `report_geometry` in `agentic_pty` sends
`{t:"size",cols,rows}` whenever the granted size differs from the requested one,
on the handshake and on every later resize. Sent ONLY on disagreement, so a
dragged seam adds no chatter and a client that does not know the frame is no
worse off than before. The pane follows it via `onGeometry` → `term.resize`,
deliberately without updating `sentSize`: that records what was ASKED, which is
what stops a refused pane from oscillating — it asks once, then stays quiet.
Following the agent also repairs what is already on screen, because that content
was drawn for the agent's geometry all along.

The below-the-floor rescue was reading the transcript too, and now reads the PTY.

**Guards:** `tests/unit/agentic_ide/test_refused_geometry.py` (a refused resize
reports, a granted one stays silent, a lifted pane reports where it was lifted
to) · `paneSocket.test.ts` "geometry reconciliation" (the frame reaches
`onGeometry`, a malformed one is ignored, an older client survives it) ·
`test_session.py::test_a_pane_under_the_crash_guard_is_lifted_off_it` now stages
"under the guard" on the PTY, where the fact lives.

**Lesson.** A viewer that reflows itself on a request it has not had granted is
holding an assumption, not a measurement. Any wire that lets one side clamp or
refuse needs a path back, or the two sides drift silently and the damage surfaces
somewhere that looks unrelated — here, as a rendering fault on one OS.

## BUG-129: Jarvis speaks three words, then goes silent for the rest of the answer — the emergency voice is one blocking whole-answer take (HIGH, FIXED 2026-08-11)

**Symptom (maintainer field report, voice session 2026-08-11 21:26, realtime).**
Asked on camera to explain what it can do, Jarvis started the answer, spoke
roughly three words, and stopped. Nothing else was ever heard. The session
transcript showed a complete, well-formed answer — so the recording claims
Jarvis delivered a paragraph the user never heard, and the only honest reading
from the chair is "it broke mid-sentence".

**What the log actually shows** (session `88eb1137`, `provider=gemini-live`,
tool mode `delegate`):

| time | event |
|---|---|
| 21:26:56.679 | `RT-SPAWN span=first_audio ms=8145` — the live model starts speaking its own answer |
| 21:26:56.842 | `OutputStream opened @ 24000 Hz (… actual_latency=1.542s)` |
| 21:26:59.713 | `blocked an action promise with no execution evidence` |
| 21:26:59.787 | `withholding provider audio … awaiting a new response after a barge-in or delegation` |
| 21:27:01.063 | the router brain (grok) returns the grounded answer — **this is the text in the transcript** |
| 21:27:04.786 | `provider produced no audio for a grounded Brain result; using surface TTS fallback` |
| 21:27:05.301 | `Gemini-TTS stream quota-blocked on gemini-3.1-flash-tts-preview (retry in 1 min)` |
| 21:27:05.302 | `stream failed before first audio — blocking fallback will synthesize this sentence` |
| 21:27:20.731 | `request_hangup` — the user gives up after **15.4 s of silence** |
| 21:27:41.345 | the blocking `gemini-2.5-flash-preview-tts` POST returns 200 — **36.0 s** after it started, 20.6 s after the session was gone |

Three of those lines are working as designed. The provider promised
capabilities it had not exercised, the action-promise guard blocked the
rendering 2.9 s in and delegated to the Brain, and the Brain produced a
grounded answer. With a 1.542 s output latency, 2.9 s of sent frames is about
1.4 s of *audible* speech — the three words. Everything after that point was
supposed to arrive through the surface TTS fallback.

**Root cause.** The realtime surface fallback is built with
`chunk_by_sentence=False` on purpose: one whole-answer generation is what keeps
the session's voice identity intact (BUG-090). That is only survivable because
the transport is meant to stream — `build_realtime_surface_tts` says so in a
comment. But `_synthesize_stream_one` streamed from `self._model_name` and
nothing else: it had **no sibling-bridge rung**, while the blocking ladder in
`_synthesize_one` has had one since 2026-05-14. So a `RESOURCE_EXHAUSTED` on
the primary model ended streaming for that text outright, and the one-take
design fell through to a single blocking `generate_content` — 36 s for a
1000-character answer, with nothing audible until the very end.

Two multipliers made it worse than a one-turn accident. The cooldown the 429
arms runs up to an hour, and the old code returned from the stream immediately
while it was armed — so *every* following turn took the same blocking take, not
just the one that tripped the quota. And `streaming` was inherited from the
pipeline's `[tts]` knob, so an install that had turned pipeline streaming off
was in this state permanently, with no quota involved at all.

**Fix.** `jarvis/plugins/tts/gemini_flash_tts.py` — `_stream_model_ladder()`
mirrors the blocking ladder exactly (primary first, sibling only while the
primary is quota-blocked, each rung skipped while its own cooldown is armed),
and `_synthesize_stream_one` walks it, delegating one generation per rung to
`_stream_one_model`. A 429 now bridges to the sibling **on the streaming
transport**, so the answer keeps a sub-second time-to-first-audio in exactly
the case that produced the silence. It reaches the same model the blocking
ladder would have used, so no new voice is introduced, and the bridge
announcement is now shared by both transports (`_announce_sibling_bridge`) so
it stays once-per-process. A non-quota error still does NOT bridge — it falls
to the blocking ladder, which retries the primary, so a transient blip cannot
flip which model speaks. `jarvis/plugins/tts/__init__.py` pins `streaming=True`
for the surface instance: it is the one-take design's precondition, not a
preference to inherit.

**Guards.** `tests/unit/plugins/tts/test_gemini_streaming.py` — a primary 429
streams from the sibling and never reaches the blocking path; an already-armed
cooldown starts on the sibling without calling the blocked primary at all; a
sibling 429 arms its **own** timer; both cooldowns armed skips the network
entirely; a transient error does not bridge and does not arm a cooldown.
`test_realtime_surface_tts.py` — the surface instance streams even with
`[tts].streaming = false`. Verified sharp: four of these fail against the
previous implementation.

**Class rule.** A fallback whose latency budget depends on a transport must own
that transport, not inherit it — and every degradation ladder needs the same
rungs on every transport that can walk it. Here the blocking path had a quota
bridge and the streaming path did not, so a quota error silently demoted the
answer from "streams in under a second" to "one 36-second generation", and the
turn reported itself healthy the whole way down. When the last mile before the
speaker can take that long, silence is indistinguishable from a crash to the
only person who can tell you it broke.

## BUG-130: a spoken self-correction opens the RETRACTED fleet — and the correction is typed into the fresh agents as their coding task (HIGH, FIXED 2026-08-12)

**Symptoms (2026-08-12 11:56, gemini-live).** The maintainer asked for two new
terminals, took it back mid-sentence and replaced the request: "Kannst du bitte
zwei neue Terminals öffnen? fünf neue Nee, nee, wir machen noch zwei neue Codex <!-- i18n-allow: production transcript -->
Terminals und fünf neue Code Terminals." <!-- i18n-allow: production transcript -->
What happened: two PLAIN panes opened (T4/T5 — the retracted half of the
sentence), and the correction itself was treated as those panes' work. The
prompt writer faithfully expanded it, so two fresh Claude Code agents were each
briefed to "open seven new terminal sessions: two running Codex and five
running Claude Code" — coding agents ordered to do the workspace's job, which
no user has ever meant. The writer even understood the correction perfectly
("the final, binding request is exactly two Codex plus five Claude Code"); the
understanding sat in the wrong executor.

**Root.** Two independent blind spots in `jarvis/agentic_ide/intent.py`, and
they compounded:

1. **Speech retracts, parsers don't.** `_spawn_span` reads exactly ONE clause —
   a deliberate bound (BUG-class of 2026-07-27: unrelated numbers joined into a
   50-terminal request). The first clause ("zwei neue Terminals öffnen") won, <!-- i18n-allow: quoted transcript -->
   and nothing anywhere knew that "Nee, nee, …" had just withdrawn it. <!-- i18n-allow: quoted transcript -->
2. **Everything after the spawn clause is "the task".** `spawn_includes_task`
   saw the verb "machen" in the remainder and queued a fleet briefing;
   `spawn_instruction` handed the whole correction to the new panes. A second
   sentence that describes MORE PANES had no representation at all — its only
   two possible readings were "noise" or "work for the fleet".

**Fix.** Three deterministic pieces, still no LLM on the hot path (AP-9/AP-11):

- `_RETRACTION_RE` + `_spoken_correction_suffix`: a spoken retraction cue
  ("nee", "nein", "doch nicht", "no wait", "mejor dicho", …) cuts the <!-- i18n-allow: input vocab -->
  utterance — but ONLY when what follows is a complete pane request of its own
  (`_spawn_span`). A cue without a replacement changes nothing. The LAST
  qualifying cue wins, because stutters repair themselves repeatedly.
- `_spawn_regions`: a fleet may be described across SEVERAL clauses, each
  carrying its own full evidence (pane noun + open verb/additive). Regions are
  collected until the first briefing boundary and merged by `_merge_groups`, so
  "Öffne zwei Terminals. Mach noch drei Codex Terminals auf" is five panes <!-- i18n-allow: spoken input -->
  instead of two panes plus an absurd brief.
- `spawn_includes_task` / `spawn_instruction` measure the remainder from the
  END of the last fleet clause, so a fleet description can never leak into the
  work handed to the panes it describes.

**Guards:** `tests/unit/agentic_ide/test_spawn_intent.py` — the live transcript
verbatim (`LIVE_CORRECTION`: replaced fleet, no task), corrections in all three
locales, a retraction without a replacement changing nothing, the additive
second sentence extending the fleet, a task after the full fleet still reaching
the panes, and counts inside a briefed task never becoming more panes.

**Lesson.** Spoken language edits by retraction, not by deletion — the words a
transcription keeps are not all words the user still means. A deterministic
parser that executes speech has to model the repair marker itself, or it will
execute the draft. And when a parser has only two buckets ("fleet" / "task for
the fleet"), everything that fits neither lands in the more dangerous one:
never hand a fresh agent a task the workspace itself should have executed.

## BUG-131: one spoken order briefs the same pane twice — the provider's VAD splits the utterance and the tail becomes a second executor (HIGH, FIXED 2026-08-12)

**Symptoms (2026-08-12 16:09, gemini-live).** The maintainer asked, in one
breath, for a skills deep-dive: "Can you please prompt Gemini Pro that he
looks at our skills and makes a deep dive … It never uses the skills I've
built. It doesn't really — you know, recognize the skills." Pane T4 then
received TWO complete deep-dive briefs three seconds apart (16:10:40 and
16:10:43, `prompts_sent: 2` in the workspace snapshot) — two differently
worded expansions of the same request, each opening its own agent turn —
while the spoken announcement claimed a single handover.

**Root.** Gemini-live's VAD read the thinking pause after "It doesn't
really" as end-of-turn, so ONE utterance arrived as two final turns. Each
final is planned independently, and the 5-word tail carried planner evidence
of its own (the word "skills" matches `_SKILL_RE`), so it became a second
orchestrator turn with a second deterministic delegate. Both Brain turns saw
the same conversation history, both correctly reconstructed the same request,
and each briefed T4 — two blind executors for one order. The existing
repeat-order guard (`_order_already_executing`, built for the 2026-07-27
provider-echo shape) is deliberately scoped to a turn that asked for NOTHING
of its own; a tail fragment WITH planner evidence walked straight past it.

**Fix.** `jarvis/realtime/session.py::_continues_executing_order` — a
refusal that needs four independent probes to agree the fragment cannot
stand alone as a new order: an earlier turn's delegate is still executing
without a result; the fragment carries no self-standing order evidence (no
ACTION/MISSION/WORKSPACE reason, no addressed pane); every planner reason it
does carry is already covered by the running order's own reasons; and it is
fragment-short. A refused turn gets the deterministic progress line and the
trusted result via the late flush. Enforced at BOTH doors a second executor
can open through: the deterministic dispatch site and the provider's
`jarvis_action` call path. Review caught a vacuous-truth hole in the subset
probe before it shipped: a bare "yes" answering a clarify question or an
ask-tier confirmation plans with EMPTY reasons, and an empty set is a subset
of every running order's — so a turn the clarify/confirm mechanism already
owns bypasses the guard entirely, and refusal additionally requires the
fragment to carry at least one reason of its own.

**Guards:** `tests/unit/realtime/test_split_utterance_single_order.py` — the
live transcripts verbatim against the real planner, plus the counter-cases
that must keep their dispatch: a command-verb follow-up, a question carrying
new evidence, an addressed pane, a long same-topic follow-up, a completed
order (clarify/confirm flows), and the provider tool-call door.

**Lesson.** A provider's VAD is a turn-splitter, not a request-splitter:
"final" marks where the model stopped listening, not where the user stopped
meaning. When two segments of one utterance each carry planner evidence,
text-level deduplication cannot see the repeat — the comparison has to
happen at the EVIDENCE level, where a segment whose reasons are a subset of
the running order's is a continuation, never a new order. And the asymmetry
decides the ties: a wrongly refused turn degrades honestly into "still
working on it", while a wrongly allowed turn executes a user order twice.

## BUG-132: a fleet briefed BY KIND gets one mashed brief — every pane receives the whole enumeration and picks its own slice (HIGH, FIXED 2026-08-12)

**Symptoms (maintainer report 2026-08-12).** Two shapes of the same failure:

1. "Open five claudes, two codexes and one opencode. Prompt the five claudes
   to fix the login bug, prompt the two codexes to write tests and prompt the
   opencode to update the docs" — the fleet opened correctly, but every one of
   the eight panes received the ENTIRE enumeration as its own task. Each agent
   then picked whatever slice it liked; the routing the user had spoken out
   loud was gone.
2. "Spawn two new terminals and prompt them, one fixes a bug on macOS and one
   fixes a bug on Linux" — both fresh agents received the whole sentence and
   raced on the FIRST slice: two macOS fixes, no Linux fix. The task text even
   opened with the leaked address fragment "them,".

**Root.** `spawn_instruction` returns ONE string — everything behind the last
fleet clause — and `SpawnGroup` carried no notion of a task. Per-pane briefs
existed only behind `wants_split`, which requires the explicit divide
vocabulary ("teilt es unter euch auf"); a division the user has ALREADY made, <!-- i18n-allow: quoted spoken input -->
by kind or by enumeration, matched nothing and fell through to the shared
brief. And `_TASK_AFTER_BRIEF_RE` did not know the pronoun "them", so the
address bled into the work.

**Fix.** Two detectors in `jarvis/agentic_ide/intent.py`, consumed by
`BrainManager._brief_spawned_agentic_ide_fleet`:

- `spawn_group_tasks`: deterministic per-CLI task map for kind-addressed
  briefs ("prompt the claudes to X", "die Codexes sollen Y"). The manager maps
  each new pane to its kind's task; a kind the brief does not name stays
  BLANK on purpose, and any shared work in front of the first kind-brief
  stands the whole map down so it is never withheld from unnamed panes. No
  provider call: the user already divided the work, routing it is string work.
- `distributes_tasks`: an enumeration that IS a division ("one fixes the
  macOS bug, one fixes the Linux bug", "einer … der andere …") now reaches <!-- i18n-allow: quoted spoken input -->
  the `work_split` planner exactly like an explicit split request, on both the
  spawn-brief path and the addressed-fleet path. Two guards keep the ordinary
  word "one" honest: the enumerator must open its clause, and it must carry
  its own predicate — "the other"/"der andere" alone is exempt, having no
  counting reading.

**Guards:** `tests/unit/agentic_ide/test_spawn_intent.py` (per-kind maps in
three locales, the stand-down cases, enumerations vs. ordinary counts) and
`tests/unit/brain/test_agentic_ide_spawn_fast_path.py` (kind-routed
assignments, the unnamed kind staying blank, the enumerated division reaching
the split planner).

**Lesson.** A parser whose output type cannot REPRESENT the request will
flatten it into something it can, and the flattening is silent: the fleet
opened, the panes were briefed, every receipt looked healthy — only the
routing the user spoke was gone. When users hand out work they do it in
exactly two ways, by NAME and by ENUMERATION; an executor that models only
the collective address will mash both.

## BUG-133: the wake word fires on random words and goes deaf mid-sentence — the shape path's competition rubber-stamps the full ring while the isolation gate demands staged silence (HIGH, FIXED 2026-08-12)

**Symptoms (2026-08-12, vosk_kws, phrase "Hey George").** Both accuracy
directions broken at once. False fires: three activations this session, and
in two the free ear heard something with NO resemblance to the phrase — a
bare `'pedro'` (16:43:34) and `'age watch'` (16:09:32); a single random word
activated a two-word wake phrase. Missed activations: the phrase spoken in
the middle or at the end of a sentence never fired, and after normal speech
the user had to stay silent for seconds before the detector would accept the
real wake word again.

**Root — four causes stacked.** (1) The acoustic competition, the shape
path's only content gate, re-scored the WHOLE 3 s ring: forced alignment
over that much audio places the phrase anywhere and absorbs the rest into
"[unk]"s, so it kept the phrase at conf 1.0 for nearly any short speech
burst — 'pedro' won a competition it should have lost. (2) The shape path
demanded no prefix-part evidence, so one bare word could satisfy a
multi-part phrase. (3) The 2026-08-10 leading-isolation gate hard-rejected
any candidate with >0.25 s of voiced speech in the 0.6 s before it — the
mid-/end-of-sentence deafness and the "wait in silence" dead time, by
design. (4) The verify re-score pooled every phrase word anywhere in the
ring decode ('hey hey george hey [unk]' → min-conf over four entries, span
seconds wide), so an embedded wake failed the re-score even where it was
heard; rejected candidates then armed a 2 s verify backoff whose latch
kept the FIRST candidate and dropped everything after it.

**Fix.** `jarvis/plugins/wake/vosk_kws_provider.py`: the competition now
judges the SPAN-TRIMMED audio (±0.3 s pad) and offers the free ear's own
hypothesis as an explicit grammar alternative next to "<prefix> [unk]" and
"[unk]" — the decoder keeps the phrase only when the span's audio genuinely
supports it; unprefixed phrases compete too. The isolation gate is removed
(maintainer mandate: mid-sentence wakes must fire); shape durations are
clipped to the unpadded span so neighbouring sentence words no longer
overflow the call-sized budget. The re-score scores only an ADJACENT
in-order token run (scattered 'hey … george' never counts — a multi-part
wake is one unit) and retries over the trailing 1.8 s cut when the full
ring absorbed the phrase. A stage-1 hit during reject-backoff refreshes the
latch instead of dying in it.

**Evidence.** `scripts/vosk_wake_bench.py` (new): synthesized de+en voices
through the REAL detector and models, HEAD vs fixed on the SAME corpus
(stream-time clock, so backoff deadlines behave as in the live pipeline).
False fires 10/48 → 0/48 for "Hey George", and 0/144 across three phrases.
Positives 14/16 → 15/16 with end-of-sentence 3/4 → 4/4 — and the baseline's
embedded-wake numbers flatter it: the TTS renders comma pauses long enough
to satisfy the isolation gate, which real flowing dictation does not (the
live "wait in silence" reports). Generalization: "Hey Jarvis" 15/16,
"Hey Alex" (a hard first-name phrase) 14/16, all at zero false fires.
Borderline candidates may flicker ±1 between runs (Kaldi prewarm conf
jitter ≤0.09 at the 0.9 anchor); the negative classes are stable at zero.

**Guards:** `tests/unit/plugins/wake/test_vosk_wake_word_agnostic.py` —
'pedro' and the 2026-08-10 mid-dictation transcripts stay silent (by
content, not position), the mid-sentence spelled wake fires, the competition
provably receives span-trimmed audio plus the hypothesis, scattered phrase
parts never re-score, and the trailing-cut retry runs only after the full
ring failed.

**Lesson.** Position was standing in for content: the isolation gate
suppressed mid-speech fires because the competition could not discriminate
mid-speech audio — but the competition was weak only because it judged
audio the candidate never claimed. Trim the question to the claimed span
and give the decoder its own best alternative, and the position heuristic
(with its dead time) becomes unnecessary. A verify stage that pools
evidence across the whole window ('any phrase word anywhere') will both
under-accept the genuine embedded case and over-accept the scattered one;
score exactly the run that claims to be the phrase.

## BUG-134: a spoken "two terminals" opens FOUR — counts inside the task half of the sentence are read as more fleet (HIGH, FIXED 2026-08-12)

**Symptoms (live 2026-08-12 17:40, maintainer report).** "Open two new cloud
code terminals and prompt them" — the workspace opened FOUR Claude Code panes,
each on a paid subscription seat, and briefed all four. The four composed
briefs even disagree about the plan: two say "two terminals share this work",
one says "audit both platforms", one says "macOS only" — the composer
downstream reconstructing sense from a fleet that was already wrong. All four
pane sessions started the same second (17:40:07), so this was ONE spawn
misread, not a repeated turn.

**Root.** The parser has exactly two buckets — "fleet description" and "task"
— and the boundary between them was a fixed briefing-verb list. Everything in
front of the first known briefing verb is fleet, so when the task half talked
ABOUT the panes, its counts became MORE panes:

- the restated count: "… and the two terminals SHOULD do a deep dive …" —
  2 + 2;
- the spoken enumeration: "… one terminal takes macOS and one terminal takes
  Linux" — 2 + 1 + 1;
- the handover verbs of plain speech were missing from the boundary list:
  "sag ihnen, …" sailed past while "tell them" was caught. <!-- i18n-allow: input vocab -->

The 2026-07-27 guard ("counts inside a briefed task never double the spawn")
only ever held when a KNOWN briefing verb marked where the task starts.

**Fix** (`jarvis/agentic_ide/intent.py`):

- `_task_subject_cut`: the verbless handover boundary. A pane noun past the
  anchor whose next words are its own predicate — a modal ("terminals
  should"), a work verb ("terminal hunts"), or the elided-verb complement
  ("ein Terminal für Linux") — marks where the clause stops describing the <!-- i18n-allow: quoted spoken input -->
  fleet and starts its work. Additive counts ("one MORE terminal") and
  named-CLI groups ("… und 3 Claude Code Terminals AUF" — the separable <!-- i18n-allow: quoted spoken input -->
  particle) are exempt, so real fleet extensions stay whole.
- `_spoken_groups` drops task-side unclaimed groups as a second net: non-first
  groups whose noun carries its own predicate, and coordinated value-one
  pairs — spoken enumeration counts items off one at a time ("one … one …"),
  while a fleet request states its size once ("two terminals").
- `_FLEET_BRIEF_RE` / `_TASK_AFTER_BRIEF_RE` learn `sag`/`diles`; "hunt"
  joins the work-verb vocabulary.

The rescued task text flows on through `distributes_tasks` (BUG-132), so the
two panes now get their own macOS/Linux slices as well.

**Guards:** `tests/unit/agentic_ide/test_spawn_intent.py` — all four live
shapes pinned to exactly two panes WITH the task and the division intact,
plus the additive-extension and separable-particle exemptions.

**Lesson.** A two-bucket parser puts everything it cannot classify into
whichever bucket is cheaper to reach, and here that bucket spends money: every
phantom pane is a billed subscription seat doing unasked work. The boundary
between "the fleet" and "the fleet's job" is not a verb list — speech hands
work over by RESTATING the subject at least as often as by naming a verb, and
the parser has to model that shape, not enumerate its vocabulary.

---

## BUG-135: speaking while Jarvis works on an action does nothing — barge-in never reached the thinking phase (HIGH, FIXED 2026-08-13)

**Symptoms (maintainer report).** In realtime mode, talking while Jarvis is
thinking does not interrupt him. Cutting into his SPEECH works; cutting into
the silent stretch where a delegated action runs does not. Worse than silence
in the common case: a short interruption came back as the progress line
("I'm still working on it"), so the session demonstrably heard the user and
carried on anyway.

**Root.** Three independent absorbers on one path, each correct in isolation:

1. **The edge is deferred, then confirmed toothlessly.** While an action runs,
   `_pending_delegate_needs_endpoint_protection()` parks the provider's
   `speech_started`/`interrupted` edge in `_deferred_provider_speech_start`
   instead of acting on it — right, because Gemini reports room noise and real
   barge-ins with the same event. The confirm path then calls
   `_barge_in(interrupt_provider=False)`, which cuts audio and sets drop flags.
2. **Nothing cancels the work.** `_barge_in` does not touch `_delegate_tasks`
   at any point. Delegates are cancelled only at teardown; on a new turn they
   are *detached* (`_retain_detached_delegate_task`) and their result is queued
   for the late flush. `test_barge_in_detaches_late_delegate_result_from_new_turn`
   pinned that as intended. So the action ran to completion and still spoke.
3. **The words were classified as a continuation.** `_continues_executing_order`
   (BUG from 2026-08-12: one sentence split by a thinking pause briefed a pane
   twice) folds a fragment into the running order when it is under
   `_CONTINUATION_FRAGMENT_MAX_TOKENS`, carries no self-standing planner
   reason, and its reasons are covered by the running order. "Stop", "warte
   mal", "never mind" satisfy all four, so the interruption was answered with
   `_speak_pending_action_status()`.

Absorber 3 is why the bug reads as being ignored rather than as a missed cut:
the session took the turn, understood it, and replied that it was busy.

**Root, corrected (2026-08-13, after the first fix failed in the field).** The
three absorbers above are real, but they are all downstream of a simpler fact:
**during the thinking phase no local barge detector runs at all.** Barge-in
over PLAYBACK works perfectly and always has — the desktop pump feeds a local
Silero detector that fires in milliseconds. That detector sits inside the
`echo_guard_active` branch of `_send_microphone`, entered only while audio is
playing. During the silent wait it is not idle, it is unreachable. Everything
above was therefore describing what happened to the ONE remaining signal (the
provider's VAD) after the local path had already been skipped.

This is why the first fix did not work in the field: it read the WORDS and
fired only on an explicit stop phrase. Interrupting must not require a command
— the maintainer's report was "I don't want to say stop; when he is speaking
it works perfectly, why not now?" The trigger has to be voice, not vocabulary.

**Fix (primary): arm the mechanism that already works.**

- `jarvis/speech/pipeline.py`: a SECOND `DesktopRealtimeBargeInDetector`, fed
  during the silent wait, firing the same `barge_in` control the playback path
  uses. Two details are load-bearing and each one silently disables it:
  `output_active=None` (with the playback probe attached, `feed` returns None
  for as long as nothing plays — the `_playback_started` gate in
  `jarvis/realtime/desktop.py`, i.e. precisely the silence it must listen
  through), and warming it alongside the first (`feed` returns None while
  `_ready` is false, so an unwarmed detector looks exactly like a room that
  never speaks).
- Arming requires the microphone to fall quiet once after the turn is
  committed (`_THINKING_BARGE_QUIET_S`), or the user's own trailing words arm
  it against themselves — that shape is turn fragmentation and belongs to the
  session's `_user_is_speaking` hold (872b05051), not to a barge.
- `RealtimeVoiceSession.owes_the_user_a_reply()` is the capability the pump
  reads. Deliberately not a turn-state enum: the microphone pump must not
  learn the turn machinery.
- The frame is uploaded either way. Unlike the playback branch there is no
  echo to withhold, and the words must reach the provider or the interruption
  would take the floor and deliver nothing.

**Fix (secondary): what the words are still for.** Once the floor is taken,
the vocabulary decides only whether the running ACTION dies — never whether
the user gets to speak.

- `jarvis/speech/interrupt_intent.py` (new, sibling of `hangup.py`): regex-only
  probe, de/en/es at equal depth, whole-utterance anchored so a stop word
  mid-sentence is inert. Two strengths — a bare "no" counts only as a complete
  utterance ("no problem" must not abort), while a hard token also carries a
  replacement ("wait, I meant Rome" → `INTERRUPT_REDIRECT`). Hang-up phrases
  are refused: ending the CALL outranks ending the action.
- `jarvis/realtime/session.py`: the probe runs on the final transcript before
  any routing decision — deliberately NOT on the VAD edge, so it also works on
  a provider that emits no `speech_started` at all. Guard order is
  load-bearing: `_user_is_speaking()` first (a hesitation mid-sentence is not a
  stop, and "warte" is also a thinking-filler — 872b05051), then the
  clarify/confirm latches (a bare "no" may be an ANSWER), then the words.
  `_cancel_running_delegates()` cancels the tasks, drops their queued late
  results, and latches `delivery_started` so a delegate finishing inside the
  cancellation window cannot re-queue itself. `_acknowledge_interrupt()`
  confirms from a closed per-language pool — the provider's context still holds
  the order and, left to itself, answers the request the user just withdrew.
- `_continues_executing_order` returns False for any interrupt intent. An
  explicit stop is never a continuation of the thing it asks to stop.

**Guards:** `tests/unit/speech/test_interrupt_intent.py` (72 cases: the stop
vocabulary per locale, the redirect shape, the ordinary-speech and hang-up
negatives) and `tests/unit/realtime/test_session_action_interrupt.py` (the
action is really cancelled and its result never surfaces; the stop is confirmed
out loud and never with a progress line; a redirect cancels AND is answered; a
continuation fragment, a clarify answer, and a mid-sentence hesitation still
cancel nothing).

**Guards (primary fix):** `tests/unit/speech/test_realtime_mode.py` — speaking
during the thinking phase sends `barge_in` and the captured speech reaches the
provider, with NO stop word anywhere in the test; a still-running utterance
does not; and the two detectors are asserted to be configured differently
(playback keeps the probe, thinking must not have one).

**Lesson.** Two lessons, and the second one cost a shipped fix.

Every one of the three absorbers was added for a real incident and none was
wrong on its own. "The user is still talking" and "the user wants me to stop"
look identical from any single signal — the microphone cannot read intent and
the words cannot tell a hesitation from a command. Guards accumulated along one
path eventually cover every case they were each built for, and between them the
case nobody wrote a guard for.

But the reason the first fix shipped and failed is worse and more general: the
investigation traced the signal that was PRESENT (the provider's VAD edge, with
its deferral and its progress line) and never asked why the mechanism that
demonstrably works ten seconds earlier — the local detector during playback —
was not running. A feature that works in one phase and not another is a
question about the DIFFERENCE between the phases, and the answer here was a
branch condition, not a heuristic. When a user says "it works perfectly there,
why not here", that sentence is the diagnosis: find the machinery that makes
"there" work and ask what disables it in "here", before writing anything new.

## BUG-136: a spoken brief for a pane outlives the call that ordered it — the confirmation is spoken before the prompt exists, and hanging up does not stop it (HIGH, FIXED 2026-08-13)

**Symptoms (maintainer report).** "The terminals are prompted before they
actually are prompted." Jarvis says the pane has been prompted, nothing in the
app indicates work is running — no thinking state, no status line — so it reads
as a failure, and roughly twenty seconds later the pane really is prompted.
Second half of the same report: hanging up does not end it. The order keeps
running in the background and types into the pane after the call is over.

**Forensics (live log, 2026-08-13 11:19-11:21).** One spoken order to T5
produced four deliveries and three contradictory spoken accounts:

| time | what happened |
| --- | --- |
| 11:19:33 | turn 1 dispatched; the composer's writer (`claude-cli`) starts |
| 11:19:42 | a repeat turn is answered with the honest progress line |
| 11:19:43 | **user hangs up** (hotkey); two delegates "retained for exactly-once delivery" |
| 11:20:03 | T5 is typed into — 20 s after the hangup, inside the NEXT call |
| 11:20:10 | T5 is typed into again (turn 2's own composition) |
| 11:20:12 | "I have prompted T5 to do a deep dive …" — **2.0 s after dispatch** |
| 11:20:17 / 11:20:21 | the same sentence announced twice more, the last one after the second hangup |
| 11:20:26 | that order's fan-out finally reports `could not send to T5` → "I could not reach T5, so nothing is running." |

**Root.** Three separate causes, one shape: **the delivery outlives the turn
that ordered it, and nothing downstream knows.**

1. **Structural.** Composing a brief is 16-30 s by deliberate design (quality
   over latency, maintainer decision 2026-07-25; `COMPOSE_TIMEOUT_S` is 90 s,
   `HEDGE_AFTER_S` 30 s), while a delegated voice turn is force-answered after
   20 s (`tool_use_loop` deadline). A spoken pane order therefore CANNOT finish
   inside its own turn: the verdict always arrives as an out-of-band
   announcement, which can land in a later call, after a hangup, or out of order
   against a second delivery of the same order.
2. **The claim.** On a turn where no delivery was dispatched at all (a VAD
   fragment — "when you speak with him you can't"), the router brain narrated
   the PENDING order as finished. Every workspace fact in front of it said a
   prompt had been sent to that pane (`prompts_sent`, `last prompt sent: "…"`)
   and nothing said the current brief was still being written. The prompt rule
   forbidding the claim was already there and was ignored — prompt compliance is
   not a correctness boundary (BUG-047 class rule).
3. **Invisibility.** Both signals that would have shown the work existed —
   the composer's `STAGE_*` beats (`AgenticIdeComposeProgress`) and
   `AgenticIdePromptSent` — were published ONLY by the REST route behind the
   typed prompt bar. The spoken path passed no sink and published no delivery
   event, so a voice-driven brief was invisible in every window until the agent
   echoed it back. The frontend has rendered both for weeks
   (`useWebSocket.ts`, `AgenticGrid.tsx`).
4. **Hangup.** `fanout.deliver` deliberately detached on caller cancellation and
   `RealtimeVoiceSession.end` transfers unfinished delegates to process scope,
   both fixes for the opposite failure (2026-08-06: a hangup mid-composition
   typed nothing while the user believed it had). Together they made a hangup a
   no-op for an order already in flight.

**Fix.**

- `jarvis/agentic_ide/fanout.py`: `deliver(..., on_progress=, on_delivered=,
  cancel_on_hangup=)`. `cancel_spoken_deliveries()` aborts every spoken fan-out
  still being written; the detach stays the rule for REST/CLI callers, where a
  lost client is a lost READER, not a withdrawn order. `in_flight_briefs()`
  exposes the per-pane ledger.
- `jarvis/realtime/session.py`: `_abandon_spoken_workspace_briefs()` runs in
  `end()` for every reason except the two handovers (`desktop_fallback`,
  `realtime_fallback`, where the same call continues in the pipeline).
  `_run_delegate` re-raises the cancellation, so an abandoned brief speaks
  nothing at all. `jarvis/speech/pipeline.py` does the same at the one
  engine-agnostic session end, for the classic engine.
- `jarvis/brain/manager.py`: `_agentic_ide_delivery_signals()` publishes the
  composer's beats and `AgenticIdePromptSent` from the spoken path — the same
  events, so no client changes.
- `jarvis/agentic_ide/context.py`: a pane with a brief in flight is stated as
  `A BRIEF IS STILL BEING WRITTEN … nothing has reached T5 yet`, with the rule
  that the counted prompts are OLD ones.

Safe to abandon precisely for THIS kind of work: the PTY write is the last step
of a fan-out, so an abandoned brief leaves no text in the input box, no receipt
and no started agent — unlike a sent mail or a spawned mission, which is why
everything else is still retained.

**Guards:** `tests/unit/agentic_ide/test_fanout.py`
(`test_a_hangup_abandons_a_spoken_brief`,
`test_a_spoken_delivery_narrates_and_reports_its_arrival`,
`test_an_injected_composer_needs_no_progress_keyword`, and the renamed
`test_a_cancelled_reader_still_briefs_the_pane` which pins the REST contract),
`tests/unit/realtime/test_session.py`
(`test_hangup_abandons_a_coding_brief_that_is_still_being_written`,
`test_a_handover_keeps_the_coding_brief_alive`, alongside the unchanged
`test_session_end_names_the_delegated_request_it_retains`), and
`tests/unit/agentic_ide/test_context.py`
(`test_a_brief_being_written_is_stated_as_not_arrived`).

**Lesson.** A quality decision that makes one step slower than the turn that
runs it changes the honesty contract of everything downstream, and that
consequence is invisible at the site of the decision. Two survival rules were
added for the same 20-30 s window from opposite directions — "finish the brief
after the caller dies" and "retain the delegate after the socket closes" — and
each was right about the incident it fixed. What neither could know is who the
reader is: a dropped HTTP client has lost the answer, while a person who hangs
up has withdrawn the request. The same cancellation means different things to
different callers, so survival has to be the CALLER's declaration, never the
work's default.

## BUG-137: a spoken order is cut off after a few sentences — the guard that protects it reports its own ceiling as the user falling silent (HIGH, FIXED 2026-08-13)

**Symptoms (maintainer report).** In realtime mode you cannot finish a long
prompt. After roughly three or four sentences the turn submits by itself and
the rest of the sentence is gone. Reported for the agentic-IDE case in
particular — the truncated fragment is what gets typed into a coding pane and
submitted with Enter, so a quarter-order becomes a real briefing.

**Evidence.** `data/flight_recorder/<date>.jsonl` + `data/jarvis_desktop.log`,
session `373af352`, 2026-08-13 16:46/16:47. Both turns were terminal-prompt
orders; the recorded `user_text` of the second ends mid-phrase ("…and you know
when you do a customer ID with a"). The log tells the whole story:

```
16:46:22.919  turn-state: LISTENING -> PROCESSING      ← provider closed the input turn
16:46:23.189  holding the dispatch — the microphone still carries the user's voice
16:46:26.939  user stopped speaking after a 4.00s hold ← the CEILING, not the user
...
16:47:19.668  holding the dispatch — the microphone still carries the user's voice
16:47:25.527  provider committed a boundary while the user is still audibly speaking
```

`4.00s` is `_USER_SPEAKING_DISPATCH_CAP_S` to the centisecond. At 16:47:25 —
six seconds after the cut — the session's own microphone probe still said the
user was talking.

**Root.** Two layers, and the second one hid the first.

1. **The provider was never asked to be patient.** `RealtimeSessionConfig`
   carried exactly one turn-detection knob, `silence_duration_ms`, pinned to
   `None` by the 2026-07-21 directive (a fixed window taxed every short
   utterance and read as "done speaking but still listening" — that directive
   was right). So `gemini_live.py` omitted `realtime_input_config` entirely
   and inherited Gemini's native end-of-speech detection, which treats an
   ordinary mid-sentence pause as end-of-turn. The knob that actually fits the
   problem — `end_of_speech_sensitivity`, present in the installed SDK, and
   unlike a silence window free for short utterances — appeared in **zero**
   files in the repository.

2. **The compensating guard was a fixed budget.** `_await_stable_input_boundary`
   correctly detects that the microphone still carries the user's voice after a
   premature commit (BUG-131's fix), but measured its authority as
   `started + 4.0s` from the provider's commit. That is a budget for how long
   the user may keep talking. Speak past it and the tail is dropped, the
   truncated text is dispatched, and the pane is briefed with it.

**Why it survived a day of live calls.** The release log line read
`user stopped speaking after a 4.00s hold`. A timeout reported as an
observation. Every truncation looked, in the log, like the guard working.

**Fix.** Both layers, because either alone leaves the failure reachable:

- `end_of_speech_sensitivity` added to `RealtimeSessionConfig` (default
  `"low"`), wired in `gemini_live.py` behind an SDK capability probe (AP-21 —
  an SDK without the enum opens on the provider default and says so in the
  log) and in `openai_realtime.py` as semantic-VAD `eagerness`. Plain
  `server_vad` has no equivalent and honestly keeps its default rather than
  faking patience with a fixed window. `silence_duration_ms` stays unset: the
  2026-07-21 directive is untouched.
- The microphone hold became a ROLLING window
  (`_MIC_HOLD_STALE_TRANSCRIPT_S`, re-armed by every growth of the input
  transcript) with an absolute backstop (`_MIC_HOLD_ABSOLUTE_CAP_S`). The
  ceiling still bounds the only failure it was ever for — a floor stuck open
  on room noise or a hot mic — because noise produces voiced frames but no
  WORDS. A talking user keeps the floor indefinitely; a stuck floor releases
  within one window. The provider-silence cap no longer fires while the
  microphone is actively holding.
- The release log line now distinguishes "the user stopped" from "the floor
  stayed loud without producing a word".

**Class.** A bounded guard whose bound is expressed in the units of the thing
it is protecting. The ceiling on "how long may the microphone override the
provider" was written as a duration of USER SPEECH, when the failure it exists
for is a microphone that produces sound without content. Bound the failure
mode, not the legitimate behaviour — and never let a guard log its own timeout
in the vocabulary of a measurement.

**Tests.** `tests/unit/realtime/test_utterance_continuation.py`
(`test_a_long_order_survives_past_the_hold_ceiling` — verified to FAIL against
the fixed-budget form, losing the final fragment exactly as the live call did;
`test_a_loud_but_wordless_floor_still_releases_the_order` for the bounded
half) and `tests/unit/realtime/test_gemini_live.py` (default sensitivity, the
opt-out, and the old-SDK degradation).

---

## BUG-138: the app is absent from Windows Search — the Start-Menu launcher is written into the Store Python's MSIX container, and every check reads it back from there (HIGH, FIXED 2026-08-16)

**Symptom.** Typing the product name into Windows Search returned a folder as
the best match and no application. The app could only be started from its
Desktop icon. On macOS and Linux the equivalent entry existed on disk but did
not appear in the desktop's app search until the next login.

**Root.** A Python installed from the Microsoft Store runs inside an MSIX
package container, and a virtual environment built from it inherits that
identity — `sys.executable` points at the venv's own `Scripts\python.exe`, so
nothing in the process looks packaged. Windows redirects such a process's
writes to `%APPDATA%` and `%LOCALAPPDATA%` into a private per-package tree
under `%LOCALAPPDATA%\Packages\<family>\LocalCache`. The per-user Start Menu
lives in `%APPDATA%`. So every launcher this code "wrote into the Start Menu"
was diverted into the container, where no shell ever looks. The folder the
shell reads was empty the whole time.

The Desktop kept working because the Desktop is not one of the redirected
roots — which is exactly why the report read "only the Desktop icon starts
it", and why the previous fix (adding a Desktop launcher) appeared to help
while leaving the real defect untouched.

**Why it survived three attempts.** The redirection is invisible from the
inside: reads are redirected too. `lnk.is_file()` answered `True` for the
trapped copy, `_shortcut_matches_install` happily validated its target and
icon, and `ensure_start_menu_shortcut` returned `True`. Every layer reported
success against a file only it could see. An earlier session measured the
shell's app index straight after writing, found the entry missing, wrote a
plain `notepad.exe` shortcut as a control, saw that missing too, and concluded
the machine's app index was broken. The control was written by the same
packaged interpreter and landed in the same container — it proved the opposite
of what it appeared to prove.

**Second half: the file has to be somewhere the index reads.** Once the
launcher reached the real per-user Start Menu, the content index still did not
carry it: on this machine the index holds only the ALL-USERS Start Menu
(`%ProgramData%`), which is where Chrome — the app the report used as the
benchmark — installs itself. A per-user install is reachable through the app
resolver rather than the file index, which is why the entry had to be a real
app entry (own executable as the target, AUMID attached) and not merely a file
in the right folder.

**Fix.** `jarvis/ui/msix_redirection.py`, wired into the launcher lifecycle:

- Detect package identity with `GetCurrentPackageFamilyName` — the only
  trustworthy probe, since the interpreter path shows nothing.
- Publish the trapped launcher into the real tree through a helper process
  that has no package identity (`powershell.exe` from System32). Its exit code
  is an honest answer to "does the shell see this file?", because the check
  runs outside the container. A launcher that stays trapped is now reported as
  NOT installed instead of as a success.
- Drop the container copy afterwards, so MSIX's container-first read
  resolution falls through to the real path and every later `is_file()` /
  staleness check upstream reasons about what the shell sees.
- Uninstall goes the same way: deletion is redirected exactly like writing, so
  an uninstall used to remove only its own container copy and leave a working
  launcher for a deleted install.

Helper paths travel in the environment, never on the command line, so a path
holding a space or a quote cannot become script.

OS parity, same class of defect — the file was written and never announced:
Linux now calls `update-desktop-database` (and carries `Keywords=`, since app
search matches `Name=` and a two-word product name loses the half users type),
macOS registers the bundle with `lsregister`. Both degrade honestly to
"appears after the next login" when the tool is absent.

**Class.** A success reported against a read that goes through the same lie as
the write. Any check that verifies an external artifact from inside the process
that produced it can only confirm its own intent. Where an artifact exists for
ANOTHER program to find — a shell, an index, a package manager — the
verification has to come from that side, or it is not a verification. The
generalisation is not MSIX-specific: sandboxes, overlay mounts, and per-user
virtual stores all fail this way.

**Verification.** Live on the reporting machine: with the launcher published,
typing "jarvis" returns Personal Jarvis as the best match, categorised as an
App and carrying the mascot icon — previously the best match was a folder and
the app appeared only as a Desktop file hit.

**Tests.** `tests/unit/ui/test_msix_redirection.py` (redirection arithmetic as
a pure function — Start Menu diverted, Desktop not, unpackaged interpreter
untouched, a container path never shadowed twice; publish moves the file and
clears the shadow; a failed publish reports failure rather than a phantom
install; uninstall removes the copy the user actually clicks) and
`tests/unit/ui/test_desktop_discoverability.py` (the announcement step on
Linux and macOS, including that an unchanged entry is still announced so a
half-finished earlier run heals). All run on every OS.

## BUG-139: the router runs on the AI Studio account while the user's Vertex project sits idle — a key-slot check dead-lists the keyless credential family (HIGH, FIXED 2026-08-17)

**Symptom.** With `brain.primary = "vertex"` and a configured
`[google].vertex_project`, small talk answered fine on Vertex Live, but every
delegated turn (anything the router handles) came back as "the account of my
language model is blocked — credit exhausted or limit reached". The log showed
`Brain gemini(...) failed: 429 ... Your prepayment credits are depleted` and
`All 1 provider attempts failed`: the chain held ONE entry, and it was the
AI Studio brain, not Vertex.

**Root.** The pre-boot key check in `BrainManager.from_config` walks
`PROVIDER_SECRET_CANDIDATES` and pushes every provider without a stored key
into `_dead_providers`. Commit `59aa705f` (same day) gave Vertex a credential
family of its own — and on the Google Cloud project path Vertex stores no key
at all: Application Default Credentials (a `gcloud` login, a service account,
workload identity) sign every request. So each boot logged
`no key in ['vertex'] -> provider 'vertex' disabled`, `_build_fallback_chain`
filtered the active provider out, and the cross-provider fallback (`gemini`,
AI Studio, prepaid, empty) answered instead. `config.py` already had
`vertex_credential_configured()` — with a docstring warning that any probe
asking "is this provider set up" must use it "or the whole family is
dead-listed" — and `app_control.KEYLESS_CREDENTIAL_PROBES` already declared
the same for every card. The key check knew neither.

**Fix.** `_keyless_provider_is_rescued_by_oauth` (`jarvis/brain/manager.py`)
now consults the declarative `KEYLESS_CREDENTIAL_PROBES` table after the codex
branch, so the pre-boot check gives the SAME answer every provider card and
every switch already gives. Log lines translated to English while touched.

**Class.** Two truths for one question. "Is this provider configured?" had a
declarative, capability-aware answer used by the UI, and a second, key-only
answer used by the boot path — and they disagreed exactly on the setup Google
documents for production. Any predicate that exists in two places will drift
on the case only one of them was taught. Same shape as BUG-138's "success
reported against a read that goes through the same lie as the write": here the
UI showed Vertex as configured while the brain had already buried it.

**Verification.** Live: the boot log line for `vertex` reads "kept active";
the fallback chain leads with `vertex` again; delegated turns answer on
`gemini-3-flash-preview` via Vertex (0.7-1.4 s measured) instead of the 429.

**Tests.** `tests/unit/brain/test_codex_oauth_rescue.py` — a project without a
key is rescued for `vertex` and `vertex-live`; no project and no key stays
dead.

## BUG-140: every Vertex Live handshake and every first brain call pays 5-8 s of local auth work — each client resolves credentials and builds its TLS trust store on its own (HIGH, FIXED 2026-08-17)

**Symptom.** "Session ready" 5.7-12.2 s after the wake word on Vertex Live
(one call ran past the 6 s provider budget and fell back to the classic
pipeline); the first turn of every brain instance 5-18 s; the log line
`vertex-live: Application Default Credentials warmed` at boot with no visible
effect. The user could not tell whether the internet or Vertex was slow.

**Root.** Measured on the maintainer box (Windows, gcloud login as ADC):

- DNS/TCP/TLS to every Google host under 200 ms; Vertex Live's first audio
  270-750 ms; text models 0.7-2.8 s. Neither the line nor Vertex was slow.
- `google.auth.default()` cost 5.3-8.5 s: google-auth reads the ADC file,
  finds no project id in it, and spawns `gcloud config config-helper` for one
  — a full CLI start — regardless of the project the caller already passes.
  Every `genai.Client` that is handed no `credentials` runs that call on its
  first request, and Jarvis built a fresh client per Live session and per
  brain instance.
- The boot warm-up built such a client and made no call — nothing was
  resolved, no token minted; the log claimed otherwise.
- `genai.Client()` itself cost 1.34 s: three `ssl.create_default_context()`
  calls (httpx, aiohttp, websocket), each parsing the certifi bundle, on the
  event loop for the Live path.

**Fix.** `jarvis/core/google_genai.py`:

- `vertex_credentials()` loads Application Default Credentials ONCE per
  process (thread-safe, keyed by the credential file in effect, failures not
  cached); every Vertex client build on the project path is handed the shared
  object. A cold cache is filled only off the event loop — the sync builder
  probes for a running loop, the async twin loads in a worker thread.
- `warm_vertex_credentials()` loads AND mints the token; `VertexLiveProvider.
  warm_transport` calls it and logs whether it succeeded.
- One shared TLS context, built with the SDK's exact parameters
  (`SSL_CERT_FILE`/certifi, `SSL_CERT_DIR`), handed to every google-genai
  client via `http_options` — the SDK's own escape hatch — for the AI Studio
  route as well; caller-supplied transport args always win. Both Live routes
  warm it at boot (`GeminiLiveProvider.warm_transport` builds the context,
  `VertexLiveProvider.warm_transport` builds it and resolves the credentials),
  and the async builders fill a cold context in a worker thread, so no first
  client of the process parses the CA bundle on the event loop.

Measured through the real code path afterwards: session open 1.07-1.5 s
(client build 2-4 ms, handshake ~1.1 s); first brain call 0.9 s.

**Class.** Per-instance resolution of a process-wide fact. Credentials and a
trust store describe the process, not the client; resolving them per client
multiplies a fixed cost by the number of sessions and hides it inside the
first request of each. The tell was a warm-up that reported success without
producing the artifact later callers would read — a warm-up must warm the
object that is actually shared, or it warms nothing.

**Tests.** `tests/unit/core/test_google_vertex_project_path.py` (loaded once
and shared; failures not cached; project clients receive the credentials, an
express key never touches ADC; the sync build never fills a cold cache on the
loop; the async build resolves off the loop; warm mints once; a service
account exported later gets its own load; every builder hands over the shared
TLS context; caller options merged not replaced; unbuildable context leaves
options untouched) and `tests/unit/brain/test_vertex_family_parity.py`
(`warm_transport` goes through the shared loader, off the loop).

## BUG-141: "handshake-budget probe failed ... not 'property'" ten times per voice call — a class-level read of an instance declaration (LOW, FIXED 2026-08-17)

**Root.** `realtime_handshake_budget_s` reads `handshake_budget_s` off the
instance when the factory instantiated the provider, and off the CLASS
otherwise. The local card declares it as a `property`; installed but not
selected, it was never instantiated, so the class read yielded the descriptor
and `float()` raised — logged as a WARNING on every probe. A provider that was
not instantiated has no handshake this session could wait for, so its
declaration cannot stretch the budget; the probe now skips a descriptor
quietly. **Tests.** `tests/unit/realtime/test_factory_handshake_budget.py`.

## BUG-142: the deck's "API this session" card shows 272k tokens as $0 — every model id of the live install was missing from the hand-maintained price table, and the table was the only source (HIGH, FIXED 2026-08-18)

**Symptom.** After 14 turns the card read `272.2k` tokens, `$0` cost, and a
per-model list of `vertex-live 10× $0` and `gemini-3.7-flash 4× $0`. The log
carried `No pricing entry for model 'gemini-3.7-flash' — 18117 in / 167 out
tokens recorded as $0.00` on every delegated turn.

**Root.** Three things, all shipping "$0" honestly and uselessly.
(1) `jarvis/brain/cost.py::calculate_cost_usd` priced ONLY from
`PRICING_USD_PER_MTOK`, a static dict last extended in the 2026-07-28 audit —
`gemini-3.7-flash` (the tool/CU model on the Vertex card) was not in it, and
neither was `gemini-live-2.5-flash-native-audio` (`VertexLiveProvider.
default_model`). Same class as the 1.87M deepseek tokens that went unbilled
in July: the table can never know the next generation, and every unknown
model reads as free. (2) `RealtimeVoiceSession` set `_active_model` to the
card's pin — an EMPTY string when the card pins nothing — so `_publish_live_
usage` priced `""` and `BrainTurnCompleted` carried no model; the deck keyed
the row on the provider name, hence "vertex-live". (3) The Realtime session
objects never told the orchestrator which id they had actually connected.
Along the way the table's own rows for `gemini-2.5-flash` ($0.075/$0.30, a
2025 preview rate) and `gemini-3.6-flash` ($1.50/$7.50) had drifted from the
vendor's price page ($0.30/$2.50 and $0.75/$3.75).

**Fix.** `resolve_rates` in `jarvis/brain/cost.py` now has three sources:
the static table; the provider feed that `jarvis/brain/model_catalog.py`
already downloads (OpenRouter publishes `pricing.prompt/completion` for every
model it routes — the catalog now carries it as `ModelInfo.pricing` and
round-trips it through `model_catalog_cache.json`); and $0 with a warning. A
`vendor/model` id (an aggregator id) is priced from the feed FIRST, because
that is what the aggregator bills; an origin id trusts the hand-verified table
and falls back to the feed's entry of the same model name
(`gemini-3.7-flash` → `google/gemini-3.7-flash`, `.`/`-` insensitive,
`:batch`/`:free` variants never shadow the plain model). `ensure_pricing_for`
— awaited by the manager's post-call hook and by `_publish_live_usage` —
refreshes the feed ONCE per unknown model per process (3 s cap, never under
the airgapped profile, never for a feed younger than 10 min), so a new
generation is priced from its first turn. The catalog is a process-wide
singleton now (`shared_catalog()`), because `_save_cache` writes the whole
file from memory and a second instance would have overwritten the first
one's refresh. The Realtime session objects expose `model` (the id the
socket really opened with), and `_active_model` is pin → session.model →
`provider.default_model`. Table rows added/corrected against
ai.google.dev/gemini-api/docs/pricing and the OpenRouter feed on 2026-08-18.

**Class.** A hand-maintained lookup table as the ONLY source for a value the
world keeps adding to. It is right on the day it is written and wrong on the
day the next model ships — and "unknown → 0" is the worst possible default
for money, because $0 looks like a fact, not like a gap. The fix is not a
bigger table; it is a live source with the table as offline fallback, plus a
turn that pauses once to learn a price rather than shipping a wrong number.
Second shape, again (BUG-139): a value derived in one place (the model the
socket opened) that another place re-derived from a weaker signal (the
card's pin) — and the weaker one was empty.

**Verification.** `resolve_rates("gemini-3.7-flash") == (0.75, 3.75)`;
`resolve_rates("gemini-live-2.5-flash-native-audio") == (0.5, 2.0)` plus
audio rates; a live OpenRouter feed pull confirms `google/gemini-3.7-flash`
at 0.375/1.875. Needs an app restart to take effect on the deck.

**Tests.** `tests/unit/brain/test_cost_feed_pricing.py` (feed resolution,
vendor-first precedence, variant suffixes, dot/dash spellings, mtime reload,
one-refresh-per-model, airgapped, timeout, never raises);
`tests/unit/brain/test_model_catalog.py` (pricing parse + cache round-trip);
`tests/unit/realtime/test_session.py::test_open_reports_the_model_the_socket_
really_opened_with`; `test_live_usage_metering.py` (Vertex Live default
priced); `test_gemini_live.py` / `test_openai_realtime.py` (`session.model`).
The root `tests/conftest.py` redirects the feed to `tmp_path` per test so no
suite reaches the network or the developer's real cache file.

## BUG-143: the same delegate reply is spoken twice — "Ich habe work geöffnet: T eins." then "…: T1." — the server-VAD transport answers a second boundary for one request and the session plays it as a phantom turn (HIGH, FIXED 2026-08-18)

**Symptom.** Realtime voice session `7b20e182` (2026-08-18 14:24, Vertex Live,
delegate tool mode). Turn 4 ("open a new workspace … a terminal") was answered
"Ich habe work geöffnet: T eins." — and 16 ms after that turn's boundary the
provider spoke "Ich habe work geöffnet: T1." again. Turn 6 repeated the
whole 144-character terminal status verbatim. The recorder wrote turns 5 and
7 with EMPTY user text and their own `AudioOutFirst`/`realtime_usage` events
(69 and 208 output tokens): two separate provider generations, not a
transcript artifact and not the surface TTS. Same shape at 15:58 (session
`805bbee2`): a phantom turn re-rendered a delivered confirmation prompt and
tripped the surface-fallback stale-readback guard, which then SPOKE its
clarify line ("Das hatte ich gerade schon beantwortet …") to a user who had
said nothing.

**Reconstruction (recorder + flight recorder + desktop log).**

1. In both doubled turns the user kept talking after Gemini's VAD had closed
   the input turn (`holding the dispatch — the microphone still carries the
   user's voice`; `provider committed a boundary while the user is still
   audibly speaking`). The session correctly merged the tail into ONE turn,
   ran the action once, and injected the trusted result once
   (`_delegate_result_prompt` via `send_text`).
2. On the server that one turn carried TWO turn-ending inputs: the injected
   text (which closes the open audio activity — the behaviour the 2026-08-13
   11:20 forensic in `deliver_announcement` recorded when an injected result
   "closed the sentence"), and the
   server's own silence detection closing that activity again moments later.
   Gemini answers each boundary with its own generation, serially. Every
   generation after the injected result re-renders that result, because the
   rendering order is the newest instruction in context — hence "T eins" and
   then "T1", two independent renderings of the same brain reply.
3. `_run_deterministic_delegate` → `_await_provider_response_boundary`
   injects immediately when `_drop_provider_output_until_new_response` is set
   ("the competing native response was already retired") — but on this
   transport `interrupt()` is a no-op (Gemini has no response cancel), so
   nothing was retired server-side. The injection races the VAD end.
4. `_ensure_turn_started` opens a turn for ANY provider output with no open
   turn. Nothing said "a generation that begins right after a rendered
   readback with no new user input is stale", so the second rendering was
   played and recorded as a user-less turn. Turns 0 and 3 of the same call
   did not double: their results were injected 2.5–5 s after the last voiced
   frame, by which time the server had already answered the trailing speech
   (natively, withheld). Only a result injected within ~1 s of the tail races
   it.

**Fix (`jarvis/realtime/session.py`, stale-generation guard).** When a turn
closes on a provider `turn_complete` after the provider rendered a text
Jarvis injected — a delegate result (`delivery_started`, provider audio was
heard, no surface fallback, no scrub cancel) or an out-of-band readback
(`_external_update`) — `_arm_stale_generation_guard` stamps the moment
(automatic-response transports only: a manual-response transport generates
nothing Jarvis did not ask for; capability, never a provider name, AP-21).
Every audio/transcript event is judged once at the top of the pump
(`_judge_stale_generation_event`): if the guard is armed, no turn is open, no
deliberate injection is pending, the microphone carried no voice since the
readback ended, no server speech edge was confirmed, and the 2.5 s window has
not run out, the generation is marked as being discarded and
`_output_withhold_reason` keeps every event of it out of the gate, the
surface, the recorder and the turn record until its own boundary. A tool
call from that generation (Gemini can answer the second boundary with a
`jarvis_action` or `end_call` instead of speech) is refused with an honest
result so the model can close the generation — never executed, never a turn;
a `handoff_requested` from it is ignored. The boundary is consumed
unconditionally: the guard only ever arms as a turn closes, so any turn open
at that moment was opened by a real transcript DURING the drop and keeps
waiting for its own answer instead of being closed as "empty" and re-routed
through the Brain chain. The discarded transcript is logged (bounded) and the
withhold stands down. The watch stays armed (BUG-149): a second unprompted
generation the moment the first phantom ends must still be dropped. Any
fresh evidence disarms it for good — a new input
transcript (`_disarm_stale_generation_guard` at the input-observed site),
local voice (`_last_voiced_input_monotonic`), an open turn,
`_external_update`, the window, a transport rebuild — and the drop itself is
bounded (`_STALE_GENERATION_DROP_MAX_S`, 20 s): a transport that loses the
one terminal frame releases the withhold with a warning instead of leaving
the call deaf. `_session_is_at_rest` and `deliver_announcement` treat a
discarded generation as "still busy" so no follow-up is injected into it. New
counter `stale_generations_dropped` on `RealtimeSessionPostmortem` (+ an info
finding in `realtime_forensics.py`).

**Not changed, on purpose.** The injection is still immediate: waiting for
the server's boundary would add the whole native generation (2–7 s live) of
dead air on every action turn, against the <2 s instant-ack target. The guard
is the deterministic net; the second generation still costs its tokens (the
counter makes that visible). The per-turn steering delta (`gemini_live.py`
`_flush_steering`, 37b4eca3) is a second text input on the same channel and
a candidate for the same double-boundary; the guard covers whichever input
triggers the extra generation.

**Class rule.** On a duplex transport that generates on its own VAD, "one
Jarvis turn" and "one server turn" are different units, and any text the
session injects is a turn-ending input of its own. After the session has
rendered what it asked for, a provider generation that arrives with no user
evidence behind it answers nothing — it must be refused as a unit, at the
pump, before any branch can open a turn for it. Play/record decisions belong
to the session, never to "the provider spoke, so someone must have asked".

**Guards.** `tests/unit/realtime/test_stale_generation_after_readback.py`
(the live failure: one rendering, one turn, no phantom; a stale generation's
`end_call` refused, not executed; a follow-up transcript landing mid-drop
keeps its own turn; and everything that must keep playing — after a new
transcript, after local microphone voice, after the window, a late result
Jarvis injected itself; the capability gate; every disarm reason; the drop
ceiling). `tests/unit/diagnostics/test_realtime_forensics.py` carries the
new finding.

## BUG-144: "Browse" in the Agentic IDE folder step does nothing — the system folder window opens BEHIND the maximised app because its invisible owner is not topmost, and a dropped folder is only searched for by name (HIGH, FIXED 2026-08-18)

**Symptom.** Clicking "Browse" in "Choose the project folder" showed
"Waiting… A folder window is open — pick a folder there" and nothing else;
five minutes later the log said `the folder dialog was left open and timed
out`. Dropping a folder from Explorer onto the step answered "Several folders
are called “shop” — pick the right one" and listed the real folder next
to two cache directories whose NAMES merely contained it.

**Root.** Two, plus a ranking bug. (1) `native_picker._WINDOWS_SCRIPT` opened
the Common Item Dialog owned by a WinForms form with `TopMost = $true` and
`Opacity = 0`. Windows silently drops `WS_EX_TOPMOST` from a layered window
that is completely transparent (measured: ex-style `0x90100`, no `0x8`; with
opacity 0.01 the flag stays), so the owner was NOT topmost, the dialog it owns
was not either, and it opened behind the maximised app window — the process
was alive and the dialog "visible" to `IsWindowVisible`, just covered. Nothing
in the log said so because nothing was wrong from the helper's point of view.
(2) A browser hands a page only the NAME of a dropped folder; the picker sent
that name to `/folders/resolve`, which searched for it. Inside the desktop
shell the host knows the real path (pywebview delivers it as
`pywebviewFullPath` to a Python `drop` handler on every OS) but nobody asked.
(3) `resolve_folder` treated every substring hit of `search_folders` as a
rival of the exact-name hit.

**Fix.** (1) The owner is a one-pixel borderless form at opacity 0.01,
topmost, parked in the middle of the screen the mouse is on (the dialog
centres itself over its owner) — and `choose_folder` hands the app's
foreground right to the helper first (`AllowSetForegroundWindow(ASFW_ANY)`,
Windows only, best effort) so the dialog opens focused, not merely visible.
(2) `jarvis/ui/native_drop.py` registers a pywebview DOM `drop` handler on
`window` (not `body`, whose serialisation would be the whole document) from
the `loaded` hook of the main and every detached window, and dispatches a
`jarvis-native-drop` DOM event carrying the resolved paths; the frontend's
`waitForNativeDrop()` (src/lib/nativeDrop.ts), armed synchronously in the
picker's drop handler, uses that path and falls back to the name search after
2 s or at once outside the shell. Verified end-to-end for a dropped FOLDER on
WebView2 (`CoreWebView2File.Path` returns the directory path); macOS/Linux
ride pywebview's own implementation, unverified from this machine
(`docs/os-parity.md` P-34). (3) One exact-name match resolves outright;
several exact matches are offered without the look-alikes.

**Class.** A window opened by a background helper is invisible unless it is
topmost AND the topmost flag actually took (fully transparent layered windows
lose it); a fully transparent owner is a bug that reads as "the button does
nothing". Browser drops carry names, not paths — inside the shell, ask the
host. A search hit that merely CONTAINS the wanted name is not a rival for the
exact one.

**Regression tests.** `tests/unit/agentic_ide/test_native_picker.py`
(`test_the_windows_owner_window_is_never_fully_transparent`, foreground
hand-off is a no-op elsewhere); `tests/unit/ui/test_native_drop.py`;
`tests/unit/ui/test_desktop_detached_windows.py::test_every_window_load_rewires_the_drop_path_bridge`;
`tests/unit/web/test_agentic_ide_resolve_folder.py`; frontend
`src/lib/nativeDrop.test.ts` and `FolderPicker.test.tsx` ("uses the real path
the desktop shell reports").

## BUG-145: a spoken "T2, do a deep dive on …" reaches the pane 26 s after the sentence ends — the brief writer's `auto` order put a coding CLI's cold process start on the voice turn's critical path, and its insurance was tuned for that CLI alone (HIGH, FIXED 2026-08-18)

**Symptom.** Realtime session `63617599` (2026-08-18 16:10, Vertex Live,
delegate tool mode). The user said "Kannst du bitte Terminal T2 … einen Deep
Dive machen …" and then watched T2 for 26 s before the brief landed
(`Agentic IDE prompt -> T2` at 16:10:50.288; the transcript closed at
16:10:24.15). First audio came at 27.3 s (`first_final_to_first_audio_ms
27338`). The session recorder holds the whole call as 53.3 s with one
sentence in and "Ist bei T2." out. The user's own reading was "my computer is <!-- i18n-allow: quoted voice output -->
lagging".

**Reconstruction (flight recorder + desktop log).**

1. +0.0 s transcript final; +0.5 s the routing decision blocked the event
   loop for 541 ms (`realtime event loop stalled 541 ms` — a stall of
   500–880 ms is logged at this point of every session, separate issue).
2. +1.1 s → +3.7 s: `AgenticIdeComposeProgress start → thinking`. The writer
   probe (`claude status` + `resolve_subscription_brain: writing on
   claude-cli`) took 2.6 s.
3. +3.7 s → +22.7 s: `claude-cli: spawning print-mode turn (prompt=693
   chars)` … `claude-cli turn ok: 1248 chars in 18.9s`. Nearly all of it was
   the SPAWN, not the writing: an EMPTY answer from the same CLI (the fact
   extractor's `2 chars` turns) took 17.2 s and 18.9 s in the same minute,
   against 5.2–6.2 s at 15:36 and 9–13 s at 16:00. The cold start had
   tripled within an hour, under a load the log shows plainly: two Claude
   Code TUIs had just been spawned in T1/T2 (16:09:38), Gemini Live and the
   wake detectors were running, 117 microphone frames were dropped in the
   session, and right after it three more CLIs (claude, gemini, codex) were
   spawned concurrently for fact extraction.
4. +22.7 s → +26.1 s: the paste was typed; `_await_arrival` polled 3.0 s for
   the text to show on T2's input line and never saw it (`never saw the
   prompt reach T2 — reporting it as unconfirmed`); Enter; delivered.
5. +26.1 s "Ist bei T2." — twice (BUG-143, fixed the same day). No instant <!-- i18n-allow: quoted voice output -->
   ack was spoken at all: unlike the 15:35 and 15:57 sessions there is no
   `delegate bridge: … line requested` line for this call, and in those two
   sessions the composed line was `dropped non-conforming` — the ack shipped
   2026-08-17 (ADR-0033) has still not produced audio in a live call. Open.

**Root.** Two decisions that were each right for the writer they were
measured against and wrong together on the voice path.
(1) `jarvis/agentic_ide/writer.py`: `auto` preferred a connected coding
subscription because it is work the user has already paid for, and only
then crossed to the API tier. A subscription CLI pays a cold process start
per brief — 10–12 s in the 2026-07-26 measurement that raised the compose
budget to 90 s, 16–25 s end to end for a lean brief on 2026-08-12, 19 s
today — and that start-up cost scales with machine load exactly when the
user is doing the most (spawning agents). The user's pinned Tool Model on
the same box writes the same brief in ~5 s warm (measured 2026-08-18: 5.0 s
and 4.8 s for the identical instruction; the deep tier 7–8 s for a plain
rewrite per the composer's own notes).
(2) `jarvis/agentic_ide/prompt_composer.py`: the hedge — a second writer
started alongside a stalled first — waited a fixed 30 s for a subscription
writer and 20 s for an API writer, each figure the healthy p90 of ONE writer
on ONE day. Neither knows that a Tool Model normally answers in 5 s, so a
hung fast writer got 20 s of silence before insurance was allowed to start.
And when the hedge itself died (on the dev box the `api` rung resolves to a
keyless OpenRouter — `JARVIS__BRAIN__PRIMARY=openrouter` sits in the shell
environment and overrides `jarvis.toml`'s `vertex`; measured: "No OpenRouter
API key found" 5 ms after the hedge started), no third writer was allowed —
"at most once per brief" — and the brief sat out the remaining budget behind
the stalled primary although a signed-in CLI was idle: measured 90.05 s to
the deterministic fallback.

**Fix.**
* `writer.py`: ONE order for `auto` and for the rescue, `_AUTO_ORDER =
  ("tool_model", "api", "subscription")` — the writer that answers soonest
  without a quality demotion. The Tool Model leads because it is, in the
  Tool Model tab's own words, the model that "routes spoken commands and
  starts background agents"; direct API calls have no process to cold-start;
  the subscription writes under `auto` only when nothing API-side qualifies,
  so a downloader whose ONLY credential is a coding CLI still gets a brief,
  and a user who wants the plan spent on purpose pins `subscription`. Pins
  are untouched. Under `auto` an unset Tool Model is the ordinary case and no
  longer logs as a broken pin. Config description and the settings card's
  "Automatic" label say the new order.
* `prompt_composer.py`: `HEDGE_AFTER_S` / `HEDGE_AFTER_API_S` are CEILINGS
  now. `_hedge_after_s(source)` derives the threshold from the writer's own
  recent valid briefs in this process (2.5 × their median, never below a 6 s
  floor, never above the family ceiling, keyed by full source so no model
  inherits another's clock, in-memory only because a CLI's spawn cost
  tripled within an hour). Without history the ceilings apply unchanged. A
  hedge that dies or writes a defect while the primary still stalls is
  replaced by the next rung at once — still one extra writer at a time,
  every rung tried at most once.

**Class.** A cost-first default on a latency-critical path, and a fixed
"suspiciously slow" number measured against one writer and applied to
every writer. Same family as BUG-140 (a per-call cold cost paid on the
critical path) and the 2026-08-12 hedge (a serial wait that had to be
measured to be seen). The general rule: on the voice path the first
question is "who answers soonest", cost and quality are constraints on that
answer, not the other way round; and any threshold that says "slow" must be
relative to what THIS writer normally does.

**Verification.** Live on the dev box, same instruction as 16:10, through
`prompt_composer.compose` with the real config: `tool_model:vertex`
resolves in 0.00 s warm; the brief lands in 5.0 s and 4.8 s (runs 2 and 3 of
one process). Honest caveats: run 1 of a fresh process took 56 s — the raw
google-genai SDK against Vertex `global` answered a bare "Say OK." in 49 s,
22 s, 9.9 s and 5.7 s in the same minute (17:25), so the provider had a slow
spell during the measurement; that is the provider, not the writer order,
and the router (same model) suffers it equally. The hedge history recorded
`[52.8, 5.0, 4.8]` and would insure the next brief at 12.5 s instead of 20 s.
Not yet observed in a live voice call — needs the app restarted.

**Tests.** `tests/unit/agentic_ide/test_writer_resolution.py` (auto prefers
the Tool Model, then the API tier, reaches the subscription last, never
probes a CLI while a faster writer qualifies, an unset Tool Model logs no
broken pin, a raising CLI probe degrades); `test_writer_rescue.py` (a dead
Tool Model crosses to the API tier before the CLI, the subscription is the
last rescue, first choice and rescue share ONE order); `test_writer_cache.py`
(cache tests pinned to the subscription rung explicitly);
`test_composer_hedge.py` (ceilings without history, a fast writer's history
pulls insurance forward, the floor, never above the ceiling, per-source
clocks, only valid briefs recorded, bounded history, the loop teaches the
threshold, a defective brief teaches nothing, history tightens the live
hedge end to end, a dead hedge is replaced by the next rung, a healthy hedge
recruits nobody else); `conftest.py` starts every test with an empty writer
clock.

**Open, seen while looking.** (a) The instant ack (ADR-0033) has produced no
audio in any of the three sessions this afternoon — needs one live call at
debug level. (b) The 500–880 ms event-loop stall at every routing decision.
(c) `JARVIS__BRAIN__PRIMARY=openrouter` / `JARVIS__TTS__PROVIDER=
openrouter-tts` in the maintainer's shell environment override `jarvis.toml`
and point the quality tier, the fact extractor ("Verdichter brain-call
failed: No OpenRouter API key") and the TTS default at a provider without a
key. (d) The paste's 3 s arrival check did not see the text on T2 — one
sample, cause unknown (slow render under load vs. a placeholder shape the
detector does not know).

## BUG-146: every pane of an ordinary six-terminal workspace is covered by "56 columns — Claude Code needs about 60 · Open it · Show it anyway", and the wizard demands "Open it anyway" from six terminals up — two guards built against a 13-column repaint bug fired at widths that work fine (HIGH, FIXED 2026-08-18)

**Symptom.** Six Claude Code panes in a 3×2 grid on a 1 920 px window at
text size 13: each measured 56 columns and each showed the card instead of
its terminal, so the user clicked "Show it anyway" six times to see agents
that were running perfectly well. In the wizard, choosing six terminals put
up a red "too narrow for an agent to draw in, so they open as status cards"
warning and blocked the next step behind "Open it anyway". Reported by the
maintainer as being patronised: "users get nagged far too early".

**Root.** `WORKABLE_COLS = 60` was written as the width below which "both
installed CLIs stop laying out a usable frame" (2026-08-09) and then used as a
GATE in three places: `PaneTooNarrowCard` in `AgenticTerminal.tsx` hid the
terminal and held its columns below it (built 2026-08-13 against a real bug —
at ~13 columns a coding CLI's relative repaint wipes its own rows and panes
come back blank); `WorkspaceLauncher.tsx` treated a measured per-pane width
below it as "crowded" and blocked with the same button that
`CROWDED_TERMINAL_COUNT = 20` used; `WorkspaceShape.tsx` said the panes would
open as status cards. The threshold was three to four times higher than the
width the bug was measured at, so the guards fired on every ordinary
multi-column layout, and each of them made the user CONFIRM something they
had just chosen.

**Fix.** The pane no longer argues about its width: `PaneTooNarrowCard`, the
column hold (`heldColsRef` / `narrowOverride`) and the older `PaneWidthNotice`
row are gone, and the terminal is exactly as wide as its tile, always shown
(only the absurd `MIN_REAL_COLS = 10` is still refused). The wizard SAYS
instead of asking: `CROWDED_TERMINAL_COUNT` is 10 and `WORKABLE_COLS` is 40,
both are ADVICE — one muted sentence with the count, the measured columns
and how many fit comfortably at the reader's text size — with no button and
no held-back step (`crowded.accept` / `crowded.accepted` removed from all
three locales; wording rewritten in en/de/es). The `activity` /
`activitySince` / `worked` pane props existed only for the card and are gone
with it.

**Trade-off, stated.** The 2026-08-13 blank-pane failure at ~13 columns is
no longer prevented by the pane; it is only ANNOUNCED by the wizard
beforehand. Reaching it takes an extreme layout (≈ 24+ panes at text size 13
on a 1 920 px window, or 12+ at size 20); the maintainer's rule is that this
is the user's call, and a pane they can maximize beats a card they must click
away.

**Class.** A guard tuned far above the failure it guards against becomes the
failure: every "confirm anyway" the user meets on an ordinary path is a bug
report waiting to happen. Thresholds that gate must be measured at the point
things actually break, and even then a warning that only informs is usually
right where a warning that blocks is wrong.

**Regression tests.** `AgenticTerminal.test.tsx` ("follows the tile down to
a width its agent cannot draw a frame in", "shows the terminal at the tile's
width, however narrow, and says nothing"); `WorkspaceLauncher.crowded.test.tsx`
("says so from ten terminals up, and lets the wizard carry on", "opens
exactly the count that was asked for" with 30 panes and no confirmation);
`layout.test.ts` (the constants' arithmetic).

## BUG-147: "erstell mir einen neuen Skill … mit YouTube Music" — the music skill captured the authoring request, the brain had no tool to write a skill, and the answer was "that did not work" (HIGH, FIXED 2026-08-18) <!-- i18n-allow: quoted voice request -->

**Symptom.** Realtime voice session `a62cbc4d` (2026-08-18 17:51, Vertex Live,
delegate tool mode). The user asked, in German, for a new "morning routine"
skill: every morning at six read the new mail, the Linear tickets ("Lenya
Tickets" in the transcript) and the day's calendar, then play an 80s classic
on YouTube Music, a different one each time. Jarvis said "I'm still on it"
after 9 s and, 90 s later, the spoken equivalent of "that did not work just
now". No skill existed afterwards.

**Reconstruction (desktop log + flight recorder).**

1. `SkillMatchEvaluated`: source `trigger`, band `fire`, winner
   `plugin-youtube_music`, candidates `[["plugin-youtube_music", 1.0]]`. The
   music connector's trigger is brand-only and un-anchored
   (`(youtube ?music|yt ?music|ytmusic)`), so it matches the brand ANYWHERE —
   including inside the description of a skill the user wants built. The
   `skill-creator` builtin's own trigger knew only the German infinitive
   ("… Skill erstellen"); the user said the conjugated "… Skill erstellst"
   after the noun, and the regex missed.
2. `Evidence gate stood down: turn is owned by matched skill
   plugin-youtube_music`, `force-spawn skipped: utterance matches an installed
   skill` — the music skill's instructions were injected inline, `run-skill`
   was dropped.
3. The model understood the request anyway and looked for a way to write a
   skill: `ActionExecuted cli_jarvisctl "jarvisctl --help"` (4.1 s),
   `"jarvisctl skills --help"` (1.6 s), `"jarvisctl skills draft --help"`
   (1.6 s) — three rounds of reading help text, each preceded by a slow
   provider round (Vertex TTFT 2–50 s that afternoon), then
   `tool_use_loop: 45.0s deadline reached after 53.6s / 3 round(s) — forcing
   a final tool-less answer round`. There was NO router tool for creating a
   skill: `spawn-skill-author` had been documented "deliberately unreachable"
   since 2026-07-25 (it needed a runner the entry-point loader cannot supply),
   and the live authoring path was the UI dialog plus a two-step
   `jarvisctl skills draft` → `commit` with JSON piped between them.
4. Even that path would not have produced what the user asked for: the
   creator's single brain call ran on the BrainManager's active provider —
   on this box `openrouter`, pinned by a stale shell variable and unkeyed —
   and its fallback is a deterministic skeleton that pastes the intent 1:1
   into `description` and a body reading "1. …", with no schedule trigger
   and no step naming a tool.

**Root cause.** Three structural gaps: (a) the trigger channel had no notion
that a request to CREATE a skill makes every service named inside it CONTENT,
not a command; (b) the brain had no authoring tool at all; (c) the authoring
service was one provider deep and its degradation was a template.

**Fix.**
- `jarvis/skills/authoring_request.py` — deterministic resolver ahead of the
  trigger channel (Channel 0.5 in `BrainManager._match_skill_for_turn`): word
  "skill" + a creation shape (indefinite/new article, or a creation verb in
  ANY conjugation right before/after the noun; DE/EN/ES) + not an information
  question. Resolves to the active `skill-creator` builtin with trigger-grade
  rights, or to NO skill when it is disabled — never to a connector named
  inside the request. Force-spawn and the evidence gate stand down on such a
  turn (`_skill_authoring_turn`). New veto `authoring_request` in
  `jarvis/skills/guards.py`.
- `create-skill` router tool (`jarvis/plugins/tool/create_skill.py`, in
  `ROUTER_TOOLS`, monitor): ONE bounded call — live tool inventory + installed
  skills → the brain ladder (active provider → Tool Model → quality tier →
  frontier chain, crossing on a dead rung) → a complete draft (name,
  description, spoken trigger regex, schedule cron when a time was named,
  numbered steps naming the connectors that exist, spoken answer format) →
  committed as `state: draft` (AP-15). Without an authoring brain it fails
  honestly and writes nothing. Same `SkillCreatorService.author()` behind
  `POST /api/skills/creator/author` and `jarvis skills create`.
- `SkillCreatorService`: provider ladder, tool inventory in the prompt, the
  Jarvis skill contract in the system prompt, trigger normalisation
  (overbroad/invalid voice patterns and bad crons dropped, the caller's cron
  hint added when the model forgot it, `requires_tools` filtered to real
  names), name-collision suffixing.
- `skill-creator` builtin: short instruction card ("restate, call
  create-skill once, report"); trigger regex accepts conjugated verbs;
  Anthropic's guide moved to `references/anthropic-skill-creator.md`.

**Measured.** With the fix, the exact transcript resolves to `skill-creator`
(the music skill no longer captures it), and a live probe of the authoring
prompt on this box's Tool Model (Vertex) produced a complete `Morgenroutine`
skill in 33 s: `cron: 0 6 * * *`, voice trigger
`(morgenroutine|morgen routine|…)`, four steps naming `gmail`,
`linear/list_issues`, `google_calendar`, `youtube_music`, "Lenya Tickets"
mapped to Linear under `assumptions`, spoken answer format, `state: draft`.

**Regression tests.** `tests/unit/skills/test_authoring_request.py` (the live
transcript + hard negatives), `tests/unit/brain/test_skill_authoring_turn.py`,
`tests/unit/plugins/test_create_skill_tool.py`,
`tests/unit/skills/test_creator_service.py` (ladder crossover, inventory in
the prompt, `author()` never commits a skeleton),
`tests/unit/ui/web/test_skills_routes.py`,
`tests/unit/cli_ctl/test_commands_wave23.py`,
`tests/fixtures/skill_routing/golden.yaml` (forensic positive).

**Generalised the same day** (maintainer: "why not automatically — this
applies to many other things"). The class, not the instance: (1) the meta
resolver covers every spoken synonym of "a skill" (routine, Morgenroutine,
Automatisierung, workflow, Ablauf; ES rutina/automatización/flujo) and a
second kind, *lifecycle* — "deaktiviere / lösch / zeig den Skill X" lets no <!-- i18n-allow: quoted voice requests -->
brand skill capture, so "deaktiviere den YouTube-Music-Skill" no longer plays
music; (2) the skill lifecycle is in the Command Registry (`skills-list` over
the new lean `GET /api/skills/brief`, `skill-enable`, `skill-disable`,
`skill-delete`) — first-class tools instead of CLI discovery; (3) the
`cli_jarvisctl` tool description carries the whole command tree
(`jarvis/cli_ctl/command_index.py`, parity-tested against the live Typer
app), which closes the "`--help` rounds" class for every CLI group, not just
skills. Guards: `tests/unit/cli_ctl/test_command_index_parity.py`,
`tests/unit/clis/test_tool.py`, `tests/unit/commands/test_registry_parity.py`,
the lifecycle cases in `tests/unit/skills/test_authoring_request.py` and
`tests/unit/brain/test_skill_authoring_turn.py`.

## BUG-148: a Vertex Live readback is spoken twice, and three grounded answers in a row are never heard — the stale-generation guard was stamped before the speaker drained, and Vertex Live had no realtime-scoped emergency voice (HIGH, FIXED 2026-08-18)

**Symptom.** Realtime voice session `28cf8436` (2026-08-18 18:39, Vertex Live,
delegate tool mode, desktop). Turn 0 ("what music would you recommend") was
answered with a 7.6 s readback ("Für konzentriertes Arbeiten empfehle ich
… Lo Fi …") — and the moment it ended the same sentence was spoken again
("… Lo-Fi …", turn 1, EMPTY user text, its own 225 output tokens). Turn 2
("… bisschen langweilig, aber fokussieren") repeated the same way (4.6 s
readback, then turn 3 verbatim). Then turns 4, 5 and 6 ("… was Cooles von Ed
Sheeran", "Hallo. …", "Hallo, sprich mal mit mir.") each produced a grounded
Brain answer that was recorded in the transcript and NEVER heard: `provider
produced no audio for a grounded Brain result; using surface TTS fallback` →
`Realtime surface fallback has no realtime-scoped TTS for provider
'vertex-live' — keeping the turn text-only`. Reported by the maintainer as
"he spoke twice, and after a while I heard no output at all".

**Reconstruction (recorder + flight recorder + desktop log).**

1. The doubled turns are BUG-143's shape exactly — a second server generation
   re-rendering the injected result — and the postmortem confirms the
   BUG-143 guard was live (`stale_generations_dropped` present) yet counted
   ZERO drops. The guard was armed at the provider's `turn_complete` and
   judged with a 2.5 s window. On the desktop the surface boundary that
   follows the arm — `_complete_surface_turn` → `send_json(turn_complete)` →
   `RealtimePlayback.finish_turn()` — BLOCKS until the speaker queue has
   drained, and Gemini streams a reply faster than real time. So for any
   reply whose remaining playback exceeded 2.5 s the stamp was already
   expired when the phantom generation's first event was judged
   (`_stale_generation_guard_reason` → window elapsed → disarm), and the
   generation played as a user-less turn. BUG-143's live case had ~2 s
   replies and never crossed the window; the fix was verified against a
   test surface whose `send_json` returns immediately.
2. The silent turns: after the trusted result was delivered (tool response
   for the provider-requested turns 4/5, result text for the deterministic
   turn 6) the provider sent a `turn_complete` ~0.3 s later with no audio,
   no transcript and only the function-call's own output tokens metered.
   The session did what it should — the no-audio net fired the surface
   fallback — but `_REALTIME_SURFACE_TTS_FAMILY` mapped ONLY `gemini-live`
   to a TTS sibling. `vertex-live` had none, so the strict mode-separation
   rule (never the pipeline `[tts]` voice) left the turn text-only, on a
   host whose pipeline had a working Vertex TTS (`Vertex TTS client built`
   two log lines later). WHY the provider rendered nothing for those three
   deliveries could not be attributed: nothing recorded what was sent on the
   wire (result text vs. steering delta vs. tool response) or which
   generation a boundary closed.

**Fix.**

- `jarvis/realtime/session.py`: the guard is armed AFTER
  `_complete_surface_turn` returns, from a stamp taken then, and only if
  `_may_arm_stale_generation_guard(boundary_at)` holds — no turn opened, no
  server speech edge, and no local microphone voice since the provider
  boundary (a confirmed barge-in during the drain stamps
  `_last_voiced_input_monotonic` and stands the guard down before it arms).
  The window comment on `_STALE_GENERATION_WINDOW_S` now states that it runs
  from the SURFACE boundary. `_DelegateTurnState.delivered_at` plus INFO
  lines for "delegate result delivered via text / tool result / direct
  speech", "stale-generation guard armed", and the no-audio warning now
  naming "boundary N s after delivery" make the next incident readable.
- `jarvis/plugins/tts/__init__.py`: `vertex-live → vertex-tts`. The
  emergency voice for a Vertex Live session is `VertexTTS` — same Gemini
  voice catalogue, same one-take streaming profile — keyed through the
  Vertex realtime slots or, keyless, the Cloud project path
  (`vertex_credential_configured`, Application Default Credentials).
  Provider & mode parity: every realtime family that HAS a TTS sibling gets
  its emergency voice; `openai-realtime` still has none and stays text-only.
- `jarvis/plugins/realtime/gemini_live.py`: wire forensics, one INFO line
  per client text/tool input (`text input sent`, `tool response sent`,
  `delivered a per-turn steering delta`) and one per server generation
  boundary (`generation started N s after the last <kind> input`,
  `turn complete — audio=… transcript=… function_calls=… generation=…`,
  `interrupted`), so a doubled or missing readback is attributable to the
  input that provoked it from the log alone.

**Not changed, on purpose.** The provider's empty answer to a delivered
result is NOT second-guessed at the boundary: with the wire log in place the
next occurrence says whether that `turn_complete` was a genuine empty
generation or a late boundary of an earlier one, and only then is a change
to the no-audio net justified. Until then the fallback voice is audible on
every realtime family that has one.

**Class rule.** A time window measured at one boundary and consumed at
another is only as good as the code between them: any await between the
stamp and the judgement — here a surface call that drains a speaker — turns
"2.5 s after the reply ended" into "2.5 s after the provider stopped
sending". Stamp fail-open windows at the LAST boundary before they are read.
And a fallback map keyed by provider id is a parity surface: every family
that ships a sibling must be in it, or the honest-degradation path degrades
one family further than the others.

**Guards.** `tests/unit/realtime/test_stale_generation_after_readback.py`
(`test_second_generation_is_discarded_even_after_a_long_speaker_drain` — a
`send_json` that blocks on `turn_complete` longer than the window, phantom
still discarded; `test_voice_during_the_speaker_drain_keeps_the_next_answer`
— a barge-in during the drain never arms the guard);
`tests/unit/plugins/tts/test_realtime_surface_tts.py`
(`test_vertex_live_builds_vertex_tts_with_session_voice`,
`test_vertex_live_builds_on_the_project_path_without_a_key`,
`test_vertex_live_without_any_credential_yields_no_surface_tts`).

## BUG-149: after a Vertex Live delegate readback the same confirmation is spoken twice — the stale-generation guard dropped the first phantom and then stood down (HIGH, FIXED 2026-08-19)

**Symptom.** Realtime voice session `e1ba9504` (2026-08-19 11:10, Vertex Live,
delegate tool mode, desktop). User: "Nova, kannst bitteschön eine Musik
abspielen vor dem Morgen?" YouTube Music played. The provider rendered
"Läuft für dich." — and ~500 ms later a second turn with EMPTY user text
spoke the truncated echo "Läuft für". Heard twice, logged twice. Reported
by the maintainer from the Mission-deck SAY lines. <!-- i18n-allow: quoted live voice output -->

**Reconstruction (flight recorder).** Turn 0: orchestrator → `youtube_music`
(21 s) → Vertex `gemini-3.7-flash` (42.7 k / 268, text_len=15) → Vertex Live
readback "Läuft für dich." Postmortem: `stale_generations_dropped=1`, <!-- i18n-allow: quoted live voice output -->
`delegate_delivery_duplicates_suppressed=0`, `turns_completed=2`. The
BUG-143/148 guard WAS live and DID drop one extra generation. Then
`_finish_stale_generation_drop` disarmed the watch on that phantom's
`turn_complete`. Vertex Live started another unprompted generation the
moment the withhold lifted (338 ms to first audio, 11.2 k / 60, no tools,
`user_text=""`). That one played.

**Fix (`jarvis/realtime/session.py::_finish_stale_generation_drop`).** The
withhold for one discarded generation still ends on its own boundary. The
watch stays armed for the rest of the 2.5 s window. Fresh evidence (user
speech, an open turn, a deliberate injection, the window) still disarms via
`_stale_generation_guard_reason`. Capability-gated the same way as
BUG-143: only `creates_responses_automatically` transports. Covers every
server-VAD family (gemini-live, vertex-live, and any later automatic
transport), not just the box that reproduced it.

**Class rule.** "Stand down when the thing you were waiting for arrives" is
wrong when the producer can emit another of the same thing. A fail-open
window is the bound; a single discarded event is not.

**Guards.** `tests/unit/realtime/test_stale_generation_after_readback.py`
(`test_second_unprompted_generation_after_a_dropped_one_is_also_discarded`
— two extras after the readback, one spoken reply, two drops;
`test_finishing_a_drop_keeps_the_watch_armed`; the original
`test_second_generation_after_readback_is_discarded_whole` now asserts the
watch stays armed).

## BUG-150: on a weak CPU the wake bar appears 1-3 s after "Hey Nova" and the phrase has to be repeated — the confirm re-decoded the whole ring after the tail, stalled its own intake until the fanout dropped audio, and three language models streamed stage 1 on every chunk (HIGH, FIXED 2026-08-19)

**Symptom.** Maintainer report from a second machine (i7-10510U, 4 cores /
15 W, Windows 11): "the wake word spawns delayed, maybe a second", "on the
main machine it never does". Live log of that box: `early_shown=0` in every
heartbeat, `oww_q=19` (the 0.6 s fanout queue FULL) at every candidate, five
"verify SUPPRESSED — re-score did not re-hear 'Hey Nova' (heard '')" inside
two minutes while the user was repeating the phrase, and an accepted wake
whose bar arrived ~1 s after the phrase.

**Measured on that box** (synthetic "Hey Nova", real de/en/es small models,
scratch timeline bench, idle CPU): stage-1 partial at −0.2 s before speech
end (fine); fixed 0.6 s confirm tail; then the from-scratch confirm
(grammar re-score + free decode over the 3 s ring) took **0.4-0.9 s** —
free decode alone 300-700 ms (0.14-0.2x realtime) — so the bar came at
**+1.0-1.6 s** after speech end, **+2.9 s** with four busy threads (the
box runs OBS, WebView, Discord, the dictation preview Whisper). Three
models' stage 1 cost 25-30 ms per 32 ms chunk during speech (~0.8x of one
core; p95 75 ms = 2.3x under load, i.e. the detector fell behind). During
the confirm the detect loop consumed nothing, so the 19-chunk queue filled
and dropped the OLDEST frames — holes inside the very audio the re-score
then judged ("heard ''"). On a desktop CPU the same path costs ~0.1-0.2 s
and is perceived as instant.

**Fixes (`jarvis/plugins/wake/vosk_kws_provider.py`, `jarvis/speech/pipeline.py`).**

1. *Streamed confirm.* A partial candidate's authoritative confirm now takes
   its grammar + free recognizers at PARTIAL time, feeds them the ring
   immediately and every tail chunk as it lands (`_StreamingVerify`: one
   single-worker executor per recognizer for the feeds — AP-24, never two
   callers on one native recognizer — and the two finals sequentially on
   the finishing thread, see BUG-151); at tail end only `FinalResult` + the
   unchanged judgement run (`_finish_streaming_verify` → `_judge_decoded`). Same
   audio, same recognizers, same thresholds — Kaldi's `AcceptWaveform`
   already slices any buffer into 0.2 s steps internally, so the chunking
   cannot change the decode. The from-scratch path remains for finals,
   latched retries, the sibling rescue and the early check, and is the
   fallback when streaming errors (never a blind accept). Bench
   (`scripts/vosk_wake_bench.py`, 64 files × 2 phrases): decision-identical
   to the baseline, file by file. Timeline on the weak box (quiet): bar at
   **+0.40-0.48 s** after speech end (idle and busy ×4), finish 20-70 ms;
   on the same box at 100 % load from other apps the finish still took
   only 0.2-0.3 s where the from-scratch confirm took 1.3 s.
2. *Intake budget as time, honoured only with batching.* The detector
   declares `coalesce_catchup_s = 1.0` and `intake_backlog_s = 3.0` (the
   ring length); `_wake_catchup_budget` turns both into capture blocks
   (the old `coalesce_catchup_chunks = 10` was chosen for 100 ms blocks and
   silently became 320 ms when the block shrank to 32 ms). A detector that
   cannot catch up keeps the generic 0.6 s queue — a deeper queue without
   batching is just a later wake.
3. *Stage-1 CPU budget.* Stage 1 demotes the LAST secondary model when its
   own decode cost proves it cannot keep up (`_STAGE1_SLOW_RT` = 2.0x on 4
   of the last 8 calls); the demoted model stays loaded for the verify and
   the sibling rescue; the primary is never demoted; the set is restored on
   the next `detect()` session so a transient load costs nothing lasting.
   Silence decodes (~0.03x) and a desktop CPU (~0.1x per model) never trip
   it.

**Not changed (checked).** The recognizer-stock replenish after a fire does
NOT collide with the realtime session build: the pipeline closes the
detector generator on the first yield, so the post-fire `_fresh_recs()`
never runs; the replenish happens at the start of the next wake session.
The analysis note claiming a burst during session assembly was wrong.

**Frontier.** A wake spoken in one breath with the command still loses the
~0.4 s between phrase end and the fire unless the early candidate armed the
preroll — the pipeline's confirmed-wake handoff seeds from the candidate
preroll only. The `es` model on a `de` box with an English-looking name is
pure cost until demotion; a vocabulary-aware model set is the cleaner fix.

**Class rule.** An always-on ear must never pay a multi-hundred-ms native
decode on the fire path that it could have paid during the wait it is
already sitting out — and it must never stop consuming its own intake
while it deliberates; bound the work, not the listening.

**Guards.** `tests/unit/plugins/wake/test_vosk_kws_provider.py`
(`test_partial_candidate_confirm_is_streamed_during_the_tail`,
`test_final_candidates_confirm_from_scratch`,
`test_streamed_confirm_error_falls_back_to_from_scratch`,
`test_positive_early_check_discards_the_streamed_confirm`,
`test_stage1_budget_demotes_the_last_secondary_and_never_the_primary`,
`test_detect_demotes_a_slow_secondary_but_keeps_it_for_the_rescue`,
`test_stage1_demotion_is_reset_per_detect_session`,
`test_provider_advertises_catchup_coalescing`);
`tests/unit/speech/test_wake_queue_coalesce.py`
(`test_catchup_budget_resolves_seconds_to_capture_blocks`,
`test_catchup_budget_defaults_without_the_capability`,
`test_catchup_budget_ignores_garbage_values`,
`test_vosk_provider_declares_both_budgets`).

## BUG-151: libvosk crashes the whole process under concurrent recognizer churn — the wake engine's parallel verify design hits it (HIGH, FIXED 2026-08-20)

**Symptom.** `scripts/vosk_wake_bench.py --phrase "Hey Jarvis" --language de`
dies with exit 139 ("Windows fatal exception: access violation", via
`python -X faulthandler`) part-way through the corpus — at HEAD before the
BUG-150 change 3 of 3 runs on the 4-core laptop that afternoon, 2 of 4 runs
after it (one baseline run in the morning passed). The crash thread is
always inside a native recognizer call: stage-1 `AcceptWaveform`
(`_grammar_hit`) or the stock prewarm's `FinalResult` (`_build_verify_rec`
in the replenish thread), with other threads decoding concurrently. The
same libvosk runs inside the desktop process, so in production this is an
app crash, not a log line. Plausibly the same memory-corruption class as
the macOS "malformed JSON from `Result()`" glitch already guarded by
`_parse_recognizer_json`.

**Minimal repro (`scripts/vosk_native_stress.py`, vosk 0.3.44, Windows
11, Python 3.13, one shared `Model`).** Five threads each looping
`KaldiRecognizer(model) → AcceptWaveform(0.1-0.3 s) → FinalResult() →
drop`, plus one thread streaming 3 s of noise through its own recognizer:
access violation after 5-50 s. Variants that SURVIVED 60-180 s: six
long-lived recognizers decoding + `FinalResult`/`Reset` concurrently (no
churn); create/free churn WITHOUT decoding (280 k recognizers) next to a
decoder; churn with `AcceptWaveform` only or `AcceptWaveform + PartialResult`
(~80 k); `FinalResult` without audio (165 k); every native call behind ONE
global lock. Variants that CRASHED: churn with `AcceptWaveform +
FinalResult` on 0.1 s silence / 0.3 s silence / 0.3 s noise; the same with
finals serialised among themselves; finals exclusive vs decodes (RW); finals
+ constructions exclusive (1 of 2 runs). So: many concurrent short
decode-and-finalise cycles are unsafe in this build, and no cheap partial
lock protocol found so far is reliably sufficient.

**Why the engine hits it.** Every candidate runs the early check
(grammar + free decode in two threads), the authoritative confirm (two
more), optionally N sibling rescues (two each, concurrently), the
competition pass, AND the replenish thread's prewarm
(`AcceptWaveform(0.3 s silence) + FinalResult + Reset`) — all short
decode-and-finalise cycles, exactly the repro pattern, on top of stage 1.
BUG-150 deliberately adds only ONE native thread (single-worker
`_StreamingVerify`), but does not reduce the pre-existing churn.

**Mitigation options (not done here).** (a) Serialise every native
recognizer call process-wide behind one lock — proven safe, but it
re-serialises the grammar/free pair and the sibling rescues (the 2026-07-10
spawn-latency win) and stalls stage 1 during every verify; with the
BUG-150 streamed confirm the fire-path cost of that is small, the early
check and rescues pay it. (b) Drop the prewarm's `FinalResult` (keep
`AcceptWaveform(silence) + Reset` — measure that the lazy-init saving
survives and decisions stay identical, per the 2026-07-11 parity method).
(c) Move all Vosk work into ONE dedicated native thread with a queue. (d)
Upgrade/patch libvosk. Whichever is chosen must be re-measured with the
repro script AND the bench (3/3 clean runs on the laptop) before it counts.

**Fix (2026-08-20).** Every native constructor and method now goes through
`jarvis/plugins/wake/vosk_native.py`: one process-wide lock, the protocol
that survived the repro. Python-level grammar/free submit still uses two
threads (the 2026-07-10 spawn-latency shape); the lock serialises the
native calls themselves. Test doubles are not wrapped, so the concurrent
barrier unit test still proves the submit shape.

**Guards.** `tests/unit/plugins/wake/test_vosk_native.py`. Native acceptance
remains `scripts/vosk_native_stress.py churn 180` (3 x clean) plus 3/3 clean
bench runs on the weak laptop — a crash test cannot run in CI without the
model.

## BUG-152: Gemini Live greeting cut off mid-sentence — a stale empty interrupt cancels the speaker drain (CRITICAL, FIXED 2026-08-19)

**Symptom (Windows live forensic, 15:59, `vertex-live` /
`gemini-live-2.5-flash-native-audio`).** The user said "Hey Hallo, was geht
ab?". The SAY panel showed the full reply ("Hallo! Mir geht es gut, und ich
bin bereit für deine nächsten Aufgaben.") but the voice stopped around
"nächsten". The session then sat silent until the user hung up. <!-- i18n-allow: quoted live German utterance and spoken reply under forensic analysis -->

**Timeline (`data/jarvis_desktop.log`).**

| Time | What happened |
|---|---|
| 15:59:07.878 | Steering text sent |
| 15:59:08.049 | Empty `interrupted` (audio=0) — deferred (RT-09) |
| 15:59:08.169 | Empty-turn re-ask sent |
| 15:59:08.331 | Second empty `interrupted` |
| 15:59:09.521 | First audio of the real 3.6 s / 71-char greeting |
| 15:59:10.188 | Provider `turn_complete` |
| 15:59:11.257 | Settle commits the stale interrupt ("silent for 1.0s") |
| 15:59:11.339 | `JARVIS_SPEAKING → LISTENING` via `tts_cancel` |

Desktop `_send_json(turn_complete)` awaits `playback.finish_turn()`, so the
pump is blocked on the remaining ~2 s of audio. The settle task treats that
expected provider silence as "the interrupt won" and cancels the drain.

**Root cause.** Three cooperating defects:

1. Gemini Live emits `interrupted` for our own `send_text` (steering, re-ask)
   even when nothing is playing. The session deferred that edge as if a
   reply were being spoken.
2. Every accepted provider event *refreshed* the 1 s silence timer, including
   the real reply's `turn_complete`. The timer then started from the
   boundary, exactly when the surface began draining.
3. `_clear` of the deferral lived in `_reset_turn_tracking`, which runs
   *after* the blocking `finish_turn`. Too late.

**Fix.** Ignore `interrupted` when no reply is in flight. Cancel the silence
backstop on continued production and on any `turn_complete`, and clear the
deferral at the *start* of `_complete_surface_turn` — before the desktop
drain. The 1 s commit remains only for a generation that dies at the edge
with no later boundary. Empty-turn re-ask silence is owned by that
watchdog, not the barge-in settle.

**Guards.**
`tests/unit/realtime/test_session_noise_interrupt.py`
(`test_empty_interrupt_before_a_reply_is_ignored`,
`test_completed_reply_survives_the_interrupt_silence_window`,
`test_stale_interrupt_cannot_cancel_speaker_drain`);
`tests/unit/realtime/test_session.py`
(`test_empty_turn_reask_reply_survives_speaker_drain`,
`test_empty_turn_reask_survives_the_interrupt_settle_window`).
Existing
`test_unconfirmed_interruption_is_committed_once_the_provider_stops` still
requires the backstop when the provider really stops.

**Class rule.** A provider VAD edge is not a barge-in, and provider silence
after a completed generation is not confirmation of that edge. The surface
drain of an already-rendered reply is not a window in which to cancel it.

**Parity.** Session-level, every realtime transport. Gemini Live is the
transport that emits the empty `interrupted` artifact; OpenAI's
`speech_started` stays instant. Desktop is the surface whose
`turn_complete` blocks on the speaker; browser/headless `send_json` is
non-blocking but the same cancel would still drop queued PCM.

**Same class, 16:07 the same afternoon.** "Was geht ab?" produced no spoken
greeting at all: the 1 s settle committed *before* first audio (1.14 s),
`_drop_provider_output_until_new_response` withheld the 2.9 s native
reply, and the user heard silence then the canned failure of BUG-153.
Guard: `test_empty_turn_reask_survives_the_interrupt_settle_window`. <!-- i18n-allow: quoted live German utterance under forensic analysis -->


## BUG-153: Vertex Live prefixes native tools with `default:` — every hybrid call answers "tools are not available" (HIGH, FIXED 2026-08-19)

**Symptom (Windows live forensic, 16:07, `vertex-live` /
`gemini-live-2.5-flash-native-audio`, hybrid tool mode).** After the
silent greeting of BUG-152 the user said the internet was broken. Jarvis
spoke "Das hat gerade nicht geklappt." Then, asked why: "Das System
meldet, dass die Tools nicht verfügbar sind. Ich kann keine Diagnose
machen, ohne sie zu starten." Tools were up; 110 native tools were
declared. <!-- i18n-allow: quoted live German utterances and spoken replies under forensic analysis -->

**Timeline (`data/jarvis_desktop.log`).**

| Time | What happened |
|---|---|
| 16:07:26.665 | `gemini-live: function call(s) default:run_shell` |
| 16:07:26.669 | tool response sent for `default:run_shell` |
| 16:07:26.674 | direct-tool turn without output; retry speech from the result |
| 16:07:27.377 | second `default:run_shell` |
| 16:07:27.566 | surface TTS fallback of the canned failure (30 chars) |

**Root cause.** Vertex Live groups `function_declarations` under an
unnamed Tool and reports calls as `{toolset}:{function}`
(`default:run_shell`). AI Studio and OpenAI send the bare declared name.
`RealtimeToolBridge.execute` looked up the prefixed name in
`_wire_to_name`, missed, and returned `"Tool is not available in this
session."` The live model read that error back. The original name must
still be echoed on `send_tool_result` or Vertex rejects the response.

**Fix.** `canonical_tool_wire_name` strips an identifier tool-set prefix
when the suffix is a legal wire name. Lookup tries the name as sent, then
the suffix, then the hashed form. Session special-cases (`jarvis_action`,
`end_call`) use the canonical name; the function response uses the
original.

**Guards.** `tests/unit/realtime/test_tools.py`
(`test_vertex_toolset_prefix_resolves_to_the_declared_tool`,
`test_vertex_prefix_on_a_hashed_wire_name_still_runs`);
`tests/unit/realtime/test_session.py`
(`test_vertex_prefixed_native_tool_call_echoes_the_original_wire_name`).

**Class rule.** A transport-specific tool-name spelling is not a missing
tool. Resolve at lookup; echo on the wire what the provider emitted.

**Parity.** Lookup is session/bridge-level, every realtime transport.
Only Vertex Live is known to emit the `default:` prefix; OpenAI and
Gemini AI Studio send the declared name and hit the exact-match path.


## BUG-154: hybrid live model says the action is done, then does nothing — user has to ask again (HIGH, FIXED 2026-08-19)

**Symptom (Windows live forensic, 17:13, `vertex-live` /
`gemini-live-2.5-flash-native-audio`, hybrid tool mode).** User: "Kannst du
bitte richtig coole Musik spielen?"  <!-- i18n-allow: live user utterance -->
Model asked what kind. User: "Egal einfach irgendwas aus den 2010ern."  <!-- i18n-allow: live follow-up -->
Model said "Alles klar, ich spiele dir eine Playlist mit Songs aus den 2010ern."  <!-- i18n-allow: live spoken false completion -->
— then silence. No music. The user had to say it a third time
("Ja, dann spiel es auch ab.") before `youtube_music` ran.  <!-- i18n-allow: live third ask -->
Same class on other actions: the voice claims the work is done, nothing
happened, repeating it once makes it work.

**Timeline (`data/jarvis_desktop.log`, session `c0f763c0`).**

| Time | What happened |
|---|---|
| 17:13:22 | Turn 1 complete — 1.8 s audio, 37 chars, **function_calls=0** (clarifying question; allowed) |
| 17:13:34 | Turn 2 complete — 3.7 s audio, 67 chars, **function_calls=0** (false completion; the bug) |
| 17:13:52 | Turn 3: native `youtube_music` starts |
| 17:13:55 | Empty-turn watchdog recovers through the Brain chain **over the in-flight tool** |
| 17:13:55 | `plugin-spotify` matched, plugin not connected, capture aborted — YouTube Music sibling never ran |

**Three stacked faults.**

1. *False completion is not a deferred promise.* The existing
   `has_deferred_action_claim` guard looks for "I'll check later". "Ich
   spiele dir eine Playlist …" looks like a delivered result (long tail),  <!-- i18n-allow: live spoken false completion -->
   so the turn was persisted as success. Prompt rules already forbid
   announcing without a function call; live 17:13 is proof they are not a
   correctness boundary.
2. *Skill capture vetoed the disconnected winner before rematch.*
   `_prefer_music_service` existed and would have swapped Spotify → YouTube
   Music. It ran *after* `VETO_PLUGIN_NOT_CONNECTED` returned None.
3. *The empty-turn watchdog ignored in-flight native tools.*
   `_executed_tool_names` is written only when execute *finishes*. A 28 s
   `youtube_music` call looked like silence, so Brain recovery raced it.

**Fix.**

- `has_false_completion_claim` (regex-only, de/en/es): first-person
  present/perfect action *for the user* ("ich spiele dir", "I'm playing  <!-- i18n-allow: matcher example -->
  you a", "te pongo una playlist"). `_recover_unbacked_action_claim` uses  <!-- i18n-allow: matcher example -->
  `has_unbacked_action_claim` (deferred OR false completion). The
  clarifying question of turn 1 does not match.
- `_match_skill_for_turn` rematches the music sibling *before* the
  disconnected veto, so Brain recovery of a music turn actually plays.
- `_native_tools_in_flight` is counted around native execute; the
  empty-turn re-ask watchdog and empty-turn recovery treat an in-flight
  call as evidence and stand down.

**Class rule.** A spoken outcome is not execution evidence. Detect both
shapes — "I'll do it later" *and* "I am doing it now" — and recover
through the orchestrator when no tool ran. An in-flight tool is already
the recovery.

**Parity.** Hybrid and delegate: the unbacked claim starts the
deterministic Brain dispatch (the 17:13 cell). Direct-tool mode and
classic `BrainManager.generate` keep the existing honest fallback
("I did not start an action…") when no tool ran — they do not
re-dispatch, same as deferred promises. Skill rematch is Brain-level, so
a later Brain pass on a music turn plays the connected service.
Execute-time music reroute (unnamed request → preferred / only connected
service) remains the net when the live model *does* call the wrong
sibling. Covers every realtime transport; Vertex Live is the forensic.

**Guards.** `tests/unit/brain/test_action_honesty.py`
(`test_false_completion_claims_are_detected`,
`test_questions_and_hobby_talk_are_not_false_completions`,
`test_honesty_fallback_is_not_itself_a_false_completion`,
`test_german_perfect_needs_the_beneficiary`);
`tests/unit/realtime/test_session.py`
(`test_false_completion_without_a_tool_dispatches_the_action`,
`test_clarifying_question_is_not_an_unbacked_action`,
`test_in_flight_native_tool_cancels_empty_turn_brain_recovery`);
`tests/unit/brain/test_paired_plugin_capture_veto.py`
(`test_capture_rematches_before_the_disconnected_veto`,
`test_match_skill_for_turn_swaps_disconnected_spotify`).


## BUG-155: "I'm still on it" during a realtime call speaks a different voice — native-tool progress and surface fallbacks default to Charon (HIGH, FIXED 2026-08-19)

**Symptom.** Realtime voice session `bc7e99c9` (2026-08-19 17:45, Vertex Live,
`gemini-live-2.5-flash-native-audio`, hybrid). The live replies were a
female native-audio voice. After the user confirmed a playlist start, a 13.7 s
`youtube_music` call ran and Jarvis said the German progress pool line
("still on it") in a different, male voice. The same flip hit the short
success and failure readbacks — every line that left the live socket.

**Reconstruction (`data/jarvis_desktop.log` + `voice_turns`).**

| Time | What spoke | Voice label |
|---|---|---|
| 17:45:19 | live native reply | *(empty — `_active_voice` was blank)* |
| 17:45:43 | surface TTS fallback of the short success | Charon @ vertex-tts |
| 17:46:20 | `native tool youtube_music still running after 3.0s; speaking the instant ack on the surface channel` | Charon @ vertex-tts |
| 17:46:37 | live native short "running" readback | *(empty; the progress line had already stamped Charon on the turn)* |
| 17:46:44 | surface TTS fallback of the canned failure | Charon @ vertex-tts |

`build_realtime_surface_tts` logged `same-family 'vertex-tts' (voice 'Charon')`
on the first fallback of the process.

**Root cause, two layers.**

1. *Vertex Live had no voice catalog.* `REALTIME_VOICES` / `REALTIME_MODELS`
   keyed only `gemini-live`. The Vertex card's picker was empty, so
   `[brain.providers.vertex-live].voice` was never written. The live adapter
   omitted `PrebuiltVoiceConfig` and Google picked its undocumented
   native-audio default (female on this box).
2. *Surface TTS had a different default.* `_active_voice` stayed `""`,
   `_surface_speech_message` sent no voice hint, and
   `build_realtime_surface_tts` hard-fell through to Charon (masculine).
   Native-tool acks cannot ride the live model (it is blocked on the
   function call, ADR-0034) so they *must* use that surface channel —
   and it was the wrong speaker.

Same class as BUG-089 / BUG-090 (a second voice mid-call) and the
rejected ADR-0033 alternative ("Surface TTS for the realtime ack — a
second voice mid-call is the exact BUG-090 failure"). The live-model
bridge path already keeps one voice; the native-tool / no-audio
surface path did not.

**Fix.**

- `REALTIME_VOICES["vertex-live"]` is the same 30-voice Gemini roster;
  `REALTIME_MODELS["vertex-live"]` leads with
  `gemini-live-2.5-flash-native-audio`. The Vertex card picker and
  preview work.
- `GeminiLiveProvider.default_voice = "Kore"` (inherited by Vertex Live).
  An empty card pin still sends `PrebuiltVoiceConfig` and fills
  `_active_voice`, so the live socket and the surface sibling name the
  same voice. A per-card pin still overrides it.
- `build_realtime_surface_tts(..., session_voice=)` takes the live
  session's voice first. `_surface_speech_message` already carries
  `_active_voice` as a synthesize hint; the builder default now matches
  that name instead of falling through to Charon.

**Class rule.** A progress / fallback line mid-call is not a second
assistant. If the live model cannot speak it, the surface sibling must
use the session's voice name — never a family default that disagrees
with the socket. A realtime provider without a voice catalog cannot
keep that promise.

**Parity.** Catalog + default + surface hint cover gemini-live and
vertex-live (the two families with a same-voice TTS sibling).
openai-realtime / local-realtime have no surface TTS sibling; their
progress lines stay on the live socket or stay text-only.

**Guards.** `tests/unit/brain/test_model_catalog.py`
(`test_every_realtime_card_has_a_voice_catalog`,
`test_vertex_live_models_lead_with_the_hardcoded_default`);
`tests/unit/web/test_realtime_provider_category.py`
(`test_get_realtime_options_vertex_live_shares_the_gemini_voice_roster`);
`tests/unit/realtime/test_session.py`
(`test_active_provider_selection_falls_back_to_adapter_default_voice`,
`test_progress_line_carries_the_active_voice_hint`);
`tests/unit/realtime/test_gemini_live.py`
(`test_open_session_pins_the_adapter_default_voice_when_unset`);
`tests/unit/plugins/tts/test_realtime_surface_tts.py`
(`test_session_voice_override_wins_over_empty_config`).


## BUG-156: "Welches Lied" while music plays answers "that did not work" — hybrid execute treated a now-playing read as smalltalk (HIGH, FIXED 2026-08-19) <!-- i18n-allow: quoted live German utterance under forensic analysis -->

**Symptom (Windows live forensic, 17:46, `vertex-live` /
`gemini-live-2.5-flash-native-audio`, hybrid tool mode).** Music was
playing. The user asked "Welches Lied". Jarvis spoke "Das hat gerade
nicht geklappt." Three seconds later the live model produced a 68-character
reply (the track name) and it was withheld because of the surface TTS
fallback. <!-- i18n-allow: quoted live German utterances and spoken replies under forensic analysis -->

**Timeline (`data/jarvis_desktop.log` + flight recorder, session
`bc7e99c9`).**

| Time | What happened |
|---|---|
| 17:46:38 | Previous turn said "Läuft." — Ed Sheeran *Perfect* was playing |
| 17:46:43 | User: "Welches Lied". Routing: orchestrator, `capability,connected_data` |
| 17:46:43 | Native `youtube_music` in 30 ms → `ActionDenied`: "nothing in the user's words asks for it" |
| 17:46:44 | Vertex Live interrupted; surface TTS of the 30-char canned failure |
| 17:46:47 | Live model audio 2.2 s / 38 chars, then another `youtube_music`, then 2.7 s / 68 chars — withheld after the fallback |

**Three stacked faults.**

1. *The shape guard used the static tool tier.* `youtube_music` is
   `monitor` because play mutates. `risk_tier_for_args(now_playing)` is
   `safe`. `_turn_shape_refusal` read only `descriptor.risk_tier`.
2. *Bare `plan_turn("Welches Lied")` had no reason.* The session planner
   saw a skill/capability. The execute guard calls `plan_turn` with no
   skill index and no context. `_CONNECTED_DOMAIN_RE` had gmail/calendar,
   not a now-playing question.
3. *The canned fallback hid the later correct reply.* Same Vertex empty
   generation + interrupt shape as BUG-152/153. The first fix is to let
   the read run so the grounded result is the track, not the denial.

**Fix.**

- Catalog descriptors carry `risk_tier_for_args`. The hybrid guard uses
  the per-call tier, so `now_playing` / empty args are `safe` and never
  shape-refused.
- A music *write* (play/pause/skip) still needs an action order —
  "Welches Lied" must not restart the player.
- Planner: service names (`spotify`, `youtube music`, `playlist`) join
  `_CONNECTED_DOMAIN_RE`. A dedicated now-playing matcher
  (`welches lied`, `what song is this`, `que cancion es`) adds
  `CONNECTED_DATA`. Bare `song`/`lied` stay out so "Which song won
  Eurovision?" remains a public fact. The matcher is also a
  non-public-evidence exception so a local model that requires
  public-fact grounding does not web-search the playing track.

**Class rule.** A read of connected state is not a side effect. Resolve
the risk tier per arguments at execute time. A now-playing question is
connected data, not smalltalk and not a public-song fact.

**Parity.** Session-level hybrid execute, every realtime transport
(`openai-realtime` / `gemini-live` / `vertex-live`). Direct and
delegate do not use this shape guard; they already reach `now_playing`
through the orchestrator. The planner change is shared, so classic chat
and the deterministic delegate get the same now-playing routing. Classic
pipeline and every OS.

**Guards.** `tests/unit/realtime/test_hybrid_tool_mode.py`
(`test_now_playing_question_runs_the_music_read`,
`test_now_playing_question_does_not_start_playback`);
`tests/unit/brain/test_turn_planner.py`
(`test_now_playing_questions_are_connected_data`,
`test_public_song_trivia_is_not_connected_music_state`,
`test_smalltalk_is_not_music_connected_data`);
`tests/unit/brain/test_factory_supervisor_gateway.py`
(`test_catalog_carries_the_per_args_risk_tier_hook`).


## BUG-157: asking for a song during a realtime call is aborted as "took too long" — the stall watchdog treats an in-flight native play as a dead transport (HIGH, FIXED 2026-08-19)

**Symptom (Windows live forensic, 18:17, `vertex-live` /
`gemini-live-2.5-flash-native-audio`, hybrid).** The user asked for a
modern 2026 track. Jarvis said it would take a moment, then 20 s later
spoke "Das hat zu lange gedauert, also habe ich abgebrochen." The song  <!-- i18n-allow: quoted live German reply under forensic analysis -->
never started. The UI error was
`RealtimeTurnStalled`: "The realtime provider produced nothing for 20s
on an open turn; the turn was closed locally."

**Timeline (`data/jarvis_desktop.log` + flight recorder, session
`128fbac6-d7ae-4fef-9810-4de3179f9889`).**

| Time | What happened |
|---|---|
| 18:17:22 | Wake `nova`. Handshake 10.4 s |
| 18:17:49 | Final: play a cool modern 2026 song. Native `youtube_music` `play` / `aktuelle Hits 2026` |
| 18:17:52 | Instant ack on the surface channel ("still a moment") |
| 18:17:53 | YouTube search HTTP 200 — the tool is still running |
| 18:18:09 | Stall watchdog: 20.3 s since the last provider event (the function call itself). `ActionExecuted` never published |
| 18:18:16 | Spoken timeout. User hangup |

Earlier the same evening, `youtube_music` play took 17.9 s and 21.2 s
and succeeded. 21 s is already longer than `_TURN_STALL_TIMEOUT_S`.

**Root cause.** BUG-154 taught the *empty-turn* recovery that
`_native_tools_in_flight` is evidence. The *stall* watchdog did not
learn it. `_turn_stall_is_excusable` allowed only delegate holds, user
speech, and provider audio. A native function call blocks the live
model (ADR-0034): Vertex Live emits nothing until `send_tool_result`.
The last `_note_turn_activity` was the `tool_call` event. Twenty
seconds later the watchdog closed the turn, spoke the timeout, and
left the still-running play with no turn to finish — the track is
cut off / never starts.

The surface instant-ack does not increment `_output_samples_sent`, so
it does not excuse the stall either.

**Fix.** `_turn_stall_is_excusable` (and a race guard in
`_recover_stalled_turn`) treat `_native_tools_in_flight` the same way
the empty-turn nets already do. The 20 s clock starts after the tool
returns, when the provider actually owes audio again.

**Class rule.** Provider silence during a native execute is the
function-call block, not a dead socket. An in-flight tool already owns
the turn. Do not abort the user's song (or any other native action) to
reopen the microphone.

**Parity.** Session-level stall watchdog, every realtime transport
(`openai-realtime` / `gemini-live` / `vertex-live`). Direct and
delegate already excuse a pending Brain turn via
`_turn_has_pending_delegate`. Classic pipeline has its own stall
guard and does not use this watchdog.

**Guards.** `tests/unit/realtime/test_stall_native_tool.py`
(`test_in_flight_native_tool_is_not_a_stalled_turn`).

## BUG-158: no skill can be started by voice in a realtime call — the live model is never told a single skill name (CRITICAL, FIXED 2026-08-20)

**Symptom (Windows live forensic, 10:52 and 11:01, `vertex-live` /
`gemini-live-2.5-flash-native-audio`, hybrid).** The user asked three
times, three ways, for the morning routine. Three different failures,
none of them "the skill ran":

| Time | Utterance | What Jarvis said | What actually happened |
|---|---|---|---|
| 10:52:04 | "Morning-Routine" | "\"Morning Routine\" ist gestartet." | `tool_calls: []` — **nothing ran, and the user was told it had** |
| 11:01:28 | "Morgenroutine" | "Was genau meinst du mit Morgenroutine?" | `ActionDenied run-skill` |
| 11:01:37 | "Skills-Morgenroutine" | "ich erreiche meinen Anbieter gerade nicht" | no provider fault occurred |
| 11:02:05 | "Okay, kannst du bitte mal ein Skill Morning Routine starten?" | "Den Skill \"Morning Routine\" habe ich nicht gefunden." | `ActionExecuted run-skill error="Unknown skill: Morning Routine"` |

<!-- i18n-allow: quoted live German utterances and replies under forensic analysis -->

`morning-routine` was installed, `validated`, switched on, and its own
voice-trigger regex matches every one of those four utterances.

**Root cause — five independent defects on one path.** Measured, not
inferred: `_session_instructions` built a 17 488-character prompt
containing the word "skill" **zero** times.

1. **No roster.** `render_available_skills_section` is called only from
   `jarvis/brain/manager.py`. The realtime session carried the
   `run-skill` tool and no catalogue, so the argument was always a
   guess from the user's words.
2. **Exact-key lookup.** `SkillRegistry.get` is a plain dict read.
   `"Morning Routine"` ≠ `"morning-routine"` → `Unknown skill`.
3. **Two naming conventions in one key space.** Builtins ship
   kebab-case slugs; `creator_service.author` wrote the model's Title
   Case display name into `name:`, and resolved collisions by
   appending `" 2"` — producing the registry key `Morning Routine 2`.
4. **A tie erased the skill reason.** The planner required
   `ranking.clear_winner` before adding `TurnReason.SKILL`. The
   authored near-duplicate put both morning routines at FIRE band, so
   installing the second skill **silently disabled routing for the
   first**. A/B measured: with the duplicate, `clear_winner=False` on
   every "Morgenroutine" variant; without it, `True`.
5. **The guard planned the same sentence differently.** The session
   passes `skill_index=` to `plan_turn`; `_tool_intent_refusal` called
   it bare. So the router routed a turn with `reasons=skill` and the
   execute-time guard refused the same turn for having no reason.

A sixth, structural: `_skill_directive` returns `""` for anything it
cannot inline, and `""` meant "no skill". Both installed morning
routines fail its conditions — `morning-routine` at 1656 chars over the
1500 cap and on the tool-word regex, `Morning Routine 2` as
`tool_backed` — so a FIRE match was dropped on the floor every time.

**Why no test caught it.** `tests/integration/test_skill_listing_in_prompt.py`
asserts the roster reaches the prompt — the *BrainManager* prompt. No
test ever asked the realtime instructions the same question.

**Fix.**
- `SkillRegistry.resolve()` — exact key, then directory slug, then
  normalized token tuple, then namespace-prefix drop. `get` stays exact.
- `render_realtime_skills_directive()` + `RealtimeVoiceSession._skills_directive`,
  wired into all three `_session_instructions` call sites. Over budget it
  drops descriptions and keeps every NAME — a listed name is callable, a
  folded one is not.
- `run-skill` resolves instead of reading, reports the *resolved* name
  downstream, and names the installed skills in its error so the model
  can correct itself inside the turn.
- `turn_planner`: a FIRE hit adds `TurnReason.SKILL` even without a clear
  winner; only the `skill:<name>` pin still requires one.
- `_tool_intent_refusal` plans with the same `skill_index` the router uses.
- `RealtimeVoiceSession._note_skill_for_delegate` hands a non-inlinable
  FIRE match to the brain via the existing `note_skill_trigger` contract
  (AD-S4), so a matched skill is inlined here or executed there, never
  neither.
- `creator_service.author` commits a slugged name and suffixes `-2`.

**Class rule.** A tool whose argument is a name from a catalogue is
half a feature until the catalogue is in the prompt — shipping the tool
without the names guarantees a guess. And two questions must not share
one answer: "is this about a skill?" is not "which skill?", so ambiguity
may cost the pin, never the route.

**Parity.** `_session_instructions` is one function shared by every
realtime transport, so `openai-realtime`, `gemini-live`, `vertex-live`,
the tool-less handoff and the self-hosted local server were equally
skill-blind and are equally fixed; the compact profile gets a names-only
roster rather than none (CLAUDE.md §3). The classic pipeline and the
chat hook already ran the `TriggerMatcher` and were never affected.

**Guards.** `tests/unit/skills/test_registry_resolve.py`,
`tests/unit/realtime/test_session_skills_roster.py`,
`tests/unit/brain/test_turn_planner_skill_ambiguity.py`,
`tests/unit/skills/test_prompt_injection.py`
(`test_realtime_directive_*`), `tests/unit/brain/test_run_skill_tool.py`
(`test_run_skill_resolves_a_display_name_to_the_installed_slug`).

## BUG-159: macOS permissions read as missing although System Settings shows them enabled — a sticky restart flag, a hidden recovery, and a silent grant-wiping rebuild (HIGH, FIXED 2026-08-20)

**Symptom (maintainer's Mac, reported 2026-08-20).** Every start warned that
macOS permissions were not enabled. Clicking the warning's button opened
System Settings, where the permission was already switched ON. Toggling it off
and on changed nothing: the next start showed the same warning. Restarting the
app did not help either.

**Root causes (three independent defects, plus the underlying identity churn).**

1. *The pending-restart flag was sticky and self-inflicted.* Any click on
   "Open Settings" for Screen Recording, Accessibility or Input Monitoring
   added the permission to `SystemPermissionPort._restart_required` — even
   when the pane showed it as already granted and the user changed nothing.
   Nothing ever removed it again except `reset()` or process exit. While set,
   `snapshot()` reported `restart_required`, every affected feature reported
   `ready: false`, and `runtime_access_granted()` returned False, so
   Computer-Use and the global hotkeys were genuinely switched off for the
   rest of the session although every grant was in place. Reproduced against
   the project's own fakes: all-granted → one `open_settings()` call →
   `computer_use`, `global_hotkeys` and `window_control` all flip to
   `ready: false` and stay there. The old test suite pinned this as intended
   behaviour (`test_runtime_access_blocks_pending_restart_even_after_native_grant`).

2. *The one recovery control was unreachable on the rows that need it.* An
   ad-hoc signature change strands the recorded TCC rows (BUG-083): System
   Settings keeps showing the checkmark while the kernel refuses. The way out
   is `tccutil reset` for this bundle id, offered as "Ask again" — but the
   frontend showed that button only for `status === "denied"`, and the boolean
   preflights behind Screen Recording (`CGPreflightScreenCaptureAccess`) and
   Accessibility (`AXIsProcessTrusted`) never produce "denied"; a stranded
   grant reads as plain `not_granted`. The two most frequently stranded rows
   therefore had no escape hatch at all, and the app-wide alert banner carried
   no reset button in any state.

3. *A request that macOS swallowed still promised a restart.* When a decision
   is already on file, `CGRequest*` returns False without showing a dialog.
   The flow set the restart flag anyway and reported success, so the user
   restarted into exactly the same state — the dead-button loop BUG-083 set
   out to end, reintroduced through the flag.

4. *A rebuild that wipes every grant was invisible.* `ensure_desktop_integration()`
   runs on every app start and rebuilds the bundle whenever an identity probe
   fails (Python minor-version change, moved checkout, failed signature
   check). The rebuild changes the ad-hoc signature, so `_reset_stale_tcc_grants()`
   drops all five TCC rows — correct, but silent: the log said only "failed its
   runtime/import probe" with no reason, and the UI simply showed everything as
   missing again, which reads as "the app forgot my permissions".

**Fix (2026-08-20).**
(a) `_live_state()` retires a pending-restart flag the moment a probe reads the
grant as ready, and every read path (`state`, `runtime_access_granted`,
`runtime_feature_ready`, `snapshot`) goes through it. A GRANTED probe IS live
access — the only per-process-frozen probe, Screen Recording, can go stale
negative but never falsely positive.
(b) `open_settings()` flags a restart only for a permission that is actually
missing, so looking at a granted pane changes nothing.
(c) New `can_reset` field on every permission row: offered whenever a
TCC-governed grant is missing under a stable app identity, suppressed only for
Screen Recording while a restart is pending (its frozen preflight cannot tell a
missing grant from the one just given, and a reset would discard it). Panel and
banner both render it, plus a hint naming the stranded-grant case.
(d) `record_identity_reset()` leaves a marker when a rebuild drops the grants;
`snapshot()` surfaces it as `identity_reset` and retires it once every TCC row
is back, so the re-ask is explained instead of looking amnesic.
(e) `_launchable_issue()` and diagnostics on both identity probes make every
rebuild log WHY it is rebuilding, at WARNING, naming that it resets permissions.
(f) `_reset_or_explain()` wipes the grants at most ONCE per unresolved round: a
recurring rebuild (failing identity probe, churning interpreter) would otherwise
discard the permissions the user just re-granted on every single start, which is
precisely the "I allow everything, restart, and it asks again" loop. While the
marker from the previous reset is unanswered, the sweep is skipped and only the
explanation is refreshed; the per-row "Ask again" stays available for the rows
the user actually wants reset.

**Class rule.** A permission flag that outlives the condition it describes is a
lie the user cannot clear. Anything the UI derives from a probe must die the
moment the probe contradicts it, and any recovery control must be keyed off an
explicit backend capability — never off one particular status string, because
the OS reports the same real condition through several of them.

**Guards.** `tests/unit/platform/test_permissions.py::test_a_pending_restart_dies_the_moment_the_probe_reads_the_grant`,
`::test_opening_settings_for_a_granted_permission_demands_no_restart`,
`::test_a_stranded_grant_offers_the_reset_although_it_never_reads_denied`,
`::test_a_granted_permission_never_offers_the_reset`,
`::test_a_signature_reset_is_explained_and_retired_once_grants_return`,
`PermissionsPanel.test.tsx` and `PermissionsAlertBanner.test.tsx`
(stranded-grant reset + signature-change explanation). The Screen Recording
fake no longer pretends the frozen preflight updates in-process.

**Still open.** The underlying churn is the ad-hoc signature itself: any forced
rebuild remains a new app to TCC. A Developer-ID signing identity is the
structural fix; until then a rebuild costs the user one round of re-granting,
now visible in the log and explained in the UI.

---

## BUG-160: a spoken question is answered with "Erledigt." and the real answer is thrown away — every Gemini/Vertex Live tool call fakes a mute provider (CRITICAL, FIXED 2026-08-20)

**Symptom (maintainer, reported 2026-08-20).** In a live call, questions came
back as the single word "Erledigt." — a completion report for a job that was
never given. The maintainer had asked something, not ordered anything, and had
no idea what was supposed to have been completed. The answer he did ask for was
never spoken.

**Root causes (two independent defects, one on top of the other).**

1. *The Gemini/Vertex Live adapter never withheld a tool-call turn boundary.*
   Gemini closes its generation when it hands out a function call, then opens a
   NEW generation carrying the spoken answer once the tool result arrives. The
   adapter has always meant to swallow that intermediate boundary, but the
   check read the PER-MESSAGE `function_calls` — and the server sends the tool
   call and the boundary in SEPARATE messages. The guard therefore never fired
   once: in one day's desktop log, 39 boundaries emitted, 0 withheld. The
   session took every one of them for a provider that had gone mute, ran its
   empty-turn recovery, sent a "say the tool result out loud" retry prompt
   (which on its own caused duplicate searches), and then spoke its own
   fallback line over the answer that was still being rendered. The OpenAI
   adapter has always done this correctly with a response-scoped flag; the two
   transports were simply not on the same contract.

2. *The direct-tool recovery reported a finished LOOKUP as a finished JOB.*
   `_direct_tool_fallback_text` reads each result for a speakable line.
   `search_web` has none by design — its whole point is that the model
   synthesizes the answer from the hits — so a successful search fell through
   to `action_phrase("cu_done")`, which is "Erledigt." / "Done." / "Listo.".
   Two lies in nine characters: nothing was completed, and the retrieved
   answer was dropped on the floor. Live evidence 2026-08-20 19:32:41:
   `transcript=9 chars` on the surface-TTS fallback, followed 2.1 s later by
   the model's real 6.5 s answer being withheld as a duplicate. In one day:
   20 spurious retries, 15 spoken fallbacks, 25 discarded answers.

**Fixes.**

1. `jarvis/plugins/realtime/gemini_live.py` decides the withhold on the
   per-GENERATION function-call counter, read before the boundary logger
   resets it. A generation that produced NOTHING after the tool result still
   emits its boundary, so the genuine-mute recovery is untouched; if the model
   never generates again, the session's own turn-stall watchdog closes the turn
   honestly rather than the adapter faking a boundary.
2. `jarvis/realtime/session.py` decides "done" on EVIDENCE, not on the tool's
   name, because a name is ambiguous (`gmail` sends mail and lists it). A
   lookup-class result diverts from `cu_done` only once its payload shows
   something the user has not heard: content came back → the facts go to the
   honesty-bound readback composer and answer the question, with
   `lookup_unspoken` ("I have the information but couldn't read it out just
   now") as the floor; an explicitly EMPTY result set → `lookup_empty`
   ("I didn't find anything on that"); a bare receipt with no payload →
   `cu_done` stands, unchanged.

**Class rule.** "Done." is a CLAIM about work, and a question is not work. Any
recovery line that a retrieval tool can reach must be able to tell an answer
from a receipt, and must never trade the user's answer for a status word. The
second half of the rule is older and was broken here too: a boundary a
transport emits for its own protocol reasons is not evidence about what the
user heard.

**Guards.** `tests/unit/realtime/test_gemini_live.py::test_tool_call_suppresses_turn_complete_from_a_later_message`
(the live message split the old per-message check could never see),
`::test_a_real_mute_after_a_tool_still_reaches_the_session` (the
counterweight — suppression must not cost the mute recovery), and
`tests/unit/realtime/test_lookup_never_reports_done.py` (a finished search is
never "Erledigt."; an empty one says so; a wordless ACTION still reports done;
the facts handed to the composer carry the content and never the URLs).

---

## BUG-161: macOS permissions still read as missing after BUG-159 — every start rebuilt the app bundle, and a copy in /Applications was treated as a stranger (HIGH, FIXED 2026-08-21)

**Symptom (maintainer's Mac, reported 2026-08-21).** The app kept asking for
permissions it had already been given. Granting them again changed nothing;
neither did a restart. This is BUG-159's symptom surviving BUG-159's fix,
which had closed three real defects but left the mechanism that produces the
loop untouched — its own "Still open" note named it.

**Root causes (four, each sufficient on its own).**

1. *A rebuild that cannot repair itself repeated on every single start.*
   `ensure_desktop_integration()` runs at every boot and rebuilds the app
   bundle whenever the identity probe refuses it. The rebuild changes the
   ad-hoc signature, and macOS answers a changed signature by orphaning every
   recorded TCC grant. When the probe fails for a reason a rebuild does not
   touch — a moved checkout, an interpreter the probe reads differently, an
   `open -W -n` that will not complete — the same rebuild happens again next
   start, and again after that. BUG-159 (f) only suppressed the second
   `tccutil reset`, and only while the user had NOT restored the grants:
   `snapshot()` retires that marker as soon as every row is back, so the user
   who did the right thing armed the next wipe. Grant everything, restart,
   be asked again — exactly as reported.

2. *A copy in `/Applications` was not recognized as this app.* The canonical
   location was `~/Applications` alone, everywhere: `_current_process_identity_valid()`
   compared the running bundle against that one path, so dragging the app to
   `/Applications` — the most ordinary thing a Mac user does — made the
   `~/Applications` slot look empty. Every start then built a bundle there,
   under the SAME bundle id, and the signature change stripped the grants of
   the copy that was running at that moment.

3. *The permission port called the same move a tampering signal.* `_bundle_identity()`
   applied the identical `~/Applications`-only rule to `stable`. An app in
   `/Applications` therefore reported an unstable identity: every request and
   settings button disappeared, `runtime_access_granted()` returned False for
   every grant, every feature reported not-ready, and the banner demanded
   permissions with nothing left to click. TCC pins a grant to the bundle id
   and its signature, never to a path, so the strict rule bought no safety.

4. *A Screen Recording grant given in System Settings was invisible forever.*
   `CGPreflightScreenCaptureAccess` answers from a value macOS freezes when the
   process first asks. The port had exactly one way to learn otherwise: a
   pending-restart flag that only its OWN request/settings flow set. A user who
   opened System Settings themselves — which is what macOS's own prompt tells
   them to do — got no flag and a permanent "not granted", however often they
   granted it.

**Fix (2026-08-21).**
(a) `_running_managed_bundle()` replaces `_current_process_identity_valid()`
and runs FIRST, before the launchable check: a process executing as a bundle
with our id, importing the managed checkout, IS the installed app and is left
alone — wherever the user keeps it. Location is no longer part of the verdict.
(b) `_rebuild_would_repeat()` records what each rebuild produced (cdhash,
checkout, interpreter minor version, machine). When a launchable bundle fails
its identity probe and a fresh build would reproduce the same app, the build is
skipped and the reason is logged at WARNING — a launchable app is worth more
than a probe verdict no rebuild can satisfy. A bundle that genuinely cannot
launch is still rebuilt every time.
(c) `_canonical_app_roots()` accepts `~/Applications` and `/Applications`
alike for the `stable` identity.
(d) `_default_screen_capture_live_check()` proves the grant by using it: window
TITLES of other applications are screen-recording-gated data, so one on-screen
window on layer 0 owned by another process and carrying a name means the grant
works right now. It only ever upgrades the frozen preflight — an empty result
is `None`, never a denial — and it costs nothing on the hot path, where the
preflight already reads granted.
(e) Opening a System Settings pane no longer requires the foreground. Only the
native prompt does (a dialog behind other windows is one the user never
answers); refusing the deep link left every background surface with a banner
whose single control answered "bring the app to the front".

**Class rule.** A repair that does not fix its own trigger is a loop, and a
loop that costs the user something must run at most once. Prove the cost is
buying a change before paying it again. Second half: a permission is bound to
an identity, never to a location — any check stricter than the OS's own turns
a normal user action into a permanent failure the user cannot even name.

**Guards.** `tests/unit/setup/test_macos_app_bundle.py::test_a_rebuild_that_reproduces_the_same_app_is_not_repeated`,
`::test_a_changed_interpreter_still_earns_a_fresh_rebuild`,
`::test_a_broken_bundle_is_always_rebuilt_however_often_it_recurs`,
`::test_the_running_app_is_accepted_wherever_the_user_keeps_it`,
`::test_the_running_bundle_probe_ignores_the_install_location`,
`::test_a_foreign_bundle_id_is_never_taken_for_the_managed_app`,
`tests/unit/platform/test_permissions.py::test_the_app_in_the_shared_applications_folder_is_a_stable_identity`,
`::test_a_live_window_probe_beats_the_frozen_screen_recording_preflight`,
`::test_a_live_window_probe_never_invents_a_grant_the_preflight_denies`,
`::test_window_titles_prove_the_grant_only_from_another_apps_own_layer`,
`::test_open_settings_works_while_the_app_sits_in_the_background`.

**Still open.** The ad-hoc signature remains the underlying churn: a rebuild
that IS necessary still costs one round of re-granting. A Developer-ID signing
identity is the structural fix. The rebuild guard also arms only after the
first rebuild it observes, so an install already caught in the loop pays for
one more round before it stops.

## BUG-162: dictation silently drops the end of what was said — a deep queue that never applied, a truncation guard that fired once in 797 recordings, and a loss nothing reported (HIGH, FIXED 2026-08-21)

**Symptom (reported 2026-08-21).** "Sometimes the last sentence just is not
transcribed." Intermittent, never reproducible on demand, and every affected
dictation reported itself as a clean success.

**Evidence.** 797 consecutive dictations from the local flight recorder, seven
days. Three independent causes, each producing the same user-visible symptom.

**Root cause 1 — the bulk queue depth never applied to a real dictation.**
`_capture_dictation_input()` opens its fallback capture with
`_DICTATION_CAPTURE_QUEUE_CHUNKS` (10 s), commented "the defence is depth".
That path only runs when NO wake stream exists. With a wake word configured —
the default, and the shipped state — every dictation instead borrows the wake
stream, which is opened at `DEFAULT_QUEUE_CHUNKS` (2 s) because a detector
wants fresh audio, not deep buffers. So the defence written for dictation was
dead code on exactly the installs that needed it. When the event loop stalls
longer than the queue holds, `MicrophoneCapture._safe_put` drops the OLDEST
frame per arrival and that speech is gone for good: it never became a chunk
anyone read, so no retry, no re-read and no Restore reaches it. Measured: 448
of 797 dictations (56 %) lost frames; 177 s of 10 293 s recorded; worst single
case 7.4 s missing from a 15.4 s recording. Confirmed independently by
differencing the recording's wall time against its captured audio seconds,
which reproduces the same figures.

**Root cause 2 — the truncation guard was calibrated out of reach.**
`_repair_truncated_read` re-reads a final-pass window whose transcript carries
fewer than `_DICTATION_TRUNCATION_TOKENS_PER_VOICED_S` tokens per voiced
second. At 1.0 that demanded a recognizer swallow some 60 % of a window before
anyone looked — real speech in the sample runs at a median 2.5 tokens per
second of WHOLE recording, pauses included. A dropped last sentence is a fifth
of the text, not two thirds, so the ordinary case never qualified. Across 797
dictations the repair fired **once**. Second gate, independent of the first:
the guard returned immediately unless the window held two or more speech runs,
so continuous speech — no pause to split at — was believed in full whatever it
came back as. Live examples, all with zero capture dropouts and
`truncation_repairs:0`: an 11.9 s recording ending "…interested in context
management **for**", a 15.7 s one ending "…as it right now is, **then**".

**Root cause 3 — nothing reported the loss.** `lost_audio_s` (recorded but
never transcribed) degrades the outcome to `partial` and offers Restore.
Capture dropouts had no equivalent: `audio_dropouts` / `audio_dropout_ms` went
to telemetry only, so a recording with seconds missing from the middle was
delivered as `inserted` with no caveat anywhere.

**Fix (2026-08-21).**
(a) `MicrophoneCapture.set_queue_depth()` re-bounds the bridge queue in place,
and `_capture_dictation_input()` deepens a BORROWED stream for the dictation
and restores the shallow bound on the way out — including on the crash path,
or this trades itself for a wake word scored against stale audio. In place, not
a fresh queue: replacing the object strands the drain already parked in
`get()`, which would stop the recording outright.
(b) `_DICTATION_TRUNCATION_TOKENS_PER_VOICED_S` 1.0 → 1.3 (78 wpm of actual
speech, against the sample's 150 wpm median), and a window with a single
speech run is now halved at its quietest middle moment
(`jarvis.dictation.segment.halve_at_quietest`) instead of returning early. The
merged reading still only wins by carrying MORE speech, so a false positive
costs one discarded read and never a worse transcript.
(c) `_finish_dictation(dropped_audio_s=…)` degrades to `partial` above 0.5 s —
one word at the measured speaking rate — and says so. Deliberately kept apart
from `lost_audio_s` and deliberately silent about Restore: the audio it would
re-read is the audio that was never recorded. Marks 10.8 % of the sample, every
one of them a real loss.

**Guards.** `tests/unit/audio/test_capture_overflow.py::test_the_depth_can_be_raised_while_the_stream_is_open`,
`::test_a_deepened_queue_stops_dropping_what_the_shallow_one_lost`,
`::test_the_queue_object_survives_a_resize`,
`::test_a_depth_below_one_is_refused`,
`tests/unit/speech/test_dictation_mic_handoff.py::test_the_borrowed_stream_is_deepened_for_the_dictation_and_handed_back`,
`::test_the_depth_is_restored_even_when_the_dictation_blows_up`,
`tests/unit/dictation/test_truncation_repair.py::test_continuous_speech_that_came_back_short_is_reread_in_halves`,
`::test_a_halved_reread_that_finds_no_more_speech_keeps_the_original`,
`::test_halving_covers_the_whole_buffer_with_an_overlap_at_the_seam`,
`tests/unit/dictation/test_partial_outcome.py::test_dropped_capture_audio_degrades_the_outcome_and_says_so`,
`::test_a_loss_too_small_to_have_taken_a_word_stays_a_success`,
`::test_both_kinds_of_loss_are_reported_in_one_sentence_each`.

**Still open.** What STALLS the loop long enough to overflow a 2 s queue is not
diagnosed — the deeper bound raises the ceiling to 10 s, it does not remove the
cause. There is no event-loop lag monitor anywhere in the process, so the stall
is currently invisible; the dictation lane runs a local preview decode every
1.2 s beside the drain, which is the first thing to measure. Cause 2's floor
also remains a density heuristic: it cannot catch a truncation that drops only
a short final clause, because no token count distinguishes that from ordinary
speech. A recognizer offering word timestamps would settle it directly.

## BUG-163: the wake word fires on a single unrelated word ('power', 'pedro', 'hey nova') — the BUG-151 lock proxy hid `SetGrammar`, so the acoustic competition silently ran on its static grammar (HIGH, FIXED 2026-08-22)

**Symptom.** Live 2026-08-22 17:10:52 (`data/jarvis_desktop.log`): with no
key pressed and no dictation running, the Jarvis Bar opened a Gemini Live
voice session on its own; the user hung it up ten seconds later with zero
turns and reported "it kept running after I let go of my key". The log line
behind it: `vosk-kws: verify OK (wake) — free ear heard 'power' at the
candidate span (conf=1.00) vs phrase 'Hey George'` — a ONE-word utterance
confirmed as a two-word wake phrase, fired from the early (partial)
candidate check on the `de` model. `scripts/vosk_wake_bench.py --phrase
"Hey George"` at HEAD reproduced it wholesale: 16/16 positives fired, but
13 of 40 negatives fired too — `neg_random` 4/12 ('pedro', 'peter',
'petra'), `neg_other_name` 6/12 ('hey nova', 'hallo florian'), `neg_flow`
3/8 — every one via the `de` model, every one "verify OK (wake|undecided)",
i.e. accepted by the acoustic competition. The 2026-08-12 calibration of
exactly that competition had measured these classes at 0.

**Cause.** The competition (`_shape_competition_ok`) installs its
per-candidate alternatives — the phrase, `"<prefix> [unk]"`, the free
ear's OWN hypothesis for the span, `"[unk]"` — with `SetGrammar`, which it
PROBES (`getattr(rec, "SetGrammar", None)`) because vosk builds before
0.3.32 lack it; without it the prewarmed recognizer keeps its static
grammar (phrase vs `"<prefix> [unk]"` vs `"[unk]"`), "a weaker but valid
competition". BUG-151 (2026-08-20) wrapped every native recognizer in
`vosk_native.LockedRecognizer`, a proxy that listed only the methods the
provider calls unconditionally — `AcceptWaveform`, `PartialResult`,
`Result`, `FinalResult`, `Reset`, `SetWords`. `SetGrammar` was not on the
list, so the probe answered "absent" on EVERY build and the competition
degraded to the static grammar everywhere, with no log line to tell the
two apart. Against phrase-vs-`[unk]` alone, a short isolated word that the
forced grammar had already stretched onto the phrase wins the phrase back
at conf ≥ 0.9; with the free hypothesis ('power', 'pedro') in the grammar
the decoder keeps the hypothesis and the candidate is rejected. The unit
test that pins the alternatives
(`test_the_competition_judges_span_trimmed_audio_with_the_hypothesis`)
passed throughout because it hands the provider a bare stub that is never
wrapped — the live path goes through the proxy.

**Fix (2026-08-22).** `LockedRecognizer` exposes `SetGrammar` explicitly
and proxies every OTHER native method through a `__getattr__` backstop
(locked, and answering a `getattr(..., None)` probe exactly like the
wrapped recognizer would), so no optional method can fall off the proxy
again. `_shape_competition_ok` logs ONE WARNING per provider when a
recognizer really has no `SetGrammar` — the static-grammar degrade is a
measurably weaker judge and must never be silent again. Tests:
`test_locked_recognizer_proxies_set_grammar_under_the_lock`,
`test_locked_recognizer_reaches_every_native_method_locked`,
`test_locked_recognizer_reports_a_missing_optional_method_honestly`
(`tests/unit/plugins/wake/test_vosk_native.py`);
`test_the_competition_installs_its_alternatives_through_the_native_lock`
(the live path: a vosk-named recognizer THROUGH `wrap_recognizer`) and
`test_a_competition_without_set_grammar_says_so_once`
(`test_vosk_wake_word_agnostic.py`). Bench after the fix, same corpus:
16/16 positives, 0/40 negatives.

**Lesson (AP-24/BUG-151 family).** A proxy around a native engine must be
TRANSPARENT — expose the wrapped object's whole surface, not the subset one
call site uses today — and any "optional capability" probe in the code
base (`getattr(x, "Method", None)`) is a place where a wrapper can lie
silently. When such a probe selects a weaker path, that path logs.

## BUG-164: "Ich habe dir Musik angemacht" — nothing played; a skill-instruction load was reported as the finished deed (HIGH, FIXED 2026-08-22) <!-- i18n-allow: quoted live German utterance under forensic analysis -->

**Symptom (maintainer, reported 2026-08-22).** In a live call (gemini-live,
hybrid tool mode) the maintainer asked for some relaxed music. Jarvis answered
"Alles klar! Ich habe dir entspannte Musik angemacht" — and no music played.
Nothing had been started; the assistant reported a deed that never happened.

**Forensic (``data/sessions.db`` ``voice_events`` + ``jarvis_desktop.log``,
session c845a2ce, turn 1, 18:16:21–31).**

* 18:16:21.942 the live model called ``run_skill`` for ``plugin-spotify``. The
  tool succeeded in 19 ms and returned the skill's INSTRUCTIONS ("use the
  connected Spotify … pass what the user said as query … say what actually
  started") plus the directive "follow these now, using your available tools".
  ``spotify`` itself was not declared natively (dropped under the declaration
  budget; reachable only through ``jarvis_action``).
* 18:16:21.983 Gemini Live emitted *interrupted* and *turn complete* 0.02 s
  after the tool response — no audio, no transcript, no further function call.
* 18:16:21.985 ``_recover_empty_provider_turn`` found one SUCCESSFUL direct
  tool result, judged nothing unfinished, and sent the "just say it" prompt:
  *the function call already finished … use only the function result … do not
  call any function and do not repeat the action*.
* 18:16:25 the model obeyed with a how-to as its "result": "Ich habe dir
  entspannte Musik angemacht." The turn's only tool call remained ``run-skill``.

**Cause.** A ``run-skill`` success is a RECEIPT FOR A TO-DO LIST, not for work:
the tool has rendered instructions the model must still carry out with other
tools. Every reader of ``_direct_tool_results`` treated it as a completed
action: ``_turn_has_unfinished_work`` saw ``success=True`` and said nothing was
owed, the continuation prompt therefore ordered a completion report and
FORBADE the very call that would have done the job, and the deterministic
fallback line (``_wordless_success_line``) would have spoken "Erledigt." for
the same receipt. The model's sentence was the predictable product of that
prompt, not a free invention. No "Tool Model" was involved: in hybrid mode the
live model holds the functions and this turn never reached the delegate.

**Fix (2026-08-22).** ``_is_skill_handoff_result`` recognises a ``run-skill``
instruction load (``instructions`` + ``directive`` in the output; a resource
read stays a real answer). ``_turn_has_pending_skill_handoff`` is true when
such a load is the NEWEST result of the turn — loaded and not carried out.
``_recover_empty_provider_turn`` then sends a third prompt variant
(``_direct_tool_result_retry_prompt(pending_instructions=True)``): the turn is
NOT over, carry the instructions out now (a tool without a native function is
reached through ``jarvis_action``), never say something was played/sent/done
unless a function result in this turn says so, and name plainly what could not
run. ``_direct_tool_fallback_text`` speaks the new
``skill_loaded_not_run`` phrase for a pending load ("Ich habe die Anleitung
dafür geladen, aber noch nichts ausgeführt.") and otherwise drops hand-off <!-- i18n-allow: quoted product line -->
receipts from the report, so they neither speak nor earn "Erledigt.". The
``run-skill`` inline directive and the BrainManager's inline injection carry
the same honesty clause for every brain. Tests:
``tests/unit/realtime/test_skill_handoff_is_not_done.py`` (the 18:16 turn end
to end, the classifier, pending vs. followed-up loads, every language).

**Follow-up fix (2026-08-22, same day).** Three more defects behind the
same turn, all landed: (1) the declaration budget was the MINIMUM over every
candidate in the provider chain, so a ``gemini-live`` call ran on the
``openai-realtime`` fallback's 8 000 tokens and lost ``spotify``,
``youtube_music``, ``gmail``, ``google_calendar``, ``home_assistant`` and
``spawn_worker`` to a budget that never applied to its wire —
``_declaration_budget_chars(provider)`` now takes ONE provider and
``_fit_declaration_budget`` re-trims the bridge (``set_declaration_budget``) for
the candidate about to open; (2) under a genuinely tight budget the connected
music connectors are now the LAST declarations to go (``keep_last`` in
``_apply_declaration_budget``, read from ``music_service.connected_music_services``);
(3) ``run-skill`` loaded ``plugin-spotify`` although only YouTube Music was
connected — it now follows the same resolver as the skill capture and the
execute-time reroute (``_prefer_connected_music_service``: a named service
wins, then the preferred connected one, then the only connected one). Tests:
``test_declaration_budget_per_provider.py``,
``test_connected_music_connectors_are_the_last_to_go``,
``test_bridge_refits_the_declared_set_to_a_new_budget``,
``test_a_music_skill_load_follows_the_only_connected_service``.

**Last mile on this box (2026-08-22 19:21, after restart).** The live model
now called ``youtube_music`` for real — and Google answered 403
``accessNotConfigured``: the YouTube Data API v3 was never enabled in the
bring-your-own Google Cloud project behind the shared Google OAuth client
(Gmail/Calendar/Drive worked because THEIR APIs were enabled there). Enabled
in the console; the first search then returned "Ed Sheeran - Perfect".
Jarvis had said it honestly ("Problem mit der YouTube-Programmierschnittstelle")
but without the one click that fixes it, so ``_explain`` now appends Google's
``activationUrl`` (``error.details[].metadata.activationUrl``) to the
``_API_DISABLED`` text — the Plugins view shows the direct link, voice scrubs
the URL as always (``_activation_url``,
``test_execute_explains_search_quota_api_disabled_and_scope``).

**Lesson (BUG-160 family).** A tool result's ``success`` flag says the TOOL
ran, not that the USER'S REQUEST happened. Any tool whose output is
instructions for the model (``run-skill``, ``answer_instruction`` lookups,
mission directives) is a hand-off, and every reader that turns results into a
completion claim — prompts and canned lines alike — must classify it as such.

---

## BUG-166: "play some music" takes three minutes and the player window never appears — the host sat in pywebview's loaded gate and the parent counted naps, not time (HIGH, FIXED 2026-08-22)

**Symptom (maintainer, 2026-08-22 20:01–20:05).** "Play nice music with
YouTube Music" — the tool call ran for **199.6 s**; the answer then said the
player "should be showing you throwback tracks"; no player window was visible.
Ten minutes later the window was found minimized on the taskbar with YouTube's
cookie-consent page in it.

**Cause (two defects, one symptom).** (1) ``music_player_host`` reads the page
with pywebview ``evaluate_js``, which is gated on the window's ``loaded``
event and blocks up to 20 s when a navigation has not reported back; after
the session's second ``load`` that event stayed clear, so EVERY ``state`` read
blocked, and because the host's command loop is sequential the ``show`` behind
them blocked too — the window was never restored. (2) ``_play_in_player``
bounded its confirm loop by counting its 0.5 s naps, not by the clock: 18
reads × (0.5 s nap + 10 s transport timeout) + one 10 s ``show`` = 199 s.

**Fix (2026-08-22).** Host: ``state`` answers ``{"loading": true, …}`` after a
bounded wait (``_page_loaded``, 1 s) instead of entering the gate; the page
commands fail fast with "still loading"; ``show`` additionally un-minimizes and
raises the window through Win32 by its title when pywebview's ``restore`` /
``show`` leave it iconic (``_force_foreground``, a quiet no-op off Windows).
Client: ``MusicPlayer.state()`` / ``show()`` take a per-call ``timeout``.
Plugin: the confirm loop runs on a wall-clock deadline, reads with a 3 s bound
(``_call_with_timeout`` also accepts players without the kwarg), skips
``loading`` snapshots, and still brings the window forward at the end. Tests:
``tests/unit/platform/test_music_player_host.py`` (new),
``test_state_and_show_take_a_per_call_timeout``,
``test_play_confirm_is_bounded_by_wall_clock_when_the_player_is_slow``,
``test_play_keeps_polling_while_the_host_says_loading``.

**Lesson.** A loop that bounds itself by counting sleeps is unbounded the
moment the work between sleeps can block; bound by the clock, and bound every
blocking read inside it tighter than the loop. A sequential command channel
must never run a call that can block for longer than its own client's timeout.

## BUG-165: the spoken answer stops mid-sentence after a web search, and the rest never comes — Gemini Live closes a tool call with `interrupted` first, which erased the BUG-160 evidence (CRITICAL, FIXED 2026-08-22)

**Symptom (maintainer, 2026-08-22 18:44).** "Erzähl mir mehr zu den 30 <!-- i18n-allow: quoted live German output under forensic analysis -->
Bundesstaaten und der Klage." <!-- i18n-allow: quoted live German utterance under forensic analysis -->
The reply was "Gerne. Diese Klage von den Bundesstaaten wirft Meta vor, dass <!-- i18n-allow: quoted live German output under forensic analysis -->
sie ihre Plattformen" — and silence. The maintainer hung up eight seconds later
and reported, with the session transcript, that Jarvis "keeps delivering
incomplete sentences". The same transcript shows the opening line restarted
("Guten Abend, bei mir ist alles entspannt und ich bin" / "bei mir läuft <!-- i18n-allow: quoted live German output under forensic analysis -->
alles, ich bin bereit …") and a mid-call "Alles klar, die Klage gegen" that <!-- i18n-allow: quoted live German output under forensic analysis -->
ended in the canned "Ich habe die Infos, konnte sie aber gerade nicht <!-- i18n-allow: quoted live German output under forensic analysis -->
vorlesen. Soll ich es nochmal versuchen?". <!-- i18n-allow: quoted live German output under forensic analysis -->

**Timeline of the marked turn (`data/jarvis_desktop.log`).**

```
18:43:57.842  gemini-live: function call(s) search_web
18:43:59.971  gemini-live: tool response sent for search_web (model_generating=True)
18:43:59.972  gemini-live: interrupted — audio=0.0s transcript=0 chars function_calls=1
18:43:59.972  gemini-live: turn complete — audio=0.0s transcript=0 chars function_calls=0
18:43:59.973  realtime[…] provider completed a direct-tool turn without output;
              retrying speech from the existing tool result
18:43:59.973  gemini-live: text input sent (526 chars; model_generating=False …)
18:43:59.974  gemini-live: generation started 0.00s after the last text input
18:44:00.003  gemini-live: interrupted — audio=4.6s transcript=82 chars   <- the answer, cut
18:44:04.253  gemini-live: generation started 4.28s after the last text input
18:44:04.253  realtime[…] discarding a provider generation that started 0.00s after the
              delivered readback with no new user input … (delivered reply: Ich habe die
              Infos, konnte sie aber gerade nicht vorlesen. …) <!-- i18n-allow: quoted live German output under forensic analysis -->
18:44:06.261  realtime[…] withholding provider audio (106 event(s) so far this window)
18:44:12.448  request_hangup
```

**Root cause.** Gemini Live closes a generation that handed out a function
call with an `interrupted` edge IMMEDIATELY followed by `turn_complete` — two
server-content messages, sent the moment the call goes out. On the wire the
session reads both right after it sends the tool result, because the receive
loop is busy running the tool in between (hence "1 ms after the tool response"
in every log). BUG-160 made the adapter withhold that `turn_complete` on the
per-generation function-call counter — but the `interrupted` branch runs
first, calls the same boundary logger, and that logger RESETS the counter. By
the time `turn_complete` arrives the evidence is gone, and the boundary leaks
exactly as before BUG-160. Count for this day: 12 function calls, 1 boundary
withheld, 7 leaked; in the 18:39 session 7 of 7 tool calls produced the
`interrupted`+`turn_complete` pair and 0 were withheld.

Every leak sets off the chain the timeline shows: the session takes the empty
boundary for a mute provider → sends its "say the tool result" retry text →
that text is a USER-side realtime input, and the server had already begun
streaming the real answer from the tool result → the server interrupts its own
answer (4.6 s / 82 characters had been sent; that is the sentence the user
heard end) → the model then answers the retry text a few seconds later →
the stale-generation guard (BUG-143/148), armed because the retry registered a
delegate readback, discards that second answer as a phantom. Net result: half
a sentence, then nothing, on a turn where the model had a complete answer.

A sibling race exists on the same wire and is NOT fixed here: the per-turn
mode line (`update_session` → `_flush_steering`) is sent in reaction to the
FINAL input transcript, which on Gemini arrives at the very moment the server
starts generating; in 2 of the 4 turns with immediate audio the steering text
interrupted the reply in flight, and the regenerated reply followed 2.5 s
later — the "Guten Abend … und ich bin / bei mir läuft alles" restart above <!-- i18n-allow: quoted live German output under forensic analysis -->
(`interrupted — … 0.07s after the last steering text input`). The session
plays both halves. That race is a product decision (keep the turn-scoped mode
line on Gemini and accept a restart, or drop the partial, or send the line
only while the user is still audibly speaking) and is left documented.
**Settled in BUG-171 (2026-08-23), second option:** the restart stands, the
partial is dropped. The race itself is still there — only its audible half is
gone.

**Fix.** `jarvis/plugins/realtime/gemini_live.py`: an `interrupted` edge that
arrives for a generation that handed out a function call is the first half of
the tool boundary, not a barge-in. The adapter withholds the edge from the
session and carries the function-call evidence across it
(`_tool_boundary_pending`), so the `turn_complete` half is withheld too. The
pending flag is cleared by that `turn_complete`, or by spoken output of a new
generation (a server that skipped the second half must never mute the real
end of the answer). A real barge-in during a tool run still confirms itself
through the user's input transcript, as it always has; an `interrupted` edge
on a generation without a function call is forwarded unchanged.

**Guards.** `tests/unit/realtime/test_gemini_live.py::test_tool_boundary_survives_the_interrupted_edge_the_server_sends_first`
(the real wire shape), `::test_tool_boundary_pair_packed_into_one_message_is_withheld_whole`,
`::test_a_chained_tool_call_keeps_withholding_until_the_spoken_answer`,
`::test_spoken_output_after_a_lone_interrupted_edge_releases_the_withhold`
(the safety net), `::test_a_real_barge_in_without_a_tool_call_still_interrupts`
(the counterweight). The BUG-160 tests keep passing; their fixtures never sent
the `interrupted` half, which is why the regression was invisible to them.

**Lesson (BUG-160 family, third instance).** A transport boundary is evidence
only in the exact shape the server sends it. Twice now the Gemini tool
boundary was "handled" against a shape the tests invented (per-message, then
per-generation-without-`interrupted`) and the live wire never matched. When a
guard is written from a log, the test fixture must replay the log's message
sequence, edge for edge — not a tidied version of it.

## BUG-167: "Drop to put it in front of T2" sits over live agents for the rest of the session — every drop target waited for an event a drag that ends elsewhere never sends (MEDIUM, FIXED 2026-08-23)

**Symptom (maintainer, 2026-08-23 08:45).** After one drag that was released
somewhere other than a terminal pane, several panes in the Agentic IDE kept
showing the dimmed "Drop to put it in front of **Tn**" overlay — over agents
that were running. The count grew with each further drag, and the same thing
happened in more than one workspace. Nothing but a reload took the overlays
back down.

**Cause (one defect, five copies).** A pane arms on `dragenter` and expected
to disarm on `dragleave`, `drop` or `dragend`. All three can be absent:
`drop` fires only when the release happens in THIS document, `dragend` fires
only on the drag's source element — which a drag out of Explorer or Finder
does not have in the page at all — and `dragleave` is not owed to a target the
drag never left by moving (Escape, a release outside the window, another
window taking the drag). The one heuristic that was there (a `dragleave` with
a null `relatedTarget` while the cursor sits on the viewport edge) covers a
drag walked slowly out of the window and nothing else.

What made it PERMANENT rather than momentary was the second half:
`usePaneFileDrag` counts enter/leave pairs so a drag crossing child elements
does not flicker. A drag that ended without its leave left that count at 1, so
every later drag over the same pane needed one leave more than it fired — the
pane could never reach zero again, and each new drag stranded one more pane on
its way past. The identical arm-and-hope pattern existed in `WorkspaceBar`
(tab highlight), `JarvisDock` (bloom), `VoiceBubble` (orb hover) and
`FolderPicker` (which had no backstop whatsoever).

**Fix (2026-08-23).** `components/agentic/dragSessionEnd.ts` — the end of a
drag is detected by ABSENCE instead of awaited. While a drag is over the
document the browser repeats `dragover` on its own (the HTML drag-and-drop
processing model runs roughly every 350 ms, cursor moving or not); that
repetition is a heartbeat, and 1.2 s of silence means the drag is gone,
whatever ended it and whichever event went missing. Nothing has to be
delivered for this to work, so nothing can be swallowed. The fast signals
(`drop`, `dragend`, the viewport-edge `dragleave`) are kept alongside it for
same-frame disarm, all in the CAPTURE phase so a `stopPropagation()` in the
tree cannot hide them; `pointerdown` and `keydown` end the session too, since
neither reaches the page while a drag runs — which is what makes a stranded
overlay heal itself the moment the user touches the app again. Watchers are
shared, so ONE drag ending takes every armed surface down together. All five
call sites now use `useDragSessionEnd(armed, disarm)`, and `disarm` zeroes the
pane's enter/leave count, so it can no longer outlive the drag that raised it.

**Guards.** `src/components/agentic/paneFileDrag.test.ts::disarms when the drag
simply stops existing — no drop, no dragend, no leave`, `::heals on the next
click when a drag stranded it`, `::does not carry a stale enter count into the
next drag`, `::takes every armed pane down together`. The pre-existing tests
for the drop path and the child-crossing count keep passing unchanged.

**Lesson.** A UI state armed by a browser event must never be disarmed by
another browser event alone — drag, pointer capture and focus all have exits
that send nothing. Detect the end by the absence of the signal that only
exists while the gesture does, and make the shared teardown reach every
surface the gesture armed, not just the one it happened to leave last.

## BUG-168: clicking Wiki says "Cannot read properties of undefined (reading 'WikiGraph')" — cancelling `vite:preloadError` turned a missing chunk into a dynamic import that RESOLVED WITH `undefined` (HIGH, FIXED 2026-08-23)

**Symptom (maintainer, 2026-08-22 20:56, repeatedly before that).** Opening the
Wiki section of the desktop app painted the view error boundary: "View could not
be loaded / memory crashed. George stays usable." with
`Cannot read properties of undefined (reading 'WikiGraph')` in the detail box.
Nothing in the wiki system was involved — the vault, `/api/wiki/graph` and
`WikiGraph.tsx` were all healthy. The report recurred often enough to read as a
chronic fault in that section.

**Root cause.** Two mechanisms met.

1. *The trigger.* `npm run build` runs with `emptyOutDir`, so every hashed chunk
   in `dist/assets/` is deleted and rewritten under any window that is already
   open. That window still holds the old entry chunk, which knows the previous
   hashes. Reproduced live while diagnosing this: `WikiGraph-ZXJsF_pi.js`
   existed at the start of the investigation and was `WikiGraph-CFi_aciO.js`
   four commands later. Wiki is the section that shows it first because
   `WikiView` opens on a lazily imported graph, and it carries four dynamic
   imports — more than any other section.

2. *The disguise, and the actual defect.* `installPreloadRecovery` called
   `event.preventDefault()` on every `vite:preloadError`, on the belief that
   "Vite's default for an unhandled preload error is to reload the page itself".
   Vite does no such thing. Its helper, verbatim from the built bundle, is

   ```js
   function o(l){const d=new Event("vite:preloadError",{cancelable:!0});
     if(d.payload=l, window.dispatchEvent(d), !d.defaultPrevented) throw l}
   return r.then(l=>{ /* … */ return t().catch(o) })
   ```

   `o` is the `.catch` handler of the dynamic import itself. Cancelling the
   event means `o` returns instead of throwing, so the import RESOLVES — with
   `undefined`. The caller's
   `import("@/components/wiki/WikiGraph").then((mod) => ({ default: mod.WikiGraph }))`
   then dereferences `undefined`, and the boundary shows a message naming the
   component instead of the missing file. Every one of ~40 lazy views in this
   app had the same trap armed; Wiki was only the one that kept springing it.

   The cancel also hid the failure from the boundary in the one case where the
   recovery does nothing: `handlePreloadError` spends one reload per incident,
   and the idle view prefetch warms 20+ chunks at once, so a rebuild burns that
   reload on the first of them and every later failure inside the 30 s settle
   window is silent by design — but silent as `undefined`, not as an error.

**Fix.** `jarvis/ui/web/frontend/src/lib/preloadRecovery.ts` — never cancel the
event. The reload still happens; the rejection travels on, so what reaches the
boundary is the real cause. `isChunkLoadError()` moved in beside it, matching
the four wordings the engines use ("Failed to fetch dynamically imported
module", "error loading dynamically imported module", "Importing a module script
failed", "Unable to preload CSS for") — there is no error type to test, a failed
dynamic import is a plain `TypeError` and the message is the only mark.

`components/ViewErrorBoundary.tsx` — a stale chunk is a rebuild, not a fault, so
it gets its own card: neutral colour, no raw engine message, `console.info`
instead of `console.error`, and a Reload button through `reloadWhenServable`
(which waits for a whole build rather than landing in a half-written `dist/`)
instead of "Back to Chats", which never fixed anything here.

**Tests.**
- `lib/preloadRecovery.test.ts::NEVER cancels the event, so the failed import
  still rejects` — asserts `preventDefault` is untouched on both passes, the one
  that reloads and the one the guard stops.
- `lib/preloadRecovery.test.ts::isChunkLoadError` — the four engine wordings,
  and that a genuine `Cannot read properties of undefined` is NOT claimed as a
  stale chunk.
- `components/ViewErrorBoundary.test.tsx` — the stale card offers reload rather
  than recovery, never prints the chunk URL, does not log a crash; a real
  component bug still shows its message, recovers, and logs.

**Lesson.** A handler attached to a cancellable error event may be sitting in
the failure path of the very promise that failed. Cancelling it does not "keep
the decision local" — it decides that the operation SUCCEEDED, and hands the
caller whatever the handler returned. Before cancelling any event, read what the
dispatcher does with `defaultPrevented`; if that code path is a `catch`, the
cancel is a silent success and the crash surfaces later, somewhere unrelated,
naming the wrong component.


## BUG-169: "The scheduled job GitHub-API Zen has started failing" keeps interrupting — one 504, one 403 or one DNS drop on the 5-minute demo healthcheck was spoken twice, as a breakage and as a recovery (MEDIUM, FIXED 2026-08-23)

**Symptom (maintainer, 2026-08-23).** Jarvis kept speaking an English sentence
about a job the maintainer had never set up — heard as "the job kit up API
started failing / is successful". `data/jarvis_desktop.log` has the exact
wording: `📢 Announcement: 'The scheduled job GitHub-API Zen has started
failing.'` at 19:38:24, `'… is working again.'` at 19:44:24, `'… has started
failing.'` again at 21:02:27 and at 09:17:12 the next morning. "GitHub-API Zen"
is the Conductor seed job `conductor/seed/url_healthcheck.yaml` — a demo HTTP
monitor that GETs `https://api.github.com/zen` every 300 s and expects a 2xx.

**Root cause.** `conductor.core.notify.classify_run` (audit AU-01) announced
every change of terminal state: `completed → failed` was "failing",
`failed → completed` was "recovered", with no confirmation window at all.
`~/.conductor/conductor.sqlite` shows what that did on a real network over seven
days: 91 runs, 29 failed — 21 × `ConnectError: [Errno 11001] getaddrinfo failed`
(the box was offline 21:02–23:32), 6 × `timeout after 10.0s`, one
`status 504`, one `status 403` (GitHub's unauthenticated rate limit, 60/h per
IP shared with every other tool on the box). The 504 and the 403 were single
blips between healthy runs, and each one produced TWO spoken announcements six
minutes apart. No monitoring product people keep installed alerts on a single
failed check; every one of them confirms across consecutive checks first. The
voice was right that it was English — a background job has no user turn to read
a language off, so `resolve_output_language` fell through to `DEFAULT_LOCALE`
("en") until the first conversation of the session — but the English was not
the defect, the alert was.

**Fix.** Two rules, both in `conductor/core/notify.py`, both pure:

1. *A single blip is not a breakage.* `failures_to_announce(schedule_type,
   schedule_expr)` returns 3 for an interval job that runs at least every
   15 minutes (`FREQUENT_INTERVAL_MAX_S`) and 1 for everything else — a daily
   cron, a manual or webhook job cannot wait three runs, three days of silence
   about a broken daily report is worse than a false alarm. "Failing" is news
   exactly when the consecutive-failure streak EQUALS the threshold: not before
   (unconfirmed), not after (same breakage, already announced — which also
   keeps a process restart from re-announcing an old outage).
2. *A recovery is only news when the breakage was.* `Runner` keeps the set of
   job ids it announced as failing, in memory on purpose, and passes
   `failing_was_announced`; a "working again" about a blip that was never
   spoken — or about an outage a previous process announced — answers a
   question nobody heard.

`ConductorStore.failure_streak(job_id)` walks the run history newest-first and
counts `failed` until the first `completed`/`cancelled` (skipping
pending/running), so the count survives restarts and needs no schema change.
`Runner._finish` reads it after `update_run` has persisted this run's own state.

**Tests.** `tests/unit/conductor/test_run_news.py` — `a_single_blip_on_a_frequent_job_is_not_news`,
`the_third_consecutive_failure_is_the_news`, `a_recovery_nobody_heard_the_breakage_of_is_silent`,
`a_rare_job_announces_on_the_first_failure`, and the real-runner pins
`a_frequent_job_blip_never_reaches_the_user`,
`a_frequent_job_announces_once_the_failure_is_confirmed`,
`a_runner_restart_does_not_replay_a_stale_recovery`,
`a_runner_restart_does_not_reannounce_an_old_outage`.

**Lesson.** A state machine that speaks is a monitoring product, and the first
question about any alert is "how many confirmations?". "Announce on change"
sounds like the minimum-noise rule and is the maximum-noise rule on a flaky
network: every blip is two changes.

## BUG-170: after a web-search answer Jarvis stops hearing the user — "I muted, unmuted, and then I could not speak until I closed the bar"; the answer spoken INSIDE the tool generation had its `turn_complete` withheld (CRITICAL, FIXED 2026-08-23)

**Symptom (maintainer, 2026-08-23 09:25).** During a voice call Jarvis
searched the web and spoke the answer. The maintainer double-clicked the bar
to mute their microphone while it was speaking, unmuted again — and from then
on nothing they said reached Jarvis. The call stayed open with the bar on the
speaking look; only closing the bar (hangup) brought the microphone back. The
mute was a bystander: the same deafness happened at 09:04:55, 09:23:42 and
09:24:38 the same morning without any mute, escaped each time only by a
shouted barge-in (`barge-in confirmed by local CPU VAD`) or by hanging up.

**Timeline (`data/jarvis_desktop.log`, session 4b885d9d).**

```
09:25:18.501  gemini-live: function call(s) search_web
09:25:20.354  🔇 Voice mute ENABLED (source=orb_dblclick_double)
09:25:20.696  gemini-live: interrupted (function call, edge withheld) — audio=0.0s … function_calls=1
09:25:20.697  gemini-live: turn complete (function call, boundary withheld) — audio=0.0s
09:25:20.697  gemini-live: generation started 0.00s after the last tool response input
09:25:20.697  gemini-live: function call(s) search_web          ← second call, SAME generation continues
09:25:22.228  gemini-live: tool response sent for search_web (model_generating=True)
09:25:23.592  turn-state: PROCESSING -> JARVIS_SPEAKING         ← the answer, spoken in place
09:25:39.350  🔇 Voice mute disabled (source=orb_dblclick_double)
09:25:41.902  gemini-live: turn complete (function call, boundary withheld) — audio=18.6s transcript=365 chars function_calls=1
              (no "Realtime echo tail armed", no "JARVIS_SPEAKING -> LISTENING" — ever)
09:25:53.470  📵 request_hangup — closing the voice session
09:25:56.038  turn-state: JARVIS_SPEAKING -> IDLE
```

**Root cause.** BUG-160/165 taught the Gemini Live adapter to withhold the
boundary of a generation that handed out a function call, because the server
closes such a generation (empty `interrupted` + `turn_complete`) so the tool
result can travel and then opens a NEW generation with the spoken answer. The
live wire has a second shape the fixtures never replayed: the model keeps the
SAME generation after the tool result and speaks the answer in place — 18.6 s
of audio, one `turn_complete` at the end, no boundary in between. The
per-generation evidence ("this generation called a tool") was true for that
boundary too, so the adapter withheld the turn's real end. Downstream the
desktop pipeline closes its speaking segment only on that `turn_complete`
(`_close_output_segment` → echo tail → LISTENING); without it `speaking` stays
True, every microphone frame goes to the local barge-in detector only, and the
provider never hears the user again. The session's turn-stall watchdog cannot
rescue this: a turn that already produced audio is excused from it forever
(`_output_samples_sent > 0`), by design.

**Fix.** `jarvis/plugins/realtime/gemini_live.py`: a boundary is a tool
boundary only while nothing has been spoken since the generation's most
recent function call (`_gen_output_since_tool`, reset on every tool call,
counting audio bytes and transcript chars afterwards). Both halves use the
same predicate (`_boundary_is_tool_boundary`): the `interrupted` edge of a
generation that already spoke past its tool call is a barge-in again, and
its `turn_complete` reaches the session as the turn's end (logged as
`turn complete (function call answered in place)`). Words spoken BEFORE the
call ("Moment …") do not count — that boundary still belongs to the hand-off.

**Guards.** `tests/unit/realtime/test_gemini_live.py::test_an_answer_spoken_in_the_tool_generation_closes_the_turn`
(the live shape), `::test_a_barge_in_over_an_answer_spoken_in_the_tool_generation_interrupts`,
`::test_a_preamble_before_the_tool_call_does_not_release_the_withhold`. The
BUG-160/165 suites keep passing.

**Lesson (BUG-160 family, fourth instance).** "This generation called a tool"
is not the evidence; "nothing was spoken since the call" is. A withhold keyed
on what a generation once did, rather than on what it has done since, turns
the first counter-example into a stuck turn — and a stuck SPEAKING turn is
the worst failure the voice path has, because nothing the user does with
their voice can end it.

## BUG-171: one question, two spoken answers — the per-turn directive interrupts the reply the server has already started, and both halves are played (HIGH, FIXED 2026-08-23)

**Symptom (maintainer, 2026-08-23 10:06).** A reply ended mid-sentence and a
different one followed it a beat later, sounding like a second speaker cutting
the first off: "Alles klar. Wenn du nachher noch was brauchst, sag" — then <!-- i18n-allow: quoted live German output under forensic analysis -->
"Dann wünsche ich dir einen entspannten Start in die neue Woche!". Every turn <!-- i18n-allow: quoted live German output under forensic analysis -->
of that call had the shape; the transcript recorded each half as its own turn,
the second with an empty `user_text`.

**Timeline (`data/sessions.db` + `data/jarvis_desktop.log`, one turn).**

```
10:05:58.588  delivered a per-turn steering delta (turn; 642 chars)
10:05:58.597  generation started 0.01s after the last steering text input
10:05:58.614  interrupted — audio=2.4s transcript=50 chars generation=0.02s;
              0.03s after the last steering text input
10:05:58.614  deferred an unconfirmed provider interruption; waiting for the
              user's own words before cutting the reply
10:05:58.652  turn complete — audio=0.0s transcript=0 chars
10:06:00.588  generation started 2.00s after the last steering text input
10:06:02.534  turn complete — audio=3.0s transcript=63 chars
```

Both generations answer the same utterance ("Nee, nicht wirklich."), and the <!-- i18n-allow: quoted live German output under forensic analysis -->
first is 50 characters of a sentence the model never got to finish.

**Root cause.** On Gemini Live a text input IS a user turn — there is no
configuration channel. `update_session` therefore delivers the per-turn mode
line as a realtime text at the FINAL input transcript, which on this transport
arrives in the same millisecond the server starts generating. `_flush_steering`
guards on `_model_generating`, but that flag is set by the first output the
adapter READS; the server had already decided. When the text loses the race it
lands on an answer in flight, the server abandons that answer (`interrupted`),
closes it with an empty `turn_complete`, and regenerates two seconds later.
3 of the 9 directives delivered that day did this (09:24, 10:05, 10:06).

The session then made it audible. RT-09 defers an `interrupted` edge that no
user words confirm — correct for a cough, wrong here: nobody was ever going to
confirm an edge caused by our own text, so the dead half played out in full,
was recorded as a turn, and the replacement was spoken after it. The gap is
that the edge was *knowable*: the adapter logs the attribution ("0.03s after
the last steering text input") and then threw it away.

**Fix (2026-08-23).** The adapter names the edge instead of logging it:
`_interrupt_is_our_own_text_input` marks an `interrupted` that follows one of
our own text inputs within 1.5 s as `superseded` on the provider event (a real
barge-in cannot land there — the user's final transcript arrived milliseconds
earlier), and withholds the empty `turn_complete` that closes the abandoned
generation, exactly as the BUG-165 tool boundary is withheld. Spoken output
from the replacement releases the withhold; the user's next words release it
too; a second empty boundary passes through, so a turn can never latch open.
`_drop_superseded_generation` in the session drops what the far end abandoned
— buffered PCM, partial transcript, surface queue — and keeps the turn open
for the replacement. It deliberately does NOT arm
`_drop_provider_output_until_new_response`: that guard is released by the next
locally grounded final transcript, and a regeneration comes with no new user
input at all, so arming it would swallow the very answer being cleared for
(the 2026-08-10 shape — three complete replies discarded, 23.3 s to first
audio). Straggling frames of the dead generation are kept out by retiring its
response id instead.

Nothing is audibly lost: the edge lands 30–90 ms after the first audio chunk,
against a device output latency of 0.41 s, so on both live incidents the cut
half had not reached the speaker yet.

**Guards.** `tests/unit/realtime/test_gemini_live.py::test_our_own_steering_text_interrupting_a_reply_supersedes_it`
(the live message sequence, edge for edge),
`::test_a_barge_in_long_after_our_own_text_still_interrupts` (the
counterweight), `::test_a_second_empty_boundary_after_a_supersede_is_not_withheld`
(the anti-latch), and
`tests/unit/realtime/test_session_noise_interrupt.py::test_a_superseded_generation_is_dropped_and_its_replacement_speaks`,
which pins the sharper half: the replacement must still arrive.

**Not fixed here.** The race itself stands. The mode line depends on the turn
plan, which depends on what the user just said, so it cannot be sent earlier
than the final transcript; sending it later would provoke an unprompted reply.
Removing the race for good means owning the turn boundary — manual activity
detection, where the client sends the directive and only then declares the
user finished — which is a turn-taking change with its own latency and
barge-in risk, not a bug fix.

**Lesson.** RT-09's "wait for the user's words" is the right default for an
edge whose cause is unknown. It is the wrong one for an edge whose cause is
US, and the adapter knew the cause well enough to write it into the log. An
attribution that is good enough to diagnose from is good enough to act on.

## BUG-172: the Jarvis Bar is gone from the screen and a restart does not bring it back — the dev instance drew its own bar and switching that one off rewrote the shared config (HIGH, FIXED 2026-08-23)

**Symptom (maintainer, 2026-08-23 evening).** "My bar is not shown any more."
The slim on-screen Jarvis Bar had disappeared. Restarting the app did not bring
it back: it came up without a bar every time.

**Timeline (`data/jarvis_desktop.log` + `data-dev/jarvis_desktop.log`).**

```
15:29:14  data-dev  On-screen overlay active: style=jarvis_bar   <- dev app drew a SECOND bar
15:42:20  data-dev  On-screen overlay active: style=none         <- the duplicate was switched off
15:48:01  data      On-screen overlay active: style=none         <- the LIVE app lost its bar
19:58:21  data      On-screen overlay active: style=none         <- and kept losing it
```

The two logs are two processes; the config file between them is one.

**Root cause.** The dev instance (`--instance dev`, shipped 2026-08-23) separates
the data dir, the ports and the OS identity, but deliberately shares
`jarvis.toml` — same providers, same keys, "everything works". The on-screen
overlay was not on the ambient-duty list, so the dev app built its own Jarvis Bar
at the very same screen position as the live one. Switching that duplicate off in
the dev window did what the settings route always does: it persisted
`[ui] orb_style = "none"` — into the ONE file the live app reads at boot. The
live app then had no bar to draw, and no amount of restarting could tell it
otherwise, because the persisted value said "none" on purpose.

Two properties made it invisible: the change is written by the app the
maintainer was NOT looking at, and it only takes effect on the next boot of the
app they WERE looking at, so the cause and the symptom never share a moment.

**Fix.** The on-screen overlay became an ambient duty, like the wake word, the
global hotkeys, the chat channels and autostart (`InstanceIdentity.owns_ambient_duties`):

* `jarvis.ui.desktop_app.boot_overlay_style()` — a non-default instance always
  boots the NullOverlay. At RUNTIME only: the configured value is never touched,
  it belongs to the app that owns the screen.
* `PUT /api/settings/overlay-style` answers 409 in a non-default instance and
  names where the switch lives, so a pick there can neither persist nor swap.
  `GET` still reads the shared value, so the dev window shows the truth.

**Why not "let each instance have its own overlay style".** That would need a
per-instance config layer for a single field, and two bars on one screen position
is a defect on its own — the second one is indistinguishable from the first.

**Tests.** `tests/unit/ui/test_overlay_boot_instance.py` (every style, both
instances, explicit identity beats the environment),
`tests/unit/ui/test_overlay_style_route.py` (dev PUT = 409 and persists nothing,
dev GET unchanged).

**Related.** BUG-031 (a second `tk.Tk()` root at runtime aborts the process) is
why the recovery still needs one restart: an app that booted with `style=none`
has no Tk root, so the bar cannot be swapped in live.

---

## BUG-173: the chat says a turn cost "10 tokens" when it cost fifty thousand — the live counter reads the placeholder and skips the cache (MEDIUM, FIXED 2026-08-24)

**Symptom (maintainer, 2026-08-23).** The live status line under a running
agent-chat turn read `Noodling… (31s · ↓10 tokens)` after half a minute of work
that included two tool calls and a thinking block. "I did not use just 8
tokens."

**Evidence.** One real Claude Code turn, `--output-format stream-json`, the
`assistant` line's own usage object:

```json
{"input_tokens": 2, "cache_creation_input_tokens": 30420,
 "cache_read_input_tokens": 21355, "output_tokens": 1}
```

and the `result` line that closes the same turn:

```json
{"input_tokens": 2, "cache_creation_input_tokens": 30420,
 "cache_read_input_tokens": 21355, "output_tokens": 4}
```

**Root cause — two mistakes stacked.**

1. `translate_claude_line` counted `input_tokens` and `output_tokens` only, so
   the 51 775 cached tokens that carry the turn were dropped. The input side
   read as **2** instead of **51 777** — the CLI reports the cached part BESIDE
   `input_tokens`, never inside it.
2. `output_tokens` in an `assistant` line is the `message_start` snapshot: a
   placeholder that only becomes true in the `result` line. Summing one
   placeholder per message is a message counter wearing a token counter's
   clothes — ten messages read as "10 tokens".

The finished-turn receipt was right all along: the `result` branch of the same
translator already read all four fields. Only the live line was blind, which is
why the number looked plausible until someone compared it with a bill.

**Fix.**

* `jarvis/agent_chat/runner_cli.py` — `_CLAUDE_USAGE_KEYS` counts all four
  fields, and a message's usage is merged with `max()` so a placeholder can
  never walk a real count back down.
* `jarvis/ui/web/frontend/src/components/agentchat/toolView.ts` — `inputTokens`
  adds `cache_read_input_tokens` and `cache_creation_input_tokens`. The OpenAI
  shape (`cached_input_tokens`) is deliberately NOT added: there the cached
  tokens are already inside `prompt_tokens`, and adding them would double-count.
* `AgentTimeline.tsx` — the live line no longer shows a token count at all.
  There is no honest one to show: measured against a real multi-step turn, every
  `assistant` line reports `output_tokens: 2` and only the closing `result` line
  carries the true 981, while the input side legitimately re-counts the whole
  conversation at every step (21 355 → 51 902 → 54 094 cached tokens read). The
  sum is arithmetically right — it matches the CLI's own `result` to the token —
  but "↑283.2k" under a one-sentence question reads as a bug, and a live figure
  that is either far too small or unbelievably large is worse than none. The
  receipt, which runs on the authoritative `result` line, keeps ↑ in / ↓ out /
  cost. If a CLI ever streams true usage, the line can come back.

**Lesson.** Two readers of one stream drift apart. The receipt path and the live
path sat thirty lines from each other in the same file; one was extended for
cache tokens, the other was not. A number that crosses layers needs one reader
and a parity test (AP-4) — a second, hand-rolled reader is a bug with a delay
fuse.

## BUG-174: right after a restart, voice on local models is unusably slow or returns nothing — the Whisper engine was built ON the event loop, and a slow build was answered by starting it again from zero (HIGH, FIXED 2026-08-24)

**Symptom (maintainer, 2026-08-24 morning).** "I used it after a restart with
local models and it is so slow or does not work at all." Dictation returned zero
characters; the whole app felt frozen for a minute at a time.

**Timeline (`data/jarvis_desktop.log`).**

```
09:05:58  Dictation STT warm-up did not finish within 20s; replacing the provider instance
09:06:10  Event loop STALLED for 15.0s   <- stack: transcribe_pcm -> _ensure_model -> WhisperModel(
09:07:11  Event loop STALLED for 75.3s   <- same stack, still building
09:08:31  (maintainer restarts the app)
09:09:15  dictation ended (0 chars, stt error: unavailable — provider did not answer within 8s)
09:12:36  dictation ended (0 chars, stt error: engine_busy (TranscribeBusy))
09:13:26  dictation ended (37 chars)     <- fine from here on, the engine is warm
```

The failure window is the first minutes after a restart and nothing else — which
is exactly when a person tries the thing they just restarted.

**Root cause, part one: the build ran on the event loop.** `transcribe_pcm` is a
coroutine, and it awaited `_ensure_model()` inline. Constructing a ctranslate2
`WhisperModel` is a long BLOCKING call — weights off disk, DLL resolution, a VRAM
allocation — so a cold `large-v3` on CUDA held the loop thread for 90+ s. That is
every WebSocket frame, HTTP route and brain turn frozen behind one model load,
which is the "so slow" half of the report.

It also disabled the caller's own protection. `_read_piece` wraps the call in
`asyncio.wait_for(..., timeout=ceiling)`, but a timeout needs a RUNNING loop to
fire on; while the loop is the thing that is blocked, the ceiling cannot expire.
When it finally did, the abandoned build kept the engine's lock, so the next
attempt came back `TranscribeBusy` and the dictation ended empty (AP-24) — the
"does not work at all" half.

**Root cause, part two: giving up made it worse.** The dictation warm-up
supervisor could only read a wall clock. Twenty seconds elapsed meant "this
warmer is unhealthy", so it abandoned the whole provider instance and built a
replacement — which starts the identical cold load AT ZERO. Replacing an
instance never shortens a load, it repeats it. The maintainer waited through both
and still got nothing. This is the same load cascade that once turned a 4 s load
into 114.7 s on the boot path (TTU forensic 2026-07-02), rediscovered on the
dictation lane.

Nothing was guarding `_ensure_model` either, so two callers arriving during that
window each started their own build — twice the wait and twice the VRAM.

**Fix.**

* `transcribe_pcm` / `transcribe` build through `_ensure_model_async`, which
  ships the construction to a thread. The already-built path costs one attribute
  read and no thread hop, so steady state is unchanged.
* `_ensure_model` takes a DEDICATED load lock, so a second caller waits for the
  first engine instead of building a rival one. It can never deadlock: only
  `_ensure_model` takes it, so the order is always infer -> load. `recover()`
  swaps it for a fresh one for the same reason it swaps the inference lock.
* Providers expose `is_loading`. The warm-up supervisor waits while the engine
  is demonstrably building (hard ceiling 180 s) and reserves instance
  replacement for a real wedge. A provider without the flag — every cloud one —
  keeps the short 20 s join unchanged.

**Tests.** `tests/unit/plugins/stt/test_fwhisper_model_load_offloop.py` runs a
heartbeat coroutine during a build and asserts the loop still ticks; recreating
the old inline build makes it tick ZERO times. It also pins one build per
provider under concurrency, `is_loading` honesty, and the fresh load lock after
`recover()`. `tests/unit/dictation/test_warmup_cold_load_patience.py` pins the
four supervisor cases: still-loading is waited for, a cloud provider keeps the
short join, a hang AFTER the build is still a wedge, and an endless build
eventually gives up.

**Lesson.** Two of them. A blocking call inside a coroutine does not just make
one thing slow, it removes the timeout that was supposed to contain it — the
supervisor and the supervised were on the same thread. And "it is taking too
long" is not a diagnosis: replacing a worker only helps when the worker is
stuck, never when it is merely loading. Patience needs evidence, so the engine
had to say which one it was.

## BUG-175: "ask him what Jarvis version 2 can do" answers nothing — the model was handed a wiki tool whose mode is switched off, and the wiki that WAS working sat unused beside it (HIGH, FIXED 2026-08-24)

**Symptom (maintainer, 2026-08-24).** A question typed into the chat, sixteen
seconds of thinking, and one red row: `ultrawiki_ask · what Jarvis Version 2 can
do — FAILED 0.0s`. No answer, no second attempt. The maintainer's reading was
that "the wiki tool is broken"; measured, the wiki was fine and every part of
this failure was in the tool set.

**Timeline (`data/jarvis_desktop.log`).**

```
09:04:12  ultrawiki: service constructed (mode disabled — dormant)
10:13:58.280  gemini-live: function call(s) ultrawiki_ask_55466fa18d
10:13:58.737  POST http://127.0.0.1/api/ultrawiki/ask "HTTP/1.1 409 Conflict"
10:13:58.739  gemini-live: tool response sent (model_generating=True)
```

0.0 s is the tell: nothing was searched. `_require_active` refuses before any
retrieval, by design — `[ultrawiki] enabled = false` in this install.

**Root cause: a tool set that promised what the app had switched off.**
`AppCommandTool.expand()` turned EVERY Command-Registry entry into a callable
tool, unconditionally. Four of them are `/api/ultrawiki/*` routes
(`ultrawiki-ask`, `people-list`, `person-profile`, `identity-queue-list`), and
that whole family answers 409 while the mode is off. So the model was offered
four tools that could not succeed, with nothing in their schemas to say so —
the registry description reads "Answer one question from UltraWiki evidence",
which is exactly what had been asked for.

Verified on the live app: `GET /api/tools` listed all four alongside the
classic wiki tools. `wiki-recall` was in the same set the whole time and
answers that question from the Obsidian vault in 13 ms (27 entities, hits with
scores). The knowledge was there; the tool set pointed at the one locked door.

This is AP-21 (gate on the capability, never on a name) applied to tools rather
than providers: a tool's presence is a claim that it can answer.

**Root cause, second defect: the section that would not open.** Reported in the
same breath — "sometimes I go into the section and it does not come up". The
Wiki view renders NEITHER body until `GET /api/ultrawiki/status` answers, and
that route probed the search legs on every call: a credential walk plus a
request to the local embedding endpoint. Measured 2026-08-24 against the live
app: 0.7-2.1 s per call, where every other wiki route is under 150 ms
(`/api/wiki/tree` 13 ms, `/api/wiki/health` 66 ms). While the mode is off
nothing renders those legs — only the Ultra body does, and it is not mounted —
so the wait bought nothing. A visit with no remembered mode paid it in full,
staring at an empty section.

The leg probe was only half of it. `UltraWikiService.status()` also ran the
three capability-slot probes on every call - embedding, distill and rerank,
each walking the keyring / `.env` or an endpoint. Removing the leg probe alone
took the route from 2.1 s to 0.9 s and no further; the slots were the rest.
They are read by the Ultra body too, and by nothing else.

**Fix.**

* `jarvis/commands/capabilities.py` resolves a command's `requires` against the
  LIVE config (the mode switch mutates it in place, so a toggle counts at
  once). `AppCommand.requires` is backend-only — the UI and CLI catalogs still
  list every command, because both of those surfaces can turn the mode ON.
  Only the LLM tool set, which cannot, is filtered.
* The gate FAILS OPEN. An unreadable config leaves the tool in place: a wrong
  "available" is caught by the execute-time re-check, while a wrong
  "unavailable" would silently remove the tool for the whole session.
* `RegistryCommandTool.execute()` re-checks before sending, because the mode
  applies live and a session can hold a tool built while it was on. The refusal
  carries a redirect naming `wiki-recall` / `wiki-list` / `wiki-page-read`, so
  the model has somewhere to go inside the same turn instead of ending it on a
  failure — the behaviour `ultrawiki-search` already had and `ultrawiki-ask`
  never did.
* Switching the mode publishes `BrainToolsChanged`, so activation brings the
  four commands BACK without a restart. Without it the gate would have traded
  one missing-tool bug for its mirror image.
* `/api/ultrawiki/status` skips the leg probe while the mode is off and says
  `search_legs.probed: false` with a reason, rather than reporting legs nobody
  measured. `/health` - which grades those legs, and would read an absent
  keyword leg as "search cannot answer" - asks for the probe explicitly.
* `status(probe_slots=False)` skips the credential probes on the same
  condition. The stored CHOICES still come back, so a client can always say
  which provider was picked; it is simply not told "ready" about a mode
  nobody switched on. Measured end to end on a dev backend with the mode
  off: **2.1 s -> 0.22 s**, level with `/api/wiki/tree` (0.26 s) and
  `/api/wiki/health` (0.24 s) on the same process.

**Tests.** `tests/unit/commands/test_capabilities.py` pins mode-off, fail-open,
that the steer names tools that actually work, and — the one that matters in a
year — derives the gated set from the registry's own PATHS, so a future
`/api/ultrawiki/*` command that forgets `requires=` fails the build.
`tests/unit/plugins/tool/test_app_command.py` pins the withheld four, their
return when the mode is on, and the mid-session steer that sends nothing to the
endpoint. `tests/unit/web/test_ultrawiki_routes.py` pins that status does not
probe while off, that `/health` still does, and that both toggle directions
publish `BrainToolsChanged`.

**Lesson.** A tool in the set is a promise. The model cannot see a config flag,
so anything the app has switched off must be absent, not merely failing — and
when it does fail anyway, the failure has to name the door that IS open. Both
defects here came from the same habit of answering "is it wired?" when the
question was "can it answer?".

## BUG-176: asked about the calendar, answered about Vercel — the retry after a blocked answer pointed at evidence that had just been thrown away (HIGH, FIXED 2026-08-24)

**Symptom (maintainer, 2026-08-24).** "Jarvis keeps producing context-less
nonsense. I clearly asked what is in my calendar and he comes back with
Vercel." The transcript shows the tool ran: a `Google Calendar` badge, 1.2 s,
right under the question. What was spoken:

> "I tried to fetch your recent Vercel deployments, but it looks like there's
> an issue with the Vercel integration. I can try again in a moment, or you
> could check the plugin settings in the UI to make sure your account is still
> connected correctly."

No Vercel tool ran that turn. Nothing about Vercel had been said in the call.

**Forensics (`data/sessions.db`, session `95b85f31`, turn 0).** The tool layer
was blameless end to end:

```
ActionProposed  {"tool_name": "google_calendar", "action": "list_events"}
ActionExecuted  {"tool_name": "google_calendar", "success": true,
                 "duration_ms": 1232,
                 "output_preview": "{'events': [{'summary': 'Busy',
                                     'start': '2026-08-24T05:15:00Z', ...}]"}
```

Right tool, right arguments, real events back, no timeout and no exception.
`tool_calls_json` for the turn is `["google_calendar"]` and nothing else.

**Timeline (`data/jarvis_desktop.log`).** The turn was answered twice:

```
10:57:05.792  gemini-live: function call(s) google_calendar
10:57:07.031  gemini-live: tool response sent for google_calendar
10:57:09.155  gemini-live: text input sent (527 chars)
10:57:09.156  realtime[95b85f31] retrying one blocked output in English
10:57:09.178  gemini-live: interrupted (our own text input; the generation is
              superseded) — audio=4.8s transcript=82 chars function_calls=1
10:57:09.946  gemini-live: generation started
10:57:10.307  first_audio        <- the Vercel answer
```

The first generation was grounded and already 4.8 s into its audio. The output-
language gate blocked it at the speech boundary, and everything the user heard
came from the second one.

**Root cause: a prompt that pointed at state the same handler had just
destroyed.** `_direct_tool_result_retry_prompt` said, verbatim, "Use only the
function result that is already present in this conversation … Do not call any
function and do not repeat the action." That is an ASSUMPTION about provider
state, and on this path it is false in three ways at once:
`_handle_output_language_mismatch` had already called
`_retire_active_provider_response()`, drained the gate and cleared
`_output_transcript`; Gemini Live marks a generation abandoned this way as
superseded server-side; and the last clause closes the only remaining route to
the facts. The model was told to answer from a result it could not see, and
forbidden to fetch one.

A model in that position does not fall silent. It substitutes the most
plausible neighbour in its declared tool set — `vercel` sits in the same
catalog, and its own not-connected string ("connect it in the Plugins view")
is the shape the spoken sentence copied. The Vercel name is a symptom of an
empty context, not of a mis-routed call.

**Not a one-off.** The same `retrying one blocked output` →
`dropping a reply the provider abandoned` pair fired three times that morning
and three times the day before. 10:25:21 (session `6f51622a`): the user asked
for the day's date and a morning briefing, tools `["run-skill",
"google_calendar", "gmail", "search_web"]` all ran, and the spoken answer was
"I've checked your active missions, and currently there are no background tasks
running." Two of three blocked turns drifted to an unrelated topic.

**Fix (`jarvis/realtime/session.py`).** The evidence travels WITH the request.

* `_tool_grounding_block()` renders the turn's own `_direct_tool_results` plus
  the user's question — deterministic, no LLM (AP-11). Nothing new is
  disclosed: each payload already crossed the wire to this model as the
  function response for its own call, bounded by `_bounded_result`.
* Both retry prompts carry a closed-world rule: the block is the COMPLETE
  record of the turn, no service, integration or topic outside it may be
  named, and a question the results do not answer is reported as unanswered
  rather than filled in.
* A tool-less turn gets its blocked answer handed back verbatim
  (`_output_language_blocked_reply`, captured before the teardown clears it).
  That turns "repeat the same answer" from an impossible request into a
  translation.
* Tools ran but nothing was retained (a legacy bridge): the retry is refused
  outright and the turn takes its deterministic local line, because "answer
  from the function result" with no result in reach is exactly the prompt that
  invents one.

Transports that genuinely retain the result see the historical prompt
unchanged: an empty grounding block appends nothing.

**Regression guard.**
`tests/unit/realtime/test_blocked_answer_retry_stays_grounded.py` — the retry
for a calendar turn must contain the calendar data and the closed-world rule;
a turn with no retained result must not be retried at all; a tool-less turn
must get its own wording back.

**Open, not fixed here.** The gate flagged an English reply in an English call,
so this turn's block was itself a false positive. `validate_output_language`
(`jarvis/core/turn_language.py`) is a pinned contract behind a blocking guard
(`test_turn_language`) and was deliberately left alone: the recovery has to be
safe whether the flag is right or wrong, and now it is. Separately, the home
surface showed "Thought for 2m 18s" for a turn whose measured `think_ms` was
3181 and whose total latency was 11.7 s — a duration computed independently in
`TurnSteps.tsx`, unrelated to this bug.

**Lesson.** A prompt may not point at provider state it cannot guarantee. When
a recovery tears down a response, whatever the replacement must be grounded in
has to be re-supplied by the side that still holds it — and the request has to
name its evidence as complete, or the model will quietly widen it. "Use the
result already in this conversation" is a promise about someone else's memory.

---

## BUG-177: the Costs section bills Agentic IDE at 37 000 USD and the speech area is empty — Codex cache hits priced twice at full rate, forks counted again, and the speech meter had not run yet (HIGH, FIXED 2026-08-24)

**Symptom (maintainer, 2026-08-24).** The Costs section's Agentic IDE area
showed a subscription-equivalent of ~40 000 USD, 17.6 BILLION input tokens on
`codex-cli`, and a 3.7-billion-token bucket with no model name. The speech
area (`jarvis-voice`) showed nothing at all.

**Evidence.** Straight from the index (`data/cli_usage_index.db`):

* The biggest Codex row: `tokens_in=334 298, tokens_cached=332 544`. OpenAI's
  usage object reports cache hits INSIDE `input_tokens`; Anthropic reports them
  BESIDE it. One rule for both meant Codex input was counted at full price
  including the 99 % that was a cache hit, and the cached share was then
  ignored by the pricer on top.
* One forked Codex thread: 1 787 `token_count` records written within 100 ms
  of the fork, all before any `turn_context` (hence no model). A fork or
  resume starts a NEW rollout file and replays the parent's entire history.
  The dedup key was the byte offset in the file, which is new for a copy, so
  every fork counted its parent's whole bill again.
* `SpeechUsageRecorded` rows in `sessions.db`: 0. The meter shipped at 16:34;
  the desktop app had been running since 09:58. Not a defect — the code had
  never executed. It also only ever fills from the classic STT → brain → TTS
  path; a speech-to-speech realtime model has no separate ears or mouth.

**Fix.**

* `jarvis/costs/cli_usage_index.py` — Codex `tokens_in` is
  `input_tokens - cached_input_tokens`; the dedup key is
  `session_id + total_token_usage` (the running total is monotonic within a
  lineage, and a fork keeps its parent's `session_id`), so a replay collapses
  onto the original; the insert lets a row with a model fill one indexed
  without. `PRAGMA user_version` = 2 drops an index built under the old rule
  and rebuilds it from the transcripts (~2 minutes for 3 300 files here).
* `jarvis/brain/cost.py` — `calculate_cost_usd` takes `tokens_cached` and
  bills it at `cache_read_fraction(model)` of the input rate (10 %, Gemini
  25 %). Before, cache reads were priced at zero — which also hid most of a
  Claude Code bill (7.9 billion cached tokens on this machine).
* `jarvis/costs/model.py` / `sources.py` — `price_entry` carries the cached
  count through for the CLI and agent-chat readers.
* Result on the same data: Codex 37 687 → 2 901 USD, the model-less bucket
  3.7 billion → 0.4 million tokens, Claude 2 535 → 4 278 USD (cache reads now
  counted). Agentic IDE total 7 623 USD API-equivalent over the seat's life.
* `costFormat.ts` — the areas are regrouped the way the maintainer reads
  them: "{name}" is the assistant (every brain, whichever channel: realtime,
  tool model, pipeline, agent chat, missions), "{name} Voice" is the speech
  layer alone (STT + TTS), Agentic IDE unchanged. Order: Overall · {name} ·
  {name} Voice · Agentic IDE.

**Lesson.** Two vendors, one word, two meanings. "input tokens" is a superset
of cache hits at one vendor and disjoint from them at the other, and a reader
that normalises both into one column has to know which it is holding. And a
dedup key that is a position in a file is only an identity for that file —
anything a tool can copy needs a key made from the content.

## BUG-178: a dictated prompt never appears in a Tauri/Electron coding-agent app — Jarvis reported `inserted` after a Ctrl+V nobody answered, and restored the old clipboard 120 ms later (HIGH, FIXED 2026-08-24)

**Symptom (maintainer, 2026-08-24).** Dictating into a terminal pane of a
Tauri-based coding-agent app (xterm.js inside WebView2): the history shows
`inserted · clipboard+ctrl_v` for every dictation of the day, the pane shows
nothing. Ordinary text fields on the same machine were fine.

**Evidence.**

* `jarvis/dictation/insert.py` wrote the text, sent Ctrl+V, slept 120 ms and
  wrote the PREVIOUS clipboard back. `inserted` was inferred from "the chord
  went out without error" — there was no observation of the target at all.
* The app's own log records paste round-trips of 0.7–21 s on this machine
  (eight agent panes running). Its paste goes through an async IPC bridge
  (`tauri-plugin-clipboard-manager read_text`), so a read after the 120 ms
  restore hands it the previous clipboard, not the dictation.
* Jarvis's own Agentic IDE needed a bridge for the same reason
  (`AgenticTerminal.tsx`: xterm.js reads Ctrl+V as `^V` and cancels the
  browser's paste; nothing arrives). An app without such a bridge ignores the
  chord entirely — and again nothing reports it.
* Measured with a delayed-rendering clipboard offer: the system asks the owner
  to render ONCE. On this box `msrdc.exe` (Remote Desktop clipboard sync)
  reads within 5 ms of every write, so the text is cached before the chord is
  sent and a later paste leaves no trace. A refused render removes the format
  (later readers get nothing), so watchers cannot be starved either.

**Root cause.** Insertion claimed success from the absence of an error, and
restored the clipboard on a timer that assumed a synchronous reader.

**Fix (`jarvis/platform/clipboard_offer.py`, `jarvis/dictation/insert.py`).**
On Windows the text is offered with delayed rendering and every reader is
recorded with its process name. *Sighted* host (nobody read during the settle
window — the default install: no RDP client, clipboard history off): a paste is
proven by the target's read and the clipboard is restored right then; silence
proves the chord was not a paste there, so Ctrl+V → Ctrl+Shift+V →
Shift+Insert are tried and, when none is answered, the text is typed with
line breaks as Shift+Enter (Enter would submit a prompt). The route that
landed is remembered per executable. *Blind* host (a watcher consumed the
render): exactly one chord, nothing inferred from silence — a guessed second
chord would paste the prompt twice — and the previous clipboard comes back
after a 2 s grace, only if the dictated text is still on it. macOS/Linux keep
the plain path (`docs/os-parity.md` P-25).

**Lesson.** "Sent without error" is not "arrived". When the only proof lives
in another process, either measure it (here: who read the clipboard) or say
so — and never let a fallback fire on the absence of evidence when the
fallback's failure mode is doing the thing twice.

---

## BUG-179: nothing in the app can be selected or copied — every window was created on pywebview's kiosk default (HIGH, FIXED 2026-08-24)

**Symptom.** Drag the mouse across an agent's answer in the Agentic IDE chat
and nothing highlights. Same on the timeline, the tool rows, the receipts,
every view in the app, on every platform. Copying a path, an error message or
a block of generated code out of Jarvis is impossible with the mouse. Typing
still works, which is why it reads as a quirk of one view rather than a window
setting.

**Root cause.** `webview.create_window()` takes `text_select=False` by default,
and on that default pywebview injects `body {-webkit-user-select: none;
user-select: none; cursor: default}` into every page it loads
(`webview/js/customize.js`), on Windows, macOS and Linux alike. No window in
this repo ever passed the flag, so the whole app ran as a kiosk. `user-select`
blocks only mouse SELECTION, not keyboard input — hence the app feeling 95 %
normal and the missing 5 % being exactly copy and paste.

**Fix.**
* `jarvis/ui/desktop_app.py` — `TEXT_SELECTABLE`, spread into the kwargs of
  every `create_window` (main window, tray-reopened main window, detached
  view) — and `jarvis/ui/shell/shell.py` pass `text_select=True`.
* `src/index.css` states the app's own policy at `html body`. Specificity, not
  order, is what makes that stick: the injected `<style>` lands LAST in the
  head, so an equally specific `body` rule would lose to it, and a shell that
  was not updated (an older install, a different frontend host) cannot
  re-impose the kiosk. Chrome that is pressed rather than read — buttons,
  tabs, switches, menu items — opts back out, so dragging across a toolbar
  does not paint it blue.

**Lesson.** A library default that silences an interaction is invisible in
review: nothing in the diff is wrong, the flag was simply never named. When a
window frame can change what the page is allowed to do, the page states its own
policy as well, so the app does not depend on every shell agreeing with it.

---

## BUG-181: clicking Restart sometimes only shut the window down (HIGH, FIXED 2026-08-25)

**Symptom.** Click Restart (top bar, Settings, the permissions banner). The
window closes. Sometimes nothing comes back. A Start-menu click later starts
it normally. Intermittent: the same button often works.

**Root cause.** Two cooperating defects, both visible in `jarvis_desktop.log`
around a live restart (2026-08-25 10:52:56 and 16:06:21):

1. The restart helper (`jarvis.ui.relauncher`) spawned
   `python -m jarvis.ui.web.launcher` and treated "that PID stayed alive for
   5 seconds" as proof the app came up. On Windows the launcher immediately
   re-execs through `PersonalJarvis.exe` (taskbar branding) and the python
   PID exits in ~40 ms. The helper read that as a bounce off the
   single-instance lock and spawned two more copies. Three branded processes
   then raced: the first still booting (lock taken, no window yet), the
   later ones hitting `_recover_from_already_running` ("lock is held but the
   holder is not responding"). One of them could focus, bounce, or — if the
   user confirmed, or if Job-Object kill-on-close took the helper with the
   dying parent — leave nothing running.
2. The helper was spawned `DETACHED_PROCESS | CREATE_NO_WINDOW` without
   `CREATE_BREAKAWAY_FROM_JOB` and without redirecting stdio. A parent
   inside a Windows Job Object (Windows Terminal, some Start-Menu launches)
   killed the helper on `os._exit`; invalid stdio could kill a child on its
   first log line. Both look like "it only shut down".

**Fix.**
* `build_launch_command` on Windows launches the existing branded exe
  directly and sets `JARVIS_BRANDED_LAUNCH=1` so it does not re-exec.
* `_new_instance_settled` succeeds when the admin port answers, not only
  when the spawn PID stays alive — a branded hand-off is success, not a
  bounce, so the helper does not spawn extras.
* `detached_creationflags` includes `CREATE_BREAKAWAY_FROM_JOB` (retry
  without it on WinError 5). Stdio is redirected to `DEVNULL`.
* `_recover_from_already_running` waits out a holder that is only seconds
  old and has no window yet, instead of offering to kill a boot in progress.

**Tests.** `tests/unit/ui/test_relauncher.py` (handoff does not respawn,
breakaway retry, branded argv) and
`tests/unit/ui/test_launcher_boot_trace.py` (young holder is waited for).

---

## BUG-180: the app "takes minutes to start" — every launch that never reached a window was mute, and a stuck earlier instance turned every click into a silent exit (HIGH, FIXED 2026-08-25)

**Symptom.** Click the Start-Menu shortcut, wait, nothing. Click again,
nothing. Five to eleven minutes later a window appears after yet another
click, and the machine was idle the whole time. `data/jarvis_desktop.log`
shows the previous instance shutting down at 09:58:24 and the next line at
10:09:16 — a hole with no explanation. Measured from the log, the boot itself
takes ~2.5 s to a painted window and ~8 s to a usable voice, so the wait was
never the boot.

**Root cause.** Three things stacked, all invisible by construction:

1. The file log sink was installed by `DesktopApp.__init__`, i.e. AFTER the
   branded re-exec, the elevation check, config, the control key and the
   single-instance lock. A launch that ended anywhere in that stretch ran
   under `pythonw.exe` — no console, no stderr — and left no trace. The one
   footprint of the 10:02:41 attempt is the autostart shortcut it rewrote
   (that write sits just before the lock); the log never heard of it.
2. `_run_desktop` answered a held lock with `print("already running")` to a
   stderr that does not exist, then `focus_existing_instance_robust()`, then
   exit 3. When the holder had no window (a previous instance whose teardown
   wedged after its accept socket died — the `_start_serving ... self._sockets
   is not None` AssertionError in the log — or a lock the zombie eviction
   could not reclaim), focusing found nothing and the launch simply ended.
   Every further click did the same until the stuck process died on its own.
3. A restart that failed (`jarvis.ui.relauncher.main` giving up after three
   spawns) returned 1 from a windowless helper — also nowhere.

**Fix.**
* `jarvis/ui/desktop_log.py` (new) — the sink + writer thread moved out of
  `desktop_app` so the launcher can install them from its first millisecond
  without the ~400 ms config import; `desktop_log_path()` mirrors
  `config.DATA_DIR` exactly (parity test). `launcher._install_boot_trace`
  writes `launcher: start pid=… argv=…`, the branded-exe hand-off, and every
  already-running decision into the same `jarvis_desktop.log`.
* `launcher.main` is now a crash-reporting wrapper around `_main`: an
  exception before the window is logged with its traceback and shown in a
  native box (`_report_startup_failure`).
* `launcher._recover_from_already_running` — a healthy holder is focused as
  before; a holder WITHOUT a window becomes one native Yes/No question ("It is
  probably stuck. Stop that process and start fresh?"). Only an explicit Yes
  terminates it and takes the lock; No, no display, no helper and any error
  all count as No. `jarvis/ui/native_dialog.py` (new) carries `ask_yes_no` and
  `show_error_dialog` for Windows (Win32), macOS (osascript) and Linux
  (zenity/kdialog, nothing without a display).
* `desktop_app._terminate_pid` logs each step and, on Windows, falls back to
  `taskkill /F /T` for the whole process tree when psutil's kill did not take —
  a wedged instance's child (inference server, ConPTY host) is often what
  keeps it alive.
* `relauncher.main` reports a failed restart (`_report_restart_failure`):
  stderr when readable, a native box when not.
* Two things the live test of the fix surfaced, fixed alongside: the log
  writer thread is now drained at interpreter exit (`atexit`), so the last
  line of a process that ends right after writing it — the bounce itself — is
  no longer lost; and `_default_lock_holder_health` treats a port that
  CONNECTS but answers late as a busy holder (return True) rather than a
  zombie — only a refused connection (nothing bound) still counts as dead.
  Before, a second click during the heavy part of a boot waited 6 s on four
  1-second probes and was one timeout away from terminating the very instance
  it was supposed to bring forward.

**Tests.** `tests/unit/ui/test_launcher_boot_trace.py`,
`tests/unit/ui/test_native_dialog_and_eviction.py`.

**Not changed, on purpose.** The boot path itself. The 0.5 s spent by the
`pythonw` parent before it re-execs through the branded exe is the price of a
branded taskbar button (`icon_utils.maybe_reexec_through_branded_launcher`);
a shortcut that targets the in-venv branded exe directly skips it.

---

## BUG-183: local speech-to-text silently disappeared after a restart because the launcher shortcut had been repointed at a Python without the engine (HIGH, FIXED 2026-08-25)

**Symptom.** Local voice transcription "sometimes" stops working after a
restart. The window opens normally, the models are on disk, and nothing
announces a change — until dictation starts returning errors from a cloud
provider. `data/jarvis_desktop.log`:

```
Interpreter: …\Programs\Python\Python312\pythonw.exe (Python 3.12.10)
Local STT is selected but the faster-whisper engine is not installed on this
  host; using 'openrouter-stt' instead.
final dictation transcription refused (STTHTTPError: OpenRouter STT failed:
  OpenRouter account out of credit) — not retrying
🎙️ dictation ended (0 chars, … LOST 24.3s after 6 failed call(s))
```

Meanwhile the API-Keys health panel kept reporting `faster-whisper` as the
active recognizer, and the package was demonstrably installed — in three of
the machine's four Python installs.

**Root cause.** Three layers, each individually reasonable:

1. **Whether the engine exists is a property of the PROCESS, not the host.**
   `jarvis` is importable from every install carrying an editable `.pth`,
   so the app boots fine anywhere; only the heavy voice extras
   (`faster_whisper`, `ctranslate2`, `torch`) live in one of them. The log
   line said "not installed on this host", which sent the reader looking for
   a package that was present and hid the real question — *which* Python.
2. **The launcher shortcut was repointed by the wrong interpreter.**
   `ensure_desktop_integration` rewrites the Start-Menu and Desktop entries
   on every boot, gated by `_interpreter_can_open_a_window()`. That guard was
   added on 2026-08-16 after a window-less interpreter stole the entry — but
   it asks about ONE capability. Python 3.12 had pywebview and no
   `faster_whisper`, so it passed, took the shortcut, and every later launch
   inherited the interpreter that cannot transcribe locally. Self-reinforcing:
   the same repair had already been made once, on 2026-08-09, and drifted back.
3. **The health panel and the pipeline answered the question differently.**
   `build_stt_from_config` knew about the engine-missing crossing;
   `_active_stt` (what the UI renders) only knew about the *keyless* one. So
   the app displayed a recognizer it had refused to build, and the swap became
   visible only when the borrowed cloud account ran out of credit — a
   privacy problem too: someone who chose on-device speech was uploading it.

**Fix.**
* `jarvis/plugins/stt/__init__.py`: `_resolve_effective_stt` is now the ONE
  answer to "which recognizer is in front", covering both crossings;
  `resolve_effective_stt_provider` is what the health panel reads.
  `local_stt_unavailable_reason()` names `sys.executable` instead of "this host".
* `jarvis/ui/web/provider_routes.py`: `_active_stt` asks that resolver, so the
  panel can no longer name a provider the factory declined to build.
* `jarvis/ui/icon_utils.py`: `_interpreter_may_own_the_launcher_shortcut()`
  gates both shortcut writers on window *and* speech capability. Conditional on
  the user's CHOICE, never on the package — a cloud-first base install has no
  on-device engine and must still own its own shortcut.

**Tests.** `tests/unit/plugins/stt/test_effective_stt_parity.py`,
`tests/unit/ui/test_start_menu_shortcut_target.py::TestSpeechCapabilityProbe`,
`::TestShortcutWriterRefusesASpeechLessInterpreter`.

**Also fixed here.** `tests/unit/plugins/stt/conftest.py`: the Vertex keyless
ADC probe reads the machine rather than the test's credential double, so ten
cross-family assertions passed in CI and failed on any host with `gcloud` ADC
configured — the AP-28 shape.

## BUG-182: the desktop app "does not start" because a leftover headless instance holds the lock and focus is rejected as CSRF (HIGH, FIXED 2026-08-25)

**Symptom.** Clicking the Start-Menu shortcut or `run.bat` does nothing.
`data/jarvis_desktop.log` shows:

```
POST http://127.0.0.1:47821/api/window/focus "HTTP/1.1 403 Forbidden"
launcher: Jarvis lock is held but the holder is not responding. — NO window found
Personal Jarvis reports that it is already running, but no window could be
found and the running process is unknown.
```

**Root cause.** Three things stacked:

1. A `--headless` leftover (smoke / agent / `run.bat --headless`) bound
   port 47821 and took `data/jarvis.lock`. Headless is allowed to be
   primary when it is the sole instance, but it never wrote the
   `.jarvis-running` sidecar, so the desktop launch could not name a pid.
2. With no sidecar, `acquire_single_instance_lock` treated the holder as a
   zombie, waited 5 s, and raised "holder is not responding" — even though
   `/api/health` was 200.
3. Recovery POSTed `/api/window/focus` with no Origin and no Bearer.
   Loopback open-access lets the request through authentication, then the
   CSRF check (`origin_is_trusted(required=True)`) rejects a missing Origin
   as 403 `Untrusted Origin header.`. No window exists on a headless
   holder anyway, and with no pid the Yes/No eviction dialog never appeared.

**Fix.**
* Headless writes the sidecar when it claims the lock, and removes it on
  shutdown (`launcher._claim_headless_primary_lock(port=…)`).
* A held production lock with a missing sidecar looks up the process bound
  to the admin port instead of waiting 5 s.
* Recovery names the holder from sidecar, then the error text, then the
  listening pid, so the "stop and start fresh?" dialog can actually run.
* The focus ping sends the Control-API Bearer (loopback Origin only if no
  key exists yet). A 200 from a headless holder with `focused=false` is
  not treated as "window raised"; the placeholder route now returns
  `ok=false, reason=no_window`.

**Tests.** `tests/contract/test_single_instance.py`
(`test_missing_sidecar_names_the_listener_instead_of_waiting`),
`tests/unit/ui/test_launcher_boot_trace.py` (pid from error / port),
`tests/unit/ui/test_focus_existing_instance.py`,
`tests/unit/ui/web/test_launcher_primary_instance.py`
(`test_sole_headless_instance_writes_the_sidecar`).

---

## BUG-178: the Costs section could not add up — a stuck filter emptied an area, seats read as "$0.00", Grok Build and three Claude accounts were never counted, and speech metering leaked (HIGH, FIXED 2026-08-25)

**Symptom (maintainer, 2026-08-25).** Overall showed "Total spend $18.41"
over a provider table summing to $6,000+; Agentic IDE showed "$0.00 —
$4,452 on a subscription"; the "{name}" tab showed "0 calls" although it had
worked an hour earlier; "{name} Voice" showed nothing; "Unpriced tokens
16.73M". "The calculations simply do not add up."

**Root causes (four deep-dive audits, one per area).**

1. *Headline.* `CostsView` subtracted the seat-covered amount and showed only
   what an API key was billed. The API-equivalent of everything is the
   number the maintainer asked for; the split is the sub-line now.
2. *Stuck filter.* `pickArea` cleared only surfaces and roles, so a provider
   chosen inside Agentic IDE (`claude-cli`) survived the switch to "{name}"
   where no row can match it. Every row filter is cleared with the area, and
   an empty area under a row filter now says so with a one-click clear.
3. *Missing accounts.* The CLI index scanned `~/.claude` only. Two further
   Claude subscriptions run with their own config directories under
   `agent-accounts/claude/<id>/` and held 396 session files / 6.5 billion
   tokens nobody counted. `_account_roots` adds every managed account and
   every leftover folder of a deleted one.
4. *Grok Build invisible.* `~/.grok/sessions/**/updates.jsonl` (608 MB) had
   no reader. `turn_completed` carries per-turn usage in the OpenAI
   convention (input includes cache reads); `prompt_id` is the identity.
   New `AGENT_GROK` reader; xAI's cache-read discount measured from its own
   `costUsdTicks` is 25 %, not 10 %.
5. *Seats priced as free.* `price_entry` returned `(0.0, "free")` for
   `SUBSCRIPTION_PROVIDERS`. A seat is worth its API-equivalent and is
   labelled `subscription`; "free" is for local engines and `:free` models.
6. *Missions.* `WorkerDraftReady` carries no CLI or model; both live on
   `WorkerSpawned` for the same `worker_id`. 64 drafts (1.78M tokens) were
   unpriced and 189 seat quotes passed as billed. The reader joins the two,
   the CLI's short names (`sonnet`/`opus`/`haiku`) resolve via
   `MODEL_ALIASES`, and the Claude worker's turn count no longer lands in a
   token column.
7. *Codex cache hits twice.* `_agent_chat_entries` summed `input_tokens`
   and `cached_input_tokens` for the Codex runner; only OpenAI-convention
   runners subtract. Same class as BUG-177, second reader.
8. *Voice pipeline cache blind.* `manager.py` read `input_tokens` and
   `output_tokens` only; the plugins report `cache_hit_tokens` and the
   OpenAI plugin counted them inside the input. `BrainTurnCompleted` carries
   `tokens_cached`, the OpenAI plugin honours the canonical contract (input =
   uncached), and grok-4.5 — 82 calls at $0.00 — has a rate.
9. *Speech metering leaks.* `set_stt_provider` and the live language switch
   replaced the metered STT with a bare one; dictation and the realtime
   surface voice were never wrapped; nothing flushed outside a classic turn
   or at session end. All four wrapped; flush before `VoiceSessionEnded`
   and after 20 s of idle.
10. *Index hygiene.* Codex fork replays inserted rows before their file's
    `turn_context` (130 rows / 16.4M tokens with no model) — backfilled from
    the session; rows of deleted transcripts lived forever (446 rows / 88M
    tokens) — pruned when the file's root is readable and the file is gone;
    `kimi-k2.5:cloud` and `codex-auto-review` priced; the report cache now
    keys on the index file too.

**Numbers on the same machine, before → after.** Agentic IDE $4,452 (one
account, Claude only) → $12.6k API-equivalent across two seats, Codex, Kimi
and Grok Build; unpriced tokens 16.7M → agy's 1.4M (the one CLI that writes
no model id, named as such).

**Still open, deliberately.** GLM panes run the Claude binary against z.ai
with no config dir of their own and are counted as Claude Code at Anthropic
rates (12× too high) — a spawn-side fix (own `CLAUDE_CONFIG_DIR`), T3. A
chat turn or mission worker that drives a vendor CLI is counted once by its
surface and once by the index; latent while the chat store holds one turn,
needs the vendor session id persisted on both sides. Per-account
attribution ("which seat burned this") needs an account column. An
index-state endpoint ("63 % of 12.7 GB read") does not exist yet.

**Lesson.** A spend report has as many readers as the app has ways to spend,
and every reader is a place to be wrong in its own way. The audit that found
these was one agent per area with the real databases in front of it, not a
walk through the code.

---

## BUG-179: OpenRouter and Gemini spend nobody could see — twenty callers of the brain protocol never reached the ledger (HIGH, FIXED 2026-08-25)

**Symptom (maintainer, 2026-08-25).** "I have spent money on OpenRouter, I
know it 100 %, and on Gemini too — the report shows almost nothing." The
provider table showed OpenRouter at $0.00 and Gemini at $0.19.

**Root cause.** The Costs section learned about spend only from the surfaces
that happened to record it: a voice turn's `BrainTurnCompleted`, a chat's
`turn_finished`, a mission's draft, the CLI transcripts. Everything else that
calls `Brain.complete()` — dictation polish (its own HTTP clients, not even a
plugin), the wiki curator and session roll-ups, awareness digests, the
mission critic and decomposer, computer-use planning, skill authoring, board
profiles, recap/prompt composers — spent on the configured primary brain
(`openrouter` → `google/gemini-3.5-flash` on this machine) and told no one.
`rg "\.complete\("` outside plugins and tests: 24 files.

**Fix.**

* `jarvis/costs/ledger.py` — one append-only ledger (`data/llm_usage.db`),
  written by a daemon thread off the hot path (AP-9), read by the report.
  `usage_context("wiki")` tags who asked.
* `jarvis/brain/usage_meter.py` — `MeteredBrain` wraps every provider at the
  one door they all come through, `BrainProviderRegistry.instantiate`. The
  usage block a plugin yields is written down with the caller; `__class__`
  forwards so `isinstance` checks keep their answer.
* Surfaces that already keep a richer record tag their calls (`voice-turn`,
  `agent-chat`, `mission-worker`) and the reader skips those tags — the
  ledger row is the audit trail, not a second bill.
* The dictation polish clients parse the usage their responses carry and
  record it as `dictation`.
* New surface `background` / role `background`, shown inside "{name}"; the
  entry's label names the job.
* The section gains a spend switch — all · billed to keys · on
  subscriptions — and "Where it went" says "the 12 most expensive of N ·
  share of the total" instead of reading like a sum.

**Lesson.** A report that depends on every caller remembering to report is a
report of the callers that remembered. Meter where the object is made, not
where it is used.

## BUG-185: a dictation ended in "IDE, Agentic IDE, Agentic IDE, …, Claude, Agentic IDE, Claude, Agentic" — Whisper recited its bias prompt, and the dictionary rewrote nearby common words toward the user's terms (HIGH, FIXED 2026-08-26)

**Symptom.** A 40-second German dictation (2026-08-26 17:24 UTC, Groq
`whisper-large-v3`) landed in the chat with a tail nobody said:
`… der bebeamtlich ist. IDE, Agentic IDE, Agentic IDE, Agentic IDE, Agentic IDE, Claude, Agentic IDE, Claude, Agentic`.
The maintainer read it as the STT dictionary replacing far too much.
Separately, the live dictionary held `GitHuib` (a typo), and every correctly
heard "GitHub" was being rewritten INTO the typo.

**Root cause.** Two independent things.

1. The recitation is not a replacement. `build_stt_from_config` hands the
   dictionary's canonical words to prompt-capable providers as a
   comma-separated Whisper `prompt` — here
   `GitHuib, Grok, Claude, Agentic IDE, Agentic`. Whisper treats the prompt
   as the previous segment and, given a stretch with no speech in it,
   continues the list. The dictation lane's truncation repair
   (`stt_audit: truncation_repairs:1`, four STT calls for two windows)
   re-read a near-silent piece and accepted the result because the merged
   text had MORE tokens than before — the one acceptance rule a hallucinated
   list always passes. The repo already withholds the prompt from the local
   engine for exactly this reason (Bug #8) and never guarded the cloud lane.
2. The fuzzy repair rewrote any token within raw edit distance 1 (2 from
   eight characters) of a dictionary word that shared its first letter. That
   is a spelling neighbourhood, not a sound: "grob" → "Grok",
   "Clause" → "Claude", and "GitHub" → "GitHuib".

**Fix.**

* `jarvis/speech/stt_dictionary.py` — `TranscriptCorrector.strip_prompt_echo()`
  drops a run of dictionary items that closes a finished transcript when it
  holds three or more items, repeats one, or is the whole transcript (two
  items suffice there: a silent piece comes back as the list alone).
  Fragments of multi-word entries ("IDE") may start the run but never count.
  `DictionaryCorrectingSTT._apply` runs it before correction on `text` and
  `raw_text`, skips partials, and logs what it dropped at INFO.
* Same file — the fuzzy repair now compares phonetic folds (`_phonetic_fold`:
  c/k, v/f, ph/f, th/t, ie/i, ei/ai/ey/ay, ou/au, doubled letters, silent h)
  and rewrites only an identical fold, or one edit apart on the fold for
  tokens of eight letters or more. "Veltrok" → "Veltroc", "Klaude" and
  "Cloude" → "Claude" still repair; "grob", "Cloud", "Clause" stay.
* The maintainer's live sidecar: `GitHuib` → `GitHub` through
  `DictionaryStore.update`, so the corrector reloaded on the next utterance.

**Guard.** `tests/unit/speech/test_stt_dictionary.py` — `TestPromptEcho`
(the verbatim tail from the history, a whole-window recitation, a repeat,
and five sentences ending in dictionary words that must survive),
`TestWrapper::test_recited_prompt_is_dropped_before_correction`,
`::test_partial_transcripts_keep_their_tail`,
`TestVocabularyWords::test_fuzzy_repairs_only_what_sounds_the_same`,
`::test_fuzzy_long_tokens_allow_one_sound_apart`,
`::test_fuzzy_never_rewrites_toward_a_misspelt_entry`.

**Lesson.** A decoder bias is text the model will happily read back to you;
whatever goes into a Whisper prompt needs a guard on the way out. And "one
letter apart" is a property of spelling — the person who registers a word is
describing how it sounds.

## BUG-186: "There you go, I've just started some cool music on YouTube Music" — over a silent machine. The tool pressed a media key blindly, called that a resume, and the voice sold it as done (HIGH, FIXED 2026-08-26)

**Symptom.** Gemini Live, 2026-08-26 19:50, "just use like the Google Music
plugin and play some cool music." The trace shows `youtube_music · play` in
0.0 s, then the voice: "One moment, let's get that music playing for you.
There you go, I've just started some cool music on YouTube Music. Just so
you know, that just toggles play or pause …". No browser opened, nothing
played. The maintainer's reading: the assistant confirms things it did not
do.

**Root cause.** Three layers agreed on a lie, and the model was the most
honest of them.

1. The model called `play` without a title (`{"action": "play", "type":
   "song"}`) — the schema says a bare `play` resumes what is paused, which is
   a fair reading of "play some music" when nothing names a song.
2. `youtube_music_rest._control("play")` on this box: the Windows media
   session cannot READ (`winrt-*` was added to the `[desktop]` extras after
   the app's interpreter was last installed — `pywebview`, `pystray`, `pycaw`
   present, `winrt` absent), so `cap.can_read` was false, no "is anything
   paused" check ran, the play/pause TOGGLE key went to whatever the OS
   considered current, and the result was
   `{'ok': True, 'action': 'play', 'note': 'Sent as a media key: this toggles
   play/pause blindly.', 'resumed': True}` in 17 ms.
3. The system prompt forbids claiming an outcome "unless a successful
   function result explicitly supports that claim" — and this one did. The
   realtime honesty guard (`action_honesty.py`, `_has_execution_evidence`)
   only fires when NO tool ran; a tool that ran and said `ok` is evidence by
   construction. The model read the note, added its "just toggles" caveat,
   and still opened with "There you go, I've just started".

**Fix.** Truth comes from the tool; the bridge makes it audible.

* `jarvis/plugins/tool/youtube_music_rest.py` — `_control` splits at
  `cap.can_read`. A readable session (background player, WinRT, MPRIS,
  nowplaying-cli) answers `verified: true` as before. A blind machine goes
  through `_control_blind`: `play` is REFUSED unless this tool itself sent a
  blind pause just before (the key is a toggle; with nothing known paused it
  starts a random tab or stops a running one), and `play()` then answers its
  existing "say what to play" error without pressing anything. Pause, next
  and previous still go out as best effort but come back
  `verified: false` with a note that nothing on this machine can confirm the
  effect. `_open_and_confirm` and the background-player path carry
  `verified` = `playback_confirmed`; a browser opened on a machine that
  cannot read the session is `verified: false` too.
* `jarvis/realtime/tools.py` — `_bounded_result` sees
  `output["verified"] is False` on a successful call and adds
  `unverified: true` plus an `instruction` ("This result is an ATTEMPT, not a
  confirmed outcome … never say it is done, playing, running, or started.
  Offer the next step in the same breath") — the same field the model
  already obeys for confirmation questions. It survives truncation.
* `jarvis/realtime/session.py` — the tool-use system prompt names the
  contract: a result marked unverified is an attempt, not a confirmation.
* This box: the four `winrt-*` packages installed into the app's Python
  (needs an app restart to take effect); with them the plugin reads what
  plays and resumes only a real pause.

**Why not gate it in the honesty guard.** Withholding the tool from
`_executed_tool_names` would make `_recover_unbacked_action_claim` replace
the whole answer with "I did not start an action for that" — false the other
way (a key WAS sent) and a dead stop in the conversation. An unverified
result is data the model can speak honestly at full speed; the fix keeps the
turn's timing untouched (the tool still answers in milliseconds) and changes
only what is true in it.

**Regression tests.**
`tests/unit/plugins/tool/test_youtube_music_rest.py::test_blind_play_with_nothing_known_paused_presses_nothing_and_says_so`,
`::test_blind_pause_is_an_attempt_not_a_result`,
`::test_blind_play_after_a_blind_pause_is_a_resume_attempt`,
`::test_readable_session_control_is_verified`,
`::test_blind_play_of_a_song_opens_the_link_but_never_claims_playback`;
`tests/unit/realtime/test_tools.py::test_unverified_outcome_carries_the_attempt_not_outcome_instruction`,
`::test_verified_outcome_carries_no_instruction`.

**Lesson.** `ok: true` is a claim about the ACTION, and the voice speaks
about the OUTCOME. Every tool that acts without being able to look owes the
model the difference in a field it cannot miss — a note in prose is a caveat
the model will politely paraphrase after the good news.
## BUG-188: Local models says every model is "not installed" and a job cannot be changed — one sweep taken while Ollama was still booting became the cache both ends read from (HIGH, FIXED 2026-08-27)

**Symptom (maintainer, 2026-08-27 10:09).** The Local models section showed
`ornith:9b` with a trailing `(not installed)`, and under it the sentence
"Nothing installed fits this job yet; this download is the pick." Picking a
different model in the dropdown appeared to do nothing — "it always switches
back to that odd one, even when you choose something else." Fifteen models
were on the server at that moment, `ornith:9b` among them.

**What was actually true.** Nothing was broken in the write path. Read live
during the investigation, the backend was correct on every count:

```
GET …/local-models/overview?fresh=1   source: live
  inventory error: None      models: 11
  chat  current='ornith:9b'  installed=True  qualifying=[10 tags]
PUT …/local-models/roles/chat  {"model":"qwen3.5:4b"}   -> ok
  jarvis.toml            brain.providers.ollama.model = "qwen3.5:4b"
  scripts/config-soll.json  brain.providers.ollama.model = "qwen3.5:4b"
```

The pick was written, the drift baseline followed it, and a re-read answered
the new value. The screen was simply not showing the server.

**Cause — a truthful answer cached as a lasting one.** `build_overview` runs
its sweep whatever the server's state; when Ollama does not answer it returns
a payload with an empty inventory and `inventory.error` set. That is the right
answer to "what can I see right now" and a ruinous one to keep, and it was
kept twice over:

* `ollama_overview._refresh` handed every payload to `save_snapshot`, so the
  offline sweep replaced the good file in `data/local_models_snapshot.json`.
  `get_overview` paints a disk snapshot for `STALE_AFTER_S` — 24 hours.
* `useOverview` mirrored every payload into `localStorage`, and reads it back
  as react-query's `initialData` — which survives a failed refetch, so it
  outlives the outage that produced it.

Opening the section while Ollama is still booting is enough. Everything
downstream then follows from an empty inventory: `RoleState.qualifying` is
empty, so each role reads `installed: false` and its shortlist falls back to
"nothing installed fits this job yet".

**Why the dropdown looked stuck.** `RoleLedgerRow` built its options from
`row.qualifying` plus the configured tag. With `qualifying` empty that list
holds exactly ONE entry — the tag already configured. There was nothing else
to select; the "reset" the maintainer saw was a picker that had never offered
an alternative. An unprobed download (`probed: false`, no capabilities) fell
out of the list the same silent way. The Simple page compounded it: the
picker lived behind a "Change" button, two clicks from the row.

**Fix.**

1. `ollama_overview.is_paintable()` — a payload whose inventory carries an
   error is never written to the snapshot, and one already on disk is dropped
   on read instead of painted. The previous, honest snapshot stays.
2. `localModelsSnapshot.isPaintable()` — the same guard on the browser side,
   on both write and read.
3. `RolePicker` offers EVERY installed download, grouped "Fits this job" /
   "Other installed models", so there is always something to pick and an
   unprobed model is visible rather than dropped.
4. The picker is on screen for every job at every detail level; `advanced`
   adds capability badges, Tune and the read-only jobs, it never removes a
   control.
5. A "Refresh" button in the section header clears the painted snapshot and
   every cached answer, then reads the server again — the recovery a user can
   reach without knowing what a cache is.

**Lesson.** A cache entry is a claim that outlives the moment it was taken.
"The server did not answer" is true when measured and false ten seconds
later, so it may be returned but never stored. And a control whose options
come from a filtered list needs an answer for the empty case: offering one
option is indistinguishable, to the person using it, from ignoring the click.

## BUG-189: the desktop app hung in every section and sometimes did not paint at all for the first minutes after a cold Windows boot — the event loop was stopped for 15 s at a time behind an SDK import and a thread start (HIGH, FIXED 2026-08-27)

**Symptom.** Windows booted at 09:57:21, Jarvis auto-started at 09:58:34.
Every section hung, views did not render, clicks and keystrokes went
nowhere, the machine felt stuck; the maintainer quit at 10:06 and relaunched,
and the second boot was fine. The log of the first boot, side by side with
the second: wake-word ready after 54.3 s (7.6 s), the German Vosk model
loaded in 18.5 s (0.7 s), Vertex credentials in 26.6 s (2.1 s), the
voice-ready watchdog fired at 45 s, 14 of 22 CLI probes were still running
at the 44 s sweep ceiling, the mic capture thread starved (`Mic stall
detected (6.4s without a frame)`, `5.0s of audio never reached the
recording`), the WebView's own UI thread stalled for 3 s — and twice
`Event loop STALLED for 15.0s — every WebSocket, HTTP route and brain turn
is blocked behind this call`.

**Root cause.** A cold OS boot (empty disk cache, Windows still starting
its own autostart) met the app's own start storm: 22 CLI probes at once
(up to 44 child processes), the agent chat's provider health sweep with a
live one-token request per provider, Ollama plus a 9B model, three MCP
servers, the wake models, Vertex ADC — and then the Agentic IDE resuming
ten coding-CLI panes within two seconds (eight `claude.exe`, each reading a
session transcript and starting its MCP servers), six more at 10:05. Under
that load two things blocked the ONE thread that answers every click:

1. `claude_api._ensure_client` ran `from anthropic import AsyncAnthropic` —
   pydantic model generation for a few hundred types — synchronously inside
   `complete()`, on the event loop, on a cold disk: 15 s. The same lazy
   import sits in every OpenAI-shaped plugin, and `provider_registry._load`
   (entry-point discovery that imports EVERY plugin) had the same shape in a
   stall stack the day before. Reached from the agent chat's boot-time
   health sweep and from the wiki extractor.
2. A synchronous FastAPI route went through Starlette's
   `run_in_threadpool` → `anyio.to_thread.run_sync` → `WorkerThread.start()`
   → `Thread.start()` waits for the new thread to be scheduled: 15 s on a
   starved box. anyio grows its pool on the calling thread — the loop —
   exactly as CPython's default executor did before `loop_executor.py`
   (2026-07-29), and its workers RETIRE after ten idle seconds, so the pool
   was empty again by the time the user clicked.

The second boot was warm: nothing to page in, no import to run, no thread
to start — which is why "it works now" told nobody anything.

**Fix.**

* `jarvis/plugins/brain/{claude_api,openai,grok,openrouter,nvidia,ollama,local_openai}.py`
  — `complete()` builds the client on a worker thread the first time
  (`asyncio.to_thread(self._ensure_client)`); the SDK import and the
  credential read never run on the loop.
* `jarvis/brain/healthcheck.py` — `probe()` instantiates the provider off
  the loop, which also moves the registry's plugin imports there.
* `jarvis/core/loop_executor.py` — `keep_anyio_workers_alive()` stops
  anyio's workers from retiring (guarded on the backend's shape; a no-op on
  an unfamiliar anyio), `warm_anyio_worker_pool()` brings eight up at a
  calm moment. `jarvis/ui/web/server.py` applies the keep-alive when the
  app is built and warms the pool five seconds after voice reports ready
  (or at the end of the deferred phase when voice is off).
* `jarvis/clis/prober.py` — `probe_all` runs `PROBE_CONCURRENCY` (6) probes
  at a time; the partial-result contract is unchanged.
* `jarvis/agentic_ide/session.py` — a cold-start slot is held until the
  pane paints its input line (`fleet_actions.wait_for_input_line`, the bare
  observation without Claude Code's typing opt-out) or
  `COLD_START_HOLD_MAX_S` (15 s), with `COLD_START_SETTLE_S` as the floor.
  A fixed 1.2 s let eight resumes pile into two seconds.
* `jarvis/ui/web/server.py` — the SPA index route decides between index
  and holding page off the loop (it reads `index.html` and stats every
  asset; it was in a stall stack on 2026-08-26).
* Side find, same log: `jarvis/telemetry/recorder.py` serializes a string
  `trace_id` instead of raising `'str' object has no attribute 'hex'` on
  every dictation.

**Regression tests.**
`tests/unit/brain/test_healthcheck_off_loop.py`,
`tests/unit/core/test_loop_executor_anyio.py`,
`tests/unit/clis/test_prober_concurrency.py`,
`tests/unit/agentic_ide/test_cold_start_gate.py::test_a_slot_is_held_until_the_pane_paints_its_input_line`,
`::test_a_pane_that_never_paints_an_input_line_holds_its_slot_only_to_the_ceiling`,
`tests/unit/telemetry/test_recorder_trace_id.py`.

**Forensics.** `grep -n "Event loop STALLED" data/jarvis_desktop.log` and
the stack under it name the blocking call; `wake-start=` in the "Warm-up
Phase A" line is the boot-health number (3–8 s warm, 20–55 s means the box
was starved); `Get-CimInstance Win32_OperatingSystem` `LastBootUpTime`
tells whether it was a cold OS boot.

**Lesson.** A lazy `import` inside an `async def` is a synchronous call on
the event loop, and the first one in a process is not cheap. Every pool
that grows by `Thread.start()` grows on the thread that asks — the asyncio
executor was fixed once; anyio's was the same bug one layer over. And a
gate that releases on a timer gates only what fits in the timer: the honest
end of a cold start is the CLI's own input line.


## BUG-190: a message typed on the Agentic IDE's chat stage answered "T11: the prompt is in its input box, not sent" and a pasted screenshot "session not found" — the hidden tile spawned the agent at 10x4, an unconfirmed delivery was read as a refusal, and the pane's history id went out as a chat session id (MEDIUM, FIXED 2026-08-27)

**Symptom.** With the Agentic IDE in chat view, a new pane (T11) was
opened and "Hlallo" typed into the stage's composer. A warning toast said
`T11: the prompt is in its input box, not sent` — while the agent in T11
was already answering it. Pasting a screenshot into the same composer put
`session not found` in red under the input. The log, eleven seconds apart:
`PTY spawned: shell=agentic-ide:t11 … size=10x4`, then `never saw the
prompt reach T11 — it may have been submitted or dropped; reporting it as
unconfirmed`.

**Cause.** Three, stacked.

1. `AgenticTerminal` measures its tile once before the socket handshake, so
   the agent is spawned at the size the grid shows. On the chat stage every
   cell is `hidden` (display:none) and the staged pane's transcript is drawn
   over the canvas instead — so the fit addon measured a box with no size
   and answered with its own minimum (2x1), the floors clamped that to 10x4,
   and the handshake spawned the CLI at exactly that. The later
   `applyResize` path already refused an unmeasurable tile; the mount-time
   fit did not.
2. In a ten-column terminal the CLI never draws its input line the way
   `_input_line_holds` reads it, so `_write_and_confirm` reported
   `submitted: null` — "never seen arriving, no claim either way".
   `useWebSocket.ts` folded that null into false and toasted the warning
   meant for a prompt provably still sitting in its box.
3. `store/paneChat.ts` handed the pane's history id out as
   `activeSessionId`. The composer sends that id as `session_id` with every
   file attach, and `/api/agent-chat/attachments` looks it up in the chat's
   session store — where no pane has ever existed — and answers 404.

**Fix.**

1. The mount-time fit runs only for a tile that has a size (`measurable()`,
   the same check `applyResize` uses); a pane that mounts hidden keeps its
   constructed 80x24 until the tile is shown, and the first real measurement
   follows from `sendResize` then. Test: "does not shrink a pane that mounts
   hidden to the floors".
2. The prompt toast has the event's three states: success for true, the
   warning for false, and an info line for null that says the pane never
   showed the prompt arriving (`use_web_socket.prompt_unconfirmed`,
   en/de/es). Test: "tells the three states of a prompt typed into a pane
   apart".
3. A pane's store reports no `activeSessionId`; the attach goes by the
   workspace folder, which is where a pane's files belong anyway. The
   session object beside it still names the pane for the stage.

**Lesson.** A measurement taken from something hidden is not a small
number, it is no number — code that clamps to a floor must first ask
whether there was anything to measure. And when the backend goes to the
trouble of reporting three states, no layer on the way to the user may fold
two of them together: an honest "unconfirmed" turned into a confident "not
sent" is a lie the user acts on.
