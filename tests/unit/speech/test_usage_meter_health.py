"""Real speech calls feed the provider-health record behind the status dots.

The Voice Output / Voice Input tabs used to learn whether a key worked by
synthesising "Test." and sending half a second of silence to the cloud
recognizer on every app start and reload. They now read what real speech
calls did, and the speech meter — which already wraps every TTS/STT call —
is where those outcomes are reported.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from jarvis.brain import provider_health_ledger as ledger
from jarvis.brain import provider_test as pt
from jarvis.core.protocols import AudioChunk
from jarvis.plugins.tts.fallback_tts import FallbackTTS
from jarvis.speech.stt_fallback import FallbackSTT
from jarvis.speech.usage_meter import SpeechUsage, meter_stt, meter_tts

_PCM = b"\x00" * 3200  # 100 ms of 16 kHz mono int16


class _Sink:
    def record(self, usage: SpeechUsage) -> None:
        pass


class _TTS:
    """A TTS plugin; ``fail`` raises before audio, ``fell_back_to`` mimics a
    plugin that absorbed its own error and let another family speak."""

    supports_streaming = True

    def __init__(
        self,
        name: str = "elevenlabs",
        *,
        fail: Exception | None = None,
        fell_back_to: str | None = None,
        absorbed: str | None = None,
    ) -> None:
        self.name = name
        self._fail = fail
        self._fell_back_to = fell_back_to
        self._absorbed = absorbed
        self.last_voice_provider: str | None = None
        self.last_failure: str | None = None

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        self.last_voice_provider = self.name
        self.last_failure = None
        if self._fail is not None:
            raise self._fail
        if self._fell_back_to:
            self.last_failure = self._absorbed
            self.last_voice_provider = self._fell_back_to
        yield AudioChunk(pcm=_PCM, sample_rate=16_000, timestamp_ns=0)


class _STT:
    supports_streaming = False

    def __init__(self, name: str = "groq-api", *, fail: Exception | None = None) -> None:
        self.name = name
        self._fail = fail

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16_000, **_kw) -> str:
        if self._fail is not None:
            raise self._fail
        return "hello"


async def _speak(tts) -> int:  # noqa: ANN001
    produced = 0
    async for _chunk in tts.synthesize("Hello there."):
        produced += 1
    return produced


def _status(provider: str, modality: str) -> str | None:
    outcome = ledger.get_ledger().get(provider, modality)
    return outcome.status if outcome is not None else None


def test_a_spoken_sentence_records_the_voice_as_answering() -> None:
    asyncio.run(_speak(meter_tts(_TTS(), _Sink())))

    assert _status("elevenlabs", ledger.MODALITY_TTS) == pt.OK


def test_a_failing_synthesis_records_its_class_and_still_raises() -> None:
    tts = meter_tts(_TTS(fail=RuntimeError("HTTP 401 Unauthorized")), _Sink())

    with pytest.raises(RuntimeError):
        asyncio.run(_speak(tts))

    assert _status("elevenlabs", ledger.MODALITY_TTS) == pt.BAD_KEY


def test_an_absorbed_failure_is_charged_to_the_voice_that_was_asked() -> None:
    """The plugin crossed to another family and the user heard a different
    voice; the asked provider's key problem must still reach its dot."""
    tts = _TTS(fell_back_to="gemini-flash-tts", absorbed="_GrokFatalError: HTTP 402")

    asyncio.run(_speak(meter_tts(tts, _Sink())))

    assert _status("elevenlabs", ledger.MODALITY_TTS) == pt.NO_CREDITS
    assert _status("gemini-flash-tts", ledger.MODALITY_TTS) == pt.OK


class _SlowTTS(_TTS):
    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator[AudioChunk]:
        await asyncio.Event().wait()  # the barge-in comes first
        yield AudioChunk(pcm=_PCM, sample_rate=16_000, timestamp_ns=0)


def test_a_sentence_cancelled_before_audio_is_no_evidence() -> None:
    async def _barge_in() -> None:
        task = asyncio.create_task(_speak(meter_tts(_SlowTTS(), _Sink())))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_barge_in())

    assert _status("elevenlabs", ledger.MODALITY_TTS) is None


def test_a_transcription_records_the_recognizer() -> None:
    stt = meter_stt(_STT(), _Sink())

    assert asyncio.run(stt.transcribe_pcm(_PCM, 16_000)) == "hello"
    assert _status("groq-api", ledger.MODALITY_STT) == pt.OK


def test_a_failing_transcription_records_its_class() -> None:
    stt = meter_stt(_STT(fail=RuntimeError("429 Too Many Requests")), _Sink())

    with pytest.raises(RuntimeError):
        asyncio.run(stt.transcribe_pcm(_PCM, 16_000))

    assert _status("groq-api", ledger.MODALITY_STT) == pt.RATE_LIMITED


def test_an_stt_crossover_charges_the_primary_and_credits_the_fallback() -> None:
    primary = _STT("groq-api", fail=RuntimeError("Error code: 429 - rate limit exceeded"))
    chain = FallbackSTT(
        primary,
        ["openai-api"],
        lambda name: _STT(name),
        primary_name="groq-api",
    )
    stt = meter_stt(chain, _Sink())

    assert asyncio.run(stt.transcribe_pcm(_PCM, 16_000)) == "hello"
    assert chain.last_failure is not None
    assert _status("groq-api", ledger.MODALITY_STT) == pt.RATE_LIMITED
    assert _status("openai-api", ledger.MODALITY_STT) == pt.OK


def test_a_tts_fallback_wrapper_exposes_what_the_primary_failed_with() -> None:
    wrapper = FallbackTTS(_TTS("cartesia", fail=RuntimeError("HTTP 401")), _TTS("piper-local"))

    asyncio.run(_speak(meter_tts(wrapper, _Sink())))

    assert wrapper.last_failure is not None
    assert _status("cartesia", ledger.MODALITY_TTS) == pt.BAD_KEY
    assert _status("piper-local", ledger.MODALITY_TTS) == pt.OK
