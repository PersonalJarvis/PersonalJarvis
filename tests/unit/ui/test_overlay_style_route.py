"""GET/PUT /api/settings/overlay-style — read current + persist + live-apply."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(*, orb_style="mascot", desktop=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        ui=SimpleNamespace(orb_style=orb_style, bar_persistent=True, bar_accent="#e7c46e")
    )
    if desktop is not None:
        app.state.desktop_app = desktop
    return TestClient(app)


def test_get_returns_current_and_options():
    r = _client(orb_style="mascot").get("/api/settings/overlay-style")
    assert r.status_code == 200
    body = r.json()
    assert body["style"] == "mascot"
    assert set(body["options"]) == {"jarvis_bar", "mascot", "none"}


def test_put_rejects_unknown_style():
    r = _client().put("/api/settings/overlay-style", json={"style": "bogus"})
    assert r.status_code == 400


def test_put_persists_and_live_applies(monkeypatch):
    persisted = {}
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_overlay_style", lambda style, **k: persisted.setdefault("v", style))

    swapped = {}

    def swap_overlay(style):
        swapped["v"] = style
        return {"ok": True, "applied_live": True, "style": style}

    desktop = SimpleNamespace(swap_overlay=swap_overlay)
    r = _client(orb_style="mascot", desktop=desktop).put(
        "/api/settings/overlay-style", json={"style": "jarvis_bar", "persist": True}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["style"] == "jarvis_bar"
    assert body["persisted"] is True
    assert body["applied_live"] is True
    assert body["restart_required"] is False
    assert persisted["v"] == "jarvis_bar"
    assert swapped["v"] == "jarvis_bar"


def test_put_without_desktop_app_persists_but_needs_restart(monkeypatch):
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_overlay_style", lambda style, **k: None)
    r = _client(orb_style="mascot").put(  # no desktop_app on app.state
        "/api/settings/overlay-style", json={"style": "none", "persist": True}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["applied_live"] is False
    assert body["restart_required"] is True
