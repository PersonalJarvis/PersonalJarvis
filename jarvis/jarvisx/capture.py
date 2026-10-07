"""Screen grabs for Jarvis X: a rectangle, a monitor, the front window.

Reuses the shared platform seams — ``mss`` for pixels, the Screen Context
cursor/window probes for *where* — but none of Screen Context's privacy
machinery: this is the user's own screenshot tool, and what they point it at
is what they get. Every function runs on a worker thread (blocking native
calls) and raises :class:`CaptureError` with a user-facing English message
when no pixels can be produced.

No platform package at module scope (``mss``, ``PIL`` and ctypes load lazily),
so ``import jarvis.jarvisx.capture`` stays clean on a headless base install.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from jarvis.jarvisx.geometry import Rect

log = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """No pixels could be produced; the message is shown to the user."""


@dataclass(frozen=True, slots=True)
class Frame:
    """Raw RGB pixels plus where they came from (capture space)."""

    width: int
    height: int
    rgb: bytes
    bbox: Rect


@contextmanager
def input_space() -> Iterator[None]:
    """Pin native geometry and capture to one coordinate convention.

    The same pinning Screen Context uses: per-monitor DPI awareness on
    Windows, so monitor rects, window rects and ``mss`` all speak physical
    pixels; a no-op on macOS and X11.
    """
    try:
        from jarvis.core.win32_dpi import ensure_dpi_awareness  # noqa: PLC0415

        ensure_dpi_awareness()
    except Exception:  # noqa: BLE001 - non-Windows and minimal hosts are valid
        log.debug("DPI-awareness probe unavailable", exc_info=True)
    try:
        from jarvis.cu.geometry import input_space as cu_input_space  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - base/headless install
        log.debug("input-space pinning unavailable", exc_info=True)
        yield
        return
    with cu_input_space():
        yield


def capability() -> tuple[bool, str]:
    """Whether this host can take a screenshot at all, and why not."""
    import importlib.util  # noqa: PLC0415

    try:
        from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

        if not display_present():
            return False, (
                "There is no screen on this computer (headless), so nothing can be captured."
            )
        if is_wayland():
            return False, (
                "Screen capture is not available in a Wayland session. "
                "Log in to an X11 session to use Jarvis X."
            )
    except Exception:  # noqa: BLE001 - probes missing: let the grab decide
        log.debug("jarvisx: platform probes unavailable", exc_info=True)
    if importlib.util.find_spec("mss") is None:
        return False, (
            "Screen capture needs the 'mss' package (part of the desktop install: "
            "pip install personal-jarvis[desktop])."
        )
    return True, ""


def permission_problem() -> str:
    """macOS Screen Recording permission (and similar), as a message or ``""``."""
    try:
        from jarvis.screen_context.ports import capture_permission_error  # noqa: PLC0415

        issue = capture_permission_error()
    except Exception:  # noqa: BLE001 - an unavailable probe must not block capture
        log.debug("jarvisx: permission probe failed", exc_info=True)
        return ""
    return issue.message if issue is not None else ""


def list_monitors() -> list[dict]:
    """mss-shaped monitor list (``[0]`` virtual desktop), ``[]`` when none."""
    try:
        import mss  # type: ignore[import-not-found]  # noqa: PLC0415

        with input_space(), mss.mss() as sct:
            return [dict(m) for m in sct.monitors]
    except Exception:  # noqa: BLE001 - no display / no mss
        log.debug("jarvisx: monitor enumeration failed", exc_info=True)
        return []


def cursor_position() -> tuple[int, int] | None:
    try:
        from jarvis.screen_context.ports import PlatformCursorLocator  # noqa: PLC0415

        return PlatformCursorLocator().position()
    except Exception:  # noqa: BLE001 - cursor unreadable: caller falls back
        log.debug("jarvisx: cursor probe failed", exc_info=True)
        return None


def monitor_under_cursor(monitors: list[dict]) -> dict | None:
    """The monitor holding the mouse pointer, else the primary one."""
    physical = monitors[1:] if len(monitors) > 1 else monitors
    if not physical:
        return None
    point = cursor_position()
    if point is not None:
        from jarvis.screen_context.targeting import monitor_for_point  # noqa: PLC0415

        found = monitor_for_point(monitors, point)
        if found is not None:
            return found
    try:
        from jarvis.platform.monitors import resolve_primary_monitor  # noqa: PLC0415

        return resolve_primary_monitor(monitors)
    except Exception:  # noqa: BLE001 - any screen beats no screen
        log.debug("jarvisx: primary monitor resolution failed", exc_info=True)
        return physical[0]


def grab_rect(bbox: Rect) -> Frame:
    """Grab ``bbox`` (capture space) from the desktop."""
    left, top, width, height = (int(v) for v in bbox)
    if width <= 0 or height <= 0:
        raise CaptureError("The area to capture has no size.")
    try:
        import mss  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise CaptureError(
            "Screen capture needs the 'mss' package, which is not installed."
        ) from exc
    try:
        with input_space(), mss.mss() as sct:
            raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Frame(int(raw.size[0]), int(raw.size[1]), bytes(raw.rgb), (left, top, width, height))
    except Exception as exc:  # noqa: BLE001 - GDI/X/Quartz errors vary widely
        raise CaptureError(
            f"The screen could not be captured right now ({exc}). A locked screen, "
            "a sleeping display or a resolution change are the usual causes."
        ) from exc


def front_window() -> tuple[Rect, int | None, str]:
    """The foreground window's frame rect, native handle and title.

    Jarvis X captures the *foreground* window rather than the one under the
    pointer: the shortcut is pressed while working in a window, so the
    focused window is the one meant, and "under the pointer" picks the wrong
    window whenever the mouse rests over a neighbour.
    """
    from jarvis.screen_context.ports import PlatformWindowProbe  # noqa: PLC0415

    snapshot = PlatformWindowProbe().foreground_snapshot()
    if snapshot is None or snapshot.facts.frame_rect is None:
        raise CaptureError("No focused window could be found to capture.")
    left, top, width, height = (int(v) for v in snapshot.facts.frame_rect)
    if width < 16 or height < 16:
        raise CaptureError("The focused window is minimized or too small to capture.")
    return (left, top, width, height), snapshot.handle, snapshot.facts.title


def grab_window(bbox: Rect, handle: int | None) -> Frame:
    """The window's own pixels where the OS offers that, else its rectangle."""
    if handle is not None:
        try:
            from jarvis.platform.window_capture import grab_window as native_grab  # noqa: PLC0415

            left, top, width, height = bbox
            with input_space():
                native = native_grab(
                    int(handle), {"left": left, "top": top, "width": width, "height": height}
                )
            if native is not None:
                (w, h), rgb = native
                return Frame(int(w), int(h), rgb, bbox)
        except Exception:  # noqa: BLE001 - the rectangle grab below still works
            log.debug("jarvisx: native window capture failed, using the rectangle", exc_info=True)
    return grab_rect(clip_to_desktop(bbox, list_monitors()))


