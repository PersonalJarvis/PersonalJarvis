"""Screen geometry and coordinate mapping for Computer-Use v2.

The chronic "clicks land next to the target" class of bugs came from mixing
coordinate spaces: the model sees a (possibly downscaled) image, the capture
covers one monitor of a multi-monitor virtual desktop (origins can be
negative), the OS input APIs consume their own units (physical pixels on
Windows/X11, logical points on macOS), and per-monitor DPI scaling makes the
spaces disagree with each other. This module makes the transform chain
explicit and single-sourced:

    model space  ->  image space  ->  screen space (OS input units)

* **Input units** are the units the OS input APIs consume. With the thread
  DPI pin below, ``mss`` monitor rects are expressed in the SAME units on
  every platform: physical virtual-desktop pixels on Windows (per-monitor
  DPI aware) and Linux/X11, logical points on macOS (where ``mss`` reports
  point rects and Quartz events consume points).
* A :class:`CoordinateMapper` is created once per captured frame and records
  the capture rect (input units) plus the exact size of the image the model
  received. Every action coordinate MUST resolve through the mapper of the
  frame the model actually saw — never through a separately-probed monitor
  that can diverge on a mixed-DPI desktop (live bug 2026-06-28).

Windows mixed-DPI note (the open "Face B" of the left-monitor bug): pywebview
flips the PROCESS DPI awareness at runtime (``SetProcessDPIAware()``), which
silently re-virtualizes ``GetSystemMetrics``/monitor rects for threads without
their own context — capture and input then disagree across monitors with
different scale factors. :func:`input_space` pins the CALLING thread to
PER_MONITOR_AWARE_V2 for the duration of any geometry query, capture, or
input dispatch, and restores the previous context afterwards (executor
threads are shared — a sticky pin could leak into unrelated code).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal

logger = logging.getLogger(__name__)

#: Coordinate conventions a vision model can emit. ``normalized_1000`` is the
#: Gemini family convention (0..1000 grid over the image); ``image_pixels`` is
#: the Claude/OpenAI convention (pixel coordinates on the image as sent).
CoordinateConvention = Literal["normalized_1000", "image_pixels"]

_NORM_MAX = 1000


# ---------------------------------------------------------------------------
# Thread DPI pin (Windows) — capture, geometry and input must share one space.
# ---------------------------------------------------------------------------

# DPI_AWARENESS_CONTEXT handles (winuser.h): V2 covers child windows and
# non-client areas; -3 is the 1607 fallback.
_DPI_CTX_PER_MONITOR_V2 = -4
_DPI_CTX_PER_MONITOR = -3


@contextmanager
def input_space() -> Iterator[None]:
    """Pin the calling thread to per-monitor DPI awareness for the block.

    On Windows this guarantees that monitor rects, ``mss`` captures,
    ``GetSystemMetrics`` and ``SendInput`` normalization all read the SAME
    physical-pixel virtual desktop, regardless of any process-level awareness
    flip (pywebview). The previous thread context is restored on exit because
    ``asyncio.to_thread`` pool threads are shared with unrelated code.

    Non-Windows and pre-1607 Windows: a no-op — those platforms have a single
    consistent space already (macOS points / X11 pixels).
    """
    if os.name != "nt":
        yield
        return
    prev = None
    set_ctx = None
    try:
        import ctypes  # noqa: PLC0415

        set_ctx = ctypes.windll.user32.SetThreadDpiAwarenessContext
        set_ctx.restype = ctypes.c_void_p
        set_ctx.argtypes = [ctypes.c_void_p]
        for context in (_DPI_CTX_PER_MONITOR_V2, _DPI_CTX_PER_MONITOR):
            prev = set_ctx(ctypes.c_void_p(context))
            if prev is not None:
                break
    except (OSError, AttributeError):  # pre-1607 or exotic host: proceed unpinned
        logger.debug("SetThreadDpiAwarenessContext unavailable", exc_info=True)
        set_ctx = None
    try:
        yield
    finally:
        if set_ctx is not None and prev is not None:
            try:
                set_ctx(ctypes.c_void_p(prev))
            except Exception:  # noqa: BLE001 — restore is best-effort
                logger.debug("thread DPI context restore failed", exc_info=True)


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MonitorInfo:
    """One monitor's rect in input units on the virtual desktop.

    ``left``/``top`` can be negative (a monitor left of / above the primary).
    """

    left: int
    top: int
    width: int
    height: int
    is_primary: bool = False
    name: str = ""

    @property
    def bbox(self) -> dict[str, int]:
        """mss-style bbox dict for grabbing this monitor."""
        return {
            "left": self.left, "top": self.top,
            "width": self.width, "height": self.height,
        }

    def contains(self, x: float, y: float) -> bool:
        return (
            self.left <= x < self.left + self.width
            and self.top <= y < self.top + self.height
        )


def list_monitors() -> list[MonitorInfo]:
    """Enumerate physical monitors in input units (DPI-pinned on Windows).

    Returns an empty list when no display / mss is available (headless) —
    callers must treat that as "cannot act" and degrade honestly.
    """
    try:
        import mss  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — desktop extras absent (headless base)
        return []
    try:
        with input_space(), mss.mss() as sct:
            monitors = sct.monitors
            if len(monitors) < 2:
                return []
            physical = monitors[1:]
            from jarvis.platform.monitors import resolve_primary_monitor  # noqa: PLC0415

            try:
                primary = resolve_primary_monitor(monitors)
            except Exception:  # noqa: BLE001
                primary = physical[0]
            return [
                MonitorInfo(
                    left=int(m["left"]),
                    top=int(m["top"]),
                    width=int(m["width"]),
                    height=int(m["height"]),
                    is_primary=(m is primary),
                    name=str(m.get("name", "") or ""),
                )
                for m in physical
            ]
    except Exception:  # noqa: BLE001 — display asleep / X server gone
        logger.debug("list_monitors failed", exc_info=True)
        return []


def virtual_screen_bounds(monitors: list[MonitorInfo]) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the bounding box over all monitors."""
    if not monitors:
        return (0, 0, 0, 0)
    left = min(m.left for m in monitors)
    top = min(m.top for m in monitors)
    right = max(m.left + m.width for m in monitors)
    bottom = max(m.top + m.height for m in monitors)
    return (left, top, right - left, bottom - top)


