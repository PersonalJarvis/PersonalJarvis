"""GET/PUT /api/settings/ptt-hotkey — the editable push-to-talk hotkey surface."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(hotkey: str = "ctrl+right_alt+j", push_to_talk: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        trigger=SimpleNamespace(hotkey=hotkey, push_to_talk=push_to_talk)
    )
    return TestClient(app)


def test_get_returns_current_default_and_suggestions() -> None:
    body = _client().get("/api/settings/ptt-hotkey").json()
    assert body["hotkey"] == "ctrl+right_alt+j"
    assert body["push_to_talk"] is True
    assert body["default"] == "ctrl+right_alt+j"
    assert "ctrl+right_alt+j" in body["suggestions"]
    assert len(body["suggestions"]) >= 3


def test_put_accepts_safe_combo() -> None:
    resp = _client().put(
        "/api/settings/ptt-hotkey",
        json={"hotkey": "ctrl+right_alt+k", "persist": False},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["hotkey"] == "ctrl+right_alt+k"
    assert body["restart_required"] is True
    assert body["persisted"] is False


def test_put_rejects_unsafe_combo() -> None:
    # A single bare key would fire on every keystroke — backend must reject it.
    resp = _client().put(
        "/api/settings/ptt-hotkey",
        json={"hotkey": "j", "persist": False},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]  # carries a user-facing reason


def test_put_rejects_os_critical_combo() -> None:
    resp = _client().put(
        "/api/settings/ptt-hotkey",
        json={"hotkey": "alt+f4", "persist": False},
    )
    assert resp.status_code == 400


def test_put_in_memory_update_reflects_in_get() -> None:
    client = _client()
    client.put(
        "/api/settings/ptt-hotkey",
        json={"hotkey": "ctrl+shift+space", "persist": False},
    )
    body = client.get("/api/settings/ptt-hotkey").json()
    assert body["hotkey"] == "ctrl+shift+space"


def test_put_persist_calls_config_writer(monkeypatch) -> None:
    from jarvis.core import config_writer

    captured: dict = {}

    def _fake_set_ptt_hotkey(hotkey, *, path=None):  # noqa: ANN001
        captured["hotkey"] = hotkey

    monkeypatch.setattr(config_writer, "set_ptt_hotkey", _fake_set_ptt_hotkey)

    resp = _client().put(
        "/api/settings/ptt-hotkey",
        json={"hotkey": "ctrl+alt+m", "persist": True},
    )
    assert resp.status_code == 200
    assert resp.json()["persisted"] is True
    assert captured["hotkey"] == "ctrl+alt+m"


def test_put_normalizes_case() -> None:
    """A mixed-case combo from a key-capture is normalized to lowercase."""
    body = _client().put(
        "/api/settings/ptt-hotkey",
        json={"hotkey": "Ctrl+Right_Alt+M", "persist": False},
    ).json()
    assert body["hotkey"] == "ctrl+right_alt+m"
