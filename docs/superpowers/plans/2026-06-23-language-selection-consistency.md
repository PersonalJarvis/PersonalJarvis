# Language-Selection Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every user-facing output layer (text reply, spoken answer, announcements, mission readbacks, TTS voice) agree on ONE per-turn language so a German turn is never spoken in English, never half-German/half-English, and never spoken with a foreign-accented voice.

**Architecture:** The codebase already has a single authoritative resolver — `jarvis/core/turn_language.py::resolve_output_language(...)` — but a set of *resolver-blind* layers bypass it (they hardcode `"de"`, default to `"de"`, run their own `de`/`en`-only detector, or pass `language_code=None` to TTS). The fix routes those layers through the same resolver / the same threaded `conversation_language`, consolidates the duplicated BCP-47 mapping, and couples the TTS *voice* (not just the pronunciation pin) to the resolved language so the active Cartesia provider never falls back to its English voice on German text.

**Tech Stack:** Python 3.11, asyncio, `pytest` (asyncio_mode=auto), `ruff`, the EventBus (`jarvis/core/events.py`), Cartesia TTS (`sonic-3.5`), Groq Whisper STT.

---

## Background — why this is happening (read before touching code)

This is not one bug; it is a **family** of bugs around a sound central design that several layers quietly evade. Three independent forensic root causes, confirmed against the current tree by two read-only research agents on 2026-06-23:

1. **The announcement choke point (the screenshot bug).** The user spoke German; the text answer rendered German; the *spoken* output (tagged "ANNOUNCEMENT") came out fully English. Root cause: `jarvis/speech/pipeline.py::_on_announcement` reads `event.language` **verbatim** (`pipeline.py:2442` → `ann_lang = (event.language or "de").lower()`) and never consults `resolve_output_language`, the live `brain.reply_language` pin, or the sticky `conversation_language`. Every emitter that publishes an `AnnouncementRequested` with the wrong (or no) language is therefore spoken in that wrong language. Several emitters either hardcode `"de"`, omit the field (default `"de"` at `jarvis/core/events.py:403`), or — worse for the screenshot — let an *English-composed* brain turn (a scheduled "morning briefing" skill) flow straight to TTS because the emitter never passed the conversation language down to the brain call that wrote the text.

2. **The British accent.** The active TTS provider is **Cartesia** (`jarvis.toml:111`), `language_code = "auto"` (`jarvis.toml:127`). Cartesia's `_resolve_voice` (`jarvis/plugins/tts/cartesia_tts.py:112-139`) picks a per-language native voice from `language_code` — but its final fallback, when the language is `auto`/unknown and the cheap text sniff fails, is the **English voice "Daniel"** (`voice_id` / `voice_id_en`, `jarvis.toml:183/185`) speaking German → an English-accented German. The top-level `[tts] voice_de/voice_en/voice_auto_switch` keys are **dead code** for Cartesia (the factory reads only `voice_de`, and Cartesia reads its own `[tts.cartesia]` block). So the only thing standing between a German turn and an English voice is whether a correct `de-DE` `language_code` actually reaches `synthesize()` on *every* path — and several paths pass `None`/`"de-DE"`-hardcoded/`auto`.

3. **The mid-answer flip.** Generative multilingual TTS (Cartesia, and Gemini in the fallback chain) auto-switches pronunciation **per word** when `language_code` is empty — a German sentence ending on an English loanword ("…Boss.") code-switches its tail. Secondary contributor: if a user ever sets `[stt].language` away from `auto`, Groq/Whisper transcribes cross-language audio into the *wrong-language text*, which the text-first resolver cannot recover. Current live config has `[stt].language` unset → `auto` (safe), so the primary live cause is the unpinned `language_code` on resolver-blind paths.

**Live config facts (verified 2026-06-23, `jarvis.toml`):**
- `[tts] provider = "cartesia"`, `language_code = "auto"`, `voice_auto_switch = true` (the latter is never read by any plugin).
- `[tts.cartesia]` generic fallback `voice_id` = Daniel (EN); `voice_id_de` = Sebastian; `voice_id_es` = Pedro; `language = "auto"`.
- `[brain] reply_language = "auto"` → **no pin**. This is the amplifier: with no pin, the *only* thing holding the layers together is conversation-stickiness + per-turn detection — and the resolver-blind layers bypass even that.
- `[stt] provider = "groq-api"`, no explicit `language` → `auto` (safe).
- `[ack_brain] enabled = true`, `preamble_enabled = false` → the blue *preamble* bubble is off, but the **spawn-announcement composer is on** (and is `de`/`en`-only).

