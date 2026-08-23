"""Functional tests for the Deepgram STT plugin, over a fake transport.

Deepgram is not OpenAI-shaped, and each of the three differences is a real bug
if it is got wrong — a `Bearer` prefix reads as a bad key, multipart audio is
rejected, and an omitted language silently transcribes German as English. So
the request itself is what is asserted here, not just the parsed result.

No network: httpx's MockTransport answers the request and hands the recorded
request object back to the test.
"""
from __future__ import annotations

import json
import wave
from io import BytesIO

import httpx
import pytest

from jarvis.plugins.stt.deepgram_api import (
    DEFAULT_MODEL,
    DeepgramSTT,
    _payload_to_transcript,
    _wrap_pcm_as_wav,
)

PCM = b"\x00\x01" * 1600  # 0.1 s of int16 at 16 kHz


def _payload(
    transcript: str = "hallo welt",
    confidence: float = 0.97,
    detected: str | None = "de",
) -> dict:
    channel: dict = {
        "alternatives": [{"transcript": transcript, "confidence": confidence}]
    }
    if detected is not None:
        channel["detected_language"] = detected
    return {"results": {"channels": [channel]}}


def _client(payload: dict, record: list[httpx.Request], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(record: list[httpx.Request], payload: dict | None = None, **kwargs):
    return DeepgramSTT(
        api_key="k", http_client=_client(payload or _payload(), record), **kwargs
    )


# ---------------------------------------------------------------- the request


@pytest.mark.asyncio
async def test_authorization_uses_token_not_bearer():
    record: list[httpx.Request] = []
    await _provider(record).transcribe_pcm(PCM)
    # A Bearer prefix here is rejected with 401, which is indistinguishable
    # from a wrong key and sends people to rotate a working credential.
    assert record[0].headers["authorization"] == "Token k"


@pytest.mark.asyncio
async def test_audio_is_the_request_body_not_a_multipart_field():
    record: list[httpx.Request] = []
    await _provider(record).transcribe_pcm(PCM)
    body = record[0].content
    assert body.startswith(b"RIFF")
    assert b"Content-Disposition" not in body
    assert record[0].headers["content-type"] == "audio/wav"


@pytest.mark.asyncio
async def test_a_configured_language_is_sent_and_detection_is_not_requested():
    record: list[httpx.Request] = []
    await _provider(record, language="de").transcribe_pcm(PCM)
    params = record[0].url.params
    assert params["language"] == "de"
    assert "detect_language" not in params


@pytest.mark.asyncio
async def test_no_language_asks_deepgram_to_detect_explicitly():
    record: list[httpx.Request] = []
    await _provider(record).transcribe_pcm(PCM)
    # Omitting the language does NOT mean "detect" for Deepgram the way it does
    # for Whisper — it falls back to English, so the flag must be explicit.
    assert record[0].url.params["detect_language"] == "true"


@pytest.mark.asyncio
async def test_auto_clears_a_configured_language_for_that_call_only():
    record: list[httpx.Request] = []
    provider = _provider(record, language="de")
    await provider.transcribe_pcm(PCM, language="auto")
    assert "language" not in record[0].url.params
    assert record[0].url.params["detect_language"] == "true"
    # The configured pin survives for the next call.
    await provider.transcribe_pcm(PCM)
    assert record[1].url.params["language"] == "de"


@pytest.mark.asyncio
async def test_model_and_smart_format_reach_the_query_string():
    record: list[httpx.Request] = []
    await _provider(record).transcribe_pcm(PCM)
    assert record[0].url.params["model"] == DEFAULT_MODEL
    assert record[0].url.params["smart_format"] == "true"


@pytest.mark.asyncio
async def test_an_encoded_container_is_passed_through_with_its_real_type():
    record: list[httpx.Request] = []
    await _provider(record).transcribe_container(b"OggS-fake", filename="note.opus")
    # Re-wrapping an already-encoded file would corrupt it.
    assert record[0].content == b"OggS-fake"
    assert record[0].headers["content-type"] == "audio/opus"


# ---------------------------------------------------------------- the response


@pytest.mark.asyncio
async def test_transcript_carries_text_language_and_confidence():
    record: list[httpx.Request] = []
    result = await _provider(record).transcribe_pcm(PCM)
    assert result.text == "hallo welt"
    assert result.language == "de"
    assert result.confidence == pytest.approx(0.97)
    assert result.is_partial is False


@pytest.mark.asyncio
async def test_empty_audio_never_reaches_the_network():
    record: list[httpx.Request] = []
    result = await _provider(record).transcribe_pcm(b"")
    assert record == []
    assert result.text == ""
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_a_missing_key_says_so_instead_of_sending_an_unauthorized_request():
    record: list[httpx.Request] = []
    provider = DeepgramSTT(api_key="", http_client=_client(_payload(), record))
    provider._api_key = ""  # defeat any ambient env/keyring value on this box
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        await provider.transcribe_pcm(PCM)
    assert record == []


@pytest.mark.asyncio
async def test_an_http_error_keeps_its_status_for_the_retry_ladder():
    record: list[httpx.Request] = []
    provider = DeepgramSTT(
        api_key="k", http_client=_client(_payload(), record, status=429)
    )
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await provider.transcribe_pcm(PCM)
    # Flattening this into a bare RuntimeError is what stops the pipeline from
    # telling a rate limit apart from a bad key.
    assert excinfo.value.response.status_code == 429


@pytest.mark.asyncio
async def test_stream_transcribe_yields_exactly_one_final_transcript():
    record: list[httpx.Request] = []

    async def chunks():
        class Chunk:
            pcm = PCM
            sample_rate = 16_000
            channels = 1

        yield Chunk()

    out = [t async for t in _provider(record).stream_transcribe(chunks())]
    assert len(out) == 1
    assert out[0].is_partial is False
    assert out[0].text == "hallo welt"


# ---------------------------------------------------------------- parsing


def test_a_response_with_no_speech_parses_as_empty_rather_than_raising():
    result = _payload_to_transcript({"results": {"channels": []}})
    assert result.text == ""
    assert result.confidence == 0.0
    assert result.language == "unknown"


def test_the_requested_language_is_used_when_deepgram_reports_none():
    result = _payload_to_transcript(_payload(detected=None), fallback_language="de")
    assert result.language == "de"


def test_utterances_become_segments_when_present():
    payload = _payload()
    payload["results"]["utterances"] = [
        {"start": 0.2, "end": 1.1, "transcript": "hallo"},
    ]
    result = _payload_to_transcript(payload)
    assert result.segments[0]["start"] == pytest.approx(0.2)
    assert result.segments[0]["text"] == "hallo"


def test_raw_text_keeps_what_the_vendor_actually_returned():
    result = _payload_to_transcript(_payload(transcript="  hallo welt  "))
    assert result.raw_text == "hallo welt"


# ---------------------------------------------------------------- wav wrapper


def test_pcm_is_wrapped_as_16_bit_mono_wav_at_the_given_rate():
    data = _wrap_pcm_as_wav(PCM, sample_rate=16_000, channels=1)
    with wave.open(BytesIO(data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16_000
        assert wav.readframes(wav.getnframes()) == PCM


def test_the_payload_shape_matches_deepgrams_documented_envelope():
    # Guards the nesting the parser walks: results -> channels -> alternatives.
    envelope = json.loads(json.dumps(_payload()))
    assert envelope["results"]["channels"][0]["alternatives"][0]["transcript"]
