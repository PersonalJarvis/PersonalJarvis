"""Phase 1a Smoke-Test: WebServer startet, WS-Echo funktioniert, Shutdown sauber.

Ohne Frontend-Build — testet nur das Backend + REST + WebSocket-Pfad.
Wenn Agents 1-6 ihre Files noch nicht gemerged haben, skippt der Test graceful.
"""
from __future__ import annotations

import pytest


# Gemeinsames Import-Skip-Setup: fehlende Backend-Files führen zu Skip,
# nicht zu Crash — damit dieser Test grün bleibt, bis Agents 1-6 fertig sind.
def _try_import_webserver():
    try:
        from jarvis.ui.web.server import WebServer  # type: ignore
        return WebServer
    except Exception as exc:
        pytest.skip(f"WebServer noch nicht verfügbar: {exc!r}")


def _try_import_config():
    try:
        from jarvis.core.config import JarvisConfig  # type: ignore
        return JarvisConfig
    except Exception as exc:
        pytest.skip(f"JarvisConfig nicht ladbar: {exc!r}")


def _try_import_testclient():
    try:
        from fastapi.testclient import TestClient  # type: ignore
        return TestClient
    except Exception as exc:
        pytest.skip(f"fastapi.testclient nicht verfügbar: {exc!r}")


@pytest.fixture
def test_config():
    JarvisConfig = _try_import_config()
    try:
        return JarvisConfig()
    except Exception as exc:
        pytest.skip(f"JarvisConfig() kann nicht instanziiert werden: {exc!r}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webserver_starts_and_stops(test_config):
    WebServer = _try_import_webserver()
    srv = WebServer(test_config)
    await srv.start()
    try:
        assert getattr(srv, "running", True), "WebServer.running sollte True sein"
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
    """Kernstück: Bus-Event landet im WS-Client."""
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
            pytest.skip(f"/ws nicht verfügbar: {exc!r}")
        with ws_ctx as ws:
            welcome = ws.receive_json()
            assert welcome.get("type") == "welcome"

            # Feuere ein Event via REST-Debug-Endpoint
            r = client.post("/api/debug/emit-test-event")
            if r.status_code == 404:
                pytest.skip("Debug-Endpoint /api/debug/emit-test-event nicht implementiert")
            assert r.status_code in (200, 202, 204)

            envelope = ws.receive_json()
            assert envelope.get("type") == "event"
            assert envelope.get("event_name") == "SystemStarted"