**Design decision that governs every task:** the user is bilingual and wants `auto` mode to *work* (output language mirrors input language, consistently across all layers). The fix must make `auto` robust — **do not** rely on setting a hard `de` pin (that would force English turns into German, the opposite of what the user wants). Per the binding CLAUDE.md "Runtime Output Language" doctrine, every spoken/written layer resolves through the one resolver and supports `de`/`en`/`es` equally — no `de`/`en`-only tables, no per-layer re-derivation, no per-layer hardcoded default.

**The recurring trap to respect (CLAUDE.md):** this working tree is shared across parallel sessions and many of the prior language fixes were committed-or-not in flux. **Task 0 verifies what is actually in the tree before changing anything.** Stage hunk-isolated (`git add -p`), never `git add` a whole shared-tree file.

---

## File map (what each task touches)

| Concern | File:line | Task |
|---|---|---|
| Announcement passthrough (choke point) | `jarvis/speech/pipeline.py:2442`, `:2486-2490` | T1 |
| Announcement event default language | `jarvis/core/events.py:403` | T1 |
| BCP-47 map duplication (4 sites, `:3379` missing `es`) | `jarvis/speech/pipeline.py:2488,3379,5916,6949` | T2 |
| Cartesia English-voice fallback | `jarvis/plugins/tts/cartesia_tts.py:112-139` | T2 |
| Hardcoded `language_code="de-DE"` background readback | `jarvis/speech/pipeline.py:2790` | T3 |
| Jarvis-Agents background-completed readback | `jarvis/speech/pipeline.py:2728,2755` | T3 |
| Cron-skill announcement (omits language) | `jarvis/speech/pipeline.py:2283-2288` | T3 |
| Tasks-runner agent-result announcement (omits language) | `jarvis/tasks/runner.py:398-404`, `:293` | T3 |
| CU progress "Schritt X von Y" hardcoded German | `jarvis/harness/screenshot_only_loop.py:4202-4208` | T3 |
| CU elevation phrase drops the pin | `jarvis/harness/screenshot_only_loop.py:828` | T3 |
| MissionAnnouncer `de`/`en`-only, frozen dispatch lang | `jarvis/missions/voice/announcer.py:54,91,174,262`; `jarvis/missions/.../spawn_worker.py:443-465` | T4 |
| `_spawn_ack_language` forbidden `_looks_german` shortcut | `jarvis/brain/manager.py:4305-4315` | T4 |
| `_direct_ack_language` default `"de"` (not `DEFAULT_LOCALE`) | `jarvis/brain/manager.py:1966-1978` | T4 |
| Browser/telephony static per-session language | `jarvis/browser_voice/session.py:147-149,223`; `jarvis/telephony/session.py:126,438` | T5 |
| Ack-brain `es` persona gap (spec-gated) | `jarvis/brain/ack_brain/{generator,persona_prompt,spawn_announcement}.py` | T6 |
| STT `auto` regression guard | `jarvis/plugins/stt/{groq_api,fwhisper}.py` | T6 |

---

## Phase ordering

- **Phase 1 — what the user sees (P0):** T0 (verify), T1 (close the announcement choke point), T2 (kill the English-voice fallback / unify BCP-47), T3 (make the announcement *emitters* carry the turn language).
- **Phase 2 — doctrine consistency (P1):** T4 (mission readback + the `_looks_german` shortcut + default-locale consistency), T5 (browser/telephony per-turn language).
- **Phase 3 — hardening (P2):** T6 (`es` ack-brain persona — spec-gated; STT auto guard).

Each task is independently shippable and leaves the tree green.

---

### Task 0: Verify the actual state of prior language fixes (no code change)

Prior fixes (central resolver, `GeminiFlashTTS.language_code` pin, `extract_reply_language_directive`, conversation stickiness) were repeatedly recorded as "UNCOMMITTED, shared tree". Confirm which are actually present so we neither re-do nor clobber them.

**Files:** none (read-only).

- [ ] **Step 1: Confirm the resolver and its `conversation_language` param exist**

Run:
```bash
grep -n "def resolve_output_language" jarvis/core/turn_language.py
grep -n "conversation_language" jarvis/core/turn_language.py jarvis/brain/manager.py jarvis/speech/pipeline.py
```
Expected: `resolve_output_language` present with `conversation_language=` kwarg; `BrainManager._conversation_language` + `conversation_language` property present; `_output_language` reads `brain.conversation_language`. If ANY are missing, STOP — a prior fix was reverted; restore it before continuing (it is a prerequisite for every task below).

- [ ] **Step 2: Confirm the TTS `language_code` pin reached the providers**

