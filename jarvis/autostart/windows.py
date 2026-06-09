"""Windows login autostart via a per-user **logon Scheduled Task** (with a
``shell:startup`` ``.lnk`` fallback).

Why a scheduled task and not just the startup shortcut? Windows 11 processes
``shell:startup`` items through Explorer's **throttled, serialized startup queue**
— one item at a time, ~30 s apart. On a machine with many startup programs the
Jarvis shortcut fires 4-8 minutes after login (measured: a sibling ``.lnk`` in the
same Startup folder fired ~9 min in), so the user reasonably concludes "autostart
is broken". The Task Scheduler is a separate subsystem that is **not** subject to
that throttle: a logon-triggered task starts Jarvis within seconds of login.

The trade-off: *registering* a task needs a one-time elevation (UAC) — a
non-elevated process is denied (verified on Windows 11, even for an Administrator
account's filtered token). *Reading* a task's state does not. So:

* The task is (un)registered only on an **interactive** call (Settings toggle /
  wizard), where a single UAC prompt is contextually expected. Once created it
  fires every login forever and Jarvis itself runs **non-elevated**
  (``RunLevel=Limited`` → microphone access, the "no Windows Service" rule AP-17).
* The silent **boot reconcile** (``interactive=False``) never prompts. If the task
  is missing it ensures the no-elevation ``.lnk`` fallback so autostart still
  works (just possibly delayed). The Settings panel surfaces an "enable instant
  start" affordance to upgrade the fallback to a task.

Everything shells out to PowerShell (subprocess) exactly like
``scripts/install_shortcuts.py`` — **no ``pywin32`` dependency**. The
script-assembly functions are pure (unit-testable cross-platform); only execution
requires Windows. The ``.lnk`` builders (``build_create_script`` /
``build_read_script``) are unchanged and still used for the fallback.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

# Scheduled-task identity. A stable name so reconcile can find/refresh it.
TASK_NAME = "Personal Jarvis Autostart"
# How long after login the task waits before launching — lets the desktop settle
# without the multi-minute Explorer startup throttle.
_LOGON_DELAY_SECONDS = 20

_SHORTCUT_NAME = "Personal Jarvis.lnk"
# Divergent names the old wizard/install paths used — removed on every write so
# Jarvis never auto-starts twice.
_LEGACY_NAMES = ("Jarvis.lnk", "Jarvis.bat", "Personal Jarvis.bat")
_READBACK_SENTINEL = "<<<JARVIS_LNK>>>"
_QUERY_SENTINEL = "<<<JARVIS_TASK>>>"


@dataclass(frozen=True, slots=True)
class _TaskInfo:
    """The action of the current scheduled task (read back for drift detection)."""

    execute: str
    arguments: str
    working_dir: str


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _shortcut_path() -> Path:
    return _startup_dir() / _SHORTCUT_NAME


def _norm(p: str | None) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def _current_user_id() -> str:
    """``DOMAIN\\user`` for the *current* login session.

    Baked into the register script at generation time so the task always targets
    the logged-in user, not whichever admin account approves the UAC prompt.
    """
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = os.environ.get("USERNAME", "").strip()
    if domain and user:
        return f"{domain}\\{user}"
    if user:
        return user
    import getpass

    return getpass.getuser()


# --------------------------------------------------------------------------- #
# Pure PowerShell-script builders (CI-provable on any OS)                      #
# --------------------------------------------------------------------------- #


def build_register_task_script(
    task_name: str, spec: LaunchSpec, user_id: str, *, delay_seconds: int = _LOGON_DELAY_SECONDS
) -> str:
    """Pure: the elevated PowerShell that registers the logon task.

    ``RunLevel=Limited`` → the launched Jarvis is NOT elevated (mic access);
    ``AtLogOn`` + ``Delay`` → fires a few seconds after login, off the Explorer
    startup throttle.
    """
    args = " ".join(spec.args)
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  $action = New-ScheduledTaskAction -Execute '{spec.program}' "
        f"-Argument '{args}' -WorkingDirectory '{spec.working_dir}'\n"
        "  $trigger = New-ScheduledTaskTrigger -AtLogOn\n"
        f"  $trigger.Delay = 'PT{int(delay_seconds)}S'\n"
        f"  $principal = New-ScheduledTaskPrincipal -UserId '{user_id}' "
        "-LogonType Interactive -RunLevel Limited\n"
        "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-MultipleInstances IgnoreNew\n"
        f"  Register-ScheduledTask -TaskName '{task_name}' -Action $action "
        "-Trigger $trigger -Principal $principal -Settings $settings "
        "-Description 'Personal Jarvis (Autostart)' -Force | Out-Null\n"
        "  exit 0\n"
        "} catch { exit 1 }\n"
    )


def build_query_task_script(task_name: str) -> str:
    """Pure: non-elevated PowerShell that prints the task action via sentinels."""
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$t = Get-ScheduledTask -TaskName '{task_name}'\n"
        "if ($t) {\n"
        "  $a = $t.Actions | Select-Object -First 1\n"
        f"  Write-Output ('{_QUERY_SENTINEL}' + $a.Execute)\n"
        f"  Write-Output ('{_QUERY_SENTINEL}' + $a.Arguments)\n"
        f"  Write-Output ('{_QUERY_SENTINEL}' + $a.WorkingDirectory)\n"
        "}\n"
    )


def build_unregister_task_script(task_name: str) -> str:
    """Pure: elevated PowerShell that removes the task (idempotent)."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false "
        "-ErrorAction SilentlyContinue\n"
        "  exit 0\n"
        "} catch { exit 1 }\n"
    )