def clip_to_desktop(bbox: Rect, monitors: list[dict]) -> Rect:
    """Clip a window rect to the virtual desktop (a window may hang off-screen)."""
    if not monitors:
        return bbox
    desk = monitors[0]
    dl, dt = int(desk.get("left", 0)), int(desk.get("top", 0))
    dr, db = dl + int(desk.get("width", 0)), dt + int(desk.get("height", 0))
    left, top, width, height = bbox
    x0, y0 = max(left, dl), max(top, dt)
    x1, y1 = min(left + width, dr), min(top + height, db)
    if x1 <= x0 or y1 <= y0:
        raise CaptureError("The focused window is not on any screen.")
    return (x0, y0, x1 - x0, y1 - y0)


def encode_png(frame: Frame) -> bytes:
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


def thumbnail_jpeg(width: int, height: int, rgb: bytes, *, edge: int = 560) -> bytes:
    """A small JPEG preview (library tiles and the corner card)."""
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    image = Image.frombytes("RGB", (width, height), rgb)
    image.thumbnail((edge, edge), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=84)
    return buffer.getvalue()


__all__ = [
    "CaptureError",
    "Frame",
    "capability",
    "clip_to_desktop",
    "cursor_position",
    "encode_png",
    "front_window",
    "grab_rect",
    "grab_window",
    "input_space",
    "list_monitors",
    "monitor_under_cursor",
    "permission_problem",
    "thumbnail_jpeg",
]
