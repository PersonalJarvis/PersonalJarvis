"""Overlay windows must be omitted from screenshots (WDA / NSWindowSharingNone)."""

from __future__ import annotations

from types import SimpleNamespace

import jarvis.platform.capture_exclusion as exclusion


class _FakeUser32:
    def __init__(self) -> None:
        self.affinity: list[tuple[int, int]] = []
        self.parent_of: dict[int, int] = {}

    def GetParent(self, hwnd: int) -> int:  # noqa: N802
        return self.parent_of.get(hwnd, 0)

    def SetWindowDisplayAffinity(self, hwnd: int, affinity: int) -> bool:  # noqa: N802
        self.affinity.append((int(hwnd), int(affinity)))
        return True


def test_exclude_hwnd_is_a_quiet_noop_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(exclusion.sys, "platform", "linux")
    assert exclusion.exclude_hwnd_from_capture(0x1234) is False


def test_exclude_hwnd_sets_wda_exclude_from_capture(monkeypatch) -> None:
    fake = _FakeUser32()
    monkeypatch.setattr(exclusion.sys, "platform", "win32")
    monkeypatch.setattr(exclusion, "_user32", lambda: fake)
    assert exclusion.exclude_hwnd_from_capture(0xABCD) is True
    assert fake.affinity == [(0xABCD, 0x00000011)]  # WDA_EXCLUDEFROMCAPTURE


def test_exclude_tk_window_targets_the_outer_toplevel(monkeypatch) -> None:
    fake = _FakeUser32()
    fake.parent_of[0x1111] = 0x2222
    monkeypatch.setattr(exclusion.sys, "platform", "win32")
    monkeypatch.setattr(exclusion, "_user32", lambda: fake)
    root = SimpleNamespace(winfo_id=lambda: 0x1111)
    assert exclusion.exclude_tk_window_from_capture(root) is True
    assert fake.affinity == [(0x2222, 0x00000011)]


def test_exclude_tk_window_survives_a_root_without_winfo_id() -> None:
    assert exclusion.exclude_tk_window_from_capture(object()) is False
    assert exclusion.exclude_tk_window_from_capture(None) is False
