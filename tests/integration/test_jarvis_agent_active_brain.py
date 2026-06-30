"""Regression: the API-Keys "SUBAGENT (HEAVY TASKS)" section must mark the
brain that ACTUALLY executes heavy tasks as active.

Root cause (2026-05-28): GET /api/jarvis-agent/status derived ``is_active_brain``
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
from jarvis.missions.worker_runtime.provider_map import canonical_worker_provider
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
def test_canonical_worker_provider(raw, expected) -> None:
    assert canonical_worker_provider(raw) == expected


# --- endpoint: active brain follows the subagent provider -----------------


def _status(cfg) -> dict:
    bus = EventBus()
    ws = WebServer(bus=bus, cfg=cfg)
    client = TestClient(ws.app)
    resp = client.get("/api/jarvis-agent/status")
    assert resp.status_code == 200
    return resp.json()


def test_active_brain_follows_sub_jarvis_not_router() -> None:
    """Router = gemini, heavy worker = claude-api. Claude must be active."""
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.worker = BrainTierConfig(provider="claude-api", model="")

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
    cfg.brain.worker = BrainTierConfig(provider="gemini", model="")

    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}

    assert active["gemini"] is True
    assert active["claude-api"] is False
    assert data["provider_slug"] == "google"


def test_openclaw_claude_alias_marks_claude_active() -> None:
    """The 'openclaw-claude' alias is still the Claude brain for display."""
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.worker = BrainTierConfig(provider="openclaw-claude", model="")

    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}

    assert active["claude-api"] is True
    assert active["gemini"] is False


# --- endpoint: POST /api/jarvis-agent/switch ----------------------------------


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
    cfg.brain.worker = BrainTierConfig(provider="claude-api", model="")

    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "gemini", "persist": True})
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
    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "banana"})
    assert resp.status_code == 404


def test_subagent_switch_409_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider without a stored key cannot be activated (409, no silent ok)."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: None)
    persisted: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda name: persisted.append(name))

    cfg = load_config()
    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "openai"})
    assert resp.status_code == 409
    assert persisted == [], "must not persist a provider that has no key"


def test_subagent_switch_updates_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a switch, /api/jarvis-agent/status reflects the new active provider."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "fake-key")
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda name: None)

    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.worker = BrainTierConfig(provider="claude-api", model="")
    client = _client(cfg)

    client.post("/api/jarvis-agent/switch", json={"provider": "openrouter", "persist": True})

    data = client.get("/api/jarvis-agent/status").json()
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}
    assert active["openrouter"] is True
    assert active["claude-api"] is False


# --- Codex as a subagent (direct worker, not an OpenClaw MAPPINGS row) -----


class _FakeCodex:
    def __init__(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        pass

    connected_value = True

    def status(self):  # noqa: ANN201
        from jarvis.codex_auth import CodexAuthStatus

        return CodexAuthStatus(
            installed=True, connected=type(self).connected_value, mode="chatgpt"
        )


def _patch_codex(monkeypatch: pytest.MonkeyPatch, *, connected: bool) -> None:
    """Stub CodexAuthService in both the route + the status endpoint paths."""
    _FakeCodex.connected_value = connected
    monkeypatch.setattr("jarvis.ui.web.provider_routes.CodexAuthService", _FakeCodex)
    monkeypatch.setattr("jarvis.codex_auth.CodexAuthService", _FakeCodex)


def test_openclaw_status_includes_codex_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex is selectable as a subagent even though it has no OpenClaw slug."""
    _patch_codex(monkeypatch, connected=False)
    cfg = load_config()
    data = _status(cfg)
    slugs = {r["jarvis"] for r in data["mapping"]}
    assert "openai-codex" in slugs


def test_codex_subagent_active_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_codex(monkeypatch, connected=True)
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.worker = BrainTierConfig(provider="openai-codex", model="")
    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}
    assert active["openai-codex"] is True
    assert active["gemini"] is False


