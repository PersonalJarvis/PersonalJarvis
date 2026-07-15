"""Taskbar identity (name) tests for ``jarvis.ui.icon_utils``.

The taskbar *icon* was already fixed (class-icon override). This covers the
independent *name* layer. Setting the AppUserModelID only groups the taskbar
button; the name shown on hover / in the jump-list header is resolved by
matching the running window's AUMID to a **Start-Menu shortcut** carrying the
same ``System.AppUserModel.ID`` and using that shortcut's file name + icon.
Without such a shortcut Windows falls back to the process ``FileDescription``
(``pythonw.exe`` -> "Python") — the exact symptom the user reported. (The HKCU
``DisplayName`` registered separately is the *toast-notification* identity, a
different surface that does NOT name the taskbar button.)

The Windows tests use a throwaway AUMID and a throwaway ``programs_dir`` /
delete the registry key afterwards, so they never touch the live
``PersonalJarvis.PersonalJarvis`` registration or the real Start Menu.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis.ui.icon_utils import (
    APP_DISPLAY_NAME,
    APP_USER_MODEL_ID,
    START_MENU_SHORTCUT_NAME,
    ensure_start_menu_shortcut,
    project_icon_path,
    register_windows_app_user_model_id,
)

_TEST_AUMID = "PersonalJarvis.Test.IconIdentity"
_TEST_SUBKEY = rf"Software\Classes\AppUserModelId\{_TEST_AUMID}"

_PROPSTORE_IID = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"  # IID_IPropertyStore


def _read_shortcut_aumid(lnk: Path) -> str | None:
    """Read a .lnk's embedded System.AppUserModel.ID via the property store."""
    import pywintypes
    from win32com.propsys import propsys, pscon

    store = propsys.SHGetPropertyStoreFromParsingName(
        str(lnk), None, 0, pywintypes.IID(_PROPSTORE_IID)
    )
    return str(store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue())


def _delete_test_key() -> None:
    if sys.platform != "win32":
        return
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _TEST_SUBKEY)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Icon RESOLUTION — the single point every Win32 icon surface depends on.
#
# The window class icon, AUMID icon, Start-Menu shortcut, taskbar name and tray
# all resolve the icon through ``project_icon_path()``. If that returns a path
# that does not exist, ALL of them silently fall back to the ``pythonw.exe``
# Python logo — the "taskbar shows Python on a fresh machine" report. The icon
# used to live ONLY at ``<repo-root>/assets/icons/jarvis.ico`` (found via
# ``parents[2]``), which resolves only for a run from the project folder; a real
# ``pip install`` relocates the package to ``site-packages`` where that repo-root
# ``assets/`` is absent. The fix bundles the icon inside the package. These
# tests are platform-neutral on purpose: the bug is about file *presence*, which
# must hold on every OS / CI runner, not just Windows.
# ---------------------------------------------------------------------------


def test_project_icon_path_always_exists() -> None:
    """The resolved desktop icon must be a real file on every install layout."""
    p = project_icon_path()
    assert p.is_file(), (
        f"project_icon_path() -> {p} does not exist; every Win32 icon surface "
        "would fall back to the pythonw.exe Python logo"
    )
    assert p.suffix == ".ico"


def test_project_icon_path_prefers_bundled_in_package_copy() -> None:
    """The primary resolution is the in-package copy, so it ships with any install."""
    from jarvis.assets import bundled_app_icon

    bundled = bundled_app_icon()
    assert bundled is not None and bundled.is_file()
    # The in-package copy lives under jarvis/assets/icons/, NOT the repo root.
    assert bundled.as_posix().endswith("jarvis/assets/icons/jarvis.ico")
    # project_icon_path() returns exactly that bundled copy when present.
    assert project_icon_path() == bundled


def test_bundled_icon_is_byte_identical_to_repo_root_copy() -> None:
    """Drift guard: the packaged icon must match the build-tool repo-root copy.

    The repo-root ``assets/icons/jarvis.ico`` is still referenced by the
    PyInstaller spec and ``scripts/install_shortcuts.py``. Keeping the two copies
    byte-identical means updating the brand icon in one place without the runtime
    and the installer drifting apart. If this fails, re-copy the repo-root icon
    into ``jarvis/assets/icons/``.
    """
    repo_root = Path(__file__).resolve().parents[3] / "assets" / "icons" / "jarvis.ico"
    if not repo_root.is_file():
        pytest.skip("repo-root icon copy absent (slim checkout)")
    from jarvis.assets import bundled_app_icon

    bundled = bundled_app_icon()
    assert bundled is not None
    assert bundled.read_bytes() == repo_root.read_bytes(), (
        "jarvis/assets/icons/jarvis.ico drifted from assets/icons/jarvis.ico — "
        "re-copy the repo-root icon into the package"
    )


def test_bundled_app_icon_png_exists_for_linux() -> None:
    """Linux's .desktop Icon= needs a PNG (most desktops can't render .ico).

    Platform-neutral on purpose: the bug is about file *presence*, which must hold
    on every OS / CI runner. Without it the Linux autostart/menu entry — and the
    running window's taskbar button — falls back to the generic python3 icon.
    """
    from jarvis.assets import bundled_app_icon_png

    png = bundled_app_icon_png()
    assert png is not None and png.is_file()
    assert png.as_posix().endswith("jarvis/assets/icons/jarvis.png")


