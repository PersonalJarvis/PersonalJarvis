"""``_on_announcement`` must resolve the announcement's spoken language through
the ONE authoritative resolver (``_output_language``) instead of trusting
``event.language`` verbatim.

Forensic 2026-06-23 (screenshot): a German voice chat spoke an English
"ANNOUNCEMENT" while the text answer was German. Root cause: the announcement
handler read ``event.language`` literally (``(event.language or "de").lower()``)
and never consulted the live ``brain.reply_language`` pin, the sticky
``conversation_language`` or the announcement TEXT — so whatever (possibly
stale/wrong) language an emitter stamped on the event drove the TTS voice.

These tests pin the contract: the event tag is only a HINT (passed where the
STT tag normally goes); the pin wins, then conversation stickiness for thin
turns, then the detected language of the announcement text, then the tag.
See CLAUDE.md "Runtime Output Language".
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from tests.unit.speech.test_announcement_bridge import (
    FakePlayer,
    FakeTTS,
    _make_pipeline,
)


def _last_language_code(tts: FakeTTS) -> str | None:
    assert tts.calls, "expected the announcement to reach tts.synthesize"
    return tts.calls[-1][1]


@pytest.mark.asyncio
async def test_announcement_uses_text_not_mistagged_event_language() -> None:
    # The emitter stamped "en" but the text is clearly German. The resolver must
    # speak German (text detection beats the stale event tag), not trust the tag.
    bus = EventBus()
    tts = FakeTTS()
    pipe = _make_pipeline(tts, bus, FakePlayer())
    pipe._brain = SimpleNamespace(reply_language="auto", conversation_language="de")

    await bus.publish(
        AnnouncementRequested(
            text="Das Dokument ist fertig und liegt bereit.", language="en"  # i18n-allow: simulated German voice text under test (mistagged as "en")
        )
    )

    assert _last_language_code(tts) == "de-DE"


@pytest.mark.asyncio
async def test_announcement_honors_hard_pin_over_event_tag() -> None:
    # A hard de pin must win over an English event tag AND English text.
    bus = EventBus()
    tts = FakeTTS()
    pipe = _make_pipeline(tts, bus, FakePlayer())
    pipe._brain = SimpleNamespace(reply_language="de", conversation_language="")

    await bus.publish(
        AnnouncementRequested(text="The file is ready now.", language="en")
    )

    assert _last_language_code(tts) == "de-DE"


@pytest.mark.asyncio
async def test_announcement_untagged_follows_text_not_german_default() -> None:
    # No event language at all: today it silently defaults to German ("de-DE").
    # It must instead mirror the announcement text (here: English).
    bus = EventBus()
    tts = FakeTTS()
    pipe = _make_pipeline(tts, bus, FakePlayer())
    pipe._brain = SimpleNamespace(reply_language="auto", conversation_language="")

    await bus.publish(
        AnnouncementRequested(text="The file is ready and waiting.", language=None)
    )

    assert _last_language_code(tts) == "en-US"
