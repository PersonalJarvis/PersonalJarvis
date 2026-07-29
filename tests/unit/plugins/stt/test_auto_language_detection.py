"""Every STT provider must treat a per-call ``language="auto"`` as DETECT.

The regression these lock down (live bug 2026-07-28): dictation's language
setting offered "Automatic", and "automatic" was implemented as *not passing a
language at all*. But an absent argument does not mean "detect" to a provider —
it means "no per-call opinion", so the transcription fell back to the language
configured in ``[stt].language``. With the recognition language pinned to English
a user dictating German got English words back, and nothing in the dictation view
could fix it because the dictation view was not the setting in charge.

So the contract is now explicit, and these tests pin it for all five providers:

* a concrete code  → transcribe as that language;
* ``"auto"``       → DETECT, clearing the configured language for this call;
* nothing passed   → the configured language stands (unchanged behaviour).

Cloud providers are exercised through ``httpx.MockTransport`` / a hand-rolled
fake client (never ``unittest.mock``), asserting on the actual outgoing request.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jarvis.plugins.stt.gemini_api import GeminiSTT
from jarvis.plugins.stt.groq_api import GroqWhisperAPI
from jarvis.plugins.stt.openai_api import OpenAIWhisperAPI
from jarvis.plugins.stt.openrouter_stt import OpenRouterSTT

_PCM = b"\x00\x00" * 1600  # 0.1 s of silence at 16 kHz


# ---------------------------------------------------------------------------
# Multipart helpers (Groq + OpenAI post form data)
# ---------------------------------------------------------------------------

def _form_field(body: bytes, name: str) -> str | None:
    """The value of one text field in a multipart body, or None when absent."""
    needle = f'name="{name}"'.encode()
    idx = body.find(needle)
    if idx == -1:
        return None
    head_end = body.find(b"\r\n\r\n", idx)
    if head_end == -1:
        return None
    start = head_end + 4
    end = body.find(b"\r\n--", start)
    return body[start:end].decode("utf-8") if end != -1 else None


def _capturing_client(captured: dict[str, Any], payload: dict[str, Any]):
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_MULTIPART_OK = {"text": "guten morgen", "language": "german", "segments": []}
_JSON_OK = {"text": "guten morgen"}


# ---------------------------------------------------------------------------
# Groq / OpenAI — identical multipart shape, so one parametrised suite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_cls", [GroqWhisperAPI, OpenAIWhisperAPI])
@pytest.mark.asyncio
async def test_auto_clears_a_configured_language(provider_cls) -> None:
    """The core regression: a configured "en" must NOT survive an auto call."""
    captured: dict[str, Any] = {}
    stt = provider_cls(
        api_key="test-key",
        language="en",
        http_client=_capturing_client(captured, _MULTIPART_OK),
    )

    await stt.transcribe_pcm(_PCM, language="auto")

    # Omitting the field entirely is how Whisper is asked to detect. Sending
    # "auto" would be just as wrong — it is not a language code.
    assert _form_field(captured["content"], "language") is None


@pytest.mark.parametrize("provider_cls", [GroqWhisperAPI, OpenAIWhisperAPI])
@pytest.mark.asyncio
async def test_a_concrete_language_still_overrides_the_configured_one(
    provider_cls,
) -> None:
    captured: dict[str, Any] = {}
    stt = provider_cls(
        api_key="test-key",
        language="en",
        http_client=_capturing_client(captured, _MULTIPART_OK),
    )

    await stt.transcribe_pcm(_PCM, language="de")

    assert _form_field(captured["content"], "language") == "de"


@pytest.mark.parametrize("provider_cls", [GroqWhisperAPI, OpenAIWhisperAPI])
@pytest.mark.asyncio
async def test_passing_nothing_keeps_the_configured_language(provider_cls) -> None:
    """Unchanged behaviour — the voice path relies on it."""
    captured: dict[str, Any] = {}
    stt = provider_cls(
        api_key="test-key",
        language="en",
        http_client=_capturing_client(captured, _MULTIPART_OK),
    )

    await stt.transcribe_pcm(_PCM)

    assert _form_field(captured["content"], "language") == "en"


@pytest.mark.parametrize("provider_cls", [GroqWhisperAPI, OpenAIWhisperAPI])
@pytest.mark.asyncio
async def test_the_configured_language_is_not_mutated_by_one_call(
    provider_cls,
) -> None:
    """A per-call override must not leak into the NEXT call.

    The previous implementation swapped ``self._language`` for the duration of
    the request, which two concurrent transcriptions (a dictation while a voice
    turn finishes) could interleave into the wrong language.
    """
    captured: dict[str, Any] = {}
    stt = provider_cls(
        api_key="test-key",
        language="en",
        http_client=_capturing_client(captured, _MULTIPART_OK),
    )

    await stt.transcribe_pcm(_PCM, language="auto")
    await stt.transcribe_pcm(_PCM)

    assert _form_field(captured["content"], "language") == "en"


# ---------------------------------------------------------------------------
# OpenRouter — JSON body
# ---------------------------------------------------------------------------

def _openrouter(captured: dict[str, Any], language: str | None) -> OpenRouterSTT:
    async def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_JSON_OK)

    return OpenRouterSTT(
        api_key="test-key",
        language=language,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_openrouter_auto_clears_a_configured_language() -> None:
    captured: dict[str, Any] = {}
    stt = _openrouter(captured, "en")

    await stt.transcribe_pcm(_PCM, language="auto")

    assert "language" not in captured["body"]


@pytest.mark.asyncio
async def test_openrouter_keeps_the_configured_language_without_an_override() -> None:
    captured: dict[str, Any] = {}
    stt = _openrouter(captured, "en")

    await stt.transcribe_pcm(_PCM)

    assert captured["body"]["language"] == "en"


# ---------------------------------------------------------------------------
# Gemini — the language rides in the prompt text, not a request field
# ---------------------------------------------------------------------------

@dataclass
class _FakeResponse:
    text: str


class _FakeModels:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def generate_content(self, *, model, contents, config):  # noqa: ANN001
        self._captured["contents"] = contents
        return _FakeResponse("guten morgen")


class _FakeClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.models = _FakeModels(captured)


def _instruction(captured: dict[str, Any]) -> str:
    parts = captured["contents"][0]["parts"]
    return " ".join(str(p.get("text", "")) for p in parts)


@pytest.mark.asyncio
async def test_gemini_auto_drops_the_language_sentence() -> None:
    """Telling a generative model "the spoken language is English" is what makes
    it write English — so an auto call must not tell it anything."""
    captured: dict[str, Any] = {}
    stt = GeminiSTT(client=_FakeClient(captured), language="en")

    await stt.transcribe_pcm(_PCM, language="auto")

    assert "spoken language" not in _instruction(captured)


@pytest.mark.asyncio
async def test_gemini_keeps_the_configured_language_without_an_override() -> None:
    captured: dict[str, Any] = {}
    stt = GeminiSTT(client=_FakeClient(captured), language="en")

    await stt.transcribe_pcm(_PCM)

    assert "The spoken language is 'en'." in _instruction(captured)


# ---------------------------------------------------------------------------
# faster-whisper — local engine, so assert on the decode argument
# ---------------------------------------------------------------------------

class _FakeWhisperModel:
    """Records the ``language`` faster-whisper is asked to decode with."""

    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def transcribe(self, audio, **kwargs):  # noqa: ANN001
        self._captured["language"] = kwargs.get("language")
        info = SimpleNamespace(language="de", language_probability=0.99)
        return iter(()), info


def _fwhisper(captured: dict[str, Any], language: str | None):
    from jarvis.plugins.stt import fwhisper as fwhisper_mod

    provider = fwhisper_mod.FasterWhisperProvider(language=language)
    provider._model = _FakeWhisperModel(captured)
    # The lazy builder would try to load real weights; the fake model is already
    # in place, so keep _ensure_model a no-op for this instance.
    provider._ensure_model = lambda: None  # type: ignore[method-assign]
    return provider


def test_fwhisper_auto_clears_a_configured_language() -> None:
    captured: dict[str, Any] = {}
    provider = _fwhisper(captured, "en")

    provider._transcribe_sync(_pcm_as_float(), 16_000, "auto")

    # None is faster-whisper's own "detect the language" value; the string
    # "auto" is not a language it knows and would decode as garbage.
    assert captured["language"] is None


def test_fwhisper_keeps_the_configured_language_without_an_override() -> None:
    captured: dict[str, Any] = {}
    provider = _fwhisper(captured, "en")

    provider._transcribe_sync(_pcm_as_float(), 16_000, None)

    assert captured["language"] == "en"


def _pcm_as_float():
    import numpy as np

    return np.zeros(1600, dtype=np.float32)
