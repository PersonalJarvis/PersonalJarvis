"""Stub for the lost ``CodexAuthService``.

Background: the original module was deleted locally (not in git, not in a
stash). The running process still had it in memory, but a cold start failed
with ``ModuleNotFoundError`` because ``provider_routes.py`` imports the
module at the top level.

This stub satisfies the minimal API so that app bootstrap completes:
- ``CodexAuthStatus`` (NamedTuple-like) with ``installed``, ``connected``,
  ``to_dict()``
- ``CodexAuthService`` with ``status()``, ``start_login()``, ``logout_blocking()``

The Codex CLI will then not be connected functionally; the UI indicator shows
"not installed" — the user can reactivate the feature later by restoring the
real module. Other providers are not affected.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexAuthStatus:
    """Status snapshot for the Codex CLI."""

    installed: bool = False
    connected: bool = False
    binary_path: str = ""
    user_email: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "connected": self.connected,
            "binary_path": self.binary_path,
            "user_email": self.user_email,
            "error": self.error,
        }


class CodexAuthService:
    """Minimal stub for Codex CLI auth.

    The full feature will return once the original module is restored.
    Until then: 'not installed' / 'not connected'.
    """

    def __init__(self, binary_path: str | None = None) -> None:
        self._binary_path = (binary_path or "").strip() or "codex"

    def status(self) -> CodexAuthStatus:
        """Minimally checks whether a 'codex' binary is on PATH — no auth."""
        try:
            proc = subprocess.run(
                [self._binary_path, "--version"],
                capture_output=True, timeout=2.0, text=True,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            installed = proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            installed = False
        except Exception as exc:  # noqa: BLE001
            log.debug("Codex-Status-Probe fehlgeschlagen: %s", exc)
            installed = False
        return CodexAuthStatus(
            installed=installed,
            connected=False,
            binary_path=self._binary_path,
            error=None if installed else "codex_auth-Stub aktiv",
        )

    def start_login(self) -> Any:
        raise FileNotFoundError(
            "Codex-Login ist im aktuellen Build nicht verfuegbar "
            "(jarvis.codex_auth-Stub aktiv).",
        )

    def logout_blocking(self) -> tuple[bool, str | None]:
        return False, "codex_auth-Stub aktiv — kein Logout moeglich"
