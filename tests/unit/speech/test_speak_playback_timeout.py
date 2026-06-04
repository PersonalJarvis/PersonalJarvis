"""``_speak`` must never hang on a stalled audio device / TTS stream.

Live incident (2026-06-01): a flaky output device made PortAudio's blocking
``stream.write`` (and once the TTS chunk generator) wedge ``play_chunks``
forever. ``_speak`` had no timeout around playback, so it never returned. That
froze ``_handle_utterance`` → ``_active_session``, so the ``_state_loop``
``finally`` that resets ``self._state`` to ``IDLE`` (the wake-loop's re-arm
gate) never ran — and "Hey Jarvis" went permanently deaf until a restart.

These tests pin the contract: regardless of which part of playback stalls,
``_speak`` returns within the hard ceiling and aborts the player (AD-OE6 —
recover, never silently hang).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover - empty async generator
            yield  # type: ignore[unreachable]


@dataclass
class HangingPlayer:
    """A player whose ``play_chunks`` never completes on its own.

    Models the live failure mode: PortAudio's blocking ``stream.write`` (or a
    stalled TTS chunk generator) parks ``play_chunks`` indefinitely. ``stop()``
    is the only thing that releases it — exactly what ``_speak`` must invoke on
    a ceiling breach.
    """

    stop_calls: int = 0
    _release: asyncio.Event = field(default_factory=asyncio.Event)

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        # Drain whatever was handed in, then block until stop() releases us.
        async for _ in chunks:
            pass
        await self._release.wait()

    def stop(self) -> None:
        self.stop_calls += 1
        self._release.set()


def _make_pipeline() -> tuple[SpeechPipeline, HangingPlayer]:
    bus = EventBus()
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)
    player = HangingPlayer()
    pipeline._player = player  # type: ignore[assignment]
    # Tiny ceiling so the test is fast; the real default is generous.
    pipeline._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]
    return pipeline, player


@pytest.mark.asyncio
async def test_speak_returns_when_playback_stalls_and_barge_idles() -> None:
    """Barge monitor idles (returns False); playback wedges → ceiling aborts."""
    pipeline, player = _make_pipeline()

    async def _no_barge() -> bool:
        return False

    pipeline._barge_monitor = _no_barge  # type: ignore[assignment,method-assign]

    # If the ceiling is missing, _speak hangs forever and this wait_for raises.
    barged = await asyncio.wait_for(pipeline._speak("hallo", language="de"), timeout=5.0)

    assert barged is False
    assert player.stop_calls >= 1, "stalled playback must be aborted via stop()"


@pytest.mark.asyncio
async def test_speak_returns_when_both_playback_and_barge_stall() -> None:
    """Barge monitor never returns either → main-wait ceiling aborts playback."""
    pipeline, player = _make_pipeline()

    async def _hang_barge() -> bool:
        await asyncio.Event().wait()
        return False  # pragma: no cover - never reached

    pipeline._barge_monitor = _hang_barge  # type: ignore[assignment,method-assign]

    barged = await asyncio.wait_for(pipeline._speak("hallo", language="de"), timeout=5.0)

    assert barged is False
    assert player.stop_calls >= 1, "stalled playback must be aborted via stop()"
