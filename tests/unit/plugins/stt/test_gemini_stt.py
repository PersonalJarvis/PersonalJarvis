"""Unit tests for the Gemini STT plugin.

Gemini has no HTTP wire we can mock at the transport layer — it transcribes via
the ``google-genai`` client's ``generate_content``. So these inject a hand-rolled
FAKE client (NOT ``unittest.mock``): a tiny object exposing
``models.generate_content(...)`` that returns a fake response with ``.text``. No
network, no SDK install, no ``jarvis.*`` monkeypatching needed for the happy path.
"""
from __future__ import annotations

import asyncio
import base64
import io
import wave
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from jarvis.plugins.stt.gemini_api import GeminiSTT, Transcript

# ---------------------------------------------------------------------------
# Fake google-genai client (records the outgoing call, returns a canned reply)
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    text: str


class _FakeModels:
    def __init__(self, response: _FakeResponse, captured: dict) -> None:
        self._response = response
        self._captured = captured

    def generate_content(self, *, model, contents, config):  # noqa: ANN001
        self._captured["model"] = model
        self._captured["contents"] = contents
        self._captured["config"] = config
        return self._response


class _FakeClient:
    def __init__(self, text: str, captured: dict) -> None:
        self.models = _FakeModels(_FakeResponse(text), captured)


@dataclass
class _FakeChunk:
    pcm: bytes
    sample_rate: int = 16_000
    channels: int = 1
    timestamp_ns: int = 0


async def _async_iter(items):
    for item in items:
        yield item


def _fake_pcm(seconds: float = 0.25, sample_rate: int = 16_000) -> bytes:
    return b"\x00\x00" * int(sample_rate * seconds)


def test_provider_identity_is_distinct_from_brain() -> None:
    """The STT id must be ``gemini-api`` (the brain owns ``gemini``)."""
    assert GeminiSTT.name == "gemini-api"
    assert GeminiSTT.supports_streaming is False


def test_transcribe_returns_text_and_sends_inline_wav_audio() -> None:
    captured: dict = {}
    pcm = _fake_pcm(0.4)
    stt = GeminiSTT(client=_FakeClient("hello there", captured))

    result = asyncio.run(stt.transcribe(_async_iter([_FakeChunk(pcm=pcm)])))

    assert isinstance(result, Transcript)
    assert result.text == "hello there"
    assert result.confidence == 1.0
    assert result.is_partial is False
    assert result.segments == ()

    # The audio rode as a base64 WAV inline_data part, and an instruction text
    # part was included.
    parts = captured["contents"][0]["parts"]
    inline = parts[0]["inline_data"]
    assert inline["mime_type"] == "audio/wav"
    wav_bytes = base64.b64decode(inline["data"])
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.readframes(wav.getnframes()) == pcm
    assert any("text" in p for p in parts[1:])


def test_empty_pcm_returns_empty_transcript_without_calling_client() -> None:
    captured: dict = {}
    stt = GeminiSTT(client=_FakeClient("should not be used", captured))
    result = asyncio.run(stt.transcribe_pcm(b""))
    assert result.text == ""
    assert result.confidence == 0.0
    assert captured == {}  # generate_content was never invoked


def test_surrounding_quotes_are_stripped() -> None:
    captured: dict = {}
    stt = GeminiSTT(client=_FakeClient('"turn on the lights"', captured))
    result = asyncio.run(stt.transcribe_pcm(_fake_pcm(0.2)))
    assert result.text == "turn on the lights"


def test_language_override_is_threaded_into_the_instruction() -> None:
    captured: dict = {}
    stt = GeminiSTT(client=_FakeClient("hello", captured))
    asyncio.run(stt.transcribe_pcm(_fake_pcm(0.2), language="de"))
    instruction = captured["contents"][0]["parts"][1]["text"]
    assert "de" in instruction


def test_stream_transcribe_yields_single_final() -> None:
    captured: dict = {}
    stt = GeminiSTT(client=_FakeClient("Test", captured))

    async def drive():
        out = []
        async for t in stt.stream_transcribe(_async_iter([_FakeChunk(pcm=_fake_pcm(0.2))])):
            out.append(t)
        return out

    results = asyncio.run(drive())
    assert len(results) == 1
    assert results[0].text == "Test"


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No injected client / key AND no configured Gemini credential → a clear
    English error so the STT factory degrades to the local floor (AP-22)."""
    from jarvis.core import config as cfg

    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *a, **k: SimpleNamespace(credential="", base_url=None, via_proxy=False),
    )

    stt = GeminiSTT()  # no client, no api_key
    with pytest.raises(RuntimeError, match="Gemini API key"):
        asyncio.run(stt.transcribe_pcm(_fake_pcm(0.2)))
