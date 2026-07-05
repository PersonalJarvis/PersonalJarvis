"""ElevenLabs TTS plugin — multi-language DE+EN via eleven_flash_v2_5.

Uses the official `elevenlabs` SDK. Output format is `pcm_24000`
(raw linear PCM, 24 kHz mono, no decoder needed) — identical to the
Gemini path, so the playback layer (`sounddevice`) doesn't need any
adjustment.

Real streaming via `client.text_to_speech.convert_as_stream(...)`:
the SDK delivers bytes immediately as individual audio chunks arrive.
Because the SDK is synchronous, the producer runs in a worker thread
and hands chunks to the async consumer via an `asyncio.Queue`.

Fallback chain on errors (quota / auth / network):
  ElevenLabs  →  Gemini TTS  →  SAPI5 (Windows native, quota-free)
The Gemini/SAPI5 helpers are imported from `gemini_flash_tts` to
keep the implementation small.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk

# Reuse the SAPI5 emergency fallback (Windows-native, quota-free).
from jarvis.plugins.tts.gemini_flash_tts import (
    SAPI5_SAMPLE_RATE,
    _sapi5_synthesize,
)

# Output-Format wie Gemini: 24 kHz mono int16 PCM (pcm_24000).
ELEVENLABS_TTS_SAMPLE_RATE = 24_000
_OUTPUT_FORMAT = "pcm_24000"
_STREAMING_LATENCY_OPTIMIZATION = 1

DEFAULT_MODEL = "eleven_flash_v2_5"

# Every ElevenLabs speech model id starts with ``eleven`` (the API namespace).
# Curated snapshot for an exact-match fast path; the prefix rule below still
# admits a NEW eleven_* model we don't list yet.
_KNOWN_ELEVEN_MODELS: frozenset[str] = frozenset({
    "eleven_flash_v2_5", "eleven_flash_v2",
    "eleven_turbo_v2_5", "eleven_turbo_v2",
    "eleven_multilingual_v2", "eleven_multilingual_v1",
    "eleven_monolingual_v1", "eleven_v3",
    "eleven_english_sts_v2", "eleven_multilingual_sts_v2",
})


def coerce_elevenlabs_model(model_id: str | None) -> str:
    """Return a usable ElevenLabs model id for ``model_id``.

    Guards against a FOREIGN model id left in the shared ``[tts].model`` config
    after switching TTS providers. ``[tts]`` has a single global ``model`` field
    shared across every TTS family, so flipping TO ElevenLabs can leave another
    provider's value in place (Cartesia's ``sonic-2``, Gemini's ``gemini-*``,
    an OpenRouter ``vendor/model``) — ElevenLabs then 400s ("An invalid ID has
    been received for voice: 'sonic-2'"). Mirrors ``coerce_speech_model`` for
    OpenRouter. Resolution:

    * empty                → the default model,
    * a known eleven model → itself,
    * any ``eleven*`` id   → itself (trust a NEW model we don't list yet),
    * anything else        → the default model (a foreign provider's id).
    """
    mid = (model_id or "").strip()
    if not mid:
        return DEFAULT_MODEL
    if mid in _KNOWN_ELEVEN_MODELS or mid.lower().startswith("eleven"):
        return mid
    return DEFAULT_MODEL

# On quota exhaustion / an auth problem, skip ElevenLabs briefly
# so not every sentence retriggers the 429 path.
_QUOTA_COOLDOWN_S = 900.0


# Curated Jarvis voices (multilingual, DE+EN via eleven_flash_v2_5).
# Voice IDs are stable within ElevenLabs (official standard library).
JARVIS_VOICE_DANIEL = "onwK4e9ZLuTAKqWW03F9"   # British, authoritative — Jarvis default
JARVIS_VOICE_GEORGE = "JBFqnCBsd6RMkjVDRZzb"   # British, deep narrator
JARVIS_VOICE_CHARLIE = "IKne3meq5aSn9XLyUdCD"  # British, mature butler tone
JARVIS_VOICE_BRIAN = "nPczCjzI2devNBz1zQrb"    # American, deep narrator
JARVIS_VOICE_ADAM = "pNInz6obpgDQGcFmaJgB"     # American, classic AI voice


DEFAULT_VOICES: tuple[str, ...] = (
    JARVIS_VOICE_DANIEL,
    JARVIS_VOICE_GEORGE,
    JARVIS_VOICE_CHARLIE,
    JARVIS_VOICE_BRIAN,
    JARVIS_VOICE_ADAM,
)


class ElevenLabsTTS:
    """TTS provider for ElevenLabs (eleven_flash_v2_5 — multi-lang)."""

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
        """Key lookup: Windows Credential Manager → ENV → .env."""
        for key, env in (
            ("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
            ("eleven_api_key", "ELEVEN_API_KEY"),
        ):
            val = cfg.get_secret(key, env_fallback=env)
            if val:
                return val
        raise RuntimeError(
            "ElevenLabs API key not found. Set ELEVENLABS_API_KEY "
            "in the Windows Credential Manager or in .env."
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
        """Synthesizes audio, yields AudioChunks in real streaming.

        `language_code` is optional — the multilingual model detects
        the language from the text automatically. We only pass the code
        through to the SAPI5 fallback (for the voice selection there).
        """
        text = text.strip()
        if not text:
            return

        voice = voice or self._default_voice

        log = logging.getLogger("jarvis.tts.elevenlabs")

        # Cooldown active? Gemini first, then SAPI5 — never stay silent.
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
                log.warning("ElevenLabs stream empty — falling back.")
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
                    "ElevenLabs quota/auth error (%s) — falling back for %.0f min.",
                    exc.__class__.__name__,
                    _QUOTA_COOLDOWN_S / 60,
                )
            else:
                log.warning(
                    "ElevenLabs error (%s: %s) — falling back.",
                    exc.__class__.__name__,
                    msg[:200],
                )
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        """Curated Jarvis-suitable voice IDs. All DE+EN via multilingual."""
        return list(DEFAULT_VOICES)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _stream_pcm(
        self, voice: str, text: str, language_code: str | None
    ) -> AsyncIterator[bytes]:
        """The ElevenLabs SDK is sync → producer thread + asyncio.Queue.

        Uses `text_to_speech.stream(...)` (SDK 2.x). Returns a sync
        `Iterator[bytes]` — each iteration is one audio chunk.
        """
        from elevenlabs import VoiceSettings

        settings = VoiceSettings(
            stability=self._stability,
            similarity_boost=self._similarity_boost,
            style=self._style,
            use_speaker_boost=True,
            speed=self._speed,
        )

        # ElevenLabs expects two-letter ISO-639-1 ("de" / "en"), not "de-DE".
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
        """Cross-provider fallback Gemini TTS → SAPI5 (the latter opt-in).

        Stage 1 (Gemini) is always active: if the primary provider drops
        out due to quota/auth, Gemini is the next cloud TTS — no
        robotic voice. Stage 2 (SAPI5) is only active if the user has
        set ``tts.allow_sapi5_fallback = true``. Default behavior:
        on total failure, prefer staying silent over playing the Windows robot voice.
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
                "Gemini fallback after the ElevenLabs error also failed (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Both ElevenLabs and Gemini TTS delivered no audio. "
                "SAPI5 emergency fallback disabled via config — staying silent. "
                "Set tts.allow_sapi5_fallback=true if you want to allow "
                "Windows TTS as an emergency exit.",
            )
            return

        # Stage 2: SAPI5 (Windows-native, quota-free) — opt-in only.
        log.warning("SAPI5 emergency fallback active (config opt-in).")
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
