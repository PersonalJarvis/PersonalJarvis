"""POST /api/settings/wake-word/activation: the SWITCH is the gesture that asks.

Switching the always-on wake word ON is the just-in-time moment for the
microphone. The route asks through the permission service (``wait_s=0``: it
returns at once, the answer arrives later through ``PermissionResolved``), saves
and applies the switch either way, and answers with an additive ``permission``
object ``{outcome, reason, can_open_settings}``. Every existing key is kept.
Switching OFF asks nothing, and another OS never touches the service.

The macOS cases run the REAL service over ``FakeTCC``, so what the OS was asked is
checked against its own call log.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.settings_routes as settings_routes
from jarvis.core import config_writer
from jarvis.core.config import WakeWordConfig
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

_LEGACY_KEYS = {"ok", "enabled", "applied_live", "restart_required", "persisted", "message"}
_PERMISSION_KEYS = {"outcome", "reason", "can_open_settings"}


class _Pipeline:
    def __init__(self) -> None:
        self.activation: bool | None = None

    def set_wake_activation(self, enabled: bool) -> None:
        self.activation = enabled


def _client(pipeline: object | None = None, **state: object) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(trigger=SimpleNamespace(wake_word=WakeWordConfig()))
    if pipeline is not None:
        app.state.speech_pipeline = pipeline
    for name, value in state.items():
        setattr(app.state, name, value)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _persist_nothing(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    saved: list[bool] = []
    monkeypatch.setattr(config_writer, "set_wake_word_enabled", saved.append)
    return saved


def _on_macos(monkeypatch: pytest.MonkeyPatch, **tcc_kwargs: object) -> FakeTCC:
    tcc = FakeTCC(**tcc_kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    monkeypatch.setattr(settings_routes.sys, "platform", "darwin")
    return tcc


def test_switching_on_on_a_fresh_mac_asks_once_and_still_saves_the_switch(
    monkeypatch: pytest.MonkeyPatch, _persist_nothing: list[bool]
) -> None:
    tcc = _on_macos(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    pipe = _Pipeline()

    response = _client(pipe).post("/api/settings/wake-word/activation", json={"enabled": True})

    assert response.status_code == 200
    body = response.json()
    assert _LEGACY_KEYS <= set(body)
    assert body["ok"] is True and body["enabled"] is True and body["persisted"] is True
    assert body["applied_live"] is True
    # The dialog is up (the route did not wait for the answer): pending, not granted.
    assert set(body["permission"]) == _PERMISSION_KEYS
    assert body["permission"]["outcome"] == PermissionOutcome.PENDING.value
    assert body["permission"]["reason"] == "not_determined"
    assert pipe.activation is True and _persist_nothing == [True]
    assert len(tcc.requests(TccService.MICROPHONE)) == 1
    assert tcc.implicit_prompts() == []
    (episode,) = get_permission_service().outstanding()
    assert (episode.feature, episode.origin, episode.permissions) == (
        "wake_word",
        "user",
        ("microphone",),
    )


def test_switching_on_with_the_microphone_already_granted_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch, granted=(TccService.MICROPHONE,))

    body = (
        _client(_Pipeline())
        .post("/api/settings/wake-word/activation", json={"enabled": True})
        .json()
    )

    assert body["permission"] == {"outcome": "granted", "reason": "", "can_open_settings": False}
    assert tcc.requests() == []
    assert get_permission_service().outstanding() == []


def test_switching_on_with_a_denied_microphone_points_at_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch)
    tcc.deny(TccService.MICROPHONE)

    body = (
        _client(_Pipeline())
        .post("/api/settings/wake-word/activation", json={"enabled": True})
        .json()
    )

    assert body["permission"]["outcome"] == PermissionOutcome.DENIED.value
    assert body["permission"]["reason"] == "denied"
    assert body["permission"]["can_open_settings"] is True
    assert tcc.requests() == [], "a denial is a stable state: macOS is not asked again"


def test_switching_off_asks_nothing_and_says_nothing_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _on_macos(monkeypatch)
    pipe = _Pipeline()

    body = _client(pipe).post("/api/settings/wake-word/activation", json={"enabled": False}).json()

    assert body["permission"] == {
        "outcome": "not_required",
        "reason": "",
        "can_open_settings": False,
    }
    assert pipe.activation is False
    tcc.assert_silent()
    assert tcc.calls == ()


def test_the_route_asks_through_the_injected_service_with_a_zero_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_routes.sys, "platform", "darwin")
    gate = FakePermissionService()
    gate.script("microphone", PermissionOutcome.PENDING)

    body = (
        _client(permission_service=gate)
        .post("/api/settings/wake-word/activation", json={"enabled": True})
        .json()
    )

    (call,) = gate.ensure_calls("microphone")
    assert (call.method, call.feature, call.interactive, call.wait_s) == (
        "ensure",
        "wake_word",
        True,
        0.0,
    )
    assert body["permission"]["outcome"] == "pending"


def test_a_broken_service_never_fails_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_routes.sys, "platform", "darwin")

    class _Broken:
        def ensure(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("native failure at /secret/path")

    pipe = _Pipeline()
    response = _client(pipe, permission_service=_Broken()).post(
        "/api/settings/wake-word/activation", json={"enabled": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["applied_live"] is True
    assert body["permission"]["outcome"] == "unavailable"
    assert "secret" not in response.text
    assert pipe.activation is True


@pytest.mark.parametrize("enabled", [True, False])
def test_other_platforms_never_touch_the_permission_layer(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    monkeypatch.setattr(settings_routes.sys, "platform", "linux")
    gate = FakePermissionService()

    body = (
        _client(_Pipeline(), permission_service=gate)
        .post("/api/settings/wake-word/activation", json={"enabled": enabled})
        .json()
    )

    assert body["permission"]["outcome"] == "not_required"
    assert gate.calls == []
    tcc.assert_silent()
