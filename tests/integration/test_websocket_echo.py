"""Integration-Tests für den WebSocket-Endpoint des WebServers.

Verifiziert:
- Welcome-Frame kommt unmittelbar nach Connect.
- Ein per bus.publish() gefeuerter Event landet als Envelope beim Client.
- Ping-Command bekommt Pong-Antwort.
- Invalide Frames werden nicht weitergeleitet, aber publishen ErrorOccurred.
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

            # Publishe direkt auf dem Bus — muss <500ms als Envelope ankommen.
            # TestClient läuft die Server-Coroutinen synchron; wir senden einen
            # Command der intern bus.publish aufruft.
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
    """Ein außerhalb des WS gefeuertes Event muss beim Client als Envelope landen."""
    import asyncio

    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            _receive_welcome(ws)

            # Publishe über einen eigenen Loop — der TestClient hat seinen
            # eigenen asyncio-Loop, daher direkt die Handler des Bus aufrufen.
            async def _pub() -> None:
                await web_server.bus.publish(
                    SystemStarted(version="test-direct", source_layer="test")
                )

            # Zwischen den send/receive-Ops des TestClients gibt es einen
            # internen Loop — wir rufen publish in der gleichen Instanz.
            # Einfacher: ein Ping-Command macht einen Server-Roundtrip, dann
            # sehen wir publish als nächstes Frame.
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

            # Der ErrorOccurred kommt zurück als Envelope (Wildcard-Subscription).
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
        # Nach dem with-Block: Disconnect → Unsubscribe
        # TestClient gibt der Server-Coroutine Zeit zum Cleanup.
        time.sleep(0.1)
        after = len(web_server.bus._wildcard_subscribers)  # type: ignore[attr-defined]
        assert after == before, f"Leak: {before} → {after}"
