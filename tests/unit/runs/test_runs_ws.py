"""Smoke test for the /api/runs/live WebSocket endpoint.

Verifies: connect → welcome frame → run-relevant events forwarded → clean close.
AP-20: the receive loop treats any non-clean read error as terminal (break, never
continue) so an unclean client teardown cannot spin on a dead socket.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.runs.runs_ws import router as runs_ws_router

# ---------------------------------------------------------------------------
# Fake bus — matches the real EventBus.subscribe_all / unsubscribe_all API
# (subscribe_all has no typed removal; the implementation uses
# _wildcard_subscribers list directly or ignores unsubscribe_all if absent).
# ---------------------------------------------------------------------------


class _FakeBus:
    """Minimal EventBus double for the WebSocket tests."""

    def __init__(self) -> None:
        self._wildcard: list = []

    def subscribe_all(self, cb) -> None:
        self._wildcard.append(cb)

    # The real EventBus has no unsubscribe_all; expose it anyway so tests can
    # call it and verify the implementation handles the no-op path gracefully.
    def unsubscribe_all(self, cb) -> None:
        try:
            self._wildcard.remove(cb)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Helper: build a fresh app wired with the runs_ws router
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(runs_ws_router)
    app.state.bus = _FakeBus()
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ws_connect_and_welcome() -> None:
    """Connecting must immediately receive a 'welcome' frame."""
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/api/runs/live") as ws:
        first = ws.receive_json()
    assert first["type"] == "welcome"


def test_ws_welcome_frame_has_channel() -> None:
    """The welcome frame must include the channel identifier."""
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/api/runs/live") as ws:
        first = ws.receive_json()
    assert first.get("channel") == "runs.live"


def test_ws_no_bus_sends_unavailable() -> None:
    """When app.state has no bus, the server must send 'unavailable' and close.

    The welcome frame is always sent first; the unavailable frame follows it.
    """
    app = FastAPI()
    app.include_router(runs_ws_router)
    # deliberately do NOT set app.state.bus
    client = TestClient(app)
    with client.websocket_connect("/api/runs/live") as ws:
        first = ws.receive_json()   # welcome (always sent before bus check)
        second = ws.receive_json()  # unavailable
    assert first["type"] == "welcome"
    assert second["type"] == "unavailable"
    assert "reason" in second


def test_ws_subscribes_to_bus() -> None:
    """Opening a connection must register a subscriber on the bus."""
    app = _make_app()
    bus: _FakeBus = app.state.bus
    client = TestClient(app)
    with client.websocket_connect("/api/runs/live") as ws:
        ws.receive_json()  # welcome
        assert len(bus._wildcard) == 1


def test_stalled_send_detaches_instead_of_slowing_every_publish(monkeypatch) -> None:
    """A tab that stops reading must not hold up the bus on every event.

    The forwarder is a wildcard observer; the real bus awaits it (5 s cap) on
    each publish. Without a send bound it stayed attached and every event in
    the app paid that wait until the tab closed.
    """
    import asyncio
    from types import SimpleNamespace

    from jarvis.runs import runs_ws
    from jarvis.runs.runs_ws import _LIVE_KINDS

    monkeypatch.setattr(runs_ws, "_SEND_TIMEOUT_S", 0.05)
    bus = _FakeBus()

    class _StalledSocket:
        def __init__(self) -> None:
            self.scope = {"app": SimpleNamespace(state=SimpleNamespace(bus=bus))}
            self.closed_with: int | None = None
            self.disconnected = asyncio.Event()

        async def accept(self) -> None:
            return None

        async def send_json(self, frame: dict) -> None:
            if frame.get("type") == "event":
                await asyncio.Event().wait()  # the tab never reads again

        async def receive_text(self) -> str:
            await self.disconnected.wait()
            raise runs_ws.WebSocketDisconnect()

        async def close(self, code: int = 1000) -> None:
            self.closed_with = code
            self.disconnected.set()

    kind = next(iter(_LIVE_KINDS))
    event = type(kind, (), {"timestamp_ns": 0, "session_id": "s", "trace_id": "t"})()

    async def _run() -> None:
        subscribed = asyncio.Event()
        subscribe_all = bus.subscribe_all

        def _subscribe_all(cb) -> None:  # noqa: ANN001
            subscribe_all(cb)
            subscribed.set()

        bus.subscribe_all = _subscribe_all  # type: ignore[method-assign]
        ws = _StalledSocket()
        endpoint = asyncio.create_task(runs_ws.runs_live(ws))
        await asyncio.wait_for(subscribed.wait(), timeout=1.0)
        forward = bus._wildcard[0]
        await asyncio.wait_for(forward(event), timeout=1.0)
        assert bus._wildcard == []
        assert ws.closed_with == 1013
        await asyncio.wait_for(endpoint, timeout=1.0)

    asyncio.run(_run())
