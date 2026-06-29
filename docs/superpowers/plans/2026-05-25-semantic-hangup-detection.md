# Semantic Hang-Up Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jarvis end a voice session on *intent* ("kannst du jetzt gehen" (you can go now), "that's all for today") — not only on the literal word list — in German and English, on both the desktop microphone pipeline and Twilio telephony.

**Architecture:** Reuse the existing brain-signal mechanism but make it robust. The brain appends a control sentinel `[[END_CALL]]` to its reply when it judges the user wants to end; the pipeline detects the sentinel (not a fragile exact magic string) and strips it before TTS. A cleaned, unified bilingual explicit-command regex lives in one shared, stdlib-only module so the mic path and telephony stop drifting. Conservative bias: when unsure, stay on (the brain only emits the sentinel on a clear dismissal; the instant regex matches only unambiguous commands). Zero added latency — the sentinel rides the brain call that already happens; no new LLM call, no new dependency, no new wire enum.

**Tech Stack:** Python 3.11, `re` (stdlib), pytest (`asyncio_mode=auto`), existing fakes in `tests/fakes/`.

---

## Background the implementer needs

- **Two voice surfaces.** Microphone: `jarvis/speech/pipeline.py`. Telephony: `jarvis/telephony/session.py`. Both consume the *same* brain (`build_default_brain(tier="router")`), which loads `jarvis/brain/JARVIS_PERSONA.md` into its system prompt via `jarvis/brain/persona_loader.py:load_persona_prompt()`. Only the **fenced code block** after the `## System-Prompt` line in that `.md` is injected — edits outside the fence are documentation only.
- **Today's hang-up paths.**
  1. Pre-brain regex `HANGUP_RE` matched against the transcript (`pipeline.py:2224`, telephony `session.py:231`). Literal only.
  2. Post-brain exact-string match against `"goodbye, alex"` / `"auf wiedersehen, alex"` (`pipeline.py:2351` streamed, `pipeline.py:2432` non-streamed). Fragile — any paraphrase breaks it. Telephony has no brain-signal path.
- **`scrub_for_voice`** (`jarvis/brain/output_filter.py:334`) is the regex-only TTS sanitizer every spoken path runs through. Adding the sentinel-strip here is defense-in-depth so the token can never be spoken even if a detection site is missed. **It runs BEFORE we read the hang-up signal in some paths — so hang-up detection must read the RAW brain response, before scrub.**
- **Hang-up reason:** reuse `HANGUP_VOICE_PATTERN` (`jarvis/sessions/constants.py:33`) for the mic path (its docstring already covers "inferred from a closing intent") and the existing `"hangup_phrase"` for telephony. **No new enum value** → the five-layer parity test (`tests/unit/sessions/test_hangup_reason_parity.py`) stays untouched and green.
- **Test harnesses to reuse:** `tests/unit/speech/test_turn_taking.py` has `_make_pipeline(...)` (builds a `SpeechPipeline` via `__new__` with stubbed `_brain_with_ack` + `_speak`, `_config=None` so `_streaming_enabled()` is False → the non-streaming path runs), `FakeSTT`, `SlowPlayer`. `tests/unit/telephony/test_session.py` has `_make_session(...)`, `_drive_one_utterance(...)`, `_Sink`. Fakes `FakeBrain`/`FakeTTS` are in `tests/fakes/fake_telephony_stack.py` (`FakeTTS.calls` records every `(text, language_code)` synthesized — use it to prove the sentinel never reaches TTS).

## File structure

- **Create** `jarvis/speech/hangup.py` — single source of truth: unified bilingual explicit-command `HANGUP_RE`, the `END_CALL_SIGNAL` sentinel + detect/strip helpers, legacy farewell fallback. Stdlib-only (`re`), no `sounddevice` (safe for telephony to import; `jarvis/speech/__init__.py` is empty).
- **Create** `tests/unit/speech/test_hangup.py` — unit tests for the new module.
- **Modify** `jarvis/brain/output_filter.py` — `scrub_for_voice` strips `END_CALL_SIGNAL`.
- **Modify** `jarvis/brain/JARVIS_PERSONA.md` — replace the exact-farewell contract with the sentinel contract + conservative-bias instruction (DE+EN).
- **Modify** `jarvis/speech/pipeline.py` — import from `hangup.py`; replace the two exact-match blocks with sentinel detection on the raw response.
- **Modify** `jarvis/telephony/session.py` — import shared `HANGUP_RE` + helpers; add brain-signal sentinel detection after the brain call.
- **Modify** `tests/unit/brain/test_output_filter.py`, `tests/unit/brain/test_persona_loader.py`, `tests/unit/speech/test_turn_taking.py`, `tests/unit/telephony/test_session.py` — regression tests.