Run:
```bash
grep -n "language_code" jarvis/plugins/tts/gemini_flash_tts.py jarvis/plugins/tts/cartesia_tts.py
grep -n "language_code" jarvis/core/protocols.py
```
Expected: `TTSProvider.synthesize` in `protocols.py` declares `language_code`; both plugins accept and *use* it (not `_ = language_code`). Note gaps for T2.

- [ ] **Step 3: Record the baseline test state**

Run:
```bash
python -m pytest tests/unit/core/test_turn_language.py tests/unit/speech/test_output_language_pin.py tests/unit/speech/test_phrase_language.py tests/unit/plugins/tts/ tests/unit/brain/ -q
```
Expected: a known-green baseline (note any *pre-existing* foreign failures — e.g. `test_contacts_integration`, `test_navigation_intent` — so they are not attributed to this work). Write the failing-test list into the PR description.

- [ ] **Step 4: Snapshot the relevant live config** (for reproduction, not commit)

Run:
```bash
grep -nE "reply_language|^\[tts\]|provider|language_code|voice_id|^\[stt\]|preamble_enabled" jarvis.toml
```
Expected: matches the "Live config facts" above. If `[stt].language` is pinned or `reply_language` is a hard pin, note it — it changes which hypothesis dominates.

---

### Task 1: Route `_on_announcement` through the resolver (close the choke point)

The single structural fix with the highest leverage: the spoken-announcement handler must resolve the announcement's language through the same `_output_language` resolver instead of trusting `event.language` verbatim. The event's language becomes a *hint* (like the STT tag), but the live pin and the sticky `conversation_language` win — so a German conversation never gets an English-voiced announcement, regardless of which emitter produced it.

**Files:**
- Modify: `jarvis/speech/pipeline.py:2442` and `:2486-2490`
- Modify: `jarvis/core/events.py:403` (default language → resolver-neutral)
- Test: `tests/unit/speech/test_announcement_language.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/speech/test_announcement_language.py
"""_on_announcement must resolve language through the pin/conversation, not
trust event.language verbatim (forensic 2026-06-23: German chat, English
'ANNOUNCEMENT' spoken)."""
import pytest

from jarvis.core.events import AnnouncementRequested
from tests.fakes.speech import make_pipeline_with_fakes  # existing helper pattern


@pytest.mark.asyncio
async def test_announcement_follows_conversation_language_over_event_tag():
    pipe, tts = make_pipeline_with_fakes()
    # German conversation established; reply_language=auto (no pin).
    pipe._brain.conversation_language = "de"
    pipe._brain.reply_language = "auto"
    # An emitter stamped EN (or omitted it) on otherwise-German-conversation.
    await pipe._on_announcement(
        AnnouncementRequested(text="Guten Morgen, Chef.", language="en", kind="completion")
    )
    # The TTS voice/pronunciation pin must be de-DE, not en-US.
    assert tts.last_language_code == "de-DE"


@pytest.mark.asyncio
async def test_announcement_honors_hard_pin_over_event_tag():
    pipe, tts = make_pipeline_with_fakes()
    pipe._brain.conversation_language = ""
    pipe._brain.reply_language = "de"   # hard pin
    await pipe._on_announcement(
        AnnouncementRequested(text="Done.", language="en", kind="completion")
    )
    assert tts.last_language_code == "de-DE"


@pytest.mark.asyncio
async def test_announcement_auto_mirrors_announcement_text_when_no_conv_no_pin():
    pipe, tts = make_pipeline_with_fakes()
    pipe._brain.conversation_language = ""
    pipe._brain.reply_language = "auto"
    await pipe._on_announcement(
        AnnouncementRequested(text="Done, the file is ready.", language=None, kind="completion")
    )
    assert tts.last_language_code == "en-US"
```

If `make_pipeline_with_fakes` / `tts.last_language_code` do not exist yet, add a minimal fake TTS that records `language_code` and a pipeline builder, following the pattern already used in `tests/unit/speech/test_output_language_pin.py` (read it first and mirror its fixtures).

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/speech/test_announcement_language.py -v`
Expected: FAIL — current code maps `en` → `en-US` from the verbatim `event.language`.

- [ ] **Step 3: Implement — resolve, don't trust**

In `jarvis/speech/pipeline.py`, change line 2442 from:
```python
        ann_lang = (event.language or "de").lower()
```
to:
```python
        # Resolve the announcement's language through the ONE authoritative
        # resolver — the live brain.reply_language pin and the sticky
        # conversation_language must win over whatever an emitter stamped on the
        # event (forensic 2026-06-23: a German chat spoke an English
        # 'ANNOUNCEMENT' because event.language was trusted verbatim). The
        # event tag is only a hint, passed where the STT tag normally goes.
        ann_lang = self._output_language(event.language, event.text or "")
