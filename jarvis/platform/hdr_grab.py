"""One full-depth still of a desktop rectangle, when the monitor needs it.

:func:`grab_extended` answers a screenshot request for a rectangle in capture
coordinates (physical pixels, the space mss and Screen Context use). When the
rectangle lies on ONE monitor that runs HDR or a wide colour gamut, it returns
the rectangle as scRGB (``float16``, linear, 1.0 = 80 nits) plus what is
needed to tag it. Anything else — an SDR monitor, a rectangle across two
monitors, macOS/Linux, a duplication failure — returns ``None`` and the
caller keeps its 8-bit grab. Never raises.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: How long to wait for Windows to present a frame before nudging a repaint.
_FIRST_WAIT_MS = 120
_AFTER_NUDGE_MS = 600


@dataclass(frozen=True, slots=True)
class ExtendedFrame:
    #: scRGB pixels of the rectangle, ``float16 (h, w, 4)``.
    pixels: Any = field(repr=False)
    #: Brightness of SDR white on that monitor (Windows "SDR content brightness").
    sdr_white_nits: float
    #: ``"hdr"`` or ``"wcg"``.
    mode: str
    #: Peak luminance the monitor reports, for metadata; 0 when unknown.
    max_nits: float = 0.0


def grab_extended(bbox: tuple[int, int, int, int]) -> ExtendedFrame | None:
    """``bbox`` = ``(left, top, width, height)`` in physical desktop pixels."""
    if sys.platform != "win32":
        return None
    try:
        return _grab_windows(bbox)
    except Exception as exc:  # noqa: BLE001 - the 8-bit grab is always the fallback
        log.info("full-depth capture unavailable (%s); using the 8-bit grab", exc)
        log.debug("full-depth capture failure", exc_info=True)
        return None


def _grab_windows(bbox: tuple[int, int, int, int]) -> ExtendedFrame | None:
    from jarvis.platform.display_color import device_at, display_color  # noqa: PLC0415
    from jarvis.platform.win_duplication import (  # noqa: PLC0415
        DesktopDuplication,
        DuplicationUnavailable,
    )

    left, top, width, height = (int(v) for v in bbox)
    if width <= 0 or height <= 0:
        return None
    device = device_at((left + width // 2, top + height // 2))
    colour = display_color(device)
    if not device or not colour.extended:
        return None
    with DesktopDuplication(device) as dup:
        out_left, out_top, out_right, out_bottom = dup.info.rect
        if (
            left < out_left or top < out_top
            or left + width > out_right or top + height > out_bottom
        ):
            return None  # spans monitors: one duplication cannot hold it
        try:
            dup.wait_first_frame(_FIRST_WAIT_MS)
        except DuplicationUnavailable:
            # A still desktop presents nothing. A repaint of the front window
            # makes Windows compose one frame without changing what is shown.
            _nudge_repaint()
            dup.wait_first_frame(_AFTER_NUDGE_MS)
        frame = dup.read_linear()
        x, y = left - out_left, top - out_top
        pixels = frame[y : y + height, x : x + width].copy()
        return ExtendedFrame(
            pixels=pixels,
            sdr_white_nits=colour.sdr_white_nits,
            mode=colour.mode,
            max_nits=dup.info.max_nits,
        )


def _nudge_repaint() -> None:
    import ctypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32")
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        # RDW_INVALIDATE | RDW_ALLCHILDREN: posts a repaint, never waits for
        # it, so a hung foreground app cannot hold the shutter.
        user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0080)
    time.sleep(0.016)
