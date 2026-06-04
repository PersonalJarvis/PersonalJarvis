"""Unit tests for jarvis/ui/icon_utils.py.

Tests cross-platform correctness:
  - project_icon_path_for_platform returns the right extension per OS
  - All Win32 helpers are no-ops on non-Windows
  - macOS / Linux helpers are no-ops on Windows
  - load_ico_as_pil_image can open the real .ico file (Windows only)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Platform-path helpers
# ---------------------------------------------------------------------------


def test_project_icon_path_returns_ico_on_windows():
    """Windows: project_icon_path_for_platform() returns .ico path."""
    if sys.platform != "win32":
        return  # live check only on Windows
    from jarvis.ui.icon_utils import project_icon_path_for_platform

    p = project_icon_path_for_platform()
    assert p.suffix == ".ico", f"Expected .ico on win32, got {p.suffix}"


def test_project_icon_path_for_platform_darwin(monkeypatch):
    """macOS: suffix is .icns (if present) or .png."""
    monkeypatch.setattr(sys, "platform", "darwin")
    from jarvis.ui import icon_utils

    # Reload so the sys.platform guard re-evaluates
    import importlib
    importlib.reload(icon_utils)

    p = icon_utils.project_icon_path_for_platform()
    assert p.suffix in (".icns", ".png"), f"Expected .icns/.png on darwin, got {p.suffix}"


def test_project_icon_path_for_platform_linux(monkeypatch):
    """Linux: suffix is .png."""
    monkeypatch.setattr(sys, "platform", "linux")
    from jarvis.ui import icon_utils

    import importlib
    importlib.reload(icon_utils)

    p = icon_utils.project_icon_path_for_platform()
    assert p.suffix == ".png", f"Expected .png on linux, got {p.suffix}"


# ---------------------------------------------------------------------------
# Win32 helpers are no-ops on non-Windows
# ---------------------------------------------------------------------------


def test_ensure_windows_app_identity_noop_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from jarvis.ui import icon_utils
    import importlib; importlib.reload(icon_utils)

    assert icon_utils.ensure_windows_app_identity() is False


def test_set_window_icon_by_title_noop_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    from jarvis.ui import icon_utils
    import importlib; importlib.reload(icon_utils)

    ico = tmp_path / "fake.ico"
    ico.write_bytes(b"")
    assert icon_utils.set_window_icon_by_title("Anything", ico) is False


def test_set_window_icon_for_current_process_noop_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    from jarvis.ui import icon_utils
    import importlib; importlib.reload(icon_utils)

    ico = tmp_path / "fake.ico"
    ico.write_bytes(b"")
    assert icon_utils.set_window_icon_for_current_process(ico) is False


def test_force_taskbar_icon_refresh_noop_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from jarvis.ui import icon_utils
    import importlib; importlib.reload(icon_utils)

    assert icon_utils.force_taskbar_icon_refresh(12345) is False


# ---------------------------------------------------------------------------
# macOS / Linux helpers are no-ops on Windows
# ---------------------------------------------------------------------------


def test_set_macos_dock_icon_noop_on_windows():
    """macOS hook is a no-op on Windows."""
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import set_macos_dock_icon

    assert set_macos_dock_icon() is False


def test_set_linux_window_icon_noop_on_windows():
    """Linux hook is a no-op on Windows."""
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import set_linux_window_icon

    assert set_linux_window_icon() is False


# ---------------------------------------------------------------------------
# Missing-file guard
# ---------------------------------------------------------------------------


def test_apply_icon_to_hwnd_missing_file_returns_false():
    """_apply_icon_to_hwnd returns False when .ico does not exist."""
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import _apply_icon_to_hwnd  # type: ignore[attr-defined]

    assert _apply_icon_to_hwnd(0, Path("does_not_exist.ico")) is False


def test_set_window_icon_for_current_process_missing_file_returns_false():
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import set_window_icon_for_current_process

    assert set_window_icon_for_current_process(Path("does_not_exist.ico")) is False


# ---------------------------------------------------------------------------
# load_ico_as_pil_image — uses the real asset on Windows
# ---------------------------------------------------------------------------


def test_load_ico_as_pil_image_real_asset():
    """The shipped jarvis.ico loads successfully via Pillow."""
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import load_ico_as_pil_image, project_icon_path

    img = load_ico_as_pil_image(project_icon_path(), size=64)
    assert img is not None
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_load_ico_as_pil_image_missing_returns_none(tmp_path):
    from jarvis.ui.icon_utils import load_ico_as_pil_image

    result = load_ico_as_pil_image(tmp_path / "ghost.ico")
    assert result is None


# ---------------------------------------------------------------------------
# project_icon_path still exists (backward compat for tray + desktop_app)
# ---------------------------------------------------------------------------


def test_project_icon_path_backward_compat():
    """project_icon_path() still exported for tray.py / desktop_app.py."""
    from jarvis.ui.icon_utils import project_icon_path

    p = project_icon_path()
    assert isinstance(p, Path)
    assert p.name == "jarvis.ico"


# ---------------------------------------------------------------------------
# set_window_appusermodel_icon — IPropertyStore path
# ---------------------------------------------------------------------------


def test_set_window_appusermodel_icon_noop_on_linux(monkeypatch, tmp_path):
    """IPropertyStore helper is a no-op on non-Windows."""
    monkeypatch.setattr(sys, "platform", "linux")
    from jarvis.ui import icon_utils
    import importlib
    importlib.reload(icon_utils)

    ico = tmp_path / "fake.ico"
    ico.write_bytes(b"")
    assert icon_utils.set_window_appusermodel_icon(12345, "Test.App", ico) is False


def test_set_window_appusermodel_icon_missing_file_returns_false():
    """Returns False when .ico does not exist (no propsys call is made)."""
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import set_window_appusermodel_icon

    assert set_window_appusermodel_icon(0, "Test.App", Path("no_such.ico")) is False


def test_set_window_appusermodel_icon_zero_hwnd_returns_false():
    """Returns False for hwnd=0 without touching propsys."""
    if sys.platform != "win32":
        return
    from jarvis.ui.icon_utils import set_window_appusermodel_icon, project_icon_path

    assert set_window_appusermodel_icon(0, "Test.App", project_icon_path()) is False


def test_set_window_appusermodel_icon_live_hwnd():
    """Calls SHGetPropertyStoreForWindow on a real HWND and verifies S_OK path.

    Uses GetForegroundWindow as a conveniently available visible top-level HWND.
    We verify the function returns True (all four HRESULTs S_OK).  The actual
    taskbar rendering cannot be asserted in an automated test.
    """
    if sys.platform != "win32":
        return
    import ctypes

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return  # no foreground window in this environment

    from jarvis.ui.icon_utils import (
        set_window_appusermodel_icon,
        project_icon_path,
        APP_USER_MODEL_ID,
    )

    result = set_window_appusermodel_icon(hwnd, APP_USER_MODEL_ID, project_icon_path())
    assert result is True, (
        "IPropertyStore SetValue+Commit should succeed on a real visible HWND"
    )
