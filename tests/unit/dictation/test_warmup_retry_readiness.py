"""Failed or retired native engines must re-enter the patient warm-up path."""

import asyncio
import threading

import pytest

from jarvis.dictation.local_final import LocalEngineUnavailable, LocalFinalSTT
from jarvis.speech.pipeline import SpeechPipeline


class RecoveringProvider:
    def __init__(self):
        self.is_warm = False
        self.calls = 0

    def warm_up(self):
        self.calls += 1
        self.is_warm = self.calls > 1


async def test_failed_warmup_is_retried_before_transcription():
    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    provider = RecoveringProvider()
    pipeline._schedule_dictation_warmup(provider)
    await pipeline._join_dictation_warmup(provider)
    assert not provider.is_warm
    assert getattr(pipeline, "_dictation_warmup_succeeded_provider", None) is None

    pipeline._schedule_dictation_warmup(provider)
    await pipeline._join_dictation_warmup(provider)
    assert provider.is_warm
    assert provider.calls == 2
    pipeline._schedule_dictation_warmup(provider)
    await pipeline._join_dictation_warmup(provider)
    assert provider.calls == 2


async def test_retired_engine_is_warmed_again_after_previous_success():
    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    provider = RecoveringProvider()
    provider.calls = 1
    pipeline._schedule_dictation_warmup(provider)
    await pipeline._join_dictation_warmup(provider)
    assert provider.is_warm
    provider.is_warm = False
    pipeline._schedule_dictation_warmup(provider)
    await pipeline._join_dictation_warmup(provider)
    assert provider.is_warm
    assert provider.calls == 3


async def test_second_press_during_abandoned_cold_load_does_not_kill_new_worker(monkeypatch):
    import jarvis.dictation.local_preview as preview

    started, release = threading.Event(), threading.Event()

    class Worker:
        device = "cpu"
        compute = "int8"
        closed = False

        def close(self):
            self.closed = True

    worker = Worker()

    def spawn(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return worker

    monkeypatch.setattr(preview, "_spawn_worker_model", spawn)
    monkeypatch.setattr(preview, "faster_whisper_available", lambda: True)
    provider = LocalFinalSTT(allow_cpu=True)
    # The first press was cancelled while its native build kept running.
    provider._call_lock.acquire()
    warm = asyncio.create_task(asyncio.to_thread(provider.warm_up))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(LocalEngineUnavailable, match="still starting"):
            await asyncio.wait_for(provider.transcribe_pcm(b"\0\0"), timeout=0.2)
    finally:
        release.set()
        await warm
        provider._call_lock.release()
    assert provider.is_warm
    assert not worker.closed