def test_app_display_name_is_personal_jarvis() -> None:
    assert APP_DISPLAY_NAME == "Personal Jarvis"
    # The grouping key itself stays the stable PascalCase AUMID.
    assert APP_USER_MODEL_ID == "PersonalJarvis.PersonalJarvis"


def test_register_aumid_is_noop_off_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows path is covered by the real-registry tests")
    assert register_windows_app_user_model_id(_TEST_AUMID) is False


@pytest.mark.skipif(sys.platform != "win32", reason="registry is Windows-only")
def test_register_aumid_writes_display_name_to_hkcu() -> None:
    import winreg

    _delete_test_key()
    try:
        ok = register_windows_app_user_model_id(
            _TEST_AUMID, display_name="Personal Jarvis"
        )
        assert ok is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _TEST_SUBKEY) as key:
            value, vtype = winreg.QueryValueEx(key, "DisplayName")
        assert value == "Personal Jarvis"
        assert vtype == winreg.REG_SZ
    finally:
        _delete_test_key()


@pytest.mark.skipif(sys.platform != "win32", reason="registry is Windows-only")
def test_register_aumid_writes_icon_resource_when_given() -> None:
    import winreg

    _delete_test_key()
    ico = Path(r"C:\fake\jarvis.ico")
    try:
        ok = register_windows_app_user_model_id(
            _TEST_AUMID, display_name="Personal Jarvis", icon_path=ico
        )
        assert ok is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _TEST_SUBKEY) as key:
            value, _ = winreg.QueryValueEx(key, "IconResource")
        # Icon resource is "<path>,<index>" so Explorer can pick the frame.
        assert value.startswith(str(ico))
    finally:
        _delete_test_key()


@pytest.mark.skipif(sys.platform != "win32", reason="registry is Windows-only")
def test_register_aumid_is_idempotent() -> None:
    _delete_test_key()
    try:
        first = register_windows_app_user_model_id(_TEST_AUMID)
        second = register_windows_app_user_model_id(_TEST_AUMID)
        assert first is True
        assert second is True
    finally:
        _delete_test_key()


# ---------------------------------------------------------------------------
# Start-Menu shortcut — the mechanism that actually names the taskbar button.
#
# Windows resolves a grouped taskbar button's name + icon by matching the
# running window's process AUMID to a Start-Menu shortcut carrying the same
# System.AppUserModel.ID, and using that shortcut's file name + icon. The HKCU
# DisplayName above does NOT drive this surface (it is the toast-notification
# identity). These tests target a throwaway Programs directory so they never
# touch the real Start Menu.
# ---------------------------------------------------------------------------


def test_start_menu_shortcut_name_is_personal_jarvis() -> None:
    # The .lnk *file name* (sans suffix) is what the taskbar shows as the name.
    assert START_MENU_SHORTCUT_NAME == "Personal Jarvis.lnk"


def test_ensure_start_menu_shortcut_noop_off_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows path is covered by the real-shortcut tests")
    assert ensure_start_menu_shortcut(programs_dir=Path("unused-off-windows")) is False


@pytest.mark.skipif(sys.platform != "win32", reason="shortcuts are Windows-only")
def test_ensure_start_menu_shortcut_creates_aumid_tagged_lnk(tmp_path: Path) -> None:
    ok = ensure_start_menu_shortcut(aumid=_TEST_AUMID, programs_dir=tmp_path)
    assert ok is True
    lnk = tmp_path / START_MENU_SHORTCUT_NAME
    assert lnk.is_file()
    # The embedded AUMID is what Windows matches the running window against.
    assert _read_shortcut_aumid(lnk) == _TEST_AUMID


@pytest.mark.skipif(sys.platform != "win32", reason="shortcuts are Windows-only")
def test_ensure_start_menu_shortcut_is_idempotent(tmp_path: Path) -> None:
    first = ensure_start_menu_shortcut(aumid=_TEST_AUMID, programs_dir=tmp_path)
    second = ensure_start_menu_shortcut(aumid=_TEST_AUMID, programs_dir=tmp_path)
    assert first is True
    assert second is True
    assert (tmp_path / START_MENU_SHORTCUT_NAME).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="shortcuts are Windows-only")
def test_ensure_start_menu_shortcut_rewrites_a_live_but_stale_target(
    tmp_path: Path,
) -> None:
    """An old Python executable may still exist after a venv migration."""
    from win32com.client import Dispatch

    assert ensure_start_menu_shortcut(aumid=_TEST_AUMID, programs_dir=tmp_path)
    link = tmp_path / START_MENU_SHORTCUT_NAME
    shell = Dispatch("WScript.Shell")
    original = shell.CreateShortcut(str(link)).TargetPath

    stale = tmp_path / "old-pythonw.exe"
    stale.write_bytes(b"still exists")
    shortcut = shell.CreateShortcut(str(link))
    shortcut.TargetPath = str(stale)
    shortcut.Save()
    assert shell.CreateShortcut(str(link)).TargetPath == str(stale)

    assert ensure_start_menu_shortcut(aumid=_TEST_AUMID, programs_dir=tmp_path)
    assert shell.CreateShortcut(str(link)).TargetPath == original
