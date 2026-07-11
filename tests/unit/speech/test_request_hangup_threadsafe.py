"""Regression coverage for JarvisBar-to-pipeline hangup dispatch.

The bar owns a Tk thread, while SpeechPipeline's asyncio events and waiters
belong to the pipeline loop. Directly setting an asyncio.Event from Tk is not
thread-safe: in debug mode it raises, and in production it may not wake the
loop until unrelated I/O arrives. The public request method must marshal once
onto the owner loop and remain synchronous when it is already on that loop.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from jarvis.speech.pipeline import SpeechPipeline


def _pipeline(owner_loop) -> SpeechPipeline:
    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    pipeline._runtime_loop = owner_loop
    pipeline._external_hangup_pending = threading.Event()
    return pipeline


@pytest.mark.asyncio
async def test_foreign_thread_hangup_runs_on_owner_loop() -> None:
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    called_on: list[int] = []
    pipeline = _pipeline(loop)
    pipeline._trigger_voice_hangup = lambda: called_on.append(threading.get_ident())

    caller = threading.Thread(target=pipeline.request_hangup)
    caller.start()
    caller.join(timeout=1.0)
    assert not caller.is_alive()

    await asyncio.sleep(0)
    assert called_on == [loop_thread]


@pytest.mark.asyncio
async def test_owner_loop_hangup_runs_synchronously() -> None:
    loop = asyncio.get_running_loop()
    called_on: list[int] = []
    pipeline = _pipeline(loop)
    pipeline._trigger_voice_hangup = lambda: called_on.append(threading.get_ident())

    pipeline.request_hangup()

    assert called_on == [threading.get_ident()]


class _QueuedLoop:
    def __init__(self) -> None:
        self.callbacks = []

    def is_running(self) -> bool:
        return True

    def call_soon_threadsafe(self, callback) -> None:
        self.callbacks.append(callback)


def test_repeated_external_hangup_queues_only_once() -> None:
    loop = _QueuedLoop()
    calls = []
    pipeline = _pipeline(loop)
    pipeline._trigger_voice_hangup = lambda: calls.append("hangup")

    pipeline.request_hangup()
    pipeline.request_hangup()

    assert len(loop.callbacks) == 1
    loop.callbacks[0]()
    assert calls == ["hangup"]