def parse_task_query(stdout: str) -> _TaskInfo | None:
    """Pure: parse :func:`build_query_task_script` output. ``None`` if absent."""
    fields = [
        line[len(_QUERY_SENTINEL):]
        for line in stdout.splitlines()
        if line.startswith(_QUERY_SENTINEL)
    ]
    if len(fields) < 3:
        return None
    return _TaskInfo(execute=fields[0], arguments=fields[1], working_dir=fields[2])


def build_create_script(link: Path, spec: LaunchSpec) -> str:
    """Pure: the PowerShell script that creates/refreshes the fallback ``.lnk``.

    WindowStyle 7 = minimized (tray-friendly), 1 = normal/visible.
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


# --------------------------------------------------------------------------- #
# PowerShell execution (live; the elevated path triggers UAC)                 #
# --------------------------------------------------------------------------- #


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _run_powershell_elevated(script: str) -> bool:
    """Run ``script`` elevated via a one-time UAC prompt. ``True`` on success.

    Writes the privileged script to a temp ``.ps1`` (avoids ``-Command`` quoting
    hell), elevates it with ``Start-Process -Verb RunAs -Wait``, and forwards the
    exit code. A declined UAC prompt makes ``Start-Process`` throw → ``False``.
    """
    fd, path = tempfile.mkstemp(suffix=".ps1")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        launcher = (
            "$ErrorActionPreference = 'Stop'\n"
            "try {\n"
            "  $p = Start-Process -FilePath powershell -ArgumentList "
            "@('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',"
            f"'-File','{path}') -Verb RunAs -Wait -PassThru\n"
            "  exit $p.ExitCode\n"
            "} catch { exit 1 }\n"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", launcher],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        return result.returncode == 0
    except Exception as exc:  # noqa: BLE001 — declined UAC / launch failure → fallback
        log.warning("Elevated autostart task registration failed: %s", exc)
        return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class WindowsAutostart:
    """Logon Scheduled Task autostart manager, with a ``.lnk`` fallback.

    The side-effecting operations (task probe, elevated run, shortcut I/O) are
    injectable so the decision logic is CI-provable without a real Task Scheduler,
    UAC prompt, or ``WScript.Shell``.
    """

    def __init__(
        self,
        *,
        task_name: str = TASK_NAME,
        task_probe: Callable[[], _TaskInfo | None] | None = None,
        run_elevated: Callable[[str], bool] | None = None,
        shortcut_present: Callable[[], bool] | None = None,
        shortcut_matches: Callable[[LaunchSpec], bool] | None = None,
        write_shortcut: Callable[[LaunchSpec], None] | None = None,
        remove_shortcut: Callable[[], None] | None = None,
    ) -> None:
        self._task_name = task_name
        self._path = _shortcut_path()
        self._task_probe = task_probe or self._default_task_probe
        self._run_elevated = run_elevated or _run_powershell_elevated
        self._shortcut_present = shortcut_present or (lambda: self._path.exists())
        self._shortcut_matches = shortcut_matches or self._default_shortcut_matches
        self._write_shortcut = write_shortcut or self._default_write_shortcut
        self._remove_shortcut = remove_shortcut or self._default_remove_shortcut

    # ---- entry helpers -----------------------------------------------------

    def _task_entry_path(self) -> str:
        return f"Task Scheduler\\{self._task_name}"

    @staticmethod
    def _task_matches(info: _TaskInfo, spec: LaunchSpec) -> bool:
        return (
            _norm(info.execute) == _norm(spec.program)
            and info.arguments.strip() == " ".join(spec.args).strip()
            and _norm(info.working_dir) == _norm(spec.working_dir)
        )

    # ---- protocol ----------------------------------------------------------

    def status(self, spec: LaunchSpec) -> AutostartStatus:
        info = self._task_probe()
        if info is not None:
            matches = self._task_matches(info, spec)
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=matches,
                entry_path=self._task_entry_path(),
                detail=(
                    "Autostart enabled via scheduled task — instant start at login."
                    if matches
                    else "Scheduled task points at a different install "
                    "(re-enable in Settings to refresh)."
                ),
            )
        if self._shortcut_present():
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=self._shortcut_matches(spec),
                entry_path=str(self._path),
                detail=(
                    "Autostart via startup shortcut — may be delayed at boot; "
                    "enable instant start in Settings."
                ),
            )
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=self._task_entry_path(),
            detail="No autostart entry yet.",
        )

    def install(self, spec: LaunchSpec, *, interactive: bool = False) -> AutostartStatus:
        # Already correct → idempotent no-op (the common boot case once enabled).
        info = self._task_probe()
        if info is not None and self._task_matches(info, spec):
            return self.status(spec)

        if interactive:
            user_id = _current_user_id()
            script = build_register_task_script(self._task_name, spec, user_id)
            if self._run_elevated(script):
                # Task created → remove the throttled fallback so Jarvis won't
                # start twice (once via task, once via the .lnk).
                self._remove_shortcut()
                log.info("Windows autostart scheduled task registered: %s", self._task_name)
                return self.status(spec)
            log.info(
                "Autostart task not granted (UAC declined) — using startup shortcut fallback."
            )

        # Boot reconcile, or declined UAC: ensure the no-elevation fallback. Never
        # prompts. Jarvis still autostarts (possibly delayed) via the shortcut.
        self._write_shortcut(spec)
        return self.status(spec)

    def uninstall(self, *, interactive: bool = False) -> AutostartStatus:
        self._remove_shortcut()  # non-elevated, always
        info = self._task_probe()
        if info is not None and interactive:
            self._run_elevated(build_unregister_task_script(self._task_name))
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=self._task_entry_path(),
            detail="Autostart disabled.",
        )

    # ---- real (live) default operations ------------------------------------

    def _default_task_probe(self) -> _TaskInfo | None:
        try:
            result = _run_powershell(build_query_task_script(self._task_name))
        except Exception as exc:  # noqa: BLE001 — query failure → treat as absent
            log.debug("scheduled-task query failed: %s", exc)
            return None
        return parse_task_query(result.stdout)

    def _default_shortcut_matches(self, spec: LaunchSpec) -> bool:
        if not self._path.exists():
            return False
        try:
            result = _run_powershell(build_read_script(self._path))
        except Exception as exc:  # noqa: BLE001 — unreadable → not a match
            log.debug("shortcut read failed: %s", exc)
            return False
        fields = [
            line[len(_READBACK_SENTINEL):]
            for line in result.stdout.splitlines()
            if line.startswith(_READBACK_SENTINEL)
        ]
        target, args, workdir = (fields + ["", "", ""])[:3]
        return (
            _norm(target) == _norm(spec.program)
            and args.strip() == " ".join(spec.args).strip()
            and _norm(workdir) == _norm(spec.working_dir)
        )

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

    def _default_write_shortcut(self, spec: LaunchSpec) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_legacy()
        _run_powershell(build_create_script(self._path, spec))
        log.info("Windows autostart shortcut (fallback) written: %s", self._path)

    def _default_remove_shortcut(self) -> None:
        self._remove_legacy()
        if self._path.exists():
            try:
                self._path.unlink()
                log.info("Windows autostart shortcut removed: %s", self._path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", self._path, exc)


__all__ = [
    "WindowsAutostart",
    "TASK_NAME",
    "build_create_script",
    "build_read_script",
    "build_register_task_script",
    "build_query_task_script",
    "build_unregister_task_script",
    "parse_task_query",
]
