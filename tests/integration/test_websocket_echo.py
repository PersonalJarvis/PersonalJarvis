"""Integration tests for the WebServer's WebSocket endpoint.

Verifies:
- The welcome frame arrives immediately after connect.
- An event fired via bus.publish() lands as an envelope at the client.
- A ping command gets a pong response.
- Invalid frames are not forwarded, but publish ErrorOccurred.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import ErrorOccurred, SystemStarted
from jarvis.ui.web.server import WebServer


@pytest.fixture
def web_server() -> WebServer:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    return WebServer(cfg, bus=bus)


def _receive_welcome(ws) -> dict:
    frame = ws.receive_json()
    assert frame["type"] == "welcome"
    assert "session_id" in frame
    assert "version" in frame
    return frame


def test_welcome_on_connect(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)


def test_bus_event_forwarded_as_envelope(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)

            # Publish directly on the bus — must arrive as an envelope in <500ms.
            # TestClient runs the server coroutines synchronously; we send a
            # command that internally calls bus.publish.
            start = time.monotonic()
            ws.send_json({"type": "command", "action": "test_event", "payload": {}})

            frame = ws.receive_json()
            elapsed_ms = (time.monotonic() - start) * 1000

            assert frame["type"] == "event"
            assert frame["event_name"] == "SystemStarted"
            assert "trace_id" in frame
            assert "timestamp_ns" in frame
            assert "payload" in frame
            assert frame["payload"]["version"]
            assert elapsed_ms < 500, f"Latenz {elapsed_ms:.1f}ms ≥ 500ms"


def test_direct_bus_publish_reaches_client(web_server: WebServer) -> None:
    """An event fired outside the WS must land at the client as an envelope."""
    import asyncio

    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)

            # Publish via our own loop — the TestClient has its own
            # asyncio loop, so call the bus handlers directly.
            async def _pub() -> None:
                await web_server.bus.publish(
                    SystemStarted(version="test-direct", source_layer="test")
                )

            # Between the TestClient's send/receive ops there is an
            # internal loop — we call publish on the same instance.
            # Simpler: a ping command does a server roundtrip, then we
            # see publish as the next frame.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_pub())
            finally:
                loop.close()

            frame = ws.receive_json()
            assert frame["type"] == "event"
            assert frame["event_name"] == "SystemStarted"
            assert frame["payload"]["version"] == "test-direct"


def test_ping_command_returns_pong(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)

            ws.send_json({"type": "command", "action": "ping", "payload": {"n": 42}})
            frame = ws.receive_json()
            assert frame["type"] == "pong"
            assert frame["payload"] == {"n": 42}


def test_invalid_frame_publishes_error_event(web_server: WebServer) -> None:
    received: list[ErrorOccurred] = []

    async def handler(evt: ErrorOccurred) -> None:
        received.append(evt)

    web_server.bus.subscribe(ErrorOccurred, handler)

    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)
            ws.send_json({"type": "bogus-frame-type"})

            # The ErrorOccurred comes back as an envelope (wildcard subscription).
            frame = ws.receive_json()
            assert frame["type"] == "event"
            assert frame["event_name"] == "ErrorOccurred"

    assert len(received) >= 1
    assert received[0].error_type == "UnknownFrameType"


def test_client_disconnect_cleans_up_subscription(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        before = len(web_server.bus._wildcard_subscribers)  # type: ignore[attr-defined]
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)
            during = len(web_server.bus._wildcard_subscribers)  # type: ignore[attr-defined]
            assert during == before + 1
        # After the with block: disconnect → unsubscribe
        # TestClient gives the server coroutine time to clean up.
        time.sleep(0.1)
        after = len(web_server.bus._wildcard_subscribers)  # type: ignore[attr-defined]
        assert after == before, f"Leak: {before} → {after}"