def test_subagent_switch_accepts_codex_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """ChatGPT-subscription (OAuth) connected, no API key -> codex selectable."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    _patch_codex(monkeypatch, connected=True)
    calls: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: calls.append(n))

    cfg = load_config()
    resp = _client(cfg).post(
        "/api/jarvis-agent/switch", json={"provider": "openai-codex", "persist": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "openai-codex"
    assert calls == ["openai-codex"]


def test_subagent_switch_codex_api_key_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OAuth, but a saved Codex API key is enough to select the subagent."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(
        cfg_mod, "get_secret",
        lambda key, *a, **k: "sk-codex" if key == "codex_openai_api_key" else None,
    )
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    _patch_codex(monkeypatch, connected=False)
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: None)

    cfg = load_config()
    resp = _client(cfg).post(
        "/api/jarvis-agent/switch", json={"provider": "openai-codex", "persist": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "openai-codex"


def test_subagent_switch_chatgpt_alias_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'chatgpt' alias resolves to the canonical 'openai-codex' slug."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    _patch_codex(monkeypatch, connected=True)
    calls: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: calls.append(n))

    cfg = load_config()
    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "chatgpt"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "openai-codex"
    assert calls == ["openai-codex"]


def test_subagent_switch_409_codex_when_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither OAuth nor API key -> 409, nothing persisted."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    _patch_codex(monkeypatch, connected=False)
    persisted: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: persisted.append(n))

    cfg = load_config()
    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "openai-codex"})
    assert resp.status_code == 409
    assert persisted == []


# --- Claude Max OAuth as a subagent credential (no API key needed) ---------
# The ClaudeDirectWorker authenticates via the live Claude Max OAuth login in
# ~/.claude/.credentials.json (read_live_claude_oauth_token), NOT a stored
# Anthropic API key. A fresh Claude-Max user who only ran `claude login` must
# therefore see claude-api unlocked + selectable, mirroring the codex/agy OAuth
# rows above. Source of the worker auth: missions/init.py::_worker_factory.


def test_claude_api_row_key_set_via_max_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key anywhere, but a live Claude Max OAuth login -> row unlocked."""
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.read_live_claude_oauth_token",
        lambda: "sk-ant-oat-live",
    )
    cfg = load_config()
    row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "claude-api")
    assert row["key_set"] is True


def test_claude_api_row_locked_without_key_or_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.read_live_claude_oauth_token", lambda: None
    )
    cfg = load_config()
    row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "claude-api")
    assert row["key_set"] is False


def test_subagent_switch_accepts_claude_max_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """OAuth login present, no API key -> claude-api selectable as subagent."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.read_live_claude_oauth_token",
        lambda: "sk-ant-oat-live",
    )
    calls: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: calls.append(n))

    cfg = load_config()
    resp = _client(cfg).post(
        "/api/jarvis-agent/switch", json={"provider": "claude-api", "persist": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "claude-api"
    assert calls == ["claude-api"]


def test_subagent_switch_409_claude_api_no_key_no_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.read_live_claude_oauth_token", lambda: None
    )
    persisted: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: persisted.append(n))

    cfg = load_config()
    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "claude-api"})
    assert resp.status_code == 409
    assert persisted == []


# --- Antigravity as a subagent (direct GoogleCliWorker, no OpenClaw slug) ---
# The Google sibling of Codex: a selectable subagent row backed by the Google
# subscription OAuth login (no API key), so the user can run heavy tasks over
# agy — exactly like picking OpenRouter/Grok/Claude, never hardcoded.


class _FakeGoogleCli:
    connected_value = True

    def __init__(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        pass

    def status(self):  # noqa: ANN201
        from jarvis.google_cli.auth_service import GoogleCliAuthStatus

        return GoogleCliAuthStatus(
            installed=True,
            connected=type(self).connected_value,
            mode="oauth-personal",
            cli_kind="agy",
        )


def _patch_antigravity(monkeypatch: pytest.MonkeyPatch, *, connected: bool) -> None:
    """Stub GoogleCliAuthService at its source so both the status endpoint and
    the switch route (each lazy-imports it) pick up the fake."""
    _FakeGoogleCli.connected_value = connected
    monkeypatch.setattr(
        "jarvis.google_cli.auth_service.GoogleCliAuthService", _FakeGoogleCli
    )


def test_openclaw_status_includes_antigravity_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Antigravity is selectable as a subagent even though it has no OpenClaw slug."""
    _patch_antigravity(monkeypatch, connected=False)
    cfg = load_config()
    data = _status(cfg)
    slugs = {r["jarvis"] for r in data["mapping"]}
    assert "antigravity" in slugs


