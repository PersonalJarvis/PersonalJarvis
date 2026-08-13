"""Integration tests for /api/modes — the assistant's shelf of characters.

These go through the real WebServer, so they also prove the router is actually
mounted: a modes screen talking to a route nobody registered fails as a blank
page, which is the least diagnosable failure this feature has.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import jarvis.core.config as core_config
from jarvis.brain import modes
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User modes go to a throwaway dir; the pointer never reaches jarvis.toml."""
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    stored: dict[str, str] = {}
    monkeypatch.setattr(modes, "_configured_slug", lambda: stored.get("slug", modes.DEFAULT_MODE))
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_active_mode",
        lambda slug, **_kw: stored.__setitem__("slug", slug),
    )
    modes.set_section_override(None)


@pytest.fixture
def client() -> Iterator[TestClient]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    server = WebServer(cfg, bus=bus)
    server.app.state.config = cfg
    server.app.state.bus = bus
    with TestClient(server.app) as c:
        yield c


def test_router_is_mounted_and_lists_the_builtins(client: TestClient) -> None:
    resp = client.get("/api/modes")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["slug"] for m in body["modes"]][:5] == list(modes.BUILTIN_SLUGS)
    assert body["active"] == modes.DEFAULT_MODE


def test_switching_the_active_mode_round_trips(client: TestClient) -> None:
    resp = client.put("/api/modes/active", json={"slug": "friend"})
    assert resp.status_code == 200
    assert resp.json()["active"] == "friend"
    assert resp.json()["restart_required"] is False
    assert client.get("/api/modes").json()["active"] == "friend"


def test_switching_to_an_unknown_mode_is_a_404(client: TestClient) -> None:
    assert client.put("/api/modes/active", json={"slug": "nope"}).status_code == 404


def test_a_mode_created_by_name_alone_gets_a_slug(client: TestClient) -> None:
    """The voice interviewer submits a spoken NAME and no slug — the server derives one."""
    resp = client.post(
        "/api/modes",
        json={"name": "Night Owl", "character": "Speak quietly. It is late."},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"]["slug"] == "night-owl"


def test_creating_a_mode_does_not_switch_to_it(client: TestClient) -> None:
    """Two separate acts — building a character is not the same as becoming it."""
    client.post("/api/modes", json={"name": "Night Owl", "character": "Quietly."})
    assert client.get("/api/modes").json()["active"] == modes.DEFAULT_MODE


def test_an_unknown_knob_value_is_rejected(client: TestClient) -> None:
    """The Literal guard: a value the backend does not know never reaches disk."""
    resp = client.post(
        "/api/modes",
        json={"name": "Loud", "character": "Shout.", "verbosity": "very-loud"},
    )
    assert resp.status_code == 422


def test_a_path_shaped_name_is_refused(client: TestClient) -> None:
    resp = client.post("/api/modes", json={"name": "../escape", "character": "x"})
    assert resp.status_code == 400


def test_builtins_cannot_be_deleted_but_a_copy_can(client: TestClient) -> None:
    assert client.delete("/api/modes/friend").status_code == 400

    client.post("/api/modes", json={"name": "Friend", "character": "My own words."})
    assert client.get("/api/modes/friend").json()["character"] == "My own words."

    assert client.post("/api/modes/friend/restore").json()["restored"] is True
    assert "not serving a client" in client.get("/api/modes/friend").json()["character"]


def test_the_response_reports_the_mode_actually_in_force(client: TestClient) -> None:
    """A section override wins; echoing the request back would be a small lie."""
    modes.set_section_override(modes.MODE_CODING)
    try:
        body = client.put("/api/modes/active", json={"slug": "friend"}).json()
        assert body["chosen"] == "friend"
        assert body["active"] == modes.MODE_CODING
        assert body["section_override"] == modes.MODE_CODING
    finally:
        modes.set_section_override(None)
