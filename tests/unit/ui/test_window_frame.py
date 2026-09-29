"""Resize hit-testing for a frameless window, with no live HWND and no wndproc."""

from types import SimpleNamespace

from jarvis.ui.window_frame import (
    caption_hit,
    install_resize_frame,
    maximized_bounds,
    native_hwnd,
    window_is_maximized,
)


def test_caption_hit_corners_beat_edges():
    assert caption_hit(0, 0, 200, 100) == 13
    assert caption_hit(7, 7, 200, 100) == 13
    assert caption_hit(199, 0, 200, 100) == 14
    assert caption_hit(192, 7, 200, 100) == 14
    assert caption_hit(0, 99, 200, 100) == 16
    assert caption_hit(7, 92, 200, 100) == 16
    assert caption_hit(199, 99, 200, 100) == 17
    assert caption_hit(192, 92, 200, 100) == 17


def test_caption_hit_edges_and_center():
    assert caption_hit(0, 50, 200, 100) == 10
    assert caption_hit(7, 40, 200, 100) == 10
    assert caption_hit(199, 50, 200, 100) == 11
    assert caption_hit(192, 40, 200, 100) == 11
    assert caption_hit(100, 0, 200, 100) == 12
    assert caption_hit(100, 7, 200, 100) == 12
    assert caption_hit(100, 99, 200, 100) == 15
    assert caption_hit(100, 92, 200, 100) == 15
    assert caption_hit(8, 40, 200, 100) is None
    assert caption_hit(191, 40, 200, 100) is None
    assert caption_hit(100, 8, 200, 100) is None
    assert caption_hit(100, 91, 200, 100) is None
    assert caption_hit(100, 50, 200, 100) is None


def test_caption_hit_maximized_and_non_positive_size_are_none():
    assert caption_hit(0, 0, 200, 100, maximized=True) is None
    assert caption_hit(0, 0, 0, 100) is None
    assert caption_hit(0, 0, 200, 0) is None
    assert caption_hit(0, 0, -1, -1) is None


def test_window_is_maximized_null_hwnd_is_false():
    assert window_is_maximized(0) is False


def test_native_hwnd_missing_is_none():
    assert native_hwnd(None) is None


def test_native_hwnd_prefers_to_int64():
    class _Handle:
        def ToInt64(self) -> int:
            return 42

    window = SimpleNamespace(native=SimpleNamespace(Handle=_Handle()))
    assert native_hwnd(window) == 42


def test_native_hwnd_falls_back_to_int():
    window = SimpleNamespace(native=SimpleNamespace(Handle=42))
    assert native_hwnd(window) == 42


def test_native_hwnd_handle_that_raises_is_none():
    class _Native:
        @property
        def Handle(self) -> int:
            raise RuntimeError("no handle")

    assert native_hwnd(SimpleNamespace(native=_Native())) is None


def test_install_resize_frame_null_hwnd_is_false():
    assert install_resize_frame(0) is False


def test_maximized_bounds_leave_the_taskbar_strip():
    # 4K monitor with a bottom taskbar: the window must not cover the monitor,
    # or Explorer takes it for a fullscreen app and hides the taskbar.
    assert maximized_bounds((0, 0, 3840, 2160), (0, 0, 3840, 2088)) == (0, 0, 3840, 2088)


def test_maximized_bounds_are_relative_to_the_monitor():
    # Secondary monitor on the right with a taskbar docked at its left edge.
    assert maximized_bounds((1920, 0, 3840, 1080), (1968, 0, 3840, 1080)) == (48, 0, 1872, 1080)