def test_antigravity_row_key_set_reflects_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """With NO Gemini API key, the row is unlocked iff the Google OAuth login is
    connected. (The Gemini-key path is covered separately below.)"""
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    _patch_antigravity(monkeypatch, connected=True)
    cfg = load_config()
    row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "antigravity")
    assert row["key_set"] is True
    _patch_antigravity(monkeypatch, connected=False)
    row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "antigravity")
    assert row["key_set"] is False


def test_antigravity_subagent_active_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_antigravity(monkeypatch, connected=True)
    cfg = load_config()
    cfg.brain.primary = "gemini"
    cfg.brain.worker = BrainTierConfig(provider="antigravity", model="")
    data = _status(cfg)
    active = {r["jarvis"]: r["is_active_brain"] for r in data["mapping"]}
    assert active["antigravity"] is True
    assert active["gemini"] is False  # gemini is only the router, not the subagent


def test_subagent_switch_accepts_antigravity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google OAuth connected, no API key -> antigravity selectable as subagent."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    _patch_antigravity(monkeypatch, connected=True)
    calls: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: calls.append(n))

    cfg = load_config()
    resp = _client(cfg).post(
        "/api/jarvis-agent/switch", json={"provider": "antigravity", "persist": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "antigravity"
    assert calls == ["antigravity"]


def test_subagent_switch_409_antigravity_when_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not signed in with Google AND no Gemini key -> 409, nothing persisted."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(cfg_mod, "get_secret", lambda *_a, **_k: None)
    _patch_antigravity(monkeypatch, connected=False)
    persisted: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: persisted.append(n))

    cfg = load_config()
    resp = _client(cfg).post("/api/jarvis-agent/switch", json={"provider": "antigravity"})
    assert resp.status_code == 409
    assert persisted == []


# --- Antigravity DUAL billing: subscription OAuth OR a Gemini API key ---------
# Mirror of Codex (subscription_or_api). A user with no Google login but a Gemini
# API key set can still run the Antigravity subagent, billed per token.


def test_antigravity_row_key_set_via_gemini_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OAuth login, but a Gemini API key is set -> the row unlocks (API billing)."""
    import jarvis.core.config as cfg_mod

    _patch_antigravity(monkeypatch, connected=False)
    monkeypatch.setattr(
        cfg_mod, "get_secret",
        lambda key, *a, **k: "AIza-fake" if key == "gemini_api_key" else None,
    )
    cfg = load_config()
    row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "antigravity")
    assert row["key_set"] is True


def test_antigravity_row_billing_is_subscription_or_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row exposes its billing mode so the UI can badge it."""
    _patch_antigravity(monkeypatch, connected=True)
    cfg = load_config()
    row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "antigravity")
    assert row["billing"] == "subscription_or_api"
    codex_row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "openai-codex")
    assert codex_row["billing"] == "subscription_or_api"
    gemini_row = next(r for r in _status(cfg)["mapping"] if r["jarvis"] == "gemini")
    assert gemini_row["billing"] == "api"


def test_subagent_switch_accepts_antigravity_via_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OAuth, but a Gemini API key -> antigravity selectable (per-token billing)."""
    import jarvis.core.config as cfg_mod
    import jarvis.core.config_writer as config_writer

    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda _p: None)
    monkeypatch.setattr(
        cfg_mod, "get_secret",
        lambda key, *a, **k: "AIza-fake" if key == "gemini_api_key" else None,
    )
    _patch_antigravity(monkeypatch, connected=False)
    calls: list[str] = []
    monkeypatch.setattr(config_writer, "set_sub_jarvis_provider", lambda n: calls.append(n))

    cfg = load_config()
    resp = _client(cfg).post(
        "/api/jarvis-agent/switch", json={"provider": "antigravity", "persist": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "antigravity"
    assert calls == ["antigravity"]
