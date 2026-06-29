"""SpeechPipeline gates OpenWakeWord hits behind a strict "hey + jarv"
transcript check.

Background: ``hey_jarvis_v0.1`` ONNX also fires on bare "Jarvis". Threshold
edits are forbidden by ``test_wake_threshold`` (BUG-009). The pipeline must
treat an OWW hit as a *candidate* only and require the prefix-verifier to
confirm before emitting the wake event.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace

from jarvis.core.config import STTConfig, TriggerConfig
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator:
        if False:  # pragma: no cover
            yield


@dataclass
class _FakeTranscript:
    text: str
    language: str = "de"
    confidence: float = 1.0


class _FakeSTT:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> _FakeTranscript:
        self.calls += 1
        return _FakeTranscript(text=self.text)


PCM_2S_16K = b"\x00\x00" * 16_000 * 2


def _bare_pipeline(
    *,
    require_hey_prefix: bool,
    utterance_stt: object | None,
) -> SpeechPipeline:
    """Build a SpeechPipeline shell without running __init__ — the gate logic
    only needs three attributes set, so we avoid the heavy provider wiring.
    """
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._require_hey_prefix = require_hey_prefix
    pipe._utterance_stt = utterance_stt
    return pipe


async def test_gate_passes_when_flag_disabled() -> None:
    """require_hey_prefix=False restores raw OWW behaviour (legacy escape)."""
    stt = _FakeSTT("Jarvis")  # would reject, but must not even be called
    pipe = _bare_pipeline(require_hey_prefix=False, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 0


async def test_gate_passes_when_no_utterance_stt_configured() -> None:
    """No STT to verify with → degrade to legacy (warn, accept). Better than a
    silently bricked wake on a misconfigured rig."""
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=None)
    assert await pipe._verify_oww_hit(PCM_2S_16K) is True


async def test_gate_passes_on_hey_jarvis_transcript() -> None:
    stt = _FakeSTT("Hey Jarvis, was läuft heute")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 1


async def test_gate_rejects_bare_jarvis_transcript() -> None:
    stt = _FakeSTT("Jarvis")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is False
    assert stt.calls == 1


async def test_gate_rejects_empty_pcm_without_stt_call() -> None:
    stt = _FakeSTT("Hey Jarvis")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(b"") is False
    assert stt.calls == 0


async def test_gate_degrades_open_on_stt_hallucination() -> None:
    """REGRESSION (live 2026-06-28): a strong OWW hit whose verify STT returns a
    KNOWN silence/noise hallucination ("Untertitelung des ZDF", "Vielen Dank.")
    must DEGRADE OPEN. Such a transcript means the STT failed to transcribe the
    real "Hey Jarvis" — it is NOT evidence the user stayed silent. Dropping these
    made the wake "stop working" for ~half of valid utterances (24 ok / 31 fail
    in one afternoon). Same intent as the transient-STT-failure degrade-open."""
    for phrase in (
        "Untertitelung des ZDF, 2020",
        "Vielen Dank.",
        "Thanks for watching",
    ):
        stt = _FakeSTT(phrase)
        pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)
        assert await pipe._verify_oww_hit(PCM_2S_16K) is True, phrase
        assert stt.calls == 1


async def test_gate_still_rejects_genuine_other_speech() -> None:
    """The hallucination degrade-open must NOT accept arbitrary non-wake speech:
    genuine other words (no "hey + jarv", not a known hallucination) are still
    suppressed, so the bare-"Jarvis" false-positive guard (BUG-009) stays."""
    stt = _FakeSTT("wie spät ist es")
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is False
    assert stt.calls == 1


class _RaisingSTT:
    """transcribe_pcm always raises — models a persistent Groq 503/timeout."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> _FakeTranscript:
        self.calls += 1
        raise self._exc


async def test_gate_degrades_open_on_empty_transcript_with_real_audio(monkeypatch) -> None:
    """REGRESSION ("Hey Jarvis sometimes stops working entirely"): a strong OWW
    hit captured real audio (non-empty buffer) but the verify STT came back with
    NO transcript — a momentary outage / rate-limit / silence-mis-transcription.
    That is not evidence the user stayed silent, so it must DEGRADE OPEN (accept)
    rather than brick the wake while the STT recovers (AP-22). This is distinct
    from empty PCM (nothing captured yet), which still rejects."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _FakeSTT("")  # STT returned nothing for genuine captured audio
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls == 1


async def test_gate_degrades_open_when_stt_persistently_fails(monkeypatch) -> None:
    """A persistently failing verify STT (every attempt raises) on a strong OWW
    hit must degrade open, not suppress — the wake survives a provider outage and
    recovers in-app with no restart (AP-22). The empty-PCM reject is unchanged."""
    import jarvis.speech.wake_verifier as wv

    monkeypatch.setattr(wv, "_WAKE_VERIFY_BACKOFF_S", 0.0, raising=False)
    stt = _RaisingSTT(RuntimeError("groq 503"))
    pipe = _bare_pipeline(require_hey_prefix=True, utterance_stt=stt)

    assert await pipe._verify_oww_hit(PCM_2S_16K) is True
    assert stt.calls >= 1


# ---------------------------------------------------------------------------
# Real construction: require_hey_prefix flows from config into the pipeline.
# ---------------------------------------------------------------------------


def _cfg_groq(*, require_hey_prefix: bool) -> SimpleNamespace:
    trigger = TriggerConfig(require_hey_prefix=require_hey_prefix)
    return SimpleNamespace(stt=STTConfig(provider="groq-api"), trigger=trigger)


def test_pipeline_default_requires_hey_prefix() -> None:
    pipe = SpeechPipeline(
        tts=_FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=_cfg_groq(require_hey_prefix=True),
    )
    assert pipe._require_hey_prefix is True


def test_pipeline_honours_disabled_require_hey_prefix() -> None:
    pipe = SpeechPipeline(
        tts=_FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=_cfg_groq(require_hey_prefix=False),
    )
    assert pipe._require_hey_prefix is False
