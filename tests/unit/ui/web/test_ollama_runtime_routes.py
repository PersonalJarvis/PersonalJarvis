"""Routes for the in-app Ollama runtime lifecycle (install / start / status).

Pins: the status route pairs the three-state runtime truth with the install
snapshot, the install route is dangerous-flagged and returns immediately,
the start route answers 409 with the honest reason when starting cannot
succeed, and none of it exists on cards whose server has no pull API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.brain import ollama_runtime
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


@pytest.fixture
def server(tmp_path, monkeypatch: pytest.MonkeyPatch) -> WebServer:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    ollama_runtime._reset_for_tests()
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    yield srv
    ollama_runtime._reset_for_tests()


def test_status_pairs_runtime_with_install_snapshot(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {
            "installed": True,
            "binary": "x",
            "running": False,
            "version": "",
            "detail": "Ollama is installed but not running.",
        },
    )
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/ollama/ollama-runtime")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["installed"] is True
    assert body["status"]["running"] is False
    assert body["install"]["phase"] == "idle"


def test_runtime_routes_refuse_non_pull_capable_cards(server: WebServer) -> None:
    """A cloud card has no runtime to manage — 400, never a silent no-op."""
    with TestClient(server.app) as client:
        resp = client.get("/api/providers/openai/ollama-runtime")
    assert resp.status_code == 400


def test_install_returns_immediately_with_the_snapshot(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ollama_runtime, "start_install", lambda: (True, "install started")
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/ollama/ollama-runtime/install")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is True
    assert body["message"] == "install started"
    assert "phase" in body


def test_install_route_is_dangerous_flagged(server: WebServer) -> None:
    """Installing third-party software must carry the danger marker the CLI
    and permission layers key on."""
    schema = server.app.openapi()
    op = schema["paths"]["/api/providers/{provider_id}/ollama-runtime/install"][
        "post"
    ]
    assert op.get("x-jarvis-dangerous") is True


def test_start_answers_409_with_the_honest_reason(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ollama_runtime,
        "start_server",
        lambda: (False, "Ollama is not installed — install it first."),
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/ollama/ollama-runtime/start")
    assert resp.status_code == 409
    assert "install" in resp.json()["detail"]


def test_start_reports_the_fresh_status(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ollama_runtime, "start_server", lambda: (True, "Ollama started.")
    )
    monkeypatch.setattr(
        ollama_runtime,
        "runtime_status",
        lambda: {
            "installed": True,
            "binary": "x",
            "running": True,
            "version": "0.9.1",
            "detail": "Ollama is running (version 0.9.1).",
        },
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/providers/ollama/ollama-runtime/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"]["running"] is True
