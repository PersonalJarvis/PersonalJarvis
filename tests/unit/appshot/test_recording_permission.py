"""The screen recorder's macOS Screen Recording gate (jarvis.platform.screen_access)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from jarvis.platform import permissions, screen_access
from jarvis.platform.permissions import PermissionId, PermissionState


class FakePort:
    def __init__(self, state: PermissionState, *, grant_on_request: bool = False) -> None:
        self._state = state
        self.grant_on_request = grant_on_request
        self.requested: list[PermissionId] = []

    def state(self, permission_id: PermissionId) -> PermissionState:
        assert permission_id is PermissionId.SCREEN_RECORDING
        return self._state

    def runtime_access_granted(self, _permission_id: PermissionId) -> bool:
        return self._state is PermissionState.GRANTED

    def request(self, permission_id: PermissionId) -> SimpleNamespace:
        self.requested.append(permission_id)
        if self.grant_on_request:
            self._state = PermissionState.GRANTED
        return SimpleNamespace(ok=True, message="asked")


@pytest.fixture
def port(monkeypatch: pytest.MonkeyPatch):
    def use(state: PermissionState, **kwargs) -> FakePort:
        fake = FakePort(state, **kwargs)
        monkeypatch.setattr(permissions, "get_system_permission_port", lambda: fake)
        return fake

    return use


@pytest.mark.parametrize(
    ("state", "allowed"),
    [
        (PermissionState.GRANTED, True),
        (PermissionState.NOT_REQUIRED, True),
        (PermissionState.DENIED, False),
        (PermissionState.NOT_DETERMINED, False),
        (PermissionState.UNAVAILABLE, False),
    ],
)
def test_only_a_live_grant_lets_a_capture_run(port, state: PermissionState, allowed: bool) -> None:
    port(state)
    assert screen_access.state_allows_capture(screen_access.screen_recording_state()) is allowed


async def test_a_granted_recording_starts_without_asking(port) -> None:
    fake = port(PermissionState.GRANTED)
    await screen_access.require_screen_recording_async("appshot")
    assert fake.requested == []


async def test_starting_a_recording_asks_macos_once(port) -> None:
    fake = port(PermissionState.NOT_DETERMINED, grant_on_request=True)
    await screen_access.require_screen_recording_async("appshot")
    assert fake.requested == [PermissionId.SCREEN_RECORDING]


async def test_a_refused_grant_names_the_permission(port) -> None:
    port(PermissionState.DENIED)
    with pytest.raises(screen_access.ScreenCaptureRefused) as refused:
        await screen_access.require_screen_recording_async("appshot")
    assert "Screen Recording" in refused.value.user_detail
    assert str(refused.value).startswith("[permission_needed:screen_recording]")


def test_the_recording_status_on_a_mac_without_the_grant(
    monkeypatch: pytest.MonkeyPatch, port
) -> None:
    """The status route on macOS: an honest refusal, never an import error."""
    pytest.importorskip("PySide6.QtMultimedia")
    pytest.importorskip("av")
    from jarvis.appshot import recording

    port(PermissionState.DENIED)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: True)

    status = recording.capability()

    assert status["available"] is False
    assert status["permission_required"] is True
    assert "Screen Recording" in status["detail"]
