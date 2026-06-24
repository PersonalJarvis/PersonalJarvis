"""Regression tests for the rolling-whisper wake backstop (custom phrases).

Forensic 2026-06-22 (branch feat/fast-boot-bootstrap): a user who sets a custom
wake word ("Hey Ruben") falls onto the ``stt_match`` path = RollingWhisperWake.
Two live symptoms — "riesige Verzoegerung" + "manchmal gar nicht":

- LATENCY (this file): ``detect`` used to ``await transcribe_pcm`` *inside* the
  chunk-consume loop, so while a (CPU "base") transcription ran for ~0.5-1 s the
  loop could not pull new chunks. They piled up (observed ``wsp_q=100``) and the
  detector trailed seconds behind live audio. The consumer must keep draining
  audio while a transcription is in flight, and each transcription must run on
  the *freshest* window, not a stale FIFO backlog.
"""
from __future__ import annotations

import asyncio

import numpy as np

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    """A 100 ms @ 16 kHz chunk, loud enough to pass the rms/peak gates.

    ``marker`` is encoded in the (constant) sample value so a test STT can read
    back which slice of audio it was handed.
    """
    val = 12000 + (marker % 80) * 100  # stays < 32767, well above min_peak
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _GatedSTT:
    """``transcribe_pcm`` blocks on an event until the test releases it."""

    def __init__(self) -> None:
        self.calls = 0
        self.first_call_started = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> Transcript:
        self.calls += 1
        self.first_call_started.set()
        await self.release.wait()
        return Transcript(text="nothing here", language="de", confidence=0.9)


class _NeverMatch:
    """A matcher that never fires, so ``detect`` loops forever (no early yield)."""

    def search(self, text: str):  # noqa: ANN001 - duck-typed re.Pattern
        return None


class _PhraseSTT:
    """Always transcribes the same (matching) phrase."""

    def __init__(
        self,
        phrase: str,
        *,
        confidence: float = 0.9,
        no_speech_prob: float | None = None,
    ) -> None:
        self._phrase = phrase
        self._confidence = confidence
        self._no_speech_prob = no_speech_prob

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> Transcript:
        segments = ()
        if self._no_speech_prob is not None:
            segments = ({"no_speech_prob": self._no_speech_prob},)
        return Transcript(
            text=self._phrase,
            language="de",
            confidence=self._confidence,
            segments=segments,
        )


async def _feed_until(src: asyncio.Queue, stop: asyncio.Event) -> None:
    i = 0
    while not stop.is_set():
        await src.put(_loud_chunk(i))
        i += 1
        await asyncio.sleep(0.002)


async def test_detect_yields_keyword_when_transcript_matches() -> None:
    """Refactor guard: a real wake (matching transcript) still yields."""
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("hey ruben"),
        pattern=re.compile(r"ruben"),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw == "ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_accepts_short_live_wake_phrase_confidence() -> None:
    """Live two-word wake phrases can score below the generic 0.55 gate."""
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("Hey Ruben!", confidence=0.499, no_speech_prob=0.05734),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw.lower() == "hey ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_accepts_real_world_low_confidence_wake() -> None:
    """The live base/cpu model scores a real, cleanly-heard wake well below 0.45.

    Forensic 2026-06-23 (running app, branch feat/fast-boot-bootstrap): the
    rolling-whisper wake rejected a CORRECTLY transcribed "Ruben." at confidence
    0.318 — and 141 more like it (142 rejects / 0 accepts in a whole evening). The
    old ``min_wake_confidence=0.45`` gate, built to suppress *prompt-bias*
    hallucinations, also kills genuine quiet wakes (the bias is now disabled, so
    the gate guards a problem that no longer exists). A matching transcript with
    real-speech ``no_speech_prob`` at the live-observed confidence must wake.
    """
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("Hey Ruben", confidence=0.318, no_speech_prob=0.05),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw.lower() == "hey ruben"
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_rejects_matching_low_confidence_transcript() -> None:
    """A prompt-biased or mumbled window may hallucinate the wake phrase.

    Matching text alone is not enough: the wake backstop must fail closed when
    Whisper itself reports the transcript as unreliable.
    """
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("hey ruben", confidence=0.2),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        try:
            kw = await asyncio.wait_for(_first_keyword(), timeout=0.7)
        except asyncio.TimeoutError:
            kw = ""
        assert kw == ""
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_detect_rejects_matching_high_no_speech_transcript() -> None:
    """faster-whisper exposes per-segment no_speech_prob for silence/noise.

    If that says the window probably contains no speech, a text match must not
    wake the assistant.
    """
    import re

    wake = RollingWhisperWake(
        _PhraseSTT("hey ruben", confidence=0.9, no_speech_prob=0.95),
        pattern=re.compile(r"hey\W+ruben", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed_until(src, stop_feed))
    try:
        try:
            kw = await asyncio.wait_for(_first_keyword(), timeout=0.7)
        except asyncio.TimeoutError:
            kw = ""
        assert kw == ""
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


async def test_consumer_keeps_draining_while_transcription_is_in_flight() -> None:
    stt = _GatedSTT()
    wake = RollingWhisperWake(
        stt,
        pattern=_NeverMatch(),
        poll_interval_s=0.02,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
    )

    src: asyncio.Queue = asyncio.Queue()
    consumed = 0
    stop_feed = asyncio.Event()

    async def _iter():
        nonlocal consumed
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            consumed += 1
            yield chunk

    async def _feeder() -> None:
        # Emulate a live mic stream: chunks keep arriving regardless of how slow
        # the STT is. A real mic never pauses for the transcriber.
        i = 0
        while not stop_feed.is_set():
            await src.put(_loud_chunk(i))
            i += 1
            await asyncio.sleep(0.002)

    async def _drive() -> None:
        async for _kw in wake.detect(_iter()):
            pass

    feeder = asyncio.create_task(_feeder())
    detect_task = asyncio.create_task(_drive())
    try:
        # Wait until the first (now-blocked) transcription is in flight.
        await asyncio.wait_for(stt.first_call_started.wait(), timeout=3.0)
        consumed_when_blocked = consumed

        # The mic keeps streaming while the transcription is stuck.
        await asyncio.sleep(0.3)
        progressed = consumed - consumed_when_blocked

        # Coupled (buggy) code is stuck on ``await transcribe`` and cannot pull
        # from the iterator -> progressed ~= 0 (the queue just backs up, exactly
        # the observed wsp_q=100). Decoupled (fixed) code keeps draining audio
        # while the transcription runs -> progressed is large.
        assert progressed >= 30, (
            f"consumer blocked on in-flight transcription: progressed={progressed} "
            f"chunks while a transcription was stuck (consumed={consumed}, "
            f"was {consumed_when_blocked} when the transcribe started)"
        )
        # And it must not fire overlapping transcriptions while one is in flight.
        assert stt.calls == 1, f"overlapping transcriptions in flight: {stt.calls}"
    finally:
        stop_feed.set()
        stt.release.set()
        await src.put(None)
        feeder.cancel()
        detect_task.cancel()
        for t in (feeder, detect_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
