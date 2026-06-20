"""Integration tests for /api/settings/agent-instructions.

The "Agent Instructions" view reads the user's standing-instructions file, saves
it, and clears it through these three endpoints. The file is named after the
assistant (e.g. Alex.md); reset is a delete.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import jarvis.core.config as core_config
from jarvis.brain import agent_instructions
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def _expected_filename(server: WebServer) -> str:
    return agent_instructions.instructions_filename(server.app.state.config)


def test_get_returns_empty_with_filename_and_template(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/agent-instructions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is False
        assert body["content"] == ""
        assert body["filename"] == _expected_filename(server)
        assert body["filename"].endswith(".md")
        assert body["template"].strip()  # a starter template is offered
        assert body["char_count"] == 0


def test_put_saves_and_get_reflects_it(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/agent-instructions",
            json={"content": "Be terse. Answer in German."},
        )
        assert resp.status_code == 200
        assert resp.json()["exists"] is True
        assert resp.json()["restart_required"] is False

        got = client.get("/api/settings/agent-instructions").json()
        assert got["exists"] is True
        assert got["content"] == "Be terse. Answer in German."
        assert got["char_count"] == len("Be terse. Answer in German.")


def test_put_rejects_empty_content(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/agent-instructions", json={"content": "   \n  "})
        assert resp.status_code == 400
        assert agent_instructions.has_agent_instructions(server.app.state.config) is False


def test_delete_clears_and_is_idempotent(server: WebServer) -> None:
    with TestClient(server.app) as client:
        client.put("/api/settings/agent-instructions", json={"content": "rules"})
        assert agent_instructions.has_agent_instructions(server.app.state.config) is True

        resp = client.delete("/api/settings/agent-instructions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is False
        assert body["removed"] is True
        assert agent_instructions.has_agent_instructions(server.app.state.config) is False

        again = client.delete("/api/settings/agent-instructions")
        assert again.status_code == 200
        assert again.json()["removed"] is False
