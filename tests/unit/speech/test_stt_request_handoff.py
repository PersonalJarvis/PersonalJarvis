"""The final STT request supersedes any in-flight preview request."""
from __future__ import annotations

import asyncio

from jarvis.core.protocols import Transcript
from jarvis.speech.pipeline import SpeechPipeline


class _FinalSTT:
    def __init__(self, preview_released: asyncio.Event) -> None:
        self._preview_released = preview_released
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes) -> Transcript:
        assert self._preview_released.is_set(), (
            "final STT overlapped the stale preview request"
        )
        self.calls += 1
        return Transcript(text="ready", language="en", confidence=1.0)


async def test_final_stt_drains_inflight_preview_before_dispatch() -> None:
    preview_started = asyncio.Event()
    preview_released = asyncio.Event()

    async def stale_preview() -> None:
        preview_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            preview_released.set()

    preview_task = asyncio.create_task(stale_preview())
    await preview_started.wait()

    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    pipeline._probe_task = preview_task
    pipeline._probe_in_flight = True
    pipeline._utterance_stt = _FinalSTT(preview_released)
    pipeline._stt_final_timeout_s = 1.0

    result = await pipeline._transcribe_final(b"\x01\x00" * 512)

    assert result is not None
    assert result.text == "ready"
    assert pipeline._utterance_stt.calls == 1
    assert preview_task.cancelled()
    assert pipeline._probe_task is None
    assert pipeline._probe_in_flight is False
