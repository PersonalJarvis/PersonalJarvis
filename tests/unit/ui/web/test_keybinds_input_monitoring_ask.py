"""PUT /api/settings/keybinds: SAVING a global shortcut is the gesture that asks macOS.

A global shortcut is a background listener, so nothing the user does at the moment
they press it can carry a system dialog. The one moment that can is the save: on
macOS, when the key tap is not already listening, the route asks for Input
Monitoring through the permission service with ``wait_s=0`` (it never waits for the
dialog) and saves the shortcut either way. It never asks for a cleared shortcut,
off macOS, from a GET, from a rejected save, or at launch; and a failing ask never
fails the save.

The macOS cases run the REAL service over ``FakeTCC`` (no Mac was available: its
fidelity labels apply, what the real dialog does is unverified), so what the OS was
asked is checked against its own call log. The consumer-level cases use
``FakePermissionService`` and read what the route asked of the gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.settings_routes as settings_routes
from jarvis.core import config_writer
from jarvis.platform import permission_service
from jarvis.platform.permission_service import PermissionOutcome, get_permission_service
from jarvis.ui.web.settings_routes import router
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_tcc import (
    DialogPolicy,
    FakeTCC,
    TccService,
    install_port,
    make_non_darwin_port,
)

_SAVE_KEYS = {"ok", "action", "hotkey", "persisted", "applied_live", "restart_required", "cautions"}


class _Trigger:
    """The slice of ``HotkeyTrigger`` the route reads: ``listening()``."""

    def __init__(self, listening: bool | None) -> None:
        self._listening = listening

    def listening(self) -> bool | None:
        return self._listening


class _Pipeline:
    def __init__(self, trigger: _Trigger | None) -> None:
        self._hotkey_trigger = trigger
        self.rearmed: list[dict[str, list[str]]] = []

    def set_keybinds(self, **kwargs: list[str]) -> None:
        self.rearmed.append(kwargs)


def _client(pipeline: object | None = None, **state: object) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        trigger=SimpleNamespace(
            hotkey="ctrl+right_alt+j", hotkey_call="f3+f4", hotkey_hangup="f1+f2", push_to_talk=True
        )
    )
    if pipeline is not None:
        app.state.speech_pipeline = pipeline
    for name, value in state.items():
        setattr(app.state, name, value)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _persist_nothing(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "set_keybind", lambda action, hotkey: saved.append((action, hotkey))
    )
    return saved


def _on_macos(monkeypatch: pytest.MonkeyPatch, **tcc_kwargs: object) -> FakeTCC:
    tcc = FakeTCC(**tcc_kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)
    return tcc


def _save(client: TestClient, action: str = "call", hotkey: str = "f5+f6"):
    return client.put("/api/settings/keybinds", json={"action": action, "hotkey": hotkey})


def test_saving_a_shortcut_on_a_fresh_mac_asks_once_and_still_saves_it(
    monkeypatch: pytest.MonkeyPatch, _persist_nothing: list[tuple[str, str]]
) -> None:
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    pipeline = _Pipeline(_Trigger(listening=False))

    response = _save(_client(pipeline))

    assert response.status_code == 200
    body = response.json()
    assert _SAVE_KEYS <= set(body)
    assert body["ok"] is True and body["hotkey"] == "f5+f6" and body["persisted"] is True
    assert body["applied_live"] is True
    assert _persist_nothing == [("call", "f5+f6")]
    assert pipeline.rearmed == [{"call": ["f5+f6"]}]
    # The dialog is up (the route did not wait for the answer): one native request,
    # and nothing implicit.
    assert len(tcc.requests(TccService.INPUT_MONITORING)) == 1
    assert tcc.implicit_prompts() == []
    (episode,) = get_permission_service().outstanding()
    assert (episode.feature, episode.origin, episode.permissions) == (
        "global_shortcuts",
        "user",
        ("input_monitoring",),
    )


def test_the_route_returns_while_the_dialog_is_still_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``wait_s=0``: an unanswered dialog never holds the save."""
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)

    response = _save(_client(_Pipeline(_Trigger(listening=False))))

    assert response.status_code == 200
    assert tcc.dialog_open(TccService.INPUT_MONITORING) is True


def test_a_second_save_in_the_same_episode_does_not_ask_macOS_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    client = _client(_Pipeline(_Trigger(listening=False)))

    assert _save(client).status_code == 200
    assert _save(client, action="hangup", hotkey="f7+f8").status_code == 200

    assert len(tcc.requests(TccService.INPUT_MONITORING)) == 1


def test_a_running_tap_means_no_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _on_macos(monkeypatch, granted=(TccService.INPUT_MONITORING,))

    response = _save(_client(_Pipeline(_Trigger(listening=True))))

    assert response.status_code == 200
    assert tcc.requests() == []
    assert get_permission_service().outstanding() == []


def test_a_granted_permission_asks_nothing_even_when_the_tap_is_not_up_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch, granted=(TccService.INPUT_MONITORING,))

    response = _save(_client(_Pipeline(_Trigger(listening=False))))

    assert response.status_code == 200
    assert tcc.requests() == []


