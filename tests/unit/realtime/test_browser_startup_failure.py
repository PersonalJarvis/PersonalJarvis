"""A failed browser start releases only its correlated native wake owner."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.events import BrowserVoiceRequested
from jarvis.live import runtime, startup
from jarvis.ui.web.voice_call_routes import router


@pytest.fixture(autouse=True)
def empty_runtime(monkeypatch):
    for name in ("_active", "_owners", "_pending_browser_starts", "_browser_call_requests"):
        monkeypatch.setattr(runtime, name, {})
    for name in ("_opening", "_watchers"):
        monkeypatch.setattr(runtime, name, set())
    monkeypatch.setattr(startup, "_pending", {})


class VoiceSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.ends = 0

    async def end(self, **kwargs):
        self.ends += 1
        runtime.unregister(self.session_id)


def watch(bus):
    events = []
    requests = asyncio.Queue()

    async def requested(event):
        events.append(event)
        if event.action == "start":
            requests.put_nowait(event.request_id)

    bus.subscribe(BrowserVoiceRequested, requested)
    return events, requests


async def test_browser_failure_releases_native_wait_without_45_second_timeout():
    bus = EventBus()
    events, requests = watch(bus)
    task = asyncio.create_task(runtime.run_browser_call(
        bus, asyncio.Event(), session_id="wake", input_buffer=object(), timeout_s=45,
    ))
    request_id = await asyncio.wait_for(requests.get(), 1)
    assert UUID(request_id).version == 4
    assert "wake" in startup._pending
    assert runtime.browser_startup_failed(request_id)
    assert await asyncio.wait_for(task, 0.5) == "error"
    assert [(event.action, event.request_id) for event in events] == [
        ("start", request_id), ("stop", request_id),
    ]
    assert asdict(events[0])["request_id"] == request_id
    assert not startup._pending and not runtime._watchers
    assert not runtime._pending_browser_starts and not runtime._browser_call_requests
    assert not runtime.browser_startup_failed(request_id)


async def test_previous_request_cannot_fail_or_stop_a_newer_request():
    bus = EventBus()
    events, requests = watch(bus)
    first = asyncio.create_task(runtime.run_browser_call(
        bus, asyncio.Event(), session_id="same-wake", timeout_s=45,
    ))
    old_id = await asyncio.wait_for(requests.get(), 1)
    second = asyncio.create_task(runtime.run_browser_call(
        bus, asyncio.Event(), session_id="same-wake", input_buffer=object(), timeout_s=45,
    ))
    new_id = await asyncio.wait_for(requests.get(), 1)
    assert old_id != new_id
    assert not runtime.browser_startup_failed(old_id)
    assert await asyncio.wait_for(first, 0.5) == "error"
    assert not second.done() and "same-wake" in startup._pending
    assert not any(event.action == "stop" for event in events)
    assert runtime.browser_startup_failed(new_id)
    assert await asyncio.wait_for(second, 0.5) == "error"
    assert [event.request_id for event in events if event.action == "stop"] == [new_id]


async def test_failure_ack_is_ignored_after_actual_session_registration():
    bus = EventBus()
    _, requests = watch(bus)
    hangup = asyncio.Event()
    session = VoiceSession("ready-wake")

    async def accept(event):
        if event.action == "start":
            runtime.register(session)

    bus.subscribe(BrowserVoiceRequested, accept)
    task = asyncio.create_task(runtime.run_browser_call(bus, hangup, session_id=session.session_id))
    request_id = await asyncio.wait_for(requests.get(), 1)
    assert not runtime.browser_startup_failed(request_id)
    assert not task.done() and session.ends == 0
    hangup.set()
    assert await asyncio.wait_for(task, 1) == "hotkey"
    assert session.ends == 1


async def test_early_registered_session_end_cannot_return_to_startup_wait():
    bus = EventBus()
    session = VoiceSession("early-end")

    async def start_then_end(event):
        if event.action == "start":
            runtime.register(session)
            runtime.unregister(session.session_id)

    bus.subscribe(BrowserVoiceRequested, start_then_end)
    result = await asyncio.wait_for(runtime.run_browser_call(
        bus, asyncio.Event(), session_id=session.session_id, timeout_s=45,
    ), 0.5)
    assert result == "client_stop"
    assert session.ends == 0
    assert not runtime._pending_browser_starts and not runtime._browser_call_requests


async def test_cancelled_request_invalidates_its_failure_ack():
    bus = EventBus()
    _, requests = watch(bus)
    task = asyncio.create_task(runtime.run_browser_call(bus, asyncio.Event(), session_id="cancel"))
    request_id = await asyncio.wait_for(requests.get(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runtime.browser_startup_failed(request_id)
    assert not runtime._pending_browser_starts and not runtime._browser_call_requests


async def test_unrelated_active_call_is_never_closed_by_pending_wake_cleanup():
    unrelated = VoiceSession("unrelated-browser")
    runtime.register(unrelated)
    bus = EventBus()
    events, _ = watch(bus)
    assert await runtime.run_browser_call(bus, asyncio.Event(), session_id="native-wake") == "error"
    assert unrelated.ends == 0 and runtime.active() == (unrelated,)
    assert not runtime.browser_startup_failed(events[0].request_id)


async def test_accepted_failure_still_wins_if_registration_races_its_delivery():
    session = VoiceSession("registration-race")
    bus = EventBus()

    async def fail_then_register(event):
        if event.action == "start":
            assert runtime.browser_startup_failed(event.request_id)
            runtime.register(session)

    bus.subscribe(BrowserVoiceRequested, fail_then_register)
    result = await runtime.run_browser_call(bus, asyncio.Event(), session_id=session.session_id)
    assert result == "error"
    assert session.ends == 1 and not runtime.active()


async def test_browser_loop_failure_ack_wakes_the_native_owner_loop():
    bus = EventBus()
    _, requests = watch(bus)
    owner = asyncio.get_running_loop()
    task = asyncio.create_task(runtime.run_browser_call(
        bus, asyncio.Event(), session_id="cross-loop",
    ))
    request_id = await asyncio.wait_for(requests.get(), 1)

    async def acknowledge():
        assert asyncio.get_running_loop() is not owner
        return runtime.browser_startup_failed(request_id)

    assert await asyncio.to_thread(asyncio.run, acknowledge())
    assert await asyncio.wait_for(task, 0.5) == "error"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as result:
        yield result


def test_startup_failure_route_exposes_only_its_bounded_correlation_token(client, monkeypatch):
    calls = []
    request_id = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(runtime, "browser_startup_failed",
                        lambda token: calls.append(token) or True)
    response = client.post("/api/voice/startup-failed", json={"request_id": request_id})
    assert response.status_code == 200 and response.json() == {"acknowledged": True}
    assert calls == [request_id]
    spec = client.get("/openapi.json").json()
    operation = spec["paths"]["/api/voice/startup-failed"]["post"]
    assert operation["tags"] == ["voice"] and operation["summary"]
    schema = spec["components"]["schemas"]["BrowserStartupFailure"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["request_id"]["maxLength"] == 36


@pytest.mark.parametrize("body", [
    {}, {"request_id": ""}, {"request_id": "x" * 128}, {"request_id": 42},
    {"request_id": "11111111-2222-4333-8444-555555555555", "reason": "private audio error"},
])
def test_startup_failure_route_rejects_unbounded_or_content_fields(client, body):
    assert client.post("/api/voice/startup-failed", json=body).status_code == 422


def test_stale_token_is_a_noop_even_on_headless_install(client):
    response = client.post("/api/voice/startup-failed", json={
        "request_id": "11111111-2222-4333-8444-555555555555",
    })
    assert response.status_code == 200 and response.json() == {"acknowledged": False}