---

## Task 1: Shared hang-up module

**Files:**
- Create: `jarvis/speech/hangup.py`
- Test: `tests/unit/speech/test_hangup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/speech/test_hangup.py`:

```python
"""Unit tests for the shared hang-up intent module (jarvis/speech/hangup.py)."""

from __future__ import annotations

import pytest

from jarvis.speech.hangup import (
    END_CALL_SIGNAL,
    HANGUP_RE,
    contains_end_signal,
    is_legacy_farewell,
    strip_end_signal,
)


@pytest.mark.parametrize(
    "phrase",
    [
        # German explicit commands
        "auflegen",
        "leg auf",
        "lege auf",
        "legen sie auf",
        "aufgelegt",
        "tschüss",
        "tschuess",
        "beenden",
        "gespräch beenden",
        "auf wiederhören",
        "auf wiedersehen",
        "bis später",
        "gute nacht",
        "jarvis aus",
        "schluss jetzt",
        # English explicit commands
        "hang up",
        "hangup",
        "goodbye",
        "good bye",
        "good night",
        "goodnight",
        "bye bye",
        "stop jarvis",
        "exit",
        "quit",
        "ciao",
        "end the call",
    ],
)
def test_hangup_re_matches_explicit_commands(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is not None


@pytest.mark.parametrize(
    "phrase",
    [
        # Ambiguous-polite phrases are delegated to the brain (stay-on bias),
        # so the INSTANT regex must NOT fire on them.
        "vielen dank",
        "danke jarvis",
        "danke schön",
        "thanks jarvis",
        "das war's",
        # Normal speech must never match.
        "wie geht es dir",
        "erzähl mir was",
        "kannst du das nochmal machen",
        "geh mal auf die seite",
        "öffne die datei",
    ],
)
def test_hangup_re_ignores_ambiguous_and_normal_speech(phrase: str) -> None:
    assert HANGUP_RE.search(phrase) is None


def test_contains_end_signal_detects_token() -> None:
    assert contains_end_signal("Bis später, Alex. [[END_CALL]]") is True
    assert contains_end_signal("Bis später, Alex.") is False
    assert contains_end_signal("") is False
    assert contains_end_signal(None) is False  # type: ignore[arg-type]


def test_strip_end_signal_removes_token_and_trims() -> None:
    assert strip_end_signal("Bis später, Alex. [[END_CALL]]") == "Bis später, Alex."
    assert strip_end_signal("[[END_CALL]]") == ""
    assert strip_end_signal("Auf Wiedersehen.") == "Auf Wiedersehen."


def test_end_call_signal_is_the_documented_token() -> None:
    assert END_CALL_SIGNAL == "[[END_CALL]]"


@pytest.mark.parametrize(
    "phrase",
    [
        "goodbye, alex",
        "goodbye alex",
        "auf wiedersehen, alex",
        "auf wiedersehen alex",
        "goodbye, sir",
        "goodbye sir",
    ],
)
def test_is_legacy_farewell_matches_old_exact_phrases(phrase: str) -> None:
    assert is_legacy_farewell(phrase) is True


def test_is_legacy_farewell_rejects_other_text() -> None:
    assert is_legacy_farewell("auf wiedersehen alex war mir ein vergnügen") is False
    assert is_legacy_farewell("hallo alex") is False
    assert is_legacy_farewell("") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/speech/test_hangup.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.speech.hangup'`.

- [ ] **Step 3: Create the module**

Create `jarvis/speech/hangup.py`:

