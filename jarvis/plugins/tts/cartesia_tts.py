"""Cartesia.ai Sonic TTS Plugin (Sonic 3.5, 42 languages incl. German).

POST https://api.cartesia.ai/tts/bytes — Bearer auth, raw PCM s16le 24 kHz mono.
Structurally identical to GrokVoiceTTS: parallel sentence synthesis, fallback
chain Cartesia -> Gemini Flash TTS -> optional SAPI5, 15-minute cooldown on
401/403/429 quota/auth errors.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk
from jarvis.core.turn_language import DEFAULT_LOCALE
from jarvis.plugins.tts.gemini_flash_tts import (
    SAPI5_SAMPLE_RATE,
    _sapi5_synthesize,
)

CARTESIA_TTS_SAMPLE_RATE = 24_000
CARTESIA_TTS_ENDPOINT = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-03-01"
_HTTP_TIMEOUT_S = 30.0
_QUOTA_COOLDOWN_S = 900.0
_MAX_CHARS_PER_REQUEST = 8_000

DEFAULT_MODEL_ID = "sonic-3.5"

# Per-language native voices chosen for the JARVIS butler pattern
# (masculine, authoritative, calm — parallel to ElevenLabs Daniel, Gemini
# Charon, Grok leo). Source: Cartesia voice library (api.cartesia.ai/voices,
# fetched 2026-05-27, filtered by language). User can override any of these
# via [tts.cartesia].voice_id_<lang> in jarvis.toml; UUIDs from
# https://play.cartesia.ai/voices.
DEFAULT_VOICE_ID_DE = "b7187e84-fe22-4344-ba4a-bc013fcb533e"  # Sebastian — Orator (DE)
DEFAULT_VOICE_ID_EN = "47c38ca4-5f35-497b-b1a3-415245fb35e1"  # Daniel — Modern Assistant (EN)
DEFAULT_VOICE_ID_ES = "15d0c2e2-8d29-44c3-be23-d585d5f154a1"  # Pedro — Formal Speaker (ES)

# Generic fallback when no language is known. Defaults to the English voice
# so a missing language hint behaves like the previous single-voice plugin.
DEFAULT_VOICE_ID = DEFAULT_VOICE_ID_EN

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")  # i18n-allow (DE letters are part of the sentence-boundary match set)

# Text-detection heuristic: when the caller does not pass a language_code,
# we look at the transcript to pick the right voice. Cheap, no LLM call,
# no library dependency. False positives fall back to the generic voice.
_DE_HINTS = re.compile(r"[äöüÄÖÜß]|\b(ich|nicht|und|der|die|das|ist|mit|für|werde|machen|gerne|bitte)\b", re.IGNORECASE)  # i18n-allow (DE word list a language-detection classifier must match)
_ES_HINTS = re.compile(r"[ñáéíóúÑÁÉÍÓÚ¿¡]|\b(que|para|con|por|esto|esta|hola|gracias|señor|cómo)\b", re.IGNORECASE)


def _detect_lang_from_text(text: str) -> str | None:
    """Return 'de', 'es' or None (unknown — caller falls back to default)."""
    if _DE_HINTS.search(text):
        return "de"
    if _ES_HINTS.search(text):
        return "es"
    return None


class _CartesiaFatalError(RuntimeError):
    """401/403/429 — triggers cooldown + fallback switch."""


class CartesiaTTS:
    """TTS provider for Cartesia Sonic (api.cartesia.ai/tts/bytes).

    Structurally compatible with the ``TTSProvider`` protocol — no
    inheritance from ``jarvis.*`` (entry_point-discovery pattern).
    """

    name = "cartesia"
    supports_streaming = True  # pseudo via sentence-chunking

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        voice_id: str = DEFAULT_VOICE_ID,
        voice_id_de: str | None = None,
        voice_id_en: str | None = None,
        voice_id_es: str | None = None,
        language: str = "auto",
        chunk_by_sentence: bool = True,
        speed: float = 1.0,
        allow_sapi5_fallback: bool = False,
    ) -> None:
        if not voice_id:
            raise ValueError(
                "CartesiaTTS requires a voice_id. Set [tts.cartesia].voice_id "
                "in jarvis.toml (find UUIDs at https://play.cartesia.ai/voices)."
            )
        self._model_id = model_id
        self._voice_id = voice_id
        # Per-language voice map. Falls back to the generic voice_id when a
        # given language has no dedicated UUID configured.
        self._voice_by_lang: dict[str, str] = {
            "de": voice_id_de or DEFAULT_VOICE_ID_DE,
            "en": voice_id_en or DEFAULT_VOICE_ID_EN,
            "es": voice_id_es or DEFAULT_VOICE_ID_ES,
        }
        self._language = language
        # Voice used when neither a per-call language_code nor the text sniff
        # resolves a language. A configured concrete language pins it; else the
        # doctrine default locale — NEVER a hardcoded English voice on German
        # text (the British-accent symptom; forensic 2026-06-23).
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
        """Pick the voice UUID that best matches the language of this segment.

        Priority:
          1. Explicit ``voice`` override from the caller (Pipeline can pass
             a per-utterance voice).
          2. ``language_code`` from the caller (matches the Pipeline's
             ``voice_auto_switch`` per-utterance detection or the user's
             reply-language pin).
          3. Text-detection heuristic on the transcript (cheap, regex-based,
             handles the case where the caller passes 'auto' or nothing).
          4. The generic ``voice_id`` fallback.
        """
        if voice_override:
            return voice_override
        hint = (language_code or "").lower().strip()
        if hint and hint not in ("auto", "automatic", ""):
            short = hint.split("-", 1)[0]
            if short in self._voice_by_lang:
                return self._voice_by_lang[short]
        # Caller did not pin a language — sniff the text.
        detected = _detect_lang_from_text(text)
        if detected and detected in self._voice_by_lang:
            return self._voice_by_lang[detected]
        # No language could be resolved — follow the configured default_locale,
        # never a hardcoded English voice on German text (British-accent
        # symptom). default_locale is "en" only when configured/auto.
        return self._voice_by_lang.get(self._default_locale, self._voice_id)

    def _resolve_api_key(self) -> str:
        val = cfg.get_secret("cartesia_api_key", env_fallback="CARTESIA_API_KEY")
        if val:
            return val
        raise RuntimeError(
            "Cartesia API key not found. Set CARTESIA_API_KEY in Windows "
            "Credential Manager or .env (slot: cartesia_api_key)."
        )

    def _ensure_client(self) -> None:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S,
                headers={
                    "Authorization": f"Bearer {self._resolve_api_key()}",
                    "Cartesia-Version": CARTESIA_VERSION,
                    "Content-Type": "application/json",
                    "Accept": "application/octet-stream",
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
        log = logging.getLogger("jarvis.tts.cartesia")

        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        try:
            self._ensure_client()
        except RuntimeError as exc:
            log.warning("Cartesia not initialisable (%s) -- falling back.", exc)
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        sentences = (
            _split_sentences(text) if self._chunk_by_sentence else [text]
        )
        if not sentences:
            return

        tasks = [
            asyncio.create_task(
                self._synthesize_one(s, voice_id, language_code)
            )
            for s in sentences
        ]
        any_success = False
        for i, task in enumerate(tasks):
            try:
                pcm = await task
            except _CartesiaFatalError as exc:
                self._quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_S
                log.warning(
                    "Cartesia quota/auth error (%s) -- fallback for %.0f min.",
                    exc, _QUOTA_COOLDOWN_S / 60,
                )
                for t in tasks[i + 1 :]:
                    t.cancel()
                await asyncio.gather(*tasks[i + 1 :], return_exceptions=True)
                remainder = " ".join(sentences[i:])
                async for chunk in self._fallback(remainder, language_code):
                    yield chunk
                return

            if pcm:
                any_success = True
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=CARTESIA_TTS_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )
            elif self._allow_sapi5_fallback:
                log.warning(
                    "Cartesia empty for sentence %d/%d -- SAPI5 emergency on.",
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
                    "Cartesia returned no audio for sentence %d/%d (%r). "
                    "SAPI5 emergency disabled -- staying silent for this segment.",
                    i + 1, len(tasks), sentences[i][:80],
                )

        if not any_success:
            log.error(
                "Cartesia produced no audio at all -- cross-provider fallback."
            )
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        if language:
            short = language.lower().split("-", 1)[0]
            if short in self._voice_by_lang:
                return [self._voice_by_lang[short]]
        # No filter -> the full per-language map plus the generic fallback.
        seen: list[str] = []
        for v in (
            self._voice_by_lang.get("de"),
            self._voice_by_lang.get("en"),
            self._voice_by_lang.get("es"),
            self._voice_id,
        ):
            if v and v not in seen:
                seen.append(v)
        return seen

    async def _synthesize_one(
        self,
        text: str,
        voice_id: str,
        language_code: str | None,
    ) -> bytes:
        log = logging.getLogger("jarvis.tts.cartesia")
        text = text[:_MAX_CHARS_PER_REQUEST]

        payload: dict[str, Any] = {
            "model_id": self._model_id,
            "transcript": text,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": CARTESIA_TTS_SAMPLE_RATE,
            },
        }
        # Cartesia accepts ISO-639-1 codes only (no "auto"). Omitting the
        # field lets Cartesia auto-detect from the transcript — required for
        # bilingual DE+EN usage. Live-evidence 2026-05-27 12:56:41:
        # HTTP 400 error_code=language_not_supported when 'auto' was sent.
        lang = _normalize_language(language_code or self._language)
        if lang is not None:
            payload["language"] = lang
        if self._speed != 1.0:
            payload["generation_config"] = {"speed": self._speed}

        assert self._client is not None
        try:
            resp = await self._client.post(CARTESIA_TTS_ENDPOINT, json=payload)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Cartesia HTTP error (%s) -- soft-fail.", exc.__class__.__name__
            )
            return b""

        if resp.status_code in (401, 403, 429):
            raise _CartesiaFatalError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            body = resp.text[:200] if resp.text else "<empty>"
            log.warning(
                "Cartesia HTTP %d -- voice=%s text=%r body=%s",
                resp.status_code, voice_id, text[:80], body,
            )
            return b""

        data = resp.content
        if not data:
            log.warning("Cartesia 200 OK but empty body -- voice=%s", voice_id)
            return b""
        return data

    async def _fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[AudioChunk]:
        log = logging.getLogger("jarvis.tts.cartesia")
        try:
            from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

            gemini = GeminiFlashTTS(
                language_code=language_code or "de-DE",
                allow_sapi5_fallback=self._allow_sapi5_fallback,
            )
            async for chunk in gemini.synthesize(
                text, language_code=language_code
            ):
                yield chunk
            return
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Gemini fallback after Cartesia error also failed (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Both Cartesia and Gemini TTS produced no audio. "
                "SAPI5 emergency disabled -- staying silent. "
                "Set tts.allow_sapi5_fallback=true if Windows TTS is an "
                "acceptable last resort."
            )
            return

        log.warning("SAPI5 emergency active (config opt-in).")
        pcm = await asyncio.to_thread(
            _sapi5_synthesize, text, language_code or "de-DE"
        )
        if pcm:
            yield AudioChunk(
                pcm=pcm,
                sample_rate=SAPI5_SAMPLE_RATE,
                timestamp_ns=0,
                channels=1,
            )


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def _normalize_language(code: str | None) -> str | None:
    """Normalise a language hint into something Cartesia accepts.

    Cartesia's /tts/bytes endpoint accepts ISO-639-1 codes ('de', 'en', ...)
    only. The string 'auto' is rejected with HTTP 400
    ('language_not_supported', live-evidence 2026-05-27 jarvis_desktop.log
    at 12:56:41). Returning ``None`` here signals the caller to omit the
    ``language`` field entirely, which triggers Cartesia's own auto-detection
    — the right default for bilingual DE+EN voice traffic.
    """
    if not code:
        return None
    low = code.lower().strip()
    if low in ("auto", "automatic", ""):
        return None
    return low.split("-", 1)[0]
