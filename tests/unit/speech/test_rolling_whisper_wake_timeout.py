"""RollingWhisperWake must self-heal from a hung local-Whisper transcription.

Live forensic (2026-06-29, data/jarvis_desktop.log): a user with a custom wake
word ("Hey Nico" -> engine=stt_match -> RollingWhisperWake) said the wake word
for minutes with no reaction. The log showed the chunk-consumer heartbeat ALIVE
(audio flowing, max-rms up to 0.27 while speaking) but the poll loop emitted ZERO
``rolling-whisper:`` transcripts for 12 minutes after a single mid-session line —
and the frozen ``last-transcript`` proved ``_last_transcript`` never updated. No
error was logged. That is a SILENT hang inside ``await stt.transcribe_pcm(...)``:
the local faster-whisper call blocked and never returned, so the poll loop waited
forever and the wake word was permanently dead (the "sometimes stops waking
entirely" symptom; the "no dead state blocks waking" requirement).

The fix is the same per-call cap the OWW prefix-verifier already uses
(``wake_verifier._WAKE_VERIFY_TIMEOUT_S``): bound each transcription with
``asyncio.wait_for`` so a hung STT is abandoned and the loop re-polls fresh audio
instead of freezing.
"""
from __future__ import annotations

import asyncio

import numpy as np

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    val = 12000 + (marker % 80) * 100  # < 32767, well above min_peak
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _HangFirstThenMatchSTT:
    """First ``transcribe_pcm`` hangs forever; later calls return a match.

    Mirrors the live wedge: one transcription blocks indefinitely (here on an
    Event the test never sets), every subsequent call transcribes normally.
    """

    def __init__(self, phrase: str) -> None:
        self._phrase = phrase
        self.calls = 0
        self._hang = asyncio.Event()  # never set -> first call blocks until cancelled

    async def transcribe_pcm(
        self, pcm: bytes, sample_rate: int = 16000, language: str | None = None
    ) -> Transcript:
        self.calls += 1
        if self.calls == 1:
            await self._hang.wait()  # the wedge: blocks until wait_for cancels it
        return Transcript(text=self._phrase, language="de", confidence=0.9)


async def test_hung_transcription_does_not_freeze_wake_forever() -> None:
    import re

    stt = _HangFirstThenMatchSTT("hey nico")
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile(r"hey\W+nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=0.15,  # abandon a hung transcription fast (test)
    )

    src: asyncio.Queue = asyncio.Queue()
    stop_feed = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _feed() -> None:
        i = 0
        while not stop_feed.is_set():
            await src.put(_loud_chunk(i))
            i += 1
            await asyncio.sleep(0.002)

    async def _first_keyword() -> str:
        async for kw in wake.detect(_iter()):
            return kw
        return ""

    feeder = asyncio.create_task(_feed())
    try:
        # Without the per-call timeout, the poll loop blocks forever on the first
        # (hung) transcription and this wait_for trips -> the wake is dead. With
        # the timeout, the first call is abandoned, the loop re-polls, the second
        # call matches, and the keyword is yielded.
        kw = await asyncio.wait_for(_first_keyword(), timeout=3.0)
        assert kw.lower() == "hey nico"
        assert stt.calls >= 2, (
            f"poll loop never retried after the hung first call (calls={stt.calls})"
        )
    finally:
        stop_feed.set()
        await src.put(None)
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass
