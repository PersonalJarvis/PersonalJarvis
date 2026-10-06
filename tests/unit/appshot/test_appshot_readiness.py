"""The Appshots page says "ready" only when a capture would really run."""

from __future__ import annotations

import pytest

from jarvis.appshot import region
from jarvis.screen_context.ports import CapturePermissionIssue
from jarvis.ui.web import appshot_routes


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: True)
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: False)
    monkeypatch.setattr("jarvis.platform.qt_sidecar.missing_system_library", lambda: "")

    def gate(issue: CapturePermissionIssue | None) -> None:
        monkeypatch.setattr("jarvis.screen_context.ports.capture_permission_error", lambda: issue)

    return gate


def test_a_mac_without_screen_recording_is_not_ready(desktop) -> None:
    pytest.importorskip("mss")
    desktop(CapturePermissionIssue(code="capture_permission", message="Grant it first."))

    readiness = appshot_routes._capability()  # noqa: SLF001

    assert readiness["capture"] is False
    assert readiness["capture_detail"] == "Grant it first."
    # The picker would freeze the screens for a capture that is refused.
    assert readiness["region"] is False
    assert readiness["region_detail"] == "Grant it first."


def test_a_granted_desktop_is_ready(desktop) -> None:
    pytest.importorskip("mss")
    desktop(None)
    readiness = appshot_routes._capability()  # noqa: SLF001
    assert readiness["capture"] is True
    assert readiness["capture_detail"] == ""


def test_a_box_without_a_screen_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: False)
    readiness = appshot_routes._capability()  # noqa: SLF001
    assert readiness["capture"] is False
    assert "no screen" in readiness["capture_detail"]


def test_the_picker_names_a_missing_qt_library(desktop, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setattr(
        "jarvis.platform.qt_sidecar.missing_system_library", lambda: "Install libxcb-cursor0"
    )
    assert region.picker_capability() == (False, "Install libxcb-cursor0")
