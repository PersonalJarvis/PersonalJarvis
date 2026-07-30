"""Cross-platform capability guards for Screen Context ports."""
from __future__ import annotations

import pytest

from jarvis.platform.window_state import WindowInfo
from jarvis.screen_context import ports


def test_wayland_display_enumeration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ports, "_is_wayland", lambda: True)

    assert ports.MssDisplayEnumerator().monitors() == []


def test_wayland_capture_refuses_before_mss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ports, "_is_wayland", lambda: True)

    with pytest.raises(ports.CaptureUnavailable, match="Wayland"):
        ports.NativeSurfaceCapturer().grab((0, 0, 100, 100))


def test_wayland_permission_probe_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ports, "_is_wayland", lambda: True)

    message = ports.capture_permission_error()

    assert message is not None
    assert "portal" in message.lower()


def test_visible_windows_retains_untitled_password_manager_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ports, "_is_wayland", lambda: False)
    monkeypatch.setattr(
        "jarvis.platform.window_state.list_windows",
        lambda: [WindowInfo(title="", handle=7, pid=303)],
    )
    monkeypatch.setattr(
        "jarvis.platform.window_state.window_frame_rect",
        lambda _window: (0, 0, 500, 500),
    )
    monkeypatch.setattr(
        "jarvis.platform.window_state.window_rect",
        lambda _window: None,
    )
    monkeypatch.setattr(ports, "_app_name_for_pid", lambda _pid: "1Password.exe")

    visible = ports.PlatformWindowProbe().visible_windows()

    assert visible is not None
    assert len(visible) == 1
    assert visible[0].app_name == "1Password.exe"
    assert visible[0].title == ""
