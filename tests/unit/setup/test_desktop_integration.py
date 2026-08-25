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


def test_macos_managed_install_gets_real_app_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _managed_root(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    apps = tmp_path / "Applications"

    # Force the deterministic cross-platform fixture bundle on every host: the
    # fixture root has no real venv interpreter, so the native clang build can
    # never succeed against it. The REAL darwin build + LaunchServices probe is
    # covered by the dedicated "Build and self-probe" step in
    # .github/workflows/macos-desktop.yml and tests/unit/setup/test_macos_app_bundle.py.
    from jarvis.setup import macos_app_bundle as mab

    monkeypatch.setattr(mab.sys, "platform", "linux")

    report = di.ensure_desktop_integration(
        install_dir=root,
        platform="darwin",
        macos_applications_dir=apps,
    )

    assert report.ok is True
    assert report.artifacts == ("applications_bundle",)
    assert (apps / "Personal Jarvis.app" / "Contents" / "Info.plist").is_file()


def test_macos_bundle_failure_warning_carries_recorded_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    root = _managed_root(tmp_path)

    def _fail(**_kwargs: object) -> None:
        monkeypatch.setattr(
            mab, "_LAST_ERROR", "RuntimeError: py2app alias build failed: boom"
        )
        return None

    monkeypatch.setattr(mab, "ensure_macos_app_bundle", _fail)

    report = di.ensure_desktop_integration(
        install_dir=root,
        platform="darwin",
        macos_applications_dir=tmp_path / "Applications",
    )

    assert report.ok is False
    assert report.warnings == (
        "could not create the macOS application bundle: "
        "RuntimeError: py2app alias build failed: boom",
    )


def test_macos_bundle_failure_without_recorded_reason_says_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.setup.macos_app_bundle as mab

    root = _managed_root(tmp_path)
    monkeypatch.setattr(mab, "_LAST_ERROR", None)
    monkeypatch.setattr(mab, "ensure_macos_app_bundle", lambda **_kwargs: None)

    report = di.ensure_desktop_integration(
        install_dir=root,
        platform="darwin",
        macos_applications_dir=tmp_path / "Applications",
    )

    assert report.ok is False
    assert report.warnings == (
        "could not create the macOS application bundle: unknown error",
    )


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
    # A throwaway Desktop too — never write into the real one from a test.
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    subkey = rf"Software\PersonalJarvisTests\DesktopIntegration\{tmp_path.name}"
    aumid = f"PersonalJarvis.Test.DesktopIntegration.{tmp_path.name}"
    aumid_subkey = rf"Software\Classes\AppUserModelId\{aumid}"
    try:
        report = di.ensure_desktop_integration(
            install_dir=root,
            platform="win32",
            windows_programs_dir=programs,
            windows_desktop_dir=desktop,
            windows_registry_subkey=subkey,
            windows_aumid=aumid,
        )

        assert report.ok is True
        assert set(report.artifacts) == {
            "start_menu_launcher",
            "desktop_launcher",
            "installed_apps_registration",
            "windows_app_identity",
        }
        assert (programs / "Personal Jarvis.lnk").is_file()
        # The Desktop entry is what Windows Search can actually see: the
        # per-user Start Menu lives under %APPDATA%, which the content index
        # excludes by default (forensic 2026-08-16 — the app was unfindable by
        # name on such a box while its Start-Menu .lnk sat right there).
        assert (desktop / "Personal Jarvis.lnk").is_file()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "Personal Jarvis"
            assert winreg.QueryValueEx(key, "InstallLocation")[0] == str(root)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, aumid_subkey) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "Personal Jarvis"

        removed = di.remove_desktop_integration(
            platform="win32",
            windows_programs_dir=programs,
            windows_desktop_dir=desktop,
            windows_registry_subkey=subkey,
            windows_aumid=aumid,
        )
        assert removed.ok is True
        assert not (programs / "Personal Jarvis.lnk").exists()
        # An uninstall that leaves an icon on the desktop has not uninstalled.
        assert not (desktop / "Personal Jarvis.lnk").exists()
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


# --- Frozen (native-installer) builds ---------------------------------------
#
# A native installer (Windows Setup.exe, macOS .dmg, Linux .AppImage/.deb)
# creates the Start-menu entry, the Applications bundle and the .desktop file,
# and removes them again on uninstall. Two things must therefore never happen
# from inside a frozen app: writing a second, source-shaped launcher over the
# installer's working one (a frozen executable cannot run
# "<interpreter> -m jarvis.ui.web.launcher" at all), and deleting an artifact
# it did not create.


