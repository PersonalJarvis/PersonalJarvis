"""``shortcuts_status`` on GET /api/settings/keybinds: one status, read silently.

States: ``ready`` / ``needs_input_monitoring`` / ``unavailable_in_this_mode``.
Driven against ``FakeTCC`` (no macOS here; its fidelity labels apply) so the
real permission port and service answer, and the route is called through a real
FastAPI app. The route never prompts: every test asserts an empty request log.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.platform import probes
from jarvis.trigger.shortcuts_status import SHORTCUTS_STATES, ShortcutsStatus, shortcuts_status
from jarvis.ui.web.settings_routes import router
from tests.fakes.fake_tcc import (
    FakeTCC,
    TccService,
    install_port,
    make_darwin_port,
    make_non_darwin_port,
)


def _client(trigger: object | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        trigger=SimpleNamespace(
            hotkey="ctrl+right_alt+j", hotkey_call="f3+f4", hotkey_hangup="f1+f2", push_to_talk=True
        )
    )
    if trigger is not None:
        app.state.speech_pipeline = SimpleNamespace(
            _hotkey_trigger=trigger, set_keybinds=lambda **_kw: None
        )
    return TestClient(app)


def _mac(monkeypatch: pytest.MonkeyPatch, *, hotkey_capable: bool = True) -> FakeTCC:
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)
    monkeypatch.setattr(probes, "has_hotkey", lambda: hotkey_capable)
    return tcc


def test_status_vocabulary_is_the_three_documented_states() -> None:
    assert SHORTCUTS_STATES == ("ready", "needs_input_monitoring", "unavailable_in_this_mode")
    from typing import get_args

    assert get_args(ShortcutsStatus.model_fields["state"].annotation) == SHORTCUTS_STATES


def test_a_fresh_mac_needs_input_monitoring_and_nothing_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _mac(monkeypatch)
    body = _client().get("/api/settings/keybinds").json()
    assert body["shortcuts_status"]["state"] == "needs_input_monitoring"
    assert "has not been allowed" in body["shortcuts_status"]["detail"]
    tcc.assert_no_prompts()
    assert tcc.requests() == []


def test_a_denied_mac_says_it_is_off_without_blaming_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _mac(monkeypatch)
    tcc.deny(TccService.INPUT_MONITORING)
    status = shortcuts_status()
    assert status.state == "needs_input_monitoring"
    assert "is off" in status.detail and "denied" not in status.detail.lower()
    assert tcc.requests() == []


def test_a_granted_mac_is_ready_even_before_the_pipeline_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _mac(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    status = _client().get("/api/settings/keybinds").json()["shortcuts_status"]
    assert status == {"state": "ready", "detail": ""}
    assert tcc.requests() == []


def test_an_unreadable_grant_is_unavailable_not_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = FakeTCC()
    tcc.drop_framework("Quartz")  # a build without the Quartz bindings
    install_port(monkeypatch, tcc.port("darwin", iohid_check=lambda _request_type: None))
    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    status = shortcuts_status()
    assert status.state == "unavailable_in_this_mode"
    assert "did not report" in status.detail
    assert tcc.requests() == []


def test_a_host_without_a_hotkey_backend_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _mac(monkeypatch, hotkey_capable=False)
    tcc.grant(TccService.INPUT_MONITORING)
    assert shortcuts_status().state == "unavailable_in_this_mode"


class _Trigger:
    def __init__(self, deaf: bool) -> None:
        self._deaf = deaf

    def deaf_tap_suspected(self, *, user_reported: bool = False) -> bool:
        return self._deaf


def test_a_deaf_tap_adds_a_restart_hint_to_the_detail_only(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _mac(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    body = _client(_Trigger(deaf=True)).get("/api/settings/keybinds").json()
    assert body["shortcuts_status"]["state"] == "ready"
    assert "Quit and reopen" in body["shortcuts_status"]["detail"]
    quiet = _client(_Trigger(deaf=False)).get("/api/settings/keybinds").json()
    assert quiet["shortcuts_status"]["detail"] == ""


def test_a_raising_liveness_probe_makes_no_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _mac(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)

    class _Broken:
        def deaf_tap_suspected(self, **_kw: object) -> bool:
            raise RuntimeError("probe exploded")

    assert shortcuts_status(_Broken()) == ShortcutsStatus(state="ready", detail="")


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_off_macos_the_state_follows_the_capability_probe_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    port, tcc = make_non_darwin_port(platform_name)  # type: ignore[arg-type]
    install_port(monkeypatch, port)
    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    assert shortcuts_status().state == "ready"
    monkeypatch.setattr(probes, "has_hotkey", lambda: False)
    assert shortcuts_status().state == "unavailable_in_this_mode"
    tcc.assert_silent()


def test_the_payload_is_the_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    _mac(monkeypatch)
    payload = _client().get("/api/settings/keybinds").json()["shortcuts_status"]
    assert set(payload) == {"state", "detail"}
    assert ShortcutsStatus(**payload).model_dump() == payload


def test_a_restricted_answer_is_unavailable_with_the_policy_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.platform import permission_service as service_mod
    from jarvis.platform.permissions import PermissionState

    class _Service:
        def check(self, _permission: object) -> PermissionState:
            return PermissionState.RESTRICTED

    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    monkeypatch.setattr(service_mod, "get_permission_service", lambda: _Service())
    status = shortcuts_status()
    assert status.state == "unavailable_in_this_mode"
    assert "policy" in status.detail
