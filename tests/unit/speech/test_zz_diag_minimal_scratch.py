"""Minimal repro #2: fallback warm-up path — which await hangs?"""
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
        self.warm_up_calls = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        self.calls += 1

        class _T:
            text = ""
            confidence = 1.0
            segments = ()

        return _T()

    def warm_up(self) -> None:
        self.warm_up_calls += 1
        self.is_warm = True


async def test_diag_fallback(capsys) -> None:
    stt = _STT()
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile("nico"),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=1.0,
        warm_wait_fallback_s=0.1,
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

    import jarvis.speech.rolling_whisper_wake as rww

    polls = {"n": 0}
    _orig_sleep = asyncio.sleep

    async def _counting_sleep(delay, *a, **k):  # noqa: ANN001, ANN002, ANN003
        if abs(delay - 0.01) < 1e-12:
            polls["n"] += 1
        return await _orig_sleep(delay, *a, **k)

    rww.asyncio.sleep = _counting_sleep
    f = asyncio.create_task(_feed())
    d = asyncio.create_task(_drain())
    _t0 = _time.perf_counter()
    for _ in range(300):
        if stt.calls > 0:
            break
        await asyncio.sleep(0.01)
    _elapsed = _time.perf_counter() - _t0
    rww.asyncio.sleep = _orig_sleep
    with capsys.disabled():
        import sys
        print(
            f"\nDIAG2 warm_up_calls={stt.warm_up_calls} calls={stt.calls} "
            f"is_warm={stt.is_warm} driver_done={d.done()} "
            f"poll_iters={polls['n']} elapsed={_elapsed:.2f}s"
        )
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
