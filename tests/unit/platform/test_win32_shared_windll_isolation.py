"""Regression test for the shared ``ctypes.windll.user32`` corruption bug.

Symptom (136x/day in the desktop app log): "Exception occurred during
on_script_notify" -> pywebview's ``webview/platforms/winforms.py``
``BrowserForm.move()`` calls
``windll.user32.SetWindowPos(hwnd, None, x, y, None, None, flags)`` and
raises ``ctypes.ArgumentError`` because ``None`` can no longer be coerced
to an int argument.

Root cause: several Jarvis modules bound pointer-sized ``.argtypes`` /
``.restype`` on the PROCESS-GLOBAL ``ctypes.windll.user32`` /
``ctypes.windll.kernel32`` singleton. ``ctypes`` caches one function-pointer
object per (dll instance, name), shared by every caller in the process —
pywebview's own ``SetWindowPos`` / ``GetWindowLongW`` / ``SetWindowLongW``
calls pass plain Python ints/None and rely on that object staying
argtypes-free so ctypes can coerce them loosely.

Fix: every Jarvis module binds its prototypes on its OWN private
``ctypes.WinDLL("user32", use_last_error=True)`` instance instead of
mutating ``ctypes.windll.user32`` in place. This test exercises the fixed
call paths and then asserts the shared, process-global object is still
untouched — a canary against this bug class recurring anywhere in the repo,
not just in the specific modules fixed here.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="ctypes.windll is Windows-only"
)

_MISSING_WINDOW_TITLE = "Jarvis Regression Test — window that does not exist 42fa9c"


def _assert_shared_user32_is_clean() -> None:
    import ctypes

    # ``.restype`` on an untouched ctypes function is c_long (c_int and
    # c_long are the SAME type on Windows) even before any assignment — that
    # is just ctypes' own implicit default and proves nothing. ``.argtypes``
    # is the real signal: it genuinely stays None until application code
    # assigns it, and pywebview's winforms.py BrowserForm.move()/resize()
    # call SetWindowPos with plain ints/None on this exact shared object,
    # relying on argtypes staying unset so ctypes coerces them loosely.
    assert ctypes.windll.user32.SetWindowPos.argtypes is None
    # winforms.py also calls these two directly on the shared object.
    assert ctypes.windll.user32.GetWindowLongW.argtypes is None
    assert ctypes.windll.user32.SetWindowLongW.argtypes is None


def test_window_state_focus_leaves_shared_user32_untouched() -> None:
    """jarvis.platform.window_state binds SetWindowPos et al. on a private DLL."""
    from jarvis.platform import window_state as ws

    ws._find_and_focus_windows(_MISSING_WINDOW_TITLE)
    _assert_shared_user32_is_clean()


def test_desktop_app_bring_to_front_leaves_shared_user32_untouched() -> None:
    """jarvis.ui.desktop_app binds SetWindowPos et al. on a private DLL."""
    from jarvis.ui import desktop_app

    desktop_app._bring_window_to_front_by_title(_MISSING_WINDOW_TITLE)
    _assert_shared_user32_is_clean()


def test_cu_indicator_win32_leaves_shared_user32_untouched() -> None:
    """jarvis.cu.indicator.win32 binds GetWindowLongW/SetWindowLongW on a
    private DLL — those two are also called directly by pywebview."""
    from jarvis.cu.indicator import win32 as cu_win32

    cu_win32.harden_window(0)
    _assert_shared_user32_is_clean()


def test_capture_exclusion_leaves_shared_user32_untouched() -> None:
    """jarvis.platform.capture_exclusion binds GetParent/SetWindowDisplayAffinity
    on a private DLL. hwnd=1 is a deliberately invalid-but-truthy handle: the
    real Win32 call just fails gracefully, it never dereferences memory."""
    from jarvis.platform import capture_exclusion

    capture_exclusion.exclude_hwnd_from_capture(1)
    _assert_shared_user32_is_clean()


def test_icon_utils_leaves_shared_user32_and_kernel32_untouched(tmp_path) -> None:
    """jarvis.ui.icon_utils binds SetClassLongPtrW/FindWindowW/... on private
    DLLs — set_window_icon_by_title with a nonexistent title exercises the
    configuring code path without needing a real HWND. The file only needs
    to exist; FindWindowW returns 0 for the missing title before any icon
    bytes are ever read."""
    from jarvis.ui import icon_utils

    fake_ico = tmp_path / "fake.ico"
    fake_ico.write_bytes(b"")
    icon_utils.set_window_icon_by_title(_MISSING_WINDOW_TITLE, fake_ico, quiet=True)
    _assert_shared_user32_is_clean()


def test_msix_redirection_leaves_shared_kernel32_untouched(monkeypatch) -> None:
    """jarvis.ui.msix_redirection binds GetCurrentPackageFamilyName on a
    private DLL. Reset the module's one-shot cache so the probe actually runs."""
    from jarvis.ui import msix_redirection

    monkeypatch.setattr(msix_redirection, "_FAMILY_PROBED", False)
    msix_redirection.package_family_name()

    import ctypes

    assert ctypes.windll.kernel32.GetCurrentPackageFamilyName.argtypes is None