```
Then change the TTS pin block at `:2486-2490` to reuse `ann_lang` (which is now always a clean `de`/`en`/`es` code) instead of re-deriving from `event.language`:
```python
            lang_code = self._bcp47(ann_lang)   # helper added in Task 2
```
(Until Task 2 lands, inline the existing map: `lang_code = {"de": "de-DE", "en": "en-US", "es": "es-ES"}.get(ann_lang)`.)

Also pass the resolved `ann_lang` (not `event.language`) into `_emit_spoken` at `:2467-2472` so the transcript records the language actually spoken:
```python
        self._emit_spoken(
            scrubbed.cleaned,
            ann_lang,
            _announcement_spoken_kind(getattr(event, "kind", None)),
            getattr(event, "detail", None),
        )
```

- [ ] **Step 4: Make the event default resolver-neutral**

In `jarvis/core/events.py:403`, change the `AnnouncementRequested.language` default from `"de"` to `None`. With T1 Step 3, `_on_announcement` now resolves a `None` language to the conversation/pin/detected language instead of silently forcing German. Read the dataclass first; if other consumers rely on the non-None default, keep the field `Optional[str] = None` and confirm `_on_announcement` is the only spoken consumer (the research map says it is).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/speech/test_announcement_language.py tests/unit/speech/test_output_language_pin.py -v`
Expected: PASS (all three new + the existing pin tests still green).

- [ ] **Step 6: Commit**

```bash
git add -p jarvis/speech/pipeline.py jarvis/core/events.py tests/unit/speech/test_announcement_language.py
git commit -m "fix(voice): resolve announcement language through the pin/conversation, not event.language verbatim"
```

---

### Task 2: One BCP-47 map + kill Cartesia's English-voice fallback (the British accent)

Two coupled changes: (a) DRY the four copies of the `{"de":"de-DE",...}` map into one helper that always covers `es` (the `:3379` prerender copy is missing `es`), so no path can ever pass a stale/None pin; (b) make Cartesia's voice fallback follow the resolved language / conversation language instead of silently defaulting to the English voice on German text.

**Files:**
- Modify: `jarvis/speech/pipeline.py` (add `_bcp47` helper; replace the 4 inline maps at `:2488`, `:3379`, `:5916`, `:6949`)
- Modify: `jarvis/plugins/tts/cartesia_tts.py:112-139`
- Test: `tests/unit/speech/test_bcp47_map.py` (create), extend `tests/unit/plugins/tts/test_cartesia_tts.py`

- [ ] **Step 1: Write the failing test for the helper**

```python
# tests/unit/speech/test_bcp47_map.py
from jarvis.speech.pipeline import SpeechPipeline


def test_bcp47_covers_all_three_locales():
    assert SpeechPipeline._bcp47("de") == "de-DE"
    assert SpeechPipeline._bcp47("en") == "en-US"
    assert SpeechPipeline._bcp47("es") == "es-ES"   # the :3379 copy dropped this


def test_bcp47_unknown_returns_none():
    assert SpeechPipeline._bcp47("xx") is None
    assert SpeechPipeline._bcp47(None) is None
    assert SpeechPipeline._bcp47("") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/speech/test_bcp47_map.py -v`
Expected: FAIL — `_bcp47` does not exist.

- [ ] **Step 3: Add the helper and replace the four inline maps**

Add to `SpeechPipeline` (near `_output_language`):
```python
    _BCP47 = {"de": "de-DE", "en": "en-US", "es": "es-ES"}

    @classmethod
    def _bcp47(cls, lang: object) -> str | None:
        """Map a de/en/es turn-language code to a TTS BCP-47 locale, else None.

        Single source for the whole pipeline — replaces four hand-copied maps,
        one of which (the task-ack prerender) had silently dropped ``es``.
        """
        return cls._BCP47.get(str(lang or "").lower())
```
Replace the inline `{"de": "de-DE", "en": "en-US", "es": "es-ES"}.get(...)` (and the `de`/`en`-only copy at `:3379`) at all four sites with `self._bcp47(<lang_code>)`. Read each site first to pass the right variable (`ann_lang` at `:2488`, the prerender lang at `:3379`, `lang` at `:5916`, `language` at `:6949`).

- [ ] **Step 4: Write the failing Cartesia test**

```python
# add to tests/unit/plugins/tts/test_cartesia_tts.py
def test_cartesia_unknown_language_does_not_fall_back_to_english_voice():
    """A German segment with an unresolved language_code must not be spoken by
    the English voice (the British-accent symptom). The conversation default
    drives the fallback instead of a hardcoded EN voice."""
    tts = _make_cartesia(default_locale="de")  # see Step 5 for the new param
    # language_code unpinned, text that the cheap sniff cannot classify:
    voice = tts._resolve_voice("Charon.", voice_override=None, language_code="auto")
    assert voice == tts._voice_by_lang["de"]   # Sebastian, not Daniel
```

