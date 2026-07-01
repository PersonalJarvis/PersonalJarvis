"""Unit tests for CartesiaTTS — mocked httpx, no live API calls."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jarvis.plugins.tts.cartesia_tts import (
    CARTESIA_TTS_SAMPLE_RATE,
    CartesiaTTS,
)


# ----- Fixtures ----- #


class _Resp:
    def __init__(
        self,
        status_code: int,
        content: bytes = b"",
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


@pytest.fixture
def patched_secret():
    with patch(
        "jarvis.plugins.tts.cartesia_tts.cfg.get_secret",
        return_value="sk_car_test_key",
    ):
        yield


@pytest.fixture
def tts(patched_secret) -> CartesiaTTS:
    return CartesiaTTS(
        voice_id="11111111-2222-3333-4444-555555555555",
        chunk_by_sentence=True,
        allow_sapi5_fallback=False,
    )


# ----- Tests ----- #


@pytest.mark.asyncio
async def test_synthesize_yields_pcm_24k_mono(tts: CartesiaTTS) -> None:
    """Happy path: one sentence -> one AudioChunk at 24kHz mono."""
    fake_pcm = b"\x00\x01" * 12_000
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(200, content=fake_pcm))
    tts._client = mock_client

    chunks = [c async for c in tts.synthesize("Hallo Welt.")]

    assert len(chunks) == 1
    assert chunks[0].pcm == fake_pcm
    assert chunks[0].sample_rate == CARTESIA_TTS_SAMPLE_RATE
    assert chunks[0].channels == 1


@pytest.mark.asyncio
async def test_multiple_sentences_yield_in_order(tts: CartesiaTTS) -> None:
    """Three sentences -> three chunks in source order despite parallel synth."""
    bodies = [b"AAA", b"BBB", b"CCC"]
    call_count = {"i": 0}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        idx = call_count["i"]
        call_count["i"] += 1
        await asyncio.sleep(0)
        return _Resp(200, content=bodies[idx])

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    chunks = [c async for c in tts.synthesize("Erste. Zweite. Dritte.")]
    payloads = [c.pcm for c in chunks]
    assert payloads == [b"AAA", b"BBB", b"CCC"]


@pytest.mark.asyncio
async def test_401_triggers_cooldown_and_falls_back(tts: CartesiaTTS) -> None:
    """401 -> fatal -> cooldown set, remainder yielded from fallback."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(401, text="unauthorized"))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(
            pcm=b"FALLBACK", sample_rate=24_000, timestamp_ns=0, channels=1
        )

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Test.")]

    assert any(c.pcm == b"FALLBACK" for c in chunks)
    assert tts._quota_blocked_until > 0


@pytest.mark.asyncio
async def test_429_triggers_cooldown_and_falls_back(tts: CartesiaTTS) -> None:
    """429 -> same cooldown branch as 401."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(429, text="rate limited"))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(
            pcm=b"FALLBACK", sample_rate=24_000, timestamp_ns=0, channels=1
        )

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Test.")]

    assert chunks and chunks[0].pcm == b"FALLBACK"
    assert tts._quota_blocked_until > 0


@pytest.mark.asyncio
async def test_empty_body_routes_to_cross_provider_fallback(
    tts: CartesiaTTS,
) -> None:
    """200 OK + empty body -> soft-fail, cross-provider fallback kicks in."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(200, content=b""))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(pcm=b"FB", sample_rate=24_000, timestamp_ns=0, channels=1)

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Hallo.")]

    assert chunks and chunks[0].pcm == b"FB"


def test_missing_voice_id_raises_at_construction(patched_secret) -> None:
    with pytest.raises(ValueError, match="voice_id"):
        CartesiaTTS(voice_id="")


def test_list_voices_returns_generic_when_no_lang_voices(tts: CartesiaTTS) -> None:
    # tts fixture has only voice_id set (no per-lang) -> defaults kick in.
    voices = tts.list_voices()
    # Fixture's generic voice_id is the EN-default fallback bucket.
    assert "11111111-2222-3333-4444-555555555555" in voices


