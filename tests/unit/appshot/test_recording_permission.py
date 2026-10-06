"""The screen recorder's macOS Screen Recording gate (jarvis.platform.screen_access)."""

from __future__ import annotations

import sys

import pytest

from jarvis.platform import permission_service, screen_access
from jarvis.platform.permissions import PermissionId
from tests.fakes.fake_permission_service import FakePermissionService


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch):
    def use(*outcomes: str) -> FakePermissionService:
        fake = FakePermissionService()
        fake.script(PermissionId.SCREEN_RECORDING, *outcomes)
        monkeypatch.setattr(screen_access, "permission_gate", lambda: fake)
        monkeypatch.setattr(permission_service, "get_permission_service", lambda: fake)
        return fake

    return use


@pytest.mark.parametrize(
    ("outcome", "allowed"),
    [
        ("granted", True),
        ("not_required", True),
        ("denied", False),
        ("pending", False),
        ("unavailable", False),
    ],
)
def test_only_a_live_grant_lets_a_capture_run(gate, outcome: str, allowed: bool) -> None:
    gate(outcome)
    assert screen_access.state_allows_capture(screen_access.screen_recording_state()) is allowed


async def test_a_granted_recording_starts_without_asking(gate) -> None:
    fake = gate("granted")
    await screen_access.require_screen_recording_async("appshot")
    assert fake.ensure_calls(PermissionId.SCREEN_RECORDING) == []


async def test_starting_a_recording_asks_macos_once(gate) -> None:
    fake = gate("pending")
    with pytest.raises(screen_access.ScreenCaptureRefused):
        await screen_access.require_screen_recording_async("appshot")
    asks = fake.ensure_calls(PermissionId.SCREEN_RECORDING)
    assert len(asks) == 1
    assert asks[0].interactive is True


async def test_a_refused_grant_names_the_permission(gate) -> None:
    gate("denied")
    with pytest.raises(screen_access.ScreenCaptureRefused) as refused:
        await screen_access.require_screen_recording_async("appshot")
    assert "Screen Recording" in refused.value.user_detail


def test_the_recording_status_on_a_mac_without_the_grant(
    monkeypatch: pytest.MonkeyPatch, gate
) -> None:
    """The status route on macOS: an honest refusal, never an import error."""
    pytest.importorskip("PySide6.QtMultimedia")
    pytest.importorskip("av")
    from jarvis.appshot import recording

    gate("denied")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: True)

    status = recording.capability()

    assert status["available"] is False
    assert status["permission_required"] is True
    assert "Screen Recording" in status["detail"]
