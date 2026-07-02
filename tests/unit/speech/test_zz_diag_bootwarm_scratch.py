"""Temporary diagnostic for the boot-warm test failure — deleted after use."""
from __future__ import annotations

import asyncio
import re

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


class _STT:
    def __init__(self) -> None:
        self.is_warm = False
        self.calls = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        self.calls += 1

        class _T:
            text = ""
            confidence = 1.0
            segments = ()

        return _T()

    def warm_up(self) -> None:
        self.is_warm = True


async def test_diag_bootwarm(capsys) -> None:
    stt = _STT()
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile("nico"),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=1.0,
        warm_wait_fallback_s=30.0,
    )
    src: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()

    async def _iter():
        while True:
            c = await src.get()
            if c is None:
                return
            yield c

    async def _feed() -> None:
        i = 0
        while not stop.is_set():
            arr = np.full(1600, 12000 + (i % 80) * 100, dtype=np.int16)
            await src.put(AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0))
            i += 1
            await asyncio.sleep(0.002)

    async def _drain() -> None:
        async for _kw in wake.detect(_iter()):
            pass

    import time as _time

    # Count warm-wait loop iterations by patching asyncio.sleep inside the
    # rolling module for the 0.25 s cadence only.
    import jarvis.speech.rolling_whisper_wake as rww
    iter_counter = {"n": 0}
    _orig_sleep = asyncio.sleep

    async def _counting_sleep(delay, *a, **k):  # noqa: ANN001, ANN002, ANN003
        if abs(delay - 0.25) < 1e-9:
            iter_counter["n"] += 1
        return await _orig_sleep(delay, *a, **k)

    rww.asyncio.sleep = _counting_sleep

    f = asyncio.create_task(_feed())
    d = asyncio.create_task(_drain())
    _t0 = _time.perf_counter()
    await asyncio.sleep(0.4)
    _slept = _time.perf_counter() - _t0
    with capsys.disabled():
        print(f"\nDIAG sleep(0.4) took {_slept:.3f}s real, warm-wait iters={iter_counter['n']}")
        print(f"DIAG cold: calls={stt.calls} feeder_done={f.done()} driver_done={d.done()}")
        if f.done():
            print("feeder exc:", repr(f.exception()))
        if d.done():
            print("driver exc:", repr(d.exception()))
    stt.is_warm = True
    for _ in range(200):
        if stt.calls > 0:
            break
        await asyncio.sleep(0.01)
    rww.asyncio.sleep = _orig_sleep
    with capsys.disabled():
        print(f"DIAG warm: calls={stt.calls} iters={iter_counter['n']} feeder_done={f.done()} driver_done={d.done()}")
        if f.done() and f.exception() is not None:
            print("feeder exc:", repr(f.exception()))
        if d.done() and d.exception() is not None:
            print("driver exc:", repr(d.exception()))
        print("stats:", wake.stats())
        import sys
        print("--- driver stack ---")
        d.print_stack(file=sys.stdout)
    stop.set()
    await src.put(None)
    d.cancel()
    f.cancel()
    for t in (d, f):
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass
