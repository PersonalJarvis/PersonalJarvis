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

import base64
import binascii
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.branding import (
    WINDOWS_AUTOSTART_DESCRIPTION,
)
from jarvis.core.branding import (
    WINDOWS_AUTOSTART_TASK_NAME as TASK_NAME,
)
from jarvis.core.branding import (
    WINDOWS_SHORTCUT_FILE_NAME as _SHORTCUT_NAME,
)
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

# Scheduled-task identity. A stable name so reconcile can find/refresh it.
# How long after login the task waits before launching — lets the desktop settle
# without the multi-minute Explorer startup throttle.
_LOGON_DELAY_SECONDS = 20

# Divergent names the old wizard/install paths used — removed on every write so
# Jarvis never auto-starts twice.
_LEGACY_NAMES = ("Jarvis.lnk", "Jarvis.bat", "Personal Jarvis.bat")
_READBACK_SENTINEL = "<<<JARVIS_LNK>>>"
_QUERY_SENTINEL = "<<<JARVIS_TASK>>>"


@dataclass(frozen=True, slots=True)
class _TaskInfo:
    """The action of the current scheduled task (read back for drift detection).

    ``enabled`` mirrors the task's own ``State``. A task can be switched off in
    Task Scheduler (or by a "startup optimizer") while still existing with a
    perfectly matching action — the read-back therefore has to carry it, or
    status() reports "enabled and current" for a task that never fires.
    """

    execute: str
    arguments: str
    working_dir: str
    enabled: bool = True


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


def _ps_lit(value: object) -> str:
    """Escape ``value`` for embedding in a SINGLE-quoted PowerShell literal.

    A single-quoted PowerShell string takes everything literally except the
    single quote itself, which is escaped by doubling it. Without this, one
    apostrophe anywhere in the baked-in values ends the literal early and the
    whole generated script fails to parse — so on a host whose login is
    ``O'Brien`` (``C:\\Users\\O'Brien\\...``) or whose project folder is
    ``Ruben's Jarvis``, task registration, task query and shortcut I/O all
    fail and autostart silently never works. ``_run_powershell_elevated``
    already escaped its own temp path this way; the script builders did not.
    """
    return str(value).replace("'", "''")


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
        f"  $action = New-ScheduledTaskAction -Execute '{_ps_lit(spec.program)}' "
        f"-Argument '{_ps_lit(args)}' -WorkingDirectory '{_ps_lit(spec.working_dir)}'\n"
        "  $trigger = New-ScheduledTaskTrigger -AtLogOn\n"
        f"  $trigger.Delay = 'PT{int(delay_seconds)}S'\n"
        f"  $principal = New-ScheduledTaskPrincipal -UserId '{_ps_lit(user_id)}' "
        "-LogonType Interactive -RunLevel Limited\n"
        "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-MultipleInstances IgnoreNew\n"
        f"  Register-ScheduledTask -TaskName '{_ps_lit(task_name)}' -Action $action "
        "-Trigger $trigger -Principal $principal -Settings $settings "
        f"-Description '{_ps_lit(WINDOWS_AUTOSTART_DESCRIPTION)}' -Force | Out-Null\n"
        "  exit 0\n"
        "} catch { exit 1 }\n"
    )


def _ps_emit_field(sentinel: str, expression: str) -> str:
    """Pure: one PowerShell line that prints ``expression`` base64-of-UTF-8.

    Why not print the value directly? Windows PowerShell 5.1 encodes a
    REDIRECTED stdout with ``[Console]::OutputEncoding`` — the OEM code page
    (cp850/cp437) — while Python's ``text=True`` decodes with the ANSI code
    page (cp1252). The two agree on ASCII, which is why this stayed invisible in
    English-only testing — but any accented or umlauted character in a user
    profile or project path came back corrupted, and some do not survive at all:
    byte 0x81 (u-umlaut in cp850) has no cp1252 mapping, so it RAISES inside
    subprocess's reader thread and the caller gets nothing. Either way
    ``_norm(readback) != _norm(spec)``, so the task/shortcut was reported as
    "points at a different install" forever — at every single login, on every
    profile whose name is not plain ASCII. Base64 is pure ASCII, so it survives
    any code page byte for byte and the value is reconstructed exactly (AP-7's
    encoding class, the read-side twin of the ``utf-8-sig`` fix in
    ``_run_powershell_elevated``).
    """
    return (
        f"Write-Output ('{sentinel}' + [Convert]::ToBase64String("
        f"$enc.GetBytes([string]({expression}))))\n"
    )


