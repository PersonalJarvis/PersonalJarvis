"""VOICEVOX adapter: speaker resolution by name, WAV decoding, request shape."""

from __future__ import annotations

import io
import wave

import pytest

from jarvis.plugins.tts import voicevox_tts as vv

_SPEAKERS = [
    {"name": "A", "styles": [{"name": "normal", "id": 3}, {"name": "happy", "id": 4}]},
    {"name": "B", "styles": [{"name": "normal", "id": 13}, {"name": "calm", "id": 14}]},
]


def _wav(frames: bytes, rate: int = 24000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def test_speaker_resolves_by_name_and_style() -> None:
    assert vv.resolve_speaker_id(_SPEAKERS, "B", "calm") == 14


def test_unknown_style_falls_back_to_the_speakers_first_style() -> None:
    assert vv.resolve_speaker_id(_SPEAKERS, "B", "shouting") == 13


def test_unknown_speaker_falls_back_to_the_catalogue_first_voice() -> None:
    assert vv.resolve_speaker_id(_SPEAKERS, "Nobody", "normal") == 3


def test_empty_catalogue_is_an_error() -> None:
    with pytest.raises(RuntimeError):
        vv.resolve_speaker_id([], "A", "normal")


def test_wav_decodes_to_mono_pcm_and_rate() -> None:
    pcm, rate = vv.wav_to_pcm(_wav(b"\x01\x00" * 100, rate=24000))
    assert rate == 24000 and len(pcm) == 200


def test_stereo_wav_is_downmixed() -> None:
    pcm, _ = vv.wav_to_pcm(_wav(b"\x02\x00\x04\x00" * 10, channels=2))
    assert len(pcm) == 20


@pytest.mark.asyncio
async def test_synthesize_queries_then_renders_with_speed(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(vv.engine, "ensure_running", lambda: True)

    def _json(method: str, path: str, **_kw):
        calls.append((method, path))
        if path == "/speakers":
            return _SPEAKERS
        return {"speedScale": 1.0, "volumeScale": 1.0}

    bodies: list[dict] = []

    def _bytes(path: str, *, body, **_kw):
        calls.append(("POST", path))
        if path.startswith("/synthesis"):
            bodies.append(body)
        return _wav(b"\x00\x00" * 50)

    monkeypatch.setattr(vv.engine, "request_json", _json)
    monkeypatch.setattr(vv.engine, "request_bytes", _bytes)
    tts = vv.VoicevoxTTS(speaker="B", style="calm", speed=1.2)
    chunks = [c async for c in tts.synthesize("test")]
    assert len(chunks) == 1 and chunks[0].sample_rate == 24000
    assert ("POST", "/synthesis?speaker=14") in calls
    assert ("POST", "/initialize_speaker?speaker=14&skip_reinit=true") in calls
    assert bodies[0]["speedScale"] == 1.2


@pytest.mark.asyncio
async def test_missing_engine_raises_so_a_fallback_can_speak(monkeypatch) -> None:
    monkeypatch.setattr(vv.engine, "ensure_running", lambda: False)
    with pytest.raises(RuntimeError):
        [c async for c in vv.VoicevoxTTS().synthesize("test")]


def test_factory_builds_voicevox_with_speaker_from_model() -> None:
    from jarvis.core.config import TTSConfig
    from jarvis.plugins.tts import _build_provider

    tts = _build_provider(TTSConfig(provider="voicevox", model="B/calm", speed=1.1), "voicevox")
    assert isinstance(tts, vv.VoicevoxTTS)
    assert (tts._speaker, tts._style, tts._speed) == ("B", "calm", 1.1)


def test_factory_ignores_a_previous_providers_model_id() -> None:
    from jarvis.core.config import TTSConfig
    from jarvis.plugins.tts import _build_provider

    tts = _build_provider(
        TTSConfig(provider="voicevox", model="gemini-3.1-flash-tts-preview"), "voicevox"
    )
    assert tts._speaker == vv.DEFAULT_SPEAKER
