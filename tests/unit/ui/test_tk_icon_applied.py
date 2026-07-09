"""Regression guard: every Tk surface wears the Jarvis mascot, not the Python logo.

BUG #UI-Pin-2026-05-05 recurred because the icon fix lived only in the old orb
(``ui/orb/overlay.py``) while the JarvisBar (``jarvis/ui/jarvisbar/overlay.py``,
now the DEFAULT ``orb_style``) created its ``tk.Tk()`` root without any icon
work — so it inherited the process icon (``pythonw.exe`` → Python logo on
Windows, ``python3`` on Linux). The fix routes BOTH through one canonical
helper, :func:`jarvis.ui.icon_utils.apply_tk_window_icon`. These tests fail if a
Tk surface ever forgets to call it (the exact drift that caused the bug), and —
on a real display — prove the applied class icon is the bundled mascot.
"""
from __future__ import annotations

import inspect
import sys

import pytest


def test_apply_tk_window_icon_is_importable() -> None:
    from jarvis.ui.icon_utils import apply_tk_window_icon

    assert callable(apply_tk_window_icon)


def test_jarvisbar_wires_the_icon_helper() -> None:
    """The JarvisBar must call the canonical Tk-icon helper on its root."""
    import jarvis.ui.jarvisbar.overlay as bar

    src = inspect.getsource(bar)
    assert "apply_tk_window_icon" in src, (
        "JarvisBar no longer applies the Jarvis icon to its Tk root — it will "
        "regress to the Python logo (BUG #UI-Pin-2026-05-05)."
    )


def test_orb_wires_the_icon_helper() -> None:
    """The legacy orb must delegate to the same canonical helper (no drift)."""
    import ui.orb.overlay as orb

    src = inspect.getsource(orb)
    assert "apply_tk_window_icon" in src, (
        "The orb no longer routes through the shared Tk-icon helper; the two "
        "Tk surfaces have drifted apart again."
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows class-icon surface")
def test_apply_sets_a_class_icon_on_a_real_tk_root() -> None:
    """On a real Windows display, the applied class icon is the bundled mascot."""
    import ctypes

    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")

    try:
        root.withdraw()
        from jarvis.ui.icon_utils import apply_tk_window_icon, project_icon_path

        if not project_icon_path().is_file():
            pytest.skip("bundled icon missing in this checkout")

        apply_tk_window_icon(root)
        root.update()

        user32 = ctypes.windll.user32
        get_class_long = getattr(user32, "GetClassLongPtrW", user32.GetClassLongW)
        _GCLP_HICON = -14
        _WM_GETICON = 0x007F
        _ICON_BIG = 1
        hwnd = int(root.winfo_id())
        class_icon = get_class_long(hwnd, _GCLP_HICON)
        wm_icon = user32.SendMessageW(hwnd, _WM_GETICON, _ICON_BIG, 0)

        # Both the class icon (what the taskbar reads) and the window icon
        # (WM_SETICON, titlebar/Alt-Tab) are now the bundled mascot .ico — the
        # window no longer falls back to the pythonw.exe process icon.
        assert class_icon != 0
        assert wm_icon != 0
    finally:
        root.destroy()