@pytest.mark.asyncio
async def test_auto_language_is_omitted_from_payload(tts: CartesiaTTS) -> None:
    """Live regression: Cartesia rejects 'auto' with HTTP 400. The plugin
    must omit the field entirely so Cartesia auto-detects from the transcript.
    """
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, content=b"\x00\x01" * 100)

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    # Default language is "auto" (from fixture's parent default).
    [_ async for _ in tts.synthesize("Hallo.")]

    assert "language" not in captured, (
        f"'language' must be absent when set to 'auto', got: {captured.get('language')!r}"
    )


@pytest.mark.asyncio
async def test_explicit_language_de_is_sent(patched_secret) -> None:
    tts_de = CartesiaTTS(
        voice_id="11111111-2222-3333-4444-555555555555",
        language="de-DE",
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, content=b"\x00\x01" * 100)

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts_de._client = mock_client

    [_ async for _ in tts_de.synthesize("Hallo.")]

    assert captured.get("language") == "de", (
        f"explicit de-DE must be normalised to 'de', got: {captured.get('language')!r}"
    )


@pytest.mark.asyncio
async def test_language_code_de_picks_de_voice(patched_secret) -> None:
    """When the caller passes language_code='de-DE', the DE voice UUID is used."""
    tts = CartesiaTTS(
        voice_id="GENERIC-FALLBACK",
        voice_id_de="DE-VOICE-UUID",
        voice_id_en="EN-VOICE-UUID",
        voice_id_es="ES-VOICE-UUID",
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, content=b"\x00\x01" * 100)

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize("Hallo.", language_code="de-DE")]

    assert captured["voice"]["id"] == "DE-VOICE-UUID"


@pytest.mark.asyncio
async def test_text_heuristic_picks_de_when_caller_says_auto(patched_secret) -> None:
    """When the caller passes nothing or 'auto', the text-detect heuristic
    routes German-looking text to the DE voice."""
    tts = CartesiaTTS(
        voice_id="GENERIC",
        voice_id_de="DE-VOICE-UUID",
        voice_id_en="EN-VOICE-UUID",
        voice_id_es="ES-VOICE-UUID",
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, content=b"\x00\x01" * 100)

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize("Ich bin Jarvis und freue mich für Sie.")]  # i18n-allow: German text synthesized to prove the German voice/locale is picked

    assert captured["voice"]["id"] == "DE-VOICE-UUID"


@pytest.mark.asyncio
async def test_text_heuristic_picks_es_for_spanish_text(patched_secret) -> None:
    tts = CartesiaTTS(
        voice_id="GENERIC",
        voice_id_de="DE-VOICE-UUID",
        voice_id_en="EN-VOICE-UUID",
        voice_id_es="ES-VOICE-UUID",
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, content=b"\x00\x01" * 100)

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize("Hola, ¿cómo está? Gracias por su atención.")]

    assert captured["voice"]["id"] == "ES-VOICE-UUID"


@pytest.mark.asyncio
async def test_voice_override_wins_over_language_detection(patched_secret) -> None:
    tts = CartesiaTTS(
        voice_id="GENERIC",
        voice_id_de="DE-VOICE-UUID",
        voice_id_en="EN-VOICE-UUID",
        voice_id_es="ES-VOICE-UUID",
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, content=b"\x00\x01" * 100)

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize(
        "Ich bin Jarvis.", voice="EXPLICIT-OVERRIDE", language_code="de-DE"
    )]

    assert captured["voice"]["id"] == "EXPLICIT-OVERRIDE"


def test_list_voices_returns_all_lang_variants(patched_secret) -> None:
    tts = CartesiaTTS(
        voice_id="GENERIC",
        voice_id_de="DE-V",
        voice_id_en="EN-V",
        voice_id_es="ES-V",
    )
    voices = tts.list_voices()
    assert "DE-V" in voices and "EN-V" in voices and "ES-V" in voices


def test_list_voices_with_language_filter(patched_secret) -> None:
    tts = CartesiaTTS(
        voice_id="GENERIC",
        voice_id_de="DE-V",
        voice_id_en="EN-V",
        voice_id_es="ES-V",
    )
    assert tts.list_voices(language="de-DE") == ["DE-V"]
    assert tts.list_voices(language="es") == ["ES-V"]
