"""Whisper-basierter Wake-Word-Fallback.

Läuft parallel zu openWakeWord als zweite, robustere Detection: nimmt
Audio-Chunks, endpointet via Silero-VAD, transkribiert jede erkannte
Utterance kurz durch Whisper und matcht das Transkript gegen ein
Keyword-Pattern (Default: "jarvis" als Einzelwort oder in "hey jarvis").

Vorteil: versteht deutsche Aussprache nativ (Whisper ist multilingual
trainiert). Nachteil: ~800-1200 ms Latenz (VAD-Endpoint + Whisper).

Wir nutzen beide parallel — openWakeWord für schnelle Hits (15-30 ms),
Whisper-Wake als Sicherheitsnetz für alle Fälle wo openWakeWord nicht
zuschlägt (z.B. deutscher Akzent, niedriger Mic-Pegel).
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
    """VAD-endpointed Whisper-Transkription mit Keyword-Match."""

    def __init__(
        self,
        stt: FasterWhisperProvider,
        vad: SileroEndpointer | None = None,
        pattern: re.Pattern[str] = DEFAULT_PATTERN,
        vad_silence_ms: int = 500,      # kürzer als bei regulärer Utterance
        min_speech_ms: int = 150,        # kürzer, "jarvis" allein reicht
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
        """Konsumiert Audio, yielded matched-Keyword-String bei Hit."""
        async for utterance_pcm in self._vad.utterances(chunks):
            try:
                transcript = await self._stt.transcribe_pcm(utterance_pcm)
            except Exception as exc:  # noqa: BLE001
                log.warning("Whisper-Wake Transkriptions-Fehler: %s", exc)
                continue
            text = transcript.text.strip()
            if not text:
                continue
            log.info("whisper-wake hört: '%s'", text)
            m = self._pattern.search(text)
            if m:
                yield m.group(0)
