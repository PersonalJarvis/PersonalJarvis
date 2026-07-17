"""Cross-platform desktop registration for managed Personal Jarvis installs.

The public installer is source-based: it creates a virtual environment and runs
the desktop through Python.  Without explicit shell registration that leaves a
working process which the operating system does not consider an installed app.

This module owns the external, per-user artifacts as one lifecycle:

* Windows: Start-menu launcher, AppUserModel identity, and Installed Apps entry.
* macOS: ``~/Applications/Personal Jarvis.app``.
* Linux: ``$XDG_DATA_HOME/applications/personal-jarvis.desktop``.

Installer, updater, first desktop boot, and uninstaller all call this module so
an update cannot move the code while leaving its launcher behind.  Registration
is restricted to installer-managed trees by default; a developer checkout must
never acquire an uninstall entry which could delete the checkout.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MANAGED_MARKER = ".jarvis-managed-install"
WINDOWS_UNINSTALL_SUBKEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PersonalJarvis"
)
WINDOWS_APP_USER_MODEL_ID = "PersonalJarvis.PersonalJarvis"


def _windows_aumid_subkey(aumid: str) -> str:
    return rf"Software\Classes\AppUserModelId\{aumid}"


@dataclass(frozen=True)
class DesktopIntegrationReport:
    """Serializable result shared by installer, updater, and boot repair."""

    platform: str
    managed: bool
    attempted: bool
    ok: bool
    artifacts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifacts"] = list(self.artifacts)
        payload["warnings"] = list(self.warnings)
        return payload


def _install_root() -> Path:
    import jarvis

    return Path(jarvis.__file__).resolve().parent.parent


def _version() -> str:
    try:
        from jarvis import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - registration remains useful without a version
        return "0.0.0"


def _platform(value: str | None = None) -> str:
    raw = value or sys.platform
    if raw == "win32":
        return "windows"
    if raw == "darwin":
        return "macos"
    if raw.startswith("linux"):
        return "linux"
    return raw


def is_managed_install(install_dir: Path) -> bool:
    """Return whether ``install_dir`` was created by the public installer."""

    return (install_dir / MANAGED_MARKER).is_file()


def _windows_uninstall_script(install_dir: Path) -> Path:
    return install_dir / "install" / "uninstall.ps1"


def _windows_powershell() -> str:
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return str(candidate)
    return "powershell.exe"


def windows_uninstall_values(
    install_dir: Path,
    *,
    version: str | None = None,
    icon_path: Path | None = None,
) -> dict[str, str | int]:
    """Render the per-user Windows Installed Apps registry values.

    Pure and platform-neutral so quoting and metadata stay testable on every CI
    runner.  The writer below is the only Windows-specific part.
    """

    script = _windows_uninstall_script(install_dir)
    base = [
        _windows_powershell(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    values: dict[str, str | int] = {
        "DisplayName": "Personal Jarvis",
        "DisplayVersion": version or _version(),
        "Publisher": "Personal Jarvis contributors",
        "InstallLocation": str(install_dir),
        "UninstallString": subprocess.list2cmdline(base),
        "QuietUninstallString": subprocess.list2cmdline([*base, "--yes"]),
        "URLInfoAbout": "https://github.com/PersonalJarvis/PersonalJarvis",
        "NoModify": 1,
        "NoRepair": 1,
    }
    if icon_path is not None and icon_path.is_file():
        values["DisplayIcon"] = f"{icon_path},0"
    return values


def register_windows_installed_app(
    install_dir: Path,
    *,
    registry_subkey: str = WINDOWS_UNINSTALL_SUBKEY,
    platform: str | None = None,
) -> bool:
    """Create or refresh the per-user Windows Installed Apps entry."""

    if _platform(platform) != "windows":
        return False
    script = _windows_uninstall_script(install_dir)
    if not script.is_file():
        log.warning("Windows app registration skipped: uninstall script is missing")
        return False
    try:
        import winreg

        from jarvis.ui.icon_utils import project_icon_path

        values = windows_uninstall_values(
            install_dir,
            icon_path=project_icon_path(),
        )
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            registry_subkey,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, kind, value)
        return True
    except Exception as exc:  # noqa: BLE001 - shell registration is best-effort
        log.warning("Windows Installed Apps registration failed: %s", exc)
        return False


def _delete_windows_registry_key(subkey: str) -> bool:
    try:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        except FileNotFoundError:
            pass
        return True
    except Exception as exc:  # noqa: BLE001 - uninstall continues with other artifacts
        log.warning("Windows registry cleanup failed for %s: %s", subkey, exc)
        return False


def _windows_programs_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _remove_windows_desktop_integration(
    *,
    programs_dir: Path | None = None,
    registry_subkey: str = WINDOWS_UNINSTALL_SUBKEY,
    aumid: str = WINDOWS_APP_USER_MODEL_ID,
) -> tuple[bool, tuple[str, ...]]:
    warnings: list[str] = []
    programs = programs_dir or _windows_programs_dir()
    if programs is not None:
        shortcut = programs / "Personal Jarvis.lnk"
        try:
            shortcut.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"could not remove the Start-menu launcher: {exc}")
    if not _delete_windows_registry_key(registry_subkey):
        warnings.append("could not remove the Installed Apps registry entry")
    if not _delete_windows_registry_key(_windows_aumid_subkey(aumid)):
        warnings.append("could not remove the Windows app identity")
    return not warnings, tuple(warnings)


def _linux_applications_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "applications"


def _remove_linux_desktop_entry(applications_dir: Path | None = None) -> bool:
    entry = (applications_dir or _linux_applications_dir()) / "personal-jarvis.desktop"
    try:
        entry.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("Linux application-menu cleanup failed: %s", exc)
        return False


def _linux_gui_available(applications_dir: Path | None) -> bool:
    if applications_dir is not None:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ensure_desktop_integration(
    *,
    install_dir: Path | None = None,
    platform: str | None = None,
    require_managed: bool = True,
    windows_programs_dir: Path | None = None,
    windows_registry_subkey: str = WINDOWS_UNINSTALL_SUBKEY,
    windows_aumid: str = WINDOWS_APP_USER_MODEL_ID,
    macos_applications_dir: Path | None = None,
    linux_applications_dir: Path | None = None,
) -> DesktopIntegrationReport:
    """Install or repair the current platform's desktop-shell artifacts."""

    root = (install_dir or _install_root()).resolve()
    plat = _platform(platform)
    managed = is_managed_install(root)
    if require_managed and not managed:
        return DesktopIntegrationReport(
            platform=plat,
            managed=False,
            attempted=False,
            ok=True,
            skipped_reason="not an installer-managed checkout",
        )

    artifacts: list[str] = []
    warnings: list[str] = []
    if plat == "windows":
        try:
            from jarvis.ui.icon_utils import (
                ensure_start_menu_shortcut,
                project_icon_path,
                register_windows_app_user_model_id,
            )

            icon = project_icon_path()
            if ensure_start_menu_shortcut(
                aumid=windows_aumid,
                icon_path=icon if icon.is_file() else None,
                programs_dir=windows_programs_dir,
            ):
                artifacts.append("start_menu_launcher")
            else:
                warnings.append("could not create the Start-menu launcher")
            if register_windows_app_user_model_id(
                windows_aumid,
                icon_path=icon if icon.is_file() else None,
            ):
                artifacts.append("windows_app_identity")
            else:
                warnings.append("could not register the Windows app identity")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not load Windows shell integration: {exc}")
        if register_windows_installed_app(
            root,
            registry_subkey=windows_registry_subkey,
            platform="win32",
        ):
            artifacts.append("installed_apps_registration")
        else:
            warnings.append("could not register Personal Jarvis in Installed Apps")
    elif plat == "macos":
        try:
            from jarvis.setup import macos_app_bundle

            bundle = macos_app_bundle.ensure_macos_app_bundle(
                install_dir=root,
                applications_dir=macos_applications_dir,
            )
            if bundle is not None:
                artifacts.append("applications_bundle")
            else:
                reason = macos_app_bundle.last_error() or "unknown error"
                warnings.append(
                    f"could not create the macOS application bundle: {reason}"
                )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not create the macOS application bundle: {exc}")
    elif plat == "linux":
        if not _linux_gui_available(linux_applications_dir):
            return DesktopIntegrationReport(
                platform=plat,
                managed=managed,
                attempted=False,
                ok=True,
                skipped_reason="headless Linux session",
            )
        try:
            from jarvis.ui.icon_utils import ensure_linux_desktop_entry

            if ensure_linux_desktop_entry(applications_dir=linux_applications_dir):
                artifacts.append("applications_menu_entry")
            else:
                warnings.append("could not create the Linux application-menu entry")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not create the Linux application-menu entry: {exc}")
    else:
        return DesktopIntegrationReport(
            platform=plat,
            managed=managed,
            attempted=False,
            ok=True,
            skipped_reason="unsupported desktop platform",
        )

    for warning in warnings:
        log.warning("Desktop integration: %s", warning)
    return DesktopIntegrationReport(
        platform=plat,
        managed=managed,
        attempted=True,
        ok=not warnings,
        artifacts=tuple(artifacts),
        warnings=tuple(warnings),
    )


