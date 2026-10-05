"""Delete old captures automatically — ``[appshot].keep_newest``.

The gallery on the Appshots page holds two kinds of capture: screenshots (the
library, :mod:`jarvis.appshot.library`) and screen recordings
(:func:`jarvis.appshot.recording.recording_dir`). With ``keep_newest`` set to
``N > 0`` only the newest ``N`` screenshots AND the newest ``N`` recordings
stay; older ones are deleted. ``0`` keeps everything (the library's own
:data:`~jarvis.appshot.library.MAX_ENTRIES` cap still applies to screenshots).

It runs after a new screenshot or recording was kept and right after the
setting changes. Every function here never raises: a capture that could not
be deleted stays and goes the next time.

Nothing here is imported at boot (AP-26).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def keep_newest(config: Any) -> int:
    """``[appshot].keep_newest`` as a non-negative int; ``0`` = keep everything."""
    value = getattr(getattr(config, "appshot", None), "keep_newest", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def prune_recordings(keep: int) -> int:
    """Delete every finished recording but the newest ``keep``. Returns the count."""
    if keep <= 0:
        return 0
    from jarvis.appshot import recording  # noqa: PLC0415

    # Only finished recordings with a valid id: a recording still being
    # written is a ``.partial`` file, and foreign files in the folder stay.
    dated = []
    for path in recording.recording_dir().glob("*.mp4"):
        if recording.recording_file(path.stem) is None:
            continue
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue  # Gone while listing: nothing left to delete.
    dated.sort(reverse=True)
    removed = 0
    for _mtime, path in dated[keep:]:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue  # Deleted in the meantime — the goal is reached.
        except OSError:
            # Open in a player, for instance; the next trim tries again.
            log.warning("appshot: could not delete an old recording", exc_info=True)
    return removed


def apply(keep: int) -> int:
    """Trim screenshots and recordings to the newest ``keep`` each. Never raises."""
    if keep <= 0:
        return 0
    removed = 0
    try:
        from jarvis.appshot import library  # noqa: PLC0415

        removed += library.prune(keep)
    except Exception:  # noqa: BLE001 - a full gallery is no reason to fail a capture
        log.warning("appshot: could not trim old screenshots", exc_info=True)
    try:
        removed += prune_recordings(keep)
    except Exception:  # noqa: BLE001 - same: the next capture tries again
        log.warning("appshot: could not trim old recordings", exc_info=True)
    if removed:
        log.info("appshot: deleted %d old capture(s), keeping the newest %d", removed, keep)
    return removed


__all__ = ["apply", "keep_newest", "prune_recordings"]
