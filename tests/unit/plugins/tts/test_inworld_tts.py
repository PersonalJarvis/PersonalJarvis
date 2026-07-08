"""Unit tests for InworldTTS — mocked httpx, no live API calls.

Inworld returns base64 audio inside JSON (WAV-wrapped LINEAR16 for the
non-streaming endpoint), so the plugin must b64-decode AND strip the RIFF
header to hand the pipeline raw s16le PCM like every other provider.
"""
from __future__ import annotations

import base64
import io
import wave
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jarvis.plugins.tts.inworld_tts import (
    INWORLD_TTS_SAMPLE_RATE,
    InworldTTS,
    strip_wav_header,
)


def _wav_b64(pcm: bytes) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(INWORLD_TTS_SAMPLE_RATE)
        w.writeframes(pcm)
    return base64.b64encode(buf.getvalue()).decode()


class _Resp:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict:
        return self._json


@pytest.fixture
def patched_secret():
    with patch(
        "jarvis.plugins.tts.inworld_tts.cfg.get_secret",
        return_value="d29ya3NwYWNlLWtleTpzZWNyZXQ=",  # a base64-looking test key
    ):
        yield


@pytest.fixture
def tts(patched_secret) -> InworldTTS:
    return InworldTTS(allow_sapi5_fallback=False)


def test_strip_wav_header_removes_riff_but_leaves_raw_pcm():
    pcm = b"\x11\x22" * 500
    wav = base64.b64decode(_wav_b64(pcm))
    assert wav.startswith(b"RIFF")
    assert strip_wav_header(wav) == pcm
    # Raw PCM without a header must pass through unchanged.
    assert strip_wav_header(pcm) == pcm


@pytest.mark.asyncio
async def test_synthesize_decodes_and_strips_to_raw_pcm(tts: InworldTTS):
    pcm = b"\x00\x01" * 1000
    resp = _Resp(200, json_data={"audioContent": _wav_b64(pcm)})
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    tts._client = mock_client

    chunks = [c async for c in tts.synthesize("Hallo Welt.")]

    assert len(chunks) == 1
    assert chunks[0].pcm == pcm
    assert chunks[0].sample_rate == INWORLD_TTS_SAMPLE_RATE
    assert chunks[0].channels == 1


@pytest.mark.asyncio
async def test_payload_uses_linear16_24k_and_default_model(patched_secret):
    tts = InworldTTS()
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, json_data={"audioContent": _wav_b64(b"\x00\x01" * 100)})

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize("Hi.", language_code="de-DE")]

    assert captured["audioConfig"]["audioEncoding"] == "LINEAR16"
    assert captured["audioConfig"]["sampleRateHertz"] == INWORLD_TTS_SAMPLE_RATE
    assert captured["modelId"] == "inworld-tts-2"
    assert captured["language"] == "de-DE"
    assert captured["voiceId"] == "Josef"  # German default voice


@pytest.mark.asyncio
async def test_language_code_picks_matching_voice(patched_secret):
    tts = InworldTTS(
        default_voice_de="Josef", default_voice_en="Dennis", default_voice_es="Diego"
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, json_data={"audioContent": _wav_b64(b"\x00\x01" * 100)})

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize("Hola.", language_code="es-ES")]
    assert captured["voiceId"] == "Diego"


@pytest.mark.asyncio
async def test_auto_language_is_omitted(tts: InworldTTS):
    captured: dict[str, Any] = {}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        captured.update(json)
        return _Resp(200, json_data={"audioContent": _wav_b64(b"\x00\x01" * 100)})

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    [_ async for _ in tts.synthesize("Hello.", language_code="auto")]
    assert "language" not in captured


@pytest.mark.asyncio
async def test_basic_auth_header(patched_secret):
    tts = InworldTTS()
    tts._ensure_client()
    try:
        auth = tts._client.headers.get("authorization")
        assert auth is not None and auth.startswith("Basic ")
    finally:
        await tts.aclose()


@pytest.mark.asyncio
async def test_401_triggers_cooldown_and_falls_back(tts: InworldTTS):
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(401, text="unauthorized"))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(pcm=b"FB", sample_rate=24_000, timestamp_ns=0, channels=1)

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Test.")]

    assert chunks and chunks[0].pcm == b"FB"
    assert tts._quota_blocked_until > 0


@pytest.mark.asyncio
async def test_missing_key_falls_back_without_raising():
    with patch(
        "jarvis.plugins.tts.inworld_tts.cfg.get_secret", return_value=None
    ):
        tts = InworldTTS()

        async def fake_fallback(text: str, lang: str | None):
            from jarvis.core.protocols import AudioChunk

            yield AudioChunk(pcm=b"FB", sample_rate=24_000, timestamp_ns=0, channels=1)

        with patch.object(tts, "_fallback", side_effect=fake_fallback):
            chunks = [c async for c in tts.synthesize("Hi.")]
        assert chunks and chunks[0].pcm == b"FB"


def test_list_voices_by_language(patched_secret):
    tts = InworldTTS(
        default_voice_de="Josef", default_voice_en="Dennis", default_voice_es="Diego"
    )
    assert tts.list_voices("de") == ["Josef"]
    assert tts.list_voices("es") == ["Diego"]
    allv = tts.list_voices()
    assert {"Josef", "Dennis", "Diego"} <= set(allv)
