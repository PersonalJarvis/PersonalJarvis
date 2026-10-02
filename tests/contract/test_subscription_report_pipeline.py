"""Real browser-call registration, report receipts, and pipeline loop ownership."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.core import runtime_refs
from jarvis.core.events import AnnouncementRequested, BrowserVoiceRequested
from jarvis.live import runtime
from jarvis.live.config import LiveConfig
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState
from tests.fakes.fake_subscription_session import (
    ScriptedSubscriptionReasoning,
    SubscriptionConnection,
    SubscriptionGateway,
    completed_response,
    spoken_result,
    subscription_provider,
)


def _pipeline() -> SpeechPipeline:
    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    pipeline._runtime_loop = asyncio.get_running_loop()
    pipeline._active_voice_mode = "realtime"
    pipeline._active_realtime_handle = None
    pipeline._turn_state = TurnTakingState.IDLE
    pipeline._muted = False
    pipeline._deferred_announcements = []
    pipeline._agent_reply_inflight = None
    pipeline._agent_reply_inflight_text = ""
    pipeline._agent_reply_retries = []
    pipeline._agent_reply_retry_task = None
    pipeline._delegation_result_task = None
    return pipeline


def _report(text: str = "Verified task result") -> AnnouncementRequested:
    return AnnouncementRequested(
        source_layer="agentic_ide.readback",
        kind="subagent",
        text=text,
        report=text,
        language="en",
    )


async def _jobs(session) -> None:
    if session._jobs:
        await asyncio.gather(*tuple(session._jobs), return_exceptions=True)
    await asyncio.sleep(0)


async def _caption(session, segment, text, *, final=True, role="output") -> None:
    await session._event(
        {
            "type": f"session.{role}_transcript.{'done' if final else 'delta'}",
            "segment_id": segment,
            "transcript": text,
            "snapshot": True,
            "is_final": final,
            "start_ms": 1,
            "end_ms": 2,
        }
    )


@pytest.fixture
async def browser_call(monkeypatch, tmp_path):
    import jarvis.live.subscription as module

    pipeline = _pipeline()
    monkeypatch.setattr(runtime_refs, "get_speech_pipeline", lambda: pipeline)
    reasoning = ScriptedSubscriptionReasoning(
        [
            completed_response("report-1", [spoken_result("Verified task result")]),
            completed_response("report-2", [spoken_result("Second result")]),
        ]
    )
    monkeypatch.setattr(module, "SubscriptionReasoning", lambda **_kwargs: reasoning)
    messages = []

    async def send(message):
        messages.append(message)

    session = module.SubscriptionLiveVoiceSession(
        session_id="report-pipeline-contract",
        providers=[subscription_provider()],
        config=SimpleNamespace(
            live=LiveConfig(configured=True, subscription_backend_model="fixture-model"),
            brain=SimpleNamespace(reply_language="en"),
        ),
        send_binary=send,
        send_json=send,
    )
    session._ledger = LiveLedger(tmp_path / "reports.sqlite3")
    session._connection = SubscriptionConnection()
    session._tools = LiveTools(
        SubscriptionGateway(),
        session._ledger,
        session.session_id,
        language="en",
        backend_model="fixture-model",
        model_selection=session._tool_model_selection,
    )
    started = asyncio.Event()
    hangup = asyncio.Event()

    async def publish(event):
        if isinstance(event, BrowserVoiceRequested) and event.action == "start":
            runtime.register(session)
            started.set()

    lifecycle = asyncio.create_task(
        runtime.run_browser_call(SimpleNamespace(publish=publish), hangup)
    )
    await asyncio.wait_for(started.wait(), 2)
    try:
        yield pipeline, session, reasoning, messages
    finally:
        hangup.set()
        await asyncio.wait_for(lifecycle, 3)
        await session.end()
        await _jobs(session)
        for name in ("_agent_reply_retry_task", "_delegation_result_task"):
            task = getattr(pipeline, name, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        runtime.unregister(session.session_id)


async def test_browser_report_is_forwarded_without_native_handle_and_waits_for_speech(browser_call):
    pipeline, session, reasoning, _ = browser_call
    event = _report()
    assert pipeline._active_realtime_handle is None
    assert not pipeline._agent_reply_needs_session()
    assert await pipeline._deliver_announcement_via_realtime(event, text=event.text, language="en")
    await _jobs(session)
    assert len(reasoning.requests) == 1
    assert pipeline._agent_reply_inflight is event
    assert session.report_pending
    await _caption(session, "real-report", event.text)
    assert pipeline._agent_reply_inflight is None
    assert not pipeline._agent_reply_retries
    assert session.take_report_outcome() == ""  # The pipeline consumed the real receipt.


async def test_straddling_filler_does_not_settle_the_pipeline_report(browser_call):
    pipeline, session, reasoning, _ = browser_call
    reasoning.block = asyncio.Event()
    event = _report()
    assert await pipeline._deliver_announcement_via_realtime(event, text=event.text, language="en")
    await reasoning.started.wait()
    await _caption(session, "filler", "Let me", final=False)
    reasoning.block.set()
    await _jobs(session)
    await _caption(session, "filler", "Let me check")
    assert pipeline._agent_reply_inflight is event
    assert session.report_pending
    await _caption(session, "actual-report", event.text)
    assert pipeline._agent_reply_inflight is None
    assert not pipeline._agent_reply_retries


@pytest.mark.parametrize("failure", ["provider", "cancel", "hangup"])
async def test_unvoiced_report_stays_owed_without_duplicate_inference(browser_call, failure):
    pipeline, session, reasoning, _ = browser_call
    event = _report()
    if failure == "provider":
        reasoning.rounds = [[{"type": "response.failed"}]]
    else:
        reasoning.block = asyncio.Event()
    assert await pipeline._deliver_announcement_via_realtime(event, text=event.text, language="en")
    if failure == "cancel":
        await session.handle_control({"type": "cancel_work"})
    elif failure == "hangup":
        await session.end()
    await _jobs(session)
    assert pipeline._agent_reply_inflight is None
    assert pipeline._agent_reply_retries == [event]
    assert not pipeline._deferred_announcements
    count = len(reasoning.requests)
    pipeline.live_call_paused(session)
    await asyncio.sleep(0)
    assert len(reasoning.requests) == count
    assert pipeline._agent_reply_retries == [event]


async def test_completed_report_flushes_one_queued_report_at_real_pause(browser_call):
    pipeline, session, reasoning, _ = browser_call
    first, second = _report(), _report("Second result")
    forwarded = []

    async def receive(event):
        forwarded.append(event)
        assert await pipeline._deliver_announcement_via_realtime(
            event, text=event.text, language="en"
        )

    # Keep this contract focused on delivery; filtering/wording is covered by
    # test_realtime_announcement_bridge's real _on_announcement integration.
    pipeline._on_announcement = receive
    assert await pipeline._deliver_announcement_via_realtime(first, text=first.text, language="en")
    pipeline._defer_agent_reply(second)
    await _jobs(session)
    await _caption(session, "first-report", first.text)
    await asyncio.wait_for(pipeline._agent_reply_retry_task, 2)
    await _jobs(session)
    assert forwarded == [second]
    assert len(reasoning.requests) == 2
    assert pipeline._agent_reply_inflight is second
    await _caption(session, "second-report", second.text)
    pipeline.live_call_paused(session)
    await asyncio.sleep(0)
    assert forwarded == [second]
    assert pipeline._agent_reply_inflight is None


async def test_registered_browser_operations_and_receipts_use_their_owner_loops():
    pipeline = _pipeline()
    owner = asyncio.new_event_loop()
    thread = threading.Thread(target=owner.run_forever, name="fixture-live-owner")
    thread.start()
    calls, receipts, settlements = [], [], []
    session = SimpleNamespace(session_id="cross-loop-report", is_active=True, report_pending=False)

    async def deliver(**_kwargs):
        calls.append(asyncio.get_running_loop())
        session.report_pending = True
        return True

    def take():
        receipts.append(asyncio.get_running_loop())
        session.report_pending = False
        return "completed"

    async def end(**_kwargs):
        calls.append(asyncio.get_running_loop())
        session.is_active = False
        runtime.unregister(session.session_id)
        pipeline.live_call_ended(session)

    session.deliver_announcement, session.take_report_outcome, session.end = deliver, take, end
    settle = pipeline._settle_agent_reply

    def record_settle(*, completed):
        settlements.append(asyncio.get_running_loop())
        settle(completed=completed)

    pipeline._settle_agent_reply = record_settle

    async def register():
        runtime.register(session)

    async def pause():
        pipeline.live_call_paused(session)

    try:
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(register(), owner))
        event = _report()
        assert await pipeline._deliver_announcement_via_realtime(
            event, text=event.text, language="en"
        )
        assert calls == [owner]
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(pause(), owner))
        await asyncio.sleep(0)
        assert receipts == [owner]
        assert settlements == [asyncio.get_running_loop()]
        assert pipeline._agent_reply_inflight is None
        await runtime.close_all()
        assert calls == [owner, owner]
        assert not any(item is session for item in runtime.active())
    finally:
        runtime.unregister(session.session_id)
        owner.call_soon_threadsafe(owner.stop)
        await asyncio.to_thread(thread.join, 2)
        assert not thread.is_alive()
        owner.close()


@pytest.mark.parametrize("browser", [False, True])
async def test_legacy_boolean_handle_does_not_wait_for_subscription_receipts(browser):
    pipeline = _pipeline()
    calls = []

    async def deliver(**kwargs):
        calls.append(kwargs)
        return True

    session = SimpleNamespace(
        session_id="legacy-boolean-report",
        is_active=True,
        deliver_announcement=deliver,
    )
    if browser:
        runtime.register(session)
    else:
        pipeline._active_realtime_handle = session
    try:
        first, second = _report(), _report("Another report")
        assert await pipeline._deliver_announcement_via_realtime(
            first, text=first.text, language="en"
        )
        if browser:
            # The API browser has no report receipt callbacks; its legacy
            # acceptance must never create a subscription receipt wait.
            assert pipeline._agent_reply_inflight is None
        else:
            # Native agent readbacks still settle on their existing audio
            # completion path, even without subscription receipt methods.
            assert pipeline._agent_reply_inflight is first
            pipeline._settle_agent_reply(completed=True)
        assert await pipeline._deliver_announcement_via_realtime(
            second, text=second.text, language="en"
        )
        assert len(calls) == 2
    finally:
        runtime.unregister(session.session_id)
