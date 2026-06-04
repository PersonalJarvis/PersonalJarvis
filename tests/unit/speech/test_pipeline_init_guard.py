"""Regression guard: ``SpeechPipeline.__init__`` must never break voice silently.

The 2026-05-28 "Hey Jarvis dead" incident: a ``self._bus.subscribe(EVENT, ...)``
line in ``__init__`` referenced an event name that was not imported
(a ``NameError`` on an unimported event class). Constructing the pipeline
therefore raised, the desktop bootstrap swallowed it into a warning, and
voice died silently.

Constructing the pipeline with a REAL ``EventBus`` runs the whole subscribe
block. Any undefined / unimported event name in ``__init__`` now fails THIS test
(NameError surfaces here) instead of silently killing voice at boot — the bug
can no longer reach committed code unnoticed.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    AnnouncementRequested,
    AudioOutFirst,
    OpenClawAnnouncement,
    OpenClawBackgroundCompleted,
    VoiceMuteToggleRequested,
)
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


def test_init_runs_full_subscribe_block_without_nameerror() -> None:
    # If any subscribed event name in __init__ is undefined, this construction
    # raises NameError and the test fails — exactly the bug we are guarding.
    bus = EventBus()
    pipeline = SpeechPipeline(tts=_FakeTTS(), bus=bus, enable_whisper_wake=False)

    assert pipeline is not None
    # The subscribe block actually executed (handlers landed on the bus).
    assert bus._subscribers, "pipeline __init__ registered no bus subscribers"


def test_init_subscribes_the_known_announcement_and_audio_events() -> None:
    """Each of these subscriptions is one line that must reference an imported
    name; asserting they registered proves every such line resolved."""
    bus = EventBus()
    SpeechPipeline(tts=_FakeTTS(), bus=bus, enable_whisper_wake=False)

    for event_type in (
        AnnouncementRequested,
        OpenClawBackgroundCompleted,
        OpenClawAnnouncement,
        VoiceMuteToggleRequested,
        AudioOutFirst,
    ):
        assert bus._subscribers.get(event_type), (
            f"{event_type.__name__} was not subscribed in __init__"
        )
