"""Finished text is independent of a stalled observer, with ordered recovery."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted, DictationTranscript
from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.pipeline import SpeechPipeline


def _pipeline(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_DICTATION_EVENT_TIMEOUT_S", 0.03)
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = EventBus()
    pipe._dictation_cfg = DictationConfig(polish=False)
    recorded = []

    async def record(**values):
        recorded.append(values)

    pipe._record_dictation = record
    return pipe, recorded


async def _finish(pipe, *, target="chat"):
    return await pipe._finish_dictation(
        raw_text="These words must arrive.", language="en", duration_s=2,
        target=target, hung_up=False,
    )


async def test_healthy_chat_receives_final_and_completion_beside_stalled_observer(monkeypatch):
    pipe, recorded = _pipeline(monkeypatch)
    healthy = []
    cancelled = []

    async def receive(event):
        healthy.append(event)

    async def stalled(event):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(type(event))

    pipe._bus.subscribe_all(receive)
    pipe._bus.subscribe_all(stalled)
    result = await asyncio.wait_for(_finish(pipe), timeout=1)

    assert result == "These words must arrive."
    assert [type(event) for event in healthy] == [DictationTranscript, DictationCompleted]
    assert cancelled == [DictationTranscript, DictationCompleted]
    assert recorded[0]["cleaned"] == result
    assert "final_notification:observer_timeout" in recorded[0]["stt_audit"]
    assert recorded[0]["stt_audit"] == healthy[-1].stt_audit


async def test_external_insertion_does_not_wait_for_notification(monkeypatch):
    pipe, recorded = _pipeline(monkeypatch)
    inserted = threading.Event()
    observer_started = asyncio.Event()
    observer_release = asyncio.Event()
    monkeypatch.setattr(pipeline_mod, "_DICTATION_EVENT_TIMEOUT_S", 1)

    async def stalled(event):
        if isinstance(event, DictationTranscript):
            observer_started.set()
            await observer_release.wait()

    def insert(text):
        inserted.set()
        return SimpleNamespace(status="inserted", detail="", method="fake")

    pipe._bus.subscribe_all(stalled)
    pipe._insert_dictation = insert
    task = asyncio.create_task(_finish(pipe, target="insert"))
    try:
        await asyncio.wait_for(observer_started.wait(), timeout=1)
        assert await asyncio.to_thread(inserted.wait, 0.5)
        assert not task.done()
    finally:
        observer_release.set()
        await asyncio.wait_for(task, timeout=1)
    assert recorded[0]["outcome_name"] == "inserted"


async def test_an_insertion_exception_still_preserves_text_and_terminal_event(monkeypatch):
    pipe, recorded = _pipeline(monkeypatch)
    events = []

    async def receive(event):
        events.append(event)

    def insert(text):
        raise RuntimeError("fake unavailable desktop")

    pipe._bus.subscribe_all(receive)
    pipe._insert_dictation = insert
    result = await _finish(pipe, target="insert")
    assert result == "These words must arrive."
    assert events[-1].outcome == "failed"
    assert recorded[0]["cleaned"] == result
    assert recorded[0]["outcome_name"] == "failed"


async def test_cancellation_retires_notification_before_next_turn(monkeypatch):
    pipe, _recorded = _pipeline(monkeypatch)
    entered, retired = asyncio.Event(), asyncio.Event()

    async def stalled(event):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            retired.set()

    pipe._bus.subscribe_all(stalled)
    task = asyncio.create_task(_finish(pipe))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert retired.is_set()
