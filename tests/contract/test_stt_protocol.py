"""Contract tests for STT providers (Phase 1 + cloud).

Scope: structural compatibility with ``jarvis.core.protocols.STTProvider`` and
end-to-end functional coverage for the Groq cloud plugin (mocked HTTP, no
network).

The legacy ``faster-whisper`` provider depends on a CUDA-capable host and is
deliberately exercised only via class-level shape checks. The Groq plugin is
testable on any host because httpx is mocked at the transport layer.

This test file is also the regression guard for the rule "plugin modules must
not import from ``jarvis.*``" (see CLAUDE.md plugin section).
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path

import httpx
import pytest

from jarvis.core.protocols import STTProvider

ENTRY_POINT_GROUP = "jarvis.stt"
# faster-whisper entry-point removed 2026-05-18 along with the dependency
# (cloud-first doctrine — see CLAUDE.md PHILOSOPHY section).
EXPECTED_PROVIDERS = {"groq-api"}


# ----------------------------------------------------------------------
# Entry-point discovery
# ----------------------------------------------------------------------

def _load_entry_points() -> dict[str, type]:
    """Discover STT entry-points; tolerate dead stubs (ghost registrations).

    ``pyproject.toml`` currently lists ``deepgram``, ``deepgram-flux``,
    ``deepgram-nova3`` and ``openai-api`` without corresponding modules. Those
    must not break discovery of the real providers.
    """
    eps = importlib_metadata.entry_points()
    selected = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") else [
        ep for ep in eps.get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    ]
    loaded: dict[str, type] = {}
    for ep in selected:
        try:
            loaded[ep.name] = ep.load()
        except (ModuleNotFoundError, ImportError):
            continue
    return loaded


@pytest.fixture(scope="module")
def stt_classes() -> dict[str, type]:
    return _load_entry_points()


def test_required_stt_providers_registered(stt_classes):
    missing = EXPECTED_PROVIDERS - set(stt_classes)
    assert not missing, f"Missing STT entry-points: {missing}"


@pytest.mark.parametrize("provider_name", sorted(EXPECTED_PROVIDERS))
def test_provider_has_required_attributes(stt_classes, provider_name):
    cls = stt_classes[provider_name]
    # Class-level attributes (instance-level fallback OK for ``name``)
    assert hasattr(cls, "name")
    assert hasattr(cls, "supports_streaming")
    # Required coroutines
    assert hasattr(cls, "transcribe")
    assert inspect.iscoroutinefunction(cls.transcribe)
    assert hasattr(cls, "stream_transcribe")
    assert inspect.isasyncgenfunction(cls.stream_transcribe)


@pytest.mark.parametrize("provider_name", sorted(EXPECTED_PROVIDERS))
def test_provider_class_name_matches_entry_point(stt_classes, provider_name):
    cls = stt_classes[provider_name]
    assert cls.name == provider_name, (
        f"{cls.__name__}.name = {cls.name!r}, entry-point = {provider_name!r}"
    )


# ----------------------------------------------------------------------
# "No jarvis.* imports" rule for the Groq plugin source
# ----------------------------------------------------------------------

def test_groq_plugin_has_no_jarvis_imports():
    """The Groq plugin source must not import from ``jarvis.*`` (CLAUDE.md)."""
    import jarvis.plugins.stt.groq_api as groq_mod

    source = Path(groq_mod.__file__).read_text(encoding="utf-8")
    offending = [
        ln
        for ln in source.splitlines()
        if ln.lstrip().startswith(("from jarvis", "import jarvis"))
    ]
    assert not offending, (
        "jarvis.* imports leaked into the Groq plugin: " + "; ".join(offending)
    )


# ----------------------------------------------------------------------
# Functional tests — Groq provider with mocked HTTP transport
# ----------------------------------------------------------------------

@dataclass
class FakeChunk:
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


def _make_mock_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _build_groq(monkeypatch, handler, **kwargs):
    from jarvis.plugins.stt.groq_api import GroqWhisperAPI

    monkeypatch.setenv("GROQ_API_KEY", "test-key-xxx")
    client = _make_mock_client(handler)
    return GroqWhisperAPI(http_client=client, **kwargs), client


def test_groq_class_implements_stt_protocol_runtime_check():
    from jarvis.plugins.stt.groq_api import GroqWhisperAPI

    instance = GroqWhisperAPI(api_key="dummy")
    assert isinstance(instance, STTProvider)


def test_groq_transcribe_uploads_wav_and_parses_response(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        body = {
            "text": "Hallo Welt",
            "language": "de",
            "segments": [
                {"start": 0.0, "end": 0.7, "text": "Hallo Welt", "avg_logprob": -0.2},
            ],
        }
        return httpx.Response(200, json=body)

    provider, _ = _build_groq(monkeypatch, handler)

    chunks = [FakeChunk(pcm=_fake_pcm(0.5))]
    result = asyncio.get_event_loop().run_until_complete(
        provider.transcribe(_async_iter(chunks))
    )

    assert result.text == "Hallo Welt"
    assert result.language == "de"
    assert 0.0 < result.confidence <= 1.0
    assert result.is_partial is False
    assert len(result.segments) == 1
    assert result.segments[0]["text"] == "Hallo Welt"

    assert captured["auth"] == "Bearer test-key-xxx"
    assert "audio/transcriptions" in captured["url"]
    # The multipart body must contain a real WAV (RIFF header).
    assert b"RIFF" in captured["body"]
    assert b"WAVE" in captured["body"]
    # The model field must be present in the multipart payload.
    assert b"whisper-large-v3" in captured["body"]


def test_groq_empty_audio_returns_empty_transcript_without_http(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="should not be called")

    provider, _ = _build_groq(monkeypatch, handler)
    result = asyncio.get_event_loop().run_until_complete(
        provider.transcribe(_async_iter([]))
    )
    assert result.text == ""
    assert result.confidence == 0.0
    assert calls["n"] == 0


def test_groq_stream_transcribe_yields_single_final(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "Test", "language": "en", "segments": []})

    provider, _ = _build_groq(monkeypatch, handler)
    chunks = [FakeChunk(pcm=_fake_pcm(0.3))]

    async def drive():
        out = []
        async for t in provider.stream_transcribe(_async_iter(chunks)):
            out.append(t)
        return out

    results = asyncio.get_event_loop().run_until_complete(drive())
    assert len(results) == 1
    assert results[0].text == "Test"
    assert results[0].language == "en"


def test_groq_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # Also short-circuit the keyring fallback so the test does not depend on
    # the host's Credential Manager state.
    import jarvis.plugins.stt.groq_api as groq_mod

    monkeypatch.setattr(groq_mod, "_read_keyring_secret", lambda *a, **k: "")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json={"text": "", "segments": []})

    from jarvis.plugins.stt.groq_api import GroqWhisperAPI

    provider = GroqWhisperAPI(http_client=_make_mock_client(handler))
    chunks = [FakeChunk(pcm=_fake_pcm(0.2))]
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        asyncio.get_event_loop().run_until_complete(
            provider.transcribe(_async_iter(chunks))
        )


def test_groq_wraps_pcm_into_valid_wav_container(monkeypatch):
    """Verify the multipart body contains a structurally valid WAV file."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "ok", "language": "de", "segments": []})

    provider, _ = _build_groq(monkeypatch, handler)
    chunks = [FakeChunk(pcm=_fake_pcm(0.4))]
    asyncio.get_event_loop().run_until_complete(provider.transcribe(_async_iter(chunks)))

    # Extract the WAV blob from the multipart body and re-parse it with `wave`.
    body = captured["body"]
    riff_idx = body.find(b"RIFF")
    assert riff_idx >= 0, "no RIFF header in upload body"
    # Read a generous suffix into a wave-parser; trailing multipart boundary
    # bytes are tolerated by wave.open since it only reads the declared sizes.
    blob = body[riff_idx:]
    with wave.open(io.BytesIO(blob), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16_000
        assert wav.getnframes() > 0


# ----------------------------------------------------------------------
# Sanity: response with no segments still produces a usable Transcript
# ----------------------------------------------------------------------

def test_groq_segmentless_response_confidence_defaults(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"text": "hi", "language": "en"}))

    provider, _ = _build_groq(monkeypatch, handler)
    chunks = [FakeChunk(pcm=_fake_pcm(0.2))]
    result = asyncio.get_event_loop().run_until_complete(
        provider.transcribe(_async_iter(chunks))
    )
    assert result.text == "hi"
    assert result.language == "en"
    assert result.confidence == 1.0
    assert result.segments == ()
