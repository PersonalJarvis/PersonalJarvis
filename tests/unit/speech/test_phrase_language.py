"""Canned-phrase language picking must survive full Whisper language NAMES.

Live bug 2026-06-09 ("Jarvis answers almost every turn with an English
standard phrase like 'could you explain that more'"): the utterance language
flows from the STT transcript as a full lowercased language NAME —
``lang = (transcript.language or "en").lower()`` yields ``"german"`` for Groq
Whisper — but every canned-phrase picker tested ``lang.startswith("de")``.
``"german"`` does not start with ``"de"``, so ALL AD-OE6 fallback phrases
(clarify question, action-done ack, brain-timeout, brain-unavailable,
STT-unavailable, smalltalk fallback) were spoken in ENGLISH to a German
speaker. The German variants were dead code from day one: the existing tests
only ever passed ``"de"``, never the live value ``"german"``.

These tests pin the live value. The shared normalizer ``_phrase_lang`` maps
language names AND BCP-47-ish codes onto the canned-phrase keys ("de"/"en").
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.pipeline import (
    _ACTION_DONE_PHRASE,
    _BRAIN_UNAVAILABLE_PHRASE,
    _CLARIFY_QUESTION_PHRASE,
    _STT_UNAVAILABLE_PHRASE,
    _TIMEOUT_NO_ANSWER_PHRASE,
    _TIMEOUT_TOOL_STALL_PHRASE,
    SpeechPipeline,
    TurnTakingState,
    _smalltalk_fallback_for_non_substantive,
)


class _FakeBrain:
    def __init__(self, *, executed_action: bool = False) -> None:
        self._last_turn_all_failed = False
        self._last_turn_suppressed = False
        self._last_turn_executed_action_tool = executed_action


def _make_pipe(
    *, executed_action: bool = False, clarify_enabled: bool = True
) -> SpeechPipeline:
    """Minimal ``SpeechPipeline`` stub (same pattern as test_clarify_question)."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._brain = _FakeBrain(executed_action=executed_action)

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = clarify_enabled
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._spoken: list[tuple[str, str | None]] = []
    pipe._state_history: list[TurnTakingState] = []

    async def _fake_speak(
        text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
        pipe._spoken.append((text, language))
        return True

    async def _fake_set_turn_state(state: TurnTakingState) -> None:
        pipe._state_history.append(state)

    pipe._speak = _fake_speak  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    return pipe


# --------------------------------------------------------------------------- #
# The shared normalizer                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        # Live Whisper value (the bug): full lowercased language name.
        ("german", "de"),
        ("German", "de"),
        # German's own name for itself (defensive; startswith already covers it).
        ("deutsch", "de"),
        # BCP-47-ish codes (config pins, announcements).
        ("de", "de"),
        ("de-DE", "de"),
        # English in all shapes stays English.
        ("english", "en"),
        ("en", "en"),
        ("en-US", "en"),
        # Spanish is a first-class supported language (Runtime Output Language
        # doctrine): names AND codes map to "es", not the English fallback.
        ("spanish", "es"),
        ("es", "es"),
        ("es-ES", "es"),
        # Unknown / missing → DEFAULT_LOCALE fallback.
        ("", "en"),
        (None, "en"),
        ("klingon", "en"),
    ],
)
def test_phrase_lang_maps_names_and_codes(lang: str | None, expected: str) -> None:
    phrase_lang = getattr(pipeline_mod, "_phrase_lang", None)
    assert phrase_lang is not None, "_phrase_lang normalizer is missing"
    assert phrase_lang(lang) == expected


def test_all_canned_phrase_tables_cover_de_en_es() -> None:
    # "Works for every configured language": no spoken canned table may be
    # de/en-only — an es speaker must never fall back to the wrong language.
    for table in (
        _BRAIN_UNAVAILABLE_PHRASE,
        _STT_UNAVAILABLE_PHRASE,
        _TIMEOUT_TOOL_STALL_PHRASE,
        _TIMEOUT_NO_ANSWER_PHRASE,
        _CLARIFY_QUESTION_PHRASE,
        _ACTION_DONE_PHRASE,
    ):
        assert {"de", "en", "es"} <= set(table), table


@pytest.mark.asyncio
async def test_clarify_question_is_spanish_for_whisper_language_name() -> None:
    # An es turn (Whisper name "spanish") must get the Spanish clarify phrase,
    # not the English one — the same regression class as the German case.
    pipe = _make_pipe(clarify_enabled=True)
    await pipe._handle_silent_brain_turn("spanish")
    assert pipe._spoken == [(_CLARIFY_QUESTION_PHRASE["es"], "es")], pipe._spoken


# --------------------------------------------------------------------------- #
# The live regression: lang="german" must select the GERMAN phrases            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_clarify_question_is_german_for_whisper_language_name() -> None:
    # Live log 2026-06-09 17:52: lang flows in as "german" (Whisper name) and
    # the clarify fallback spoke "What do you mean exactly?" to a German user.
    pipe = _make_pipe(clarify_enabled=True)
    await pipe._handle_silent_brain_turn("german")
    assert pipe._spoken == [(_CLARIFY_QUESTION_PHRASE["de"], "de")], pipe._spoken


@pytest.mark.asyncio
async def test_action_done_ack_is_german_for_whisper_language_name() -> None:
    pipe = _make_pipe(executed_action=True)
    await pipe._handle_silent_brain_turn("german", "öffne chrome")  # i18n-allow
    assert pipe._spoken == [(_ACTION_DONE_PHRASE["de"], "de")], pipe._spoken


@pytest.mark.asyncio
async def test_brain_timeout_phrase_is_german_for_whisper_language_name() -> None:
    # A bare brain timeout with no tool evidence honestly admits "couldn't find
    # that out" — and resolves the Whisper language NAME "german" to the German
    # phrase, not the English fallback (the 2026-06-09 regression class).
    pipe = _make_pipe()
    await pipe._speak_brain_timeout("german")
    assert pipe._spoken == [(_TIMEOUT_NO_ANSWER_PHRASE["de"], "de")], pipe._spoken


def test_smalltalk_fallback_is_german_for_whisper_language_name() -> None:
    answer = _smalltalk_fallback_for_non_substantive("wie geht es dir", "german")
    assert answer is not None
    assert "Ruben" in answer
    assert "geht's gut" in answer, answer  # the German variant, not "I'm good"


@pytest.mark.asyncio
async def test_english_speaker_still_gets_english_phrases() -> None:
    # Regression guard: the fix must not over-rotate — a genuinely English
    # utterance keeps the English phrase set.
    pipe = _make_pipe(clarify_enabled=True)
    await pipe._handle_silent_brain_turn("english")
    assert pipe._spoken == [(_CLARIFY_QUESTION_PHRASE["en"], "en")], pipe._spoken
