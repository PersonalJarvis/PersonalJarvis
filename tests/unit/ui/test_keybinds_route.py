"""GET/PUT /api/settings/keybinds — editable Call/Hangup/Talk keybinds."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(**trig) -> TestClient:
    defaults = dict(
        hotkey="ctrl+right_alt+j",
        hotkey_call="f3+f4",
        hotkey_hangup="f1+f2",
        push_to_talk=True,
    )
    defaults.update(trig)
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(trigger=SimpleNamespace(**defaults))
    return TestClient(app)


def test_get_returns_all_three_plus_defaults() -> None:
    body = _client().get("/api/settings/keybinds").json()
    assert body["keybinds"] == {
        "call": "f3+f4",
        "hangup": "f1+f2",
        "ptt": "ctrl+right_alt+j",
    }
    assert body["defaults"]["call"] == "f3+f4"
    assert body["restart_required"] is True
    assert len(body["suggestions"]) >= 3


def test_put_call_accepts_and_normalizes_case() -> None:
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "F7+F8", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["action"] == "call"
    assert body["hotkey"] == "f7+f8"
    assert body["restart_required"] is True


def test_put_rejects_unsafe_combo() -> None:
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "j", "persist": False},
    )
    assert resp.status_code == 400


def test_put_rejects_unknown_action() -> None:
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "mute", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 400


def test_put_rejects_collision_with_other_action() -> None:
    # call defaults to f3+f4; binding hangup to the same combo must be rejected.
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "f3+f4", "persist": False},
    )
    assert resp.status_code == 400
    assert "call" in resp.json()["detail"]


def test_put_in_memory_update_reflects_in_get() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == "f7+f8"


def test_put_persist_calls_config_writer(monkeypatch) -> None:
    from jarvis.core import config_writer

    captured: dict = {}

    def _fake_set_keybind(action, hotkey, *, path=None):  # noqa: ANN001
        captured["action"] = action
        captured["hotkey"] = hotkey

    monkeypatch.setattr(config_writer, "set_keybind", _fake_set_keybind)

    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "ctrl+shift+h", "persist": True},
    )
    assert resp.status_code == 200
    assert resp.json()["persisted"] is True
    assert captured == {"action": "hangup", "hotkey": "ctrl+shift+h"}
