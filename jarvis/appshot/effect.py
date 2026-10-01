"""The shutter effect: flash, then the picture flies into the corner.

Installed as the Screen Context shutter hook, so it fires at the true capture
moment for every look the shared service takes — a shortcut appshot, a spoken
"take an appshot", a "what do you see?". The thumbnail is cut from the raw
frame in memory and piped to the local indicator sidecar; it is never stored
and never published on the event bus.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Longest edge of the thumbnail sent to the sidecar. The card is painted at
#: ~250 logical px, so this stays sharp on a 2x display and small on the pipe.
_THUMB_EDGE = 560

_tasks: set[asyncio.Task[None]] = set()


def on_shutter(target: Any, size: tuple[int, int], rgb: bytes, monitors: list[dict]) -> None:
    """Screen Context shutter hook — synchronous, schedules the slow part."""
    from jarvis.cu.indicator.controller import get_indicator_controller  # noqa: PLC0415

    controller = get_indicator_controller()
    if controller is None:
        return
    # Before the capture's own border dismissal can quit the sidecar.
    controller.hold_for_snap()
    task = asyncio.get_running_loop().create_task(
        _play(controller, tuple(target.bbox), size, rgb, monitors),
        name="appshot-effect",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _play(
    controller: Any,
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
    rgb: bytes,
    monitors: list[dict],
) -> None:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        config = await asyncio.to_thread(load_config)
        if not bool(getattr(getattr(config, "appshot", None), "effect", True)):
            return
        thumb = await asyncio.to_thread(thumbnail_jpeg, size, rgb)
        monitor, rect = placement(bbox, monitors)
        shown = await controller.snap(
            monitor=monitor,
            rect=rect,
            thumb_b64=base64.b64encode(thumb).decode("ascii"),
        )
        if not shown:
            log.info("appshot: shutter effect could not be shown on this desktop")
    except Exception:  # noqa: BLE001 - the effect is decoration, never a failure
        log.warning("appshot: shutter effect failed", exc_info=True)


def thumbnail_jpeg(size: tuple[int, int], rgb: bytes) -> bytes:
    """A small JPEG of the raw frame, for the local effect only."""
    from PIL import Image  # noqa: PLC0415

    image = Image.frombytes("RGB", size, rgb)
    image.thumbnail((_THUMB_EDGE, _THUMB_EDGE), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


def placement(
    bbox: tuple[int, int, int, int], monitors: list[dict]
) -> tuple[list[int], list[float]]:
    """The monitor holding ``bbox`` and ``bbox`` as fractions of it.

    Fractions keep the sidecar independent of the capture's pixel units
    (physical on Windows, points on macOS). ``monitors`` is mss-shaped:
    ``[0]`` is the virtual desktop, ``[1:]`` the physical screens.
    """
    left, top, width, height = bbox
    cx, cy = left + width / 2.0, top + height / 2.0
    screens = [m for m in monitors[1:] if isinstance(m, dict)] or [
        m for m in monitors[:1] if isinstance(m, dict)
    ]
    chosen = None
    for mon in screens:
        ml, mt = mon.get("left", 0), mon.get("top", 0)
        mw, mh = mon.get("width", 0), mon.get("height", 0)
        if ml <= cx < ml + mw and mt <= cy < mt + mh:
            chosen = mon
            break
    if chosen is None:
        if not screens:
            return [left, top, max(1, width), max(1, height)], [0.0, 0.0, 1.0, 1.0]
        chosen = screens[0]
    ml, mt = int(chosen.get("left", 0)), int(chosen.get("top", 0))
    mw, mh = max(1, int(chosen.get("width", 1))), max(1, int(chosen.get("height", 1)))
    x0, y0 = max(left, ml), max(top, mt)
    x1, y1 = min(left + width, ml + mw), min(top + height, mt + mh)
    if x1 <= x0 or y1 <= y0:
        return [ml, mt, mw, mh], [0.0, 0.0, 1.0, 1.0]
    return [ml, mt, mw, mh], [
        (x0 - ml) / mw,
        (y0 - mt) / mh,
        (x1 - x0) / mw,
        (y1 - y0) / mh,
    ]


__all__ = ["on_shutter", "placement", "thumbnail_jpeg"]
