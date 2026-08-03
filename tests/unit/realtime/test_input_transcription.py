"""Local user-speech transcription for transports that do not supply it.

ChatGPT-Live sends assistant transcripts only, so without these events the
provider is deaf to Jarvis: the bar stays blank, the indicators never move,
and every transcript-driven integration (delegate, wiki, project files,
hang-up phrase) sits idle while the model happily talks.
"""

from __future__ import annotations

import asyncio
import math
import struct
from types import SimpleNamespace

import pytest

from jarvis.realtime.input_transcription import LocalInputTranscriber

RATE = 24_000
CHUNK_SAMPLES = 480  # 20 ms


def _loud(samples: int = CHUNK_SAMPLES) -> bytes:
    return b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * 180 * n / RATE)))
        for n in range(samples)
    )


def _quiet(samples: int = CHUNK_SAMPLES) -> bytes:
    return b"\x00\x00" * samples


class _FakeSTT:
    def __init__(self, text: str = "hallo welt") -> None:
        self.text = text
        self.calls = 0
        self.seconds: list[float] = []

    async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
        self.calls += 1
        total = 0
        async for chunk in audio:
            total += len(chunk.pcm)
        self.seconds.append(total / 2 / RATE)
        return SimpleNamespace(text=self.text)


_EVENT_WAIT_S = 5.0


async def _drain(transcriber: LocalInputTranscriber, expected: int):
    events = []
    for _ in range(expected):
        events.append(
            await asyncio.wait_for(transcriber.next_event(), timeout=_EVENT_WAIT_S)
        )
    return events


@pytest.mark.asyncio
async def test_speech_then_silence_yields_speech_start_and_transcript() -> None:
    stt = _FakeSTT("Ben, durchsuche mein Wiki.")
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    for _ in range(40):  # 800 ms of speech
        transcriber.feed(_loud(), RATE)
    for _ in range(40):  # 800 ms of silence closes the utterance
        transcriber.feed(_quiet(), RATE)

    events = await _drain(transcriber, 2)
    assert events[0].kind == "speech_started"
    assert events[1].kind == "transcript"
    assert events[1].text == "Ben, durchsuche mein Wiki."
    assert events[1].is_final is True
    assert stt.calls == 1
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_blip_of_noise_never_becomes_a_transcript() -> None:
    """A cough must not cost a recognizer call — or worse, become something
    Jarvis believes the user said. The guard measures VOICED audio, because
    the buffer also holds pre-roll and the silence that closed it: judging by
    buffer length, 160 ms of noise looks like a whole sentence."""
    stt = _FakeSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    for _ in range(8):  # 160 ms of noise — real, but far too short to be speech
        transcriber.feed(_loud(), RATE)
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)
    await asyncio.sleep(0.1)

    assert stt.calls == 0
    await transcriber.close()


@pytest.mark.asyncio
async def test_the_utterance_carries_its_own_onset() -> None:
    """Pre-roll matters: without it the recognizer receives a sentence whose
    first syllable was already spent proving that speech had started."""
    stt = _FakeSTT()
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)

    for _ in range(50):  # 1.0 s of speech
        transcriber.feed(_loud(), RATE)
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)
    await _drain(transcriber, 2)

    # Speech + pre-roll + the trailing silence that closed it.
    assert stt.seconds and stt.seconds[0] > 1.0
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_failing_recognizer_never_kills_the_call() -> None:
    class _Broken:
        async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
            async for _ in audio:
                pass
            raise RuntimeError("recognizer exploded")

    transcriber = LocalInputTranscriber(
        sample_rate=RATE, stt_factory=lambda: _Broken()
    )
    for _ in range(40):
        transcriber.feed(_loud(), RATE)
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)

    first = await asyncio.wait_for(transcriber.next_event(), timeout=5.0)
    assert first.kind == "speech_started"
    # The provider receives one explicit failure boundary so it can promote
    # its own energy-gated preview; closing still completes cleanly.
    failed = await asyncio.wait_for(transcriber.next_event(), timeout=5.0)
    assert failed.kind == "transcript_failed"
    await transcriber.close()
    assert await asyncio.wait_for(transcriber.next_event(), timeout=5.0) is None


@pytest.mark.asyncio
async def test_silence_never_vouches_for_a_server_transcript() -> None:
    """The energy gate the provider asks before trusting a server-side user
    transcript. ChatGPT-Live invented "[exhale]" and "a_lee pixelated image"
    while the user sat silent, and each was recorded as something they said."""
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    assert transcriber.speech_recently() is False  # nothing fed yet
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)
    assert transcriber.speech_recently() is False
    await transcriber.close()


@pytest.mark.asyncio
async def test_real_speech_vouches_for_a_server_transcript() -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    for _ in range(20):  # mid-utterance
        transcriber.feed(_loud(), RATE)
    assert transcriber.speech_recently() is True

    for _ in range(40):  # the silence that closes it
        transcriber.feed(_quiet(), RATE)
    # The far end transcribes with its own latency, so a genuine transcript
    # arrives shortly AFTER the audio stopped.
    assert transcriber.speech_recently() is True
    # ...but the vouching expires, so a transcript arriving much later in the
    # silence is no longer covered by it.
    await asyncio.sleep(0.05)
    assert transcriber.speech_recently(grace_ms=10) is False
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_cough_does_not_vouch_for_a_server_transcript() -> None:
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _FakeSTT())

    for _ in range(8):  # 160 ms — too short to be an utterance
        transcriber.feed(_loud(), RATE)
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)
    assert transcriber.speech_recently() is False
    await transcriber.close()


@pytest.mark.asyncio
async def test_a_failed_recognizer_announces_itself() -> None:
    """Silence would strand the turn: the provider needs to know it must fall
    back to the far end's transcript (AP-30 - never fail without saying so)."""

    class _Broken:
        async def transcribe(self, audio):  # noqa: ANN001 - protocol shape
            async for _ in audio:
                pass
            raise RuntimeError("recognizer exploded")

    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: _Broken())
    for _ in range(40):
        transcriber.feed(_loud(), RATE)
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)

    events = await _drain(transcriber, 2)
    assert events[0].kind == "speech_started"
    assert events[1].kind == "transcript_failed"
    await transcriber.close()


@pytest.mark.asyncio
async def test_an_empty_result_also_announces_itself() -> None:
    stt = _FakeSTT("   ")
    transcriber = LocalInputTranscriber(sample_rate=RATE, stt_factory=lambda: stt)
    for _ in range(40):
        transcriber.feed(_loud(), RATE)
    for _ in range(40):
        transcriber.feed(_quiet(), RATE)

    events = await _drain(transcriber, 2)
    assert events[1].kind == "transcript_failed"
    await transcriber.close()
