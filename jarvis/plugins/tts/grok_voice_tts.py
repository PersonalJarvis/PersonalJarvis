"""xAI Grok Voice TTS Plugin (Launch April 2026, "Think Fast 1.0").

Uses the unary endpoint `POST https://api.x.ai/v1/tts` with bearer auth.
We request **raw PCM 24 kHz mono int16** — identical to the Gemini /
ElevenLabs path, so the `sounddevice` playback needs no adjustment
and no MP3 decoder is needed.

Voice whitelist:
  - leo  — authoritative, command-like (JARVIS default, butler counterpart
           to ElevenLabs "Daniel" / Gemini "Charon")
  - rex  — business-confident
  - sal  — neutral, balanced
  - ara  — warm, friendly
  - eve  — energetic (xAI default)

Streaming:
  Real WebSocket streaming exists at xAI (`wss://api.x.ai/v1/tts`), but for
  Jarvis pseudo-streaming via sentence chunking is enough — analogous to
  GeminiFlashTTS: all sentences in flight in parallel, yielded in original
  order. First-chunk latency dominates; sentences 2..N are already
  synthesized before sentence 1 finishes playing.

Fallback chain on errors (quota / auth / network):
  Grok Voice  →  Gemini TTS  →  SAPI5 (Windows native, no quota)
The Gemini / SAPI5 helpers are reused from `gemini_flash_tts`.
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
from jarvis.plugins.tts.gemini_flash_tts import (
    SAPI5_SAMPLE_RATE,
    _sapi5_synthesize,
)

# xAI delivers PCM as 16-bit signed little-endian mono — the same format
# as Gemini TTS (`audio/l16; rate=24000`) and ElevenLabs (`pcm_24000`).
GROK_TTS_SAMPLE_RATE = 24_000
GROK_TTS_ENDPOINT = "https://api.x.ai/v1/tts"
_HTTP_TIMEOUT_S = 30.0
_QUOTA_COOLDOWN_S = 900.0

# xAI limit: max 15,000 characters per unary request. We split by sentence
# anyway, so a single sentence practically never reaches the limit.
_MAX_CHARS_PER_REQUEST = 15_000

# 5 voices per the launch blog (April 2026). JARVIS default = "leo"
# (authoritative, command-like — fits the butler pattern).
GROK_VOICE_LEO = "leo"
GROK_VOICE_REX = "rex"
GROK_VOICE_SAL = "sal"
GROK_VOICE_ARA = "ara"
GROK_VOICE_EVE = "eve"

DEFAULT_VOICES: tuple[str, ...] = (
    GROK_VOICE_LEO,
    GROK_VOICE_REX,
    GROK_VOICE_SAL,
    GROK_VOICE_ARA,
    GROK_VOICE_EVE,
)

# Sentence splitter: identical to GeminiFlashTTS — DE+EN capital lookahead.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")


class GrokVoiceTTS:
    """TTS provider for xAI Grok Voice (api.x.ai/v1/tts).

    Structurally compatible with the `TTSProvider` protocol — no need to
    inherit from `jarvis.*` (entry_point discovery pattern).
    """

    name = "grok-voice"
    supports_streaming = True  # pseudo via sentence-chunking

    def __init__(
        self,
        default_voice: str = GROK_VOICE_LEO,
        language: str = "auto",
        chunk_by_sentence: bool = True,
        speed: float = 1.0,
        text_normalization: bool = True,
        optimize_streaming_latency: int = 1,
        allow_sapi5_fallback: bool = False,
    ) -> None:
        # Voice-mismatch protection: a voice name like "Charon" carried over
        # from the Gemini profile would make xAI respond with HTTP 400. We
        # fall back to the default and log the override visibly.
        if default_voice not in DEFAULT_VOICES:
            logging.getLogger("jarvis.tts.grok-voice").warning(
                "Voice %r is not a Grok voice (expected: %s). Using default %r.",
                default_voice, ", ".join(DEFAULT_VOICES), GROK_VOICE_LEO,
            )
            default_voice = GROK_VOICE_LEO
        self._default_voice = default_voice
        self._language = language
        self._chunk_by_sentence = chunk_by_sentence
        self._speed = speed
        self._text_normalization = text_normalization
        self._optimize_streaming_latency = optimize_streaming_latency
        self._allow_sapi5_fallback = allow_sapi5_fallback
        self._client: Any = None  # httpx.AsyncClient, lazy
        self._quota_blocked_until: float = 0.0

    # ------------------------------------------------------------------
    # Auth + Client-Setup
    # ------------------------------------------------------------------

    def _resolve_api_key(self) -> str:
        """Single-key policy: xAI uses one token for brain + voice.

        Order:
          1. `xai_api_key` / ENV `XAI_API_KEY` (xAI doc default)
          2. `grok_api_key` / ENV `GROK_API_KEY` (Jarvis wizard slot,
             also used by the Grok brain plugin)
        """
        for key, env in (
            ("xai_api_key", "XAI_API_KEY"),
            ("grok_api_key", "GROK_API_KEY"),
        ):
            val = cfg.get_secret(key, env_fallback=env)
            if val:
                return val
        raise RuntimeError(
            "xAI API key not found. Set GROK_API_KEY or XAI_API_KEY "
            "in the Windows Credential Manager or in .env."
        )

    def _ensure_client(self) -> None:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S,
                headers={
                    "Authorization": f"Bearer {self._resolve_api_key()}",
                    "Content-Type": "application/json",
                    "Accept": "application/octet-stream",
                },
            )

    async def aclose(self) -> None:
        """Closes the HTTP client. Idempotent."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    # ------------------------------------------------------------------
    # Protocol-API
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Synthesizes audio, yielding AudioChunks sentence by sentence.

        Multilingual: `language="auto"` is the xAI default and detects the
        language from the text. Anyone wanting a hard pin passes `language_code`
        as BCP-47 (`de-DE`, `en-US`); we pass it through normalized
        to the API.
        """
        text = text.strip()
        if not text:
            return

        voice = voice or self._default_voice
        log = logging.getLogger("jarvis.tts.grok-voice")

        # Cooldown active? First Gemini, then SAPI5 — never stay silent.
        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        try:
            self._ensure_client()
        except RuntimeError as exc:
            log.warning("Grok Voice could not be initialized (%s) — falling back.", exc)
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        sentences = (
            _split_sentences(text) if self._chunk_by_sentence else [text]
        )
        if not sentences:
            return

        # All sentences in flight in parallel, yielded in original order.
        tasks = [
            asyncio.create_task(self._synthesize_one(s, voice, language_code))
            for s in sentences
        ]
        any_success = False
        for i, task in enumerate(tasks):
            try:
                pcm = await task
            except _GrokFatalError as exc:
                # 401/403/429 → arm the cooldown, cancel remaining tasks,
                # fall back for the rest of the text.
                self._quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_S
                log.warning(
                    "Grok Voice quota/auth error (%s) — falling back for %.0f min.",
                    exc, _QUOTA_COOLDOWN_S / 60,
                )
                for t in tasks[i + 1 :]:
                    t.cancel()
                await asyncio.gather(*tasks[i + 1 :], return_exceptions=True)
                # Play out the remaining text via the fallback chain.
                remainder = " ".join(sentences[i:])
                async for chunk in self._fallback(remainder, language_code):
                    yield chunk
                return

            if pcm:
                any_success = True
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=GROK_TTS_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )
            elif self._allow_sapi5_fallback:
                # Single sentence empty and the emergency brake is allowed:
                # SAPI5 for exactly this sentence, so the flow doesn't break.
                log.warning(
                    "Grok Voice empty for sentence %d/%d — SAPI5 emergency brake active.",
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
                    "Grok Voice returned no audio for sentence %d/%d (%r) — "
                    "SAPI5 fallback disabled (tts.allow_sapi5_fallback=false). "
                    "Audio stays silent for this sentence.",
                    i + 1, len(tasks), sentences[i][:80],
                )

        if not any_success:
            log.error(
                "Grok Voice returned no audio at all for the whole text. "
                "Trying cross-provider fallback.",
            )
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        """5 whitelisted voices, all multilingual (20+ languages)."""
        return list(DEFAULT_VOICES)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _synthesize_one(
        self,
        text: str,
        voice: str,
        language_code: str | None,
    ) -> bytes:
        """A single unary POST to /v1/tts. Returns raw PCM or b"" on a soft error.

        Raises `_GrokFatalError` on 401/403/429, so the caller can arm the
        cooldown and switch to the fallback.
        """
        log = logging.getLogger("jarvis.tts.grok-voice")
        text = text[:_MAX_CHARS_PER_REQUEST]

        payload: dict[str, Any] = {
            "text": text,
            "voice_id": voice,
            "language": _normalize_language(language_code or self._language),
            "output_format": {
                "codec": "pcm",
                "sample_rate": GROK_TTS_SAMPLE_RATE,
            },
            "optimize_streaming_latency": self._optimize_streaming_latency,
            "text_normalization": self._text_normalization,
        }
        if self._speed != 1.0:
            # `speed` is not officially documented (as of 2026-04-25),
            # but inline speed tags are accepted by the model. We
            # include the field anyway in case the API adds it later.
            payload["speed"] = self._speed

        assert self._client is not None
        try:
            resp = await self._client.post(GROK_TTS_ENDPOINT, json=payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("Grok Voice HTTP error (%s) — soft fail.", exc.__class__.__name__)
            return b""

        if resp.status_code in (401, 403, 429):
            raise _GrokFatalError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # 400/500 — usually transient or a schema mismatch. Log it, return
            # b"" so the caller falls back to SAPI5. Only log the first
            # 200 characters of the body, often a JSON error.
            body = resp.text[:200] if resp.text else "<empty>"
            log.warning(
                "Grok Voice HTTP %d — voice=%s text=%r body=%s",
                resp.status_code, voice, text[:80], body,
            )
            return b""

        data = resp.content
        if not data:
            log.warning("Grok Voice 200 OK but empty body — voice=%s", voice)
            return b""
        return data

    async def _fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[AudioChunk]:
        """Cross-provider fallback Gemini TTS → optional SAPI5.

        Stage 1 (Gemini) stays active: another cloud TTS sounds better
        than the Windows robot. Stage 2 (SAPI5) is only active when the user
        has set ``tts.allow_sapi5_fallback = true``.
        """
        log = logging.getLogger("jarvis.tts.grok-voice")

        # Stage 1: try Gemini (separate quota).
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
                "Gemini fallback after the Grok error also failed (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Neither Grok nor Gemini TTS returned any audio. "
                "SAPI5 emergency brake disabled via config — staying silent. "
                "Set tts.allow_sapi5_fallback=true if you want Windows TTS "
                "as an emergency exit.",
            )
            return

        # Stage 2: SAPI5 (Windows native, no quota) — opt-in only.
        log.warning("SAPI5 emergency brake active (config opt-in).")
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


# ---------------------------------------------------------------------- #
# Helpers                                                                 #
# ---------------------------------------------------------------------- #


class _GrokFatalError(RuntimeError):
    """Auth/quota error — triggers a cooldown + fallback switch."""


def _split_sentences(text: str) -> list[str]:
    """Heuristic sentence splitter (DE+EN). Identical to GeminiFlashTTS."""
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


# xAI languages per the docs: auto, en, ar-EG, ar-SA, ar-AE, bn, zh, fr, de,
# hi, id, it, ja, ko, pt-BR, pt-PT, ru, es-MX, es-ES, tr, vi.
# Languages with a sub-tag are passed through 1:1; everything else is
# shortened to the primary tag (`de-DE` → `de`).
_LANGS_WITH_SUBTAG = frozenset({
    "ar-eg", "ar-sa", "ar-ae", "pt-br", "pt-pt", "es-mx", "es-es",
})


def _normalize_language(code: str | None) -> str:
    if not code:
        return "auto"
    low = code.lower().strip()
    if low in ("auto", "automatic", ""):
        return "auto"
    if low in _LANGS_WITH_SUBTAG:
        return low
    # `de-DE` → `de`, `en-US` → `en`, `en` → `en`, `zh-CN` → `zh`
    return low.split("-", 1)[0]
