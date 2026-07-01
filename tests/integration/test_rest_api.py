"""Integration tests for the WebServer's REST endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import SystemStarted
from jarvis.ui.web.server import WebServer


@pytest.fixture
def web_server() -> WebServer:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True  # no static mount during tests
    bus = EventBus()
    return WebServer(cfg, bus=bus)


def test_health_endpoint(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "version" in body


def test_config_endpoint(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        # Must contain the top-level sections.
        for section in ("profile", "trigger", "stt", "tts", "brain", "ui"):
            assert section in body


def test_plugins_endpoint(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        resp = client.get("/api/plugins")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)


def test_debug_emit_test_event_publishes(web_server: WebServer) -> None:
    received: list[SystemStarted] = []

    async def handler(evt: SystemStarted) -> None:
        received.append(evt)

    web_server.bus.subscribe(SystemStarted, handler)

    with TestClient(web_server.app) as client:
        resp = client.post("/api/debug/emit-test-event")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["event"] == "SystemStarted"
        assert "trace_id" in body

    assert len(received) == 1
    assert isinstance(received[0], SystemStarted)


def test_window_focus_endpoint(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        resp = client.post("/api/window/focus")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
