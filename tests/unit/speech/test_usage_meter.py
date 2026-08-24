"""The speech meter must count without ever being felt.

It sits on the voice critical path (AP-9), so the properties that matter are
as much about what it does NOT do — buffer, block, raise, or hide a provider's
surface — as about the numbers it reports.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.speech.usage_meter import (
    SpeechUsage,
    meter_stt,
    meter_tts,
    pcm_duration_ms,
)

# 16-bit mono at 16 kHz: 32 bytes = 1 ms.
_RATE = 16_000
_MS = b"\x00" * 32


class _Sink:
    def __init__(self) -> None:
        self.records: list[SpeechUsage] = []

    def record(self, usage: SpeechUsage) -> None:
        self.records.append(usage)


class _AngrySink:
    """A sink that fails every time — the meter must absorb it."""

    def record(self, usage: SpeechUsage) -> None:
        raise RuntimeError("sink is down")


class _FakeTTS:
    name = "cartesia"
    supports_streaming = True

    def __init__(self, chunks: int = 3) -> None:
        self._chunks = chunks
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, voice))
        for _ in range(self._chunks):
            yield AudioChunk(pcm=_MS * 100, sample_rate=_RATE, timestamp_ns=0)

    def list_voices(self, language: str | None = None) -> list[str]:
        return ["ben", "nova"]

    def some_provider_specific_thing(self) -> str:
        return "still here"


class _FakeSTT:
    name = "deepgram-api"
    supports_streaming = False

    def __init__(self) -> None:
        self.seen = 0

    async def transcribe(self, audio: AsyncIterator[AudioChunk]) -> str:
        async for _chunk in audio:
            self.seen += 1
        return "hello"


async def _audio(chunks: int) -> AsyncIterator[AudioChunk]:
    for _ in range(chunks):
        yield AudioChunk(pcm=_MS * 250, sample_rate=_RATE, timestamp_ns=0)


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def test_pcm_duration_is_bytes_over_rate() -> None:
    assert pcm_duration_ms(_MS * 500, _RATE) == pytest.approx(500.0)
    assert pcm_duration_ms(b"", _RATE) == 0.0


@pytest.mark.asyncio
async def test_tts_counts_the_text_it_was_given() -> None:
    sink = _Sink()
    tts = meter_tts(_FakeTTS(), sink)

    async for _chunk in tts.synthesize("hallo welt", voice="ben"):
        pass

    (usage,) = sink.records
    assert usage.stage == "tts"
    assert usage.provider == "cartesia"
    assert usage.chars == len("hallo welt")
    # The audio it produced is reported too, as the by-product it is.
    assert usage.audio_ms == pytest.approx(300, abs=1)


@pytest.mark.asyncio
async def test_tts_reports_once_per_call_not_once_per_chunk() -> None:
    sink = _Sink()
    tts = meter_tts(_FakeTTS(chunks=12), sink)

    async for _chunk in tts.synthesize("x" * 40):
        pass

    assert len(sink.records) == 1


@pytest.mark.asyncio
async def test_stt_counts_the_audio_it_consumed() -> None:
    sink = _Sink()
    stt = meter_stt(_FakeSTT(), sink)

    assert await stt.transcribe(_audio(4)) == "hello"

    (usage,) = sink.records
    assert usage.stage == "stt"
    assert usage.audio_ms == pytest.approx(1000, abs=1)
    # Counting transcript characters would mean reading the user's words
    # in order to bill them.
    assert usage.chars == 0


# ---------------------------------------------------------------------------
# Never felt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_is_lazy() -> None:
    """The first chunk must arrive before the stream ends, or the meter has
    buffered the whole reply and added latency to every spoken word."""
    sink = _Sink()
    tts = meter_tts(_FakeTTS(chunks=5), sink)

    stream = tts.synthesize("hallo")
    first = await stream.__anext__()

    assert first.sample_rate == _RATE
    assert sink.records == []  # nothing reported yet — the call is still open
    await stream.aclose()


@pytest.mark.asyncio
async def test_an_abandoned_stream_still_reports() -> None:
    """A cancelled turn that spent 400 characters spent them."""
    sink = _Sink()
    tts = meter_tts(_FakeTTS(chunks=99), sink)

    stream = tts.synthesize("y" * 400)
    await stream.__anext__()
    await stream.aclose()

    (usage,) = sink.records
    assert usage.chars == 400


@pytest.mark.asyncio
async def test_a_failing_sink_never_breaks_synthesis() -> None:
    tts = meter_tts(_FakeTTS(), _AngrySink())

    chunks = [c async for c in tts.synthesize("hallo")]

    assert len(chunks) == 3


@pytest.mark.asyncio
async def test_no_sink_returns_the_provider_untouched() -> None:
    """Zero cost when nobody is listening — not even a wrapper object."""
    provider = _FakeTTS()
    assert meter_tts(provider, None) is provider
    stt = _FakeSTT()
    assert meter_stt(stt, None) is stt


# ---------------------------------------------------------------------------
# Nothing of the provider may go missing
# ---------------------------------------------------------------------------


def test_the_protocol_surface_survives_wrapping() -> None:
    tts = meter_tts(_FakeTTS(), _Sink())

    assert tts.name == "cartesia"
    assert tts.supports_streaming is True
    assert tts.list_voices() == ["ben", "nova"]
    # Anything the concrete provider adds stays reachable, or a `hasattr`
    # probe somewhere quietly loses a capability.
    assert tts.some_provider_specific_thing() == "still here"


@pytest.mark.asyncio
async def test_arguments_reach_the_provider_unchanged() -> None:
    provider = _FakeTTS()
    tts = meter_tts(provider, _Sink())

    async for _chunk in tts.synthesize("guten tag", voice="nova"):
        pass

    assert provider.calls == [("guten tag", "nova")]


@pytest.mark.asyncio
async def test_the_trace_id_of_the_turn_is_carried() -> None:
    sink = _Sink()
    turn = {"id": "trace-1"}
    tts = meter_tts(_FakeTTS(), sink, trace_id=lambda: turn["id"])

    async for _chunk in tts.synthesize("eins"):
        pass
    turn["id"] = "trace-2"
    async for _chunk in tts.synthesize("zwei"):
        pass

    assert [u.trace_id for u in sink.records] == ["trace-1", "trace-2"]
