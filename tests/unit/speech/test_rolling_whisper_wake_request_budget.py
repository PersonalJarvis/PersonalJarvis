"""Rolling wake only transcribes a window after new audio arrives."""
from __future__ import annotations

import asyncio

import numpy as np

from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake


class _CountingSTT:
    def __init__(self) -> None:
        self.calls = 0
        self.called = asyncio.Event()

    async def transcribe_pcm(
        self,
        pcm: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        self.calls += 1
        self.called.set()
        return Transcript(text="not a wake", language="en", confidence=1.0)


class _NeverMatch:
    def search(self, text: str):  # noqa: ANN001
        return None


async def test_stalled_audio_source_does_not_repeat_identical_stt_request() -> None:
    stt = _CountingSTT()
    wake = RollingWhisperWake(
        stt,
        pattern=_NeverMatch(),
        poll_interval_s=0.01,
        cooldown_s=0.0,
        min_rms=0.0,
        min_peak=0.0,
        save_debug_wavs=False,
    )
    release = asyncio.Event()

    async def source():
        samples = np.full(16_000, 12_000, dtype=np.int16)
        yield AudioChunk(
            pcm=samples.tobytes(),
            sample_rate=16_000,
            timestamp_ns=0,
            channels=1,
        )
        await release.wait()

    async def consume() -> None:
        async for _ in wake.detect(source()):
            pass

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(stt.called.wait(), timeout=1.0)
        await asyncio.sleep(0.08)
        assert stt.calls == 1
    finally:
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
