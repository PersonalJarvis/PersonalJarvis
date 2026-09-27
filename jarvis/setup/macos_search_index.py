"""Spotlight indexing for the managed macOS app bundle.

LaunchServices and Spotlight are two separate databases. ``lsregister`` makes
``open -a`` and the Dock resolve the bundle, but the Spotlight search field
answers from the metadata store that ``mds`` keeps per volume — an app can be
fully registered with LaunchServices and still be missing from Spotlight.
The bundle is built in a hidden ``.jarvis-native-*`` directory and renamed
into ``~/Applications``, and that import is left to the FSEvents stream; this
module asks for it explicitly with ``mdimport``.

It also names the cases no app can repair, each only on evidence macOS gives:
indexing switched off for the volume (``mdutil -s``), or a stalled index that
accepts an import request and still never lists the bundle
(``wait_until_indexed``). Both come with the exact admin command. Every probe
is bounded, macOS-only and never raises.

``mdutil`` is asked about the volume that CONTROLS indexing. Since macOS 10.15
the user data lives on ``/System/Volumes/Data``, firmlinked under ``/``; that
volume answers ``mdutil -s`` with "unknown indexing state" and refuses
``mdutil -i`` with error -405 even on a healthy Mac, because the system volume
group is indexed and administered through ``/``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.branding import MACOS_BUNDLE_ID
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_MDIMPORT = "/usr/bin/mdimport"
_MDUTIL = "/usr/bin/mdutil"
_MDFIND = "/usr/bin/mdfind"
_DF = "/bin/df"
_TIMEOUT_S = 30
# The data half of the APFS system volume group; indexing is controlled at "/".
_SYSTEM_DATA_VOLUME = "/System/Volumes/Data"


@dataclass(frozen=True)
class SpotlightVolumeIssue:
    """A volume whose Spotlight store will not index a new app."""

    volume: str
    reason: str

    @property
    def repair_command(self) -> str:
        return f"sudo mdutil -i on {self.volume} && sudo mdutil -E {self.volume}"


# A stalled index reports "Indexing enabled" and simply stops absorbing
# changes; erasing the store makes mds rebuild it from scratch.
STALLED_INDEX_REPAIR_COMMAND = "sudo mdutil -E /"


def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    if sys.platform != "darwin" or not Path(argv[0]).is_file():
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed system path, no shell
            argv,
            timeout=_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("%s did not run: %s", Path(argv[0]).name, exc)
        return None


def request_spotlight_import(bundle: Path) -> bool:
    """Ask Spotlight to index ``bundle`` now instead of waiting for FSEvents.

    Idempotent; ``True`` only when ``mdimport`` accepted the request.
    """
    result = _run([_MDIMPORT, str(bundle)])
    if result is None:
        return False
    if result.returncode != 0:
        log.debug(
            "mdimport failed (rc=%s): %s", result.returncode, (result.stderr or "").strip()[:300]
        )
        return False
    return True


def parse_mdutil_status(output: str) -> SpotlightVolumeIssue | None:
    """Read ``mdutil -s <path>`` output; ``None`` means indexing is enabled.

    Output we do not recognise is treated as healthy — a warning must rest on
    a state macOS actually reported, never on a guess.
    """
    volume = ""
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not raw[:1].isspace() and line.endswith(":"):
            volume = line[:-1]
            continue
        lowered = line.lower()
        if "indexing enabled" in lowered:
            return None
        if "indexing disabled" in lowered:
            return SpotlightVolumeIssue(volume or "/", "Spotlight indexing is turned off")
    # "unknown indexing state" is what a healthy /System/Volumes/Data answers
    # too, so it proves nothing on its own.
    return None


def parse_df_mount_point(output: str) -> str | None:
    """The "Mounted on" column of ``df -P <path>`` output."""
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    # Filesystem, blocks, used, available, capacity, then the mount point,
    # which may itself contain spaces.
    fields = lines[1].split(None, 5)
    return fields[5].strip() if len(fields) == 6 else None


def indexing_control_volume(mount_point: str) -> str:
    """The volume whose ``mdutil`` state governs ``mount_point``."""
    return "/" if mount_point == _SYSTEM_DATA_VOLUME else mount_point


def _mount_point(path: Path) -> str | None:
    result = _run([_DF, "-P", str(path)])
    if result is None or result.returncode != 0:
        return None
    return parse_df_mount_point(result.stdout or "")


def spotlight_volume_issue(path: Path) -> SpotlightVolumeIssue | None:
    """Whether the volume holding ``path`` can index new items at all.

    ``mdutil -s`` must be given a mount point: handed any other path it echoes
    that path back, and the repair command would then name a folder.
    """
    mount = _mount_point(path)
    if mount is None:
        return None
    volume = indexing_control_volume(mount)
    result = _run([_MDUTIL, "-s", volume])
    if result is None:
        return None
    return parse_mdutil_status(f"{result.stdout or ''}\n{result.stderr or ''}")


def indexed_bundle_paths(bundle_id: str = MACOS_BUNDLE_ID) -> list[Path] | None:
    """Every app Spotlight has indexed under ``bundle_id``; ``None`` if unknown."""
    result = _run([_MDFIND, f"kMDItemCFBundleIdentifier == '{bundle_id}'"])
    if result is None or result.returncode != 0:
        return None
    return [
        Path(line.strip())
        for line in (result.stdout or "").splitlines()
        if line.strip().endswith(".app")
        # Build and rollback directories are hidden; a hit there is not what
        # the user's search shows.
        and not any(part.startswith(".") for part in Path(line.strip()).parts)
    ]


def wait_until_indexed(
    bundle: Path,
    *,
    timeout_s: float = 20.0,
    poll_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool | None:
    """Request an import of ``bundle`` and wait for Spotlight to list it.

    ``True`` once it is indexed, ``False`` when Spotlight accepted the request
    and still did not list the bundle in time — the observable shape of a
    stalled index — and ``None`` when Spotlight could not be asked at all.
    """
    if not request_spotlight_import(bundle):
        return None
    deadline = clock() + timeout_s
    while True:
        indexed = indexed_bundle_paths()
        if indexed is None:
            return None
        if bundle in indexed:
            return True
        if clock() >= deadline:
            return False
        sleep(poll_s)


def announce_to_spotlight(bundle: Path) -> bool:
    """Import ``bundle`` into Spotlight and warn when its volume cannot index.

    Returns ``True`` when the import request was accepted.
    """
    imported = request_spotlight_import(bundle)
    issue = spotlight_volume_issue(bundle)
    if issue is not None:
        log.warning(
            "%s is installed but Spotlight cannot find it: %s on %s. "
            "macOS indexes nothing new there until an administrator runs: %s",
            bundle.name,
            issue.reason,
            issue.volume,
            issue.repair_command,
        )
    return imported


__all__ = [
    "STALLED_INDEX_REPAIR_COMMAND",
    "SpotlightVolumeIssue",
    "announce_to_spotlight",
    "indexed_bundle_paths",
    "indexing_control_volume",
    "parse_df_mount_point",
    "parse_mdutil_status",
    "request_spotlight_import",
    "spotlight_volume_issue",
    "wait_until_indexed",
]