- [ ] **Step 5: Run it to verify it fails**

Run: `python -m pytest tests/unit/plugins/tts/test_cartesia_tts.py -k english_voice -v`
Expected: FAIL — current fallback returns `_voice_by_lang["en"]` (Daniel).

- [ ] **Step 6: Implement the Cartesia fallback fix**

In `jarvis/plugins/tts/cartesia_tts.py`, give the provider a `default_locale` (wired from the resolver's `DEFAULT_LOCALE` / the pipeline's conversation language — passed via the factory) and change the final fallback in `_resolve_voice` (`:138`) from:
```python
        # Default English voice keeps the previous single-voice behaviour.
        return self._voice_by_lang.get("en", self._voice_id)
```
to:
```python
        # Never silently default to the English voice on un-sniffable text — that
        # is the British-accent-on-German symptom. Fall back to the configured
        # default locale's native voice (the conversation/turn language), only
        # then to the generic voice_id.
        return self._voice_by_lang.get(self._default_locale, self._voice_id)
```
Wire `default_locale` in the factory (`jarvis/plugins/tts/__init__.py`) from `DEFAULT_LOCALE` (import from `turn_language`) — or, better, ensure the pipeline always passes a concrete `language_code` so the fallback is never reached (defense in depth; both are cheap).

**Important non-break note:** the larger root cause is that `language_code` must always arrive resolved. After T1+T2, `_on_announcement`, `_speak`, and `_brain_streaming` all pass a concrete `de-DE`/`en-US`/`es-ES`. The Cartesia fallback then only matters for any path that still passes `auto`/`None` — make it safe rather than English-biased.

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/unit/speech/test_bcp47_map.py tests/unit/plugins/tts/test_cartesia_tts.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -p jarvis/speech/pipeline.py jarvis/plugins/tts/cartesia_tts.py jarvis/plugins/tts/__init__.py tests/unit/speech/test_bcp47_map.py tests/unit/plugins/tts/test_cartesia_tts.py
git commit -m "fix(tts): one BCP-47 map (incl. es) + Cartesia stops defaulting to the English voice on German text"
```

---

### Task 3: Make announcement *emitters* carry the turn language (fix the text, not just the voice)

T1 makes the *voice* consistent; this task fixes the *text* — the screenshot's announcement was English because the emitting brain turn (a scheduled skill) ran with no language directive. Every emitter that produces spoken text must pass the current `conversation_language` down to the brain call/composer and onto the event, instead of hardcoding/omitting `"de"`.

**Files:**
- Modify: `jarvis/speech/pipeline.py:2790` (hardcoded `de-DE`), `:2728,2755` (Jarvis-Agents-bg), `:2283-2288` (cron-skill)
- Modify: `jarvis/tasks/runner.py:398-404,293`
- Modify: `jarvis/harness/screenshot_only_loop.py:4202-4208,828`
- Test: extend `tests/unit/speech/test_announcement_language.py`; add `tests/unit/tasks/test_runner_announcement_language.py`

- [ ] **Step 1: Write failing tests for the emitters**

```python
# tests/unit/tasks/test_runner_announcement_language.py
"""A scheduled/agent task that announces its result must stamp the active
conversation language on the AnnouncementRequested, and must drive the brain
turn that writes the text with that language directive (forensic 2026-06-23:
'Good morning, Chef...' spoken in English inside a German chat)."""
import pytest

from jarvis.core.events import AnnouncementRequested


@pytest.mark.asyncio
async def test_agent_result_announcement_carries_conversation_language(fake_bus, fake_brain):
    fake_brain.conversation_language = "de"
    runner = _make_tasks_runner(bus=fake_bus, brain=fake_brain)
    await runner._announce_agent_result("Result text")
    ann = fake_bus.last_event(AnnouncementRequested)
    assert ann.language == "de"
```

For the pipeline-side hardcodes, add to `test_announcement_language.py` a test that the background Jarvis-Agent readback (`:2790`) and the Jarvis-Agents background-completed readback (`:2728`) stamp the conversation language rather than a fixed `"de"`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/tasks/test_runner_announcement_language.py -v`
Expected: FAIL — emitter omits `language` / hardcodes `"de"`.

- [ ] **Step 3: Implement — thread the conversation language to each emitter**

