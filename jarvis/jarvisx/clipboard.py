"""Put a Jarvis X screenshot on the system clipboard (Windows, macOS, Linux).

One native implementation serves the whole app:
:mod:`jarvis.platform.clipboard_image` offers the picture in every format the
OS knows (PNG + bitmap on Windows, PNG + TIFF on macOS, ``image/png`` on
Linux), so Ctrl/Cmd+V pastes it into any app. This module only keeps the
``(ok, message)`` shape Jarvis X reports in.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def copy_png(png: bytes) -> tuple[bool, str]:
    """Replace the clipboard with this PNG image; never raises."""
    from jarvis.platform.clipboard_image import copy_image  # noqa: PLC0415

    try:
        result = copy_image(png)
    except Exception:  # noqa: BLE001 - a failed copy never fails the capture
        log.warning("jarvisx: clipboard copy failed", exc_info=True)
        return False, "The clipboard could not be reached."
    return result.ok, result.message


__all__ = ["copy_png"]
