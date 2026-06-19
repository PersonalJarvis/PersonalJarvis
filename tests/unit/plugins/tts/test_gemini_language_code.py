"""Unit tests for GeminiFlashTTS language-code pinning.

Background (2026-06-19 voice forensic): a German answer "Morgen ist Samstag,
der 20. Juni, Boss." was spoken correctly until the last two words — "Juni,
Boss" came out in English pronunciation with a foreign intonation. Root cause:
Gemini Flash TTS is a generative multilingual model that decides pronunciation
PER WORD from the text content when it is given no language pin. A German
sentence ending on the English loanword/address "Boss" makes the model
code-switch the tail into English.

The speech pipeline already resolves the turn language (``resolve_output_language``)
and calls ``synthesize(text, language_code="de-DE")`` — but the plugin threw the
value away (``_ = language_code or self._language_code``) and never wrote it into
the ``SpeechConfig``, even though the SDK exposes ``SpeechConfig.language_code``.

These tests lock the language pin into the request the model actually sees, so
a per-turn ``language_code`` reaches the API and the model stops auto-switching
mid-sentence. A call WITHOUT a language_code stays unpinned (auto-detect), so
no path that deliberately omits it (e.g. the ack preamble) regresses.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

# --- _build_config carries the per-call language pin ------------------------

def test_build_config_sets_language_code() -> None:
    """A language_code passed to _build_config must reach SpeechConfig — this
    is the field the model reads to fix the pronunciation language."""
    tts = GeminiFlashTTS()
    cfg = tts._build_config("Charon", language_code="de-DE")
    assert cfg.speech_config.language_code == "de-DE"


def test_build_config_without_language_code_leaves_it_unset() -> None:
    """No pin -> auto-detection (the historical behaviour). A path that omits
    the language_code (ack preamble) must NOT get a stray config pin."""
    tts = GeminiFlashTTS()
    cfg = tts._build_config("Charon")
    assert cfg.speech_config.language_code is None


# --- synthesize threads the call language_code all the way to the request ---


class _FakeInlineData:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakePart:
    def __init__(self, data: bytes) -> None:
        self.inline_data = _FakeInlineData(data)


class _FakeContent:
    def __init__(self, data: bytes) -> None:
        self.parts = [_FakePart(data)]


class _FakeCandidate:
    def __init__(self, data: bytes) -> None:
        self.content = _FakeContent(data)


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.candidates = [_FakeCandidate(data)]


class _CapturingModels:
    """Captures the GenerateContentConfig the blocking call hands to the SDK."""

    def __init__(self) -> None:
        self.configs: list[object] = []

    def generate_content(self, model, contents, config):  # noqa: ANN001
        self.configs.append(config)
        return _FakeResponse(b"PCM")


class _FakeClient:
    def __init__(self) -> None:
        self.models = _CapturingModels()


@pytest.mark.asyncio
async def test_synthesize_propagates_language_code_to_request() -> None:
    """The per-turn language_code the pipeline passes must land in the
    SpeechConfig of the actual generate_content request — the end-to-end proof
    that the German answer is synthesized with a German pronunciation pin."""
    tts = GeminiFlashTTS(chunk_by_sentence=False)
    tts._ensure_client = lambda: None  # type: ignore[assignment]
    tts._client = _FakeClient()

    _ = [c async for c in tts.synthesize("Der 20. Juni, Boss.", language_code="de-DE")]

    configs = tts._client.models.configs
    assert len(configs) == 1
    assert configs[0].speech_config.language_code == "de-DE"


@pytest.mark.asyncio
async def test_synthesize_without_language_code_stays_unpinned() -> None:
    """No language_code from the caller -> no pin in the request (auto-detect),
    so paths that deliberately omit it do not regress."""
    tts = GeminiFlashTTS(chunk_by_sentence=False)
    tts._ensure_client = lambda: None  # type: ignore[assignment]
    tts._client = _FakeClient()

    _ = [c async for c in tts.synthesize("Hallo zusammen.")]

    configs = tts._client.models.configs
    assert len(configs) == 1
    assert configs[0].speech_config.language_code is None


# --- streaming fast-path: the live path (jarvis.toml streaming = true) ------


class _FakeStreamChunk:
    def __init__(self, data: bytes) -> None:
        self.candidates = [_FakeCandidate(data)]


class _FakeAioStream:
    """Minimal async-iterable over streamed chunks."""

    def __init__(self, chunks: list[_FakeStreamChunk]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> _FakeAioStream:
        return self

    async def __anext__(self) -> _FakeStreamChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _CapturingAioModels:
    """Captures the GenerateContentConfig handed to the streaming call."""

    def __init__(self) -> None:
        self.configs: list[object] = []

    async def generate_content_stream(self, model, contents, config):  # noqa: ANN001
        self.configs.append(config)
        return _FakeAioStream([_FakeStreamChunk(b"PCM")])


class _FakeStreamingClient:
    def __init__(self) -> None:
        self.aio = type("_Aio", (), {"models": _CapturingAioModels()})()


@pytest.mark.asyncio
async def test_streaming_synthesize_propagates_language_code() -> None:
    """The live config runs streaming=true, so the streaming fast-path — not
    the blocking fallback — is what actually synthesizes the answer. The
    per-turn language_code must reach the streamed request's SpeechConfig too,
    or the real-world fix would not apply on the path that runs in production."""
    tts = GeminiFlashTTS(streaming=True, chunk_by_sentence=False)
    tts._ensure_client = lambda: None  # type: ignore[assignment]
    tts._client = _FakeStreamingClient()

    _ = [c async for c in tts.synthesize("Der 20. Juni, Boss.", language_code="de-DE")]

    configs = tts._client.aio.models.configs
    assert len(configs) == 1
    assert configs[0].speech_config.language_code == "de-DE"
