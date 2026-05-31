"""Shared cross-platform capability layer (AD-5).

`detect_platform()` is the single source of truth for which OS family Jarvis is
running on. The six platform ports (terminal, app-launch, hotkey, ui-tree,
overlay, elevation) read their platform decision and capability flags from here
instead of each re-detecting ``sys.platform`` (which guarantees drift — see the
BUG-008 multi-layer-enum-drift class). See ``capabilities.py`` for the cached
``Capabilities`` snapshot and ``probes.py`` for the individual feature probes.

Import-cleanliness contract (HN-7): nothing in this package imports a
platform-only package (``winreg``/``win32*``/``pyobjc``/``pyatspi``/``pynput``/
``ptyprocess``) at module scope. Every such import is lazy + guarded inside a
function body, mirroring ``jarvis/plugins/tool/app_resolver.py:24``.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

log = logging.getLogger(__name__)

PlatformName = Literal["win32", "darwin", "linux"]


def detect_platform() -> PlatformName:
    """Return the canonical OS family.

    Maps ``sys.platform`` to one of ``"win32"``, ``"darwin"``, ``"linux"``.
    Never raises (AD-6): an unrecognized platform logs a one-line warning and
    falls back to the POSIX-shaped ``"linux"`` default, since every non-Windows,
    non-macOS target Jarvis is designed for is a POSIX/Linux-family host.
    """
    plat = sys.platform
    if plat == "win32":
        return "win32"
    if plat == "darwin":
        return "darwin"
    if plat.startswith("linux"):
        return "linux"
    log.warning(
        "Unknown sys.platform %r — defaulting to POSIX/'linux' capability shape.",
        plat,
    )
    return "linux"


__all__ = ["PlatformName", "detect_platform"]
