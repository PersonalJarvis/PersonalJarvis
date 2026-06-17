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
