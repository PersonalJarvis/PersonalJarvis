"""Standardized voice readbacks prefer an active idle realtime model."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        del voice
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield AudioChunk(pcm=b"", sample_rate=24_000)


@dataclass
class _FakePlayer:
    plays: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self.plays += 1
        async for _chunk in chunks:
            pass

    def stop(self) -> None:
        return None


class _FakeRealtimeHandle:
    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted
        self.calls: list[dict[str, object]] = []

    async def deliver_announcement(self, **kwargs: object) -> bool:
        self.calls.append(kwargs)
        return self.accepted


def _pipeline(*, accepted: bool) -> tuple[
    SpeechPipeline, _FakeTTS, _FakePlayer, _FakeRealtimeHandle
]:
    tts = _FakeTTS()
    player = _FakePlayer()
    realtime = _FakeRealtimeHandle(accepted=accepted)
    pipeline = SpeechPipeline(tts=tts, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._active_voice_mode = "realtime"
    pipeline._active_realtime_handle = realtime
    pipeline._turn_state = TurnTakingState.LISTENING
    pipeline._active_realtime_provider = "fake-live"
    return pipeline, tts, player, realtime


@pytest.mark.asyncio
async def test_subagent_readback_is_handed_to_active_realtime_model() -> None:
    pipeline, tts, player, realtime = _pipeline(accepted=True)

    await pipeline._on_announcement(
        AnnouncementRequested(
            text="The research report is ready.",
            language="en",
            kind="subagent",
            detail="artifact: report.md",
        )
    )

    assert realtime.calls == [
        {
            "text": "The research report is ready.",
            "language": "en",
            "spoken_kind": "subagent",
            "detail": "artifact: report.md",
        }
    ]
    assert tts.calls == []
    assert player.plays == 0


@pytest.mark.asyncio
async def test_dead_session_rejection_preserves_classic_tts_fallback() -> None:
    """Classic TTS resumes only after the realtime handle is fully removed."""
    pipeline, tts, player, realtime = _pipeline(accepted=False)
    pipeline._active_realtime_handle = None

    await pipeline._on_announcement(
        AnnouncementRequested(
            text="The research report is ready.",
            language="en",
            kind="completion",
        )
    )

    assert realtime.calls == []
    assert tts.calls == [("The research report is ready.", "en-US")]
    assert player.plays == 1


@pytest.mark.asyncio
async def test_busy_live_session_defers_owed_readback() -> None:
    """A healthy busy call parks the readback; no second voice speaks."""
    pipeline, tts, player, realtime = _pipeline(accepted=False)

    event = AnnouncementRequested(
        text="The research report is ready.",
        language="en",
        kind="completion",
    )
    await pipeline._on_announcement(event)

    assert len(realtime.calls) == 1
    assert tts.calls == []
    assert player.plays == 0
    assert pipeline._deferred_announcements == [event]


@pytest.mark.asyncio
async def test_busy_live_session_drops_ephemeral_preamble() -> None:
    """A stale preamble is dropped, never spoken by the classic voice."""
    pipeline, tts, player, realtime = _pipeline(accepted=False)

    await pipeline._on_announcement(
        AnnouncementRequested(
            text="I am searching your wiki right now.",
            language="en",
            kind="preamble",
        )
    )

    assert len(realtime.calls) == 1
    assert tts.calls == []
    assert player.plays == 0
    assert pipeline._deferred_announcements == []


@pytest.mark.asyncio
async def test_deferred_readback_is_replayed_to_the_idle_live_model() -> None:
    """At the turn boundary the parked readback reaches the live voice."""
    pipeline, tts, player, realtime = _pipeline(accepted=False)

    event = AnnouncementRequested(
        text="The research report is ready.",
        language="en",
        kind="completion",
    )
    await pipeline._on_announcement(event)
    assert pipeline._deferred_announcements == [event]

    realtime.accepted = True
    await pipeline._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.05)

    assert len(realtime.calls) == 2
    assert tts.calls == []
    assert player.plays == 0
    assert pipeline._deferred_announcements == []
