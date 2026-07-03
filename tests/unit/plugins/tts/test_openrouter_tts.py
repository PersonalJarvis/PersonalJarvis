"""Unit tests for the OpenRouter TTS plugin + its speech-model filter.

Uses lightweight fake httpx stream objects (no unittest.mock, per repo policy);
no live network. Covers: request body shape, streaming AudioChunk yielding at
24 kHz mono, per-model voice validation + list_voices, the missing-key fatal
error path (FallbackTTS contract), and the speech-model filter (mixed catalog ->
only speech models survive).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.tts import openrouter_tts as ortts
from jarvis.plugins.tts.openrouter_speech_models import (
    DEFAULT_MODEL,
    coerce_speech_model,
    filter_tts_models,
    is_speech_model,
    voices_for_model,
)
from jarvis.plugins.tts.openrouter_tts import (
    OPENROUTER_TTS_SAMPLE_RATE,
    OpenRouterTTS,
    OpenRouterTTSError,
)

# --------------------------------------------------------------------------- #
# Fake httpx streaming client                                                 #
# --------------------------------------------------------------------------- #


class _FakeStreamResponse:
    def __init__(self, status_code: int, chunks: list[bytes] | None = None,
                 error_body: bytes = b"") -> None:
        self.status_code = status_code
        self._chunks = chunks or []
        self._error_body = error_body

    async def aread(self) -> bytes:
        return self._error_body

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for c in self._chunks:
            yield c


class _FakeStreamCM:
    def __init__(self, resp: _FakeStreamResponse) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._resp

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeClient:
    """Records the last stream() call and returns a canned response."""

    def __init__(self, resp: _FakeStreamResponse) -> None:
        self._resp = resp
        self.captured: dict[str, Any] = {}

    def stream(self, method: str, url: str, json: dict[str, Any] | None = None) -> _FakeStreamCM:
        self.captured = {"method": method, "url": url, "payload": json}
        return _FakeStreamCM(self._resp)


def _tts_with_client(resp: _FakeStreamResponse, **kw: Any) -> tuple[OpenRouterTTS, _FakeClient]:
    tts = OpenRouterTTS(**kw)
    client = _FakeClient(resp)
    tts._client = client  # inject; skips key resolution
    return tts, client


# --------------------------------------------------------------------------- #
# synthesize — request shape + streaming                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_request_body_shape() -> None:
    """Body carries model/input/voice/response_format=pcm and NO language field."""
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x01" * 10])
    tts, client = _tts_with_client(resp, model=DEFAULT_MODEL, voice_en="Kore")

    _ = [c async for c in tts.synthesize("Hello there.", language_code="en-US")]

    payload = client.captured["payload"]
    assert payload["model"] == DEFAULT_MODEL
    assert payload["input"] == "Hello there."
    assert payload["voice"] == "Kore"
    assert payload["response_format"] == "pcm"
    assert "language" not in payload  # OpenAI speech API takes no language field
    assert "speed" not in payload  # default speed is omitted
    assert client.captured["url"].endswith("/audio/speech")


@pytest.mark.asyncio
async def test_speed_included_when_non_default() -> None:
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x01" * 4])
    tts, client = _tts_with_client(resp, speed=1.25)
    _ = [c async for c in tts.synthesize("Hi.")]
    assert client.captured["payload"]["speed"] == 1.25


@pytest.mark.asyncio
async def test_streaming_yields_pcm_24k_mono() -> None:
    """Multiple byte chunks -> multiple AudioChunks at 24 kHz mono s16le."""
    chunks = [b"\x01\x02" * 100, b"\x03\x04" * 100, b"\x05\x06" * 100]
    resp = _FakeStreamResponse(200, chunks=chunks)
    tts, _ = _tts_with_client(resp)

    out = [c async for c in tts.synthesize("Stream this.")]

    assert len(out) == 3
    assert all(isinstance(c, AudioChunk) for c in out)
    assert all(c.sample_rate == OPENROUTER_TTS_SAMPLE_RATE for c in out)
    assert all(c.channels == 1 for c in out)
    assert b"".join(c.pcm for c in out) == b"".join(chunks)


@pytest.mark.asyncio
async def test_odd_byte_alignment_is_preserved() -> None:
    """A 16-bit sample split across two network chunks is not dropped/misaligned."""
    # 3 bytes then 1 byte => 4 bytes total (2 samples), no loss, no split sample.
    resp = _FakeStreamResponse(200, chunks=[b"\xaa\xbb\xcc", b"\xdd"])
    tts, _ = _tts_with_client(resp)
    out = [c async for c in tts.synthesize("x")]
    joined = b"".join(c.pcm for c in out)
    assert joined == b"\xaa\xbb\xcc\xdd"
    assert len(joined) % 2 == 0


@pytest.mark.asyncio
async def test_empty_text_yields_nothing() -> None:
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x00"])
    tts, _ = _tts_with_client(resp)
    out = [c async for c in tts.synthesize("   ")]
    assert out == []


# --------------------------------------------------------------------------- #
# Fatal error paths (FallbackTTS contract: raise before first chunk)           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
async def test_http_error_raises_before_any_chunk(status: int) -> None:
    resp = _FakeStreamResponse(status, error_body=b'{"error":"nope"}')
    tts, _ = _tts_with_client(resp)
    with pytest.raises(OpenRouterTTSError):
        _ = [c async for c in tts.synthesize("Test.")]


@pytest.mark.asyncio
async def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credential -> OpenRouterTTSError before any audio (FallbackTTS crosses)."""

    class _Endpoint:
        credential = ""
        base_url = ortts.BASE_URL

    monkeypatch.setattr(
        ortts.cfg, "resolve_provider_endpoint", lambda *a, **k: _Endpoint()
    )
    tts = OpenRouterTTS()  # no injected client -> _ensure_client runs
    with pytest.raises(OpenRouterTTSError, match="OpenRouter API key"):
        _ = [c async for c in tts.synthesize("Test.")]