```python
"""Shared hang-up intent detection — single source of truth for both voice surfaces.

Two surfaces end a voice session: the desktop microphone pipeline
(``jarvis/speech/pipeline.py``) and Twilio telephony
(``jarvis/telephony/session.py``). They used to carry two separate, drifting
copies of the hang-up regex, and the microphone path additionally matched a
fragile *exact* farewell string emitted by the brain.

This module unifies both:

1. ``HANGUP_RE`` — explicit, unambiguous closing **commands** in German and
   English, matched against the transcript BEFORE the brain is called. Fast and
   deterministic. Deliberately narrow: ambiguous-polite phrases (a bare "thank
   you", "das war's") are NOT here — they are delegated to the brain, which has
   conversational context and a conservative "stay on when unsure" mandate.

2. ``END_CALL_SIGNAL`` — a control sentinel the brain appends to its reply when
   it judges the user wants to end (see ``JARVIS_PERSONA.md``). The pipeline
   detects it on the RAW brain response and strips it before TTS, so the brain
   may phrase the farewell naturally instead of emitting a magic string.

3. ``is_legacy_farewell`` — backward compatibility for the old exact phrases
   ("auf wiedersehen, alex" / "goodbye, alex"), so a brain instance still
   running the previous persona contract continues to hang up during rollout.

Standard-library only (``re``). It must stay free of ``sounddevice`` and any
heavy import so the telephony path can import it (``jarvis/speech/__init__.py``
is intentionally empty, so importing ``jarvis.speech.hangup`` pulls in nothing
else).
"""
from __future__ import annotations

import re
from typing import Final

# --- Explicit closing commands (pre-brain, instant) -----------------------
# Bilingual. Whisper mis-transcribes "auflegen" in many ways, so the German
# "auflegen" morphology is matched generously — it is the single most-used
# command and a false negative there is the worst failure. Ambiguous-polite
# phrases ("vielen dank", "danke jarvis", "das war's") are intentionally
# absent: they are handled by the brain under the stay-on-when-unsure mandate.
_HANGUP_PATTERNS: Final[tuple[str, ...]] = (
    # German — auflegen morphology + variants
    r"\bauflegen\b",
    r"\bauf\s*legen\b",
    r"\baufleg\w*\b",
    r"\bleg(e|t|en)?\s+auf\b",
    r"\blegs?\s+auf\b",
    r"\blegen sie auf\b",
    r"\baufgelegt\b",
    r"\bdrauf\s*leg\w*\b",
    r"\bableg\w*\b",
    # German — other explicit closings
    r"\btschüss\b",
    r"\btschuess\b",
    r"\bbeenden\b",
    r"\bgespräch beenden\b",
    r"\bauf wiederhören\b",
    r"\bauf wiederhoeren\b",
    r"\bauf wiedersehen\b",
    r"\bbis später\b",
    r"\bgute nacht\b",
    r"\bjarvis aus\b",
    r"\bjarvis ende\b",
    r"\bende jarvis\b",
    r"\bschluss jetzt\b",
    r"\bfertig jarvis\b",
    r"\bjarvis fertig\b",
    r"\bstopp jarvis\b",
    r"\bjarvis stopp\b",
    # English — explicit closings
    r"\bhang ?up\b",
    r"\bhang up the phone\b",
    r"\bend the call\b",
    r"\bgood ?bye\b",
    r"\bgood ?night\b",
    r"\bbye bye\b",
    r"\bbye jarvis\b",
    r"\bjarvis off\b",
    r"\boff jarvis\b",
    r"\bjavis off\b",
    r"\bshut up jarvis\b",
    r"\bstop jarvis\b",
    r"\bjarvis stop\b",
    r"\bexit\b",
    r"\bquit\b",
    r"\bciao\b",
)

HANGUP_RE: Final[re.Pattern[str]] = re.compile("|".join(_HANGUP_PATTERNS), re.IGNORECASE)

# --- Brain control sentinel (post-brain, semantic) ------------------------
END_CALL_SIGNAL: Final[str] = "[[END_CALL]]"


def contains_end_signal(text: str | None) -> bool:
    """True if the brain response carries the hang-up sentinel."""
    return bool(text) and END_CALL_SIGNAL in text


def strip_end_signal(text: str | None) -> str:
    """Remove the sentinel and trim surrounding whitespace.

    Safe on partial chunks and on the full response. ``scrub_for_voice`` also
    strips the sentinel for the production TTS path; this helper is the direct,
    dependency-free equivalent for call sites that do not scrub.
    """
    if not text:
        return text or ""
    return text.replace(END_CALL_SIGNAL, "").strip()


# --- Legacy exact-farewell fallback (backward compatibility) --------------
LEGACY_FAREWELL_PHRASES: Final[frozenset[str]] = frozenset(
    {
        "goodbye, alex",
        "goodbye alex",
        "auf wiedersehen, alex",
        "auf wiedersehen alex",
        "goodbye, sir",
        "goodbye sir",
    }
)


def is_legacy_farewell(normalized: str | None) -> bool:
    """True if ``normalized`` equals an old exact farewell phrase.

    ``normalized`` is expected pre-lowered and stripped of trailing ``!``/``.``
    by the caller (``text.strip().rstrip("!.").strip().lower()``).
    """
    return bool(normalized) and normalized in LEGACY_FAREWELL_PHRASES


__all__ = [
    "END_CALL_SIGNAL",
    "HANGUP_RE",
    "LEGACY_FAREWELL_PHRASES",
    "contains_end_signal",
    "is_legacy_farewell",
    "strip_end_signal",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/speech/test_hangup.py -q`
