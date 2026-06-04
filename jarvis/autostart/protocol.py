"""Autostart-at-login seam — protocol + wire types (the 7th cross-platform port).

Login autostart follows the established "six ports" pattern (CLAUDE.md →
*Cross-platform desktop features*): one ``Protocol`` + one per-OS implementation
+ a capability factory + a graceful logged null-fallback (AD-5/AD-6).

Nothing in this package imports a platform-only module at module scope (HN-7);
the Windows implementation shells out to PowerShell (subprocess), exactly like
``scripts/install_shortcuts.py`` does today, so there is no ``pywin32`` import and
no new dependency in any extras group. macOS/Linux are pure ``pathlib`` writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """How the OS should (re)launch Jarvis at login.

    Resolved fresh at every boot from the *running* package
    (``jarvis.__file__``) by :func:`jarvis.autostart.command.resolve_launch_spec`,
    never read from a stored absolute string. That is what lets the reconcile
    loop notice "the autostart entry points at an old clone" (path drift, the
    BUG-006 restore-trap class) and self-heal it.
    """

    program: str               # absolute interpreter path (pythonw.exe / python3)
    args: tuple[str, ...]       # ("-m", "jarvis.ui.web.launcher")
    working_dir: str            # PROJECT_ROOT
    minimized: bool = True      # Windows WindowStyle hint; other OSes ignore it

    def command_line(self) -> str:
        """Human-readable command for the Settings UI / diagnostics."""
        parts = [self.program, *self.args]
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class AutostartStatus:
    """Snapshot of the host's autostart state for the current install.

    ``supported`` — does this OS/seat support GUI login autostart at all
    (False on a headless server with no display, and on unknown platforms).
    ``installed`` — is an autostart entry present right now.
    ``matches_spec`` — does the present entry point at the *current* install
    (False = path drift, the reconcile loop will rewrite it).
    ``entry_path`` — where the entry lives (diagnostics; None when unsupported).
    ``detail`` — one human-readable English status line.
    """

    supported: bool
    installed: bool
    matches_spec: bool
    entry_path: str | None
    detail: str


@runtime_checkable
class AutostartManager(Protocol):
    """Per-OS login-autostart manager.

    ``install`` and ``uninstall`` are idempotent: applying an already-applied
    state (or removing an absent one) is a no-op that returns the resulting
    status, never an error. They raise only on a genuine I/O failure; callers
    (the reconcile loop + the Settings route) wrap them in try/except so a
    failure never blocks boot or the HTTP response.
    """

    def status(self, spec: LaunchSpec) -> AutostartStatus:
        """Report the current entry state vs. the desired ``spec``."""
        ...

    def install(self, spec: LaunchSpec) -> AutostartStatus:
        """Create/refresh the autostart entry so it matches ``spec``."""
        ...

    def uninstall(self) -> AutostartStatus:
        """Remove the autostart entry (and legacy entries, where applicable)."""
        ...


__all__ = ["LaunchSpec", "AutostartStatus", "AutostartManager"]
