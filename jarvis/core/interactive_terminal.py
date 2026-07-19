"""Open an interactive CLI in a real, user-visible terminal window.

Desktop applications do not own an interactive TTY.  Starting an OAuth CLI
with all three standard streams redirected to ``DEVNULL`` therefore launches a
process the user cannot see or answer.  This helper provides one capability
probe for the three supported desktop families and fails honestly on a
headless host.

The command is always supplied as an argv sequence.  Linux and Windows never
invoke a shell for native executables.  macOS Terminal.app accepts only a shell
command through AppleScript, so every argument and the working directory are
quoted with :mod:`shlex` before the command crosses that boundary.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)


class InteractiveTerminalUnavailable(RuntimeError):
    """Raised when the current session cannot open a graphical terminal."""


@dataclass(frozen=True)
class InteractiveTerminalLaunch:
    """Display-safe result of opening an external terminal."""

    pid: int | None
    method: str


def _validated_argv(argv: Sequence[str]) -> list[str]:
    values = [str(value) for value in argv]
    if not values or not values[0].strip():
        raise ValueError("An interactive terminal command requires an executable.")
    if any("\x00" in value for value in values):
        raise ValueError("Interactive terminal arguments cannot contain NUL bytes.")
    return values


def _macos_script(argv: Sequence[str], cwd: Path) -> str:
    command = f"cd {shlex.quote(str(cwd))} && {shlex.join(argv)}"
    apple_string = command.replace("\\", "\\\\").replace('"', '\\"')
    return f'tell application "Terminal"\nactivate\ndo script "{apple_string}"\nend tell'


def _launch_macos(
    argv: Sequence[str],
    *,
    cwd: Path,
) -> InteractiveTerminalLaunch:
    osascript = shutil.which("osascript")
    if not osascript:
        raise InteractiveTerminalUnavailable(
            "macOS Terminal could not be opened because osascript is unavailable."
        )
    try:
        result = subprocess.run(
            [osascript, "-e", _macos_script(argv, cwd)],
            capture_output=True,
            text=True,
            timeout=8.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InteractiveTerminalUnavailable(
            "macOS Terminal could not be opened from this session."
        ) from exc
    if result.returncode != 0:
        log.warning("macOS Terminal launch failed with exit code %s", result.returncode)
        raise InteractiveTerminalUnavailable(
            "macOS Terminal could not be opened from this session."
        )
    log.info("Opened interactive CLI in macOS Terminal")
    return InteractiveTerminalLaunch(pid=None, method="macos-terminal")


def _windows_command(argv: Sequence[str]) -> list[str]:
    suffix = Path(argv[0]).suffix.lower()
    if suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or "cmd.exe"
        return [comspec, "/d", "/k", subprocess.list2cmdline(list(argv))]
    if suffix == ".ps1":
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if not powershell:
            raise InteractiveTerminalUnavailable(
                "The CLI is a PowerShell script, but no PowerShell executable was found."
            )
        return [powershell, "-NoExit", "-NoLogo", "-File", *argv]
    return list(argv)


def _launch_windows(
    argv: Sequence[str],
    *,
    cwd: Path,
) -> InteractiveTerminalLaunch:
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    try:
        process = subprocess.Popen(  # noqa: S603 - validated argv, shell=False
            _windows_command(argv),
            cwd=str(cwd),
            creationflags=flags,
            close_fds=True,
        )
    except OSError as exc:
        raise InteractiveTerminalUnavailable(
            "Windows could not open a visible console for the login command."
        ) from exc
    log.info("Opened interactive CLI in a Windows console")
    return InteractiveTerminalLaunch(pid=process.pid, method="windows-console")


def _linux_terminal_command(
    name: str,
    executable: str,
    argv: Sequence[str],
    *,
    title: str,
) -> list[str]:
    if name == "gnome-terminal":
        return [executable, "--title", title, "--", *argv]
    if name == "konsole":
        return [executable, "--new-tab", "-p", f"tabtitle={title}", "-e", *argv]
    if name == "kitty":
        return [executable, "--title", title, *argv]
    if name == "alacritty":
        return [executable, "--title", title, "-e", *argv]
    if name == "xterm":
        return [executable, "-T", title, "-e", *argv]
    return [executable, "-e", *argv]


def _launch_linux(
    argv: Sequence[str],
    *,
    cwd: Path,
    title: str,
) -> InteractiveTerminalLaunch:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise InteractiveTerminalUnavailable(
            "No graphical terminal is available in this headless Linux session."
        )

    failures: list[str] = []
    for name in (
        "x-terminal-emulator",
        "gnome-terminal",
        "konsole",
        "kitty",
        "alacritty",
        "xterm",
    ):
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            process = subprocess.Popen(  # noqa: S603 - validated argv, shell=False
                _linux_terminal_command(name, executable, argv, title=title),
                cwd=str(cwd),
                start_new_session=True,
                close_fds=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except OSError:
            failures.append(name)
            continue
        log.info("Opened interactive CLI in Linux terminal %s", name)
        return InteractiveTerminalLaunch(pid=process.pid, method=name)

    if failures:
        log.warning("Linux terminal candidates failed to start: %s", failures)
    raise InteractiveTerminalUnavailable(
        "No supported graphical terminal emulator was found in this Linux session."
    )


def launch_interactive_terminal(
    argv: Sequence[str],
    *,
    title: str,
    cwd: Path | None = None,
) -> InteractiveTerminalLaunch:
    """Open ``argv`` in a visible terminal, or raise an honest capability error.

    Windows receives a fresh console, macOS uses Terminal.app, and Linux probes
    common terminal emulators only when a graphical display is present.  A
    headless server never starts an invisible OAuth process.
    """
    command = _validated_argv(argv)
    workdir = Path(cwd) if cwd is not None else Path.home()
    if sys.platform == "win32":
        return _launch_windows(command, cwd=workdir)
    if sys.platform == "darwin":
        return _launch_macos(command, cwd=workdir)
    if sys.platform.startswith("linux"):
        return _launch_linux(command, cwd=workdir, title=title)
    raise InteractiveTerminalUnavailable(
        f"Interactive terminal launch is not supported on platform {sys.platform!r}."
    )


__all__ = [
    "InteractiveTerminalLaunch",
    "InteractiveTerminalUnavailable",
    "launch_interactive_terminal",
]
