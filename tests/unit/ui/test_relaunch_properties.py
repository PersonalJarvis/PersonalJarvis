"""Regression guard: per-window Relaunch* properties brand the taskbar button.

THE universal taskbar-icon fix (2026-07-09, part 3). The Windows taskbar renders
a button with the icon of the window-owning EXECUTABLE unless the window carries
explicit ``System.AppUserModel.RelaunchIconResource`` properties — for a source
run under ``pythonw.exe`` that exe icon is the Python logo, and on MS-Store
Python the owning exe cannot even be copied/branded (read-only 0-byte alias), so
the branded-exe re-exec is impossible there. ``SHGetPropertyStoreForWindow`` +
the Relaunch* keys exist precisely for interpreter-hosted apps and take effect
on a LIVE window (verified on an MS-Store-Python machine). These tests pin the
wiring so no refactor silently drops the one layer that works everywhere.
"""
from __future__ import annotations

import inspect
import sys

import pytest

from jarvis.ui import icon_utils


def test_relaunch_properties_api_is_importable() -> None:
    assert callable(icon_utils.set_window_relaunch_properties)


def test_apply_icon_chokepoint_stamps_relaunch_properties() -> None:
    """Every icon path funnels through ``_apply_icon_to_hwnd`` — it must stamp
    the relaunch properties, or MS-Store-Python installs regress to the Python
    logo (the branded-exe re-exec cannot run there)."""
    src = inspect.getsource(icon_utils._apply_icon_to_hwnd)
    assert "set_window_relaunch_properties" in src


def test_desktop_icon_setter_reensures_the_start_menu_shortcut() -> None:
    """The icon-setter poll must re-ensure the Start-Menu shortcut once the
    window is up — without it, a shortcut deleted mid-session leaves Windows
    search unable to find "Personal Jarvis" (regression 2026-07-09)."""
    import jarvis.ui.desktop_app as desktop_app

    src = inspect.getsource(desktop_app.DesktopApp._start_icon_setter_thread)
    assert "ensure_start_menu_shortcut" in src


def test_non_windows_is_a_noop() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows behaviour covered by the real-window test")
    assert icon_utils.set_window_relaunch_properties(12345) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only shell surface")
def test_relaunch_properties_stick_on_a_real_window() -> None:
    """Stamp a real Tk window and read the properties back from the shell."""
    tk = pytest.importorskip("tkinter")
    pywintypes = pytest.importorskip("pywintypes")
    propsys_mod = pytest.importorskip("win32com.propsys")
    propsys = propsys_mod.propsys

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    try:
        root.withdraw()
        root.update_idletasks()
        hwnd = int(root.winfo_id())

        assert icon_utils.set_window_relaunch_properties(hwnd) is True
        # Second call: session-cached fast path.
        assert icon_utils.set_window_relaunch_properties(hwnd) is True

        fmtid = pywintypes.IID(icon_utils._APPUSERMODEL_FMTID)
        store = propsys.SHGetPropertyStoreForWindow(
            hwnd, propsys.IID_IPropertyStore
        )
        aumid = store.GetValue((fmtid, icon_utils._PID_AUMID)).GetValue()
        icon = store.GetValue((fmtid, icon_utils._PID_RELAUNCH_ICON)).GetValue()
        name = store.GetValue((fmtid, icon_utils._PID_RELAUNCH_NAME)).GetValue()

        assert aumid == icon_utils.APP_USER_MODEL_ID
        assert name == icon_utils.APP_DISPLAY_NAME
        assert str(icon).lower().endswith(".ico,0")
    finally:
        try:
            icon_utils._RELAUNCH_STAMPED.discard(hwnd)
        except Exception:  # noqa: BLE001 — cache hygiene only
            pass
        root.destroy()
