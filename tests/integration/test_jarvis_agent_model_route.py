"""The dedicated subagent LLM model override (C2).

The API-Keys "Subagent" section lets the user pin which MODEL the heavy-task
sub-agents run, separate from the router brain. Write side:
``POST /api/jarvis-agent/model`` -> 3-layer ``config_writer.set_sub_jarvis_model``
(TOML + config-soll + ENV — ``brain.sub_jarvis.model`` is drift-guard pinned).  # i18n-allow
Read side: ``GET /api/jarvis-agent/status`` exposes ``sub_model_override`` and the
effective ``model_resolved`` (override wins, else the provider's deep model).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, load_config
from jarvis.ui.web.server import WebServer


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.worker = BrainTierConfig(provider="claude-api", model="")
    bus = EventBus()
    yield WebServer(cfg=cfg, bus=bus)


@pytest.fixture(autouse=True)
def _no_toml_write(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture persistence instead of writing the real jarvis.toml/soll/ENV."""  # i18n-allow
    calls: list[str] = []
    from jarvis.core import config_writer

    monkeypatch.setattr(
        config_writer, "set_worker_model",
        lambda model, *, path=None: calls.append(model),
    )
    return calls


def test_post_model_persists_and_updates_memory(
    server: WebServer, _no_toml_write: list[str]
) -> None:
    with TestClient(server.app) as client:
        resp = client.post(
            "/api/jarvis-agent/model", json={"model": "claude-sonnet-4-6"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["model"] == "claude-sonnet-4-6"
        assert body["persisted"] is True
        assert body["restart_required"] is True
    assert _no_toml_write == ["claude-sonnet-4-6"]
    # In-memory cfg updated so the next /jarvis-agent/status reflects it.
    assert server.cfg.brain.worker.model == "claude-sonnet-4-6"


def test_post_empty_model_resets_to_provider_default(
    server: WebServer, _no_toml_write: list[str]
) -> None:
    server.cfg.brain.worker.model = "claude-sonnet-4-6"
    with TestClient(server.app) as client:
        resp = client.post("/api/jarvis-agent/model", json={"model": ""})
        assert resp.status_code == 200
        assert resp.json()["model"] == ""
    assert _no_toml_write == [""]
    assert server.cfg.brain.worker.model == ""


def test_status_exposes_override_and_resolved_model(server: WebServer) -> None:
    server.cfg.brain.worker = BrainTierConfig(
        provider="claude-api", model="claude-sonnet-4-6",
    )
    with TestClient(server.app) as client:
        resp = client.get("/api/jarvis-agent/status")
        assert resp.status_code == 200
        data = resp.json()
    assert data["sub_model_override"] == "claude-sonnet-4-6"
    # Explicit override wins the resolution (display form: "<slug>/<model>").
    assert data["model_resolved"].endswith("claude-sonnet-4-6")
