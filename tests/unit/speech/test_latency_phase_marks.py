"""Wave-0 omni-latency completion: the full per-turn phase ladder + the
``LatencyTurnComplete`` flush event.

Status quo being fixed (latency deep-dive 2026-06-10): ``LatencyTracker``
exists and the JSONL writer (``jarvis/telemetry/latency_log.py``) is attached
at boot, but

  * the streaming hot path only marks ``BRAIN_FIRST_TOKEN`` — the report
    phases ``BRAIN_REQUEST_SENT``, ``BRAIN_LAST_TOKEN``, ``TTS_REQUEST_SENT``,
    ``TTS_FIRST_CHUNK`` and ``TTS_STREAM_DONE`` are enum members that are
    never marked, so every derived duration in the bottleneck report
    (``brain_ttft``, ``tts_ttfb``, …) is ``None``;
  * ``LatencyTurnComplete`` is never published anywhere, so the writer's
    ``state/latency_log.jsonl`` stays empty forever and no live turn can be
    diagnosed after the fact.

These tests pin the missing half. Fakes follow the conventions of
``test_tts_pipeline_overlap.py`` (no audio device, no network, causality not
wall-clock).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import LatencyPhase, LatencyTurnComplete
from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.telemetry.latency import LatencyTracker


@dataclass
class _OneChunkTTS:
    """Minimal streaming TTS fake: one AudioChunk per sentence."""

    name: str = "one-chunk-tts"
    supports_streaming: bool = True
    synth_calls: list[str] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.synth_calls.append(text)
        yield AudioChunk(
            pcm=text.encode("utf-8"),
            sample_rate=24_000,
            timestamp_ns=0,
            channels=1,
        )


@dataclass
class _DrainPlayer:
    """Player fake that consumes chunks immediately (no blocking)."""

    consumed: list[str] = field(default_factory=list)
    stop_calls: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for chunk in chunks:
            self.consumed.append(chunk.pcm.decode("utf-8"))

    def stop(self) -> None:
        self.stop_calls += 1


class _TwoSentenceBrain:
    """Streams a fixed two-sentence reply token-by-token."""

    async def __call__(self, text: str) -> str:  # pragma: no cover - unused
        return "Erstens. Zweitens."

    async def generate_stream(self, text: str) -> AsyncIterator[str]:
        for token in ("Erstens. ", "Zweitens."):
            yield token


class _FixedSTT:
    """STT fake returning one fixed final transcript."""

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        return Transcript(
            text="wie geht es dir", language="de",
            confidence=0.95, is_partial=False,
        )


def _make_streaming_pipeline(bus: EventBus | None = None) -> SpeechPipeline:
    bus = bus or EventBus()
    tts = _OneChunkTTS()
    pipeline = SpeechPipeline(tts=tts, bus=bus, enable_whisper_wake=False)
    pipeline._player = _DrainPlayer()  # type: ignore[assignment]
    pipeline._brain = _TwoSentenceBrain()  # type: ignore[assignment]

    async def _never_barge() -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    return pipeline


async def _settle(pred, deadline_s: float = 2.0) -> bool:
    steps = max(1, int(deadline_s / 0.01))
    for _ in range(steps):
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


@pytest.mark.asyncio
async def test_brain_streaming_marks_full_phase_ladder() -> None:
    """One streamed turn must record every report phase the JSONL bottleneck
    table derives its durations from — not just BRAIN_FIRST_TOKEN."""
    pipeline = _make_streaming_pipeline()
    tracker = LatencyTracker(None, uuid4())  # bus-less: records, emits nothing
    pipeline._latency_tracker = tracker  # type: ignore[assignment]

    await asyncio.wait_for(pipeline._brain_streaming("egal", "de"), timeout=3.0)

    stages = tracker.stages_snapshot()
    for phase in (
        LatencyPhase.BRAIN_REQUEST_SENT,
        LatencyPhase.BRAIN_FIRST_TOKEN,
        LatencyPhase.BRAIN_LAST_TOKEN,
        LatencyPhase.TTS_REQUEST_SENT,
        LatencyPhase.TTS_FIRST_CHUNK,
        LatencyPhase.TTS_STREAM_DONE,
    ):
        assert phase.value in stages, f"phase {phase.value} never marked"
    # Causal ordering of the cumulative offsets.
    assert stages[LatencyPhase.BRAIN_REQUEST_SENT] <= stages[LatencyPhase.BRAIN_FIRST_TOKEN]
    assert stages[LatencyPhase.BRAIN_FIRST_TOKEN] <= stages[LatencyPhase.BRAIN_LAST_TOKEN]
    assert stages[LatencyPhase.TTS_REQUEST_SENT] <= stages[LatencyPhase.TTS_FIRST_CHUNK]
    assert stages[LatencyPhase.TTS_FIRST_CHUNK] <= stages[LatencyPhase.TTS_STREAM_DONE]


@pytest.mark.asyncio
async def test_handle_utterance_publishes_latency_turn_complete() -> None:
    """A completed voice turn must flush exactly one ``LatencyTurnComplete``
    carrying the stage snapshot — this is what feeds state/latency_log.jsonl."""
    bus = EventBus()
    received: list[LatencyTurnComplete] = []

    async def _capture(event: LatencyTurnComplete) -> None:
        received.append(event)

    bus.subscribe(LatencyTurnComplete, _capture)

    pipeline = _make_streaming_pipeline(bus)
    pipeline._utterance_stt = _FixedSTT()  # type: ignore[assignment]
    # Enable the streaming brain path + leave latency at its enabled default.
    pipeline._config = SimpleNamespace(  # type: ignore[assignment]
        performance=SimpleNamespace(
            streaming_tts=True, tts_lookahead_sentences=1,
        ),
        latency=None,
    )

    # Single-turn mode returns False ("session may close") on a COMPLETED
    # turn — completion is proven by the player having spoken both sentences.
    await asyncio.wait_for(
        pipeline._handle_utterance(b"\x00\x00" * 1600, skip_completion=True),
        timeout=5.0,
    )
    assert pipeline._player.consumed == ["Erstens.", "Zweitens."]

    assert await _settle(lambda: len(received) == 1), (
        "no LatencyTurnComplete published after a completed turn — the JSONL "
        "latency log can never receive a row"
    )
    event = received[0]
    stages = dict(event.stages_ms)
    assert LatencyPhase.STT_FINALIZE.value in stages
    assert LatencyPhase.TTS_STREAM_DONE.value in stages
    assert event.anchor_ns > 0
    tracker = pipeline._latency_tracker
    assert tracker is not None and event.trace_id == tracker.trace_id


@pytest.mark.asyncio
async def test_forced_cut_carry_turn_publishes_no_turn_complete() -> None:
    """A forced-cut fragment (user still talking, turn NOT finalized) must not
    flush a row — it would poison the stats with half-turns."""
    bus = EventBus()
    received: list[LatencyTurnComplete] = []

    async def _capture(event: LatencyTurnComplete) -> None:
        received.append(event)

    bus.subscribe(LatencyTurnComplete, _capture)

    pipeline = _make_streaming_pipeline(bus)
    pipeline._last_endpoint_reason = "max_utterance"  # type: ignore[assignment]

    ok = await asyncio.wait_for(
        pipeline._handle_utterance(b"\x00\x00" * 1600), timeout=2.0
    )
    assert ok is True

    await asyncio.sleep(0.05)  # let any (wrong) fire-and-forget task land
    assert received == [], "carry fragment must not emit LatencyTurnComplete"
