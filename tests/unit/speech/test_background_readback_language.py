"""The OpenClaw background-completed readback ("Done." / "That didn't work.")
must follow the resolved conversation language, not a hardcoded German literal.

Forensic 2026-06-23 family: announcement emitters bypassed the resolver. This
one hardcoded German text + ``language="de"`` + ``language_code="de-DE"``, so an
English- or Spanish-speaking user heard "Fertig." in German. The readback now
resolves the language through ``_conversation_language_for_announcement`` and
picks a de/en/es phrase, with the matching BCP-47 voice pin.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import JarvisAgentBackgroundCompleted
from tests.unit.speech.test_announcement_bridge import (
    FakePlayer,
    FakeTTS,
    _make_pipeline,
)


@pytest.mark.asyncio
async def test_background_readback_speaks_english_for_english_conversation() -> None:
    bus = EventBus()
    tts = FakeTTS()
    pipe = _make_pipeline(tts, bus, FakePlayer())
    pipe._brain = SimpleNamespace(reply_language="auto", conversation_language="en")

    await bus.publish(
        JarvisAgentBackgroundCompleted(success=True, summary="", duration_s=1.0)
    )

    assert tts.calls, "expected a background readback to be synthesized"
    text, lang_code = tts.calls[-1]
    assert lang_code == "en-US"
    assert "Fertig" not in text


@pytest.mark.asyncio
async def test_background_readback_stays_german_for_german_conversation() -> None:
    bus = EventBus()
    tts = FakeTTS()
    pipe = _make_pipeline(tts, bus, FakePlayer())
    pipe._brain = SimpleNamespace(reply_language="auto", conversation_language="de")

    await bus.publish(
        JarvisAgentBackgroundCompleted(success=True, summary="", duration_s=1.0)
    )

    assert tts.calls, "expected a background readback to be synthesized"
    text, lang_code = tts.calls[-1]
    assert lang_code == "de-DE"
    assert "Fertig" in text
