"""Dedicated Computer-Use provider tier — backend for the API-Keys "Computer-Use" tab.

Overlay over the brain-tier provider cards (Claude/OpenAI/OpenRouter/Gemini),
not a new provider tier — a GLOBAL "which brain provider plans Computer-Use"
selection, decoupled from ``[brain] primary``. Covers the ``computer_use_active``
resolution in ``list_providers`` and the ``POST /computer-use/switch`` route.

Style follows ``tests/unit/web/test_realtime_provider_category.py``: a
lightweight FastAPI app with just the router mounted, monkeypatched secrets —
never the live ``jarvis.toml``. Every test that calls the switch route with
``persist=True`` monkeypatches ``config_writer.set_computer_use_provider`` so
it NEVER writes the real config file (isolation — see the earlier real-write
defect this repeats the fix for).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.provider_routes import router
from jarvis.ui.web.provider_spec import get_spec


def _only_claude_key(key: str, *_a, **_kw) -> str | None:
    """Fake ``cfg_mod.get_secret``: only ``anthropic_api_key`` looks configured."""
    return "sk-test" if key == "anthropic_api_key" else None


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.config = JarvisConfig()
    return app


# ---------------------------------------------------------------------------
# GET /api/providers — computer_use_active
# ---------------------------------------------------------------------------


def test_list_providers_defaults_computer_use_active_to_brain_primary(monkeypatch):
    """With no [brain.computer_use].provider configured, CU runs on the main
    Brain — brain.primary's card must show computer_use_active True (default-
    preserving contract from the plan)."""
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    app = _app()
    assert app.state.config.brain.primary == "claude-api"
    client = TestClient(app)

    resp = client.get("/api/providers")

    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.json()["providers"]}
    assert by_id["claude-api"]["computer_use_active"] is True
    assert by_id["openai"]["computer_use_active"] is False


def test_list_providers_marks_configured_cu_provider_active(monkeypatch):
    """Once [brain.computer_use].provider is set, ITS card gets
    computer_use_active True — independent of brain.primary / the Brain
    tab's "active" field."""
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    app = _app()
    from types import SimpleNamespace

    from jarvis.core.config import BrainTierConfig

    app.state.config.brain.computer_use = BrainTierConfig(provider="openai")
    # The Brain tab's "active" field reads app.state.brain (the live
    # BrainManager), independent of cfg.brain.computer_use.
    app.state.brain = SimpleNamespace(active_provider="claude-api")
    client = TestClient(app)

    resp = client.get("/api/providers")

    by_id = {p["id"]: p for p in resp.json()["providers"]}
    assert by_id["openai"]["computer_use_active"] is True
    # Brain primary stays claude-api's "active" — the two selections are
    # independent, never coupled.
    assert by_id["claude-api"]["active"] is True
    assert by_id["claude-api"]["computer_use_active"] is False
    assert by_id["openai"]["active"] is False


# ---------------------------------------------------------------------------
# POST /api/computer-use/switch
# ---------------------------------------------------------------------------


def test_computer_use_switch_persists_with_key(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", _only_claude_key)
    writes: list[str] = []
    monkeypatch.setattr(
        config_writer, "set_computer_use_provider",
        lambda name, **kw: writes.append(name),
    )

    app = _app()
    client = TestClient(app)
    resp = client.post(
        "/api/computer-use/switch", json={"provider": "claude-api", "persist": True},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["active"] == "claude-api"
    assert body["persisted"] is True
    # Unlike worker/realtime, CU takes effect immediately — no restart.
    assert body["restart_required"] is False
    assert writes == ["claude-api"]
    assert app.state.config.brain.computer_use is not None
    assert app.state.config.brain.computer_use.provider == "claude-api"


def test_computer_use_switch_updates_existing_tier_in_memory(monkeypatch):
    """A second switch must UPDATE the existing [brain.computer_use] block,
    not silently no-op or create a duplicate."""
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    monkeypatch.setattr(config_writer, "set_computer_use_provider", lambda name, **kw: None)

    app = _app()
    from jarvis.core.config import BrainTierConfig

    app.state.config.brain.computer_use = BrainTierConfig(provider="claude-api")
    client = TestClient(app)

    resp = client.post(
        "/api/computer-use/switch", json={"provider": "gemini", "persist": True},
    )

    assert resp.status_code == 200
    assert app.state.config.brain.computer_use.provider == "gemini"


def test_computer_use_switch_without_key_is_409(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: None)
    client = TestClient(_app())

    resp = client.post(
        "/api/computer-use/switch", json={"provider": "claude-api", "persist": True},
    )

    assert resp.status_code == 409


def test_computer_use_switch_rejects_non_brain_switchable_provider(monkeypatch):
    """Codex/Antigravity are brain_switchable=False (can't receive
    screenshots) — the CU planner must reject them like /api/brain/switch does."""
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    spec = get_spec("codex")
    assert spec is not None and spec.brain_switchable is False
    client = TestClient(_app())

    resp = client.post(
        "/api/computer-use/switch", json={"provider": "codex", "persist": True},
    )

    assert resp.status_code == 400


def test_computer_use_switch_rejects_non_brain_tier_provider(monkeypatch):
    """A TTS/STT-tier id is not a valid CU planner candidate either."""
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    client = TestClient(_app())

    resp = client.post(
        "/api/computer-use/switch",
        json={"provider": "elevenlabs", "persist": True},
    )

    assert resp.status_code == 400


def test_computer_use_switch_unknown_provider_is_404(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: None)
    client = TestClient(_app())

    resp = client.post(
        "/api/computer-use/switch",
        json={"provider": "does-not-exist", "persist": True},
    )

    assert resp.status_code == 404


def test_computer_use_switch_does_not_touch_brain_primary(monkeypatch):
    """Activating a Computer-Use provider must NEVER move brain.primary or
    the Brain tab's active provider (Constraint: don't break Pipeline)."""
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    monkeypatch.setattr(config_writer, "set_computer_use_provider", lambda name, **kw: None)

    app = _app()
    assert app.state.config.brain.primary == "claude-api"
    client = TestClient(app)

    resp = client.post(
        "/api/computer-use/switch", json={"provider": "gemini", "persist": True},
    )

    assert resp.status_code == 200
    assert app.state.config.brain.primary == "claude-api"


def test_computer_use_switch_emits_secret_configured_event(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *a, **kw: "sk-test")
    monkeypatch.setattr(config_writer, "set_computer_use_provider", lambda name, **kw: None)
    from jarvis.core.bus import EventBus

    bus = EventBus()
    events: list[object] = []

    async def _record(ev: object) -> None:
        events.append(ev)

    bus.subscribe_all(_record)

    app = _app()
    app.state.bus = bus
    client = TestClient(app)
    resp = client.post(
        "/api/computer-use/switch", json={"provider": "claude-api", "persist": True},
    )

    assert resp.status_code == 200
    keys = [getattr(e, "key", None) for e in events]
    assert "brain.computer_use.provider" in keys
