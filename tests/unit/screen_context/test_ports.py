"""Cross-platform capability guards for Screen Context ports."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

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

    issue = ports.capture_permission_error()

    assert issue is not None
    assert issue.code == "wayland_portal"
    assert "portal" in issue.message.lower()


def test_windows_window_capture_never_falls_back_to_desktop_rect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ports, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ports, "_input_space", nullcontext)
    monkeypatch.setattr(
        "jarvis.cu.indicator.capture_guard.indicator_suppressed",
        nullcontext,
    )
    monkeypatch.setattr("jarvis.platform.window_capture.grab_window", lambda *_a, **_k: None)

    with pytest.raises(ports.CaptureUnavailable, match="refused"):
        ports.NativeSurfaceCapturer().grab(
            (0, 0, 100, 100),
            window_handle=0x1_0000_1234,
        )


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
