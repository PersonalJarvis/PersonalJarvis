"""Observability tests for the /api/brain/switch persistence outcome.

Repo invariant AD-OE6 (anti-silent-drop): the route must report the ACTUAL
disk outcome of the persist write, not echo the request flag. A failed write
must surface as ``"persisted": false`` so the UI knows the choice will not
survive a restart — never a silent success.

These tests use a FakeBrainManager and monkeypatch ``config_writer`` so they
never touch the live jarvis.toml, config-soll.json, or registry.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _InMemorySecretStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str, env_fallback: str | None = None) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True

    def delete(self, key: str) -> bool:
        self.data.pop(key, None)
        return True


class _RealishBrainManager:
    """Brain stub that delegates persistence to the REAL _persist_primary.

    Mirrors the production BrainManager.switch persist path so we exercise the
    real outcome-threading (last_persist_ok), while config_writer is mocked.
    """

    def __init__(self, *, available: list[str], active: str = "openai") -> None:
        self._available = available
        self.active_provider = active
        self.last_persist_ok: bool | None = None
        self.calls: list[tuple[str, bool]] = []

    def available_providers(self) -> list[str]:
        return list(self._available)

    async def switch(self, provider: str, *, persist: bool = False) -> None:
        self.calls.append((provider, persist))
        self.active_provider = provider
        if persist:
            # Same call chain as production: BrainManager._persist_primary.
            from jarvis.brain.manager import BrainManager

            self.last_persist_ok = BrainManager._persist_primary(provider)
        else:
            self.last_persist_ok = False


@pytest.fixture
def secret_store(monkeypatch: pytest.MonkeyPatch) -> _InMemorySecretStore:
    store = _InMemorySecretStore()
    from jarvis.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_secret", store.get)
    monkeypatch.setattr(cfg_mod, "set_secret", store.set)
    monkeypatch.setattr(cfg_mod, "delete_secret", store.delete)
    return store


@pytest.fixture
def web_server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    server = WebServer(cfg, bus=bus)
    yield server


@pytest.fixture
def server_with_brain(web_server: WebServer) -> WebServer:
    web_server.app.state.brain = _RealishBrainManager(
        available=["openai", "claude-api", "openrouter", "codex"],
        active="openai",
    )
    web_server.app.state.cfg = web_server.cfg
    web_server.app.state.bus = web_server.bus
    return web_server


def test_persisted_true_on_successful_write(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful config_writer write → response reports persisted=true."""
    secret_store.set("openrouter_api_key", "sk-or-test-123")
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_primary",
        lambda name, **kw: None,  # succeeds
    )
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/brain/switch", json={"provider": "openrouter", "persist": True}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] == "openrouter"
        assert body["persisted"] is True


def test_persisted_false_when_writer_raises(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If config_writer raises, the route must NOT report success — the
    response carries persisted=false (anti-silent-drop AD-OE6)."""
    secret_store.set("openrouter_api_key", "sk-or-test-123")

    def _boom(name: str, **kw: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("jarvis.core.config_writer.set_brain_primary", _boom)

    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/brain/switch", json={"provider": "openrouter", "persist": True}
        )
        # Switch itself succeeds (live provider changed) — only persistence failed.
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] == "openrouter"
        assert body["persisted"] is False


def test_persisted_false_when_persist_not_requested(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """persist=false → no disk write, response reports persisted=false."""
    secret_store.set("openrouter_api_key", "sk-or-test-123")
    writes: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_primary",
        lambda name, **kw: writes.append(name),
    )
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/brain/switch", json={"provider": "openrouter", "persist": False}
        )
        assert resp.status_code == 200
        assert resp.json()["persisted"] is False
    assert writes == []


# ----------------------------------------------------------------------
# BrainManager._persist_primary returns a real bool.
# ----------------------------------------------------------------------


def test_persist_primary_returns_true_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.brain.manager import BrainManager

    monkeypatch.setattr(
        "jarvis.core.config_writer.set_brain_primary", lambda name, **kw: None
    )
    assert BrainManager._persist_primary("openai") is True


def test_persist_primary_returns_false_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.brain.manager import BrainManager

    def _boom(name: str, **kw: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("jarvis.core.config_writer.set_brain_primary", _boom)
    assert BrainManager._persist_primary("openai") is False
