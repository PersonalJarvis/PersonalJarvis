"""Regression: the API-Keys "SUBAGENT (HEAVY TASKS)" section must mark the
brain that ACTUALLY executes heavy tasks as active.

Root cause (2026-05-28): GET /api/openclaw/status derived ``is_active_brain``
from ``cfg.brain.primary`` — that is only the lightweight ROUTER brain
(Gemini). The heavy-task subagent runs on ``[brain.sub_jarvis].provider``
(claude-api -> ClaudeDirectWorker -> Claude Max OAuth). The UI therefore
showed "Google Gemini · aktiver Brain" while heavy work ran on Claude.

These tests pin the active brain to the subagent provider so the displayed
brain never drifts from the worker that runs (mirrors the routing source in
jarvis/missions/init.py::_worker_factory).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import BrainTierConfig, load_config
from jarvis.missions.worker_runtime.provider_map import canonical_subagent_provider
from jarvis.ui.web.server import WebServer

# --- pure helper: canonical subagent (display) provider -------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("claude-api", "claude-api"),
        # OpenClaw transport, but still the Claude brain -> display as claude.
        ("openclaw-claude", "claude-api"),
        ("  Claude-API  ", "claude-api"),  # stripped + lower-cased
        ("GEMINI", "gemini"),
        ("grok", "grok"),
        ("openrouter", "openrouter"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_canonical_subagent_provider(raw, expected) -> None:
    assert canonical_subagent_provider(raw) == expected


# --- endpoint: active brain follows the subagent provider -----------------


def _status(cfg) -> dict:
    bus = EventBus()
    ws = WebServer(bus=bus, cfg=cfg)
    client = TestClient(ws.app)
    resp = client.get("/api/openclaw/status")
    assert resp.status_code == 200
    return resp.json()


def test_active_brain_follows_sub_jarvis_not_router() -> None:
    """Router = gemini, heavy worker = claude-api. Claude must be active."""
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.sub_jarvis = BrainTierConfig(provider="claude-api", model="")

    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}

    assert active["claude-api"] is True, "the real heavy worker must be active"
    assert active["gemini"] is False, "gemini is only the router, not the subagent"
    assert data["brain_primary"] == "claude-api"
    assert data["provider_slug"] == "claude-cli"


def test_active_brain_follows_sub_jarvis_gemini() -> None:
    """When the subagent provider IS gemini, gemini is correctly active."""
    cfg = load_config()
    cfg.brain.primary = "claude-api"
    cfg.brain.sub_jarvis = BrainTierConfig(provider="gemini", model="")

    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}

    assert active["gemini"] is True
    assert active["claude-api"] is False
    assert data["provider_slug"] == "google"


def test_openclaw_claude_alias_marks_claude_active() -> None:
    """The 'openclaw-claude' alias is still the Claude brain for display."""
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.sub_jarvis = BrainTierConfig(provider="openclaw-claude", model="")

    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}

    assert active["claude-api"] is True
    assert active["gemini"] is False


# --- endpoint: POST /api/subagent/switch ----------------------------------


def _client(cfg) -> TestClient:
    return TestClient(WebServer(bus=EventBus(), cfg=cfg).app)


def test_subagent_switch_persists_and_restart_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switch to a key-present provider: 200, persisted, restart_required."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    # Key present for every provider in this test.
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "fake-key")
    # Do NOT touch the real jarvis.toml — record the persist call instead.
    calls: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda name: calls.append(name))

    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.sub_jarvis = BrainTierConfig(provider="claude-api", model="")

    resp = _client(cfg).post("/api/subagent/switch", json={"provider": "gemini", "persist": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active"] == "gemini"
    assert body["persisted"] is True
    assert body["restart_required"] is True
    assert calls == ["gemini"], "set_sub_jarvis_provider must be called with the new provider"


def test_subagent_switch_404_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "fake-key")
    cfg = load_config()
    resp = _client(cfg).post("/api/subagent/switch", json={"provider": "banana"})
    assert resp.status_code == 404


def test_subagent_switch_409_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider without a stored key cannot be activated (409, no silent ok)."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: None)
    persisted: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda name: persisted.append(name))

    cfg = load_config()
    resp = _client(cfg).post("/api/subagent/switch", json={"provider": "openai"})
    assert resp.status_code == 409
    assert persisted == [], "must not persist a provider that has no key"


def test_subagent_switch_updates_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a switch, /api/openclaw/status reflects the new active provider."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "fake-key")
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda name: None)

    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.sub_jarvis = BrainTierConfig(provider="claude-api", model="")
    client = _client(cfg)

    client.post("/api/subagent/switch", json={"provider": "grok", "persist": True})

    data = client.get("/api/openclaw/status").json()
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}
    assert active["grok"] is True
    assert active["claude-api"] is False
