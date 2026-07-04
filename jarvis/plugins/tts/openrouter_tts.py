"""OpenRouter Text-to-Speech plugin (OpenAI Audio-Speech compatible).

One OpenRouter API key — the SAME ``openrouter_api_key`` the OpenRouter brain /
Jarvis-Agent providers already use — reaches every speech-synthesis model
OpenRouter hosts (Gemini Flash TTS, Grok Voice, Microsoft MAI-Voice, Mistral
Voxtral, Kokoro, Orpheus, Zonos, Sesame CSM, ...).

Endpoint (verified live 2026-07-02, format matrix 2026-07-03):
  ``POST https://openrouter.ai/api/v1/audio/speech`` — OpenAI-compatible.
  Body ``{"model","input","voice","response_format":"pcm"|"mp3","speed"?}``.
  ``response_format`` accepts ONLY ``"mp3"`` and ``"pcm"`` (``wav`` is rejected
  with a Zod validation error). **The format is chosen PER MODEL, not blanket
  ``pcm``** (:func:`~jarvis.plugins.tts.openrouter_speech_models.response_format_for_model`):
  most models return raw ``Content-Type: audio/pcm;rate=<n>;channels=1`` — 16-bit
  signed little-endian mono PCM (24 kHz for the default) that the Jarvis playback
  pipeline consumes with NO decode — so those stream chunk-by-chunk over
  ``aiter_bytes`` and the first frame leaves as soon as the provider flushes it.
  An mp3-only model (Mistral Voxtral, which 400s on ``pcm``) is requested as
  ``mp3``, buffered, and decoded to 24 kHz mono PCM via the optional ``miniaudio``
  decoder (:func:`_decode_mp3_to_pcm`); missing the decoder fails honestly so
  ``FallbackTTS`` crosses to a PCM model. The true PCM rate is read from the
  response ``Content-Type`` so a non-24 kHz model never plays at the wrong pitch.

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
    response_format_for_model,
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
# When a whole (mp3-only) response is decoded up front, hand the PCM to the
# pipeline in ~0.1 s slices (24 kHz * 2 bytes * 0.1 s) so playback still starts
# promptly instead of one giant chunk.
_PCM_CHUNK_BYTES = OPENROUTER_TTS_SAMPLE_RATE * 2 // 10


def _sample_rate_from_content_type(content_type: str | None) -> int:
    """Parse the true PCM rate from ``audio/pcm;rate=24000;channels=1``.

    The endpoint reports the real sample rate in the response ``Content-Type``;
    honouring it keeps a non-24 kHz model (e.g. a 44.1 kHz voice) from playing at
    the wrong pitch. Falls back to :data:`OPENROUTER_TTS_SAMPLE_RATE` when the
    header is absent or unparseable.
    """
    if not content_type:
        return OPENROUTER_TTS_SAMPLE_RATE
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "rate":
            try:
                rate = int(value.strip())
            except ValueError:
                return OPENROUTER_TTS_SAMPLE_RATE
            return rate if rate > 0 else OPENROUTER_TTS_SAMPLE_RATE
    return OPENROUTER_TTS_SAMPLE_RATE


def _decode_mp3_to_pcm(data: bytes) -> bytes:
    """Decode mp3 bytes to 24 kHz mono s16le PCM (the pipeline's native format).

    mp3-only OpenRouter models (Mistral Voxtral) cannot return raw PCM, so their
    output is decoded here via ``miniaudio`` — a small, ffmpeg-free, universal
    wheel. The import is lazy so a base install WITHOUT the decoder fails
    honestly (a clear :class:`OpenRouterTTSError` raised before the first chunk,
    so ``FallbackTTS`` crosses to a PCM model) instead of breaking at import
    time or shipping silence.
    """
    try:
        import miniaudio
    except ImportError as exc:  # pragma: no cover - exercised via the honest-error path
        raise OpenRouterTTSError(
            "This OpenRouter TTS model returns mp3, which needs the optional "
            "'miniaudio' decoder (pip install miniaudio). Pick a PCM model "
            "(e.g. Gemini Flash TTS or Grok Voice) or install the decoder."
        ) from exc
    decoded = miniaudio.decode(
        data,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=OPENROUTER_TTS_SAMPLE_RATE,
    )
    return decoded.samples.tobytes()


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
        # Overridden by _ensure_client with the resolved endpoint; a default here
        # lets a directly-injected client (tests / warm reuse) synthesise without
        # re-resolving the key.
        self._base_url = BASE_URL

    # ------------------------------------------------------------------
    # Auth + client
    # ------------------------------------------------------------------

    def _ensure_client(self) -> None:
        """Build the shared httpx client, resolving the ONE OpenRouter key.

        Raises :class:`OpenRouterTTSError` when no credential is configured — the
        same clear, fail-closed signal the factory / FallbackTTS expects.
        """
        if self._client is not None:
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

        self._base_url = (ep.base_url or BASE_URL).rstrip("/")
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

    async def aclose(self) -> None:
        """Close the HTTP client. Idempotent."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

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
        self._ensure_client()

        resolved_voice = self._resolve_voice(voice, language_code)
        # Gate the wire format on a per-model CAPABILITY, never a blanket "pcm":
        # an mp3-only model (Mistral Voxtral) 400s on pcm. PCM streams raw and
        # plays with no decoder; mp3 is buffered and decoded to PCM below.
        response_format = response_format_for_model(self._model)
        payload: dict[str, Any] = {
            "model": self._model,
            "input": text[:_MAX_CHARS_PER_REQUEST],
            "voice": resolved_voice,
            "response_format": response_format,
        }
        # Only send speed when non-default: some speech models reject an
        # unexpected field; the default (1.0) never needs it.
        if self._speed and self._speed != 1.0:
            payload["speed"] = self._speed

        assert self._client is not None
        url = self._base_url + OPENROUTER_TTS_ENDPOINT_PATH

        if response_format == "mp3":
            async for chunk in self._synthesize_mp3(url, payload, resolved_voice):
                yield chunk
            return

        carry = b""
        sample_rate = OPENROUTER_TTS_SAMPLE_RATE
        try:
            async with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    detail = body.decode("utf-8", "replace")[:300] if body else "<empty>"
                    raise OpenRouterTTSError(
                        f"OpenRouter TTS HTTP {resp.status_code} "
                        f"(model={self._model}, voice={resolved_voice}): {detail}"
                    )
                # Honour the model's real rate (audio/pcm;rate=<n>) so a non-24 kHz
                # model does not play at the wrong pitch.
                sample_rate = _sample_rate_from_content_type(
                    getattr(resp, "headers", {}).get("content-type")
                    if getattr(resp, "headers", None)
                    else None
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
                            sample_rate=sample_rate,
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
                sample_rate=sample_rate,
                timestamp_ns=0,
                channels=1,
            )

    async def _synthesize_mp3(
        self,
        url: str,
        payload: dict[str, Any],
        resolved_voice: str,
    ) -> AsyncIterator[AudioChunk]:
        """Buffer an mp3-only response, decode to PCM, and yield it in slices.

        mp3 is not cleanly decodable at arbitrary network-chunk boundaries, so
        the whole (short TTS) clip is read, then decoded to 24 kHz mono s16le PCM
        and handed out in ``_PCM_CHUNK_BYTES`` slices. A fatal error (4xx, decode
        failure, missing decoder, transport) is raised BEFORE the first chunk so
        ``FallbackTTS`` can cross to a PCM model.
        """
        assert self._client is not None
        buf = bytearray()
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
                    if raw:
                        buf.extend(raw)
        except OpenRouterTTSError:
            raise
        except Exception as exc:  # noqa: BLE001 — transport/stream failure
            raise OpenRouterTTSError(
                f"OpenRouter TTS request failed ({exc.__class__.__name__}): {exc}"
            ) from exc

        # Decode OUTSIDE the transport try so a decoder/format error surfaces as
        # its own honest OpenRouterTTSError (raised before any chunk is yielded).
        pcm = _decode_mp3_to_pcm(bytes(buf))
        for start in range(0, len(pcm), _PCM_CHUNK_BYTES):
            chunk = pcm[start : start + _PCM_CHUNK_BYTES]
            if chunk:
                yield AudioChunk(
                    pcm=chunk,
                    sample_rate=OPENROUTER_TTS_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )
