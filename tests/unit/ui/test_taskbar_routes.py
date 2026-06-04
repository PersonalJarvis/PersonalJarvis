"""GET/PUT /api/settings/bar-persistent + /mute-music — persist + live-apply."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(*, bar_persistent=True, ducking_enabled=False, desktop=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        ui=SimpleNamespace(bar_persistent=bar_persistent),
        ducking=SimpleNamespace(enabled=ducking_enabled),
    )
    if desktop is not None:
        app.state.desktop_app = desktop
    return TestClient(app)


def test_get_bar_persistent():
    r = _client(bar_persistent=True).get("/api/settings/bar-persistent")
    assert r.status_code == 200 and r.json()["enabled"] is True


def test_put_bar_persistent_live(monkeypatch):
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_bar_persistent", lambda v, **k: None)
    applied = {}
    desktop = SimpleNamespace(
        set_bar_persistent=lambda v: applied.setdefault("v", v) or {"applied_live": True}
    )
    r = _client(desktop=desktop).put(
        "/api/settings/bar-persistent", json={"enabled": False}
    )
    assert r.status_code == 200
    assert applied["v"] is False and r.json()["applied_live"] is True


def test_get_mute_music():
    r = _client(ducking_enabled=False).get("/api/settings/mute-music")
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_put_mute_music_live(monkeypatch):
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_mute_music", lambda v, **k: None)
    seen = {}

    async def _set_enabled(v):
        seen["v"] = v

    desktop = SimpleNamespace(_ducker=SimpleNamespace(set_enabled=_set_enabled))
    r = _client(desktop=desktop).put(
        "/api/settings/mute-music", json={"enabled": True}
    )
    assert r.status_code == 200
    assert seen["v"] is True and r.json()["applied_live"] is True
