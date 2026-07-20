"""macOS login autostart via a per-user LaunchAgent.

Writes ``~/Library/LaunchAgents/com.personal-jarvis.autostart.plist`` with
``RunAtLoad=true``. The canonical launch specification uses ``/usr/bin/open``
to enter through ``Personal Jarvis.app`` and preserve its TCC identity. A
**LaunchAgent (per-user), not a LaunchDaemon** — the agent
runs inside the user's GUI session so it keeps microphone access (a Daemon runs
as a non-interactive system context with no mic/seat, the macOS analogue of the
"no Windows Service" rule, AP-17).

The plist write is pure stdlib ``plistlib`` (CI-provable on any OS via a temp
HOME). ``launchctl load/unload`` is best-effort and gated to ``darwin`` so the
writer is unit-testable cross-platform; correctness rests on the plist +
``RunAtLoad`` at the next login, not on the live ``launchctl`` call.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
from pathlib import Path

from jarvis.core.branding import MACOS_AUTOSTART_LABEL as _LABEL

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

_ENTRY_NAME = f"{_LABEL}.plist"


def _agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _program_arguments(spec: LaunchSpec) -> list[str]:
    return [spec.program, *spec.args]


def _launchctl(*argv: str) -> None:
    """Best-effort ``launchctl`` call — darwin-only, never raises."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["launchctl", *argv],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 — live arming is a nicety, RunAtLoad covers next login
        log.debug("launchctl %s failed (non-fatal): %s", " ".join(argv), exc)


class MacOSAutostart:
    """LaunchAgent plist autostart manager."""

    def __init__(self) -> None:
        self._path = _agents_dir() / _ENTRY_NAME

    def status(self, spec: LaunchSpec) -> AutostartStatus:
        if not self._path.exists():
            return AutostartStatus(
                supported=True,
                installed=False,
                matches_spec=False,
                entry_path=str(self._path),
                detail="No LaunchAgent yet.",
            )
        try:
            with self._path.open("rb") as fh:
                data = plistlib.load(fh)
        except Exception as exc:  # noqa: BLE001 — corrupt plist → treat as drift
            log.warning("Could not parse %s: %s", self._path, exc)
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=False,
                entry_path=str(self._path),
                detail=f"LaunchAgent present but unparsable: {exc}.",
            )
        matches = (
            data.get("ProgramArguments") == _program_arguments(spec)
            and data.get("WorkingDirectory") == spec.working_dir
            and data.get("RunAtLoad") is True
            and data.get("ProcessType") == "Interactive"
            and data.get("LimitLoadToSessionType") == "Aqua"
        )
        return AutostartStatus(
            supported=True,
            installed=True,
            matches_spec=matches,
            entry_path=str(self._path),
            detail=(
                "Autostart enabled and current."
                if matches
                else "LaunchAgent points at a different install (will be refreshed)."
            ),
        )

    def install(  # noqa: ARG002 — per-user LaunchAgent never needs elevation
        self, spec: LaunchSpec, *, interactive: bool = False
    ) -> AutostartStatus:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": _LABEL,
            "ProgramArguments": _program_arguments(spec),
            "WorkingDirectory": spec.working_dir,
            "RunAtLoad": True,
            "ProcessType": "Interactive",
            # Keep voice and Computer-Use inside the signed-in GUI session.
            "LimitLoadToSessionType": "Aqua",
        }
        tmp = self._path.with_suffix(".plist.tmp")
        with tmp.open("wb") as fh:
            plistlib.dump(plist, fh)
        tmp.replace(self._path)
        log.info("macOS LaunchAgent written: %s", self._path)
        # Re-arm in the current session so it also works before the next login.
        _launchctl("unload", str(self._path))
        _launchctl("load", "-w", str(self._path))
        return self.status(spec)

    def uninstall(self, *, interactive: bool = False) -> AutostartStatus:  # noqa: ARG002
        _launchctl("unload", "-w", str(self._path))
        if self._path.exists():
            try:
                self._path.unlink()
                log.info("macOS LaunchAgent removed: %s", self._path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", self._path, exc)
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=str(self._path),
            detail="Autostart disabled.",
        )


__all__ = ["MacOSAutostart"]
