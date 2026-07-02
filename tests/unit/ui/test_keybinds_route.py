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


def test_put_rejects_subset_collision() -> None:
    """A combo whose keys are a SUBSET of another action's combo must be
    rejected: with hangup=f1+f2, binding call to bare f1 would make every
    F1+F2 press fire BOTH actions (the polling backend matches subsets)."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f1", "persist": False},
    )
    assert resp.status_code == 400
    assert "hangup" in resp.json()["detail"]


def test_put_rejects_superset_collision() -> None:
    """Superset direction too: with call=f3+f4, binding hangup to f3+f4+f5
    would fire call as soon as F3+F4 land mid-chord."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "f3+f4+f5", "persist": False},
    )
    assert resp.status_code == 400
    assert "call" in resp.json()["detail"]


def test_put_allows_disjoint_combo_sharing_a_modifier() -> None:
    """Sharing a MODIFIER is fine — ctrl+shift+h vs ctrl+right_alt+j do not
    overlap as chords; only key-set subset/superset relations collide."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "ctrl+shift+h", "persist": False},
    )
    assert resp.status_code == 200


def test_put_in_memory_update_reflects_in_get() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == "f7+f8"


def test_put_live_applies_to_running_pipeline() -> None:
    """When a voice pipeline is live, the PUT re-arms it immediately (no
    restart) and reports applied_live + restart_required False."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    body = resp.json()
    assert body["applied_live"] is True
    assert body["restart_required"] is False
    # Only the changed action is re-armed, as a single-combo list.
    assert calls == [{"call": ["f7+f8"]}]


def test_put_live_apply_failure_still_persists(monkeypatch) -> None:
    """A live-apply hiccup must NOT fail the save — it falls back to restart."""
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "set_keybind", lambda *a, **k: None)

    class _BoomPipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            raise RuntimeError("pipeline busy")

    client = _client()
    client.app.state.speech_pipeline = _BoomPipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "ptt", "hotkey": "ctrl+alt+m", "persist": True},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["persisted"] is True
    assert body["applied_live"] is False
    assert body["restart_required"] is True


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


def test_put_empty_hotkey_unbinds_without_validation_error() -> None:
    """An explicit empty hotkey clears the action instead of being rejected
    as an incomplete recording (validate_hotkey normally rejects '')."""
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["hotkey"] == ""


def test_put_empty_hotkey_skips_collision_check() -> None:
    """Clearing hangup must never be rejected as 'overlapping' with call —
    there is nothing left to collide with."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    )
    assert resp.status_code == 200


def test_put_after_clearing_other_action_still_allows_a_new_combo() -> None:
    """Regression for the false-positive collision bug: an unbound OTHER
    action's empty key-set must not be treated as a subset of every new
    combo (an empty set is a mathematical subset of everything), which would
    otherwise reject every future save once any one action is cleared."""
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    )
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 200


def test_put_empty_hotkey_live_applies_empty_list() -> None:
    """The running pipeline is re-armed with an empty list (not [\"\"])."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "ptt", "hotkey": "", "persist": False},
    )
    assert resp.json()["applied_live"] is True
    assert calls == [{"ptt": []}]


def test_get_reflects_cleared_keybind() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == ""