# ---------------------------------------------------------------------------
# CoordinateMapper — one per captured frame
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoordinateMapper:
    """Maps model/image coordinates of ONE frame to absolute screen input units.

    ``capture_*`` is the rect that was grabbed, in input units on the virtual
    desktop. ``image_*`` is the pixel size of the image that was actually sent
    to the model (after any downscale). The physical capture resolution drops
    out of the math: image -> screen is a pure scale by
    ``capture_size / image_size`` plus the capture origin, which stays correct
    on Retina (capture rect in points, image in pixels) and on downscaled
    frames alike.
    """

    capture_left: int
    capture_top: int
    capture_width: int
    capture_height: int
    image_width: int
    image_height: int

    def __post_init__(self) -> None:
        if self.capture_width <= 0 or self.capture_height <= 0:
            raise ValueError("capture rect must have positive size")
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("image size must be positive")
        # The capture is downscaled uniformly (no letterbox/crop), so the
        # aspect ratios must agree; a mismatch means the caller built the
        # mapper from the wrong frame — fail loudly instead of mis-clicking.
        cap_aspect = self.capture_width / self.capture_height
        img_aspect = self.image_width / self.image_height
        if abs(cap_aspect - img_aspect) > 0.02 * max(cap_aspect, img_aspect):
            raise ValueError(
                f"aspect mismatch: capture {self.capture_width}x"
                f"{self.capture_height} vs image {self.image_width}x"
                f"{self.image_height}"
            )

    # -- image space -> screen space ------------------------------------

    def image_to_screen(self, ix: float, iy: float) -> tuple[int, int]:
        """Map an image-pixel coordinate to absolute screen input units.

        Input is clamped to the image so a slightly-out-of-range model
        estimate cannot escape the captured monitor. ``+0.5`` centers the
        mapping on the pixel so a 1:1 frame maps pixel N to pixel N.

        The local offset is computed non-negative and floored BEFORE the
        (possibly negative) capture origin is added — ``int()`` on a negative
        float truncates toward zero and shifted every left-of-primary monitor
        click by one unit toward the primary.
        """
        ix = min(max(float(ix), 0.0), float(self.image_width - 1))
        iy = min(max(float(iy), 0.0), float(self.image_height - 1))
        lx = int((ix + 0.5) * self.capture_width / self.image_width)
        ly = int((iy + 0.5) * self.capture_height / self.image_height)
        lx = min(lx, self.capture_width - 1)
        ly = min(ly, self.capture_height - 1)
        return (self.capture_left + lx, self.capture_top + ly)

    def normalized_to_screen(self, nx: float, ny: float) -> tuple[int, int]:
        """Map a 0..1000 normalized coordinate (Gemini grid) to screen units.

        Uses ``round`` on the local offset — the exact math the legacy engine
        live-proved (norm 636 on the 3840px monitor at -3840 -> abs -1398) —
        then adds the origin as an exact integer.
        """
        nx = min(max(float(nx), 0.0), float(_NORM_MAX))
        ny = min(max(float(ny), 0.0), float(_NORM_MAX))
        lx = round(nx / _NORM_MAX * self.capture_width)
        ly = round(ny / _NORM_MAX * self.capture_height)
        # Clamp inside the capture rect (1000/1000 is the bottom-right EDGE).
        lx = min(lx, self.capture_width - 1)
        ly = min(ly, self.capture_height - 1)
        return (self.capture_left + lx, self.capture_top + ly)

    def model_to_screen(
        self, x: float, y: float, convention: CoordinateConvention,
    ) -> tuple[int, int]:
        """Resolve a model-emitted coordinate per the provider's convention."""
        if convention == "normalized_1000":
            return self.normalized_to_screen(x, y)
        if convention == "image_pixels":
            return self.image_to_screen(x, y)
        raise ValueError(f"unknown coordinate convention: {convention!r}")

    # -- screen space -> image space ------------------------------------

    def screen_to_image(self, sx: float, sy: float) -> tuple[int, int]:
        """Inverse mapping, clamped to the image (for verification crops)."""
        ix = (float(sx) - self.capture_left) * self.image_width / self.capture_width
        iy = (float(sy) - self.capture_top) * self.image_height / self.capture_height
        ix = min(max(ix, 0.0), float(self.image_width - 1))
        iy = min(max(iy, 0.0), float(self.image_height - 1))
        return (int(ix), int(iy))

    # -- helpers ----------------------------------------------------------

    @property
    def screen_rect(self) -> tuple[int, int, int, int]:
        return (
            self.capture_left, self.capture_top,
            self.capture_width, self.capture_height,
        )

    def contains_screen(self, sx: float, sy: float) -> bool:
        return (
            self.capture_left <= sx < self.capture_left + self.capture_width
            and self.capture_top <= sy < self.capture_top + self.capture_height
        )

    def region_around(self, sx: int, sy: int, radius: int) -> dict[str, int]:
        """mss-style bbox of side ``2*radius`` around a screen point, clamped
        to the capture rect (never grabs outside the frame's monitor)."""
        r = max(1, int(radius))
        left = max(self.capture_left, min(int(sx) - r,
                   self.capture_left + self.capture_width - 1))
        top = max(self.capture_top, min(int(sy) - r,
                  self.capture_top + self.capture_height - 1))
        width = max(1, min(r * 2, self.capture_left + self.capture_width - left))
        height = max(1, min(r * 2, self.capture_top + self.capture_height - top))
        return {"left": left, "top": top, "width": width, "height": height}