def build_query_task_script(task_name: str) -> str:
    """Pure: non-elevated PowerShell that prints the task action via sentinels.

    Emits FOUR fields: Execute, Arguments, WorkingDirectory and the task
    ``State`` — a disabled task still has a matching action, so without the
    state a switched-off autostart reads as "enabled and current".
    """
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        "$enc = [System.Text.Encoding]::UTF8\n"
        f"$t = Get-ScheduledTask -TaskName '{_ps_lit(task_name)}' | "
        "Select-Object -First 1\n"
        "if ($t) {\n"
        "  $a = $t.Actions | Select-Object -First 1\n"
        f"  {_ps_emit_field(_QUERY_SENTINEL, '$a.Execute')}"
        f"  {_ps_emit_field(_QUERY_SENTINEL, '$a.Arguments')}"
        f"  {_ps_emit_field(_QUERY_SENTINEL, '$a.WorkingDirectory')}"
        f"  {_ps_emit_field(_QUERY_SENTINEL, '$t.State')}"
        "}\n"
    )


def build_unregister_task_script(task_name: str) -> str:
    """Pure: elevated PowerShell that removes the task (idempotent)."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        f"  Unregister-ScheduledTask -TaskName '{_ps_lit(task_name)}' -Confirm:$false "
        "-ErrorAction SilentlyContinue\n"
        "  exit 0\n"
        "} catch { exit 1 }\n"
    )


def _sentinel_fields(stdout: str, sentinel: str) -> list[str]:
    """Pure: the base64-of-UTF-8 payloads behind ``sentinel``, decoded.

    A payload that is not valid base64 (a PowerShell host that emitted a
    warning on the same line, a truncated pipe) is dropped rather than guessed
    at, so the caller's "fewer fields than expected = drift, not a match" rule
    stays the single place that decides what a partial read-back means.
    """
    out: list[str] = []
    for line in stdout.splitlines():
        # lstrip: a UTF-8 BOM or stray whitespace must not hide the sentinel.
        stripped = line.lstrip("\ufeff \t")
        if not stripped.startswith(sentinel):
            continue
        payload = stripped[len(sentinel):].strip()
        if not payload:
            out.append("")    # an empty field is a legitimate value ("no args")
            continue
        try:
            out.append(base64.b64decode(payload, validate=True).decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            log.debug("undecodable read-back field %r: %s", payload, exc)
    return out


def parse_task_query(stdout: str) -> _TaskInfo | None:
    """Pure: parse :func:`build_query_task_script` output. ``None`` if absent."""
    fields = _sentinel_fields(stdout, _QUERY_SENTINEL)
    if len(fields) < 3:
        return None
    # A read-back without the 4th field is treated as enabled: only an explicit
    # "Disabled" may switch autostart off, so a truncated state can never raise
    # a false alarm about a task that is in fact running.
    state = fields[3].strip().lower() if len(fields) > 3 else ""
    return _TaskInfo(
        execute=fields[0],
        arguments=fields[1],
        working_dir=fields[2],
        enabled=state != "disabled",
    )


def build_create_script(link: Path, spec: LaunchSpec, *, icon: str | None = None) -> str:
    """Pure: the PowerShell script that creates/refreshes the fallback ``.lnk``.

    WindowStyle 7 = minimized (tray-friendly), 1 = normal/visible.

    ``icon`` is the absolute path to ``jarvis.ico``. When given, the shortcut
    carries ``IconLocation`` so the taskbar button is branded with the Jarvis
    icon from the moment the app autostarts — instead of the bare ``pythonw.exe``
    Python logo. Without it (the historical behaviour) an autostart launch on a
    box where the elevated scheduled task was UAC-declined shows the Python logo,
    because this fallback shortcut is then the only launch entry point and the
    runtime class-icon setter is still racing (see the taskbar-icon bug report).
    """
    window_style = 7 if spec.minimized else 1
    args = " ".join(spec.args)
    icon_line = f"$sc.IconLocation = '{_ps_lit(icon)},0'\n" if icon else ""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{_ps_lit(link)}')\n"
        f"$sc.TargetPath = '{_ps_lit(spec.program)}'\n"
        f"$sc.Arguments = '{_ps_lit(args)}'\n"
        f"$sc.WorkingDirectory = '{_ps_lit(spec.working_dir)}'\n"
        f"$sc.Description = '{_ps_lit(WINDOWS_AUTOSTART_DESCRIPTION)}'\n"
        f"{icon_line}"
        f"$sc.WindowStyle = {window_style}\n"
        "$sc.Save()\n"
    )


def build_read_script(link: Path) -> str:
    """Pure: PowerShell that prints TargetPath/Arguments/WorkingDirectory.

    Base64-encoded for the same reason as the task query — see
    :func:`_ps_emit_field`.
    """
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$enc = [System.Text.Encoding]::UTF8\n"
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{_ps_lit(link)}')\n"
        f"{_ps_emit_field(_READBACK_SENTINEL, '$sc.TargetPath')}"
        f"{_ps_emit_field(_READBACK_SENTINEL, '$sc.Arguments')}"
        f"{_ps_emit_field(_READBACK_SENTINEL, '$sc.WorkingDirectory')}"
    )


# --------------------------------------------------------------------------- #
# PowerShell execution (live; the elevated path triggers UAC)                 #
# --------------------------------------------------------------------------- #


def _resolve_app_icon() -> str | None:
    """Absolute ``jarvis.ico`` path for the shortcut, or ``None`` if unresolved.

    Lazy import (never at module scope, HN-7): keeps this Windows-only module
    free of a UI import on other OSes and off the boot critical path. Returns the
    same install-layout-agnostic path every other Win32 icon surface uses.
    """
    try:
        from jarvis.ui.icon_utils import project_icon_path

        ico = project_icon_path()
        return str(ico) if ico.is_file() else None
    except Exception as exc:  # noqa: BLE001 — a missing icon must never block autostart
        log.debug("autostart shortcut icon could not be resolved: %s", exc)
        return None


def _tag_shortcut_aumid(link: Path) -> bool:
    """Best-effort: write the app AUMID into ``link``'s property store.

    Mirrors ``scripts/install_shortcuts._set_shortcut_app_id`` (the proven path).
    Lazy pywin32 import in a try/except: on a host without pywin32 this is a
    silent no-op — the ``IconLocation`` set by :func:`build_create_script` is the
    load-bearing fix, the AUMID is a reinforcement that keeps this shortcut from
    diverging from the Start-Menu one. Never raises, never blocks autostart.
    """
    try:
        import pywintypes  # type: ignore[import-not-found]
        from win32com.propsys import propsys, pscon  # type: ignore[import-not-found]

        from jarvis.ui.icon_utils import APP_USER_MODEL_ID

        store = propsys.SHGetPropertyStoreFromParsingName(
            str(link),
            None,
            2,  # GPS_READWRITE
            pywintypes.IID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"),  # IID_IPropertyStore
        )
        store.SetValue(
            pscon.PKEY_AppUserModel_ID,
            propsys.PROPVARIANTType(APP_USER_MODEL_ID),
        )
        store.Commit()
        return True
    except Exception as exc:  # noqa: BLE001 — pywin32 absent / COM failure → icon-only fallback
        log.debug("autostart shortcut AUMID not tagged (non-fatal): %s", exc)
        return False


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    # encoding/errors are pinned rather than left to the locale: without them
    # Python decodes the pipe with the ANSI code page while PowerShell wrote it
    # with the OEM one, and an undecodable byte raises UnicodeDecodeError out of
    # a read-back that the callers translate into "no entry" (see
    # _ps_emit_field). The payloads themselves are base64 (pure ASCII), so this
    # only has to survive whatever a PowerShell host prints around them.
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="replace",
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
        # utf-8-SIG, not plain utf-8: Windows PowerShell 5.1 (the `powershell`
        # this launches) decodes a BOM-less -File script with the system ANSI
        # codepage. Without the BOM every non-ASCII character baked into the
        # script — an accented or umlauted login name in the interpreter path,
        # a project folder with one, the description — is mojibake'd, so the
        # registered task points at a path that does not exist (AP-7's BOM
        # class). Every non-English-speaking user has such a path.
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(script)
        # Escape any single-quote in the temp path (e.g. a login like O'Brien →
        # C:\Users\O'Brien\...\Temp) before baking it into the single-quoted PS arg.
        safe_path = path.replace("'", "''")
        # The path is additionally wrapped in LITERAL double quotes inside the
        # argument element. Start-Process joins -ArgumentList with single spaces
        # into one raw command line and quotes NOTHING, so on a host whose temp
        # directory contains a space — an account named "John Doe"
        # (C:\Users\John Doe\AppData\Local\Temp\...), or a redirected
        # TEMP=D:\My Temp — the elevated PowerShell received "-File C:\Users\John"
        # and aborted. The user saw the UAC prompt, approved it, and autostart
        # still fell back to the throttled .lnk while the log claimed the prompt
        # had been declined.
        launcher = (
            "$ErrorActionPreference = 'Stop'\n"
            "try {\n"
            "  $p = Start-Process -FilePath powershell -ArgumentList "
            "@('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',"
            f"'-File','\"{safe_path}\"') -Verb RunAs -Wait -PassThru\n"
            # `exit $null` exits with code 0 — measured on Windows 11,
            # 2026-08-10. `Start-Process -Verb RunAs -Wait -PassThru` can hand
            # back a process object whose ExitCode is $null, because the
            # elevated child is launched through ShellExecute and is not always
            # associated with the returned object. This function then reported
            # SUCCESS for a registration that never happened, and install()
            # took the success branch: it deleted the .lnk fallback this whole
            # module is built around and logged "scheduled task registered".
            # Autostart was dead on both paths with the log claiming otherwise.
            # Fail safe instead — a false negative only costs a redundant .lnk,
            # which the next boot reconcile removes once the probe sees the task.
            "  if ($null -eq $p -or $null -eq $p.ExitCode) { exit 1 }\n"
            "  exit $p.ExitCode\n"
            "} catch { exit 1 }\n"
        )
        # No check=True (unlike _run_powershell): a declined UAC prompt makes the
        # outer launcher exit non-zero, which we translate to a clean `False`
        # (→ .lnk fallback), never an exception.
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", launcher],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        # INFO so a declined prompt (non-zero) vs success (0) is diagnosable in the log.
        log.info("autostart task registration: powershell returncode=%d", result.returncode)
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
            action_matches = self._task_matches(info, spec)
            if not info.enabled:
                # A task can be switched off in Task Scheduler, by a group
                # policy or by a "startup optimizer" while its action still
                # points at exactly this install. Reporting that as "enabled
                # and current" left the Settings toggle showing autostart ON
                # for a task that never fires — and, because install() treats
                # a matching task as done, the .lnk fallback was not written
                # either, so autostart was dead on BOTH paths.
                detail = (
                    "The logon scheduled task exists but is disabled, so Jarvis "
                    "does not start at login — re-enable instant start in "
                    "Settings to switch it back on."
                )
            elif action_matches:
                detail = (
                    "Autostart enabled via scheduled task — instant start at login."
                )
            else:
                detail = (
                    "Scheduled task points at a different install "
                    "(re-enable in Settings to refresh)."
                )
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=action_matches and info.enabled,
                entry_path=self._task_entry_path(),
                detail=detail,
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
        # "Correct" includes being switched ON: a disabled task matches the spec
        # but never fires, so it must fall through to the refresh/fallback path
        # below instead of being reported as done.
        info = self._task_probe()
        if info is not None and info.enabled and self._task_matches(info, spec):
            # ...except for a leftover fallback .lnk. A boot whose task probe
            # transiently failed (PowerShell is slow under login load, and a
            # failed query is treated as "no task") writes the shortcut, and
            # nothing removed it once the task became visible again — so Jarvis
            # launched TWICE at login, once via the task and once via the
            # Explorer startup queue. The task is authoritative; drop the
            # fallback whenever both exist.
            if self._shortcut_present():
                log.info(
                    "Autostart: removing the leftover startup shortcut — the "
                    "scheduled task already covers this install."
                )
                self._remove_shortcut()
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
        elif info is not None:
            # Non-interactive boot reconcile found a task it cannot repair: either
            # *stale* (path drift — the BUG-006 restore-trap class) or *disabled*.
            # Re-registering needs elevation, which the silent reconcile must never
            # prompt for, so surface it loudly: the user must re-enable instant
            # start in Settings (one UAC prompt) to refresh it. The .lnk fallback
            # below keeps autostart working (delayed) meanwhile.
            log.warning(
                "Autostart scheduled task is %s; re-enable instant start in "
                "Settings to refresh it. Using shortcut fallback.",
                "disabled and will not fire"
                if not info.enabled
                else "stale (points at a different install)",
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
            info = self._task_probe()  # did the elevated removal actually land?
        if info is not None:
            # Unregistering the task needs elevation: the silent boot reconcile
            # must never prompt, and an interactive user can decline the UAC
            # prompt. Reporting "disabled" in either case was a lie — the
            # Settings toggle showed autostart off while the logon task kept
            # starting Jarvis at every login, with nothing to explain why.
            log.warning(
                "Autostart: the logon scheduled task survives (removing it needs "
                "a one-time admin confirmation) — reporting it as still installed."
            )
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=False,
                entry_path=self._task_entry_path(),
                detail=(
                    "Startup shortcut removed, but the scheduled task still starts "
                    "Jarvis at login — turn instant start off in Settings and "
                    "confirm the admin prompt to remove it."
                ),
            )
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
        fields = _sentinel_fields(result.stdout, _READBACK_SENTINEL)
        if len(fields) < 3:
            # A truncated read-back is "unknown", not "matches". Padding the
            # missing fields with "" made a half-failed COM read compare equal
            # to any spec with no args — reporting a stale shortcut as current
            # and suppressing the refresh that would have fixed it.
            log.debug(
                "shortcut read-back returned %d of 3 fields — treating as drift",
                len(fields),
            )
            return False
        target, args, workdir = fields[:3]
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
        _run_powershell(build_create_script(self._path, spec, icon=_resolve_app_icon()))
        # Tag the shortcut with the SAME AUMID as the Start-Menu shortcut so the
        # two same-named .lnk files don't diverge and confuse the shell's taskbar
        # button resolution (best-effort; a box without pywin32 still gets the
        # icon above, which is the load-bearing visual fix).
        _tag_shortcut_aumid(self._path)
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