Resolve once at each emitter from the brain's sticky state, e.g.:
```python
        lang = self._conversation_language_for_announcement()  # see helper below
        bus.publish(AnnouncementRequested(text=..., language=lang, kind=...))
```
Add a tiny pipeline helper (DRY) that mirrors `_output_language` but for the no-utterance announcement case:
```python
    def _conversation_language_for_announcement(self) -> str:
        brain = getattr(self, "_brain", None)
        pin = getattr(brain, "reply_language", None)
        conv = getattr(brain, "conversation_language", "")
        # pin wins; else the established conversation language; else default.
        return resolve_output_language(pin, None, "", conversation_language=conv)
```
Apply at:
- `pipeline.py:2790` — replace `language_code="de-DE"` with `self._bcp47(lang)` and `_emit_spoken(..., lang, ...)`.
- `pipeline.py:2728,2755` — replace `language="de"` + the German literals: keep German literals only when `lang == "de"`, otherwise pick the matching localized literal from a small `de`/`en`/`es` table (mirror `action_phrases.py`).
- `pipeline.py:2283-2288` (cron) and `tasks/runner.py:398-404` — add `language=lang`.
- `tasks/runner.py:293` — replace `or "de"` with `or self._conversation_language_for_announcement()` (or the runner's brain handle equivalent).

For the brain turn that *writes* the scheduled text (`pipeline.py:2276` synthetic prompt): pass the resolved language into the brain request's reply-language directive so the model writes in `lang`, not English. Read how `_reply_language_directive` is built in `manager.py:2329` and feed the same directive into the scheduled-run request path.

- [ ] **Step 4: CU progress / elevation phrases**

In `jarvis/harness/screenshot_only_loop.py`:
- `:4202-4208` — replace the hardcoded German "Schritt X von Y erledigt." + `language="de"` with a `de`/`en`/`es` phrase table keyed by the resolved language (the harness already has the turn language in its context — `computer_use_context`; thread it in if not present).
- `:828` — replace `resolve_phrase_language(None, task_prompt)` with a call that passes the live `reply_language` pin (so the pin is honored), mirroring `_ctx_output_language` in `computer_use_tool`.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/tasks/test_runner_announcement_language.py tests/unit/speech/test_announcement_language.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -p jarvis/speech/pipeline.py jarvis/tasks/runner.py jarvis/harness/screenshot_only_loop.py tests/unit/tasks/test_runner_announcement_language.py tests/unit/speech/test_announcement_language.py
git commit -m "fix(voice): announcement emitters carry the conversation language to text + event + TTS"
```

---

### Task 4: Mission readback + the `_looks_german` shortcut + default-locale consistency

`MissionAnnouncer` is `de`/`en`-only and reads a *frozen* dispatch-time language; `_spawn_ack_language` uses the doctrine-forbidden `de if _looks_german(...) else en` binary (drops `es`); `_direct_ack_language` defaults to `"de"` instead of `DEFAULT_LOCALE`.

**Files:**
- Modify: `jarvis/missions/voice/announcer.py:54,91,174,262`; `jarvis/missions/.../spawn_worker.py:443-465`
- Modify: `jarvis/brain/manager.py:4305-4315` (`_spawn_ack_language`), `:1966-1978` (`_direct_ack_language`)
- Test: `tests/unit/brain/test_spawn_ack_language.py` (extend), `tests/unit/missions/test_announcer_language.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/brain/test_spawn_ack_language.py (extend)
def test_spawn_ack_language_honors_es_pin(manager):
    manager.set_reply_language("es")
    assert manager._spawn_ack_language("cualquier texto") == "es"

def test_spawn_ack_language_uses_conversation_not_looks_german(manager):
    manager.set_reply_language("auto")
    manager._conversation_language = "es"
    # a thin/ambiguous utterance must inherit es, not collapse to de/en
    assert manager._spawn_ack_language("ok") == "es"
```
```python
# tests/unit/missions/test_announcer_language.py
def test_mission_readback_supports_es():
    ann = _make_announcer(language_default="en")
    line = ann.summary_line(status="approved", language="es")
    assert _is_spanish(line)   # not a German/English fallback
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/brain/test_spawn_ack_language.py tests/unit/missions/test_announcer_language.py -v`
Expected: FAIL — `_looks_german` collapses `es`; `_Lang = Literal["de","en"]`.

- [ ] **Step 3: Replace `_spawn_ack_language` with the resolver**

In `manager.py:4305-4315`, replace the binary shortcut:
```python
        return "de" if _looks_german(user_text) else "en"
```
with a resolver call that honors pin + stickiness:
```python
        return resolve_output_language(
            self.reply_language, "unknown", user_text,
            conversation_language=self._conversation_language,
        )
```
In `_direct_ack_language` (`:1978`), change the local `default="de"` to `default=DEFAULT_LOCALE` and prefer `resolve_output_language` (with `conversation_language`) over `resolve_turn_language` so stickiness applies there too. Import `DEFAULT_LOCALE` from `turn_language`.

- [ ] **Step 4: Extend MissionAnnouncer to de/en/es + live language**

In `jarvis/missions/voice/announcer.py`: widen `_Lang = Literal["de","en"]` → `Literal["de","en","es"]`; add the `es` phrase variants for each summary line; and change `:262` so the readback prefers the **current** conversation language (passed in at readback time from the brain) and only falls back to the frozen `MissionDispatched.language` when no live language is available. Widen `spawn_worker.py:465` `mission_language = turn_language if turn_language in ("de","en") else "de"` to include `"es"`.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/brain/test_spawn_ack_language.py tests/unit/missions/test_announcer_language.py tests/unit/brain/ -q`
Expected: PASS (and no regression in the brain suite beyond the known foreign failures from T0).

- [ ] **Step 6: Commit**

```bash
git add -p jarvis/brain/manager.py jarvis/missions/voice/announcer.py jarvis/missions/*/spawn_worker.py tests/unit/brain/test_spawn_ack_language.py tests/unit/missions/test_announcer_language.py
git commit -m "fix(missions): mission readback + spawn-ack resolve de/en/es via the central resolver (drop _looks_german)"
```

---

### Task 5: Per-turn language in the browser-voice and telephony sessions

`browser_voice/session.py` and `telephony/session.py` set a **static** `self.language_code` once per session (default `de-DE`) and never re-resolve per turn — so a session that starts German stays German even when the user switches, and vice versa. This is the headless/VPS voice path (first-class per CLOUD.md), so it must honor the resolver too.

**Files:**
- Modify: `jarvis/browser_voice/session.py:147-149,223,86`
- Modify: `jarvis/telephony/session.py:126,438`
- Test: `tests/unit/browser_voice/test_session_language.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/browser_voice/test_session_language.py
@pytest.mark.asyncio
async def test_browser_session_reresolves_language_per_turn(fake_brain):
    fake_brain.reply_language = "auto"
    sess = _make_browser_session(brain=fake_brain, default="de-DE")
    out = await sess._language_code_for_turn(transcript_lang="en", text="What's the weather?")
    assert out == "en-US"   # not the static de-DE session default
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/browser_voice/test_session_language.py -v`
Expected: FAIL — no per-turn resolution method; `self.language_code` is static.

- [ ] **Step 3: Implement per-turn resolution**

Add a `_language_code_for_turn(transcript_lang, text)` that calls `resolve_output_language(brain.reply_language, transcript_lang, text, conversation_language=brain.conversation_language)` and maps to BCP-47 via the shared helper, and use it at `session.py:223` (and the telephony equivalent at `:438`) instead of `self.language_code`. Keep the `audio_start` control-frame value only as the *seed*/hint, not the per-turn truth.

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/unit/browser_voice/test_session_language.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -p jarvis/browser_voice/session.py jarvis/telephony/session.py tests/unit/browser_voice/test_session_language.py
git commit -m "fix(channels): browser-voice + telephony resolve language per turn, not once per session"
```

---

### Task 6: Ack-brain `es` persona (spec-gated) + STT `auto` regression guard

Two hardening items. The ack-brain `es` persona is **spec-gated** — `PERSONA_PROMPT_DE/EN` and the spawn persona are locked to `docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md` (the docstrings forbid a silent rewrite). So this task **amends the spec first**, then adds the constant. The STT guard locks in the safe `auto` default.

**Files:**
- Modify: `docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md` (add the `es` persona section)
- Modify: `jarvis/brain/ack_brain/persona_prompt.py:158-175`, `jarvis/brain/ack_brain/generator.py:96-110`, `jarvis/brain/ack_brain/spawn_announcement.py:176,181`
- Modify: `jarvis/plugins/stt/groq_api.py`, `jarvis/plugins/stt/fwhisper.py` (guard only)
- Test: extend `tests/unit/brain/test_ack_brain/`, `tests/unit/speech/test_stt_language_autodetect.py`

- [ ] **Step 1: Amend the flash-brain spec** to define `PERSONA_PROMPT_ES` and the `es` spawn persona (a short section mirroring the DE/EN persona contract). Without this the constant change is a doctrine violation. Get maintainer sign-off on the Spanish persona wording before coding (it is the only step in this whole plan that genuinely needs human input — the persona voice is a product decision).

- [ ] **Step 2: Write failing tests** that `get_persona_prompt("es")` returns a Spanish persona (not the German fallback) and `_detect_language` recognizes Spanish.

- [ ] **Step 3: Run to verify they fail.** Run: `python -m pytest tests/unit/brain/test_ack_brain/ -k es -v`. Expected: FAIL.

- [ ] **Step 4: Add `PERSONA_PROMPT_ES`**, widen `_normalise_language`/`_detect_language`/`_PERSONA_LANGS` to include `es`, per the amended spec.

- [ ] **Step 5: STT auto guard** — add a regression test asserting `build_stt_from_config` maps `language == "auto"` → `None` (no forced language) for both Groq and faster-whisper, so a future edit cannot silently reintroduce the echo bug. The mapping already exists (`pipeline.py:7127`, `build_stt_from_config:45-46`); this only locks it.

- [ ] **Step 6: Run the tests.** Run: `python -m pytest tests/unit/brain/test_ack_brain/ tests/unit/speech/test_stt_language_autodetect.py -v`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -p docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md jarvis/brain/ack_brain/ jarvis/plugins/stt/ tests/
git commit -m "feat(ack-brain): es persona (spec-amended) + STT auto-language regression guard"
```

---

## Cross-cutting verification (run after each phase, mandatory before any restart/ship)

- [ ] **Full targeted suite:**
```bash
python -m pytest tests/unit/core/test_turn_language.py tests/unit/speech/ tests/unit/plugins/tts/ tests/unit/brain/ tests/unit/missions/ tests/unit/tasks/ -q
```
Expected: green except the *known* pre-existing foreign failures recorded in T0 Step 3.

- [ ] **Lint the touched lines only:** `ruff check jarvis/speech/pipeline.py jarvis/plugins/tts/cartesia_tts.py jarvis/brain/manager.py jarvis/missions/voice/announcer.py jarvis/tasks/runner.py jarvis/harness/screenshot_only_loop.py jarvis/browser_voice/session.py` — 0 new findings (pre-existing S110/UP037 noise is foreign).

- [ ] **Deploy:** none of these touch entry points → **no `pip install -e .`**. They are loaded via the editable install but need an app restart to take effect: `POST /api/settings/restart-app` (force=false, only when no mission is running). NOT `Stop-Process`.

- [ ] **Live mic verification (the real proof):** with `reply_language = "auto"`, run four turns and confirm voice + text + announcement all match:
  1. German turn → German text, German announcement, German voice (Sebastian, no British accent).
  2. English turn ("What's on my calendar?") → English everywhere.
  3. German turn ending on a loanword ("…und dann der Boss.") → no English tail. <!-- i18n-allow: quoted German voice test utterance -->
  4. A scheduled/skill announcement while the conversation is German → German announcement (the screenshot scenario).

---

## Risk register ("damit nichts kaputtgeht")

| Risk | Mitigation |
|---|---|
| A prior fix was reverted in the shared tree and a task re-introduces a half-state | T0 verifies presence of the resolver, the `language_code` pin, and the directive extractor BEFORE any change |
| Forcing `conversation_language` voice onto a genuinely other-language announcement text (German voice reads English text) | T1 resolves from `event.text` too (auto path mirrors the text); T3 fixes the *text* upstream so text+voice agree. The mismatch only occurs transiently if an emitter is fixed in T1 but not yet T3 — ship T1→T3 together within Phase 1 |
| Changing `events.py:403` default breaks a non-spoken consumer | Confirm in T1 Step 4 that `_on_announcement` is the only *spoken* consumer (the research map says so); keep the field `Optional[str] = None` |
| Cartesia `default_locale` wired wrong → wrong voice for an English user | Default it to `DEFAULT_LOCALE` and prefer always passing a concrete `language_code` (defense in depth); the fallback is only reached when the pipeline failed to resolve |
| `es` persona constant change boot-fails an `extra="forbid"` ack config | T6 is spec-gated and additive; the new constant has a default, no new required config key |
| Shared-tree commit sweeps another session's work | Every commit uses `git add -p` / explicit pathspec, never `git add .` or a whole foreign-dirty file |

---

## Self-review notes

- **Spec coverage:** screenshot bug (announcement EN / text DE) → T1+T3; British accent → T2; mid-answer flip → T2 (always-pinned `language_code`) + T6 (STT guard); `es` equality / doctrine → T2/T3/T4/T6; browser+telephony → T5. All three user-reported symptoms map to a task.
- **Type consistency:** `_bcp47` (T2) is referenced by T1/T3/T5; `resolve_output_language(pin, stt, text, conversation_language=...)` signature is used identically everywhere; `_conversation_language_for_announcement` (T3) reused by every emitter.
- **Known soft spots requiring a read-first:** the exact fake/fixture names in the speech tests (`make_pipeline_with_fakes`, `tts.last_language_code`) must be confirmed against `tests/unit/speech/test_output_language_pin.py` before writing — mirror what exists rather than inventing helpers.