# --------------------------------------------------------------------------- #
# Voice resolution + list_voices                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_foreign_voice_corrected_to_model_default() -> None:
    """A voice not valid for the model is replaced by the model default (no 400)."""
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x00"])
    # Grok model does not accept a Gemini "Charon" voice.
    tts, client = _tts_with_client(
        resp, model="x-ai/grok-voice-tts-1.0", voice_en="Charon"
    )
    _ = [c async for c in tts.synthesize("Hi.", language_code="en")]
    assert client.captured["payload"]["voice"] == "leo"  # grok model default


@pytest.mark.asyncio
async def test_valid_voice_passes_through() -> None:
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x00"])
    tts, client = _tts_with_client(resp, model=DEFAULT_MODEL, voice_de="Charon")
    # de-DE language_code selects the voice_de slot (voice resolves from the
    # language code, not the text), so the input text stays English.
    _ = [c async for c in tts.synthesize("Hello.", language_code="de-DE")]
    assert client.captured["payload"]["voice"] == "Charon"


@pytest.mark.asyncio
async def test_explicit_voice_override_wins() -> None:
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x00"])
    tts, client = _tts_with_client(resp, model=DEFAULT_MODEL, voice_en="Kore")
    _ = [c async for c in tts.synthesize("Hi.", voice="Puck", language_code="en")]
    assert client.captured["payload"]["voice"] == "Puck"


def test_list_voices_returns_model_voices() -> None:
    tts = OpenRouterTTS(model="x-ai/grok-voice-tts-1.0")
    voices = tts.list_voices()
    assert set(voices) == {"eve", "ara", "rex", "sal", "leo"}


def test_list_voices_narrows_by_language_for_prefixed_model() -> None:
    """Kokoro voices are language-prefixed; a German filter narrows, never empties."""
    tts = OpenRouterTTS(model="hexgrad/kokoro-82m")
    en = tts.list_voices(language="en-US")
    assert en and all(v[0] in ("a", "b") for v in en)  # a*/b* = English families
    # A language the model has no voices for falls back to the full valid list.
    de = tts.list_voices(language="de")
    assert de  # never empty


# --------------------------------------------------------------------------- #
# Speech-model filter (the TTS model picker predicate)                        #
# --------------------------------------------------------------------------- #


