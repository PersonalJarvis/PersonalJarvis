"""The non-streaming voice path must not announce a failure that never happened.

``scrub_for_voice`` ends with a residue guard: a filter fired AND fewer than
three alphanumeric characters survived → the whole text becomes the canned
error phrase ("Es trat ein Fehler auf." / "An error occurred."). That guard is
right for what it defends — a machine leak must never reach TTS — but it
conflates two turns that have nothing in common:

* a leak was cut out (tool JSON, stacktrace, raw repr, shell command): the
  error phrase is honest, something really did go wrong;
* only harmless prose was cut out (filler opener, honorific, self-reference,
  jargon, markdown): nothing failed, the model just said nothing of substance.

Measured 2026-08-18 before the fix: a turn whose whole content was "Tolle
Frage!" was spoken as "Es trat ein Fehler auf.". The streaming path was already
covered from two sides (``scrub_gate._is_stream_safe_residue`` and the
per-sentence drop in ``_brain_streaming``); the non-streaming call sites were
not. These tests pin the split for the two whole-turn chokepoints in the
pipeline: the bus announcement handler and the non-streaming brain turn.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.output_filter import FALLBACK_PHRASES
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import Transcript
from tests.unit.speech.test_announcement_bridge import (
    FakePlayer,
    FakeTTS,
    _make_pipeline,
)

# A turn made of nothing but a filler opener. Every filter that touches it is a
# prose transform — no machine data is involved anywhere.
FILLER_DE = "Tolle Frage!"  # i18n-allow: German filler opener under test
FILLER_EN = "Great question."
# A real leak: the tool-call wrapper is removed and the residue guard then fires
# on what is left. The user IS owed the error phrase here.
TOOL_LEAK = (
    "<function_calls><invoke name='read_file'>"
    "<parameter name='path'>x</parameter></invoke></function_calls>"
)


def _spoken(tts: FakeTTS) -> list[str]:
    return [text for text, _lang in tts.calls]


async def _run_announcement(text: str, language: str) -> list[str]:
    bus = EventBus()
    tts = FakeTTS()
    _make_pipeline(tts, bus, FakePlayer())
    await bus.publish(AnnouncementRequested(text=text, language=language))
    return _spoken(tts)


async def _run_nonstreaming_turn(response: str, language: str) -> list[str]:
    """Drive ONE complete turn down the non-streaming branch of the pipeline."""
    bus = EventBus()
    tts = FakeTTS()
    pipe = _make_pipeline(tts, bus, FakePlayer())
    # No ``generate_stream`` on the brain → the non-streaming fallback branch.
    pipe._streaming_enabled = lambda: False  # type: ignore[method-assign]
    pipe._output_language = lambda *_a, **_kw: language  # type: ignore[method-assign]
    pipe._brain = SimpleNamespace(reply_language=language, conversation_language=language)

    async def _transcribe(_pcm: bytes) -> Transcript:
        # i18n-allow: spoken German user prompt in the test
        prompt = "Wie ist das Wetter?"
        return Transcript(text=prompt, language=language, confidence=0.99)

    async def _brain(*_a: object, **_kw: object) -> str:
        return response

    pipe._transcribe_final = _transcribe  # type: ignore[method-assign]
    pipe._brain_with_ack = _brain  # type: ignore[method-assign]
    await pipe._handle_utterance_turn(bytes(3200), skip_completion=True)
    return _spoken(tts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "language"),
    [(FILLER_DE, "de"), (FILLER_EN, "en")],
)
async def test_filler_only_turn_is_silent_not_an_error(
    response: str, language: str
) -> None:
    """A substance-free turn says nothing — it never claims a failure."""
    assert await _run_nonstreaming_turn(response, language) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "language"),
    [(FILLER_DE, "de"), (FILLER_EN, "en")],
)
async def test_filler_only_announcement_is_silent_not_an_error(
    text: str, language: str
) -> None:
    """Same verdict on the bus-announcement chokepoint."""
    assert await _run_announcement(text, language) == []


@pytest.mark.asyncio
async def test_scrubbed_leak_still_speaks_the_fallback_on_a_turn() -> None:
    """A machine leak really is a failure — the error phrase must survive."""
    assert await _run_nonstreaming_turn(TOOL_LEAK, "de") == [FALLBACK_PHRASES["de"]]


@pytest.mark.asyncio
async def test_scrubbed_leak_still_speaks_the_fallback_on_an_announcement() -> None:
    assert await _run_announcement(TOOL_LEAK, "de") == [FALLBACK_PHRASES["de"]]


@pytest.mark.asyncio
async def test_ordinary_answer_is_untouched() -> None:
    """The guard only ever fires on a whole-text fallback, never on real prose."""
    answer = "Es sind achtzehn Grad und sonnig."  # i18n-allow: German voice answer under test
    assert await _run_nonstreaming_turn(answer, "de") == [answer]
