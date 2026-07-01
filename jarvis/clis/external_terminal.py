"""External terminal spawn: opens a real Windows Terminal (wt) or
PowerShell window detached from the app process.

Background: The embedded xterm.js + ConPTY in the desktop app is fine
for short commands but has UX limitations for interactive OAuth logins:
- Browser login redirects often land in the background
- The terminal is tied to the app section (user switches section → it disappears)
- No familiar "real terminal" look for the user

Solution: For install + connect we spawn an **external** terminal window
using ``Windows Terminal`` (``wt.exe``) as the preferred option, with a
fallback to ``pwsh.exe`` and finally ``powershell.exe``. The CLI command is
passed directly via ``-Command``; the terminal stays open with ``-NoExit``
so the user can see output and enter follow-up commands.

cwd default = user home (``%USERPROFILE%``), NOT the project directory —
the user wants a "neutral" terminal, not a "Personal-Jarvis" terminal.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def spawn_external_terminal(
    command: str,
    *,
    cwd: Path | None = None,
    title: str | None = None,
) -> tuple[bool, str]:
    """Open an external terminal window and execute ``command`` inside it.

    Args:
        command: The full PowerShell command (e.g. ``"firebase login"``).
            Passed to pwsh/powershell via ``-Command``.
        cwd: Working directory for the new terminal. ``None`` → user home.
        title: Optional tab title (only relevant for the ``wt`` path).

    Returns:
        ``(ok, used_method)`` — used_method is one of "wt", "pwsh", "powershell",
        or "failed". ok=True on success.

    Behavior:
        - The spawned terminal lives independently of the app process (detached).
        - ``-NoExit`` ensures the window stays open after the command finishes.
        - If no terminal is available: returns False with the reason logged.
    """
    workdir = Path(cwd) if cwd else Path(os.environ.get("USERPROFILE", str(Path.home())))
    workdir_str = str(workdir)

    # 1) Windows Terminal (wt) — preferred, because of modern UX + tabs.
    wt = shutil.which("wt")
    if wt:
        argv: list[str] = [
            wt,
            "new-tab",
            "--startingDirectory", workdir_str,
        ]
        if title:
            argv += ["--title", title]
        argv += [
            "pwsh.exe" if shutil.which("pwsh") else "powershell.exe",
            "-NoExit",
            "-NoLogo",
            "-Command", command,
        ]
        try:
            subprocess.Popen(  # noqa: S603
                argv,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                close_fds=True,
            )
            log.info("external terminal via wt: %s (cwd=%s)", command, workdir_str)
            return True, "wt"
        except Exception as exc:  # noqa: BLE001
            log.warning("wt spawn failed, falling back to pwsh: %s", exc)

    # 2) pwsh.exe as a standalone window.
    pwsh = shutil.which("pwsh")
    if pwsh:
        try:
            subprocess.Popen(  # noqa: S603
                [pwsh, "-NoExit", "-NoLogo", "-Command", command],
                cwd=workdir_str,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
            )
            log.info("external terminal via pwsh: %s (cwd=%s)", command, workdir_str)
            return True, "pwsh"
        except Exception as exc:  # noqa: BLE001
            log.warning("pwsh spawn failed, falling back to powershell: %s", exc)

    # 3) powershell.exe (Windows default).
    ps = shutil.which("powershell")
    if ps:
        try:
            subprocess.Popen(  # noqa: S603
                [ps, "-NoExit", "-NoLogo", "-Command", command],
                cwd=workdir_str,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
            )
            log.info("external terminal via powershell: %s (cwd=%s)", command, workdir_str)
            return True, "powershell"
        except Exception as exc:  # noqa: BLE001
            log.warning("powershell spawn failed: %s", exc)

    log.error("No external terminal available (wt/pwsh/powershell all not found)")
    return False, "failed"


__all__ = ["spawn_external_terminal"]
