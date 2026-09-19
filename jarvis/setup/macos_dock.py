"""Keep the managed macOS app in the Dock — once, and only once.

A Mac user looks for an app in three places: the Applications folder,
Spotlight, and the Dock. The first is a file, the second an index macOS owns;
the Dock is a preference list, and an installer that never touches it leaves a
freshly installed app reachable only by someone who already knows where it is.

The tile is added exactly once per install, recorded by a marker: a user who
drags the app out of the Dock has made a decision, and no update or repair may
undo it. Uninstall takes the tile back out, so no "?" tile is left pointing at
a deleted app. Everything here is best-effort, macOS-only and never raises.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from jarvis.core.branding import MACOS_APP_DIR_NAME, MACOS_BUNDLE_ID
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_DEFAULTS = "/usr/bin/defaults"
_KILLALL = "/usr/bin/killall"
_DOCK_DOMAIN = "com.apple.dock"
_MARKER_FILENAME = "macos-dock-pinned"
_TIMEOUT_S = 30


def _marker_path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / _MARKER_FILENAME


def _run(argv: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    if sys.platform != "darwin" or not Path(argv[0]).is_file():
        return None
    try:
        return subprocess.run(  # noqa: S603 - fixed system path, no shell
            argv,
            timeout=_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("%s did not run: %s", Path(argv[0]).name, exc)
        return None


def _persistent_apps() -> list[dict] | None:
    """The Dock's app tiles, or ``None`` when the preferences are unreadable."""
    result = _run([_DEFAULTS, "export", _DOCK_DOMAIN, "-"])
    if result is None or result.returncode != 0:
        return None
    try:
        tiles = plistlib.loads(result.stdout).get("persistent-apps", [])
    except (ValueError, plistlib.InvalidFileException) as exc:
        log.debug("Dock preferences are unreadable: %s", exc)
        return None
    return [tile for tile in tiles if isinstance(tile, dict)]


def is_our_tile(tile: dict) -> bool:
    """Whether a Dock tile is the managed app, wherever it points.

    Matches the bundle id the Dock recorded, or — for a tile written before
    the Dock resolved it — the bundle directory name at the end of its URL.
    """
    data = tile.get("tile-data")
    if not isinstance(data, dict):
        return False
    if data.get("bundle-identifier") == MACOS_BUNDLE_ID:
        return True
    file_data = data.get("file-data")
    url = file_data.get("_CFURLString") if isinstance(file_data, dict) else None
    if not isinstance(url, str):
        return False
    path = unquote(urlparse(url).path if "://" in url else url).rstrip("/")
    return Path(path).name == MACOS_APP_DIR_NAME and "bundle-identifier" not in data


def _tile_for(bundle: Path) -> dict:
    # No "bundle-identifier": the Dock fills that in itself when it resolves
    # the URL, and silently drops a hand-written tile that claims one without
    # the bookmark data that normally comes with it (seen live, macOS 15).
    return {
        "tile-type": "file-tile",
        "tile-data": {
            "file-label": bundle.stem,
            "file-type": 41,
            "file-data": {
                "_CFURLString": bundle.as_uri() + "/",
                "_CFURLStringType": 15,
            },
        },
    }


def _xml_fragment(value: dict) -> str:
    """``defaults write -array`` takes one XML plist fragment per element."""
    document = plistlib.dumps(value, fmt=plistlib.FMT_XML).decode("utf-8")
    start = document.index("<dict>")
    return document[start : document.rindex("</dict>") + len("</dict>")]


def _restart_dock() -> None:
    # The Dock only reads its preferences at launch; launchd restarts it at once.
    _run([_KILLALL, "Dock"])


def pin_to_dock_once(bundle: Path) -> bool:
    """Add ``bundle`` to the Dock unless this install already did so once.

    Returns ``True`` only when a tile was added now.
    """
    marker = _marker_path()
    if marker.exists():
        return False
    tiles = _persistent_apps()
    if tiles is None:
        return False
    added = False
    if not any(is_our_tile(tile) for tile in tiles):
        result = _run(
            [
                _DEFAULTS,
                "write",
                _DOCK_DOMAIN,
                "persistent-apps",
                "-array-add",
                _xml_fragment(_tile_for(bundle)),
            ]
        )
        if result is None or result.returncode != 0:
            log.debug("The Dock tile could not be written.")
            return False
        _restart_dock()
        added = True
        log.info("Added %s to the Dock.", bundle.name)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1\n", encoding="utf-8")
    except OSError as exc:
        # Without the marker the next installer run looks again; it finds the
        # tile and adds nothing, so the cost is one more look.
        log.debug("Could not record the Dock pin: %s", exc)
    return added


def remove_from_dock() -> bool:
    """Take the managed app's tile out of the Dock; ``True`` when one was removed."""
    _marker_path().unlink(missing_ok=True)
    tiles = _persistent_apps()
    if tiles is None:
        return False
    kept = [tile for tile in tiles if not is_our_tile(tile)]
    if len(kept) == len(tiles):
        return False
    result = _run(
        [_DEFAULTS, "write", _DOCK_DOMAIN, "persistent-apps", "-array"]
        + [_xml_fragment(tile) for tile in kept]
    )
    if result is None or result.returncode != 0:
        log.debug("The Dock tile could not be removed.")
        return False
    _restart_dock()
    log.info("Removed the app from the Dock.")
    return True


__all__ = ["is_our_tile", "pin_to_dock_once", "remove_from_dock"]
