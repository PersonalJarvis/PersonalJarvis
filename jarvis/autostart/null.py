"""Graceful no-op autostart manager (AD-6).

Used on a headless host (no display — a €5 VPS / server: GUI login autostart is
meaningless there) and on an unknown platform. Every method is a logged no-op
that reports ``supported=False`` so the Settings UI can render an honest "not
available here" caption instead of pretending the toggle did something.
"""

from __future__ import annotations

import logging

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)


class NullAutostart:
    """No-op manager for headless / unsupported hosts."""

    def __init__(self, reason: str = "autostart-at-login not available on this host") -> None:
        self._reason = reason

    def _status(self) -> AutostartStatus:
        return AutostartStatus(
            supported=False,
            installed=False,
            matches_spec=False,
            entry_path=None,
            detail=f"Login autostart is not available: {self._reason}.",
        )

    def status(self, spec: LaunchSpec) -> AutostartStatus:  # noqa: ARG002 — interface
        return self._status()

    def install(  # noqa: ARG002 — interface
        self, spec: LaunchSpec, *, interactive: bool = False
    ) -> AutostartStatus:
        log.debug("NullAutostart.install no-op (%s)", self._reason)
        return self._status()

    def uninstall(self, *, interactive: bool = False) -> AutostartStatus:  # noqa: ARG002
        log.debug("NullAutostart.uninstall no-op (%s)", self._reason)
        return self._status()


__all__ = ["NullAutostart"]
