"""Readiness releases real input without waiting for optional status observers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import RealtimeSessionReady, VoiceSessionStarted, VoiceTurnStarted
from jarvis.live import session as module
from jarvis.live.config import LiveConfig
from jarvis.plugins.realtime import _live_transport as transport
from tests.fakes.fake_subscription_live_wire import FakeSubscriptionLiveWire


@pytest.fixture
def voice(monkeypatch, tmp_path):
    sent = []
    bus = EventBus()
    wire = FakeSubscriptionLiveWire()

    async def send(message):
        sent.append(message)

    async def take_input(self, message):
        return None  # No physical capture or native handoff in these tests.

    monkeypatch.setattr(module, "get_supervisor_tool_gateway", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_identity", lambda config: "")
    monkeypatch.setattr(module.LiveTools, "declarations", lambda self, **kw: [])
    monkeypatch.setattr(module.LiveVoiceSession, "_take_startup_input", take_input)
    monkeypatch.setattr(module.LiveVoiceSession, "_adopt_desktop_session", lambda self: None)
    config = SimpleNamespace(
        brain=SimpleNamespace(reply_language="en"),
        live=LiveConfig(
            configured=True, backend_model="test", model="gpt-live-1-codex", voice="cove",
        ),
    )
    provider = wire.provider()
    provider.requires_close_ack = False
    session = module.LiveVoiceSession(
        session_id="readiness", send_binary=send, send_json=send,
        providers=[provider], config=config, bus=bus,
    )
    return session, wire.config().offer_sdp, sent, bus


async def test_ready_observer_cannot_delay_audio_release(voice):
    session, offer, sent, bus = voice
    observing, release = asyncio.Event(), asyncio.Event()
    lifecycle = []

    async def started(event):
        lifecycle.append(type(event))

    async def observe(event):
        observing.set()
        await release.wait()

    bus.subscribe(VoiceSessionStarted, started)
    bus.subscribe(VoiceTurnStarted, started)
    bus.subscribe(RealtimeSessionReady, observe)
    task = asyncio.create_task(session.handle_control({
        "type": "audio_start", "webrtc_offer_sdp": offer, "sample_rate": 48000,
    }))
    try:
        await asyncio.wait_for(observing.wait(), 3)
        assert lifecycle == [VoiceSessionStarted, VoiceTurnStarted]
        assert not task.done()
        assert any(message["type"] == "audio_ready" for message in sent)
        assert session._connection is not None and not session._pump_task.done()
        # The connection remains owned if hangup arrives during notification.
        await session.end()
        release.set()
        await task
        assert sum(message["type"] == "audio_ready" for message in sent) == 1
        assert session._closed.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await session.end()


async def test_hangup_during_required_lifecycle_never_releases_audio(voice):
    session, offer, sent, bus = voice

    async def hangup(event):
        await session.end()

    bus.subscribe(VoiceTurnStarted, hangup)
    await session.handle_control({
        "type": "audio_start", "webrtc_offer_sdp": offer, "sample_rate": 48000,
    })
    assert session._closed.is_set()
    assert not any(message["type"] == "audio_ready" for message in sent)


@pytest.mark.parametrize("kind", ["api", "subscription"])
async def test_local_tls_prepares_during_allocation_and_answer_does_not_wait(monkeypatch, kind):
    wire = FakeSubscriptionLiveWire()
    preparing, release = asyncio.Event(), asyncio.Event()
    phases = []

    async def options():
        preparing.set()
        await release.wait()
        return {}

    async def credentials(**kwargs):
        await asyncio.wait_for(preparing.wait(), 1)
        return await wire.credentials(**kwargs)

    async def answer(sdp):
        assert preparing.is_set() and not release.is_set()
        assert not wire.connects  # No control connection before allocation.
        release.set()

    monkeypatch.setattr(transport, "websocket_options", options)
    config = wire.config()
    if kind == "subscription":
        provider = wire.provider()
        provider._credentials = credentials
    else:
        from websockets.asyncio import client as ws_client

        from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

        original_client = httpx.AsyncClient

        async def response(request):
            await asyncio.wait_for(preparing.wait(), 1)
            wire.requests.append(request)
            return httpx.Response(200, json={
                "session": {"id": "rtc_fake"}, "transport": {"sdp": config.offer_sdp},
            })

        def client(**kwargs):
            return original_client(transport=httpx.MockTransport(response), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client)
        monkeypatch.setattr(ws_client, "connect", wire.connect)
        provider = OpenAILiveProvider(api_key="offline-fake")
    config.on_transport_ready = answer
    config.on_startup_phase = phases.append
    connection = await provider.open_session(config)
    assert phases.index("session_response") < phases.index("control_tls_ready")
    assert len(wire.requests) == 1 and len(wire.connects) == 1
    await connection.close()


async def test_tls_failure_after_allocation_retires_subscription(monkeypatch):
    wire = FakeSubscriptionLiveWire()
    allocated = asyncio.Event()
    attempts = 0

    async def options():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await allocated.wait()
            raise RuntimeError("Invalid local trust configuration")
        return {}

    async def answer(sdp):
        allocated.set()

    monkeypatch.setattr(transport, "websocket_options", options)
    config = wire.config()
    config.on_transport_ready = answer
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await wire.provider().open_session(config)
    assert len(wire.requests) == 1
    assert wire.socket.sent == [{"type": "session.close"}]
    assert wire.socket.closed == 1


async def test_cancelled_local_tls_preparation_is_reaped(monkeypatch):
    preparing, stopped = asyncio.Event(), asyncio.Event()

    async def options():
        preparing.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(transport, "websocket_options", options)

    async def prepare():
        async with transport.preparing_websocket_options():
            await asyncio.Future()

    task = asyncio.create_task(prepare())
    await asyncio.wait_for(preparing.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
