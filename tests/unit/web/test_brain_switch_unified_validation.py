"""Guard: provider-switch REST routes delegate to one shared implementation.

``app_control.apply_provider_switch`` is the validation source for the voice
gate, brain tools, CLI, and UI. A route-local credential check can otherwise
drift from the worker's real auth capabilities (AP-4 class).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _FakeBrain:
    def __init__(self, *, available: list[str], active: str = "openai") -> None:
        self._available = available
        self.active_provider = active
        self.last_persist_ok = False
        self.calls: list[tuple[str, bool]] = []

    def available_providers(self) -> list[str]:
        return list(self._available)

    async def switch(self, provider: str, *, persist: bool = False) -> None:
        self.calls.append((provider, persist))
        self.active_provider = provider
        self.last_persist_ok = False


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.brain = _FakeBrain(available=["openai", "claude-api", "openrouter"])
    srv.app.state.cfg = cfg
    srv.app.state.bus = srv.bus
    yield srv


def test_route_calls_shared_apply_provider_switch(server, monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def fake_apply(tier, provider, *, cfg, persist=True, manager=None):
        seen.update(tier=tier, provider=provider, persist=persist, manager=manager)
        return {
            "ok": True, "tier": tier, "old_provider": "openai",
            "new_provider": provider, "persisted": True,
            "applied_live": True, "requires_restart": False,
        }

    monkeypatch.setattr(
        "jarvis.brain.app_control.apply_provider_switch", fake_apply
    )
    with TestClient(server.app) as client:
        resp = client.post(
            "/api/brain/switch", json={"provider": "openrouter", "persist": True}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["active"] == "openrouter"
    assert body["persisted"] is True
    assert body["old_provider"] == "openai"
    # The route handed its live manager to the shared implementation.
    assert seen["tier"] == "brain"
    assert seen["manager"] is server.app.state.brain


def test_shared_error_kind_maps_to_http_status(server, monkeypatch) -> None:
    async def fake_apply(tier, provider, *, cfg, persist=True, manager=None):
        return {
            "ok": False,
            "error_kind": "missing_credential",
            "error": "openrouter is not configured — its API key is missing.",
        }

    monkeypatch.setattr(
        "jarvis.brain.app_control.apply_provider_switch", fake_apply
    )
    with TestClient(server.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "openrouter"})
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"]


def test_jarvis_agent_route_calls_shared_apply_provider_switch(
    server, monkeypatch
) -> None:
    seen: dict[str, object] = {}

    async def fake_apply(tier, provider, *, cfg, persist=True, manager=None):
        seen.update(tier=tier, provider=provider, persist=persist, cfg=cfg)
        return {
            "ok": True,
            "tier": tier,
            "old_provider": "openai-codex",
            "new_provider": "claude-api",
            "persisted": True,
            "applied_live": True,
            "requires_restart": False,
        }

    monkeypatch.setattr(
        "jarvis.brain.app_control.apply_provider_switch", fake_apply
    )

    with TestClient(server.app) as client:
        response = client.post(
            "/api/jarvis-agent/switch",
            json={"provider": "claude-api", "persist": True},
        )

    assert response.status_code == 200, response.text
    assert response.json()["active"] == "claude-api"
    assert response.json()["restart_required"] is False
    assert seen == {
        "tier": "subagent",
        "provider": "claude-api",
        "persist": True,
        "cfg": server.cfg,
    }


def test_airgapped_lock_now_lives_in_shared_logic(server) -> None:
    """The airgapped check moved INTO apply_provider_switch; the route must
    surface it as 403 without any route-local profile logic."""
    server.cfg.profile.name = "airgapped"
    with TestClient(server.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "openrouter"})
    assert resp.status_code == 403
    assert "privacy mode" in resp.json()["detail"].lower()
