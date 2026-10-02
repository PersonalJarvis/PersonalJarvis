"""BUG-228: an appshot taken right after a click on the Jarvis bar or the mascot
photographed the overlay itself (a 288x288 square) instead of the app window.

The Z-order walk is pure and tested here with plain fakes; the Win32 glue is
exercised on a real desktop, not in CI.
"""
from __future__ import annotations

from jarvis.platform import window_state as ws
from jarvis.platform.window_state import WindowInfo

# Z-order, top to bottom, as measured live: mascot overlay, bar overlay, the
# taskbar (a topmost tool window), a minimized window, the editor, the wallpaper.
_MASCOT, _BAR, _TASKBAR, _MINIMIZED, _EDITOR, _DESKTOP = 10, 11, 12, 13, 14, 15
_ORDER = [_MASCOT, _BAR, _TASKBAR, _MINIMIZED, _EDITOR, _DESKTOP]


def _pick(foreground: int, *, overlays=(_MASCOT, _BAR), app_windows=(_EDITOR,)):
    def next_below(hwnd: int) -> int:
        index = _ORDER.index(hwnd)
        return _ORDER[index + 1] if index + 1 < len(_ORDER) else 0

    return ws._pick_app_window(
        foreground,
        next_below=next_below,
        is_own_overlay=lambda hwnd: hwnd in overlays,
        is_app_window=lambda hwnd: hwnd in app_windows,
        is_desktop=lambda hwnd: hwnd == _DESKTOP,
    )


def test_normal_foreground_window_is_kept() -> None:
    assert _pick(_EDITOR) == _EDITOR


def test_foreground_overlay_falls_through_to_the_app_window_under_it() -> None:
    assert _pick(_MASCOT) == _EDITOR


def test_the_taskbar_between_overlay_and_app_does_not_stop_the_walk() -> None:
    assert _pick(_BAR) == _EDITOR


def test_only_the_desktop_under_the_overlay_means_no_app_window() -> None:
    assert _pick(_MASCOT, app_windows=()) is None


def test_end_of_the_window_list_means_no_app_window() -> None:
    def next_below(hwnd: int) -> int:
        return {_MASCOT: _EDITOR}.get(hwnd, 0)

    assert ws._pick_app_window(
        _MASCOT,
        next_below=next_below,
        is_own_overlay=lambda hwnd: hwnd == _MASCOT,
        is_app_window=lambda _hwnd: False,
        is_desktop=lambda _hwnd: False,
    ) is None


def test_foreground_app_window_dispatches_to_win32(monkeypatch) -> None:
    editor = WindowInfo(title="Editor", handle=_EDITOR, pid=7)
    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(ws, "_foreground_app_window_windows", lambda: editor)

    assert ws.foreground_app_window() == editor


def test_foreground_app_window_is_the_foreground_window_elsewhere(monkeypatch) -> None:
    front = WindowInfo(title="Terminal", handle=99, pid=8)
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(ws, "detect_platform", lambda platform=platform: platform)
        monkeypatch.setattr(ws, "foreground_window", lambda: front)

        assert ws.foreground_app_window() == front


def test_foreground_app_window_never_raises(monkeypatch) -> None:
    def boom():
        raise OSError("user32 gone")

    monkeypatch.setattr(ws, "detect_platform", lambda: "win32")
    monkeypatch.setattr(ws, "_foreground_app_window_windows", boom)

    assert ws.foreground_app_window() is None
