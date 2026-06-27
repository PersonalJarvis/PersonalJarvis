---
title: Bug Register Sub-Agent Pipeline
date: 2026-04-29
scope: Voice → Router → Sub-Jarvis-Spawn → Harness-Dispatch
---

# Bug Register: Sub-Agent Pipeline (2026-04-29)

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
5. User hears "Die parallele Ausführung der Sub-Agenten ist fehlgeschlagen." (The parallel execution of the sub-agents has failed.)

Token cost per failed spawn: ~40k tokens × 4 providers ≈ $0.13.

---

## Bug #1: `spawn` missing in `spawn_verbs` (CRITICAL)

- **File**: `jarvis/core/config.py:156-168` (BrainRoutingConfig.spawn_verbs)
- **Symptom**: User says "Spawn sub-agents.", Router-Brain responds
  "Hallo Alex, was kann ich für dich tun?" ("Hello Alex, what can I do for you?") — no action.
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
  `success=False, duration_ms=5, error="ein oder mehr Sections mit non-zero exit"` (one or more sections with non-zero exit). Production logs show 3× HarnessDispatched, NO
  HarnessProgress, NO HarnessCompleted.
- **Root cause**: `HarnessManager.get(name)` cached ONE
  `OpenClawHarness` instance. `SubprocessHarness.invoke()` wrote
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
  `error="pyautogui nicht verfuegbar: No module named 'pyautogui'. Native Windows-Eingabe fehlgeschlagen: [WinError 0] Falscher Parameter."` (pyautogui not available: No module named 'pyautogui'. Native Windows input failed: [WinError 0] Incorrect parameter.)
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
  1. Wake is correctly detected: `WAKE erkannt ueber whisper:Hey JARVIS` (WAKE detected via whisper:Hey JARVIS).
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
Brain claude-api(claude-haiku-4-5-20251001) fehlgeschlagen: 400
  'Your credit balance is too low to access the Anthropic API. Please
   go to Plans & Billing.'
Brain grok(grok-4.1-fast) fehlgeschlagen: 404
  'The model grok-4.1-fast does not exist or your team does not have
   access to it.'
Brain gemini(gemini-3-flash) fehlgeschlagen: 11 validation errors for
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
  2. User-actionable message: "Account-Problem bei {providers}: Credit aufladen, Plan upgraden oder Modell-Tier freischalten. Bei Anthropic: console.anthropic.com/settings/billing. Bei xAI: console.x.ai/team/billing." (Account problem with {providers}: Top up credit, upgrade plan or unlock model tier. For Anthropic: console.anthropic.com/settings/billing. For xAI: console.x.ai/team/billing.)
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
  Frage: 'Antworte mit genau einem Wort: ja oder nein.'
  Response: 'Ja.'

--- Step 4: Verdict ---
  [OK] Brain-Call lieferte echte Response
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
- Empty state: `Bereit fuer Befehle`
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
- **Symptom**: User clicks "Aufgaben" (Tasks) in the sidebar. The page shows a
  red banner: *"Konnte Aufgaben nicht laden: HTTP 503"* (Could not load tasks: HTTP 503). Regardless of the
  state filter (All/Active/Done/Problems), regardless of refresh — it stays 503,
  because the polling gets the same answer every 3s. The UI is otherwise
  functional (chats, skills, missions, etc. work normally).
