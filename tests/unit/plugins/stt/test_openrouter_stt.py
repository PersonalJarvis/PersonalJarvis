"""Unit tests for the OpenRouter STT plugin.

Covers the wire contract (WAV wrapping, JSON body shape, base64 audio, language
handling), response parsing, the missing-key / HTTP-error fallbacks, and the
transcription-model filter that keeps the STT picker free of chat / embedding /
TTS models.

These use ``httpx.MockTransport`` (a fake transport, not ``unittest.mock``) so
no real network call is made and the exact outgoing request can be asserted.
"""
from __future__ import annotations

import base64
import io
import wave
from types import SimpleNamespace

import httpx
import pytest

from jarvis.plugins.stt.openrouter_stt import (
    DEFAULT_MODEL,
    OpenRouterSTT,
    Transcript,
    filter_stt_models,
    is_transcription_model,
)

_JSON_OK = {"text": "hello world", "usage": {"seconds": 1.2, "cost": 0.00003}}


def _silent_pcm(num_samples: int = 1600) -> bytes:
    """0.1 s of silence at 16 kHz, int16-LE."""
    return b"\x00\x00" * num_samples


def test_provider_identity_is_distinct_from_brain() -> None:
    """The STT provider id must NOT be ``openrouter`` (that is the brain's id in
    the shared catalog / provider-spec namespaces) and must be non-streaming."""
    assert OpenRouterSTT.name == "openrouter-stt"
    assert OpenRouterSTT.supports_streaming is False


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# WAV wrapping + request body shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pcm_is_wrapped_as_valid_wav_and_base64_encoded() -> None:
    """input_audio.data must be base64 of a real WAV container (raw bytes, no
    data-URI), and the PCM payload must round-trip through the WAV header."""
    captured: dict[str, object] = {}
    pcm = _silent_pcm(1600)

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = __import__("json").loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(api_key="test-key", http_client=_mock_client(handler))
    try:
        await stt.transcribe_pcm(pcm, sample_rate=16_000)
    finally:
        await stt.aclose()

    body = captured["json"]
    assert body["model"] == DEFAULT_MODEL
    assert body["input_audio"]["format"] == "wav"
    b64 = body["input_audio"]["data"]
    assert not b64.startswith("data:"), "must be raw base64, not a data-URI"

    wav_bytes = base64.b64decode(b64)
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16_000
        assert w.readframes(w.getnframes()) == pcm

    assert str(captured["url"]).endswith("/audio/transcriptions")


@pytest.mark.asyncio
async def test_language_is_sent_when_set_and_omitted_when_auto() -> None:
    captured: dict[str, dict] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json=_JSON_OK)

    # language="de" → forwarded
    stt = OpenRouterSTT(
        api_key="k", language="de", http_client=_mock_client(handler)
    )
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()
    assert captured["body"].get("language") == "de"

    # language="auto" → treated as unset, no field on the wire
    stt2 = OpenRouterSTT(
        api_key="k", language="auto", http_client=_mock_client(handler)
    )
    try:
        await stt2.transcribe_pcm(_silent_pcm())
    finally:
        await stt2.aclose()
    assert "language" not in captured["body"]


@pytest.mark.asyncio
async def test_temperature_omitted_by_default_and_sent_when_configured() -> None:
    captured: dict[str, dict] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(api_key="k", http_client=_mock_client(handler))
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()
    assert "temperature" not in captured["body"]

    stt2 = OpenRouterSTT(
        api_key="k", temperature=0.0, http_client=_mock_client(handler)
    )
    try:
        await stt2.transcribe_pcm(_silent_pcm())
    finally:
        await stt2.aclose()
    assert captured["body"]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_auth_and_attribution_headers_are_sent() -> None:
    captured: dict[str, httpx.Headers] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(api_key="secret-key", http_client=_mock_client(handler))
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    h = captured["headers"]
    assert h["Authorization"] == "Bearer secret-key"
    assert h["HTTP-Referer"] == "https://github.com/PersonalJarvis"
    assert h["X-Title"] == "Personal Jarvis"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_response_text_is_parsed_into_transcript() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(api_key="k", http_client=_mock_client(handler))
    try:
        result = await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    assert isinstance(result, Transcript)
    assert result.text == "hello world"
    assert result.confidence == 1.0
    assert result.is_partial is False
    assert result.segments == ()


