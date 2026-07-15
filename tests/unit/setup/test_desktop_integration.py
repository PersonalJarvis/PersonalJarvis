"""Desktop-shell registration lifecycle on Windows, macOS, and Linux."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from jarvis.setup import desktop_integration as di


def _managed_root(tmp_path: Path) -> Path:
    root = tmp_path / "install with spaces"
    (root / "install").mkdir(parents=True)
    (root / di.MANAGED_MARKER).write_text("{}\n", encoding="utf-8")
    (root / "install" / "uninstall.ps1").write_text("# test\n", encoding="utf-8")
    return root


def test_windows_uninstall_values_are_per_user_app_metadata(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    icon = tmp_path / "app.ico"
    icon.write_bytes(b"ico")

    values = di.windows_uninstall_values(root, version="9.8.7", icon_path=icon)

    assert values["DisplayName"] == "Personal Jarvis"
    assert values["DisplayVersion"] == "9.8.7"
    assert values["InstallLocation"] == str(root)
    assert str(root / "install" / "uninstall.ps1") in str(values["UninstallString"])
    assert "--yes" in str(values["QuietUninstallString"])
    assert values["DisplayIcon"] == f"{icon},0"
    assert values["NoModify"] == 1
    assert values["NoRepair"] == 1


def test_unmanaged_checkout_is_never_registered(tmp_path: Path) -> None:
    apps = tmp_path / "applications"

    report = di.ensure_desktop_integration(
        install_dir=tmp_path,
        platform="linux",
        linux_applications_dir=apps,
    )

    assert report.ok is True
    assert report.managed is False
    assert report.attempted is False
    assert report.skipped_reason == "not an installer-managed checkout"
    assert not apps.exists()


def test_linux_managed_install_gets_searchable_application_entry(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    apps = tmp_path / "applications"

    report = di.ensure_desktop_integration(
        install_dir=root,
        platform="linux",
        linux_applications_dir=apps,
    )

    assert report.ok is True
    assert report.artifacts == ("applications_menu_entry",)
    entry = apps / "personal-jarvis.desktop"
    text = entry.read_text(encoding="utf-8")
    assert "Name=Personal Jarvis" in text
    assert "StartupWMClass=personal-jarvis" in text


def test_headless_linux_does_not_create_desktop_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _managed_root(tmp_path)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    report = di.ensure_desktop_integration(install_dir=root, platform="linux")

    assert report.ok is True
    assert report.attempted is False
    assert report.skipped_reason == "headless Linux session"


def test_macos_managed_install_gets_real_app_bundle(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    apps = tmp_path / "Applications"

    report = di.ensure_desktop_integration(
        install_dir=root,
        platform="darwin",
        macos_applications_dir=apps,
    )

    assert report.ok is True
    assert report.artifacts == ("applications_bundle",)
    assert (apps / "Personal Jarvis.app" / "Contents" / "Info.plist").is_file()


def test_linux_uninstall_removes_application_entry(tmp_path: Path) -> None:
    apps = tmp_path / "applications"
    apps.mkdir()
    entry = apps / "personal-jarvis.desktop"
    entry.write_text("[Desktop Entry]\n", encoding="utf-8")

    report = di.remove_desktop_integration(
        platform="linux", linux_applications_dir=apps
    )

    assert report.ok is True
    assert not entry.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry and links only")
def test_windows_managed_install_is_visible_to_start_and_installed_apps(
    tmp_path: Path,
) -> None:
    import winreg

    root = _managed_root(tmp_path)
    programs = tmp_path / "Programs"
    subkey = rf"Software\PersonalJarvisTests\DesktopIntegration\{tmp_path.name}"
    aumid = f"PersonalJarvis.Test.DesktopIntegration.{tmp_path.name}"
    aumid_subkey = rf"Software\Classes\AppUserModelId\{aumid}"
    try:
        report = di.ensure_desktop_integration(
            install_dir=root,
            platform="win32",
            windows_programs_dir=programs,
            windows_registry_subkey=subkey,
            windows_aumid=aumid,
        )

        assert report.ok is True
        assert set(report.artifacts) == {
            "start_menu_launcher",
            "installed_apps_registration",
            "windows_app_identity",
        }
        assert (programs / "Personal Jarvis.lnk").is_file()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "Personal Jarvis"
            assert winreg.QueryValueEx(key, "InstallLocation")[0] == str(root)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, aumid_subkey) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "Personal Jarvis"

        removed = di.remove_desktop_integration(
            platform="win32",
            windows_programs_dir=programs,
            windows_registry_subkey=subkey,
            windows_aumid=aumid,
        )
        assert removed.ok is True
        assert not (programs / "Personal Jarvis.lnk").exists()
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey)
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, aumid_subkey)
    finally:
        for key in (subkey, aumid_subkey):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass


def test_desktop_boot_repairs_registration_after_first_paint() -> None:
    import jarvis.ui.desktop_app as desktop_app

    source = inspect.getsource(desktop_app.DesktopApp._inject_token)
    assert "_start_desktop_integration_repair" in source