Expected: PASS (all parametrizations green).

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/hangup.py tests/unit/speech/test_hangup.py
git commit -m "feat(voice): shared hang-up module with END_CALL sentinel + unified bilingual regex"
```

---

## Task 2: scrub_for_voice strips the sentinel

**Files:**
- Modify: `jarvis/brain/output_filter.py` (import + early strip pass in `scrub_for_voice`)
- Test: `tests/unit/brain/test_output_filter.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_output_filter.py` (module already imports `scrub_for_voice`; if not, add `from jarvis.brain.output_filter import scrub_for_voice`):

```python
def test_scrub_strips_end_call_sentinel() -> None:
    from jarvis.speech.hangup import END_CALL_SIGNAL

    result = scrub_for_voice(f"Bis später, Alex. {END_CALL_SIGNAL}", language="de")
    assert END_CALL_SIGNAL not in result.cleaned
    assert result.cleaned.strip() == "Bis später, Alex."
    assert "stripped_end_signal" in result.actions


def test_scrub_sentinel_only_yields_empty() -> None:
    from jarvis.speech.hangup import END_CALL_SIGNAL

    result = scrub_for_voice(END_CALL_SIGNAL, language="de")
    assert result.cleaned == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/brain/test_output_filter.py -k end_call -q`
Expected: FAIL — sentinel still present in `cleaned` / `"stripped_end_signal"` not in actions.

- [ ] **Step 3: Implement the strip pass**

In `jarvis/brain/output_filter.py`, add the import near the other imports at the top of the file:

```python
from jarvis.speech.hangup import END_CALL_SIGNAL
```

Then in `scrub_for_voice`, immediately after the line `out = text` (currently `output_filter.py:369`), insert:

```python
    # 0. Hang-up control sentinel: the brain appends END_CALL_SIGNAL to signal
    #    session end. The signal is read upstream on the RAW response; here we
    #    guarantee it can never reach TTS (defense-in-depth). If the text was
    #    nothing but the token, return empty so the caller stays silent.
    if END_CALL_SIGNAL in out:
        out = out.replace(END_CALL_SIGNAL, "")
        actions.append("stripped_end_signal")
        if not out.strip():
            return ScrubResult(cleaned="", actions=actions, fallback_used=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/brain/test_output_filter.py -q`
Expected: PASS (new tests pass; the existing ~40 cases stay green).

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/output_filter.py tests/unit/brain/test_output_filter.py
git commit -m "feat(voice): strip END_CALL sentinel in scrub_for_voice (defense-in-depth)"
```

---

## Task 3: Persona contract — sentinel + conservative bias

**Files:**
- Modify: `jarvis/brain/JARVIS_PERSONA.md` (the fenced system-prompt block + the doc prose)
- Test: `tests/unit/brain/test_persona_loader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_persona_loader.py`:

```python
def test_persona_fence_carries_end_call_sentinel() -> None:
    from jarvis.brain.persona_loader import invalidate_cache, load_persona_prompt
    from jarvis.speech.hangup import END_CALL_SIGNAL

    invalidate_cache()
    try:
        prompt = load_persona_prompt()
    finally:
        invalidate_cache()
    assert prompt, "persona fence must load"
    assert END_CALL_SIGNAL in prompt, "persona must instruct the END_CALL sentinel"
    # Conservative bias must be spelled out: do not end when unsure.
    assert "do NOT" in prompt or "do not" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/brain/test_persona_loader.py -k end_call -q`
Expected: FAIL — `[[END_CALL]]` not yet in the persona.

- [ ] **Step 3: Edit the persona**

In `jarvis/brain/JARVIS_PERSONA.md`, **inside the fenced `## System-Prompt` block**, replace this exact text (currently lines 95-98):

```
- When the conversation ends or Alex dismisses you, reply with
  EXACTLY one of (depending on his language):
  EN: Goodbye, Alex.
  DE: Auf Wiedersehen, Alex.
```

with:

```
- ENDING THE CALL — only when Alex clearly wants to end the conversation
  (an explicit goodbye, a dismissal such as "you can go now" / "kannst du
  jetzt gehen" / "das war's für heute", or telling you to hang up): say a
  short, natural farewell in HIS language AND append the control token
  [[END_CALL]] as the very last characters of your reply.
    EN: "Goodbye, Alex. [[END_CALL]]" / "Until next time, Alex. [[END_CALL]]"
    DE: "Auf Wiedersehen, Alex. [[END_CALL]]" / "Bis später, Alex. [[END_CALL]]"
  The token is silent — it is stripped before anything is spoken and only
  tells the system to hang up. If you are NOT sure he wants to end (he merely
  paused, is thinking, or just thanked you), do NOT append the token and do
  NOT say goodbye — keep the conversation open.
```

Then update the documentation prose **outside** the fence so it stays accurate. Replace the section currently at lines 18-25:

```
## Hangup-Signal (Pipeline-Contract)

Die Pipeline erkennt Hangup wenn die Brain-Antwort (normalisiert) equals einer von:
- `"goodbye, alex"` (englisch)
- `"auf wiedersehen, alex"` (deutsch)

Der Brain MUSS exakt eine dieser beiden Phrasen ausgeben um aufzulegen — sprachabhängig
entsprechend der User-Sprache.
```

with:

```
## Hangup-Signal (Pipeline-Contract)

The pipeline hangs up when the brain response contains the control sentinel
`[[END_CALL]]` (single source of truth: `jarvis/speech/hangup.py`). The brain
speaks a natural farewell and appends the token; `scrub_for_voice` strips it
before TTS. Conservative bias: emit the token only on a clear intent to end.

Backward compatibility: the old exact phrases `"goodbye, alex"` /
`"auf wiedersehen, alex"` still trigger a hangup via `is_legacy_farewell`.
```

And update the cross-reference line (currently line 133):

```
- Hangup-Matcher: `pipeline.py` — normalized equals gegen `"goodbye, alex"` oder `"auf wiedersehen, alex"`
```

to:

```
- Hangup-Matcher: `jarvis/speech/hangup.py` — `contains_end_signal` ([[END_CALL]]) + `is_legacy_farewell` fallback; wired in `pipeline.py` and `telephony/session.py`
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/brain/test_persona_loader.py -q`
Expected: PASS (the new test plus the existing persona-loader tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/brain/JARVIS_PERSONA.md tests/unit/brain/test_persona_loader.py
git commit -m "feat(voice): persona emits [[END_CALL]] sentinel with stay-on-when-unsure bias"
```

---

## Task 4: Wire the microphone pipeline

**Files:**
- Modify: `jarvis/speech/pipeline.py` (imports; remove inline regex; streamed + non-streamed detection)
- Test: `tests/unit/speech/test_turn_taking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/speech/test_turn_taking.py` (reuses `_make_pipeline`, `FakeSTT`, `SlowPlayer` already defined there):

```python
@pytest.mark.asyncio
async def test_brain_end_call_sentinel_hangs_up_and_is_not_spoken() -> None:
    # Conservative-but-clear dismissal: STT text is NOT an explicit regex
    # command, so the brain decides — and signals end via the sentinel.
    pipe = _make_pipeline(
        FakeSTT(text="Ich glaube wir sind durch"),
        brain_response="Bis später, Alex. [[END_CALL]]",
        continue_listening_after_response=True,  # prove hangup overrides stay-open
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._spoken == [("Bis später, Alex.", "de")]  # sentinel stripped
    assert pipe._session_end_reason == "voice_pattern"
    assert pipe._hangup_event.is_set()


@pytest.mark.asyncio
async def test_polite_thanks_no_longer_auto_hangs_up() -> None:
    # Regression for the old over-eager regex: a bare "Vielen Dank" used to
    # match HANGUP_RE and end the call. Now it reaches the brain, which (no
    # sentinel) keeps the conversation open. Realizes "stay on when unsure".
    pipe = _make_pipeline(
        FakeSTT(text="Vielen Dank"),
        brain_response="Gern geschehen, Alex.",
        continue_listening_after_response=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._session_end_reason is None
    assert pipe._hangup_event.is_set() is False


@pytest.mark.asyncio
async def test_explicit_auflegen_still_hard_hangs_up_via_regex() -> None:
    pipe = _make_pipeline(FakeSTT(text="Auflegen bitte"))
    pipe._player = SlowPlayer()

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._hangup_event.is_set()
    assert pipe._player.stop_calls == 1  # "auflegen" stays an absolute kill switch


@pytest.mark.asyncio
async def test_brain_streaming_strips_sentinel_but_keeps_it_in_full_text() -> None:
    pipe = _make_pipeline(FakeSTT(text="x"))
    pipe._latency_tracker = None

    class _StreamBrain:
        async def generate_stream(self, _text: str, **_kw):
            for ch in ["Alles erledigt. ", "Bis später, Alex. ", "[[END_CALL]]"]:
                yield ch

    pipe._brain = _StreamBrain()

    full, _barged = await pipe._brain_streaming("x", "de")

    assert "[[END_CALL]]" in full  # detection in _handle_utterance reads this
    assert all("[[END_CALL]]" not in t for (t, _l) in pipe._spoken)  # never spoken
    assert any("Bis später" in t for (t, _l) in pipe._spoken)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/speech/test_turn_taking.py -k "end_call or polite_thanks or auflegen_still or streaming_strips" -q`
Expected: FAIL — `test_polite_thanks_no_longer_auto_hangs_up` fails (old regex matches "vielen dank"); the sentinel tests fail (sentinel spoken / not stripped / no detection).

- [ ] **Step 3: Add the import**

In `jarvis/speech/pipeline.py`, after the `from jarvis.sessions.constants import (...)` block (around line 64), add:

```python
from jarvis.speech.hangup import (
    HANGUP_RE,
    contains_end_signal,
    is_legacy_farewell,
)
```

- [ ] **Step 4: Remove the inline regex**

Delete the inline pattern block in `jarvis/speech/pipeline.py` — the lines from the comment `# Hangup-Pattern großzügig ...` (the "hang-up pattern generously matched" block) through the `HANGUP_RE = re.compile(...)` line (currently lines 122-180). Replace the whole block with a single pointer comment:

```python
# Hang-up patterns + the END_CALL sentinel live in jarvis/speech/hangup.py
# (shared, stdlib-only, also imported by jarvis/telephony/session.py).
```

(`HANGUP_RE` is now the imported symbol; the pre-brain check at the former line 2224 and the barge path at the former line 2544 keep working unchanged. `import re` stays — it is still used by `_STREAM_SENTENCE_END` and others.)

- [ ] **Step 5: Replace the streamed exact-match**

In `_handle_utterance`, replace this exact block (currently `pipeline.py:2350-2355`):

```python
            normalized = response.strip().rstrip("!.").strip().lower()
            is_hangup = normalized in (
                "goodbye, alex", "goodbye alex",
                "auf wiedersehen, alex", "auf wiedersehen alex",
                "goodbye, sir", "goodbye sir",
            )
```

with:

```python
            normalized = response.strip().rstrip("!.").strip().lower()
            # `response` is the RAW streamed full text (still carries the
            # sentinel); the spoken sentences were already scrubbed inside
            # _brain_streaming. Legacy exact farewells stay supported.
            is_hangup = contains_end_signal(response) or is_legacy_farewell(normalized)
```

- [ ] **Step 6: Replace the non-streamed exact-match (detect BEFORE scrub)**

In the non-streaming branch, the response is scrubbed (sentinel removed) *before* the current detection — so detection must move ahead of the scrub. First, insert the detection just before the scrub block. Find this line (currently `pipeline.py:2410`):

```python
        # Phase-1-Output-Filter (Persona-Mandat): Tool-JSON, Stacktraces,
```

and insert immediately ABOVE it:

```python
        # Hang-up intent must be read from the RAW brain response, BEFORE
        # scrub_for_voice strips the [[END_CALL]] sentinel below.
        _normalized_raw = response.strip().rstrip("!.").strip().lower()
        is_hangup = contains_end_signal(response) or is_legacy_farewell(_normalized_raw)

```

Then remove the now-redundant recomputation. Replace this exact block (currently `pipeline.py:2429-2436`):

```python
        # Brain-basiertes Hangup: Claude antwortet bei Hangup-Intent mit exakt
        # "Goodbye, Alex." (EN) oder "Auf Wiedersehen, Alex." (DE).
        normalized = response.strip().rstrip("!.").strip().lower()
        is_hangup = normalized in (
            "goodbye, alex", "goodbye alex",
            "auf wiedersehen, alex", "auf wiedersehen alex",
            "goodbye, sir", "goodbye sir",  # Backward-Compat
        )
        # Jarvis spricht — Orb-Mode wechselt zur Speak-Wellenform
```

with:

```python
        # Jarvis spricht — Orb-Mode wechselt zur Speak-Wellenform
```

(The `is_hangup` used by the `if is_hangup:` block right below now comes from the raw-response detection inserted above the scrub.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/unit/speech/test_turn_taking.py -q`
Expected: PASS (the four new tests plus all existing turn-taking tests).

- [ ] **Step 8: Commit**

```bash
git add jarvis/speech/pipeline.py tests/unit/speech/test_turn_taking.py
git commit -m "feat(voice): mic pipeline ends on [[END_CALL]] sentinel; drop fragile exact-match + over-eager regex"
```

---

## Task 5: Wire telephony

**Files:**
- Modify: `jarvis/telephony/session.py` (import shared regex + helpers; brain-signal detection)
- Test: `tests/unit/telephony/test_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/telephony/test_session.py` (reuses `_make_session`, `_drive_one_utterance`, `_Sink`, and the fakes already imported):

```python
async def test_brain_end_call_sentinel_ends_call_after_speaking():
    sink = _Sink()
    brain = FakeBrain("Auf Wiedersehen, Alex. [[END_CALL]]")
    tts = FakeTTS(ms_per_char=2)
    session = _make_session(
        sink,
        stt=FakeSTT(["Ich glaube wir sind durch"]),  # not an explicit regex command
        brain=brain,
        tts=tts,
    )

    await _drive_one_utterance(session)

    assert brain.prompts == ["Ich glaube wir sind durch"]  # brain WAS reached
    assert session.ended
    assert session.status == CALL_COMPLETED
    assert session.end_reason == "hangup_phrase"
    assert tts.calls, "the farewell must be spoken before hanging up"
    assert all("[[END_CALL]]" not in text for (text, _lang) in tts.calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/telephony/test_session.py -k end_call -q`
Expected: FAIL — `session.ended` is False (no brain-signal path yet) and/or the sentinel reaches TTS.

- [ ] **Step 3: Swap the regex for the shared module**

In `jarvis/telephony/session.py`, replace the self-contained regex block (currently the comment at lines 64-68, the `_HANGUP_PATTERNS` tuple at 69-87, and the `HANGUP_RE = re.compile(...)` at line 88) with an import. Put the import with the other imports near the top of the file:

```python
from jarvis.speech.hangup import HANGUP_RE, contains_end_signal, is_legacy_farewell
```

and delete the old `_HANGUP_PATTERNS`/`HANGUP_RE` definition. Leave a short comment where the block was:

```python
# Hang-up regex + the END_CALL sentinel are shared with the mic path via
# jarvis/speech/hangup.py (stdlib-only — no sounddevice import on this path).
```

`HANGUP_RE` is re-exported (it is already listed in `__all__` at the bottom of the file), so `tests/unit/telephony/test_session.py` keeps importing it from `jarvis.telephony.session`. If `import re` is now unused in the file, remove it (ruff will flag it in the final task).

- [ ] **Step 4: Add brain-signal detection in the turn loop**

In `_run_turn`, replace this exact block (currently `session.py:235-244`):

```python
            response = await self._think(text)
            spoken = scrub_for_voice(response, language=self._lang_short()).cleaned
            if not spoken.strip():
                # Always-speak invariant (AD-OE6): never leave the caller in
                # silence. Use a minimal acknowledgement.
                spoken = self._fallback_phrase()

            frames = await self._speak(spoken)
            self._turns += 1
            self._publish_turn(text, response, frames)
```

with:

```python
            response = await self._think(text)
            # Brain-signal hangup: the brain appends [[END_CALL]] on a clear
            # intent to end (mirrors the mic path). Read it from the RAW
            # response BEFORE scrub_for_voice strips the sentinel below.
            end_requested = contains_end_signal(response) or is_legacy_farewell(
                response.strip().rstrip("!.").strip().lower()
            )
            spoken = scrub_for_voice(response, language=self._lang_short()).cleaned
            if not spoken.strip():
                # Always-speak invariant (AD-OE6): never leave the caller in
                # silence. Use a minimal acknowledgement.
                spoken = self._fallback_phrase()

            frames = await self._speak(spoken)
            self._turns += 1
            self._publish_turn(text, response, frames)
            if end_requested:
                await self.end(reason="hangup_phrase", status=CALL_COMPLETED)
                return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/telephony/test_session.py -q`
Expected: PASS (the new test plus all existing telephony tests — including the regex parametrizations, which still expect "beenden"/"goodbye" to match and "danke schön" (thank you) not to).

- [ ] **Step 6: Commit**

```bash
git add jarvis/telephony/session.py tests/unit/telephony/test_session.py
git commit -m "feat(telephony): end call on [[END_CALL]] sentinel; share hang-up regex with mic path"
```

---

## Task 6: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full touched-surface suite**

Run:

```bash
python -m pytest tests/unit/speech/test_hangup.py tests/unit/speech/test_turn_taking.py tests/unit/telephony/test_session.py tests/unit/brain/test_output_filter.py tests/unit/brain/test_persona_loader.py tests/unit/sessions/test_hangup_reason_parity.py -q
```

Expected: PASS, all collected tests green. The parity test proves no enum drift was introduced.

- [ ] **Step 2: Lint the touched files**

Run:

```bash
ruff check jarvis/speech/hangup.py jarvis/speech/pipeline.py jarvis/telephony/session.py jarvis/brain/output_filter.py
```

Expected: no errors. If `import re` is now unused in `jarvis/telephony/session.py`, remove it and re-run.

- [ ] **Step 3: Guard against a forbidden import on the telephony path**

Run:

```bash
python -c "import jarvis.speech.hangup; import sys; assert 'sounddevice' not in sys.modules, 'hangup.py must not pull in sounddevice'; print('ok: hangup import is light')"
```

Expected: `ok: hangup import is light`.

- [ ] **Step 4: Commit any lint fixups**

```bash
git add -A
git commit -m "chore(voice): lint fixups for semantic hang-up wiring" || echo "nothing to commit"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** unified bilingual regex (Task 1), END_CALL sentinel detect/strip (Task 1) + scrub strip (Task 2), persona contract + conservative bias (Task 3), mic streamed+non-streamed wiring (Task 4), telephony wiring (Task 5), reuse of `HANGUP_VOICE_PATTERN`/`"hangup_phrase"` with the parity test as the no-drift guard (Task 6). The spec's `BRAIN_HANGUP_INSTRUCTION` Python constant was intentionally dropped (YAGNI): the persona `.md` fence is the injected source of truth, and `test_persona_fence_carries_end_call_sentinel` is the anti-drift guard tying it to `END_CALL_SIGNAL`.
- **Regex scope refinement vs. spec:** the spec listed "exit/quit/beenden" among removals; the plan keeps them (the existing telephony test asserts "beenden" matches) and removes only the genuinely dangerous polite-thanks phrases ("vielen dank", "danke jarvis", "thanks jarvis", "das war's") — this is the precise realization of the stay-on-when-unsure decision without breaking green tests.
- **Type/name consistency:** `contains_end_signal`, `strip_end_signal`, `is_legacy_farewell`, `END_CALL_SIGNAL`, `HANGUP_RE` are used identically across Tasks 1, 2, 4, 5.
- **Detection-before-scrub:** both the non-streamed mic path and telephony read the sentinel from the raw response before `scrub_for_voice` removes it; the streamed mic path reads it from the raw accumulated full text returned by `_brain_streaming` while the per-sentence scrub keeps it out of TTS.
