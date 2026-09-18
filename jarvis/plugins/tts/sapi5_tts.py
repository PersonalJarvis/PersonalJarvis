"""Windows' built-in SAPI5 voices as a keyless, on-device TTS provider.

The last-resort local voice: every Windows install ships SAPI5, and a
Japanese Windows ships a Japanese voice (Microsoft Haruka). Quality is plain,
latency is low and nothing leaves the machine — the right floor under a
neural local voice (VOICEVOX) that may still be starting or may be missing.

Other operating systems have no SAPI5; ``synthesize`` raises there so a
``FallbackTTS`` in front of it moves on instead of going silent.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator

from jarvis.core.protocols import AudioChunk


class Sapi5TTS:
    """SAPI5 speech in the voice matching the turn's language (Windows only)."""

    name = "sapi5"
    supports_streaming = False
    last_voice: str | None = None
    last_voice_provider: str | None = None
    runs_on_device = True

    def __init__(self, *, language_code: str | None = None) -> None:
        self._language_code = (language_code or "").strip() or "en-US"

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        spoken = (text or "").strip()
        if not spoken:
            return
        if sys.platform != "win32":
            raise RuntimeError("SAPI5 voices exist only on Windows.")
        from jarvis.plugins.tts.gemini_flash_tts import SAPI5_SAMPLE_RATE, _sapi5_synthesize

        lang = (language_code or self._language_code).strip() or "en-US"
        pcm = await asyncio.to_thread(_sapi5_synthesize, spoken, lang)
        if not pcm:
            raise RuntimeError("SAPI5 produced no audio for this sentence.")
        self.last_voice = f"sapi5:{lang}"
        self.last_voice_provider = self.name
        yield AudioChunk(pcm=pcm, sample_rate=SAPI5_SAMPLE_RATE, timestamp_ns=time.monotonic_ns())

    def list_voices(self, language: str | None = None) -> list[str]:
        return []
