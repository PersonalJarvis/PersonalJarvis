"""Whisper-based wake-word fallback.

Runs in parallel with openWakeWord as a second, more robust detector: takes
audio chunks, endpoints them via Silero VAD, transcribes each detected
utterance briefly through Whisper, and matches the transcript against a
keyword pattern (default: "jarvis" as a standalone word or within "hey jarvis").

Advantage: understands German pronunciation natively (Whisper is trained
multilingually). Disadvantage: ~800-1200 ms latency (VAD endpoint + Whisper).

We use both in parallel — openWakeWord for fast hits (15-30 ms), Whisper wake
as a safety net for all the cases where openWakeWord doesn't catch it (e.g. a
German accent, low mic level).
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from jarvis.audio.vad import SileroEndpointer
from jarvis.core.protocols import AudioChunk
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

log = logging.getLogger("jarvis.wake.whisper")


# 2026-05-12: require the full "Hey/Hi/Hallo Jarvis" phrase, not bare
# "Jarvis". The bare-word pattern fired on any utterance containing
# "Jarvis" anywhere — including Whisper hallucinations on short audio
# windows. Kept consistent with rolling_whisper_wake.DEFAULT_PATTERN.
# Matches "hey jarvis", "hi jarvis", "hallo jarvis" (with or without
# comma, any whitespace). Does NOT match a bare "jarvis" without a
# hey/hi/hallo prefix.
DEFAULT_PATTERN = re.compile(
    r"\bh(ey|i|allo)\W+jarvis\b",
    re.IGNORECASE,
)


class WhisperWakeDetector:
    """VAD-endpointed Whisper transcription with keyword matching."""

    def __init__(
        self,
        stt: FasterWhisperProvider,
        vad: SileroEndpointer | None = None,
        pattern: re.Pattern[str] = DEFAULT_PATTERN,
        vad_silence_ms: int = 500,      # shorter than for a regular utterance
        min_speech_ms: int = 150,        # shorter — "jarvis" alone is enough
    ) -> None:
        self._stt = stt
        self._vad = vad or SileroEndpointer(
            silence_ms=vad_silence_ms,
            min_speech_ms=min_speech_ms,
        )
        self._pattern = pattern

    async def detect(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[str]:
        """Consumes audio, yields the matched keyword string on a hit."""
        async for utterance_pcm in self._vad.utterances(chunks):
            try:
                transcript = await self._stt.transcribe_pcm(utterance_pcm)
            except Exception as exc:  # noqa: BLE001
                log.warning("Whisper wake transcription error: %s", exc)
                continue
            text = transcript.text.strip()
            if not text:
                continue
            log.info("whisper-wake heard: '%s'", text)
            m = self._pattern.search(text)
            if m:
                yield m.group(0)
