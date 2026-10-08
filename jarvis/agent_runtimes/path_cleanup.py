"""Remove the user-PATH entries older Jarvis builds let Hermes register.

Hermes' home maintenance mirrors its Windows installer: it stages launchers in
``<data root>\\bin`` and prepends that folder to the user's PATH
(``hermes_cli._launchers._expose_windows_user_bin``). Earlier Jarvis builds
gave every agent its own data root, so every agent folder (and every test's
temporary folder) ended up on the user's PATH, in front of the real Hermes.
The profile layout in ``hermes.py`` stops new entries; this module takes the
old ones out again, once per process, touching nothing but entries that point
into a Jarvis ``agent_runtimes`` folder.

The registry is injectable so tests never read or write the real one.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import MutableMapping
from typing import Final, Protocol

log = logging.getLogger(__name__)

#: ``...\\agent_runtimes[-<instance>]\\hermes\\<agent>\\bin`` (old per-agent
#: data roots, including tests' temporary ones) and the shared root's ``bin``.
_JARVIS_HERMES_BIN: Final[re.Pattern[str]] = re.compile(
    r"[\\/]agent_runtimes(?:-[a-z0-9-]+)?[\\/](?:hermes[\\/][^\\/]+|hermes-home)[\\/]bin[\\/]?$",
    re.IGNORECASE,
)

_done = False


class UserPathStore(Protocol):
    """Where the user's persistent PATH lives (HKCU\\Environment on Windows)."""

    def read(self) -> tuple[str, int] | None:
        """The stored value and its registry type, or ``None`` when there is none."""
        ...

    def write(self, value: str, kind: int) -> None: ...


class _WindowsUserPath:
    def read(self) -> tuple[str, int] | None:
        import winreg

        access = winreg.KEY_QUERY_VALUE
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access) as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:  # no user PATH at all: nothing to clean
                return None
        return str(value), int(kind)

    def write(self, value: str, kind: int) -> None:
        import ctypes
        import winreg

        access = winreg.KEY_SET_VALUE
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access) as key:
            winreg.SetValueEx(key, "Path", 0, kind, value)
        # WM_SETTINGCHANGE so new terminals see the cleaned PATH.
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            0xFFFF, 0x1A, 0, "Environment", 0x0002, 5000, None
        )


def is_jarvis_hermes_bin(entry: str) -> bool:
    """Whether a PATH entry is a Hermes ``bin`` folder inside Jarvis' data."""
    expanded = os.path.expandvars(entry.strip().strip('"'))
    return bool(expanded) and bool(_JARVIS_HERMES_BIN.search(expanded))


def _without(value: str, separator: str) -> tuple[str, list[str]]:
    kept: list[str] = []
    removed: list[str] = []
    for part in value.split(separator):
        (removed if part and is_jarvis_hermes_bin(part) else kept).append(part)
    return separator.join(kept), removed


def clean_user_path(
    *,
    store: UserPathStore | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Drop Jarvis' Hermes ``bin`` entries from the user PATH and this process.

    Returns the removed registry entries. Windows only (POSIX installs never
    had them: Hermes honours ``cli.expose_on_path`` there); a no-op elsewhere
    unless a ``store`` is injected.
    """
    env = os.environ if environ is None else environ
    current = env.get("PATH", "")
    if current:
        cleaned, dropped = _without(current, os.pathsep)
        if dropped:
            env["PATH"] = cleaned
    if store is None:
        if sys.platform != "win32":
            return []
        store = _WindowsUserPath()
    stored = store.read()
    if stored is None:
        return []
    value, kind = stored
    cleaned, removed = _without(value, ";")
    if removed:
        store.write(cleaned, kind)
        log.info("agent runtimes: removed %d stale Hermes PATH entries", len(removed))
    return removed


def clean_user_path_once() -> None:
    """:func:`clean_user_path` the first time a Hermes turn or setup runs."""
    global _done
    if _done:
        return
    _done = True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # A test that reaches the real driver never edits this machine's
        # registry; the cleanup itself is tested with an injected store.
        return
    try:
        clean_user_path()
    except OSError as exc:
        # The registry may be locked down by policy; Hermes still runs, the
        # stale entries only shadow the user's own ``hermes`` command.
        log.warning("agent runtimes: could not clean the user PATH: %s", exc)
