"""A wedged GPU wake model must fall BACK to base/cpu, not rebuild itself.

The background hot-swap attaches the proven base/cpu provider to the turbo
instance as ``_wake_gpu_fallback`` before the ref swap. If the GPU model later
wedges live (a hang the one-off inference probe missed), rebuilding the same
CUDA model would just wedge again — the AP-25 deaf cycle. The self-heal must
instead swap straight back to the still-warm fallback and persist the bad
verdict (``mark_wake_gpu_bad``) so every later build stays on CPU.
"""
from __future__ import annotations

import asyncio
import re

import numpy as np

import jarvis.plugins.stt as stt_pkg
from jarvis.core.protocols import AudioChunk
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


def _loud_chunk(marker: int, n: int = 1600) -> AudioChunk:
    val = 12000 + (marker % 80) * 100
    arr = np.full(n, val, dtype=np.int16)
    return AudioChunk(pcm=arr.tobytes(), sample_rate=16000, timestamp_ns=0)


class _BaseFallbackSTT:
    """The pre-upgrade base/cpu provider — healthy, transcribes instantly."""

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        self.calls += 1

        class _T:
            text = ""
            confidence = 1.0
            segments = ()

        return _T()


class _HangingTurboSTT:
    """The swapped-in GPU model — every transcribe hangs (probe was wrong)."""

    def __init__(self, fallback: _BaseFallbackSTT) -> None:
        self._wake_gpu_fallback = fallback
        self.recovered = 0

    async def transcribe_pcm(self, pcm, sample_rate=16000, language=None):  # noqa: ANN001
        await asyncio.Event().wait()

    def recover(self) -> None:  # must NOT be used — rebuilding re-wedges
        self.recovered += 1


async def test_wedged_gpu_model_swaps_back_to_fallback_and_marks_bad(
    monkeypatch,
) -> None:
    marked = []
    monkeypatch.setattr(stt_pkg, "mark_wake_gpu_bad", lambda: marked.append(True))

    fallback = _BaseFallbackSTT()
    turbo = _HangingTurboSTT(fallback)
    wake = RollingWhisperWake(
        turbo,
        pattern=re.compile(r"nova", re.IGNORECASE),
        poll_interval_s=0.01,
        cooldown_s=0.0,
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
            if fallback.calls >= 1:
                break
            await asyncio.sleep(0.02)
        # The heal swapped back: the fallback is transcribing again...
        assert fallback.calls >= 1, "the base/cpu fallback never took over"
        assert wake._stt is fallback
        # ...the bad verdict was persisted for every later build...
        assert marked, "mark_wake_gpu_bad was never called"
        # ...and the hung CUDA model was NOT rebuilt (the AP-25 deaf cycle).
        assert turbo.recovered == 0
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