def remove_desktop_integration(
    *,
    platform: str | None = None,
    windows_programs_dir: Path | None = None,
    windows_registry_subkey: str = WINDOWS_UNINSTALL_SUBKEY,
    windows_aumid: str = WINDOWS_APP_USER_MODEL_ID,
    macos_applications_dir: Path | None = None,
    linux_applications_dir: Path | None = None,
) -> DesktopIntegrationReport:
    """Remove the current platform's external desktop-shell artifacts."""

    plat = _platform(platform)
    warnings: list[str] = []
    artifacts: list[str] = []
    if plat == "windows":
        ok, found = _remove_windows_desktop_integration(
            programs_dir=windows_programs_dir,
            registry_subkey=windows_registry_subkey,
            aumid=windows_aumid,
        )
        artifacts.extend(
            (
                "start_menu_launcher",
                "installed_apps_registration",
                "windows_app_identity",
            )
        )
        if not ok:
            warnings.extend(found)
    elif plat == "macos":
        try:
            from jarvis.setup.macos_app_bundle import remove_macos_app_bundle

            if remove_macos_app_bundle(applications_dir=macos_applications_dir):
                artifacts.append("applications_bundle")
            else:
                warnings.append("could not remove the macOS application bundle")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not remove the macOS application bundle: {exc}")
    elif plat == "linux":
        if _remove_linux_desktop_entry(linux_applications_dir):
            artifacts.append("applications_menu_entry")
        else:
            warnings.append("could not remove the Linux application-menu entry")
    else:
        return DesktopIntegrationReport(
            platform=plat,
            managed=False,
            attempted=False,
            ok=True,
            skipped_reason="unsupported desktop platform",
        )
    return DesktopIntegrationReport(
        platform=plat,
        managed=False,
        attempted=True,
        ok=not warnings,
        artifacts=tuple(artifacts),
        warnings=tuple(warnings),
    )


def main(argv: list[str] | None = None) -> int:
    # Route log.warning detail to stderr so the installer can surface the real
    # failure reason; the --json contract stays: JSON is the last stdout line.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Repair Personal Jarvis desktop registration")
    parser.add_argument("--install-dir", type=Path, default=None)
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--allow-unmanaged", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = (
        remove_desktop_integration()
        if args.remove
        else ensure_desktop_integration(
            install_dir=args.install_dir,
            require_managed=not args.allow_unmanaged,
        )
    )
    if args.json:
        print(json.dumps(report.to_dict(), sort_keys=True))
    elif report.ok:
        print("Personal Jarvis desktop registration is ready.")
    else:
        print("Personal Jarvis desktop registration is incomplete.", file=sys.stderr)
        for warning in report.warnings:
            print(f"- {warning}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DesktopIntegrationReport",
    "MANAGED_MARKER",
    "WINDOWS_APP_USER_MODEL_ID",
    "WINDOWS_UNINSTALL_SUBKEY",
    "ensure_desktop_integration",
    "is_managed_install",
    "register_windows_installed_app",
    "remove_desktop_integration",
    "windows_uninstall_values",
]
