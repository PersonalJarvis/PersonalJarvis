"""ElevenLabs TTS Plugin — Multi-Language DE+EN via eleven_flash_v2_5.

Nutzt das offizielle `elevenlabs`-SDK. Output-Format ist `pcm_24000`
(Raw Linear-PCM, 24 kHz mono, kein Decoder noetig) — identisch zum
Gemini-Pfad, damit der Playback-Layer (`sounddevice`) nichts anpassen
muss.

Echtes Streaming via `client.text_to_speech.convert_as_stream(...)`:
das SDK liefert beim Eintreffen einzelner Audio-Chunks sofort Bytes.
Weil das SDK synchron ist, laeuft der Producer in einem Worker-Thread
und reicht Chunks ueber eine `asyncio.Queue` an den async-Consumer.

Fallback-Kette bei Fehlern (Quota / Auth / Netzwerk):
  ElevenLabs  →  Gemini-TTS  →  SAPI5 (Windows native, Quota-frei)
Die Gemini-/SAPI5-Helpers werden aus `gemini_flash_tts` importiert, um
die Implementierung klein zu halten.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk

# SAPI5-Emergency-Fallback wiederverwenden (Windows-native, Quota-frei).
from jarvis.plugins.tts.gemini_flash_tts import (
    SAPI5_SAMPLE_RATE,
    _sapi5_synthesize,
)

# Output-Format wie Gemini: 24 kHz mono int16 PCM (pcm_24000).
ELEVENLABS_TTS_SAMPLE_RATE = 24_000
_OUTPUT_FORMAT = "pcm_24000"
_STREAMING_LATENCY_OPTIMIZATION = 1

# Bei Quota-Exhaustion / Auth-Problem ElevenLabs kurzzeitig skippen,
# damit nicht jeder Satz erneut den 429-Pfad triggert.
_QUOTA_COOLDOWN_S = 900.0


# Kuratierte Jarvis-Voices (Multi-Lingual, DE+EN via eleven_flash_v2_5).
# Voice-IDs sind ElevenLabs-intern stabil (offizielle Standard-Library).
JARVIS_VOICE_DANIEL = "onwK4e9ZLuTAKqWW03F9"   # British, autoritativ — Jarvis-Default
JARVIS_VOICE_GEORGE = "JBFqnCBsd6RMkjVDRZzb"   # British, tiefer Narrator
JARVIS_VOICE_CHARLIE = "IKne3meq5aSn9XLyUdCD"  # British, maturer Butler-Ton
JARVIS_VOICE_BRIAN = "nPczCjzI2devNBz1zQrb"    # American, Deep Narrator
JARVIS_VOICE_ADAM = "pNInz6obpgDQGcFmaJgB"     # American, klassische AI-Voice


DEFAULT_VOICES: tuple[str, ...] = (
    JARVIS_VOICE_DANIEL,
    JARVIS_VOICE_GEORGE,
    JARVIS_VOICE_CHARLIE,
    JARVIS_VOICE_BRIAN,
    JARVIS_VOICE_ADAM,
)


class ElevenLabsTTS:
    """TTS-Provider fuer ElevenLabs (eleven_flash_v2_5 — Multi-Lang)."""

    name = "elevenlabs"
    supports_streaming = True

    def __init__(
        self,
        model: str = "eleven_flash_v2_5",
        default_voice: str = JARVIS_VOICE_DANIEL,
        language_code: str = "de-DE",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        speed: float = 1.0,
        allow_sapi5_fallback: bool = False,
    ) -> None:
        self._model = model
        self._default_voice = default_voice
        self._language_code = language_code
        self._stability = stability
        self._similarity_boost = similarity_boost
        self._style = style
        self._speed = speed
        self._allow_sapi5_fallback = allow_sapi5_fallback
        self._client: Any = None  # lazy
        self._quota_blocked_until: float = 0.0

    # ------------------------------------------------------------------
    # Client-Setup
    # ------------------------------------------------------------------

    def _resolve_api_key(self) -> str:
        """Key-Lookup: Windows Credential Manager → ENV → .env."""
        for key, env in (
            ("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
            ("eleven_api_key", "ELEVEN_API_KEY"),
        ):
            val = cfg.get_secret(key, env_fallback=env)
            if val:
                return val
        raise RuntimeError(
            "ElevenLabs-API-Key nicht gefunden. Setze ELEVENLABS_API_KEY "
            "im Windows Credential Manager oder in der .env."
        )

    def _ensure_client(self) -> None:
        if self._client is None:
            from elevenlabs.client import ElevenLabs
            self._client = ElevenLabs(api_key=self._resolve_api_key())

    # ------------------------------------------------------------------
    # Protocol-API
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Synthetisiert Audio, yielded AudioChunks im echten Streaming.

        `language_code` ist optional — das multilinguale Modell erkennt
        die Sprache aus dem Text automatisch. Wir reichen den Code nur
        an den SAPI5-Fallback durch (fuer die Stimmen-Auswahl dort).
        """
        text = text.strip()
        if not text:
            return

        voice = voice or self._default_voice

        log = logging.getLogger("jarvis.tts.elevenlabs")

        # Cooldown aktiv? Erst Gemini, dann SAPI5 — niemals stumm bleiben.
        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        try:
            self._ensure_client()
            got_any = False
            async for pcm in self._stream_pcm(voice, text, language_code):
                if pcm:
                    got_any = True
                    yield AudioChunk(
                        pcm=pcm,
                        sample_rate=ELEVENLABS_TTS_SAMPLE_RATE,
                        timestamp_ns=0,
                        channels=1,
                    )
            if not got_any:
                log.warning("ElevenLabs-Stream leer — Fallback.")
                async for chunk in self._fallback(text, language_code):
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            low = msg.lower()
            if (
                "quota" in low
                or "rate" in low
                or "429" in msg
                or "401" in msg
                or "403" in msg
            ):
                self._quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_S
                log.warning(
                    "ElevenLabs-Quota/Auth-Fehler (%s) — Fallback fuer %.0f min.",
                    exc.__class__.__name__,
                    _QUOTA_COOLDOWN_S / 60,
                )
            else:
                log.warning(
                    "ElevenLabs-Fehler (%s: %s) — Fallback.",
                    exc.__class__.__name__,
                    msg[:200],
                )
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        """Kuratierte Jarvis-tauglichen Voice-IDs. Alle DE+EN via multilingual."""
        return list(DEFAULT_VOICES)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _stream_pcm(
        self, voice: str, text: str, language_code: str | None
    ) -> AsyncIterator[bytes]:
        """ElevenLabs-SDK ist sync → Producer-Thread + asyncio.Queue.

        Nutzt `text_to_speech.stream(...)` (SDK 2.x). Liefert einen sync
        `Iterator[bytes]` — jede Iteration ist ein Audio-Chunk.
        """
        from elevenlabs import VoiceSettings

        settings = VoiceSettings(
            stability=self._stability,
            similarity_boost=self._similarity_boost,
            style=self._style,
            use_speaker_boost=True,
            speed=self._speed,
        )

        # ElevenLabs erwartet ISO-639-1 zweistellig ("de" / "en"), nicht "de-DE".
        lang_short: str | None = None
        code = (language_code or self._language_code or "").lower().strip()
        if code:
            lang_short = code.split("-")[0] or None

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None | BaseException] = asyncio.Queue(maxsize=64)

        def _producer() -> None:
            try:
                kwargs: dict[str, Any] = dict(
                    voice_id=voice,
                    text=text,
                    model_id=self._model,
                    output_format=_OUTPUT_FORMAT,
                    voice_settings=settings,
                    optimize_streaming_latency=_STREAMING_LATENCY_OPTIMIZATION,
                )
                if lang_short:
                    kwargs["language_code"] = lang_short
                stream = self._client.text_to_speech.stream(**kwargs)
                for chunk in stream:
                    if chunk:
                        asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
            except BaseException as exc:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        producer_future = loop.run_in_executor(None, _producer)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await producer_future

    async def _fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[AudioChunk]:
        """Cross-Provider-Fallback Gemini-TTS → SAPI5 (Letzteres Opt-in).

        Stage 1 (Gemini) bleibt immer aktiv: Wechselt der primaere Provider
        wegen Quota/Auth aus, ist Gemini der naechste Cloud-TTS — keine
        roboterhafte Stimme. Stage 2 (SAPI5) ist nur aktiv wenn der User
        ``tts.allow_sapi5_fallback = true`` gesetzt hat. Default-Verhalten:
        bei Totalausfall lieber stumm bleiben als Windows-Roboter abspielen.
        """
        log = logging.getLogger("jarvis.tts.elevenlabs")

        # Stage 1: Gemini probieren (separate Quota).
        try:
            from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

            gemini = GeminiFlashTTS(
                language_code=language_code or self._language_code,
                allow_sapi5_fallback=self._allow_sapi5_fallback,
            )
            async for chunk in gemini.synthesize(text, language_code=language_code):
                yield chunk
            return
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Gemini-Fallback nach ElevenLabs-Fehler ebenfalls gescheitert (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Sowohl ElevenLabs als auch Gemini-TTS lieferten kein Audio. "
                "SAPI5-Notbremse per Config deaktiviert — bleibe stumm. "
                "Setze tts.allow_sapi5_fallback=true wenn Du Windows-TTS als "
                "Notausgang erlaubst.",
            )
            return

        # Stage 2: SAPI5 (Windows native, Quota-frei) — nur bei Opt-in.
        log.warning("SAPI5-Notbremse aktiv (Config-Opt-in).")
        pcm = await asyncio.to_thread(
            _sapi5_synthesize, text, language_code or self._language_code
        )
        if pcm:
            yield AudioChunk(
                pcm=pcm,
                sample_rate=SAPI5_SAMPLE_RATE,
                timestamp_ns=0,
                channels=1,
            )
