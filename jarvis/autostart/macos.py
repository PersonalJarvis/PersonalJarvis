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

from jarvis.core.branding import MACOS_APP_DIR_NAME as _APP_DIR_NAME
from jarvis.core.branding import MACOS_AUTOSTART_LABEL as _LABEL

from .protocol import AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)

_ENTRY_NAME = f"{_LABEL}.plist"


def _agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _program_arguments(spec: LaunchSpec) -> list[str]:
    return [spec.program, *spec.args]


def _launchctl(*argv: str) -> bool:
    """Best-effort ``launchctl`` call — darwin-only, never raises.

    Returns ``True`` when launchctl exited 0 (or when this is not darwin, where
    the call is deliberately skipped). A non-zero exit used to be discarded
    together with its stderr, so a failure like ``Load failed: 5: Input/output
    error`` left NO trace at all while ``install`` still reported "Autostart
    enabled and current" — the user saw a green toggle and no diagnosis for a
    LaunchAgent that never armed in the running session (AP-30).
    """
    if sys.platform != "darwin":
        return True
    try:
        proc = subprocess.run(
            ["launchctl", *argv],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 — live arming is a nicety, RunAtLoad covers next login
        log.debug("launchctl %s failed (non-fatal): %s", " ".join(argv), exc)
        return False
    if proc.returncode != 0:
        log.debug(
            "launchctl %s exited %d: %s",
            " ".join(argv),
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip(),
        )
        return False
    return True


def _write_plist_atomically(path: Path, plist: dict) -> None:
    tmp = path.with_suffix(".plist.tmp")
    try:
        with tmp.open("wb") as fh:
            plistlib.dump(plist, fh)
        tmp.replace(path)
    except BaseException:
        # A half-written .plist.tmp left next to the real entry in
        # ~/Library/LaunchAgents is confusing at best; the successful
        # replace() consumes the temp file, so anything still there is
        # debris from a failed write.
        tmp.unlink(missing_ok=True)
        raise


def retarget_launch_agent(bundle: Path) -> bool:
    """Point an existing LaunchAgent at ``bundle`` after the app moved.

    The entry names the app by absolute path. Whoever moves the bundle has to
    move the entry with it IN THE SAME STEP: the boot-time reconcile only runs
    once the app is up, and an entry aimed at the old path is exactly what
    keeps it from coming up at login — the app would stay gone from login
    until the user happened to start it by hand.

    Only ever edits an entry that already exists, so a user who switched
    autostart off keeps it off. Returns ``True`` when the entry was rewritten;
    never raises.
    """
    path = _agents_dir() / _ENTRY_NAME
    try:
        if not path.is_file():
            return False
        with path.open("rb") as fh:
            plist = plistlib.load(fh)
        arguments = plist.get("ProgramArguments")
        if not isinstance(arguments, list):
            return False
        target = str(bundle)
        updated = [
            target
            if isinstance(arg, str) and Path(arg).name == _APP_DIR_NAME and arg != target
            else arg
            for arg in arguments
        ]
        if updated == arguments:
            return False
        plist["ProgramArguments"] = updated
        _write_plist_atomically(path, plist)
    except Exception as exc:  # noqa: BLE001 - the boot reconcile is the second line of defence
        log.warning("Could not point the LaunchAgent at %s: %s", bundle, exc)
        return False
    # Deliberately no ``launchctl load`` here: RunAtLoad would start the app on
    # the spot, in the middle of the installer run that moved it. launchd reads
    # the file again at the next login, which is the only moment it matters.
    log.info("macOS LaunchAgent now starts the app from %s", bundle)
    return True


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
        _write_plist_atomically(self._path, plist)
        log.info("macOS LaunchAgent written: %s", self._path)
        # Re-arm in the current session so it also works before the next login.
        # unload WITHOUT -w (a plain refresh must not write Disabled=true into
        # launchd's override database); load WITH -w to clear a Disabled flag a
        # previous uninstall set.
        _launchctl("unload", str(self._path))
        if not _launchctl("load", "-w", str(self._path)):
            log.info(
                "macOS LaunchAgent written but launchctl could not arm it in this "
                "session; RunAtLoad still starts Jarvis at the next login.",
            )
        return self.status(spec)

    def uninstall(self, *, interactive: bool = False) -> AutostartStatus:  # noqa: ARG002
        _launchctl("unload", "-w", str(self._path))
        removed = True
        error = ""
        if self._path.exists():
            try:
                self._path.unlink()
                log.info("macOS LaunchAgent removed: %s", self._path)
            except OSError as exc:
                log.warning("Could not remove %s: %s", self._path, exc)
                removed = False
                error = str(exc)
        if not removed:
            # Reporting "Autostart disabled." while the plist survives was a
            # lie: RunAtLoad still starts Jarvis at the next login, and the
            # Settings toggle showed off with nothing to explain the mismatch.
            return AutostartStatus(
                supported=True,
                installed=True,
                matches_spec=False,
                entry_path=str(self._path),
                detail=(
                    f"The LaunchAgent could not be removed ({error}) — Jarvis may "
                    "still start at login; delete the file manually and retry."
                ),
            )
        return AutostartStatus(
            supported=True,
            installed=False,
            matches_spec=False,
            entry_path=str(self._path),
            detail="Autostart disabled.",
        )


__all__ = ["MacOSAutostart", "retarget_launch_agent"]
