"""Native handoff overlap owns both local capture and any allocated call."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

from jarvis.live import session as module
from jarvis.live.config import LiveConfig
from jarvis.live.timing import StartupTimings
from jarvis.plugins.realtime._live_transport import startup_http_trace


@pytest.fixture
def voice(monkeypatch, tmp_path):
    messages = []

    async def send(message):
        messages.append(message)

    class Connection:
        answer_sdp = "answer"
        session_id = "fake"
        closed = 0
        close_requested = 0

        async def send(self, message):
            if message["type"] == "session.close":
                self.close_requested += 1

        async def receive(self):
            await asyncio.Future()

        async def close(self):
            self.closed += 1

    connection = Connection()

    class Provider:
        name = "test"
        source_timed_audio = True
        requires_close_ack = False

        async def open_session(self, config):
            return connection

    provider = Provider()
    monkeypatch.setattr(module, "get_supervisor_tool_gateway", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_identity", lambda cfg: "")
    monkeypatch.setattr(module.LiveTools, "declarations", lambda self, **kw: [])
    monkeypatch.setattr(module.LiveVoiceSession, "_adopt_desktop_session", lambda self: None)
    config = SimpleNamespace(brain=SimpleNamespace(reply_language="en"),
                             live=LiveConfig(configured=True, backend_model="test"))
    session = module.LiveVoiceSession(session_id="deep-startup", send_binary=send,
                                     send_json=send, providers=[provider], config=config)
    return session, provider, connection, messages


def start(session):
    return asyncio.create_task(session.handle_control({
        "type": "audio_start", "sample_rate": 48000, "webrtc_offer_sdp": "offer",
    }))


async def test_native_handoff_overlaps_provider_but_prefix_precedes_audio_ready(voice, monkeypatch):
    session, provider, connection, messages = voice
    handoff_started, release_handoff, provider_finished = (
        asyncio.Event(), asyncio.Event(), asyncio.Event()
    )

    async def handoff(message):
        handoff_started.set()
        await release_handoff.wait()
        await session._send_json({"type": "input_prefix"})

    async def open_session(config):
        assert handoff_started.is_set()
        await config.on_transport_ready("answer")
        provider_finished.set()
        return connection

    monkeypatch.setattr(session, "_take_startup_input", handoff)
    monkeypatch.setattr(provider, "open_session", open_session)
    task = start(session)
    try:
        await asyncio.wait_for(provider_finished.wait(), 2)
        await asyncio.sleep(0)
        assert session._connection is None  # Owned by the pending handoff until it succeeds.
        assert "control_connected" in session._startup_timing.marks
        assert not task.done()
        assert not any(item["type"] == "audio_ready" for item in messages)
        release_handoff.set()
        await task
        types = [item["type"] for item in messages]
        assert types.index("audio_transport") < types.index("input_prefix")
        assert types.index("input_prefix") < types.index("audio_ready")
    finally:
        release_handoff.set()
        await asyncio.gather(task, return_exceptions=True)
        await session.end()


@pytest.mark.parametrize("cancel_start", [False, True])
async def test_failed_handoff_owns_provider_that_returns_during_cancellation(
    voice, monkeypatch, cancel_start,
):
    session, provider, connection, messages = voice
    opening, handoff_waiting = asyncio.Event(), asyncio.Event()

    async def handoff(message):
        handoff_waiting.set()
        await opening.wait()
        if cancel_start:
            await asyncio.Future()
        raise ValueError("Native handoff failed")

    async def open_session(config):
        opening.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            # A successful remote allocation may race the local cancellation.
            return connection

    monkeypatch.setattr(session, "_take_startup_input", handoff)
    monkeypatch.setattr(provider, "open_session", open_session)
    task = start(session)
    if cancel_start:
        await asyncio.wait_for(opening.wait(), 2)
        await handoff_waiting.wait()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel_start else ValueError):
        await asyncio.wait_for(task, 2)
    assert connection.closed == 1 and connection.close_requested == 1
    assert not any(item["type"] == "audio_ready" for item in messages)
    assert not any(task.get_name() in {"live-input-handoff", "live-provider-open"}
                   for task in asyncio.all_tasks())


async def test_failed_provider_cancels_pending_native_handoff(voice, monkeypatch):
    session, provider, connection, messages = voice
    handoff_closed = asyncio.Event()

    async def handoff(message):
        try:
            await asyncio.Future()
        finally:
            handoff_closed.set()

    async def open_session(config):
        raise ValueError("Allocation refused")

    monkeypatch.setattr(session, "_take_startup_input", handoff)
    monkeypatch.setattr(provider, "open_session", open_session)
    with pytest.raises(ValueError, match="Allocation refused"):
        await asyncio.wait_for(start(session), 2)
    assert handoff_closed.is_set()
    assert connection.closed == 0  # No connection was allocated.
    assert not any(item["type"] == "audio_ready" for item in messages)


@pytest.mark.parametrize("late_allocation", [False, True])
async def test_hangup_alone_closes_allocation_while_handoff_never_finishes(
    voice, monkeypatch, late_allocation,
):
    session, provider, connection, messages = voice
    opening, handoff_cancelled = asyncio.Event(), asyncio.Event()

    async def handoff(message):
        try:
            await asyncio.Future()
        finally:
            handoff_cancelled.set()

    async def open_session(config):
        opening.set()
        if late_allocation:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                # Remote creation can complete concurrently with hangup.
                return connection
        return connection

    monkeypatch.setattr(session, "_take_startup_input", handoff)
    monkeypatch.setattr(provider, "open_session", open_session)
    task = start(session)
    await asyncio.wait_for(opening.wait(), 2)
    await asyncio.wait_for(session.end(), 2)
    assert handoff_cancelled.is_set()
    assert connection.closed == 1 and connection.close_requested == 1
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    await session.end()  # Repeated end() must not close the allocation twice.
    assert connection.closed == 1 and connection.close_requested == 1
    assert session._opening_task is None
    assert not any(task.get_name() in {"live-startup", "live-input-handoff", "live-provider-open"}
                   for task in asyncio.all_tasks())
    assert not any(item["type"] == "audio_ready" for item in messages)


async def test_output_signal_timing_ignores_silent_provider_packets(voice):
    session, provider, connection, messages = voice
    session._ledger = object()
    session._tools = object()
    session._connection = connection
    session._startup_timing = StartupTimings(session.session_id)
    for pcm in (b"", b"\0\0" * 240):
        await session._event({"type": "session.output_audio.delta",
                              "delta": base64.b64encode(pcm).decode()})
    assert "first_output_audio_received" in session._startup_timing.marks
    assert "first_output_signal_received" not in session._startup_timing.marks
    await session._event({"type": "session.output_audio.delta",
                          "delta": base64.b64encode(b"\1\0" * 240).decode()})
    assert "first_output_signal_received" in session._startup_timing.marks
    assert messages[-1] == b"\1\0" * 240


async def test_surface_cancel_during_hangup_cannot_interrupt_remote_cleanup(voice, monkeypatch):
    session, provider, connection, messages = voice
    opened, closing, release_close = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handoff(message):
        await asyncio.Future()

    async def open_session(config):
        opened.set()
        return connection

    async def close():
        closing.set()
        await release_close.wait()
        connection.closed += 1

    monkeypatch.setattr(session, "_take_startup_input", handoff)
    monkeypatch.setattr(provider, "open_session", open_session)
    monkeypatch.setattr(connection, "close", close)
    task = start(session)
    await asyncio.wait_for(opened.wait(), 2)
    ending = asyncio.create_task(session.end())
    try:
        await asyncio.wait_for(closing.wait(), 2)
        task.cancel()  # A disconnected surface races the in-progress hotkey hangup.
        await asyncio.sleep(0)
        assert not ending.done()
        release_close.set()
        await asyncio.wait_for(ending, 2)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert connection.closed == 1 and connection.close_requested == 1
        assert not any(item["type"] == "audio_ready" for item in messages)
    finally:
        release_close.set()
        await asyncio.gather(task, ending, return_exceptions=True)


@pytest.mark.parametrize("stalled_operation", ["send", "close"])
async def test_startup_remote_cleanup_has_a_deadline(
    voice, monkeypatch, caplog, stalled_operation,
):
    session, provider, connection, messages = voice
    opened = asyncio.Event()
    close_attempted = asyncio.Event()
    stalled_cancelled = asyncio.Event()

    async def handoff(message):
        await asyncio.Future()

    async def open_session(config):
        opened.set()
        return connection

    async def stall(*args):
        try:
            await asyncio.Future()
        finally:
            stalled_cancelled.set()

    async def close():
        close_attempted.set()
        if stalled_operation == "close":
            await stall()
        connection.closed += 1

    monkeypatch.setattr(module, "_STARTUP_CLOSE_SEND_TIMEOUT_S", 0.02)
    monkeypatch.setattr(module, "_STARTUP_CLOSE_TIMEOUT_S", 0.02)
    monkeypatch.setattr(session, "_take_startup_input", handoff)
    monkeypatch.setattr(provider, "open_session", open_session)
    monkeypatch.setattr(connection, "close", close)
    if stalled_operation == "send":
        monkeypatch.setattr(connection, "send", stall)
    task = start(session)
    await asyncio.wait_for(opened.wait(), 2)
    await asyncio.wait_for(session.end(), 2)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert close_attempted.is_set() and stalled_cancelled.is_set()
    assert connection.closed == (1 if stalled_operation == "send" else 0)
    assert "could not confirm the allocated call closed" in caplog.text
    assert not any(item["type"] == "audio_ready" for item in messages)


async def test_http_trace_ignores_all_payloads_and_unknown_events():
    phases = []
    trace = startup_http_trace(phases.append)

    class PrivateMetadata(dict):
        def get(self, *args):
            pytest.fail("HTTP trace metadata must never be read")

        def __getitem__(self, key):
            pytest.fail("HTTP trace metadata must never be read")

    metadata = PrivateMetadata(authorization="private", body="private")
    await trace("connection.connect_tcp.complete", metadata)
    await trace("connection.start_tls.complete", metadata)
    await trace("http11.send_request_body.complete", metadata)
    await trace("http11.receive_response_headers.complete", metadata)
    await trace("unknown.private.event", metadata)
    assert phases == ["http_tcp_connected", "http_tls_connected",
                      "http_request_sent", "http_response_headers"]


async def test_subscription_allocation_installs_content_free_http_trace():
    from tests.fakes.fake_subscription_live_wire import FakeSubscriptionLiveWire

    wire = FakeSubscriptionLiveWire()
    phases = []
    config = wire.config()
    config.on_startup_phase = phases.append
    connection = await wire.provider().open_session(config)
    trace = wire.requests[0].extensions["trace"]
    await trace("http2.send_request_body.complete", {"request": "private"})
    assert phases[-1] == "http_request_sent"
    await connection.close()
