"""The wake poll loop must not poke the STT model while it is still loading.

TTU forensic 2026-07-02 (data/jarvis_desktop.log, boot 08:20): the poll loop's
first ``transcribe_pcm`` triggered the LAZY model load, hit the 8 s per-call
timeout while the load was still running, the next poll hit TranscribeBusy,
two failures tripped the self-heal ``recover()`` which threw the half-loaded
model away — a reload cascade under the boot CPU storm that turned a ~4 s
model load into 114.7 s and TTU ~200 s. The fix: the poll (and the fail
counter) starts only once the provider reports ``is_warm``; if nobody warms
the model within the fallback window, the poll loop owns the warm-up itself —
exactly one loader either way.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    val = 12000 + (marker % 80) * 100
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _WarmableSTT:
    """Fake provider with the warm-signal surface of FasterWhisperProvider."""

    def __init__(self, *, warm: bool = False) -> None:
        self.is_warm = warm
        self.transcribe_calls = 0
        self.warm_up_calls = 0
        self.recover_calls = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        self.transcribe_calls += 1

        class _T:
            text = ""
            confidence = 1.0
            segments = ()

        return _T()

    def warm_up(self) -> None:
        self.warm_up_calls += 1
        self.is_warm = True

    def recover(self) -> None:
        self.recover_calls += 1


def _wake(stt, *, fallback_s: float = 30.0) -> RollingWhisperWake:
    return RollingWhisperWake(
        stt,
        pattern=re.compile(r"nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=1.0,
        warm_wait_fallback_s=fallback_s,
    )


async def _drive_with_audio(wake: RollingWhisperWake):
    """Start detect() with a steady loud-audio feed; return (stop, tasks)."""
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()

    async def _iter():
        while True:
            chunk = await src.get()
            if chunk is None:
                return
            yield chunk

    async def _feed() -> None:
        i = 0
        while not stop.is_set():
            await src.put(_loud_chunk(i))
            i += 1
            await asyncio.sleep(0.002)

    async def _drain() -> None:
        async for _kw in wake.detect(_iter()):
            pass

    feeder = asyncio.create_task(_feed())
    driver = asyncio.create_task(_drain())

    async def _shutdown() -> None:
        stop.set()
        await src.put(None)
        driver.cancel()
        feeder.cancel()
        for t in (driver, feeder):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass

    return _shutdown


async def test_poll_does_not_touch_a_cold_model_then_starts_when_warm() -> None:
    stt = _WarmableSTT(warm=False)
    wake = _wake(stt)
    shutdown = await _drive_with_audio(wake)
    try:
        # Cold phase: plenty of loud audio, but the model is not warm — the
        # poll loop must make ZERO transcribe attempts (no timeout/busy fails,
        # no self-heal recover() that would throw a loading model away).
        await asyncio.sleep(0.4)
        assert stt.transcribe_calls == 0, "poll loop poked a cold model"
        assert stt.recover_calls == 0

        # Someone (the deferred loader) finishes the warm-up -> polling starts.
        stt.is_warm = True
        for _ in range(200):
            if stt.transcribe_calls > 0:
                break
            await asyncio.sleep(0.01)
        assert stt.transcribe_calls > 0, "poll never started on a warm model"
    finally:
        await shutdown()


async def test_poll_owns_the_warmup_after_the_fallback_window() -> None:
    """If nobody warms the model (unusual wiring), the poll loop must warm it
    itself after the fallback window instead of waiting forever."""
    stt = _WarmableSTT(warm=False)
    wake = _wake(stt, fallback_s=0.1)
    shutdown = await _drive_with_audio(wake)
    try:
        for _ in range(300):
            if stt.transcribe_calls > 0:
                break
            await asyncio.sleep(0.01)
        assert stt.warm_up_calls >= 1, "fallback warm-up never ran"
        assert stt.transcribe_calls > 0, "poll never started after fallback warm-up"
    finally:
        await shutdown()


async def test_provider_without_warm_flag_polls_immediately() -> None:
    """Fakes / cloud STT providers without ``is_warm`` must behave as before —
    no warm-wait phase, polling starts right away."""
    stt = _WarmableSTT(warm=False)
    del stt.is_warm  # instance attr removed -> getattr falls back to default
    wake = _wake(stt)
    shutdown = await _drive_with_audio(wake)
    try:
        for _ in range(200):
            if stt.transcribe_calls > 0:
                break
            await asyncio.sleep(0.01)
        assert stt.transcribe_calls > 0
    finally:
        await shutdown()
