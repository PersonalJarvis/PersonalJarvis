"""Local user-speech transcription for realtime transports that lack it.

Every Jarvis feature that reacts to what the USER said hangs off one event:
``input_transcript``. The live bar text, the speaking indicators, the
"auflegen" hang-up phrase, the echo guard, and — most importantly — the
delegate that reaches the wiki, the project files and every Jarvis action.

Most realtime providers transcribe user audio themselves. ChatGPT-Live does
not: its notification stream carries assistant transcripts only (verified
live — 14 assistant deltas, zero user deltas, no speech-start items), and its
client-event vocabulary has no way to ask for one. Without this bridge that
provider is deaf to Jarvis: the model answers, but Jarvis itself never learns
what was said, so none of the integrations fire.

So Jarvis transcribes the microphone audio it already owns, with the
recognizer the user already configured, and emits the same events every
other provider emits. "It is just another provider" then holds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Endpointing: deliberately simple and dependency-free. The far end runs its
# own turn detection for the model's benefit; this endpointer only has to
# decide when to hand one utterance to the recognizer.
_SPEECH_RMS = 700.0
_SILENCE_RMS = 500.0
_SPEECH_START_MS = 120
_SILENCE_END_MS = 700
_PREROLL_MS = 300
_MAX_UTTERANCE_MS = 20_000
# Minimum VOICED audio — not buffer length. The buffer also holds pre-roll and
# the trailing silence that closed the utterance, so measuring it would let a
# cough or a keyboard knock buy a recognizer call and, worse, a transcript
# that Jarvis would treat as something the user said.
_MIN_VOICED_MS = 300


# How long after the microphone last carried real speech a server-side user
# transcript is still plausible. The far end transcribes with its own latency,
# so a genuine transcript trails the audio; a hallucinated one arrives while
# the user is silent or while the assistant itself is talking.
_SERVER_TRANSCRIPT_GRACE_MS = 2_000


@dataclass(frozen=True, slots=True)
class InputTranscriptEvent:
    """One normalized user-speech event for the provider event stream."""

    kind: str  # "speech_started" | "transcript" | "transcript_failed"
    text: str = ""
    is_final: bool = False


def _rms(pcm: bytes) -> float:
    import numpy as np  # noqa: PLC0415

    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))


class LocalInputTranscriber:
    """Turn microphone PCM into ``input_transcript`` events."""

    def __init__(self, *, sample_rate: int = 24_000, stt_factory: Any = None) -> None:
        self._sample_rate = sample_rate
        self._stt_factory = stt_factory
        self._stt: Any = None
        self._events: asyncio.Queue[InputTranscriptEvent | None] = asyncio.Queue(
            maxsize=64
        )
        self._preroll: list[bytes] = []
        self._preroll_ms = 0
        self._utterance: list[bytes] = []
        self._utterance_ms = 0
        self._voiced_ms = 0
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_speech = False
        self._last_speech_end = 0.0
        self._tasks: set[asyncio.Task[None]] = set()
        # A configured recognizer may wrap a native inference engine. Those
        # engines are not safe to enter concurrently (AP-24), and output
        # transcript recovery can overlap the tail of input recognition.
        self._recognition_lock = asyncio.Lock()
        self._closed = False
        self._unavailable_logged = False

    # -- voice activity ------------------------------------------------
    def speech_recently(self, grace_ms: int = _SERVER_TRANSCRIPT_GRACE_MS) -> bool:
        """Did the microphone actually carry speech just now?

        The answer is derived from audio ENERGY alone — never from the words
        of any transcript (AP-27). Server-side recognizers hallucinate
        caption-style text ("[exhale]", "pixelated image") on silence and on
        the echo of the assistant's own voice, and no spelling rule can
        separate those from a genuine short utterance. Their giveaway is
        physical: they arrive when nobody was making a sound.
        """
        if self._in_speech:
            return True
        if self._last_speech_end <= 0.0:
            return False
        return (time.monotonic() - self._last_speech_end) * 1000.0 <= grace_ms

    # -- feeding -------------------------------------------------------
    def feed(self, pcm: bytes, sample_rate: int) -> None:
        """Accept one microphone chunk; never blocks the audio path."""
        if self._closed or not pcm:
            return
        if sample_rate != self._sample_rate:
            # The pipeline is fixed at one rate per session; a mismatch means
            # a caller bug, and guessing would corrupt every duration below.
            log.debug(
                "Local input transcription ignoring a %d Hz chunk (session is %d Hz)",
                sample_rate,
                self._sample_rate,
            )
            return
        duration_ms = int(len(pcm) / 2 / self._sample_rate * 1000)
        if duration_ms <= 0:
            return
        level = _rms(pcm)

        if not self._in_speech:
            self._preroll.append(pcm)
            self._preroll_ms += duration_ms
            while self._preroll_ms > _PREROLL_MS and len(self._preroll) > 1:
                dropped = self._preroll.pop(0)
                self._preroll_ms -= int(
                    len(dropped) / 2 / self._sample_rate * 1000
                )
            if level >= _SPEECH_RMS:
                self._speech_ms += duration_ms
                if self._speech_ms >= _SPEECH_START_MS:
                    self._begin_utterance()
            else:
                self._speech_ms = 0
            return

        self._utterance.append(pcm)
        self._utterance_ms += duration_ms
        if level >= _SPEECH_RMS:
            self._voiced_ms += duration_ms
        if level < _SILENCE_RMS:
            self._silence_ms += duration_ms
        else:
            self._silence_ms = 0
        if (
            self._silence_ms >= _SILENCE_END_MS
            or self._utterance_ms >= _MAX_UTTERANCE_MS
        ):
            self._finish_utterance()

    def _begin_utterance(self) -> None:
        self._in_speech = True
        # The frames that proved speech started are already voiced audio.
        self._voiced_ms = self._speech_ms
        self._speech_ms = 0
        self._silence_ms = 0
        self._utterance = list(self._preroll)
        self._utterance_ms = self._preroll_ms
        self._preroll = []
        self._preroll_ms = 0
        self._emit(InputTranscriptEvent(kind="speech_started"))

    def _finish_utterance(self) -> None:
        pcm = b"".join(self._utterance)
        voiced_ms = self._voiced_ms
        self._in_speech = False
        self._utterance = []
        self._utterance_ms = 0
        self._voiced_ms = 0
        self._silence_ms = 0
        self._speech_ms = 0
        if voiced_ms < _MIN_VOICED_MS or not pcm:
            return
        # Only a qualifying utterance vouches for a server-side transcript; a
        # cough must not open the door that the energy gate just closed.
        self._last_speech_end = time.monotonic()
        task = asyncio.create_task(
            self._transcribe(pcm), name="realtime-local-input-transcribe"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- recognition ---------------------------------------------------
    async def _ensure_stt(self) -> Any:
        if self._stt is not None:
            return self._stt
        factory = self._stt_factory
        if factory is None:

            def factory() -> Any:  # noqa: ANN202 - local default wiring
                from jarvis.core.config import load_config  # noqa: PLC0415
                from jarvis.plugins.stt import build_stt_from_config  # noqa: PLC0415

                return build_stt_from_config(load_config().stt)

        self._stt = await asyncio.to_thread(factory)
        return self._stt

    async def _transcribe(self, pcm: bytes) -> None:
        started = time.monotonic()
        try:
            text = await self.transcribe_audio(pcm, sample_rate=self._sample_rate)
        except Exception:  # noqa: BLE001 - one failed utterance is not fatal
            if self._stt is None and not self._unavailable_logged:
                self._unavailable_logged = True
                log.warning(
                    "Local input transcription is unavailable; this provider's "
                    "voice still answers, but Jarvis-side features that need "
                    "the user's words stay idle",
                    exc_info=True,
                )
            elif self._stt is not None:
                log.warning(
                    "Local input transcription failed for one utterance",
                    exc_info=True,
                )
            self._emit(InputTranscriptEvent(kind="transcript_failed"))
            return
        if not text:
            # Real speech that produced no words: the far end's own transcript
            # of the same audio is now the best thing Jarvis has.
            self._emit(InputTranscriptEvent(kind="transcript_failed"))
            return
        log.info(
            "realtime local input transcript (%.0f ms): %r",
            (time.monotonic() - started) * 1000,
            text[:120],
        )
        self._emit(InputTranscriptEvent(kind="transcript", text=text, is_final=True))

    async def transcribe_audio(self, pcm: bytes, *, sample_rate: int) -> str:
        """Recognize one bounded PCM segment without emitting input events.

        The Codex subscription transport uses this only when ChatGPT-Live
        supplied assistant audio but omitted its matching transcript. The
        recovered text still passes through the ordinary output scrub gate;
        this method merely supplies the missing evidence. Calls are serialized
        because the configured STT may own a native, non-thread-safe engine.
        """
        if sample_rate != self._sample_rate:
            raise ValueError(
                "Local transcription received audio at an unexpected sample rate"
            )
        if not pcm:
            return ""

        from jarvis.core.protocols import AudioChunk  # noqa: PLC0415

        async with self._recognition_lock:
            stt = await self._ensure_stt()

            async def chunks():  # noqa: ANN202 - one-shot adapter
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=self._sample_rate,
                    timestamp_ns=0,
                    channels=1,
                )

            result = await stt.transcribe(chunks())
        return str(getattr(result, "text", "") or "").strip()

    def _emit(self, event: InputTranscriptEvent) -> None:
        try:
            self._events.put_nowait(event)
        except asyncio.QueueFull:  # noqa: S110 - a backed-up consumer already has plenty
            log.debug("Local input transcript queue is full; dropping one event")

    # -- consuming -----------------------------------------------------
    async def next_event(self) -> InputTranscriptEvent | None:
        return await self._events.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        try:
            self._events.put_nowait(None)
        except asyncio.QueueFull:  # noqa: S110 - the consumer will drain and see the end
            pass


__all__ = ["InputTranscriptEvent", "LocalInputTranscriber"]
