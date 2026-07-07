"""Inworld AI TTS plugin (inworld-tts-2 — arena-#1 realtime, mid-2026).

POST https://api.inworld.ai/tts/v1/voice — Basic auth, JSON response carrying
base64 LINEAR16 audio (WAV-wrapped), decoded + RIFF-header-stripped to raw
s16le 24 kHz mono so the pipeline handles it exactly like Cartesia/Grok.

Structurally identical to CartesiaTTS: per-language voice resolution, parallel
sentence synthesis, 15-minute cooldown on 401/403/429, cross-provider fallback.
Inworld voices are multilingual — one voice speaks any of 15 languages via the
per-turn ``language`` field — so we default to native masculine voices per
language (Josef/Dennis/Diego) but the user can pin one voice across all.

The auth key (``INWORLD_API_KEY``) is ALREADY base64 (a blob of
``workspace-key:secret``) — it is sent verbatim after ``Basic ``, never
re-encoded.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk
from jarvis.core.turn_language import DEFAULT_LOCALE
from jarvis.plugins.tts.cartesia_tts import _detect_lang_from_text
from jarvis.plugins.tts.gemini_flash_tts import SAPI5_SAMPLE_RATE, _sapi5_synthesize

INWORLD_TTS_SAMPLE_RATE = 24_000
INWORLD_TTS_ENDPOINT = "https://api.inworld.ai/tts/v1/voice"
INWORLD_TTS_STREAM_ENDPOINT = "https://api.inworld.ai/tts/v1/voice:stream"
_HTTP_TIMEOUT_S = 30.0
_QUOTA_COOLDOWN_S = 900.0
_MAX_CHARS_PER_REQUEST = 2_000  # Inworld hard limit per request

DEFAULT_MODEL = "inworld-tts-2"

# Native masculine assistant voices per language (Inworld voice catalog,
# mid-2026). Parallel to Cartesia Sebastian/Daniel/Pedro. Every Inworld voice
# is multilingual, so these are defaults, not hard language locks.
DEFAULT_VOICE_DE = "Josef"
DEFAULT_VOICE_EN = "Dennis"
DEFAULT_VOICE_ES = "Diego"

# BCP-47 tags Inworld expects. Jarvis' turn resolver already emits de-DE/en-US/
# es-ES, but a bare "de" is normalized up so the field is always well-formed.
_BCP47 = {"de": "de-DE", "en": "en-US", "es": "es-ES"}

# DE letters are part of the sentence-boundary match set on the next line.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")  # i18n-allow


def strip_wav_header(data: bytes) -> bytes:
    """Return the raw PCM payload of a RIFF/WAVE buffer, or ``data`` unchanged.

    Inworld's non-streaming LINEAR16 response is WAV-wrapped; the pipeline wants
    raw s16le PCM. Locate the ``data`` sub-chunk and return everything after its
    8-byte (id + size) header. Robust to a missing header (already-raw PCM) and
    to a truncated buffer.
    """
    if not data.startswith(b"RIFF") or b"WAVE" not in data[:16]:
        return data
    idx = data.find(b"data")
    if idx == -1 or idx + 8 > len(data):
        return data
    return data[idx + 8:]


def _normalize_language(code: str | None) -> str | None:
    """Normalise a language hint into a BCP-47 tag Inworld accepts, or ``None``.

    ``None``/``auto`` → ``None`` (omit the field → Inworld auto-detects from the
    text). A bare two-letter code or a BCP-47 tag is mapped to the canonical
    de-DE/en-US/es-ES; an unknown tag is passed through untouched.
    """
    if not code:
        return None
    low = code.lower().strip()
    if low in ("auto", "automatic", ""):
        return None
    short = low.split("-", 1)[0]
    return _BCP47.get(short, code)


class _InworldFatalError(RuntimeError):
    """401/403/429 — triggers cooldown + fallback switch."""


class InworldTTS:
    """TTS provider for Inworld (api.inworld.ai/tts/v1/voice).

    Structurally compatible with the ``TTSProvider`` protocol — no inheritance
    from ``jarvis.*`` (entry-point discovery pattern).
    """

    name = "inworld"
    supports_streaming = True  # pseudo via sentence-chunking (true NDJSON = follow-up)

    def __init__(
        self,
        default_voice_de: str = DEFAULT_VOICE_DE,
        default_voice_en: str = DEFAULT_VOICE_EN,
        default_voice_es: str = DEFAULT_VOICE_ES,
        model: str = DEFAULT_MODEL,
        language: str = "auto",
        chunk_by_sentence: bool = True,
        speed: float = 1.0,
        allow_sapi5_fallback: bool = False,
    ) -> None:
        self._model = model or DEFAULT_MODEL
        self._voice_by_lang: dict[str, str] = {
            "de": default_voice_de or DEFAULT_VOICE_DE,
            "en": default_voice_en or DEFAULT_VOICE_EN,
            "es": default_voice_es or DEFAULT_VOICE_ES,
        }
        self._language = language
        _loc = (language or "").lower().split("-", 1)[0]
        self._default_locale = _loc if _loc in self._voice_by_lang else DEFAULT_LOCALE
        self._chunk_by_sentence = chunk_by_sentence
        self._speed = speed
        self._allow_sapi5_fallback = allow_sapi5_fallback
        self._client: Any = None
        self._quota_blocked_until: float = 0.0

    def _resolve_voice(
        self, text: str, voice_override: str | None, language_code: str | None
    ) -> str:
        """Pick the voice best matching the segment language (Cartesia parity):
        explicit override → caller language_code → text heuristic → default
        locale (never a hardcoded English voice on German text)."""
        if voice_override:
            return voice_override
        hint = (language_code or "").lower().strip()
        if hint and hint not in ("auto", "automatic", ""):
            short = hint.split("-", 1)[0]
            if short in self._voice_by_lang:
                return self._voice_by_lang[short]
        detected = _detect_lang_from_text(text)
        if detected and detected in self._voice_by_lang:
            return self._voice_by_lang[detected]
        return self._voice_by_lang.get(self._default_locale, self._voice_by_lang["en"])

    def _resolve_api_key(self) -> str:
        val = cfg.get_secret("inworld_api_key", env_fallback="INWORLD_API_KEY")
        if val:
            return val
        raise RuntimeError(
            "Inworld API key not found. Set INWORLD_API_KEY in the OS keyring "
            "or .env (slot: inworld_api_key). The value is the already-base64 "
            "key string from platform.inworld.ai — do not re-encode it."
        )

    def _ensure_client(self) -> None:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S,
                headers={
                    # Key is already base64 — sent verbatim, never re-encoded.
                    "Authorization": f"Basic {self._resolve_api_key()}",
                    "Content-Type": "application/json",
                },
            )

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        text = text.strip()
        if not text:
            return

        voice_id = self._resolve_voice(text, voice, language_code)
        log = logging.getLogger("jarvis.tts.inworld")

        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        try:
            self._ensure_client()
        except RuntimeError as exc:
            log.warning("Inworld not initialisable (%s) -- falling back.", exc)
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        sentences = _split_sentences(text) if self._chunk_by_sentence else [text]
        if not sentences:
            return

        tasks = [
            asyncio.create_task(self._synthesize_one(s, voice_id, language_code))
            for s in sentences
        ]
        any_success = False
        for i, task in enumerate(tasks):
            try:
                pcm = await task
            except _InworldFatalError as exc:
                self._quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_S
                log.warning(
                    "Inworld quota/auth error (%s) -- fallback for %.0f min.",
                    exc, _QUOTA_COOLDOWN_S / 60,
                )
                for t in tasks[i + 1:]:
                    t.cancel()
                await asyncio.gather(*tasks[i + 1:], return_exceptions=True)
                remainder = " ".join(sentences[i:])
                async for chunk in self._fallback(remainder, language_code):
                    yield chunk
                return

            if pcm:
                any_success = True
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=INWORLD_TTS_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )
            elif self._allow_sapi5_fallback:
                log.warning(
                    "Inworld empty for sentence %d/%d -- SAPI5 emergency on.",
                    i + 1, len(tasks),
                )
                fallback_pcm = await asyncio.to_thread(
                    _sapi5_synthesize, sentences[i], language_code or "de-DE"
                )
                if fallback_pcm:
                    yield AudioChunk(
                        pcm=fallback_pcm,
                        sample_rate=SAPI5_SAMPLE_RATE,
                        timestamp_ns=0,
                        channels=1,
                    )
            else:
                log.error(
                    "Inworld returned no audio for sentence %d/%d (%r). "
                    "SAPI5 emergency disabled -- staying silent for this segment.",
                    i + 1, len(tasks), sentences[i][:80],
                )

        if not any_success:
            log.error("Inworld produced no audio at all -- cross-provider fallback.")
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        if language:
            short = language.lower().split("-", 1)[0]
            if short in self._voice_by_lang:
                return [self._voice_by_lang[short]]
        seen: list[str] = []
        for v in (
            self._voice_by_lang.get("de"),
            self._voice_by_lang.get("en"),
            self._voice_by_lang.get("es"),
        ):
            if v and v not in seen:
                seen.append(v)
        return seen

    async def _synthesize_one(
        self, text: str, voice_id: str, language_code: str | None
    ) -> bytes:
        log = logging.getLogger("jarvis.tts.inworld")
        text = text[:_MAX_CHARS_PER_REQUEST]

        audio_config: dict[str, Any] = {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": INWORLD_TTS_SAMPLE_RATE,
        }
        if self._speed != 1.0:
            audio_config["speakingRate"] = self._speed
        payload: dict[str, Any] = {
            "text": text,
            "voiceId": voice_id,
            "modelId": self._model,
            "audioConfig": audio_config,
        }
        lang = _normalize_language(language_code or self._language)
        if lang is not None:
            payload["language"] = lang

        assert self._client is not None
        try:
            resp = await self._client.post(INWORLD_TTS_ENDPOINT, json=payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("Inworld HTTP error (%s) -- soft-fail.", exc.__class__.__name__)
            return b""

        if resp.status_code in (401, 403, 429):
            raise _InworldFatalError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            body = resp.text[:200] if resp.text else "<empty>"
            log.warning(
                "Inworld HTTP %d -- voice=%s text=%r body=%s",
                resp.status_code, voice_id, text[:80], body,
            )
            return b""

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("Inworld 200 but non-JSON body (%s).", exc.__class__.__name__)
            return b""
        b64 = data.get("audioContent") or ""
        if not b64:
            log.warning("Inworld 200 OK but empty audioContent -- voice=%s", voice_id)
            return b""
        try:
            raw = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            log.warning("Inworld audioContent not base64 (%s).", exc.__class__.__name__)
            return b""
        return strip_wav_header(raw)

    async def _fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[AudioChunk]:
        log = logging.getLogger("jarvis.tts.inworld")
        try:
            from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

            gemini = GeminiFlashTTS(
                language_code=language_code or "de-DE",
                allow_sapi5_fallback=self._allow_sapi5_fallback,
            )
            async for chunk in gemini.synthesize(text, language_code=language_code):
                yield chunk
            return
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Gemini fallback after Inworld error also failed (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Both Inworld and Gemini TTS produced no audio. SAPI5 emergency "
                "disabled -- staying silent. Set tts.allow_sapi5_fallback=true "
                "if Windows TTS is an acceptable last resort."
            )
            return

        log.warning("SAPI5 emergency active (config opt-in).")
        pcm = await asyncio.to_thread(_sapi5_synthesize, text, language_code or "de-DE")
        if pcm:
            yield AudioChunk(
                pcm=pcm, sample_rate=SAPI5_SAMPLE_RATE, timestamp_ns=0, channels=1
            )


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]
