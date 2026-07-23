"""Unit tests for the OpenAI Whisper STT plugin.

Covers the wire contract (WAV multipart upload, Bearer auth, model + language
fields), response parsing, and the missing-key / HTTP-error fallbacks that let
the STT factory degrade to the local floor (AP-22).

These use ``httpx.MockTransport`` (a fake transport, not ``unittest.mock``) so no
real network call is made and the exact outgoing request can be asserted.
"""
from __future__ import annotations

import asyncio
import io
import wave
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
import pytest

from jarvis.plugins.stt.openai_api import (
    DEFAULT_MODEL,
    OpenAIWhisperAPI,
    Transcript,
)


@dataclass
class _FakeChunk:
    """Minimal AudioChunk duck-type for STT input."""

    pcm: bytes
    sample_rate: int = 16_000
    channels: int = 1
    timestamp_ns: int = 0


async def _async_iter(items):
    for item in items:
        yield item


def _fake_pcm(seconds: float = 0.25, sample_rate: int = 16_000) -> bytes:
    return b"\x00\x00" * int(sample_rate * seconds)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_provider_identity_is_distinct_from_brain() -> None:
    """The STT id must be ``openai-api`` (the brain owns ``openai``) and be
    non-streaming."""
    assert OpenAIWhisperAPI.name == "openai-api"
    assert OpenAIWhisperAPI.supports_streaming is False


def test_transcribe_uploads_wav_and_parses_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        body = {
            "text": "hello world",
            "language": "en",
            "segments": [
                {"start": 0.0, "end": 0.7, "text": "hello world", "avg_logprob": -0.2},
            ],
        }
        return httpx.Response(200, json=body)

    provider = OpenAIWhisperAPI(api_key="test-key-xxx", http_client=_mock_client(handler))
    chunks = [_FakeChunk(pcm=_fake_pcm(0.5))]
    result = asyncio.run(provider.transcribe(_async_iter(chunks)))

    assert isinstance(result, Transcript)
    assert result.text == "hello world"
    assert result.language == "en"
    assert 0.0 < result.confidence <= 1.0
    assert result.is_partial is False
    assert len(result.segments) == 1

    assert captured["auth"] == "Bearer test-key-xxx"
    assert str(captured["url"]).endswith("/audio/transcriptions")
    assert "api.openai.com" in str(captured["url"])
    # The multipart body must contain a real WAV and the whisper-1 model field.
    assert b"RIFF" in captured["body"]
    assert b"WAVE" in captured["body"]
    assert DEFAULT_MODEL.encode() in captured["body"]


def test_empty_audio_returns_empty_transcript_without_http() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="should not be called")

    provider = OpenAIWhisperAPI(api_key="k", http_client=_mock_client(handler))
    result = asyncio.run(provider.transcribe(_async_iter([])))
    assert result.text == ""
    assert result.confidence == 0.0
    assert calls["n"] == 0


def test_language_is_sent_when_set_and_omitted_when_auto() -> None:
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "ok", "language": "de", "segments": []})

    prov = OpenAIWhisperAPI(api_key="k", language="de", http_client=_mock_client(handler))
    asyncio.run(prov.transcribe_pcm(_fake_pcm(0.2)))
    assert b'name="language"' in captured["body"]  # language field present

    prov2 = OpenAIWhisperAPI(api_key="k", language="auto", http_client=_mock_client(handler))
    asyncio.run(prov2.transcribe_pcm(_fake_pcm(0.2)))
    # "auto" is treated as unset — no language multipart field named "language".
    assert b'name="language"' not in captured["body"]


def test_stream_transcribe_yields_single_final() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "Test", "language": "en", "segments": []})

    provider = OpenAIWhisperAPI(api_key="k", http_client=_mock_client(handler))
    chunks = [_FakeChunk(pcm=_fake_pcm(0.3))]

    async def drive():
        out = []
        async for t in provider.stream_transcribe(_async_iter(chunks)):
            out.append(t)
        return out

    results = asyncio.run(drive())
    assert len(results) == 1
    assert results[0].text == "Test"


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no injected key AND no configured credential, a clear English error
    is raised so the STT factory degrades to the local floor (AP-22)."""
    from jarvis.core import config as cfg

    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *a, **k: SimpleNamespace(
            credential="", base_url="https://api.openai.com/v1", via_proxy=False
        ),
    )
    monkeypatch.setattr(cfg, "secret_revision", lambda _key: 0)

    provider = OpenAIWhisperAPI()  # no api_key injected
    with pytest.raises(RuntimeError, match="OpenAI API key"):
        asyncio.run(provider.transcribe_pcm(_fake_pcm(0.2)))


@pytest.mark.parametrize(
    "status,needle",
    [
        (401, "invalid or missing"),
        (402, "out of credit"),
        (429, "rate limit"),
    ],
)
def test_http_errors_raise_clear_runtime_error(status: int, needle: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    provider = OpenAIWhisperAPI(api_key="k", http_client=_mock_client(handler))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(provider.transcribe_pcm(_fake_pcm(0.2)))
    assert needle in str(excinfo.value)


def test_wraps_pcm_into_valid_wav_container() -> None:
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "ok", "language": "de", "segments": []})

    provider = OpenAIWhisperAPI(api_key="k", http_client=_mock_client(handler))
    asyncio.run(provider.transcribe_pcm(_fake_pcm(0.4)))

    body = captured["body"]
    riff_idx = body.find(b"RIFF")
    assert riff_idx >= 0, "no RIFF header in upload body"
    with wave.open(io.BytesIO(body[riff_idx:]), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16_000
        assert wav.getnframes() > 0
