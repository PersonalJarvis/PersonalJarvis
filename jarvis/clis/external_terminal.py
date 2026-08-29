"""External terminal spawn: opens a real terminal window, detached from the app.

Background: The embedded xterm.js + ConPTY in the desktop app is fine
for short commands but has UX limitations for interactive OAuth logins:
- Browser login redirects often land in the background
- The terminal is tied to the app section (user switches section -> it disappears)
- No familiar "real terminal" look for the user

Solution: For install + connect we spawn an **external** terminal window and
keep it open after the command finishes, so the user can read the output and
type follow-up commands.

Each OS spells "open a terminal and run this" differently and no spelling
survives a move to another OS, so the platform picks the branch:

- Windows: Windows Terminal (``wt.exe``), else ``pwsh.exe``, else ``powershell.exe``
- macOS:   ``Terminal.app`` via ``osascript``
- Linux:   the first of ``x-terminal-emulator``, ``gnome-terminal``, ``konsole``,
           ``xfce4-terminal``, ``kitty``, ``alacritty``, ``xterm`` that exists

A headless box has no terminal to open at all. That is not a failure to hide:
the caller falls back to the in-app install job, which streams its output into
the UI instead. ``no_display`` says so explicitly, so the caller can tell
"nothing installed" apart from "installed somewhere you cannot see".

cwd default = user home, NOT the project directory - the user wants a
"neutral" terminal, not a "Personal-Jarvis" terminal.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Terminal emulators that accept ``-e <argv...>``, in the order we try them.
# ``x-terminal-emulator`` is the Debian alternatives symlink and therefore the
# user's own default; it goes first for that reason, not because it is common.
_LINUX_TERMINALS: tuple[str, ...] = (
    "x-terminal-emulator",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "kitty",
    "alacritty",
    "xterm",
)


def _has_display() -> bool:
    """Whether this Linux box can show a window at all.

    A container or an SSH session without X11 forwarding has neither variable
    set. Spawning a terminal there does not fail loudly - ``xterm`` exits with a
    "cannot open display" on stderr that nobody reads, and the app would report
    a successful install that never ran.
    """
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _posix_keep_open(command: str) -> list[str]:
    """``sh -c`` payload that runs ``command`` and then hands over an interactive shell.

    The trailing ``exec`` is what ``-NoExit`` does on Windows: the window stays
    up with the output still on screen. ``|| true`` keeps a failed install from
    closing the window before the error can be read - the exit status is
    printed instead.
    """
    return ["sh", "-c", f'{command}; echo; echo "[exit $?]"; exec ${{SHELL:-sh}} -i']



def path_refresh_command() -> str:
    """Shell snippet that makes a just-installed binary findable in THIS shell.

    A package manager writes the new binary's directory into the persisted PATH,
    which every shell started afterwards inherits - but not the one that is
    running the install. Without this line, chaining ``gcloud auth login`` onto
    ``winget install gcloud`` fails with "command not found" one second after a
    successful install, which reads as a broken installer rather than a stale
    environment.
    """
    if sys.platform == "win32":
        return (
            "$env:Path = "
            "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + "
            "[Environment]::GetEnvironmentVariable('Path','User')"
        )
    # POSIX shells cache resolved command paths; the PATH itself is inherited
    # from the login shell and a global install lands in a directory already on
    # it, so only the cache has to go.
    return "hash -r"


def chain_commands(parts: list[str]) -> str:
    """Join commands so each one runs only if the previous one succeeded.

    The spelling is not portable and there is no single one that works
    everywhere: ``&&`` is a parser error in Windows PowerShell 5.1, which is the
    last fallback when neither Windows Terminal nor pwsh is installed, so the
    Windows branch nests explicit ``$?`` tests instead.
    """
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if sys.platform == "win32":
        head, *rest = parts
        return f"{head}; if ($?) {{ {chain_commands(rest)} }}"
    return " && ".join(parts)


def _spawn_windows(command: str, workdir: str, title: str | None) -> tuple[bool, str]:
    # 1) Windows Terminal (wt) - preferred, because of modern UX + tabs.
    wt = shutil.which("wt")
    if wt:
        argv: list[str] = [wt, "new-tab", "--startingDirectory", workdir]
        if title:
            argv += ["--title", title]
        argv += [
            "pwsh.exe" if shutil.which("pwsh") else "powershell.exe",
            "-NoExit", "-NoLogo", "-Command", command,
        ]
        try:
            subprocess.Popen(  # noqa: S603
                argv,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                close_fds=True,
            )
            log.info("external terminal via wt: %s (cwd=%s)", command, workdir)
            return True, "wt"
        except Exception as exc:  # noqa: BLE001
            log.warning("wt spawn failed, falling back to pwsh: %s", exc)

    # 2) pwsh.exe / powershell.exe as a standalone window.
    for exe_name in ("pwsh", "powershell"):
        exe = shutil.which(exe_name)
        if not exe:
            continue
        try:
            subprocess.Popen(  # noqa: S603
                [exe, "-NoExit", "-NoLogo", "-Command", command],
                cwd=workdir,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
            )
            log.info("external terminal via %s: %s (cwd=%s)", exe_name, command, workdir)
            return True, exe_name
        except Exception as exc:  # noqa: BLE001
            log.warning("%s spawn failed: %s", exe_name, exc)

    log.error("No external terminal available (wt/pwsh/powershell all not found)")
    return False, "failed"


def _spawn_macos(command: str, workdir: str, title: str | None) -> tuple[bool, str]:
    """Terminal.app via AppleScript.

    ``do script`` takes ONE AppleScript string literal, so the shell line is
    embedded in it and both the backslash and the double quote have to survive
    two levels of quoting - AppleScript's, then the shell's inside it. Getting
    that wrong does not raise; it silently runs a truncated command.
    """
    del title  # Terminal.app names its window after the running command.
    shell_line = f"cd {shlex.quote(workdir)} && {command}"
    applescript_literal = shell_line.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "Terminal"\n'
        f'  activate\n'
        f'  do script "{applescript_literal}"\n'
        f"end tell"
    )
    try:
        subprocess.Popen(  # noqa: S603
            ["osascript", "-e", script],
            close_fds=True,
            start_new_session=True,
        )
        log.info("external terminal via Terminal.app: %s (cwd=%s)", command, workdir)
        return True, "terminal.app"
    except Exception as exc:  # noqa: BLE001
        log.error("osascript spawn failed: %s", exc)
        return False, "failed"


def _spawn_linux(command: str, workdir: str, title: str | None) -> tuple[bool, str]:
    if not _has_display():
        log.info("no DISPLAY/WAYLAND_DISPLAY - cannot open a terminal window")
        return False, "no_display"

    payload = _posix_keep_open(command)
    for name in _LINUX_TERMINALS:
        exe = shutil.which(name)
        if not exe:
            continue
        argv = [exe]
        if title and name in ("gnome-terminal", "konsole", "xfce4-terminal", "xterm"):
            argv += ["--title" if name != "xterm" else "-T", title]
        # gnome-terminal deprecated ``-e`` and parses ``--`` as "everything
        # after this is the argv"; the others still want ``-e``.
        argv += ["--"] if name == "gnome-terminal" else ["-e"]
        argv += payload
        try:
            subprocess.Popen(  # noqa: S603
                argv,
                cwd=workdir,
                close_fds=True,
                start_new_session=True,
            )
            log.info("external terminal via %s: %s (cwd=%s)", name, command, workdir)
            return True, name
        except Exception as exc:  # noqa: BLE001
            log.warning("%s spawn failed, trying the next terminal: %s", name, exc)

    log.error("No external terminal available (tried: %s)", ", ".join(_LINUX_TERMINALS))
    return False, "failed"


def spawn_external_terminal(
    command: str,
    *,
    cwd: Path | None = None,
    title: str | None = None,
) -> tuple[bool, str]:
    """Open an external terminal window and execute ``command`` inside it.

    Args:
        command: The full shell command (e.g. ``"firebase login"``).
        cwd: Working directory for the new terminal. ``None`` -> user home.
        title: Optional window/tab title, where the terminal supports one.

    Returns:
        ``(ok, used_method)``. ``used_method`` names the terminal that opened it
        ("wt", "pwsh", "powershell", "terminal.app", "gnome-terminal", ...), or
        ``"no_display"`` when the box has no screen, or ``"failed"`` when no
        terminal emulator is installed. ok=True on success.

    Behavior:
        - The spawned terminal lives independently of the app process (detached).
        - The window stays open after the command finishes, so its output can
          be read and follow-up commands typed.
    """
    workdir = Path(cwd) if cwd else Path(os.environ.get("USERPROFILE") or Path.home())
    workdir_str = str(workdir)

    if sys.platform == "win32":
        return _spawn_windows(command, workdir_str, title)
    if sys.platform == "darwin":
        return _spawn_macos(command, workdir_str, title)
    return _spawn_linux(command, workdir_str, title)


__all__ = ["chain_commands", "path_refresh_command", "spawn_external_terminal"]
