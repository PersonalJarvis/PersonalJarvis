"""Popup-surface enumeration degrades cleanly off Windows (AD-6)."""
from __future__ import annotations

import pytest

from jarvis.platform import window_state as ws


@pytest.mark.parametrize("platform", ["darwin", "linux", "unknown"])
def test_visible_popup_windows_empty_off_windows(monkeypatch, platform):
    monkeypatch.setattr(ws, "detect_platform", lambda: platform)
    assert ws.visible_popup_windows() == ()


@pytest.mark.parametrize("platform", ["darwin", "linux", "unknown"])
def test_open_menu_surface_absent_off_windows(monkeypatch, platform):
    monkeypatch.setattr(ws, "detect_platform", lambda: platform)
    assert ws.open_menu_surface_present() is False


def test_visible_popup_windows_never_raises_on_windows_probe_failure(monkeypatch):
    # The Windows path swallows native failures into an empty result — the
    # effect check treats that as "no evidence", never as an error.
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(
        ws, "_window_class_windows",
        lambda hwnd: (_ for _ in ()).throw(OSError("boom")),
    )
    # Whatever the live EnumWindows returns, the call must not raise.
    result = ws.visible_popup_windows()
    assert isinstance(result, tuple)
