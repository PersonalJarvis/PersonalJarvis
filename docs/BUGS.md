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
  (session ``bf44825d-c3cb-41d8-aac5-fc61482e52d4`` at 17:22): user said
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

A custom wake word ("Hey Nico", any name on the `stt_match` / `RollingWhisperWake`
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

**Symptom.** Voice session 2026-07-05 19:47 (session 3e27dd8e, screenshot from
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

**Symptom.** Voice session 2026-07-05 19:48 (session 3e27dd8e, turn 2): the
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
15:50–15:52, mission_019f3cd8-1dd4: three identical iterations of
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
— 019f3d01, then 019f3d0f — walked one family further):
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
   RESOURCE_EXHAUSTED, mission 019f3d0f). `ApiAgentWorker` recorded nothing
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
people folder with profiles for Sam, Joy, and the user's mother. The real
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
Grok error wording killed the session (session `204b108a`, first live run
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
idle-gated defense at once (session `1fd3fa38`, first live run WITH the
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
turn af736681 10:21).** Five sequential tool rounds (wiki-recall ×3 →
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

## BUG-086: Realtime voice audibly flips gender between turns while every transcript label reads the same pinned voice (HIGH, MITIGATED 2026-07-18, provider-side root cause OPEN)

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

**Still OPEN.**

- The provider can still drift despite instructions; if it does, the next
  escalation is rendering delegate results through the surface TTS
  (deterministic voice) instead of native readback — trading first-audio
  latency for voice identity.
- Label honesty: `voice_name` should distinguish "requested" from
  "verified" (classic TTS engines are verified by construction; native
  realtime audio is only ever requested). Until then, treat realtime
  `voice_name` as the pin, not as evidence.

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
