"""xAI Grok Voice TTS Plugin (Launch April 2026, "Think Fast 1.0").

Nutzt den Unary-Endpoint `POST https://api.x.ai/v1/tts` mit Bearer-Auth.
Wir requesten **rohes PCM 24 kHz mono int16** — identisch zum Gemini-/
ElevenLabs-Pfad, damit der `sounddevice`-Playback nichts anpassen muss
und kein MP3-Decoder noetig ist.

Stimmen-Whitelist:
  - leo  — authoritativ, kommandoartig (JARVIS-Default, Butler-Pendant
           zu ElevenLabs "Daniel" / Gemini "Charon")
  - rex  — Business-Confident
  - sal  — neutral, balanced
  - ara  — warm, freundlich
  - eve  — energisch (xAI-Default)

Streaming:
  Echtes WebSocket-Streaming gibt es bei xAI (`wss://api.x.ai/v1/tts`),
  fuer Jarvis reicht Pseudo-Streaming via Satz-Chunking — analog zu
  GeminiFlashTTS: alle Saetze parallel in Flight, in Original-Reihenfolge
  yielden. Erste-Chunk-Latenz dominiert, Saetze 2..N sind synthetisiert
  bevor Satz 1 zu Ende gespielt ist.

Fallback-Kette bei Fehlern (Quota / Auth / Netzwerk):
  Grok-Voice  →  Gemini-TTS  →  SAPI5 (Windows native, Quota-frei)
Die Gemini-/SAPI5-Helpers werden aus `gemini_flash_tts` wiederverwendet.
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

# xAI liefert PCM in 16-bit signed little-endian mono — gleiches Format
# wie Gemini-TTS (`audio/l16; rate=24000`) und ElevenLabs (`pcm_24000`).
GROK_TTS_SAMPLE_RATE = 24_000
GROK_TTS_ENDPOINT = "https://api.x.ai/v1/tts"
_HTTP_TIMEOUT_S = 30.0
_QUOTA_COOLDOWN_S = 900.0

# xAI-Limit: max 15 000 Zeichen pro Unary-Request. Wir splitten ohnehin
# satzweise, ein einzelner Satz erreicht das Limit praktisch nie.
_MAX_CHARS_PER_REQUEST = 15_000

# 5 Voices laut Launch-Blog (April 2026). JARVIS-Default = "leo"
# (authoritativ, kommandoartig — passt zum Butler-Pattern).
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

# Satz-Splitter: identisch zu GeminiFlashTTS — DE+EN-Capital-Lookahead.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")


class GrokVoiceTTS:
    """TTS-Provider fuer xAI Grok Voice (api.x.ai/v1/tts).

    Strukturell kompatibel zum `TTSProvider`-Protocol — kein Vererben aus
    `jarvis.*` noetig (entry_point-Discovery-Pattern).
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
        # Voice-Mismatch-Schutz: ein vom Gemini-Profil uebernommener Voice-
        # Name wie "Charon" wuerde xAI mit HTTP 400 quittieren. Wir weisen
        # auf den Default zurueck und loggen den Override sichtbar.
        if default_voice not in DEFAULT_VOICES:
            logging.getLogger("jarvis.tts.grok-voice").warning(
                "Voice %r ist kein Grok-Voice (erwartet: %s). Nutze Default %r.",
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
        """Single-Key-Policy: xAI nutzt einen Token fuer Brain + Voice.

        Reihenfolge:
          1. `xai_api_key` / ENV `XAI_API_KEY` (xAI-Doku-Default)
          2. `grok_api_key` / ENV `GROK_API_KEY` (Jarvis-Wizard-Slot,
             gleichzeitig vom Grok-Brain-Plugin genutzt)
        """
        for key, env in (
            ("xai_api_key", "XAI_API_KEY"),
            ("grok_api_key", "GROK_API_KEY"),
        ):
            val = cfg.get_secret(key, env_fallback=env)
            if val:
                return val
        raise RuntimeError(
            "xAI-API-Key nicht gefunden. Setze GROK_API_KEY oder XAI_API_KEY "
            "im Windows Credential Manager oder in der .env."
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
        """Schliesst den HTTP-Client. Idempotent."""
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
        """Synthetisiert Audio, yielded AudioChunks satz-weise.

        Multilingual: `language="auto"` ist xAI-Default und detektiert die
        Sprache aus dem Text. Wer hart pinnen will, gibt `language_code`
        als BCP-47 mit (`de-DE`, `en-US`); wir reichen das normalisiert
        an die API durch.
        """
        text = text.strip()
        if not text:
            return

        voice = voice or self._default_voice
        log = logging.getLogger("jarvis.tts.grok-voice")

        # Cooldown aktiv? Erst Gemini, dann SAPI5 — niemals stumm bleiben.
        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        try:
            self._ensure_client()
        except RuntimeError as exc:
            log.warning("Grok-Voice nicht initialisierbar (%s) — Fallback.", exc)
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        sentences = (
            _split_sentences(text) if self._chunk_by_sentence else [text]
        )
        if not sentences:
            return

        # Alle Saetze parallel in Flight, Original-Reihenfolge yielden.
        tasks = [
            asyncio.create_task(self._synthesize_one(s, voice, language_code))
            for s in sentences
        ]
        any_success = False
        for i, task in enumerate(tasks):
            try:
                pcm = await task
            except _GrokFatalError as exc:
                # 401/403/429 → Cooldown setzen, restliche Tasks abbrechen,
                # Fallback fuer den Rest des Textes.
                self._quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_S
                log.warning(
                    "Grok-Voice Quota/Auth-Fehler (%s) — Fallback fuer %.0f min.",
                    exc, _QUOTA_COOLDOWN_S / 60,
                )
                for t in tasks[i + 1 :]:
                    t.cancel()
                await asyncio.gather(*tasks[i + 1 :], return_exceptions=True)
                # Restlichen Text via Fallback-Chain ausspielen.
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
                # Einzelner Satz leer und Notbremse erlaubt: SAPI5 fuer genau
                # diesen Satz, damit der Flow nicht abreisst.
                log.warning(
                    "Grok-Voice leer fuer Satz %d/%d — SAPI5-Notbremse aktiv.",
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
                    "Grok-Voice lieferte kein Audio fuer Satz %d/%d (%r) — "
                    "SAPI5-Fallback deaktiviert (tts.allow_sapi5_fallback=false). "
                    "Audio fuer diesen Satz bleibt stumm.",
                    i + 1, len(tasks), sentences[i][:80],
                )

        if not any_success:
            log.error(
                "Grok-Voice lieferte keinerlei Audio fuer den gesamten Text. "
                "Versuche Cross-Provider-Fallback.",
            )
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        """5 Whitelisted Voices, alle multilingual (20+ Sprachen)."""
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
        """Ein Unary-POST an /v1/tts. Returnt rohes PCM oder b"" bei Soft-Error.

        Raised `_GrokFatalError` bei 401/403/429, damit der Caller den
        Cooldown setzen und auf Fallback umschalten kann.
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
            # `speed` ist nicht offiziell dokumentiert (Stand 2026-04-25),
            # aber inline-Speed-Tags werden vom Modell akzeptiert. Wir
            # legen das Feld trotzdem mit, falls die API es spaeter ergaenzt.
            payload["speed"] = self._speed

        assert self._client is not None
        try:
            resp = await self._client.post(GROK_TTS_ENDPOINT, json=payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("Grok-Voice HTTP-Fehler (%s) — Soft-Fail.", exc.__class__.__name__)
            return b""

        if resp.status_code in (401, 403, 429):
            raise _GrokFatalError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # 400/500 — meist transient oder Schema-Mismatch. Loggen, b""
            # zurueck, der Caller faellt auf SAPI5 zurueck. Body nur erste
            # 200 Zeichen mitloggen, oft JSON-Error.
            body = resp.text[:200] if resp.text else "<empty>"
            log.warning(
                "Grok-Voice HTTP %d — voice=%s text=%r body=%s",
                resp.status_code, voice, text[:80], body,
            )
            return b""

        data = resp.content
        if not data:
            log.warning("Grok-Voice 200 OK aber leerer Body — voice=%s", voice)
            return b""
        return data

    async def _fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[AudioChunk]:
        """Cross-Provider-Fallback Gemini-TTS → optional SAPI5.

        Stage 1 (Gemini) bleibt aktiv: ein anderer Cloud-TTS klingt besser
        als Windows-Roboter. Stage 2 (SAPI5) ist nur aktiv wenn der User
        ``tts.allow_sapi5_fallback = true`` gesetzt hat.
        """
        log = logging.getLogger("jarvis.tts.grok-voice")

        # Stage 1: Gemini probieren (separate Quota).
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
                "Gemini-Fallback nach Grok-Fehler ebenfalls gescheitert (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Sowohl Grok als auch Gemini-TTS lieferten kein Audio. "
                "SAPI5-Notbremse per Config deaktiviert — bleibe stumm. "
                "Setze tts.allow_sapi5_fallback=true wenn Du Windows-TTS "
                "als Notausgang erlaubst.",
            )
            return

        # Stage 2: SAPI5 (Windows native, Quota-frei) — nur bei Opt-in.
        log.warning("SAPI5-Notbremse aktiv (Config-Opt-in).")
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
    """Auth/Quota-Fehler — triggert Cooldown + Fallback-Switch."""


def _split_sentences(text: str) -> list[str]:
    """Heuristischer Satz-Splitter (DE+EN). Identisch zu GeminiFlashTTS."""
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


# xAI-Sprachen laut Doku: auto, en, ar-EG, ar-SA, ar-AE, bn, zh, fr, de,
# hi, id, it, ja, ko, pt-BR, pt-PT, ru, es-MX, es-ES, tr, vi.
# Sprachen mit Sub-Tag werden 1:1 durchgereicht; alles andere auf den
# Primary-Tag verkuerzt (`de-DE` → `de`).
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
