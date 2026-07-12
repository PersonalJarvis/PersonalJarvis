"""Standardized voice readbacks prefer an active idle realtime model."""

from __future__ import annotations

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
async def test_realtime_rejection_preserves_classic_tts_fallback() -> None:
    pipeline, tts, player, realtime = _pipeline(accepted=False)

    await pipeline._on_announcement(
        AnnouncementRequested(
            text="The research report is ready.",
            language="en",
            kind="completion",
        )
    )

    assert len(realtime.calls) == 1
    assert tts.calls == [("The research report is ready.", "en-US")]
    assert player.plays == 1
