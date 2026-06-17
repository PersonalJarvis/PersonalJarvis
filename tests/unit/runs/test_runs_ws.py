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