@pytest.mark.asyncio
async def test_empty_pcm_returns_empty_transcript_without_calling_api() -> None:
    called = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(api_key="k", http_client=_mock_client(handler))
    try:
        result = await stt.transcribe_pcm(b"")
    finally:
        await stt.aclose()

    assert result.text == ""
    assert result.confidence == 0.0
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# Missing key + HTTP error fallbacks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no injected key AND no configured credential, a clear English error
    is raised so the STT factory degrades to the local floor (AP-22)."""
    from jarvis.core import config as cfg

    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *a, **k: SimpleNamespace(
            credential="", base_url="https://openrouter.ai/api/v1", via_proxy=False
        ),
    )

    stt = OpenRouterSTT()  # no api_key injected
    with pytest.raises(RuntimeError) as excinfo:
        await stt.transcribe_pcm(_silent_pcm())
    assert "OpenRouter API key" in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,needle",
    [
        (401, "invalid or missing"),
        (402, "out of credit"),
        (429, "rate limit"),
    ],
)
async def test_http_errors_raise_clear_runtime_error(status: int, needle: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    stt = OpenRouterSTT(api_key="k", http_client=_mock_client(handler))
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()
    assert needle in str(excinfo.value)


@pytest.mark.asyncio
async def test_key_is_resolved_lazily_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no key is injected, it is pulled from resolve_provider_endpoint and
    the base_url override (if any) is honored for the transcription URL."""
    from jarvis.core import config as cfg

    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *a, **k: SimpleNamespace(
            credential="resolved-key",
            base_url="https://proxy.example/v1",
            via_proxy=False,
        ),
    )
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers["Authorization"]
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(http_client=_mock_client(handler))
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    assert captured["auth"] == "Bearer resolved-key"
    assert captured["url"] == "https://proxy.example/v1/audio/transcriptions"


@pytest.mark.asyncio
async def test_shared_key_replacement_applies_to_the_next_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config-resolved key is refreshed without rebuilding the STT instance."""
    from jarvis.core import config as cfg

    state: dict[str, object] = {"key": "old-key", "revision": 0}
    seen_auth: list[str] = []
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *a, **k: SimpleNamespace(
            credential=state["key"],
            base_url="https://openrouter.ai/api/v1",
            via_proxy=False,
        ),
    )
    monkeypatch.setattr(cfg, "secret_revision", lambda _key: state["revision"])

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["Authorization"])
        return httpx.Response(200, json=_JSON_OK)

    stt = OpenRouterSTT(http_client=_mock_client(handler))
    try:
        await stt.transcribe_pcm(_silent_pcm())
        state["key"] = "new-key"
        state["revision"] = 1
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    assert seen_auth == ["Bearer old-key", "Bearer new-key"]


# ---------------------------------------------------------------------------
# Transcription-model filter (the STT picker guard)
# ---------------------------------------------------------------------------

def test_filter_keeps_only_transcription_models() -> None:
    """A mixed roster (raw OpenRouter dicts + a parsed ModelInfo-like object)
    must reduce to ONLY the transcription models — no chat, no audio-in chat,
    no generation, no TTS."""
    whisper = {
        "id": "openai/whisper-large-v3",
        "architecture": {
            "modality": "audio->transcription",
            "input_modalities": ["audio"],
            "output_modalities": ["transcription"],
        },
    }
    gpt4o_transcribe = {
        "id": "openai/gpt-4o-transcribe",
        "architecture": {"output_modalities": ["transcription"]},
    }
    chat = {
        "id": "openai/gpt-5",
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    }
    audio_in_chat = {  # accepts audio but is a CHAT model → must be excluded
        "id": "google/gemini-2.5-pro",
        "architecture": {
            "input_modalities": ["text", "audio"],
            "output_modalities": ["text"],
        },
    }
    gpt_audio = {  # outputs text+audio, NOT transcription → excluded
        "id": "openai/gpt-audio",
        "architecture": {
            "input_modalities": ["text", "audio"],
            "output_modalities": ["text", "audio"],
        },
    }
    image_gen = {
        "id": "black-forest-labs/flux",
        "architecture": {"output_modalities": ["image"]},
    }
    no_arch = {"id": "some/legacy-model"}  # missing architecture → excluded
    # A parsed ModelInfo-like object (attribute access, not a dict).
    parsed_transcribe = SimpleNamespace(
        id="google/chirp-3", output_modalities=("transcription",)
    )

    roster = [
        whisper,
        gpt4o_transcribe,
        chat,
        audio_in_chat,
        gpt_audio,
        image_gen,
        no_arch,
        parsed_transcribe,
    ]
    kept = filter_stt_models(roster)
    kept_ids = {getattr(m, "id", None) or m.get("id") for m in kept}
    assert kept_ids == {
        "openai/whisper-large-v3",
        "openai/gpt-4o-transcribe",
        "google/chirp-3",
    }


def test_is_transcription_model_single_predicate() -> None:
    assert is_transcription_model({"architecture": {"output_modalities": ["transcription"]}})
    assert is_transcription_model(SimpleNamespace(output_modalities=["transcription"]))
    assert not is_transcription_model({"architecture": {"output_modalities": ["text"]}})
    assert not is_transcription_model(SimpleNamespace(output_modalities=("text", "audio")))
    assert not is_transcription_model({"id": "x"})  # no modality info
    assert not is_transcription_model({"architecture": {"output_modalities": []}})
