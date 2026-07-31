"""Native per-window capture seam for the window-centric Computer-Use path.

:func:`grab_window` returns ``((width, height), rgb_bytes)`` for one window's
CURRENT content, or ``None`` when no native per-window capture is available
on this host — callers then fall back to grabbing the window's frame rect
from the virtual desktop (mss), which is pixel-identical for the raised
foreground window Computer-Use acts on. Whatever produced the pixels, the
position translation stays with the ONE central
:class:`jarvis.cu.geometry.CoordinateMapper` built from the window's frame
rect in input units.

Per platform:

* **Windows** — Pillow's HWND capture path captures only the requested
  window, so an overlapping window cannot leak into the result. A framing or
  DPI-size mismatch fails closed to ``None``.
* **macOS** — ScreenCaptureKit by CGWindowID (``SCScreenshotManager``,
  macOS 14+; pyobjc ``[desktop-macos]`` extra): captures the window on
  whatever display it sits, at native backing resolution, independent of
  monitor layout. Needs the Screen-Recording permission. Anything missing
  degrades to ``None`` (rect-grab fallback via CoreGraphics/mss).
* **Linux/X11** — ``None``: the window is identified and its geometry
  resolved per window id (xdotool, see
  :func:`jarvis.platform.window_state.window_frame_rect`); the pixels come
  from the root rect grab in the same root-pixel space. Wayland offers no
  addressable global window capture (the portal screencast flow is
  user-interactive) and is refused upstream with the X11/XWayland message.

Import-cleanliness (HN-7): no platform-only package at module scope; pyobjc
frameworks are imported lazily inside the darwin path. Every failure returns
``None`` — this seam must never raise into the capture loop.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from jarvis.platform import detect_platform

log = logging.getLogger(__name__)

#: Completion-handler wait ceiling for one ScreenCaptureKit screenshot. SCK
#: answers in tens of milliseconds; a hung/denied call must not stall the
#: perceive loop, whose own observe timeout is 12 s.
_SCK_TIMEOUT_S = 3.0


def grab_window(
    handle: int, bbox: dict[str, int],
) -> tuple[tuple[int, int], bytes] | None:
    """Capture one window natively by its platform window id.

    ``bbox`` is the window's frame rect in input units (advisory — the
    native backends capture the window's own current bounds). Returns
    ``None`` whenever the host has no native per-window path; never raises.
    """
    try:
        plat = detect_platform()
        if plat == "win32":
            return _grab_window_windows(int(handle), bbox)
        if plat == "darwin":
            return _grab_window_macos(int(handle))
        return None
    except Exception:  # noqa: BLE001 — capture seam must never raise
        log.debug("grab_window failed (non-fatal)", exc_info=True)
        return None


def _grab_window_windows(
    hwnd: int, bbox: dict[str, int],
) -> tuple[tuple[int, int], bytes] | None:
    """Capture exactly one HWND without desktop-compositing overlap."""
    if os.name != "nt" or not hwnd:
        return None

    from jarvis.core.win32_dpi import per_monitor_dpi_context  # noqa: PLC0415

    try:
        from PIL import Image, ImageGrab  # noqa: PLC0415
    except ImportError:
        log.debug("Pillow ImageGrab unavailable")
        return None

    expected = (int(bbox.get("width", 0)), int(bbox.get("height", 0)))
    if expected[0] <= 0 or expected[1] <= 0:
        return None
    with per_monitor_dpi_context():
        image = ImageGrab.grab(window=hwnd)
    if tuple(image.size) != expected:
        from jarvis.platform.window_state import (  # noqa: PLC0415
            _window_client_rect_windows,
            _window_rect_windows,
        )

        outer = _window_rect_windows(hwnd)
        client = _window_client_rect_windows(hwnd)
        crop_box = None
        if outer is not None and tuple(image.size) == outer[2:]:
            left = int(bbox.get("left", 0)) - outer[0]
            top = int(bbox.get("top", 0)) - outer[1]
            crop_box = (left, top, left + expected[0], top + expected[1])
            if (
                crop_box[0] < 0
                or crop_box[1] < 0
                or crop_box[2] > image.size[0]
                or crop_box[3] > image.size[1]
            ):
                crop_box = None
        if crop_box is not None:
            cropped = image.crop(crop_box)
            image.close()
            image = cropped
        elif client is not None and tuple(image.size) == client[2:]:
            client_left = client[0] - int(bbox.get("left", 0))
            client_top = client[1] - int(bbox.get("top", 0))
            if (
                client_left < 0
                or client_top < 0
                or client_left + image.size[0] > expected[0]
                or client_top + image.size[1] > expected[1]
            ):
                image.close()
                return None
            canvas = Image.new("RGB", expected, color=(0, 0, 0))
            client_image = image.convert("RGB")
            try:
                canvas.paste(client_image, (client_left, client_top))
            finally:
                if client_image is not image:
                    client_image.close()
                image.close()
            image = canvas
        else:
            log.debug(
                "HWND capture size %s does not match frame bounds %s",
                image.size,
                expected,
            )
            image.close()
            return None
    try:
        rgb = image.convert("RGB")
        try:
            return (expected, rgb.tobytes())
        finally:
            if rgb is not image:
                rgb.close()
    finally:
        image.close()


def _grab_window_macos(window_id: int) -> tuple[tuple[int, int], bytes] | None:
    """One ScreenCaptureKit screenshot of the window with ``window_id``.

    Blocking wrapper over the async SCK API (pyobjc bridges the completion
    handlers): resolve the ``SCWindow`` from ``SCShareableContent``, build a
    desktop-independent window filter, size the configuration to the
    window's point frame times its backing scale, and decode the returned
    ``CGImage`` to raw RGB via an NSBitmapImageRep PNG round-trip (PIL does
    the decode — no manual bytes-per-row/BGRA handling to get subtly wrong).

    Requirements probed at runtime, each miss returning ``None``:
    pyobjc ScreenCaptureKit framework (``[desktop-macos]`` extra), macOS 14+
    (``SCScreenshotManager``), Screen-Recording permission, window still on
    screen.
    """
    import io  # noqa: PLC0415
    import threading  # noqa: PLC0415

    try:
        import ScreenCaptureKit as sck  # noqa: PLC0415
        from AppKit import NSBitmapImageRep  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — frameworks absent (base install)
        log.debug("ScreenCaptureKit/AppKit unavailable", exc_info=True)
        return None
    manager = getattr(sck, "SCScreenshotManager", None)
    if manager is None:  # pre-14 macOS: no screenshot API on this framework
        log.debug("SCScreenshotManager unavailable (macOS < 14)")
        return None

    # -- shareable content (async -> event) -------------------------------
    content_box: dict[str, Any] = {}
    content_ready = threading.Event()

    def _content_handler(content, error):  # pragma: no cover - live macOS only
        content_box["content"] = content
        content_box["error"] = error
        content_ready.set()

    sck.SCShareableContent.getShareableContentWithCompletionHandler_(
        _content_handler,
    )
    if not content_ready.wait(_SCK_TIMEOUT_S):
        log.debug("SCShareableContent timed out (permission prompt pending?)")
        return None
    content = content_box.get("content")
    if content is None or content_box.get("error") is not None:
        log.debug("SCShareableContent failed: %s", content_box.get("error"))
        return None

    target = None
    for window in content.windows() or []:
        if int(window.windowID()) == window_id:
            target = window
            break
    if target is None:
        log.debug("SCK window id %d not on screen", window_id)
        return None

    # -- screenshot (async -> event) ---------------------------------------
    sc_filter = sck.SCContentFilter.alloc().initWithDesktopIndependentWindow_(
        target,
    )
    config = sck.SCStreamConfiguration.alloc().init()
    frame = target.frame()  # points
    try:
        scale = float(sc_filter.pointPixelScale())
    except Exception:  # noqa: BLE001 — attribute is macOS 14+; default 2x-safe
        scale = 2.0
    config.setWidth_(max(1, round(frame.size.width * scale)))
    config.setHeight_(max(1, round(frame.size.height * scale)))
    config.setShowsCursor_(False)
    # A framed single-window image can include drop-shadow pixels outside the
    # SCWindow frame. The coordinate mapper is anchored to that frame, so
    # accepting shadow padding would shift every inferred target. macOS 14+
    # exposes this selector; if the runtime bridge cannot provide it, fall
    # back to the frame-rect capture path instead of using ambiguous pixels.
    ignore_shadows = getattr(config, "setIgnoreShadowsSingleWindow_", None)
    if not callable(ignore_shadows):
        log.debug("SCK cannot disable single-window shadows; using rect fallback")
        return None
    try:
        ignore_shadows(True)
    except Exception:  # noqa: BLE001 - unsafe framing must fail closed
        log.debug("SCK single-window shadow suppression failed", exc_info=True)
        return None

    image_box: dict[str, Any] = {}
    image_ready = threading.Event()

    def _image_handler(image, error):  # pragma: no cover - live macOS only
        image_box["image"] = image
        image_box["error"] = error
        image_ready.set()

    manager.captureImageWithFilter_configuration_completionHandler_(
        sc_filter, config, _image_handler,
    )
    if not image_ready.wait(_SCK_TIMEOUT_S):
        log.debug("SCScreenshotManager timed out")
        return None
    cg_image = image_box.get("image")
    if cg_image is None or image_box.get("error") is not None:
        log.debug("SCK screenshot failed: %s", image_box.get("error"))
        return None

    # -- CGImage -> raw RGB -------------------------------------------------
    rep = NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
    if rep is None:
        return None
    png = rep.representationUsingType_properties_(4, None)  # NSPNGFileType
    if png is None:
        return None
    img = Image.open(io.BytesIO(bytes(png))).convert("RGB")
    return ((img.width, img.height), img.tobytes())


__all__ = ["grab_window"]
