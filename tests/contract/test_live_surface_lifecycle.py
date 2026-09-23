"""Live media state reaches desktop surfaces and wake ownership cannot linger."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import BrowserVoiceRequested, SystemStateChanged
from jarvis.live import runtime
from jarvis.live.session import LiveVoiceSession


@pytest.mark.asyncio
async def test_rtp_playback_reaches_native_bar_without_sideband_audio():
    bus = EventBus()
    states = []
    frames = []

    async def changed(event):
        states.append(event.new_state)
        assert event.trace_id == session._indicator_trace_id

    async def send(frame):
        frames.append(frame)

    bus.subscribe(SystemStateChanged, changed)
    session = LiveVoiceSession(
        session_id="live-indicators",
        bus=bus,
        send_json=send,
        send_binary=send,
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        providers=[SimpleNamespace(name="test")],
    )
    await session._publish_phase("connecting")
    await session._publish_phase()
    await session._note_thinking()
    session._response_revisions["work"] = 0
    await session.handle_control({"type": "playback_state", "active": True})
    await session.handle_control({"type": "playback_state", "active": True})
    await session.handle_control({"type": "playback_state", "active": False})
    session._response_revisions.clear()
    await session.handle_control({"type": "playback_state", "active": True})
    await session.handle_control({"type": "playback_state", "active": False})
    await session.end()
    await session._publish_phase("listening")
    await session.handle_control({"type": "playback_state", "active": True})
    assert states == [
        "CONNECTING",
        "LISTENING",
        "THINKING",
        "SPEAKING",
        "THINKING",
        "SPEAKING",
        "LISTENING",
        "IDLE",
    ]
    assert {"type": "thinking"} in frames


@pytest.mark.asyncio
async def test_expired_wake_retracts_the_browser_request(monkeypatch):
    monkeypatch.setattr(runtime, "_active", {})
    monkeypatch.setattr(runtime, "_watchers", set())
    requests = []
    bus = EventBus()

    async def requested(event):
        requests.append(event.action)

    bus.subscribe(BrowserVoiceRequested, requested)
    assert await runtime.run_browser_call(bus, asyncio.Event(), timeout_s=0.01) == "error"
    assert requests == ["start", "stop"]
    assert not runtime._watchers


@pytest.mark.asyncio
async def test_cancelled_wake_never_starts_a_late_call(monkeypatch):
    monkeypatch.setattr(runtime, "_active", {})
    monkeypatch.setattr(runtime, "_watchers", set())
    requests = []
    bus = EventBus()
    started = asyncio.Event()

    async def requested(event):
        requests.append(event.action)
        started.set()

    bus.subscribe(BrowserVoiceRequested, requested)
    task = asyncio.create_task(runtime.run_browser_call(bus, asyncio.Event()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert requests == ["start", "stop"]
    assert not runtime._watchers


@pytest.mark.asyncio
async def test_hangup_before_handoff_does_not_start_browser(monkeypatch):
    monkeypatch.setattr(runtime, "_active", {})
    monkeypatch.setattr(runtime, "_watchers", set())
    requests = []
    bus = EventBus()

    async def requested(event):
        requests.append(event.action)

    bus.subscribe(BrowserVoiceRequested, requested)
    hangup = asyncio.Event()
    hangup.set()
    assert await runtime.run_browser_call(bus, hangup) == "hotkey"
    assert requests == ["stop"]


@pytest.mark.asyncio
async def test_new_input_recovers_after_task_cancellation(tmp_path):
    from jarvis.live.state import LiveLedger
    from jarvis.live.tools import LiveTools

    ledger = LiveLedger(tmp_path / "live.sqlite3")
    try:
        tools = LiveTools(SimpleNamespace(), ledger, "s", language="en", backend_model="chosen")
        old_token = tools.cancel_token
        await tools.cancel_work()
        assert old_token.is_cancelled()
        assert not tools.accepting
        assert tools.revision == 1
        tools.accept_new_input()
        assert tools.accepting
        assert not tools.cancel_token.is_cancelled()
        assert old_token.is_cancelled()
    finally:
        ledger.close()
