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


class _FakeTkRoot:
    """A toplevel whose outer window exists only after its first map."""

    def __init__(self) -> None:
        self.bindings: list[tuple[str, object, str]] = []

    def winfo_id(self) -> int:
        return 0x1111

    def bind(self, sequence: str, func, add: str = "") -> None:
        self.bindings.append((sequence, func, add))

    def fire_map(self, widget=None) -> None:
        for sequence, func, _add in self.bindings:
            if sequence == "<Map>":
                func(SimpleNamespace(widget=self if widget is None else widget))


def test_unmapped_tk_window_is_excluded_once_tk_creates_its_outer_window(monkeypatch) -> None:
    """The overlays exclude themselves before their first show, when Tk has no
    outer window yet; the exclusion landed on the inner child and the mascot
    stayed in every appshot."""
    fake = _FakeUser32()
    monkeypatch.setattr(exclusion.sys, "platform", "win32")
    monkeypatch.setattr(exclusion, "_user32", lambda: fake)
    root = _FakeTkRoot()

    assert exclusion.exclude_tk_window_from_capture(root) is True
    assert fake.affinity == [], "the inner child cannot carry a display affinity"

    fake.parent_of[0x1111] = 0x2222  # Tk maps the toplevel: the outer window exists
    root.fire_map()
    assert fake.affinity == [(0x2222, 0x00000011)]


def test_map_hook_keeps_foreign_bindings_and_ignores_child_widgets(monkeypatch) -> None:
    fake = _FakeUser32()
    fake.parent_of[0x1111] = 0x2222
    monkeypatch.setattr(exclusion.sys, "platform", "win32")
    monkeypatch.setattr(exclusion, "_user32", lambda: fake)
    root = _FakeTkRoot()

    exclusion.exclude_tk_window_from_capture(root)
    exclusion.exclude_tk_window_from_capture(root)

    assert [b for b in root.bindings if b[0] == "<Map>"] == [root.bindings[0]], "bound once"
    assert root.bindings[0][2] == "+", "an existing <Map> binding is never replaced"
    fake.affinity.clear()
    root.fire_map(widget=object())  # a child label mapping
    assert fake.affinity == []
    root.fire_map()  # the toplevel itself (re-)mapping
    assert fake.affinity == [(0x2222, 0x00000011)]