def test_a_denied_permission_is_reported_not_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch)
    tcc.deny(TccService.INPUT_MONITORING)

    response = _save(_client(_Pipeline(_Trigger(listening=False))))

    assert response.status_code == 200
    assert response.json()["persisted"] is True
    assert tcc.requests() == []
    (episode,) = get_permission_service().outstanding()
    assert episode.feature == "global_shortcuts" and episode.origin == "user"


def test_re_saving_the_combo_already_in_force_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op save is not a new gesture: no native request, no episode."""
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    pipeline = _Pipeline(_Trigger(listening=False))

    # "f3+f4" is the combo the client's config already holds for the call action.
    response = _save(_client(pipeline), action="call", hotkey="f3+f4")

    assert response.status_code == 200
    assert response.json()["hotkey"] == "f3+f4"
    assert tcc.requests() == []
    assert tcc.probes() == []
    assert get_permission_service().outstanding() == []


def test_an_identical_re_save_does_not_announce_a_denied_permission_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch)
    tcc.deny(TccService.INPUT_MONITORING)
    published: list[object] = []
    service = get_permission_service()
    monkeypatch.setattr(service, "_publish", published.append)
    # A gesture re-announces an open episode (service re-announce window); with the
    # window at zero only the route's "changed" rule can keep the identical save quiet.
    monkeypatch.setattr(permission_service, "_REANNOUNCE_MIN_S", 0.0)
    client = _client(_Pipeline(_Trigger(listening=False)))

    assert _save(client, action="call", hotkey="f5+f6").status_code == 200
    assert len(published) == 1  # the denied permission is announced once

    # The same combo again (any case or padding) is no gesture: nothing is published.
    assert _save(client, action="call", hotkey=" F5+F6 ").status_code == 200
    assert len(published) == 1
    assert tcc.requests() == []


def test_clearing_a_shortcut_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)

    response = _save(_client(_Pipeline(_Trigger(listening=False))), hotkey="")

    assert response.status_code == 200
    assert response.json()["hotkey"] == ""
    assert tcc.requests() == []
    assert tcc.probes() == []


def test_a_rejected_save_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    client = _client(_Pipeline(_Trigger(listening=False)))

    # Same chord as the hangup action: the save is refused before anything else.
    refused = _save(client, action="call", hotkey="f1+f2")

    assert refused.status_code == 400
    assert tcc.requests() == []
    assert tcc.probes() == []


def test_a_get_never_asks_and_no_longer_carries_a_shortcuts_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)

    body = _client(_Pipeline(_Trigger(listening=False))).get("/api/settings/keybinds").json()

    assert "shortcuts_status" not in body
    assert tcc.requests() == []
    assert tcc.probes() == []


def test_without_a_voice_pipeline_the_save_still_asks_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tap object to read: the save is still the user's gesture for the permission."""
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)

    response = _save(_client())

    assert response.status_code == 200
    assert response.json()["applied_live"] is False
    assert len(tcc.requests(TccService.INPUT_MONITORING)) == 1


def test_off_macos_the_permission_layer_is_never_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_non_darwin_port("win32")
    install_port(monkeypatch, port)
    monkeypatch.setattr(settings_routes, "_is_macos", lambda: False)
    gate = FakePermissionService()

    response = _save(_client(_Pipeline(_Trigger(listening=None)), permission_service=gate))

    assert response.status_code == 200
    assert gate.ensure_calls() == []
    assert gate.native_free()
    tcc.assert_silent()


def test_the_route_asks_the_gate_exactly_once_non_blocking_and_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)
    gate = FakePermissionService({"input_monitoring": PermissionOutcome.PENDING})

    response = _save(_client(_Pipeline(_Trigger(listening=False)), permission_service=gate))

    assert response.status_code == 200
    (call,) = gate.ensure_calls()
    assert call.method == "ensure"
    assert call.permission.value == "input_monitoring"
    assert call.feature == "global_shortcuts"
    assert call.interactive is True
    assert call.wait_s == 0.0


def test_a_gate_that_says_no_never_fails_the_save(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)
    gate = FakePermissionService({"input_monitoring": PermissionOutcome.DENIED})

    response = _save(_client(_Pipeline(_Trigger(listening=False)), permission_service=gate))

    assert response.status_code == 200
    assert response.json()["persisted"] is True


def test_a_gate_that_raises_never_fails_the_save(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("native layer exploded")

    broken = SimpleNamespace(ensure=boom)

    with caplog.at_level("DEBUG", logger=settings_routes.log.name):
        response = _save(_client(_Pipeline(_Trigger(listening=False)), permission_service=broken))

    assert response.status_code == 200
    assert _SAVE_KEYS <= set(response.json())
    # AP-30: the failure is logged, not swallowed.
    assert any("Input Monitoring" in record.getMessage() for record in caplog.records)


def test_a_trigger_whose_probe_raises_still_saves_and_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken:
        def listening(self) -> bool:
            raise RuntimeError("probe failed")

    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)
    gate = FakePermissionService({"input_monitoring": PermissionOutcome.PENDING})

    response = _save(_client(_Pipeline(_Broken()), permission_service=gate))  # type: ignore[arg-type]

    assert response.status_code == 200
    assert response.json()["persisted"] is True
    # A probe that fails is "unknown", which is not "listening": the ask still happens.
    assert len(gate.ensure_calls()) == 1
