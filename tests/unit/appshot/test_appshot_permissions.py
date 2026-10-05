"""Appshots say which OS permission is missing instead of arming or freezing for nothing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.appshot import gesture, service
from jarvis.platform import permissions


class FakePort:
    def __init__(self, granted: bool) -> None:
        self.granted = granted

    def runtime_access_granted(self, permission_id) -> bool:
        assert permission_id is permissions.PermissionId.INPUT_MONITORING
        return self.granted


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("jarvis.platform.detect_platform", lambda: "darwin")
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: True)
    monkeypatch.setattr(gesture, "_macos_probe", lambda family: f"probe:{family}")

    def use(granted: bool) -> None:
        monkeypatch.setattr(permissions, "get_system_permission_port", lambda: FakePort(granted))

    return use


def test_a_mac_without_input_monitoring_does_not_arm_the_gesture(mac) -> None:
    mac(False)
    probe, reason = gesture.make_probe("alt")
    assert probe is None
    assert "Input Monitoring" in reason


def test_a_mac_with_input_monitoring_arms_it(mac) -> None:
    mac(True)
    assert gesture.make_probe("shift") == ("probe:shift", "")


class RefusingService:
    async def capture_permission_issue(self):
        return "capture_permission", "Grant Screen Recording first."


async def test_the_area_picker_never_opens_without_capture_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[bool] = []

    async def pick_area(*_args, **_kwargs):
        opened.append(True)
        raise AssertionError("the screens must not freeze for a capture that will be refused")

    config = SimpleNamespace(
        screen_context=SimpleNamespace(enabled=True),
        ui=SimpleNamespace(language="en"),
        appshot=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_load_config", lambda: config)
    monkeypatch.setattr(service, "_pick_area", pick_area)
    monkeypatch.setattr(
        "jarvis.screen_context.turn.get_service", lambda bus=None: RefusingService()
    )

    result = await service.take_appshot(trigger="hotkey", scope="region")

    assert result.status == "refused"
    assert result.reason_code == "capture_permission"
    assert result.message == "Grant Screen Recording first."
    assert opened == []
