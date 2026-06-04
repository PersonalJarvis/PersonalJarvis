"""``sys.platform`` / capability factory for the autostart port (AD-5).

The ``display_present`` gate makes the headless €5-VPS / server case a no-op
without duplicating any platform check — login autostart is meaningless without
a GUI login session, so a host with no display gets the :class:`NullAutostart`
regardless of OS family.
"""

from __future__ import annotations

import logging

from jarvis.platform.capabilities import Capabilities

from .protocol import AutostartManager

log = logging.getLogger(__name__)


def make_autostart_manager(caps: Capabilities) -> AutostartManager:
    """Return the right per-OS autostart manager, or a graceful null-fallback."""
    if not caps.display_present:
        from .null import NullAutostart

        return NullAutostart(reason="no display (headless host)")

    if caps.platform == "win32":
        from .windows import WindowsAutostart

        return WindowsAutostart()
    if caps.platform == "darwin":
        from .macos import MacOSAutostart

        return MacOSAutostart()
    if caps.platform == "linux":
        from .linux import LinuxAutostart

        return LinuxAutostart()

    from .null import NullAutostart

    return NullAutostart(reason=f"unsupported platform {caps.platform!r}")


__all__ = ["make_autostart_manager"]
