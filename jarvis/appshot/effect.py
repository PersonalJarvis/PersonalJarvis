"""The shutter effect: flash, then the picture flies into the corner.

Installed as the Screen Context shutter hook, so it fires at the true capture
moment for every look the shared service takes — a shortcut appshot, a spoken
"take an appshot", a "what do you see?". The thumbnail is cut from the raw
frame in memory and piped to the local indicator sidecar; it is never stored
and never published on the event bus.

The thumbnail then rests in the corner as a card: a click opens the appshot
editor (in its own window where the desktop shell allows), a drag hands the
picture to another app, and on hover it offers Copy, Save, Edit and Close
(:mod:`jarvis.appshot.card_actions`). How long it rests is
``[appshot].card_seconds`` (``0`` = until closed). For that drag the
finished, privacy-filtered appshot follows (:func:`attach_card_image`) — the
raw-frame thumbnail itself never leaves the sidecar.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import io
import logging
from typing import Any

log = logging.getLogger(__name__)

#: Longest edge of the thumbnail sent to the sidecar. The card is painted at
#: ~250 logical px, so this stays sharp on a 2x display and small on the pipe.
_THUMB_EDGE = 560

_tasks: set[asyncio.Task[None]] = set()

#: Markings the user drew in the area picker for the capture now running
#: (:mod:`jarvis.appshot.markup`); the thumbnail shows them like the appshot.
shutter_markup: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "appshot_shutter_markup", default=None
)

# The card's hover line — app chrome, so it follows [ui].language; a phrase
# table always carries every supported locale.
_CARD_HINTS: dict[str, str] = {
    "de": "Klicken zum Bearbeiten · Ziehen zum Teilen",  # i18n-allow: product UI string
    "en": "Click to edit · Drag to share",
    "es": "Clic para editar · Arrastra para compartir",  # i18n-allow: product UI string
    "zh": "点击编辑 · 拖动分享",  # i18n-allow: product UI string
    "pt": "Clica para editar · Arrasta para partilhar",  # i18n-allow: product UI string
}


def card_hint(config: Any) -> str:
    language = str(getattr(getattr(config, "ui", None), "language", "") or "").lower()[:2]
    return _CARD_HINTS.get(language, _CARD_HINTS["en"])


def on_shutter(target: Any, size: tuple[int, int], rgb: bytes, monitors: list[dict]) -> None:
    """Screen Context shutter hook — synchronous, schedules the slow part."""
    from jarvis.cu.indicator.controller import get_indicator_controller  # noqa: PLC0415

    controller = get_indicator_controller()
    if controller is None:
        return
    # Before the capture's own border dismissal can quit the sidecar.
    controller.hold_for_snap()
    loop = asyncio.get_running_loop()
    task = loop.create_task(
        _play(controller, tuple(target.bbox), size, rgb, monitors, shutter_markup.get()),
        name="appshot-effect",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    # The card that follows opens the editor: have its window loaded by then.
    from jarvis.appshot.editor_window import prewarm_editor_window  # noqa: PLC0415

    warm = loop.create_task(prewarm_editor_window(), name="appshot-editor-prewarm")
    _tasks.add(warm)
    warm.add_done_callback(_tasks.discard)


async def _play(
    controller: Any,
    bbox: tuple[int, int, int, int],
    size: tuple[int, int],
    rgb: bytes,
    monitors: list[dict],
    markup: Any = None,
) -> None:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        config = await asyncio.to_thread(load_config)
        if not bool(getattr(getattr(config, "appshot", None), "effect", True)):
            return
        from jarvis.appshot.card_actions import card_labels, card_rest_ms  # noqa: PLC0415

        if markup is not None:
            from jarvis.appshot.markup import apply_to_rgb  # noqa: PLC0415

            size, rgb = await asyncio.to_thread(apply_to_rgb, size, rgb, markup)
        thumb = await asyncio.to_thread(thumbnail_jpeg, size, rgb)
        monitor, rect = placement(bbox, monitors)
        shown = await controller.snap(
            monitor=monitor,
            rect=rect,
            thumb_b64=base64.b64encode(thumb).decode("ascii"),
            hint=card_hint(config),
            rest_ms=card_rest_ms(config),
            labels=card_labels(config),
        )
        if not shown:
            log.info("appshot: shutter effect could not be shown on this desktop")
    except Exception:  # noqa: BLE001 - the effect is decoration, never a failure
        log.warning("appshot: shutter effect failed", exc_info=True)


async def attach_card_image(image: bytes, shot_id: str = "") -> None:
    """Give the resting card the finished picture, so a drag can share it.

    ``shot_id`` ties the card to its appshot, so Copy, Save and Edit on an
    older card in the corner stack reach that card's own picture.
    """
    try:
        from jarvis.cu.indicator.controller import get_indicator_controller  # noqa: PLC0415

        controller = get_indicator_controller()
        if controller is None:
            return
        await controller.snap_image(base64.b64encode(image).decode("ascii"), shot_id=shot_id)
    except Exception:  # noqa: BLE001 - without it the card still opens the editor
        log.warning("appshot: could not hand the picture to the card", exc_info=True)


def thumbnail_jpeg(size: tuple[int, int], rgb: bytes) -> bytes:
    """A small JPEG of the raw frame, for the local effect only."""
    from PIL import Image  # noqa: PLC0415

    image = Image.frombytes("RGB", size, rgb)
    image.thumbnail((_THUMB_EDGE, _THUMB_EDGE), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


def thumbnail_from_image(image: bytes) -> bytes:
    """A small JPEG of a finished (edited) appshot, for the card coming back."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as picture:
        small = picture.convert("RGB")
        small.thumbnail((_THUMB_EDGE, _THUMB_EDGE), Image.Resampling.BILINEAR)
        buffer = io.BytesIO()
        small.save(buffer, format="JPEG", quality=82)
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


__all__ = [
    "attach_card_image",
    "card_hint",
    "on_shutter",
    "placement",
    "shutter_markup",
    "thumbnail_jpeg",
]
