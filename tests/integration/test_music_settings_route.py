"""GET/PUT /api/settings/music — preferred music service + where YouTube Music
plays (2026-08-18). JARVIS_CONFIG points the writer at a temp file so the real
jarvis.toml is never touched; the connection probe and the player probe are
faked so no keyring or window is involved.
"""
from __future__ import annotations

import tomllib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer
from jarvis.ui.web import settings_routes


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    config_file = tmp_path / "jarvis.toml"
    config_file.write_text('[ui]\nlanguage = "en"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(config_file))
    monkeypatch.setattr(
        settings_routes, "_connected_music_services", lambda: ["spotify", "youtube_music"]
    )
    monkeypatch.setattr(settings_routes, "_background_player_available", lambda: True)
    app = FastAPI()
    app.state.config = cfg_mod.load_config()
    app.include_router(settings_routes.router)
    return TestClient(app), config_file


def test_get_returns_defaults_options_and_context(ctx) -> None:
    tc, _ = ctx
    body = tc.get("/api/settings/music").json()
    assert body["preferred_service"] == "auto" and body["playback"] == "background"
    assert set(body["service_options"]) == {"auto", "spotify", "youtube_music"}
    assert set(body["playback_options"]) == {"background", "browser"}
    assert body["connected"] == ["spotify", "youtube_music"]
    assert body["background_player_available"] is True


def test_put_persists_each_field_independently(ctx) -> None:
    tc, config_file = ctx
    res = tc.put("/api/settings/music", json={"preferred_service": "youtube_music"})
    assert res.status_code == 200, res.text
    assert res.json() == {
        "ok": True,
        "preferred_service": "youtube_music",
        "playback": "background",
        "persisted": True,
    }
    res = tc.put("/api/settings/music", json={"playback": "browser"})
    assert res.json()["preferred_service"] == "youtube_music"
    assert res.json()["playback"] == "browser"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert data["music"] == {"preferred_service": "youtube_music", "playback": "browser"}
    assert data["ui"]["language"] == "en"  # sibling preserved
    body = tc.get("/api/settings/music").json()
    assert body["preferred_service"] == "youtube_music" and body["playback"] == "browser"


def test_put_rejects_unknown_values_with_the_allowed_list(ctx) -> None:
    tc, _ = ctx
    res = tc.put("/api/settings/music", json={"preferred_service": "deezer"})
    assert res.status_code == 400 and "youtube_music" in res.json()["detail"]
    res = tc.put("/api/settings/music", json={"playback": "tab"})
    assert res.status_code == 400 and "background" in res.json()["detail"]


def test_writer_round_trip_preserves_siblings(tmp_path) -> None:
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "jarvis_bar"\n', encoding="utf-8")
    config_writer.set_preferred_music_service("spotify", path=cfg)
    config_writer.set_music_playback("browser", path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["music"] == {"preferred_service": "spotify", "playback": "browser"}
    assert data["ui"]["orb_style"] == "jarvis_bar"
