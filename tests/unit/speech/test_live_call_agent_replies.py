"""Agent results reach a browser/native live call at its next pause.

Live 2026-10-02: Jarvis-Scout finished its research during a GPT-Live call,
but the speech pipeline never knew the call existed (no realtime handle, the
engine flag stuck on "transitioning"), so the result waited in silence until
the call ended and the user had to ask for it.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.delegation import result_announcement
from jarvis.core.events import AnnouncementRequested
from jarvis.live import runtime as live_runtime
from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState
from tests.unit.speech.test_realtime_announcement_bridge import _FakePlayer, _FakeTTS


class FakeLiveCall:
    """The parts of ``jarvis.live.session.LiveVoiceSession`` the pipeline uses."""

    def __init__(self, session_id: str = "live-1") -> None:
        self.session_id = session_id
        self.is_active = True
        self.busy = False
        self.calls: list[dict[str, object]] = []
        self.report_state = ""

    @property
    def ready_for_report(self) -> bool:
        return self.is_active and not self.busy and not self.report_pending

    @property
    def report_pending(self) -> bool:
        return self.report_state in {"sent", "started"}

    def take_report_outcome(self) -> str:
        if self.report_state not in {"done", "failed"}:
            return ""
        outcome = "completed" if self.report_state == "done" else "failed"
        self.report_state = ""
        return outcome

    async def deliver_announcement(self, text: str, **kwargs: object) -> bool:
        if kwargs.get("report") and not self.ready_for_report:
            return False
        self.calls.append({"text": text, **kwargs})
        if kwargs.get("report"):
            self.report_state = "sent"
        return True

    def finish_report(self, *, voiced: bool = True) -> None:
        self.report_state = "done" if voiced else "failed"


@pytest.fixture(autouse=True)
def quick_batch(monkeypatch):
    monkeypatch.setattr("jarvis.core.delegation.BATCH_WINDOW_S", 0.005)


@pytest.fixture
async def live():
    call = FakeLiveCall()
    live_runtime.register(call)
    try:
        yield call
    finally:
        live_runtime.unregister(call.session_id)


def browser_call_pipeline() -> tuple[SpeechPipeline, _FakeTTS, _FakePlayer]:
    """The pipeline exactly as a browser call leaves it: no handle, still
    'transitioning', LISTENING for the whole call."""
    tts = _FakeTTS()
    player = _FakePlayer()
    pipe = SpeechPipeline(tts=tts, enable_whisper_wake=False)
    pipe._player = player  # type: ignore[assignment]
    pipe._active_voice_mode = "realtime"
    pipe._active_realtime_handle = None
    pipe._voice_engine_transitioning = True
    pipe._turn_state = TurnTakingState.LISTENING
    return pipe, tts, player


def result(name: str = "Jarvis-Scout", status: str = "done") -> AnnouncementRequested:
    return result_announcement(
        source="society.lead", request_id=name, name=name,
        request=f"Research for {name}", status=status,
        report=f"Findings from {name}", language="de",
    )


async def settle() -> None:
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_scout_result_reaches_an_idle_browser_call(live):
    pipe, tts, player = browser_call_pipeline()
    await pipe._on_announcement(result())
    await settle()
    assert len(live.calls) == 1
    assert "Research for Jarvis-Scout" in str(live.calls[0]["report"])
    # Never a second, classic voice inside the live call.
    assert not tts.calls and not player.plays


@pytest.mark.asyncio
async def test_result_waits_for_the_pause_then_is_offered_without_a_user_turn(live):
    pipe, tts, _ = browser_call_pipeline()
    live.busy = True  # Jarvis is answering something else right now.
    await pipe._on_announcement(result())
    await settle()
    assert not live.calls
    live.busy = False
    pipe.live_call_paused(live)
    await settle()
    assert len(live.calls) == 1
    assert not tts.calls


@pytest.mark.asyncio
async def test_simultaneous_results_are_one_report_and_later_ones_follow(live):
    pipe, _, _ = browser_call_pipeline()
    live.busy = True
    await asyncio.gather(
        pipe._on_announcement(result("Scout")),
        pipe._on_announcement(result("Coder", "blocked")),
    )
    live.busy = False
    pipe.live_call_paused(live)
    await settle()
    assert len(live.calls) == 1
    report = str(live.calls[0]["report"])
    assert "Research for Scout" in report and "Research for Coder" in report
    # A third agent finishes while the first report is being spoken.
    await pipe._on_announcement(result("Mailbox"))
    await settle()
    assert len(live.calls) == 1
    live.finish_report()
    pipe.live_call_paused(live)
    await settle()
    assert len(live.calls) == 2
    assert "Research for Mailbox" in str(live.calls[1]["report"])
    assert pipe._agent_reply_inflight is not None
    live.finish_report()
    pipe.live_call_paused(live)
    await settle()
    assert pipe._agent_reply_inflight is None
    assert len(live.calls) == 2  # Nothing is read twice.


@pytest.mark.asyncio
async def test_unvoiced_report_waits_for_the_next_call_without_repeating_inference(live):
    pipe, _, _ = browser_call_pipeline()
    await pipe._on_announcement(result())
    await settle()
    assert len(live.calls) == 1
    live.finish_report(voiced=False)
    pipe.live_call_paused(live)
    for _ in range(pipeline_mod._LIVE_REPLY_MAX_ATTEMPTS):
        pipe.live_call_paused(live)
        await asyncio.sleep(0.2)
    assert len(live.calls) == 1
    assert pipe._agent_reply_inflight is None
    assert len(pipe._agent_reply_retries) == 1


@pytest.mark.asyncio
async def test_hangup_mid_report_keeps_it_owed_for_the_next_call():
    pipe, _, _ = browser_call_pipeline()
    first = FakeLiveCall("call-1")
    live_runtime.register(first)
    try:
        await pipe._on_announcement(result())
        await settle()
        assert len(first.calls) == 1
        first.is_active = False
        pipe.live_call_ended(first)
    finally:
        live_runtime.unregister(first.session_id)
    assert pipe._agent_reply_inflight is None
    assert len(pipe._agent_reply_retries) == 1
    second = FakeLiveCall("call-2")
    live_runtime.register(second)
    try:
        pipe.live_call_paused(second)
        await asyncio.sleep(0.2)
        assert len(second.calls) == 1
        assert second.calls[0]["report"] == first.calls[0]["report"]
    finally:
        live_runtime.unregister(second.session_id)


@pytest.mark.asyncio
async def test_finished_report_is_not_repeated_on_the_next_call():
    pipe, _, _ = browser_call_pipeline()
    first = FakeLiveCall("call-1")
    live_runtime.register(first)
    try:
        await pipe._on_announcement(result())
        await settle()
        first.finish_report()
        first.is_active = False
        pipe.live_call_ended(first)
    finally:
        live_runtime.unregister(first.session_id)
    assert not pipe._agent_reply_retries
    second = FakeLiveCall("call-2")
    live_runtime.register(second)
    try:
        pipe.live_call_paused(second)
        await asyncio.sleep(0.2)
        assert not second.calls
    finally:
        live_runtime.unregister(second.session_id)


@pytest.mark.asyncio
async def test_result_finished_between_calls_is_offered_when_the_next_call_pauses():
    pipe, _, _ = browser_call_pipeline()
    pipe._turn_state = TurnTakingState.IDLE
    pipe._active_voice_mode = None
    await pipe._on_announcement(result())
    await settle()
    call = FakeLiveCall("standalone")
    live_runtime.register(call)
    try:
        pipe.live_call_paused(call)
        await settle()
        assert len(call.calls) == 1
    finally:
        live_runtime.unregister(call.session_id)


@pytest.mark.asyncio
async def test_progress_and_completion_lines_go_to_the_live_call_not_classic_tts(live):
    pipe, tts, player = browser_call_pipeline()
    live.busy = True
    await pipe._on_announcement(AnnouncementRequested(
        text="Almost there.", language="en", kind="progress", source_layer="missions.voice",
    ))
    completion = AnnouncementRequested(
        text="The artifact is ready.", language="en", kind="completion",
        source_layer="missions.voice", report="Artifact report",
    )
    await pipe._on_announcement(completion)
    assert not tts.calls and not player.plays
    assert [call["text"] for call in live.calls] == ["Almost there."]
    live.busy = False
    pipe.live_call_paused(live)
    await asyncio.sleep(0.2)
    assert live.calls[-1]["report"] == "Artifact report"
    assert not tts.calls and not player.plays


@pytest.mark.asyncio
async def test_muted_live_call_keeps_the_result_until_unmuted(live):
    pipe, _, _ = browser_call_pipeline()
    pipe._muted = True
    await pipe._on_announcement(result())
    await settle()
    assert not live.calls
    pipe._muted = False
    pipe.live_call_paused(live)
    await settle()
    assert len(live.calls) == 1


async def test_desktop_realtime_handle_still_wins_over_a_live_call(live):
    pipe, _, _ = browser_call_pipeline()
    handle = object()
    pipe._active_realtime_handle = handle
    assert pipe._realtime_voice_handle() is handle
    pipe._active_realtime_handle = None
    assert pipe._realtime_voice_handle() is live


async def test_a_desktop_driven_live_session_keeps_the_desktop_bookkeeping():
    pipe, _, _ = browser_call_pipeline()
    desktop = FakeLiveCall("desktop-call")
    desktop.surface = "desktop"
    live_runtime.register(desktop)
    try:
        assert pipe._live_call() is None
        pipe._agent_reply_inflight = result()
        desktop.finish_report(voiced=False)
        pipe.live_call_paused(desktop)
        pipe.live_call_ended(desktop)
        # The desktop path's turn_complete settles it, not the pause signal.
        assert pipe._agent_reply_inflight is not None
        assert desktop.report_state == "failed"
    finally:
        live_runtime.unregister(desktop.session_id)


@pytest.mark.asyncio
async def test_a_call_that_refuses_at_a_pause_is_not_hammered(live):
    pipe, _, _ = browser_call_pipeline()
    refusals = 0

    async def refuse(text: str, **kwargs: object) -> bool:
        nonlocal refusals
        refusals += 1
        raise ConnectionError("send failed")

    live.deliver_announcement = refuse  # type: ignore[method-assign]
    await pipe._on_announcement(result())
    await asyncio.sleep(0.5)
    assert refusals <= 2
    for _ in range(pipeline_mod._LIVE_REPLY_MAX_ATTEMPTS + 1):
        pipe.live_call_paused(live)
        await asyncio.sleep(0.2)
    assert refusals <= 2 + pipeline_mod._LIVE_REPLY_MAX_ATTEMPTS
    assert len(pipe._agent_reply_retries) == 1  # Handed to the next call.


@pytest.mark.asyncio
async def test_agent_reply_without_a_report_still_waits_for_a_pause(live):
    pipe, _, _ = browser_call_pipeline()
    live.busy = True
    routine = AnnouncementRequested(
        source_layer="tasks.runner", kind="completion", language="en",
        text="Nala reports: the draft is ready.", detail="agent=Nala",
    )
    await pipe._on_announcement(routine)
    await settle()
    assert not live.calls
    live.busy = False
    pipe.live_call_paused(live)
    await asyncio.sleep(0.2)
    assert live.calls[0]["report"] == "Nala reports: the draft is ready."
