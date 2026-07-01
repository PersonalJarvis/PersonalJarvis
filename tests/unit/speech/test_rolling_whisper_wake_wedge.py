"""The wedged-model self-heal must fire FAST so the wake is not deaf for long.

Live-log evidence (2026-06-30, data/jarvis_desktop.log): the base/cpu wake model
wedged (``model.transcribe`` hung) dozens of times a day. Each wedge left the
wake totally deaf until a run of consecutive transcribe failures triggered a
model rebuild — at the old threshold of 5 that dead window ran long enough to
swallow several spoken wakes ("say it 2-3 times"). Recovering after just 2
consecutive failures (with zero successes in between — the unambiguous wedge
signature) shortens the deaf window while a single transient overlap, which
clears on the next successful poll, never trips it.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np

from jarvis.core.protocols import AudioChunk
from jarvis.speech.rolling_whisper_wake import (
    _WEDGE_RECOVER_AFTER_FAILS,
    RollingWhisperWake,
)


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    val = 12000 + (marker % 80) * 100
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


def test_wedge_recover_threshold_is_snappy() -> None:
    assert _WEDGE_RECOVER_AFTER_FAILS <= 2, (
        "a wedged wake model must self-heal within ~2 failures so the wake is not "
        "deaf for tens of seconds"
    )


class _HangingSTT:
    """Every transcribe hangs forever; records how many times recover() ran and
    at which failure count it first fired."""

    def __init__(self) -> None:
        self.calls = 0
        self.recovered = 0
        self.calls_at_first_recover: int | None = None

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        self.calls += 1
        await asyncio.Event().wait()  # the wedge — abandoned by the per-call timeout

    def recover(self) -> None:
        self.recovered += 1
        if self.calls_at_first_recover is None:
            self.calls_at_first_recover = self.calls


async def test_wedge_self_heals_within_the_snappy_threshold() -> None:
    stt = _HangingSTT()
    wake = RollingWhisperWake(
        stt,
        pattern=re.compile(r"nico", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        save_debug_wavs=False,
        min_rms=0.0,
        min_peak=0.0,
        transcribe_timeout_s=0.02,  # fast so the wedge is detected quickly
    )
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

    async def _drive() -> None:
        async for _kw in wake.detect(_iter()):
            pass

    feeder = asyncio.create_task(_feed())
    drive = asyncio.create_task(_drive())
    try:
        for _ in range(300):
            if stt.recovered >= 1:
                break
            await asyncio.sleep(0.02)
        assert stt.recovered >= 1, "a wedged model never self-healed"
        assert stt.calls_at_first_recover is not None
        assert stt.calls_at_first_recover <= 2, (
            f"self-heal took {stt.calls_at_first_recover} failures — too slow"
        )
    finally:
        stop.set()
        await src.put(None)
        drive.cancel()
        feeder.cancel()
        for t in (drive, feeder):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
