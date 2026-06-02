"""Pin the hang-up-during-TTS abort contract of the streaming voice path.

Live bug 2026-06-01 ("Jarvis shows SPEAKING the whole time even though it is
not speaking"): a vision question ("Was siehst du hier?") runs a Gemini
tool-use loop. The brain keeps streaming, so ``_merged_chunks`` never receives
its end-sentinel and ``play_task`` in ``_brain_streaming`` never completes.
When the user then hits "auflegen" (the hard kill-switch), ``_player.stop()``
alone is not enough: the ``await asyncio.wait({play_task, barge_task})`` is not
watching the hangup event, so it blocks forever. ``_handle_utterance`` never
returns, ``_active_session`` never reaches its ``IDLE`` finally, and the
supervisor — and therefore the UI voice-state — stays wedged on SPEAKING. The
log evidence shows the user pressing hangup repeatedly with no effect.

Fix: ``_brain_streaming`` waits on the hangup event too and aborts the turn the
instant the user hangs up, exactly like a barge-in, so the session loop can
unwind to its IDLE finally.

The fakes record causality, not wall-clock timing, so the assertions are
deterministic without any real audio device or network.
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
class _SingleSentenceTTS:
    name: str = "single-sentence-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(
            pcm=text.encode("utf-8"),
            sample_rate=24_000,
            timestamp_ns=0,
            channels=1,
        )


@dataclass
class _NeverEndingPlayer:
    """Player fake that begins playback and then blocks forever — mimics a turn
    whose brain stream is still running (tool-use loop), so the merged-chunk
    consumer never sees the end-sentinel and ``play_task`` never completes."""

    consumed: list[str] = field(default_factory=list)
    stop_calls: int = 0
    play_started: asyncio.Event = field(default_factory=asyncio.Event)
    _first_seen: bool = False

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for chunk in chunks:
            self.consumed.append(chunk.pcm.decode("utf-8"))
            if not self._first_seen:
                self._first_seen = True
                self.play_started.set()
                await asyncio.sleep(3600)  # held until the turn is cancelled

    def stop(self) -> None:
        self.stop_calls += 1


class _StreamingBrain:
    async def __call__(self, text: str) -> str:  # pragma: no cover - unused
        return "Ich sehe ein Fenster."

    async def generate_stream(self, text: str) -> AsyncIterator[str]:
        yield "Ich sehe ein Fenster. "
        # Then the (tool-use) stream stays open without producing a closing
        # sentinel quickly — the player is what holds the turn open here.
        await asyncio.sleep(3600)


def _make_pipeline(tts, player, brain) -> SpeechPipeline:
    bus = EventBus()
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._brain = brain  # type: ignore[assignment]
    pipeline._latency_tracker = None

    async def _never_barge() -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    return pipeline


@pytest.mark.asyncio
async def test_hangup_during_tts_aborts_the_streaming_turn() -> None:
    """A hangup fired while Jarvis is mid-utterance must stop the player and
    return promptly — never block until the brain stream ends. Pre-fix the
    ``asyncio.wait`` ignores the hangup event, so this turn hangs forever and
    the assertion below times out → RED."""
    tts = _SingleSentenceTTS()
    player = _NeverEndingPlayer()
    brain = _StreamingBrain()
    pipeline = _make_pipeline(tts, player, brain)

    turn = asyncio.create_task(pipeline._brain_streaming("Was siehst du hier?", "de"))

    # Wait until Jarvis is audibly mid-sentence, then the user hangs up.
    assert await asyncio.wait_for(_event(player.play_started), timeout=2.0)
    pipeline._hangup_event.set()

    # The turn must unwind promptly instead of hanging on the never-ending
    # player. If it blocks, wait_for raises TimeoutError → test fails.
    _full_text, barged = await asyncio.wait_for(turn, timeout=2.0)

    assert player.stop_calls >= 1, "hangup must silence the player"
    assert barged is True, "an aborted turn must not suppress session input"


@pytest.mark.asyncio
async def test_hangup_during_nonstreaming_speak_aborts_the_turn() -> None:
    """The non-streaming ``_speak`` path must abort on hangup too — it shares
    the exact ``asyncio.wait({play_task, barge_task})`` structure as the
    streaming path. ``_speak`` is what every fallback phrase
    (``_speak_brain_unavailable`` on a total provider-chain failure,
    ``_speak_brain_timeout``, ``_speak_stt_unavailable``) goes through, so the
    same wedge applies there. Pre-fix this hangs until the 120 s ceiling."""
    tts = _SingleSentenceTTS()
    player = _NeverEndingPlayer()
    pipeline = _make_pipeline(tts, player, brain=_StreamingBrain())

    turn = asyncio.create_task(pipeline._speak("Ein langer Satz.", language="de"))

    assert await asyncio.wait_for(_event(player.play_started), timeout=2.0)
    pipeline._hangup_event.set()

    barged = await asyncio.wait_for(turn, timeout=2.0)

    assert player.stop_calls >= 1, "hangup must silence the player"
    assert barged is True, "an aborted turn must not suppress session input"


async def _event(evt: asyncio.Event) -> bool:
    await evt.wait()
    return True
