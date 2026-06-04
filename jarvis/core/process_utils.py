"""Cross-platform helpers for spawning subprocesses without flashing console windows.

Background:
    The desktop app runs under ``pythonw.exe`` (no attached console). When a
    child process is started without explicit ``creationflags``, Windows
    allocates a fresh console window for every child — for ``npx``, ``git``,
    ``uvx`` and CLI probes that means a flicker storm of black terminals
    popping up and closing during normal startup.

    Setting ``CREATE_NO_WINDOW`` on every spawn makes children silently
    inherit no-console state. ``asyncio.create_subprocess_exec`` accepts the
    same Windows constants as ``subprocess.Popen``.

Usage:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

On non-Windows platforms ``NO_WINDOW_CREATIONFLAGS`` is ``0`` and the
parameter is silently ignored by the subprocess machinery.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

if sys.platform == "win32":
    NO_WINDOW_CREATIONFLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
else:
    NO_WINDOW_CREATIONFLAGS = 0


def resolve_executable(name: str) -> str:
    """Resolve a binary name to its full on-disk path, honoring PATHEXT.

    On Windows, many CLIs ship as ``.cmd`` / ``.bat`` / ``.ps1`` shims (gcloud,
    npm, vercel, firebase, ...). ``asyncio.create_subprocess_exec`` /
    ``subprocess`` with ``shell=False`` do NOT perform PATH + PATHEXT lookup the
    way the shell does — passing a bare ``"gcloud"`` raises
    ``FileNotFoundError`` even though ``gcloud.cmd`` is on PATH. ``shutil.which``
    DOES honor PATHEXT, so resolving the name to its full path first lets us
    exec a ``.cmd``/``.bat`` shim directly.

    Returns the resolved absolute path when found, otherwise the original name
    unchanged (so the caller still raises a clean ``FileNotFoundError`` instead
    of silently swallowing a typo).
    """
    if not name:
        return name
    resolved = shutil.which(name)
    return resolved or name


__all__ = ["NO_WINDOW_CREATIONFLAGS", "resolve_executable"]
