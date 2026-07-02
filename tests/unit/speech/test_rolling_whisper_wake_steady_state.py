"""Steady-state wedge accounting: slow calls must not tear the model down.

Live forensic 2026-07-02 (data/jarvis_desktop.log 08:21-08:26): ONE
transcription slower than the 8 s poll cap started a self-perpetuating deaf
cycle. Old accounting: the timeout counted as failure #1, the very next poll
hit ``TranscribeBusy`` (the SAME abandoned call still holding the model lock)
and counted as failure #2 -> ``recover()`` dropped a healthy model -> the lazy
cold rebuild ran INSIDE the next poll's 8 s timeout, re-wedged under load, and
the cycle repeated for minutes while the wake was deaf.

New contract (tested here):
- A ``TranscribeBusy`` poll is NOT an independent failure — a slow-but-alive
  call that eventually returns must never trigger ``recover()``.
- A busy streak longer than ``busy_hang_recover_s`` IS a true hang (BUG-036,
  un-cancellable native call) and must recover.
- After any mid-session ``recover()`` the poll loop re-warms the rebuilt model
  OFF the transcribe timeout (``warm_up``), so polling resumes against a hot
  model instead of racing a cold load against the 8 s cap.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.stt.fwhisper import TranscribeBusy
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    val = 12000 + (marker % 80) * 100
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _Transcript:
    text = ""
    confidence = 1.0
    segments = ()


class _LockedSTT:
    """Fake with the REAL provider's lock semantics: the first call runs for
    ``first_call_s`` on a background task (the un-cancellable worker-thread
    analogue — ``asyncio.shield`` keeps it running when the poll's timeout
    abandons the await) and every overlapping call raises ``TranscribeBusy``
    until it finishes. ``first_call_s=None`` = the first call hangs forever
    (true wedge)."""

    def __init__(self, first_call_s: float | None) -> None:
        self._first_call_s = first_call_s
        self._first_started = False
        self.busy = False
        self.is_warm = True
        self.completed = 0
        self.recover_calls = 0
        self.warm_up_calls = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        if self.busy:
            raise TranscribeBusy("a transcription is already in flight")
        if not self._first_started:
            self._first_started = True
            self.busy = True

            async def _finish() -> None:
                if self._first_call_s is None:
                    await asyncio.Event().wait()  # never returns
                await asyncio.sleep(self._first_call_s)
                self.busy = False
                self.completed += 1

            task = asyncio.get_running_loop().create_task(_finish())
            await asyncio.shield(task)  # timeout abandons the await, task lives on
            return _Transcript()
        self.completed += 1
        return _Transcript()

    def recover(self) -> None:
        self.recover_calls += 1
        # Mirrors FasterWhisperProvider.recover(): fresh lock (the orphaned
        # call keeps the OLD one) + the model needs a re-warm.
        self.busy = False
        self.is_warm = False

    def warm_up(self) -> None:
        self.warm_up_calls += 1
        self.is_warm = True


class _AlwaysHangingSTT:
    """Every call hangs (cancellable, no lock) — two DISTINCT timeouts."""

    def __init__(self) -> None:
        self.is_warm = True
        self.recover_calls = 0
        self.warm_up_calls = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        await asyncio.Event().wait()

    def recover(self) -> None:
        self.recover_calls += 1
        self.is_warm = False

    def warm_up(self) -> None:
        self.warm_up_calls += 1
        self.is_warm = True


def _wake(stt, *, timeout_s: float, busy_hang_s: float) -> RollingWhisperWake:
    return RollingWhisperWake(
        stt,
        pattern=re.compile(r"nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=timeout_s,
        busy_hang_recover_s=busy_hang_s,
    )


async def _drive_with_audio(wake: RollingWhisperWake):
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


async def test_slow_but_alive_call_never_tears_down_the_model() -> None:
    """timeout -> busy is ONE slow call, not two failures: no recover()."""
    stt = _LockedSTT(first_call_s=0.3)
    wake = _wake(stt, timeout_s=0.06, busy_hang_s=10.0)
    shutdown = await _drive_with_audio(wake)
    try:
        # Wait until the slow first call finished AND at least one later call
        # succeeded (polling resumed against the SAME model).
        for _ in range(400):
            if stt.completed >= 2:
                break
            await asyncio.sleep(0.01)
        assert stt.completed >= 2, "polling never resumed after the slow call"
        assert stt.recover_calls == 0, (
            "a slow-but-alive transcription tore down a healthy model "
            "(timeout+busy double-counted as two failures)"
        )
    finally:
        await shutdown()


async def test_true_hang_recovers_after_the_busy_hard_cap_and_rewarms() -> None:
    """A busy streak past ``busy_hang_recover_s`` is a real wedge: recover,
    then re-warm OFF the poll path, then polling resumes."""
    stt = _LockedSTT(first_call_s=None)  # first call never returns
    wake = _wake(stt, timeout_s=0.05, busy_hang_s=0.2)
    shutdown = await _drive_with_audio(wake)
    try:
        for _ in range(500):
            if stt.completed >= 1:
                break
            await asyncio.sleep(0.01)
        assert stt.recover_calls >= 1, "true hang never recovered"
        assert stt.warm_up_calls >= 1, (
            "rebuilt model was not re-warmed off the poll path (cold rebuild "
            "would race the transcribe timeout again — the cascade)"
        )
        assert stt.completed >= 1, "polling never resumed after the recovery"
    finally:
        await shutdown()


async def test_two_distinct_timeouts_still_recover_and_rewarm() -> None:
    """Two timeouts of two DIFFERENT calls remain the wedge signature."""
    stt = _AlwaysHangingSTT()
    wake = _wake(stt, timeout_s=0.03, busy_hang_s=10.0)
    shutdown = await _drive_with_audio(wake)
    try:
        for _ in range(400):
            if stt.recover_calls >= 1 and stt.warm_up_calls >= 1:
                break
            await asyncio.sleep(0.01)
        assert stt.recover_calls >= 1, "distinct-timeout wedge never recovered"
        assert stt.warm_up_calls >= 1, "no re-warm after the wedge recovery"
    finally:
        await shutdown()
