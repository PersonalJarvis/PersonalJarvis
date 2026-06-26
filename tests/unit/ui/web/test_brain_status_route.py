"""GET /api/brain/status reports the active brain provider + model.

The frontend reads this on mount to seed the sidebar BRAIN footer. Under the
fast-boot bootstrap (`feat/fast-boot-bootstrap`) the heavy ``BrainManager`` build
is deferred to a background thread, so ``app.state.brain`` is ``None`` for the
first ~850 ms while uvicorn already serves. A frontend that mounts inside that
window must NOT be told ``provider="unknown"`` (which freezes the sidebar on a
bare "—" until a manual provider switch) — the configured ``cfg.brain.primary``
already names the provider that WILL become active, so the endpoint resolves it
from config when the live brain object is not on ``app.state`` yet.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.ui.web.server import WebServer


def test_brain_status_falls_back_to_configured_primary_before_brain_built() -> None:
    """Fast-boot window: no live brain on app.state yet → resolve from config."""
    cfg = JarvisConfig()
    cfg.brain.primary = "gemini"
    cfg.brain.providers["gemini"] = BrainProviderConfig(model="gemini-3.1-flash")
    srv = WebServer(cfg, bus=EventBus())

    # Deliberately do NOT set app.state.brain — mirrors the deferred build.
    with TestClient(srv.app) as client:
        resp = client.get("/api/brain/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["model"] == "gemini-3.1-flash"


def test_brain_status_prefers_live_active_provider_when_built() -> None:
    """Once built, the live brain's active_provider is authoritative (it may
    differ from the configured primary after a runtime switch)."""
    cfg = JarvisConfig()
    cfg.brain.primary = "claude-api"
    cfg.brain.providers["claude-api"] = BrainProviderConfig(model="claude-opus-4-8")
    cfg.brain.providers["openrouter"] = BrainProviderConfig(model="some-router-model")
    srv = WebServer(cfg, bus=EventBus())

    class _LiveBrain:
        active_provider = "openrouter"

    srv.app.state.brain = _LiveBrain()

    with TestClient(srv.app) as client:
        resp = client.get("/api/brain/status")

    body = resp.json()
    assert body["provider"] == "openrouter"
    assert body["model"] == "some-router-model"
