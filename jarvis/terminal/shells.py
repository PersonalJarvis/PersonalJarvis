"""Discovery of the shells available on this system.

On Windows we detect 4 shell types and verify their executable path at
startup. On macOS/Linux we discover the installed POSIX shells (``$SHELL`` ->
``/etc/shells`` -> ``which bash/zsh/fish``). Only shells that are actually
installed land in the UI dropdown — that avoids "command not found" errors on
spawn. ``discover_shells()`` dispatches on ``detect_platform()`` (AD-6/AD-9);
the four Windows factories stay untouched (AD-7 additive coexistence).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from jarvis.platform import detect_platform


@dataclass(frozen=True, slots=True)
class ShellInfo:
    """Describes one discovered shell installation."""

    id: str            # stable key ("pwsh", "powershell", "cmd", "bash")
    label: str         # user-facing label for the dropdown
    argv: tuple[str, ...]  # full argv list for spawn


def _powershell_7() -> ShellInfo | None:
    # pwsh.exe on PATH (default install of PowerShell 7+)
    found = shutil.which("pwsh")
    if found:
        return ShellInfo(id="pwsh", label="PowerShell 7", argv=(found, "-NoLogo"))
    # Fallback: known install path
    candidate = os.path.expandvars(r"%ProgramFiles%\PowerShell\7\pwsh.exe")
    if os.path.isfile(candidate):
        return ShellInfo(id="pwsh", label="PowerShell 7", argv=(candidate, "-NoLogo"))
    return None


def _windows_powershell() -> ShellInfo | None:
    candidate = os.path.expandvars(
        r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    if os.path.isfile(candidate):
        return ShellInfo(
            id="powershell",
            label="Windows PowerShell 5.1",
            argv=(candidate, "-NoLogo"),
        )
    return None


def _cmd() -> ShellInfo | None:
    candidate = os.path.expandvars(r"%SystemRoot%\System32\cmd.exe")
    if os.path.isfile(candidate):
        return ShellInfo(id="cmd", label="CMD", argv=(candidate,))
    return None


def _git_bash() -> ShellInfo | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Git\bin\bash.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Git\bin\bash.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            # -i = interactive, -l = login (loads .bashrc/.profile)
            return ShellInfo(id="bash", label="Git Bash", argv=(cand, "-i", "-l"))
    found = shutil.which("bash")
    if found and "git" in found.lower():
        return ShellInfo(id="bash", label="Git Bash", argv=(found, "-i", "-l"))
    return None


def _unix_shells() -> list[ShellInfo]:
    """Discover the installed POSIX shells in preference order (AD-9).

    Order:
      1. ``$SHELL`` if set and present on disk (the user's login shell).
      2. Each path listed in ``/etc/shells`` that exists on disk.
      3. ``shutil.which("bash"/"zsh"/"fish")`` as a final fallback.

    Shells are deduplicated by their resolved (``os.path.realpath``) path so a
    symlinked ``/bin/sh -> bash`` or a ``$SHELL`` that is also in ``/etc/shells``
    is listed once. ``argv`` is ``(path, "-i")`` (interactive) — never an
    unconditional ``-l`` (a login shell on every spawn re-sources profiles and
    is slow).
    """
    results: list[ShellInfo] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if not path or not os.path.isfile(path):
            return
        resolved = os.path.realpath(path)
        if resolved in seen:
            return
        seen.add(resolved)
        label = os.path.basename(path)
        results.append(ShellInfo(id=label, label=label, argv=(path, "-i")))

    # 1. $SHELL (the user's configured login shell).
    _add(os.environ.get("SHELL", ""))

    # 2. /etc/shells — the system registry of valid login shells.
    try:
        with open("/etc/shells", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                _add(line)
    except OSError:
        # No /etc/shells (some minimal containers) — skip to the which() probe.
        pass

    # 3. which() fallback for the common interactive shells.
    for name in ("bash", "zsh", "fish"):
        found = shutil.which(name)
        if found:
            _add(found)

    return results


def discover_shells() -> list[ShellInfo]:
    """Return every shell installed on this system in preference order.

    Dispatches on ``detect_platform()`` (AD-6): Windows iterates the four
    untouched Windows factories (AD-7); macOS/Linux return ``_unix_shells()``.
    """
    if detect_platform() == "win32":
        results: list[ShellInfo] = []
        for factory in (_powershell_7, _windows_powershell, _cmd, _git_bash):
            info = factory()
            if info is not None:
                results.append(info)
        return results
    return _unix_shells()


def get_shell(shell_id: str) -> ShellInfo | None:
    """Looks up a shell by ID — None if not installed."""
    for shell in discover_shells():
        if shell.id == shell_id:
            return shell
    return None
