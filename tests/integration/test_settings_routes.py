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
