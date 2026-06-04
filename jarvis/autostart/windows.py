"""Windows login autostart via a ``shell:startup`` ``.lnk`` shortcut.

Writes ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Personal
Jarvis.lnk`` targeting ``pythonw.exe -m jarvis.ui.web.launcher`` (GUI subsystem →
no console window, BUG-012 hygiene). The shortcut is created/read via PowerShell
+ ``WScript.Shell`` — the same mechanism as ``scripts/install_shortcuts.py``, so
there is **no ``pywin32`` dependency** (AD-7: the proven Windows path is reused
unchanged). This is a tray/login app, never a Windows Service (AP-17).

It also cleans up the divergent legacy autostart entries the old wizard left
behind (``Jarvis.bat`` / ``Jarvis.lnk``), collapsing the two historical
mechanisms into this one.

The PowerShell-script assembly is a pure function (unit-testable cross-platform);
only execution requires Windows.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

_SHORTCUT_NAME = "Personal Jarvis.lnk"
# Divergent names the old wizard/install paths used — removed on every write so
# Jarvis never auto-starts twice.
_LEGACY_NAMES = ("Jarvis.lnk", "Jarvis.bat", "Personal Jarvis.bat")
_READBACK_SENTINEL = "<<<JARVIS_LNK>>>"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _shortcut_path() -> Path:
    return _startup_dir() / _SHORTCUT_NAME


def _norm(p: str | None) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def build_create_script(link: Path, spec: LaunchSpec) -> str:
    """Pure: the PowerShell script that creates/refreshes the ``.lnk``.

    WindowStyle 7 = minimized (tray-friendly), 1 = normal.
    """
    window_style = 7 if spec.minimized else 1
    args = " ".join(spec.args)
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{link}')\n"
        f"$sc.TargetPath = '{spec.program}'\n"
        f"$sc.Arguments = '{args}'\n"
        f"$sc.WorkingDirectory = '{spec.working_dir}'\n"
        "$sc.Description = 'Personal Jarvis (Autostart)'\n"
        f"$sc.WindowStyle = {window_style}\n"
        "$sc.Save()\n"
    )


def build_read_script(link: Path) -> str:
    """Pure: PowerShell that prints TargetPath/Arguments/WorkingDirectory."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{link}')\n"
        f"Write-Output ('{_READBACK_SENTINEL}' + $sc.TargetPath)\n"
        f"Write-Output ('{_READBACK_SENTINEL}' + $sc.Arguments)\n"
        f"Write-Output ('{_READBACK_SENTINEL}' + $sc.WorkingDirectory)\n"
    )


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


class WindowsAutostart:
    """``shell:startup`` ``.lnk`` autostart manager."""

    def __init__(self) -> None:
        self._path = _shortcut_path()

    def _remove_legacy(self) -> None:
        startup = _startup_dir()
        for name in _LEGACY_NAMES:
            legacy = startup / name
            if legacy.exists():
                try:
                    legacy.unlink()
                    log.info("Removed legacy autostart entry: %s", legacy)
                except OSError as exc:
                    log.warning("Could not remove legacy %s: %s", legacy, exc)

    def status(self, spec: LaunchSpec) -> AutostartStatus:
        if not self._path.exists():
            return AutostartStatus(
                supported=True,
                installed=False,
                matches_spec=False,
                entry_path=str(self._path),
                detail="No autostart shortcut yet.",
            )
        try:
            result = _run_powershell(build_read_script(self._path))
        except Exception as exc:  # noqa: BLE001 — unreadable shortcut → treat as drift
            log.warning("Could not read %s: %s", self._path, exc)
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=False,
                entry_path=str(self._path),
                detail=f"Autostart shortcut present but unreadable: {exc}.",
            )
        fields = [
            line[len(_READBACK_SENTINEL):]
            for line in result.stdout.splitlines()
            if line.startswith(_READBACK_SENTINEL)
        ]
        target, args, workdir = (fields + ["", "", ""])[:3]
        matches = (
            _norm(target) == _norm(spec.program)
            and args.strip() == " ".join(spec.args).strip()
            and _norm(workdir) == _norm(spec.working_dir)
        )
        return AutostartStatus(
            supported=True,
            installed=True,
            matches_spec=matches,
            entry_path=str(self._path),
            detail=(
                "Autostart enabled and current."
                if matches
                else "Autostart shortcut points at a different install (will be refreshed)."
            ),
        )

    def install(self, spec: LaunchSpec) -> AutostartStatus:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_legacy()
        _run_powershell(build_create_script(self._path, spec))
        log.info("Windows autostart shortcut written: %s", self._path)
        return self.status(spec)

    def uninstall(self) -> AutostartStatus:
        self._remove_legacy()
        if self._path.exists():
            try:
                self._path.unlink()
                log.info("Windows autostart shortcut removed: %s", self._path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", self._path, exc)
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=str(self._path),
            detail="Autostart disabled.",
        )


__all__ = ["WindowsAutostart", "build_create_script", "build_read_script"]
