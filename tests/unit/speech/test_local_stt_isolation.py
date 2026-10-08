"""Local preview must not occupy the final decoder (issue #464)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.config import STTConfig
from jarvis.core.protocols import Transcript
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.speech.pipeline import (
    _STT_UNAVAILABLE_PHRASE,
    SpeechPipeline,
    TurnTakingState,
)


def _pipeline(monkeypatch, *, wake=None):
    final = FasterWhisperProvider(model="small", device="cpu", language="en")
    monkeypatch.setattr("jarvis.plugins.stt.build_stt_from_config", lambda cfg: final)
    monkeypatch.setattr(
        "jarvis.speech.pipeline._stt_crossover_would_leave_the_machine", lambda: True
    )
    pipe = SpeechPipeline(
        stt=wake,
        tts=SimpleNamespace(name="test-tts"),
        config=SimpleNamespace(stt=STTConfig(provider="faster-whisper")),
        enable_local_whisper=False,
        enable_openwakeword=False,
        enable_whisper_wake=False,
    )
    return pipe, final


@pytest.mark.asyncio
async def test_local_final_without_wake_model_never_starts_preview(monkeypatch) -> None:
    pipe, final = _pipeline(monkeypatch)
    calls = []

    async def transcribe(pcm):
        calls.append(pcm)
        return Transcript(text="Open the calendar", language="en", confidence=0.99)

    monkeypatch.setattr(final, "transcribe_pcm", transcribe)
    pcm = b"\x00\x01" * 1600
    pipe._on_vad_probe(pcm)
    await asyncio.sleep(0)
    assert pipe._probe_stt is None
    assert not pipe._probe_in_flight
    result = await pipe._transcribe_final(pcm)
    assert result is not None and result.text == "Open the calendar"
    assert calls == [pcm]


def test_same_provider_class_keeps_configured_final_model_separate(monkeypatch) -> None:
    wake = FasterWhisperProvider(model="tiny", device="cpu")
    pipe, final = _pipeline(monkeypatch, wake=wake)
    assert pipe._utterance_stt._model_name == "small"
    assert pipe._probe_stt._model_name == "tiny"
    assert pipe._probe_stt._inner is wake
    assert pipe._utterance_stt._inner._inner is final


def test_explicit_shared_native_provider_disables_preview() -> None:
    wake = FasterWhisperProvider(model="tiny", device="cpu")
    pipe = SpeechPipeline(
        stt=wake, tts=SimpleNamespace(name="test-tts"),
        enable_openwakeword=False, enable_whisper_wake=False,
    )
    assert pipe._probe_stt is None


@pytest.mark.parametrize("setting", ["provider", "language"])
def test_live_switch_does_not_share_local_final_with_preview(monkeypatch, setting) -> None:
    pipe, final = _pipeline(monkeypatch)
    pipe._probe_stt = SimpleNamespace(name="old-cloud-preview")
    if setting == "provider":
        assert pipe.set_stt_provider("faster-whisper")
    else:
        assert pipe.set_stt_language("en")
    assert pipe._probe_stt is None
    assert pipe._utterance_stt._model_name == final._model_name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pin,conversation,ui,explicit,expected",
    [
        ("en", "de", "de", None, "en"),
        ("de", "en", "en", None, "de"),
        ("es", "en", "en", None, "es"),
        ("auto", "es", "de", None, "es"),
        ("auto", "", "de", None, "de"),
        ("auto", "", "en", None, "en"),
        ("en", "", "en", "es", "es"),
    ],
)
async def test_stt_apology_uses_output_language(pin, conversation, ui, explicit, expected):
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._config = SimpleNamespace(
        brain=SimpleNamespace(reply_language=pin), ui=SimpleNamespace(language=ui)
    )
    pipe._brain = SimpleNamespace(conversation_language=conversation)
    spoken = []
    states = []

    async def speak(text, language=None, **kwargs):
        spoken.append((text, language))

    async def set_state(state):
        states.append(state)

    pipe._speak = speak
    pipe._set_turn_state = set_state
    await pipe._speak_stt_unavailable(explicit)
    assert spoken == [(_STT_UNAVAILABLE_PHRASE[expected], expected)]
    assert states == [TurnTakingState.JARVIS_SPEAKING]
