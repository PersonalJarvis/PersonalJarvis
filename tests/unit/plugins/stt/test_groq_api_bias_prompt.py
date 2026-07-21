"""Unit tests for GroqWhisperAPI bias-prompt wiring.

Background (2026-05-26): the user reported that some words — Eigennamen, tech
terms, longer German compounds — get mis-transcribed even though simple words
come through fine. Root cause: the plugin was sending only ``model``,
``language``, ``temperature`` and ``response_format`` to Groq's Whisper endpoint,
but never the ``prompt`` parameter. Whisper accepts up to 224 tokens of
``prompt`` text and uses it as Bayesian bias on the token distribution — that
is the same mechanism third-party dictation tools use to keep user-specific
vocabulary stable.

These tests lock the wiring in place so a future refactor cannot silently drop
the bias-prompt and re-open the regression.
"""
from __future__ import annotations

import httpx
import pytest

from jarvis.plugins.stt.groq_api import GroqWhisperAPI

_JSON_OK = {
    "text": "hello",
    "language": "german",
    "segments": [
        {"start": 0.0, "end": 0.5, "text": "hello", "avg_logprob": -0.1},
    ],
}


def _silent_pcm(num_samples: int = 1600) -> bytes:
    """0.1 s of silence at 16 kHz, int16-LE."""
    return b"\x00\x00" * num_samples


def _extract_form_field(multipart_body: bytes, field_name: str) -> str | None:
    """Pull a single text field's value out of a multipart/form-data body.

    Crude but enough for tests: locate the boundary block whose
    ``Content-Disposition`` names ``field_name``, then read the bytes between
    the empty CRLF-CRLF separator and the next boundary marker.
    """
    needle = f'name="{field_name}"'.encode("utf-8")
    idx = multipart_body.find(needle)
    if idx == -1:
        return None
    head_end = multipart_body.find(b"\r\n\r\n", idx)
    if head_end == -1:
        return None
    value_start = head_end + 4
    value_end = multipart_body.find(b"\r\n--", value_start)
    if value_end == -1:
        return None
    return multipart_body[value_start:value_end].decode("utf-8")


# -- Behaviour we want --------------------------------------------------------

@pytest.mark.asyncio
async def test_prompt_is_sent_when_constructor_param_is_set() -> None:
    """When constructed with prompt='...', the field rides along on every POST."""
    captured: dict[str, bytes] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        return httpx.Response(200, json=_JSON_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    stt = GroqWhisperAPI(
        api_key="test-key",
        prompt="Jarvis, Jarvis-Agent, Mission-Manager, Subagent.",
        http_client=client,
    )
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    value = _extract_form_field(captured["content"], "prompt")
    assert value is not None, "prompt field must appear in the multipart body"
    assert "Jarvis" in value and "Jarvis-Agent" in value


@pytest.mark.asyncio
async def test_prompt_field_omitted_when_not_set() -> None:
    """Backwards-compat: without a prompt, nothing is sent — same wire as before."""
    captured: dict[str, bytes] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        return httpx.Response(200, json=_JSON_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    stt = GroqWhisperAPI(api_key="test-key", http_client=client)
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    assert _extract_form_field(captured["content"], "prompt") is None


@pytest.mark.asyncio
async def test_prompt_is_capped_to_token_safe_length() -> None:
    """Whisper hard-caps the prompt at 224 tokens (~1000 chars). We must not
    let a user-pasted multi-page vocabulary push us past that — Groq would
    400-reject and the whole turn would go silent.
    """
    long_prompt = ("Wort " * 500).strip()  # 2499 chars
    assert len(long_prompt) > 1024
    captured: dict[str, bytes] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        return httpx.Response(200, json=_JSON_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    stt = GroqWhisperAPI(api_key="test-key", prompt=long_prompt, http_client=client)
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    value = _extract_form_field(captured["content"], "prompt")
    assert value is not None
    assert len(value) <= 1024


@pytest.mark.asyncio
async def test_empty_prompt_is_treated_as_unset() -> None:
    """An empty string from a config default must not become a literal empty
    multipart field — that confuses some HTTP servers and adds noise."""
    captured: dict[str, bytes] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        return httpx.Response(200, json=_JSON_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    stt = GroqWhisperAPI(api_key="test-key", prompt="   ", http_client=client)
    try:
        await stt.transcribe_pcm(_silent_pcm())
    finally:
        await stt.aclose()

    assert _extract_form_field(captured["content"], "prompt") is None