def _model(mid: str, out_mods: list[str], voices: list[str] | None = None) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": mid,
        "architecture": {"output_modalities": out_mods, "modality": f"text->{out_mods[0]}"},
    }
    if voices is not None:
        obj["supported_voices"] = voices
    return obj


def test_is_speech_model_predicate() -> None:
    assert is_speech_model(_model("google/gemini-3.1-flash-tts-preview", ["speech"]))
    assert not is_speech_model(_model("openai/gpt-5.5", ["text"]))
    assert not is_speech_model(_model("google/gemini-3-pro", ["text", "image"]))
    assert not is_speech_model(_model("openai/gpt-audio", ["text", "audio"]))  # audio-chat, not TTS
    assert not is_speech_model(_model("google/lyria-3-pro", ["text", "audio"]))  # music
    assert not is_speech_model({"id": "broken"})  # no architecture -> False


def test_is_speech_model_via_modality_string_fallback() -> None:
    obj = {"id": "x", "architecture": {"modality": "text->speech"}}
    assert is_speech_model(obj)


def test_filter_tts_models_keeps_only_speech() -> None:
    catalog = [
        _model("openai/gpt-5.5", ["text"]),
        _model("google/gemini-3.1-flash-tts-preview", ["speech"]),
        _model("x-ai/grok-voice-tts-1.0", ["speech"]),
        _model("openai/gpt-audio", ["text", "audio"]),
        _model("some/embedding-model", ["text"]),
        _model("google/lyria-3-pro", ["text", "audio"]),
    ]
    kept = filter_tts_models(catalog)
    ids = {m["id"] for m in kept}
    assert ids == {
        "google/gemini-3.1-flash-tts-preview",
        "x-ai/grok-voice-tts-1.0",
    }


def test_voices_for_model_reads_live_supported_voices() -> None:
    obj = _model("x-ai/grok-voice-tts-1.0", ["speech"], voices=["eve", "leo"])
    assert voices_for_model(obj) == ["eve", "leo"]


def test_voices_for_model_falls_back_to_curated_by_id() -> None:
    obj = {"id": "x-ai/grok-voice-tts-1.0"}  # no live supported_voices
    assert set(voices_for_model(obj)) == {"eve", "ara", "rex", "sal", "leo"}


# --------------------------------------------------------------------------- #
# Model coercion — a foreign / empty model must NOT reach the API verbatim     #
# (the [tts] block shares one global `model` across all TTS providers)         #
# --------------------------------------------------------------------------- #


def test_coerce_speech_model_resolves_foreign_and_empty_to_default() -> None:
    # Foreign single-token ids left over from another TTS provider -> default.
    assert coerce_speech_model("sonic-2") == DEFAULT_MODEL  # Cartesia
    assert coerce_speech_model("whisper-large-v3") == DEFAULT_MODEL  # Groq/STT
    assert coerce_speech_model("") == DEFAULT_MODEL
    assert coerce_speech_model(None) == DEFAULT_MODEL
    # Known OpenRouter speech models pass through unchanged.
    assert coerce_speech_model("x-ai/grok-voice-tts-1.0") == "x-ai/grok-voice-tts-1.0"
    # Unknown but OpenRouter-shaped (vendor/model) is trusted (a new TTS model).
    assert coerce_speech_model("newvendor/brand-new-tts") == "newvendor/brand-new-tts"


@pytest.mark.asyncio
async def test_foreign_model_in_config_falls_back_to_default_not_400() -> None:
    """Regression: switching TTS providers leaves a foreign `sonic-2` + `leo` in
    the shared [tts] block. The provider must send the default model + a valid
    voice, never the foreign `sonic-2` that 400s ("Model sonic-2 does not exist").
    """
    resp = _FakeStreamResponse(200, chunks=[b"\x00\x00"])
    tts, client = _tts_with_client(resp, model="sonic-2", voice_de="leo", voice_en="leo")

    _ = [c async for c in tts.synthesize("Hallo.", language_code="de-DE")]

    payload = client.captured["payload"]
    assert payload["model"] == DEFAULT_MODEL  # not "sonic-2"
    # "leo" is a Grok voice, invalid for the Gemini default -> its model default.
    assert payload["voice"] == "Charon"
