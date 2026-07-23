"""Integration tests for /api/settings/reply-language.

The desktop "Languages" view writes the Reply Language through this endpoint.
Before this existed the choice died in localStorage and Jarvis ignored it.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _FakeBrain:
    """Mirrors the real BrainManager reply-language surface."""

    def __init__(self, lang: str = "auto") -> None:
        self._reply_language = lang
        self._bus: EventBus | None = None

    @property
    def reply_language(self) -> str:
        return self._reply_language

    def set_reply_language(self, lang: str) -> None:
        code = lang.strip().lower()
        if code not in {"auto", "de", "en", "es"}:
            raise ValueError(f"unknown reply language {lang!r}")
        self._reply_language = code


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    s = WebServer(cfg, bus=bus)
    s.app.state.brain = _FakeBrain("auto")
    s.app.state.config = cfg
    s.app.state.bus = bus
    yield s


@pytest.fixture(autouse=True)
def _no_toml_write(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture persistence calls instead of writing the real jarvis.toml."""
    calls: list[str] = []
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "set_reply_language", lambda name, **kw: calls.append(name))
    return calls


def test_get_returns_current_language_and_options(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/reply-language")
        assert resp.status_code == 200
        body = resp.json()
        assert body["language"] == "auto"
        assert set(body["options"]) == {"auto", "de", "en", "es"}


def test_put_switches_live_brain(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/reply-language", json={"language": "en"})
        assert resp.status_code == 200
        assert resp.json()["language"] == "en"
        assert server.app.state.brain.reply_language == "en"


def test_put_persists_by_default(server: WebServer, _no_toml_write: list[str]) -> None:
    with TestClient(server.app) as client:
        client.put("/api/settings/reply-language", json={"language": "es"})
    assert _no_toml_write == ["es"]


def test_put_rejects_unknown_language(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/reply-language", json={"language": "klingon"})
        assert resp.status_code == 422 or resp.status_code == 400
        # live brain unchanged
        assert server.app.state.brain.reply_language == "auto"


def test_get_503_without_brain(server: WebServer) -> None:
    server.app.state.brain = None
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/reply-language")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Bar size slider (/api/settings/bar-size)
# ---------------------------------------------------------------------------
def test_bar_size_get_returns_default_and_range(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/bar-size")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scale"] == 1.0
        assert body["min"] == 0.5
        assert body["max"] == 2.0


def test_bar_size_put_persists_and_updates_config(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core import config_writer

    written: list[float] = []
    monkeypatch.setattr(config_writer, "set_bar_size_scale", lambda s, **kw: written.append(s))
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/bar-size", json={"scale": 1.5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["scale"] == 1.5
        assert body["persisted"] is True
        # No live desktop app in this harness → not applied live, restart flagged.
        assert body["applied_live"] is False
        assert body["restart_required"] is True
    assert written == [1.5]
    assert server.app.state.config.ui.bar_size_scale == 1.5


def test_bar_size_put_live_applies_to_the_desktop_app(
    server: WebServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "set_bar_size_scale", lambda s, **kw: None)

    class _FakeDesktop:
        def __init__(self) -> None:
            self.applied: list[float] = []

        def set_bar_size(self, scale: float) -> dict[str, object]:
            self.applied.append(scale)
            return {"ok": True, "applied_live": True, "scale": scale}

    desktop = _FakeDesktop()
    server.app.state.desktop_app = desktop
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/bar-size", json={"scale": 0.7, "persist": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["applied_live"] is True
        assert body["restart_required"] is False
        assert body["persisted"] is False  # persist=false → nothing written to disk
    assert desktop.applied == [0.7]


def test_bar_size_put_rejects_out_of_range(server: WebServer) -> None:
    with TestClient(server.app) as client:
        assert client.put("/api/settings/bar-size", json={"scale": 5.0}).status_code == 422
        assert client.put("/api/settings/bar-size", json={"scale": 0.1}).status_code == 422