- **Root cause** (wiring gap, not a code bug):
  1. **Backend fully built**: `jarvis/tasks/{schema,store,scheduler,
     runner}.py` + 25 unit tests green, ADR-0003/0005 documented.
  2. **REST layer correct**: `jarvis/ui/web/tasks_routes.py:28` expects
     `app.state.task_store`/`app.state.task_scheduler`. When not set,
     `_require_store` deliberately throws HTTP 503 (`detail="TaskStore nicht verfuegbar"` — TaskStore not available) — defensive, this is not a crash.
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
    before fix): `STATUS=503 detail="TaskStoreNicht verfuegbar"` (TaskStore not available).
  - App restart with patched code → the same endpoint:
    `STATUS=200 BODY={tasks:[],total:0}`.
  - Demo task via `POST /api/tasks {trigger:after_delay 30s, action:tool_call
    noop}` → appears immediately in the UI with the correct trigger icon (clock),
    "Verzögerung" (Delay) label, ID short form, and live countdown.
  - After 30s: the card switches to state `failed` with the expected step
    *"RuntimeError: ToolExecutor oder Tool-Registry nicht konfiguriert"* (ToolExecutor or Tool-Registry not configured) —
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
      Phase-5 brain-tool step (`schedule_task` as a Sub-Jarvis tool).
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
`SamHerz.PersonalJarvis`). With no AUMID-matched shortcut, Explorer fell back
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
  - User confirmation: "Läuft sauber, UI sichtbar." (Runs cleanly, UI visible.)
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
       without `creationflags` for every Sub-Agent-output session.
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
- **Symptom:** The sidebar tab "Transkription" (Transcription) shows **"Noch keine Voice-Sessions"** (No voice sessions yet),
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
    `hangupLabel("turn_complete")` → "Antwort fertig" (Response complete); the default case
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
    "Transkription" now shows the full list — newest session "läuft" (running),
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
  `TTS-Echo-Sperre aktiv` (TTS echo lock active) is set — but **no sound comes out of the
  headset**. The wake-acknowledge chime is also missing. Clear in the log:
  ```
  WARNING | ACK-Playback fehlgeschlagen: Error opening OutputStream:
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
  - Logs showed `OSError: [WinError 64] Der angegebene Netzwerkname ist nicht mehr verfügbar` (The specified network name is no longer available) in
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
     `docs/openclaw-bridge.md` or its overlay equivalent.
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
  No `OpenClawBackgroundCompleted` event ever fires, so the speech
  pipeline has nothing to read back.
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
  2. Plumb a `kontrollierer_resolver` into `SpawnOpenClawTool`. The
     background dispatch now calls `kontrollierer.run_mission(mission_id)`
     after the persist step, mirroring the REST path.
  3. Register both setters in `jarvis/ui/web/server.py::_init_mission_stack`
     after `bootstrap_missions()` returns.
  4. New regression tests in
     `tests/integration/test_openclaw_lazy_bootstrap.py`:
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
  `tests/integration/test_openclaw_lazy_bootstrap.py` cover the happy
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
  the Transcription view shows the preview text ``Auflegen.`` (or
  ``(kein User-Text aufgezeichnet)`` (no user text recorded) for sessions where the brain
  emitted ``suppress_response`` early). Opening any session shows a
  single Turn whose User-block reads ``Auflegen.`` even when the user
  spoke a full conversation. Jarvis-block is empty. Tools list shows
  ``spawn_openclaw`` × N. The actual transcription is gone.
- **Root cause A — multi-utterance turn collapse:**
  ``jarvis/sessions/recorder.py::_on_transcript_final`` always wrote
  ``current_turn.user_text = event.transcript.text`` and never closed
  the turn. Turn boundaries are normally drawn by ``_on_system_state``
  on the SPEAKING→LISTENING transition, but in OpenClaw-routed turns
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
  finish complex sentences. Symptom (user words): "der Jarvis denkt immer, man hat schon zu Ende gesprochen … das hat auch damals schon ganz gut funktioniert, dann ist ein Bug aufgetreten." (Jarvis always thinks you've already finished speaking … that used to work quite well, then a bug appeared.) Concrete production case
  (session ``bf44825d-c3cb-41d8-aac5-fc61482e52d4`` at 17:22): user said
  "Kannst du bitte einen Subagenten spawnen, welcher..." ("Can you please spawn a sub-agent that...") — VAD endpointed
  after 160 ms of silence (budget was 1200 ms) and the brain was called
  on a half-question; the rest of the sentence ("...mir fünf Recherchenthemen rausholt" — "...pulls out five research topics for me") became Turn 2 and arrived as a fragment.
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
  "der Jarvis, das Maskottchen spawnt nicht, wenn man sagt Hey Jarvis, spawnt ja nicht mehr" (Jarvis, the mascot doesn't spawn anymore when you say Hey Jarvis, it just doesn't spawn anymore). The wake-word still triggers, the speech pipeline
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
  - **L2** Discovery-independent recovery — voice phrases "Orb zurück" (Orb back),
    "wo bist du" (where are you), "reset orb" are matched by
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
says "wird erledigt" ("will be done"), the brain returns a phantom success response, and the TTS
reads a confirmation to the user. The action never happens. The user is deceived.

Classic trigger example: "Schick eine Email an Sam" ("Send an email to Sam") → TTS plays "Die Email wurde gesendet." ("The email has been sent.") → No email was sent. No error was raised. No log entry indicates a failure.

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
   action-promise phrases ("mache ich" — "I'll do it", "wird erledigt" — "will be done", "ist gesendet" — "has been sent").
   The Ack-Brain therefore confirms phantom actions sub-second, before the
   deep brain even runs.

3. **Critic layer:** The Critic currently ratifies empty diffs for non-file
   tasks (AD-9 in `docs/openclaw-bridge.md`). An OpenClaw worker can produce
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
     alongside `_should_force_openclaw`. If action-intent and no matching
     capability and not smalltalk: skip brain + OpenClaw, emit UNSUPPORTED.

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
   from the worker's unverified text claim. For Welle-2 mock OpenClaw (no
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
- AD-9 in `docs/openclaw-bridge.md` — Critic + risk-tier preconditions that
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

## BUG-035: "Listens forever" #4 — explicit Sub-Agent command hijacked by a topical skill match, then a beheaded mute turn ends in silence (HIGH, 2026-06-10)

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