def _freeze(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make ``jarvis.core.frozen.is_frozen()`` report a PyInstaller onedir bundle."""

    internal = tmp_path / "bundle" / "_internal"
    internal.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_frozen_install_registers_nothing(
    platform: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, tmp_path)
    # Deliberately a MANAGED tree: the frozen probe has to win even over the
    # marker that normally authorizes registration.
    root = _managed_root(tmp_path)
    apps = tmp_path / "applications"
    programs = tmp_path / "Programs"

    report = di.ensure_desktop_integration(
        install_dir=root,
        platform=platform,
        windows_programs_dir=programs,
        windows_desktop_dir=tmp_path / "Desktop",
        windows_registry_subkey=rf"Software\PersonalJarvisTests\Frozen\{tmp_path.name}",
        windows_aumid=f"PersonalJarvis.Test.Frozen.{tmp_path.name}",
        macos_applications_dir=apps,
        linux_applications_dir=apps,
    )

    assert report.attempted is False
    assert report.ok is True
    assert report.artifacts == ()
    assert report.skipped_reason == di.FROZEN_SKIP_REASON
    assert not apps.exists()
    assert not programs.exists()


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_frozen_uninstall_leaves_the_installers_artifacts_alone(
    platform: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, tmp_path)
    apps = tmp_path / "applications"
    apps.mkdir()
    entry = apps / "personal-jarvis.desktop"
    entry.write_text("[Desktop Entry]\n", encoding="utf-8")
    bundle_plist = apps / "Personal Jarvis.app" / "Contents" / "Info.plist"
    bundle_plist.parent.mkdir(parents=True)
    bundle_plist.write_text("<plist/>\n", encoding="utf-8")
    programs = tmp_path / "Programs"
    programs.mkdir()
    shortcut = programs / "Personal Jarvis.lnk"
    shortcut.write_bytes(b"the installer's own launcher")

    report = di.remove_desktop_integration(
        platform=platform,
        windows_programs_dir=programs,
        windows_desktop_dir=tmp_path / "Desktop",
        windows_registry_subkey=rf"Software\PersonalJarvisTests\Frozen\{tmp_path.name}",
        windows_aumid=f"PersonalJarvis.Test.Frozen.{tmp_path.name}",
        macos_applications_dir=apps,
        linux_applications_dir=apps,
    )

    assert report.attempted is False
    assert report.ok is True
    assert report.artifacts == ()
    assert report.skipped_reason == di.FROZEN_SKIP_REASON
    # Deleting these would leave an installed product with no way to start it.
    assert entry.is_file()
    assert bundle_plist.is_file()
    assert shortcut.read_bytes() == b"the installer's own launcher"


def test_frozen_build_writes_no_linux_application_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Runs on every OS: the applications_dir seam is what makes the writer
    # reachable off Linux, and inside an AppImage sys.executable points into a
    # mount that is gone the moment the app exits - so the entry this would
    # write launches nothing at all.
    from jarvis.ui import icon_utils

    _freeze(monkeypatch, tmp_path)
    apps = tmp_path / "applications"

    assert icon_utils.ensure_linux_desktop_entry(applications_dir=apps) is False
    assert not apps.exists()


def test_windows_shortcut_writers_consult_the_frozen_guard() -> None:
    from jarvis.ui import icon_utils

    for writer in (
        icon_utils.ensure_start_menu_shortcut,
        icon_utils.ensure_desktop_shortcut,
    ):
        source = inspect.getsource(writer)
        assert "_installer_owns_shell_registration" in source, writer.__name__


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell links only")
def test_frozen_windows_build_does_not_touch_the_installers_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.ui import icon_utils

    _freeze(monkeypatch, tmp_path)
    programs = tmp_path / "Programs"
    programs.mkdir()
    installer_lnk = programs / icon_utils.START_MENU_SHORTCUT_NAME
    installer_lnk.write_bytes(b"the installer's own launcher")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    assert icon_utils.ensure_start_menu_shortcut(programs_dir=programs) is False
    assert icon_utils.ensure_desktop_shortcut(desktop_dir=desktop) is False
    assert installer_lnk.read_bytes() == b"the installer's own launcher"
    assert not (desktop / icon_utils.START_MENU_SHORTCUT_NAME).exists()
