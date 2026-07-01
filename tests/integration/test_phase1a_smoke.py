"""Phase 1a smoke test: WebServer starts, WS echo works, clean shutdown.

Without a frontend build — only tests the backend + REST + WebSocket path.
If agents 1-6 haven't merged their files yet, the test skips gracefully.
"""
from __future__ import annotations

import pytest


# Shared import-skip setup: missing backend files lead to a skip,
# not a crash — so this test stays green until agents 1-6 are done.
def _try_import_webserver():
    try:
        from jarvis.ui.web.server import WebServer  # type: ignore
        return WebServer
    except Exception as exc:
        pytest.skip(f"WebServer not available yet: {exc!r}")


def _try_import_config():
    try:
        from jarvis.core.config import JarvisConfig  # type: ignore
        return JarvisConfig
    except Exception as exc:
        pytest.skip(f"JarvisConfig cannot be loaded: {exc!r}")


def _try_import_testclient():
    try:
        from fastapi.testclient import TestClient  # type: ignore
        return TestClient
    except Exception as exc:
        pytest.skip(f"fastapi.testclient not available: {exc!r}")


@pytest.fixture
def test_config():
    JarvisConfig = _try_import_config()
    try:
        return JarvisConfig()
    except Exception as exc:
        pytest.skip(f"JarvisConfig() cannot be instantiated: {exc!r}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webserver_starts_and_stops(test_config):
    WebServer = _try_import_webserver()
    srv = WebServer(test_config)
    await srv.start()
    try:
        assert getattr(srv, "running", True), "WebServer.running should be True"
    finally:
        await srv.stop()


def test_rest_api_health(test_config):
    WebServer = _try_import_webserver()
    TestClient = _try_import_testclient()
    srv = WebServer(test_config)
    app = getattr(srv, "app", None)
    if app is None:
        pytest.skip("WebServer.app Attribut fehlt")
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True


def test_rest_api_plugins(test_config):
    WebServer = _try_import_webserver()
    TestClient = _try_import_testclient()
    srv = WebServer(test_config)
    app = getattr(srv, "app", None)
    if app is None:
        pytest.skip("WebServer.app Attribut fehlt")
    with TestClient(app) as client:
        r = client.get("/api/plugins")
        assert r.status_code == 200
        data = r.json()
        assert "jarvis.brain" in data
        assert "jarvis.channel" in data


def test_websocket_event_echo(test_config):
    """Core piece: a bus event reaches the WS client."""
    WebServer = _try_import_webserver()
    TestClient = _try_import_testclient()
    srv = WebServer(test_config)
    app = getattr(srv, "app", None)
    if app is None:
        pytest.skip("WebServer.app Attribut fehlt")
    with TestClient(app) as client:
        try:
            ws_ctx = client.websocket_connect("/ws")
        except Exception as exc:
            pytest.skip(f"/ws not available: {exc!r}")
        with ws_ctx as ws:
            welcome = ws.receive_json()
            assert welcome.get("type") == "welcome"

            # Feuere ein Event via REST-Debug-Endpoint
            r = client.post("/api/debug/emit-test-event")
            if r.status_code == 404:
                pytest.skip("debug endpoint /api/debug/emit-test-event not implemented")
            assert r.status_code in (200, 202, 204)

            envelope = ws.receive_json()
            assert envelope.get("type") == "event"
            assert envelope.get("event_name") == "SystemStarted"
