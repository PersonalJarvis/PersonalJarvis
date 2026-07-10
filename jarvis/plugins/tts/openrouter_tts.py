"""OpenRouter Text-to-Speech plugin (OpenAI Audio-Speech compatible).

One OpenRouter API key — the SAME ``openrouter_api_key`` the OpenRouter brain /
Jarvis-Agent providers already use — reaches every speech-synthesis model
OpenRouter hosts (Gemini Flash TTS, Grok Voice, Microsoft MAI-Voice, Mistral
Voxtral, Kokoro, Orpheus, Zonos, Sesame CSM, ...).

Endpoint (verified live 2026-07-02):
  ``POST https://openrouter.ai/api/v1/audio/speech`` — OpenAI-compatible.
  Body ``{"model","input","voice","response_format":"pcm"|"mp3","speed"?}``.
  ``response_format`` accepts ONLY ``"mp3"`` and ``"pcm"`` (``wav`` is rejected
  with a Zod validation error). We request ``pcm`` and the endpoint answers with
  ``Content-Type: audio/pcm;rate=24000;channels=1`` — raw 16-bit signed
  little-endian mono PCM at 24 kHz, i.e. EXACTLY the format the Jarvis playback
  pipeline consumes (same as Gemini / Grok / Cartesia), so no decode and no
  resample is needed. Streaming is real chunked-transfer over ``aiter_bytes``, so
  the first audio frame leaves as soon as the provider flushes it.

Error contract (open-source AP-22 resilience): a fatal error (missing key,
401/402/403/429, or a transport failure) is raised BEFORE the first
``AudioChunk`` is yielded. The provider-level ``FallbackTTS`` wrapper catches a
pre-first-chunk failure and crosses to the configured fallback voice, so a dead
/ keyless / rate-limited OpenRouter never bricks the TTS tier. Once audio has
started we re-raise rather than restart (no duplicated opening words).
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk
from jarvis.plugins.tts.openrouter_speech_models import (
    GENERIC_DEFAULT_VOICE,
    MODEL_DEFAULT_VOICE,
    MODEL_VOICES,
    coerce_speech_model,
    voice_matches_language,
)

log = logging.getLogger("jarvis.tts.openrouter")

# Raw PCM the endpoint returns: 16-bit signed LE, mono, 24 kHz — the pipeline's
# native playback format (audio/pcm;rate=24000;channels=1, verified live).
OPENROUTER_TTS_SAMPLE_RATE = 24_000
OPENROUTER_TTS_ENDPOINT_PATH = "/audio/speech"
BASE_URL = "https://openrouter.ai/api/v1"
_HTTP_TIMEOUT_S = 60.0
# OpenAI-style speech endpoints cap input length; keep a generous bound.
_MAX_CHARS_PER_REQUEST = 12_000


class OpenRouterTTSError(RuntimeError):
    """Fatal OpenRouter TTS error (no key / auth / quota / transport).

    Raised before any audio is yielded so ``FallbackTTS`` can cross to another
    TTS family instead of leaving Jarvis mute.
    """


class OpenRouterTTS:
    """TTS provider for OpenRouter's ``/audio/speech`` endpoint.

    Structurally compatible with the ``TTSProvider`` protocol — no inheritance
    from ``jarvis.*`` (the factory dispatches on it explicitly).
    """

    name = "openrouter"
    supports_streaming = True

    def __init__(
        self,
        model: str | None = None,
        default_voice: str | None = None,
        voice_de: str | None = None,
        voice_en: str | None = None,
        language: str = "auto",
        speed: float = 1.0,
    ) -> None:
        # Coerce a foreign / unknown / empty model id to a real OpenRouter speech
        # model. The [tts] block shares ONE global `model` across all TTS
        # providers, so switching to OpenRouter can inherit e.g. Cartesia's
        # `sonic-2` — sending that verbatim 400s ("Model sonic-2 does not
        # exist"). This makes an untouched / cross-provider config Just Work.
        requested = (model or "").strip()
        self._model = coerce_speech_model(model)
        if requested and requested != self._model:
            log.info(
                "TTS model %r is not an OpenRouter speech model — using %r.",
                requested, self._model,
            )
        # Per-language config voices (mirrors the [tts] voice_de / voice_en split).
        self._voice_de = (voice_de or "").strip() or None
        self._voice_en = (voice_en or "").strip() or None
        # A single explicit default voice (used when no per-language voice fits).
        self._default_voice = (default_voice or "").strip() or None
        self._language = language or "auto"
        self._speed = speed
        self._client: Any = None  # httpx.AsyncClient, lazy
        # Credential/base URL used by the internally-created client. ``None``
        # while a test or embedding injects its own client, which remains fully
        # caller-owned and is never replaced here.
        self._resolved_credential: str | None = None
        self._resolved_base_url: str | None = None
        # Overridden by _ensure_client with the resolved endpoint; a default here
        # lets a directly-injected client (tests / warm reuse) synthesise without
        # re-resolving the key.
        self._base_url = BASE_URL

    # ------------------------------------------------------------------
    # Auth + client
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> None:
        """Build or refresh the client from the ONE shared OpenRouter key.

        Raises :class:`OpenRouterTTSError` when no credential is configured — the
        same clear, fail-closed signal the factory / FallbackTTS expects. The
        endpoint is resolved on every synthesis boundary so replacing the shared
        brain/TTS/STT credential takes effect without rebuilding the speech
        pipeline. An unchanged credential reuses the existing keep-alive pool.
        """
        # A directly injected client (tests / embedding) has no resolved
        # credential marker and stays caller-owned.
        if self._client is not None and self._resolved_credential is None:
            return
        ep = cfg.resolve_provider_endpoint(
            "openrouter", vendor_default_base_url=BASE_URL
        )
        if not ep.credential:
            raise OpenRouterTTSError(
                "No OpenRouter API key found (openrouter_api_key / "
                "OPENROUTER_API_KEY). It is the SAME key the OpenRouter brain "
                "uses — set it in the API-Keys view or in .env."
            )
        import httpx

        resolved_base_url = (ep.base_url or BASE_URL).rstrip("/")
        if (
            self._client is not None
            and self._resolved_credential == ep.credential
            and self._resolved_base_url == resolved_base_url
        ):
            return

        old_client = self._client
        self._base_url = resolved_base_url
        self._client = httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_S,
            headers={
                "Authorization": f"Bearer {ep.credential}",
                "Content-Type": "application/json",
                # OpenRouter attribution headers (same as the brain plugin).
                "HTTP-Referer": "https://github.com/PersonalJarvis",
                "X-Title": "Personal Jarvis",
            },
        )
        self._resolved_credential = ep.credential
        self._resolved_base_url = resolved_base_url
        if old_client is not None:
            try:
                await old_client.aclose()
            except Exception as exc:  # noqa: BLE001 -- replacement already succeeded
                log.debug("Closing the superseded OpenRouter TTS client failed: %s", exc)

    async def aclose(self) -> None:
        """Close the HTTP client. Idempotent."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None
                self._resolved_credential = None
                self._resolved_base_url = None

    # ------------------------------------------------------------------
    # Voice resolution
    # ------------------------------------------------------------------

    def _resolve_voice(self, voice: str | None, language_code: str | None) -> str:
        """Pick a voice VALID for the current model.

        Priority: explicit per-call ``voice`` → the config voice for the resolved
        language (``voice_de`` for German, else ``voice_en``) → the single
        ``default_voice`` → the model's own default. The chosen voice is then
        validated against the model's ``supported_voices`` (curated snapshot); an
        invalid voice (e.g. a Gemini name left over after switching to Grok) is
        replaced by the model default with a logged note, so the provider never
        400s on a foreign voice id.
        """
        chosen = voice
        if not chosen:
            lang = (language_code or self._language or "").lower()
            is_de = lang.startswith("de")
            chosen = (self._voice_de if is_de else self._voice_en) or self._default_voice
        return self._validate_voice(chosen)

    def _validate_voice(self, voice: str | None) -> str:
        allowed = MODEL_VOICES.get(self._model)
        model_default = (
            MODEL_DEFAULT_VOICE.get(self._model)
            or (allowed[0] if allowed else None)
            or GENERIC_DEFAULT_VOICE
        )
        # Unknown model (not in the curated snapshot): trust the caller's voice,
        # since we cannot validate it — never block a model we simply do not know.
        if allowed is None:
            return voice or model_default
        if voice and voice in allowed:
            return voice
        if voice:
            log.info(
                "Voice %r is not valid for OpenRouter TTS model %r — using %r.",
                voice, self._model, model_default,
            )
        return model_default

    def list_voices(self, language: str | None = None) -> list[str]:
        """Voices valid for the CURRENT model (from the curated per-model map).

        When ``language`` is given and the model's voice ids are language-prefixed
        (e.g. Kokoro's ``de_``/``af_``, MAI-Voice's ``de-DE-...``), the list is
        narrowed to that language — but never to empty, so the picker always shows
        something valid. Language-agnostic models (Gemini, Grok) return all voices.
        """
        voices = list(MODEL_VOICES.get(self._model, ()))
        if not voices:
            dv = MODEL_DEFAULT_VOICE.get(self._model) or self._default_voice
            return [dv] if dv else []
        if language:
            short = language.lower().split("-", 1)[0]
            narrowed = [v for v in voices if voice_matches_language(v, short)]
            if narrowed:
                return narrowed
        return voices

    # ------------------------------------------------------------------
    # Synthesis (streaming)
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Stream synthesised speech as 24 kHz mono s16le PCM ``AudioChunk``s.

        A fatal error (no key / 4xx / transport) is raised BEFORE the first chunk
        so ``FallbackTTS`` can cross to another provider. Language is inferred by
        the model from ``input`` (the OpenAI speech API takes no language field);
        ``voice`` pins the speaker.
        """
        text = (text or "").strip()
        if not text:
            return

        # No key / build failure → raise before any chunk (FallbackTTS crosses).
        await self._ensure_client()

        resolved_voice = self._resolve_voice(voice, language_code)
        payload: dict[str, Any] = {
            "model": self._model,
            "input": text[:_MAX_CHARS_PER_REQUEST],
            "voice": resolved_voice,
            "response_format": "pcm",
        }
        # Only send speed when non-default: some speech models reject an
        # unexpected field; the default (1.0) never needs it.
        if self._speed and self._speed != 1.0:
            payload["speed"] = self._speed

        assert self._client is not None
        url = self._base_url + OPENROUTER_TTS_ENDPOINT_PATH
        carry = b""
        try:
            async with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    detail = body.decode("utf-8", "replace")[:300] if body else "<empty>"
                    raise OpenRouterTTSError(
                        f"OpenRouter TTS HTTP {resp.status_code} "
                        f"(model={self._model}, voice={resolved_voice}): {detail}"
                    )
                async for raw in resp.aiter_bytes():
                    if not raw:
                        continue
                    buf = carry + raw
                    # Keep 16-bit sample alignment; hold back an odd trailing byte.
                    aligned = len(buf) - (len(buf) % 2)
                    chunk, carry = buf[:aligned], buf[aligned:]
                    if chunk:
                        yield AudioChunk(
                            pcm=chunk,
                            sample_rate=OPENROUTER_TTS_SAMPLE_RATE,
                            timestamp_ns=0,
                            channels=1,
                        )
        except OpenRouterTTSError:
            raise
        except Exception as exc:  # noqa: BLE001 — transport/stream failure
            # Wrap as a fatal error so a pre-first-chunk failure crosses families;
            # if chunks already flowed, FallbackTTS re-raises (no double audio).
            raise OpenRouterTTSError(
                f"OpenRouter TTS request failed ({exc.__class__.__name__}): {exc}"
            ) from exc

        if carry:
            # Flush a leftover odd byte padded to a full sample (defensive; the
            # 24 kHz s16le stream is byte-even in practice).
            yield AudioChunk(
                pcm=carry + b"\x00",
                sample_rate=OPENROUTER_TTS_SAMPLE_RATE,
                timestamp_ns=0,
                channels=1,
            )
