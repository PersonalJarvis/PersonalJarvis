"""A live call tells Jarvis when it pauses and whether a report was voiced."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core import runtime_refs
from jarvis.live import session as session_mod
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.session import LiveVoiceSession

REPORT = '{"agent": "Scout", "status": "done", "report": "Three findings."}'


class Pipeline:
    def __init__(self) -> None:
        self.paused: list[Any] = []
        self.ended: list[Any] = []

    def live_call_paused(self, session: Any) -> None:
        self.paused.append(session)

    def live_call_ended(self, session: Any) -> None:
        self.ended.append(session)


class Connection:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.texts: list[str] = []
        self.answer_sdp = ""

    async def send(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def close(self) -> None:
        return None


@pytest.fixture
def pipeline():
    previous = runtime_refs.get_speech_pipeline()
    fake = Pipeline()
    runtime_refs.set_speech_pipeline(fake)
    try:
        yield fake
    finally:
        runtime_refs.set_speech_pipeline(previous)


def live(cls: type = LiveVoiceSession) -> tuple[Any, Connection]:
    async def _send(_frame: Any) -> None:
        return None

    session = cls(
        session_id="voice",
        bus=None,
        send_json=_send,
        send_binary=_send,
        providers=[SimpleNamespace(name="test")],
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
    )
    connection = Connection()
    session._connection = connection
    return session, connection


def created(response_id: str) -> dict[str, Any]:
    return {"event": {"type": "response.created", "response": {"id": response_id}}}


def finished(response_id: str, kind: str = "response.completed") -> dict[str, Any]:
    return {"event": {"type": kind, "response": {"id": response_id, "usage": {}}}}


def tools(session: LiveVoiceSession) -> None:
    session._tools = SimpleNamespace(
        user_text="", revision=0, language="en", backend_model="backend"
    )
    session._ledger = SimpleNamespace(backend_usage=lambda *args: None)


@pytest.mark.asyncio
async def test_report_is_settled_only_after_the_model_answered_it(pipeline):
    session, connection = live()
    tools(session)
    assert session.ready_for_report
    assert await session.deliver_announcement("Scout reports.", report=REPORT) is True
    assert session.report_pending and not session.ready_for_report
    # A second report never overlaps the first.
    assert await session.deliver_announcement("Coder reports.", report=REPORT) is False
    await session._response(created("r1"))
    assert session.take_report_outcome() == ""
    await session._response(finished("r1"))
    assert session.take_report_outcome() == "completed"
    assert session.take_report_outcome() == ""  # Reported exactly once.
    assert len([f for f in connection.frames if f["type"] == "response.create"]) == 1


@pytest.mark.asyncio
async def test_failed_answer_keeps_the_report_owed(pipeline):
    session, _ = live()
    tools(session)
    await session.deliver_announcement("Scout reports.", report=REPORT)
    await session._response(created("r1"))
    await session._response(finished("r1", "response.failed"))
    assert session.take_report_outcome() == "failed"


@pytest.mark.asyncio
async def test_barge_in_counts_as_heard(pipeline):
    session, _ = live()
    tools(session)
    await session.deliver_announcement("Scout reports.", report=REPORT)
    await session._response(created("r1"))
    await session._response(finished("r1", "response.incomplete"))
    assert session.take_report_outcome() == "completed"


@pytest.mark.asyncio
async def test_unanswered_report_times_out_and_signals_a_pause(pipeline, monkeypatch):
    monkeypatch.setattr(session_mod, "REPORT_START_TIMEOUT_S", 0.01)
    session, _ = live()
    tools(session)
    await session.deliver_announcement("Scout reports.", report=REPORT)
    await asyncio.sleep(0.05)
    assert pipeline.paused == [session]
    assert session.take_report_outcome() == "failed"


@pytest.mark.asyncio
async def test_pause_is_signalled_only_when_nobody_speaks(pipeline):
    session, _ = live()
    session._thinking = True
    await session._emit_indicator({"type": "thinking"})
    assert not pipeline.paused
    session._thinking = False
    await session._emit_indicator({"type": "turn_complete"})
    assert pipeline.paused == [session]


@pytest.mark.asyncio
async def test_user_falling_silent_is_a_pause_even_without_a_phase_change(pipeline):
    session, _ = live()
    await session._receive_media_levels({
        "type": "media_levels", "input_active": True, "playback_active": False,
        "input_level": 0.5, "output_level": 0.0,
    })
    pipeline.paused.clear()
    await session._receive_media_levels({
        "type": "media_levels", "input_active": False, "playback_active": False,
        "input_level": 0.0, "output_level": 0.0,
    })
    assert pipeline.paused == [session]
    session._clear_media_levels()


@pytest.mark.asyncio
async def test_a_finished_tool_opens_the_floor(pipeline):
    session, _ = live()
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    session._track_job(task)
    assert not session.ready_for_report
    gate.set()
    await task
    await asyncio.sleep(0)
    assert pipeline.paused == [session]


@pytest.mark.asyncio
async def test_ending_the_call_reports_once(pipeline):
    session, _ = live()
    await session.end(reason="client_stop")
    session._notify_ended()
    assert pipeline.ended == [session]


@pytest.mark.asyncio
async def test_native_report_settles_on_its_turn_complete(pipeline):
    session, connection = live(NativeLiveVoiceSession)
    session._tools = SimpleNamespace(user_text="", revision=0)
    session._ledger = SimpleNamespace()
    session._transcript = SimpleNamespace(finish=lambda role: None)
    assert await session.deliver_announcement("Scout reports.", report=REPORT) is True
    assert "Three findings." in connection.texts[0]
    assert session.report_pending
    await session._native_event(
        SimpleNamespace(type="audio_delta", audio=SimpleNamespace(pcm=b"\0\0"))
    )
    await session._native_event(SimpleNamespace(type="turn_complete"))
    assert session.take_report_outcome() == "completed"
    assert pipeline.paused  # The turn end is the pause that offers the next result.


@pytest.mark.asyncio
async def test_native_report_started_by_a_tool_call_does_not_time_out(pipeline, monkeypatch):
    monkeypatch.setattr(session_mod, "REPORT_START_TIMEOUT_S", 0.01)
    session, _ = live(NativeLiveVoiceSession)

    async def call(event: Any, revision: int) -> None:
        return None

    session._tools = SimpleNamespace(user_text="", revision=0)
    session._ledger = SimpleNamespace()
    session._call = call
    await session.deliver_announcement("Scout reports.", report=REPORT)
    await session._native_event(SimpleNamespace(type="tool_call"))
    await asyncio.sleep(0.05)
    assert session.report_pending
    assert session.take_report_outcome() == ""


@pytest.mark.asyncio
async def test_unmuting_offers_parked_results(pipeline):
    session, _ = live()
    session._user_muted = False
    await session._apply_input_mute()
    assert pipeline.paused == [session]
