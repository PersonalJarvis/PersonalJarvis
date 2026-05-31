"""Resolve the canonical launch command for autostart — single source of truth.

Every OS implementation and the reconcile loop derive the same
:class:`~jarvis.autostart.protocol.LaunchSpec` from here, so they never disagree
about *which* interpreter/path Jarvis should be relaunched with at login.

The launch target is the full desktop app (``-m jarvis.ui.web.launcher``, voice +
Orb enabled — NOT ``--headless``): that is what makes "Hey Jarvis" available
after boot. The interpreter and working directory are computed at call time from
the running package, never from a stored absolute string, so a moved/re-cloned
project never leaves a stale autostart entry (BUG-006 restore-trap class).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from jarvis.core.config import PROJECT_ROOT
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

from .protocol import LaunchSpec

log = logging.getLogger(__name__)

# The module the autostart entry launches — the full voice + Orb desktop app.
LAUNCHER_MODULE = "jarvis.ui.web.launcher"


def _detect_pythonw() -> str:
    """Return ``pythonw.exe`` on Windows (GUI subsystem → no console window).

    Mirrors ``scripts/install_shortcuts.py::_detect_pythonw``: prefer a project
    ``.venv``, then ``sys.executable``'s sibling, then a PATH search. Falls back
    to ``sys.executable`` (``python.exe``) if no ``pythonw.exe`` exists — a
    visible console is ugly but strictly better than a dead autostart entry.
    """
    venv_pyw = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pyw.exists():
        return str(venv_pyw)

    sibling = Path(sys.executable).with_name("pythonw.exe")
    if sibling.exists():
        return str(sibling)

    try:
        result = subprocess.run(
            ["where", "pythonw.exe"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if first:
            return first
    except Exception as exc:  # noqa: BLE001 — PATH search is best-effort
        log.debug("pythonw.exe PATH search failed: %s", exc)

    log.warning(
        "pythonw.exe not found — autostart will use %s (a console window may flash).",
        sys.executable,
    )
    return sys.executable


def resolve_launch_spec(cfg: object | None = None) -> LaunchSpec:
    """Build the :class:`LaunchSpec` for the *current* install.

    ``cfg`` is optional; only ``cfg.autostart.start_minimized`` is read (default
    True). Everything else is derived from the running interpreter + package so
    the entry always targets the clone that is actually running.
    """
    minimized = True
    autostart = getattr(cfg, "autostart", None) if cfg is not None else None
    if autostart is not None:
        minimized = bool(getattr(autostart, "start_minimized", True))

    if sys.platform == "win32":
        program = _detect_pythonw()
    else:
        program = sys.executable

    return LaunchSpec(
        program=program,
        args=("-m", LAUNCHER_MODULE),
        working_dir=str(PROJECT_ROOT),
        minimized=minimized,
    )


__all__ = ["LAUNCHER_MODULE", "resolve_launch_spec"]
