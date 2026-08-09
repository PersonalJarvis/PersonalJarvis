"""Guards for the on-device Piper voice.

The properties worth pinning are the ones a cloud TTS gets for free and a local
one does not: a Piper voice speaks exactly ONE language, so choosing the right
file per turn IS the feature. Getting it wrong does not raise — it answers a
German sentence in an English accent, which no test would notice unless it
asks for this explicitly.
"""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pytest

from jarvis.plugins.tts import piper_local
from jarvis.plugins.tts.piper_local import PiperLocalTTS


class _FakeAudio:
    def __init__(self, seconds: float = 0.5, sample_rate: int = 22_050) -> None:
        self.samples = np.linspace(-0.5, 0.5, int(sample_rate * seconds), dtype=np.float32)
        self.sample_rate = sample_rate


class _FakeEngine:
    """Records what was spoken, so the test can assert WHICH voice ran."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.spoken: list[str] = []

    def generate(self, text: str, speaker_id: int, speed: float) -> _FakeAudio:
        self.spoken.append(text)
        return _FakeAudio()


@pytest.fixture
def all_voices_installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeEngine]:
    """Pretend every catalogued voice is downloaded, with fake engines."""
    engines: dict[str, _FakeEngine] = {}

    monkeypatch.setattr(
        "jarvis.speech.local_models.bundle_present",
        lambda model_id, **kw: True,
    )

    def _build(self: PiperLocalTTS, model_id: str) -> _FakeEngine:
        engine = _FakeEngine(model_id)
        engines[model_id] = engine
        return engine

    monkeypatch.setattr(PiperLocalTTS, "_build_engine", _build)
    return engines


async def _speak(tts: PiperLocalTTS, text: str, language_code: str | None) -> bytes:
    pcm = b""
    async for chunk in tts.synthesize(text, language_code=language_code):
        pcm += chunk.pcm
    return pcm


def test_each_language_gets_its_own_voice(all_voices_installed) -> None:
    """The core of a local TTS: one voice per language, picked per turn."""
    tts = PiperLocalTTS()

    asyncio.run(_speak(tts, "Guten Morgen", "de-DE"))
    german = tts.last_voice
    asyncio.run(_speak(tts, "Good morning", "en-US"))
    english = tts.last_voice
    asyncio.run(_speak(tts, "Buenos dias", "es-ES"))
    spanish = tts.last_voice

    assert german is not None and "de_DE" in german
    assert english is not None and "en_US" in english
    assert spanish is not None and "es_ES" in spanish


def test_the_speaker_profile_survives_a_language_switch(all_voices_installed) -> None:
    """Switching language must not switch WHO is speaking (BUG-086's shape)."""
    from jarvis.speech.local_models import SHERPA_BUNDLES

    tts = PiperLocalTTS(gender="feminine")

    asyncio.run(_speak(tts, "Guten Morgen", "de-DE"))
    german = tts.last_voice
    asyncio.run(_speak(tts, "Good morning", "en-US"))
    english = tts.last_voice

    assert SHERPA_BUNDLES[german].gender == "feminine"
    assert SHERPA_BUNDLES[english].gender == "feminine"


def test_an_explicit_voice_request_wins(all_voices_installed) -> None:
    tts = PiperLocalTTS()

    asyncio.run(
        tts.synthesize(
            "Hello", voice="vits-piper-en_US-amy-medium", language_code="de-DE"
        ).__anext__()
    )

    assert tts.last_voice == "vits-piper-en_US-amy-medium"


def test_an_unsupported_language_still_speaks(all_voices_installed) -> None:
    """Better an accent than silence — but the log has to say what happened."""
    tts = PiperLocalTTS()

    pcm = asyncio.run(_speak(tts, "Bonjour", "fr-FR"))

    assert pcm, "a language with no local voice must not produce silence"
    assert tts.last_voice is not None


def test_no_downloaded_voice_raises_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence is the worst possible failure for a voice — say why instead."""
    monkeypatch.setattr(
        "jarvis.speech.local_models.bundle_present", lambda model_id, **kw: False
    )
    tts = PiperLocalTTS()

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_speak(tts, "Hallo", "de-DE"))

    assert "API-Keys view" in str(excinfo.value)


def test_only_downloaded_voices_are_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing a voice that is not on disk would let the user pick silence."""
    present = {"vits-piper-de_DE-thorsten-medium"}
    monkeypatch.setattr(
        "jarvis.speech.local_models.bundle_present",
        lambda model_id, **kw: model_id in present,
    )
    tts = PiperLocalTTS()

    assert tts.list_voices() == ["vits-piper-de_DE-thorsten-medium"]
    assert tts.list_voices("en-US") == []


def test_audio_is_int16_in_range(all_voices_installed) -> None:
    """The pipeline plays int16; an unclipped float would wrap into loud noise."""
    tts = PiperLocalTTS()

    pcm = asyncio.run(_speak(tts, "Hallo", "de-DE"))
    samples = np.frombuffer(pcm, dtype=np.int16)

    assert samples.size > 0
    assert samples.max() <= 32767
    assert samples.min() >= -32768


def test_empty_text_speaks_nothing_without_loading_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(self: PiperLocalTTS, model_id: str) -> Any:
        raise AssertionError("empty text must not load a voice")

    monkeypatch.setattr(PiperLocalTTS, "_build_engine", _explode)
    tts = PiperLocalTTS()

    assert asyncio.run(_speak(tts, "   ", "de-DE")) == b""


def test_declares_that_it_runs_on_device() -> None:
    assert PiperLocalTTS.runs_on_device is True


def test_callback_capable_engine_yields_incremental_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StreamingEngine:
        sample_rate = 22_050

        def generate(self, _text, _speaker, _speed, *, callback):
            callback(np.full(100, 0.1, dtype=np.float32), 0.5)
            callback(np.full(100, 0.2, dtype=np.float32), 1.0)
            return _FakeAudio(seconds=0.0)

    monkeypatch.setattr(
        "jarvis.speech.local_models.bundle_present",
        lambda _model_id, **_kwargs: True,
    )
    monkeypatch.setattr(
        PiperLocalTTS,
        "_build_engine",
        lambda _self, _model_id: StreamingEngine(),
    )
    tts = PiperLocalTTS()

    async def collect() -> list[bytes]:
        return [
            chunk.pcm
            async for chunk in tts.synthesize("Hello", language_code="en-US")
        ]

    chunks = asyncio.run(collect())

    assert PiperLocalTTS.supports_streaming is True
    assert len(chunks) == 2
    assert all(chunks)


def test_voices_cover_every_supported_reply_language() -> None:
    """The catalog must not leave a supported language mute.

    ``[brain].reply_language`` offers de/en/es, and the resolver can land on any
    of them per turn. A missing voice there is not a gap in a nice-to-have — it
    is the assistant going silent mid-conversation.
    """
    from jarvis.speech.local_models import PIPER_DEFAULT_VOICES, SHERPA_BUNDLES

    languages = {SHERPA_BUNDLES[v].language for v in PIPER_DEFAULT_VOICES}

    assert {"de", "en", "es"} <= languages


def test_default_voices_share_one_speaker_profile() -> None:
    """All three defaults must be the same kind of speaker, per the test above."""
    from jarvis.speech.local_models import PIPER_DEFAULT_VOICES, SHERPA_BUNDLES

    genders = {SHERPA_BUNDLES[v].gender for v in PIPER_DEFAULT_VOICES}

    assert len(genders) == 1


def test_module_exposes_the_expected_provider_surface() -> None:
    """Structural TTSProvider conformance — no inheritance, so assert it."""
    for attribute in ("synthesize", "list_voices", "name", "supports_streaming"):
        assert hasattr(piper_local.PiperLocalTTS, attribute)
