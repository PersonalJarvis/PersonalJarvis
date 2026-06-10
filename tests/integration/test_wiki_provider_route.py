"""Integration tests for /api/settings/wiki-provider.

The desktop API-Keys view writes the Wiki-curator model through this endpoint.
It reads/writes [memory.wiki.curator].provider/.model and applies the choice
live to a running WikiCurator.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _FakeBrainManager:
    """Mirrors BrainManager.available_providers (the only surface used here)."""

    def available_providers(self) -> list[str]:
        return ["gemini", "claude-api", "grok"]


class _FakeLLM:
    """Stand-in for WikiCuratorLLM: holds the live cfg + a cached brain."""

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._brain = object()  # a non-None cached brain to be cleared
        self._resolved_provider = "gemini"
        self._resolved_model = "gemini-3-flash-preview"


class _FakeCurator:
    def __init__(self, cfg) -> None:
        self._llm = _FakeLLM(cfg)


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    s = WebServer(cfg, bus=bus)
    s.app.state.brain = _FakeBrainManager()
    s.app.state.config = cfg
    s.app.state.bus = bus
    yield s


@pytest.fixture(autouse=True)
def _no_toml_write(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Capture persistence calls instead of writing the real jarvis.toml."""
    calls: list[tuple[str, str]] = []
    from jarvis.core import config_writer

    def _capture(name, *, model="", path=None):  # noqa: ANN001
        calls.append((name, model))

    monkeypatch.setattr(config_writer, "set_wiki_curator_provider", _capture)
    return calls


@pytest.fixture(autouse=True)
def _no_running_curator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: no live curator, auto-restored after the test.

    Reset the module-level registry to ``None`` so a headless run is the
    baseline; individual tests install a fake via ``_set_running_curator``.
    ``monkeypatch.setattr`` restores the original global on teardown so a fake
    installed mid-test never leaks into the next test.
    """
    from jarvis.memory.wiki import integration

    monkeypatch.setattr(integration, "_running_curator", None, raising=False)


def test_get_returns_current_config_and_available(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/wiki-provider")
        assert resp.status_code == 200
        body = resp.json()
        # Fresh JarvisConfig: empty provider/model = "follow brain.primary".
        assert body["provider"] == ""
        assert body["model"] == ""
        # available is a list of OBJECTS {provider, models[]}, not flat strings.
        assert isinstance(body["available"], list)
        assert all(
            isinstance(row, dict) and "provider" in row and "models" in row
            for row in body["available"]
        )
        provs = {row["provider"] for row in body["available"]}
        assert {"gemini", "claude-api", "grok"} <= provs
        # Cheap/fast router model is listed first for each provider.
        gemini = next(r for r in body["available"] if r["provider"] == "gemini")
        assert gemini["models"][0] == "gemini-3-flash-preview"


def test_put_persists_by_default(
    server: WebServer, _no_toml_write: list[tuple[str, str]]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/wiki-provider",
            json={"provider": "claude-api", "model": ""},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "claude-api"
        assert body["persisted"] is True
    assert _no_toml_write == [("claude-api", "")]
    # In-memory cfg updated so a later read agrees pre-restart.
    assert server.app.state.config.memory.wiki.curator.provider == "claude-api"


def test_put_applies_live_to_running_curator(server: WebServer) -> None:
    from jarvis.memory.wiki import integration

    # The autouse _no_running_curator fixture restores the module global on
    # teardown, so no explicit reset is needed here.
    curator = _FakeCurator(server.app.state.config.memory.wiki.curator)
    integration._set_running_curator(curator)
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/wiki-provider",
            json={"provider": "grok", "model": "grok-4.3"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied_live"] is True
        assert body["restart_required"] is False
    # Live cfg mutated + cached brain cleared so the next ingest re-resolves.
    assert curator._llm._cfg.provider == "grok"
    assert curator._llm._cfg.model == "grok-4.3"
    assert curator._llm._brain is None


def test_put_rejects_unknown_provider(
    server: WebServer, _no_toml_write: list[tuple[str, str]]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/wiki-provider",
            json={"provider": "definitely-not-a-provider", "model": ""},
        )
        assert resp.status_code == 400
    # Neither persisted nor applied to the in-memory cfg.
    assert _no_toml_write == []
    assert server.app.state.config.memory.wiki.curator.provider == ""


def test_put_empty_provider_is_valid_follow_brain(
    server: WebServer, _no_toml_write: list[tuple[str, str]]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/wiki-provider", json={"provider": "", "model": ""}
        )
        assert resp.status_code == 200
        assert resp.json()["provider"] == ""
    assert _no_toml_write == [("", "")]


def test_put_empty_strings_round_trip_verbatim(
    server: WebServer, _no_toml_write: list[tuple[str, str]]
) -> None:
    """provider="" and model="" are sentinels — persisted/echoed verbatim."""
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/wiki-provider", json={"provider": "", "model": ""}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == ""
        assert body["model"] == ""
    # Persisted verbatim (no coercion of "" to brain.primary at write time).
    assert _no_toml_write == [("", "")]
    assert server.app.state.config.memory.wiki.curator.provider == ""
    assert server.app.state.config.memory.wiki.curator.model == ""


def test_put_persist_false_skips_writer(
    server: WebServer, _no_toml_write: list[tuple[str, str]]
) -> None:
    """persist=False updates the in-memory cfg only — no disk write."""
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/settings/wiki-provider",
            json={"provider": "gemini", "model": "", "persist": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "gemini"
        assert body["persisted"] is False
    # The writer was never called.
    assert _no_toml_write == []
    # In-memory cfg still updated (persist=False is "live only, not boot default").
    assert server.app.state.config.memory.wiki.curator.provider == "gemini"
