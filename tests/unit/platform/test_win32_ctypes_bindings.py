from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

from jarvis.cu.indicator.win32 import _configure_user32 as configure_indicator_api
from jarvis.platform.window_state import _configure_window_query_api


class _Fn:
    def __call__(self, *args):
        return 0


def _api(*names: str) -> SimpleNamespace:
    return SimpleNamespace(**{name: _Fn() for name in names})


def test_window_query_bindings_preserve_pointer_sized_hwnds() -> None:
    user32 = _api(
        "GetForegroundWindow",
        "GetWindowTextLengthW",
        "GetWindowTextW",
        "GetWindowThreadProcessId",
        "GetWindowRect",
        "GetClientRect",
        "ClientToScreen",
        "IsWindowVisible",
        "IsIconic",
        "GetSystemMetrics",
        "GetClassNameW",
        "GetWindowLongW",
        "ShowWindow",
        "SetForegroundWindow",
        "SetActiveWindow",
        "BringWindowToTop",
        "AttachThreadInput",
        "GetWindowPlacement",
        "SetWindowPos",
    )

    _configure_window_query_api(user32, ctypes, wintypes)

    assert user32.GetForegroundWindow.restype is wintypes.HWND
    assert user32.GetWindowRect.argtypes[0] is wintypes.HWND
    assert ctypes.sizeof(user32.GetWindowRect.argtypes[0]) == ctypes.sizeof(
        ctypes.c_void_p
    )


def test_indicator_bindings_preserve_pointer_sized_hwnds() -> None:
    user32 = _api(
        "GetWindowLongW",
        "SetWindowLongW",
        "SetWindowDisplayAffinity",
    )

    configure_indicator_api(user32, ctypes, wintypes)

    assert user32.GetWindowLongW.argtypes[0] is wintypes.HWND
    assert user32.SetWindowDisplayAffinity.argtypes[0] is wintypes.HWND
    assert ctypes.sizeof(user32.GetWindowLongW.argtypes[0]) == ctypes.sizeof(
        ctypes.c_void_p
    )
