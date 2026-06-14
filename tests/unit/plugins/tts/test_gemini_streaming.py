"""GeminiFlashTTS true-streaming synthesis (time-to-first-audio collapse).

Measured root cause (latency deep-dive 2026-06-10): the provider synthesizes
via blocking ``generate_content`` and yields ONE AudioChunk per sentence, so
the first audible byte waits for the FULL generation of sentence 1 —
2.4–8.1 s in live probes. The same model/voice/config over
``client.aio.models.generate_content_stream`` delivered the first audio chunk
in 575–1330 ms (114 incremental chunks). The ``[tts].streaming`` config key
already promised exactly this ("Echtes Streaming, chunkweise PCM_24000") but
was wired to nothing.

Contract pinned here:
  * ``streaming=True``  → chunks are yielded incrementally as the stream
    delivers them (same single generation → voice consistency preserved).
  * stream fails BEFORE any audio → fall back to the existing blocking path
    (which owns quota cooldown / sibling bridge / SAPI5 semantics).
  * 429 during the stream arms the same quota cooldown the blocking path uses.
  * stream fails AFTER audio was yielded → keep the partial audio, never
    re-synthesize (no duplicated opening words — mirrors FallbackTTS policy).
  * ``streaming=False`` (default) → byte-identical legacy behaviour.
  * the factory forwards ``[tts].streaming`` to the provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

# Verbatim 429 captured live 2026-05-14 (same fixture as the sibling-bridge tests).
_GOOGLE_429_MESSAGE = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
    "'You exceeded your current quota', 'status': 'RESOURCE_EXHAUSTED', "
    "'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
    "'retryDelay': '17270s'}]}}"
)


def _audio_chunk(data: bytes) -> Any:
    """Build a google-genai response chunk shape carrying one PCM piece."""
    part = SimpleNamespace(inline_data=SimpleNamespace(data=data))
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


@dataclass
class _FakeAioModels:
    """Fake for ``client.aio.models`` — scripted streaming behaviour."""

    pieces: list[bytes] = field(default_factory=list)
    raise_on_call: Exception | None = None
    raise_after_pieces: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def generate_content_stream(
        self, *, model: str, contents: str, config: Any
    ) -> Any:
        self.calls.append(model)
        if self.raise_on_call is not None:
            raise self.raise_on_call

        async def _gen():
            for piece in self.pieces:
                yield _audio_chunk(piece)
            if self.raise_after_pieces is not None:
                raise self.raise_after_pieces

        return _gen()


def _new_streaming_tts(aio: _FakeAioModels, **overrides) -> GeminiFlashTTS:
    tts = GeminiFlashTTS(streaming=True, **overrides)
    tts._client = SimpleNamespace(aio=SimpleNamespace(models=aio))
    return tts


async def _collect_pcm(tts: GeminiFlashTTS, text: str) -> list[bytes]:
    return [chunk.pcm async for chunk in tts.synthesize(text)]


@pytest.mark.asyncio
async def test_streaming_yields_chunks_incrementally() -> None:
    """Three streamed PCM pieces → three AudioChunks (not one buffered blob)."""
    aio = _FakeAioModels(pieces=[b"A1", b"A2", b"A3"])
    tts = _new_streaming_tts(aio)

    chunks = [c async for c in tts.synthesize("Ein kurzer Satz.")]

    assert [c.pcm for c in chunks] == [b"A1", b"A2", b"A3"]
    assert all(c.sample_rate == 24_000 for c in chunks)
    assert aio.calls == ["gemini-3.1-flash-tts-preview"]


@pytest.mark.asyncio
async def test_streaming_falls_back_to_blocking_when_stream_fails_before_audio() -> None:
    """Stream dies before the first byte → the blocking path (quota/sibling/
    SAPI5 owner) must produce the sentence instead. No silent drop (AD-OE6)."""
    aio = _FakeAioModels(raise_on_call=RuntimeError("transport exploded"))
    tts = _new_streaming_tts(aio)
    blocking_calls: list[str] = []

    async def fake_one(text: str, voice: str) -> bytes:
        blocking_calls.append(text)
        return b"BLOCKING_PCM"

    tts._synthesize_one = fake_one  # type: ignore[assignment]

    pcm = await _collect_pcm(tts, "Ein kurzer Satz.")

    assert pcm == [b"BLOCKING_PCM"]
    assert blocking_calls == ["Ein kurzer Satz."]


@pytest.mark.asyncio
async def test_streaming_429_arms_quota_cooldown() -> None:
    """A RESOURCE_EXHAUSTED during the stream must arm the same cooldown the
    blocking path uses, so the fallback call goes straight to the sibling."""
    aio = _FakeAioModels(raise_on_call=RuntimeError(_GOOGLE_429_MESSAGE))
    tts = _new_streaming_tts(aio)

    async def fake_one(text: str, voice: str) -> bytes:
        return b"SIBLING_PCM"

    tts._synthesize_one = fake_one  # type: ignore[assignment]

    pcm = await _collect_pcm(tts, "Ein kurzer Satz.")

    assert pcm == [b"SIBLING_PCM"]
    assert tts._quota_blocked_until > 0.0, "429 must arm the quota cooldown"


@pytest.mark.asyncio
async def test_streaming_midstream_failure_keeps_partial_audio() -> None:
    """Audio already reached the speaker → never re-synthesize (duplicate
    opening words); keep the partial audio and end the sentence."""
    aio = _FakeAioModels(
        pieces=[b"A1"], raise_after_pieces=RuntimeError("mid-stream drop")
    )
    tts = _new_streaming_tts(aio)
    blocking_calls: list[str] = []

    async def fake_one(text: str, voice: str) -> bytes:
        blocking_calls.append(text)
        return b"MUST_NOT_APPEAR"

    tts._synthesize_one = fake_one  # type: ignore[assignment]

    pcm = await _collect_pcm(tts, "Ein kurzer Satz.")

    assert pcm == [b"A1"]
    assert blocking_calls == [], "mid-stream failure must not re-synthesize"


@pytest.mark.asyncio
async def test_streaming_disabled_default_keeps_legacy_single_chunk() -> None:
    """Default ctor (no streaming kwarg) → legacy blocking path, one chunk."""
    tts = GeminiFlashTTS()
    tts._client = object()  # sentinel: _ensure_client returns early

    async def fake_one(text: str, voice: str) -> bytes:
        return b"FULL_SENTENCE"

    tts._synthesize_one = fake_one  # type: ignore[assignment]

    pcm = await _collect_pcm(tts, "Ein kurzer Satz.")

    assert pcm == [b"FULL_SENTENCE"]


@pytest.mark.asyncio
async def test_streaming_closes_provider_stream_on_early_exit() -> None:
    """Barge-in closes the ``synthesize`` generator mid-stream — the genai
    stream underneath must be ``aclose()``d deterministically, not left to GC
    (otherwise every barge-in with streaming=True leaks a connection).
    ``async for`` does NOT close its iterator (PEP 525), so both layers need
    explicit closure."""
    closed: list[bool] = []

    class _Stream:
        def __init__(self, pieces: list[bytes]) -> None:
            self._it = iter(pieces)

        def __aiter__(self) -> _Stream:
            return self

        async def __anext__(self) -> Any:
            try:
                return _audio_chunk(next(self._it))
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            closed.append(True)

    class _Models:
        async def generate_content_stream(
            self, *, model: str, contents: str, config: Any
        ) -> _Stream:
            return _Stream([b"A1", b"A2", b"A3"])

    tts = GeminiFlashTTS(streaming=True)
    tts._client = SimpleNamespace(aio=SimpleNamespace(models=_Models()))

    gen = tts.synthesize("Ein kurzer Satz.")
    first = await gen.__anext__()
    assert first.pcm == b"A1"
    await gen.aclose()  # simulates the barge-in cancellation path

    assert closed == [True], (
        "generate_content_stream was not aclose()d on early generator exit"
    )


def test_factory_wires_tts_streaming_flag() -> None:
    """``[tts].streaming = true`` must reach the provider (the key existed in
    jarvis.toml but was never forwarded — dead config)."""
    from jarvis.plugins.tts import _build_provider

    cfg = SimpleNamespace(
        provider="gemini-flash-tts", model="", voice_de="Charon",
        language_code="de-DE", style_prompt="", allow_sapi5_fallback=False,
        chunk_by_sentence=False, seed=7, temperature=0.7,
        use_vertex=False, vertex_project=None, vertex_location="us-central1",
        service_account_path=None, streaming=True,
    )
    tts = _build_provider(cfg, "gemini-flash-tts")
    assert tts._streaming is True

    cfg.streaming = False
    tts_off = _build_provider(cfg, "gemini-flash-tts")
    assert tts_off._streaming is False
