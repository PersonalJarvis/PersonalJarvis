"""Open the appshot editor in a window of its own.

A click on the corner card (or "Edit" on the Appshots page) should put the
editor in front of the user, CleanShot X style — not raise the whole app and
not change what the app shows. The desktop shell can open a detached window
for that; a headless or browser-only run cannot, and then the caller falls
back to the editor inside the app's page.

The shell registers its opener at start-up (``register_window_opener``); this
module never imports the shell, so it stays importable on a bare server.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

#: The detached view id; mirrored by ``DETACHABLE_VIEWS`` in the desktop shell
#: and by the frontend's window entry (``view=appshot-editor``).
EDITOR_VIEW = "appshot-editor"

#: Appshot ids are uuid hex; nothing else may reach the window URL.
_ID = re.compile(r"^[0-9a-f]{8,64}$")

_opener: Callable[[str], dict[str, Any]] | None = None
_prewarm: Callable[[], dict[str, Any]] | None = None


def register_window_opener(
    opener: Callable[[str], dict[str, Any]] | None,
    *,
    prewarm: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """Install (or, with ``None``, remove) the shell's window opener.

    ``opener(query)`` must open or re-point the ``appshot-editor`` window and
    may block — it is always called from a worker thread. ``prewarm()``
    creates that window hidden ahead of time, so opening it is instant.
    """
    global _opener, _prewarm
    _opener = opener
    _prewarm = prewarm if opener is not None else None


async def prewarm_editor_window() -> None:
    """Have the editor window loaded (hidden) before anyone clicks the card."""
    prewarm = _prewarm
    if prewarm is None:
        return
    try:
        await asyncio.to_thread(prewarm)
    except Exception:  # noqa: BLE001 - opening on click still works, just slower
        log.debug("appshot: the editor window could not be prepared", exc_info=True)


def can_open_window() -> bool:
    return _opener is not None


async def open_editor_window(shot_id: str) -> bool:
    """Open the editor window on ``shot_id``. ``False`` = use the page instead."""
    opener = _opener
    if opener is None or not _ID.match(shot_id or ""):
        return False
    try:
        result = await asyncio.to_thread(opener, f"appshot={shot_id}")
    except Exception:  # noqa: BLE001 - the page editor is the honest fallback
        log.warning("appshot: the editor window could not be opened", exc_info=True)
        return False
    ok = bool(isinstance(result, dict) and result.get("ok"))
    if not ok:
        log.info("appshot: no editor window (%s); using the page", (result or {}).get("reason"))
    return ok


__all__ = [
    "EDITOR_VIEW",
    "can_open_window",
    "open_editor_window",
    "prewarm_editor_window",
    "register_window_opener",
]
