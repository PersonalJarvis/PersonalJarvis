"""Pin the producer/consumer overlap contract of the streaming TTS path.

Root cause (TTS-latency deep-dive 2026-05-28): on the streaming voice path
``_brain_streaming`` (jarvis/speech/pipeline.py) the brain-token loop did
``await self._speak(sentence)`` inline. That ``await`` suspends brain-token
consumption — and therefore the construction of sentence N+1 — until sentence
N has FULLY PLAYED. Synthesis and playback were the same task with no queue,
so the ~2 s per-sentence synthesis wall was paid serially between every
sentence boundary, inflating perceived output latency to 2-3x the audio
length.

The fix decouples synthesis from playback with a bounded look-ahead
producer/consumer: the next sentence synthesizes WHILE the current one plays.
These tests pin that behaviour without any real audio device or network — the
fakes record causality, not wall-clock timing, so the assertions are
deterministic.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import SpeechSpoken
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _RecordingTTS:
    """TTS fake whose ``synthesize`` records the START of each sentence's
    synthesis (the generator body runs on first iteration = synth start).

    The sentence text is smuggled through ``AudioChunk.pcm`` so the player
    fake can identify which sentence a chunk belongs to (AudioChunk is frozen
    and carries no free-form tag field).
    """

    name: str = "recording-tts"
    supports_streaming: bool = True
    synth_started: list[str] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.synth_started.append(text)
        yield AudioChunk(
            pcm=text.encode("utf-8"),
            sample_rate=24_000,
            timestamp_ns=0,
            channels=1,
        )


@dataclass
class _GatingPlayer:
    """Player fake that blocks playback of the FIRST chunk it ever sees until
    the test releases it — simulating "sentence 1 is still playing".

    Records the order in which sentences are consumed so ordering can be
    asserted after release.
    """

    consumed: list[str] = field(default_factory=list)
    stop_calls: int = 0
    play_1_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_play_1: asyncio.Event = field(default_factory=asyncio.Event)
    _first_seen: bool = False

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for chunk in chunks:
            self.consumed.append(chunk.pcm.decode("utf-8"))
            if not self._first_seen:
                self._first_seen = True
                self.play_1_started.set()
                await self.release_play_1.wait()

    def stop(self) -> None:
        self.stop_calls += 1


class _ThreeSentenceBrain:
    """Streams a fixed three-sentence reply token-by-token."""

    async def __call__(self, text: str) -> str:  # pragma: no cover - unused
        return "Erstes. Zweites. Drittes."

    async def generate_stream(self, text: str) -> AsyncIterator[str]:
        for token in ("Erstes. ", "Zweites. ", "Drittes."):
            yield token


def _make_pipeline(tts, player, brain) -> SpeechPipeline:
    bus = EventBus()
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = player  # type: ignore[assignment]
    pipeline._brain = brain  # type: ignore[assignment]
    pipeline._latency_tracker = None  # not set by ctor; _brain_streaming reads it
    pipeline._tts_lookahead_sentences = 2  # bounded look-ahead knob

    # No real microphone in unit tests: a barge monitor that never fires.
    async def _never_barge(**_kwargs) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    return pipeline


async def _wait_until(pred, timeout_s: float = 2.0) -> bool:
    steps = max(1, int(timeout_s / 0.01))
    for _ in range(steps):
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


@pytest.mark.asyncio
async def test_next_sentence_synthesizes_while_current_sentence_plays() -> None:
    """While sentence 1's audio is still playing, sentence 2 must already be
    synthesizing. This is the core latency win: synth(N+1) overlaps play(N).

    Pre-fix the brain loop blocks on ``await _speak(sentence_1)`` until
    playback finishes, so sentence 2 never starts synthesizing while sentence
    1 is gated open → this assertion times out → RED.
    """
    tts = _RecordingTTS()
    player = _GatingPlayer()
    brain = _ThreeSentenceBrain()
    pipeline = _make_pipeline(tts, player, brain)

    turn = asyncio.create_task(pipeline._brain_streaming("egal", "de"))
    try:
        # Sentence 1 playback has begun and is now blocked on the gate.
        assert await _wait_until(player.play_1_started.is_set), (
            "sentence 1 playback never started"
        )
        # THE CONTRACT: sentence 2 synthesizes while sentence 1 is still gated.
        assert await _wait_until(lambda: "Zweites." in tts.synth_started), (
            "sentence 2 did not start synthesizing while sentence 1 was still "
            "playing — synthesis and playback are serialized (no overlap)"
        )
    finally:
        player.release_play_1.set()
        try:
            await asyncio.wait_for(turn, timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            turn.cancel()


def test_performance_config_lookahead_default_and_floor() -> None:
    """The look-ahead knob defaults to 1 and is floored at 1 — a 0/negative
    look-ahead would stall the synth/playback queue (no sentence may be
    synthesized ahead of playback)."""
    from jarvis.core.config import PerformanceConfig

    assert PerformanceConfig().tts_lookahead_sentences == 1
    assert PerformanceConfig(tts_lookahead_sentences=3).tts_lookahead_sentences == 3
    assert PerformanceConfig(tts_lookahead_sentences=0).tts_lookahead_sentences == 1
    assert PerformanceConfig(tts_lookahead_sentences=-5).tts_lookahead_sentences == 1


@dataclass
class _BargePlayer:
    """Player fake that holds playback on the first chunk forever (until
    cancelled) so a barge-in can preempt the turn mid-playback."""

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
                await asyncio.sleep(3600)  # held until barge cancels us

    def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_barge_in_stops_playback_and_skips_pending_sentences() -> None:
    """Barge-in during a turn must stop the player, return barged=True, and
    NOT play sentences still queued behind the barged one (no ghost audio,
    "Auflegen ist Hard-Kill")."""
    tts = _RecordingTTS()
    player = _BargePlayer()
    brain = _ThreeSentenceBrain()
    pipeline = _make_pipeline(tts, player, brain)
    confirmed: list[SpeechSpoken] = []

    async def _capture(event: SpeechSpoken) -> None:
        confirmed.append(event)

    pipeline._bus.subscribe(SpeechSpoken, _capture)

    async def _barge_after_play_starts() -> bool:
        await player.play_started.wait()
        return True

    pipeline._barge_monitor = _barge_after_play_starts  # type: ignore[assignment]

    full_text, barged = await asyncio.wait_for(
        pipeline._brain_streaming("egal", "de"), timeout=3.0
    )

    assert barged is True
    assert player.stop_calls >= 1
    # Only sentence 1 ever reached the player; the rest were cancelled.
    assert player.consumed == ["Erstes."]
    assert confirmed == []
    # Full raw text is still returned for post-stream hangup/history logic.
    assert full_text == "Erstes. Zweites. Drittes."


@pytest.mark.asyncio
async def test_sentences_play_in_order_and_full_text_returned() -> None:
    """FIFO ordering must be preserved (single continuous voice) and the full
    raw text returned for post-stream logic (hangup detection, history)."""
    tts = _RecordingTTS()
    player = _GatingPlayer()
    brain = _ThreeSentenceBrain()
    pipeline = _make_pipeline(tts, player, brain)
    confirmed: list[SpeechSpoken] = []

    async def _capture(event: SpeechSpoken) -> None:
        confirmed.append(event)

    pipeline._bus.subscribe(SpeechSpoken, _capture)

    turn = asyncio.create_task(pipeline._brain_streaming("egal", "de"))
    await _wait_until(player.play_1_started.is_set)
    player.release_play_1.set()
    full_text, barged = await asyncio.wait_for(turn, timeout=2.0)
    await asyncio.sleep(0.05)

    assert player.consumed == ["Erstes.", "Zweites.", "Drittes."]
    assert full_text == "Erstes. Zweites. Drittes."
    assert barged is False
    assert [event.text for event in confirmed] == ["Erstes.", "Zweites.", "Drittes."]
    assert {event.spoken_kind for event in confirmed} == {"reply"}
