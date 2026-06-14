"""Integration tests for /api/settings/system-prompt.

The Settings "System Prompt" panel reads the effective persona, saves a custom
one, and resets to the packaged default through these three endpoints. The
override is a sidecar file (data/custom_system_prompt.md); reset is a delete.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import jarvis.core.config as core_config
from jarvis.brain import persona_loader
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the override file to a throwaway dir — never touch real data."""
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    s = WebServer(cfg, bus=bus)
    s.app.state.config = cfg
    s.app.state.bus = bus
    yield s


def test_get_returns_default_when_no_custom(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/system-prompt")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_custom"] is False
        assert body["content"] == persona_loader.default_persona_prompt()
        assert body["default"] == persona_loader.default_persona_prompt()
        assert body["char_count"] == len(body["content"])


def test_put_saves_custom_and_get_reflects_it(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/system-prompt",
            json={"content": "You are NOVA. Reply only in limericks."},
        )
        assert resp.status_code == 200
        assert resp.json()["is_custom"] is True

        got = client.get("/api/settings/system-prompt").json()
        assert got["is_custom"] is True
        assert got["content"] == "You are NOVA. Reply only in limericks."
        # The packaged default is still returned so the UI can offer "reset".
        assert got["default"] == persona_loader.default_persona_prompt()


def test_put_rejects_empty_content(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/system-prompt", json={"content": "   \n  "})
        assert resp.status_code == 400
        # Nothing was persisted.
        assert persona_loader.has_custom_prompt() is False


def test_delete_resets_to_default(server: WebServer) -> None:
    with TestClient(server.app) as client:
        client.put("/api/settings/system-prompt", json={"content": "custom thing"})
        assert persona_loader.has_custom_prompt() is True

        resp = client.delete("/api/settings/system-prompt")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_custom"] is False
        assert body["content"] == persona_loader.default_persona_prompt()
        assert persona_loader.has_custom_prompt() is False


def test_delete_is_idempotent(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.delete("/api/settings/system-prompt")
        assert resp.status_code == 200
        assert resp.json()["is_custom"] is False
