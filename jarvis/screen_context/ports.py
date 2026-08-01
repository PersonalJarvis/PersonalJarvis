"""The one platform seam for Screen Context (Windows / macOS / Linux / next).

Five ``Protocol``s, a per-OS implementation behind a factory, and a logged null
fallback for each. Adding a fourth platform means writing new classes and
extending the factories — nothing above this module changes, and nothing above
this module imports a platform package.

The contract every port obeys, without exception:

1. **Never raise into the caller.** A native API that is missing, denied or
   wedged returns the port's "unavailable" value. The single exception is
   :class:`CaptureUnavailable`, raised only by :meth:`SurfaceCapturer.grab`,
   because "I produced no pixels" cannot be expressed as a picture.
2. **Unavailable is not empty.** A port that cannot answer returns ``None`` or
   raises, so the service can record a
   :class:`~jarvis.screen_context.models.Degradation`. Returning ``""`` or an
   empty list would let a caller narrate a blank screen as fact (AP-30).
3. **No module-scope platform import.** ``ctypes.windll``, ``Quartz``,
   ``pynput``, ``mss`` and ``PIL`` are imported inside method bodies, so
   ``import jarvis.screen_context.ports`` stays clean on ``python:3.11-slim``
   (HN-7, mirroring ``jarvis.platform.mouse``).

Nothing here initializes at import time (AP-26): the factories are called on
the first capture, not at boot.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from jarvis.screen_context.models import WindowFacts

log = logging.getLogger(__name__)

#: A point in virtual-desktop coordinates, in the platform's input units.
Point = tuple[int, int]
#: ``(left, top, width, height)``.
Rect = tuple[int, int, int, int]


@contextmanager
def _input_space() -> Iterator[None]:
    """Pin native geometry and capture to one OS coordinate convention."""
    try:
        from jarvis.core.win32_dpi import ensure_dpi_awareness  # noqa: PLC0415

        ensure_dpi_awareness()
    except Exception:  # noqa: BLE001 - non-Windows/minimal hosts are valid
        log.debug("DPI-awareness probe unavailable", exc_info=True)
    try:
        from jarvis.cu.geometry import input_space  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - base/headless install
        yield
        return
    with input_space():
        yield


def _is_wayland() -> bool:
    try:
        from jarvis.platform.probes import is_wayland  # noqa: PLC0415

        return bool(is_wayland())
    except Exception:  # noqa: BLE001 - an absent probe is not Wayland evidence
        return False


class CaptureUnavailable(RuntimeError):
    """No pixels could be produced, with a user-facing reason.

    Carries an English, actionable message: this text is shown to the user and
    read by the model, so "capture failed" is never acceptable — it must name
    the cause and, where one exists, the setting that fixes it.
    """


@dataclass(frozen=True, slots=True)
class CapturePermissionIssue:
    """Structured platform remediation returned before any pixels are read."""

    code: Literal["capture_permission", "wayland_portal"]
    message: str


# --------------------------------------------------------------------------
# Cursor
# --------------------------------------------------------------------------


@runtime_checkable
class CursorLocator(Protocol):
    """Where the mouse pointer is, right now."""

    name: str

    def position(self) -> Point | None:
        """Cursor ``(x, y)``, or ``None`` when unreadable. Never raises."""
        ...


class PlatformCursorLocator:
    """Delegates to the existing cross-platform cursor seam.

    ``jarvis.platform.mouse`` already resolves Windows ``GetCursorPos`` /
    pynput / a null fallback and never raises, so this adapter is a thin
    rename rather than a second implementation — two cursor backends would
    drift.
    """

    name = "platform-cursor"

    def __init__(self) -> None:
        self._backend = None

    def position(self) -> Point | None:
        try:
            if self._backend is None:
                from jarvis.platform.mouse import make_cursor_backend  # noqa: PLC0415

                self._backend = make_cursor_backend()
            with _input_space():
                return self._backend.position()
        except Exception:  # noqa: BLE001 — the seam must never raise
            log.debug("cursor position probe failed", exc_info=True)
            return None


# --------------------------------------------------------------------------
# Bar position (fallback target when the cursor is unreadable)
# --------------------------------------------------------------------------


@runtime_checkable
class BarLocator(Protocol):
    """Where the on-screen bar sits — the documented cursor fallback."""

    name: str

    def position(self) -> Point | None:
        ...


class PersistedBarLocator:
    """Reads the bar's last persisted position.

    The bar runs in its own process (or its own Qt/Tk thread) and exposes no
    in-process handle, but it persists its position on every move — so the
    persisted value is the cheapest correct answer and needs no IPC on the
    voice path. A bar that has never been moved has no persisted position;
    that is a ``None``, and the service falls through to the primary monitor.
    """

    name = "persisted-bar"

    def position(self) -> Point | None:
        try:
            from jarvis.core.config_writer import DEFAULT_CONFIG_FILE  # noqa: PLC0415
            from jarvis.ui.jarvisbar import interaction  # noqa: PLC0415

            return interaction.load_jarvisbar_position(Path(DEFAULT_CONFIG_FILE))
        except Exception:  # noqa: BLE001 — the seam must never raise
            log.debug("bar position probe failed", exc_info=True)
            return None


# --------------------------------------------------------------------------
# Displays
# --------------------------------------------------------------------------


@runtime_checkable
class DisplayEnumerator(Protocol):
    """The monitor layout, and which monitor holds a given point."""

    name: str

    def monitors(self) -> list[dict]:
        """mss-shaped list: ``[0]`` virtual bounds, ``[1:]`` physical screens.

        Returns ``[]`` when no display is addressable (headless, Wayland).
        """
        ...


class MssDisplayEnumerator:
    """Enumerates displays via ``mss`` — the same source every other capture
    path in the tree uses, so monitor identities line up across features."""

    name = "mss-displays"

    def monitors(self) -> list[dict]:
        if _is_wayland():
            log.info(
                "display enumeration unavailable: Wayland requires a portal "
                "capture backend"
            )
            return []
        try:
            import mss  # type: ignore[import-not-found]  # noqa: PLC0415

            with _input_space(), mss.mss() as sct:
                return [dict(m) for m in sct.monitors]
        except Exception:  # noqa: BLE001 — no display / no mss / X error
            log.debug("display enumeration failed", exc_info=True)
            return []


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


@runtime_checkable
class WindowProbe(Protocol):
    """What application is in front, and where its window is."""

    name: str

    def foreground(self) -> WindowFacts | None:
        """Facts about the focused window, or ``None`` when unreadable."""
        ...

    def visible_windows(self) -> tuple[WindowFacts, ...] | None:
        """Visible top-level windows, or ``None`` when unverifiable."""
        ...


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    """Facts and native identity sampled from one foreground-window read."""

    facts: WindowFacts
    handle: int | None = None


class PlatformWindowProbe:
    """Delegates to ``jarvis.platform.window_state``.

    That module already resolves Win32 / Quartz+AX / xdotool behind one API
    with per-OS fallbacks. The frame rect is preferred over the plain window
    rect because it is the visible extent the user sees (DWM shadows excluded
    on Windows), which is what a capture should match.

    ``WindowInfo`` carries no process name, so the application name is resolved
    from the pid when the platform filled one in. That lookup is best-effort and
    optional on purpose: the app name feeds the denylist and the model's
    context, and a missing one degrades to matching on the title alone rather
    than to a hard failure.
    """

    name = "platform-window"

    def foreground_snapshot(self) -> WindowSnapshot | None:
        """Sample facts, frame and native handle atomically."""
        try:
            from jarvis.platform import window_state as ws  # noqa: PLC0415

            with _input_space():
                win = ws.foreground_window()
                if win is None:
                    return None
                rect = ws.window_frame_rect(win) or ws.window_rect(win)
                pid = int(getattr(win, "pid", 0) or 0)
                handle = getattr(win, "handle", None)
                return WindowSnapshot(
                    facts=WindowFacts(
                        app_name=_app_name_for_pid(pid),
                        title=str(getattr(win, "title", "") or ""),
                        pid=pid,
                        frame_rect=rect,
                    ),
                    handle=int(handle) if handle is not None else None,
                )
        except Exception:  # noqa: BLE001 — the seam must never raise
            log.debug("foreground window probe failed", exc_info=True)
            return None

    def foreground(self) -> WindowFacts | None:
        snapshot = self.foreground_snapshot()
        return snapshot.facts if snapshot is not None else None

    def foreground_handle(self) -> int | None:
        """The native window handle of the focused window, when the OS gives one.

        Kept separate from :meth:`foreground` because a handle is only useful to
        the capturer, and threading it through ``WindowFacts`` would put a
        platform-specific integer into the model-facing data.
        """
        try:
            snapshot = self.foreground_snapshot()
            return snapshot.handle if snapshot is not None else None
        except Exception:  # noqa: BLE001
            log.debug("foreground handle probe failed", exc_info=True)
            return None

    def visible_windows(self) -> tuple[WindowFacts, ...] | None:
        """Visible windows for monitor-wide privacy checks.

        A window with unknown geometry is deliberately retained. The service
        treats a denylist match with no rect as intersecting every monitor;
        false refusal is safer than photographing a protected surface.
        """
        if _is_wayland():
            return None
        try:
            from jarvis.platform import window_state as ws  # noqa: PLC0415

            with _input_space():
                windows = ws.list_windows()
                if not windows:
                    return None
                facts: list[WindowFacts] = []
                for window in windows:
                    if bool(getattr(window, "minimized", False)):
                        continue
                    pid = int(getattr(window, "pid", 0) or 0)
                    facts.append(
                        WindowFacts(
                            app_name=_app_name_for_pid(pid),
                            title=str(getattr(window, "title", "") or ""),
                            pid=pid,
                            frame_rect=(
                                ws.window_frame_rect(window)
                                or ws.window_rect(window)
                            ),
                        )
                    )
                return tuple(facts)
        except Exception:  # noqa: BLE001 - privacy verification fails closed above
            log.warning("visible-window privacy probe failed", exc_info=True)
            return None


def _app_name_for_pid(pid: int) -> str:
    """Best-effort process name for ``pid``; ``""`` when it cannot be resolved.

    Not every platform path fills a pid in (only the macOS foreground probe does
    today), and ``psutil`` may be absent on a minimal install — both degrade to
    the empty string, which the denylist treats as "match on title only".
    """
    if pid <= 0:
        return ""
    try:
        import psutil  # noqa: PLC0415

        return str(psutil.Process(pid).name() or "")
    except Exception:  # noqa: BLE001 — no psutil, dead pid, access denied
        log.debug("process name lookup failed for pid %s", pid, exc_info=True)
        return ""


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------


@runtime_checkable
class SurfaceCapturer(Protocol):
    """Grabs a rectangle of the screen, once.

    Returns RAW RGB — encoding happens after redaction, so black boxes are
    burned into the pixels rather than layered over a already-encoded image
    that still contains the secret underneath.
    """

    name: str

    def grab(
        self, bbox: Rect, *, window_handle: int | None = None
    ) -> tuple[tuple[int, int], bytes]:
        """``((width, height), rgb_bytes)``.

        Raises :class:`CaptureUnavailable` — with an actionable English
        message — when no pixels can be produced.
        """
        ...


class NativeSurfaceCapturer:
    """Native per-window capture where the OS offers it, rect grab otherwise.

    Window-scoped requests use a native window-only backend where available.
    On Windows, failure of that backend is a privacy refusal: composing the
    same rectangle from the desktop could photograph an overlapping app.
    """

    name = "native-capture"

    def grab(
        self, bbox: Rect, *, window_handle: int | None = None
    ) -> tuple[tuple[int, int], bytes]:
        left, top, width, height = (int(v) for v in bbox)
        if width <= 0 or height <= 0:
            raise CaptureUnavailable(
                "The area to capture had no size — the window may have been "
                "closed or minimized between the request and the capture."
            )

        if window_handle is not None:
            native = None
            try:
                from jarvis.cu.indicator.capture_guard import (  # noqa: PLC0415
                    indicator_suppressed,
                )
                from jarvis.platform.window_capture import grab_window  # noqa: PLC0415

                with indicator_suppressed(), _input_space():
                    native = grab_window(
                        int(window_handle),
                        {"left": left, "top": top, "width": width, "height": height},
                    )
                if native is not None:
                    return native
            except Exception:  # noqa: BLE001 — platform policy below decides fallback
                log.debug("native window capture failed", exc_info=True)
            if os.name == "nt":
                raise CaptureUnavailable(
                    "The focused Windows window could not be captured safely. "
                    "A desktop-rectangle fallback was refused because another "
                    "window could overlap it. Bring the window to the foreground and "
                    "retry; some protected or GPU-rendered windows do not support "
                    "window-only capture."
                )

        try:
            if _is_wayland():
                raise CaptureUnavailable(
                    "Screen capture is unavailable in this Wayland session. "
                    "Use an X11 session or install a supported desktop-portal "
                    "capture backend."
                )
            import mss  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:
            raise CaptureUnavailable(
                "Screen capture is not available because the 'mss' package is "
                "missing from this installation."
            ) from exc

        try:
            from jarvis.cu.indicator.capture_guard import (  # noqa: PLC0415
                indicator_suppressed,
            )

            with indicator_suppressed(), _input_space(), mss.mss() as sct:
                raw = sct.grab(
                    {"left": left, "top": top, "width": width, "height": height}
                )
            return ((int(raw.size[0]), int(raw.size[1])), bytes(raw.rgb))
        except Exception as exc:  # noqa: BLE001 — GDI/X/Quartz errors vary widely
            raise CaptureUnavailable(
                f"The screen could not be captured right now ({exc}). This is "
                "usually a locked screen, a sleeping display, or a resolution "
                "change in progress."
            ) from exc


# --------------------------------------------------------------------------
# UI text
# --------------------------------------------------------------------------


@runtime_checkable
class UiTextReader(Protocol):
    """Reads visible UI text from the platform's accessibility layer."""

    name: str

    async def read(self, *, window_title_filter: str | None = None):
        """Returns an ``Observation``-shaped object, or ``None`` if unreadable.

        ``None`` (not an empty node list) is the signal that the accessibility
        layer itself is unavailable — the caller must be able to tell that
        apart from a window that genuinely has no text.
        """
        ...


class AccessibilityTextReader:
    """Delegates to ``jarvis.vision.tree_factory.make_ui_tree_source()``.

    That factory already picks UIA (Windows) / AXUIElement (macOS) / AT-SPI
    (Linux) / a null source, which is exactly the per-OS accessibility seam
    this feature needs. The null source returns an Observation with no nodes;
    this adapter maps that to ``None`` so "no accessibility on this host" and
    "this window has no text" stay distinguishable.
    """

    name = "accessibility-text"

    def __init__(self) -> None:
        self._source = None

    async def read(self, *, window_title_filter: str | None = None):
        try:
            if self._source is None:
                from jarvis.vision.tree_factory import (  # noqa: PLC0415
                    make_ui_tree_source,
                )

                self._source = make_ui_tree_source()
            if type(self._source).__name__ == "NullUITreeSource":
                return None
            return await self._source.observe(window_title_filter=window_title_filter)
        except Exception:  # noqa: BLE001 — UIA/AX/AT-SPI raise a wide variety
            log.debug("accessibility text read failed", exc_info=True)
            return None


# --------------------------------------------------------------------------
# Permission probe
# --------------------------------------------------------------------------


def capture_permission_error() -> CapturePermissionIssue | None:
    """``None`` when capture is permitted, else an actionable English message.

    Deliberately uncached: macOS can revoke a TCC grant while Jarvis runs, and
    a cached "granted" would produce a wallpaper-only capture that looks like a
    successful screenshot of an empty desktop. One native call per capture is
    the right price for not lying about what was seen.
    """
    if _is_wayland():
        return CapturePermissionIssue(
            code="wayland_portal",
            message=(
                "Screen capture is unavailable in this Wayland session because "
                "no desktop-portal capture backend is installed. Use an X11 "
                "session or install a supported portal backend, then ask again."
            ),
        )
    try:
        from jarvis.platform.permissions import (  # noqa: PLC0415
            PermissionId,
            get_system_permission_port,
        )

        port = get_system_permission_port()
        if port.runtime_access_granted(PermissionId.SCREEN_RECORDING):
            return None
        return CapturePermissionIssue(
            code="capture_permission",
            message=(
                "Screen capture is blocked because this app does not have the "
                "screen-recording permission. Grant it in your system privacy "
                "settings (macOS: System Settings > Privacy & Security > Screen "
                "Recording), then ask again."
            ),
        )
    except Exception:  # noqa: BLE001 — an unavailable probe must not block Windows/Linux
        log.debug("screen-recording permission probe failed", exc_info=True)
        return None


def accessibility_permission_error() -> str | None:
    """``None`` when UI text may be read, else an actionable English message."""
    try:
        from jarvis.platform.permissions import (  # noqa: PLC0415
            PermissionId,
            get_system_permission_port,
        )

        port = get_system_permission_port()
        if port.runtime_access_granted(PermissionId.ACCESSIBILITY):
            return None
        return (
            "Visible UI text could not be read because the accessibility "
            "permission is missing. The screen image is still available; "
            "grant accessibility in your system privacy settings to include "
            "on-screen text."
        )
    except Exception:  # noqa: BLE001
        log.debug("accessibility permission probe failed", exc_info=True)
        return None


# --------------------------------------------------------------------------
# Factories — the ONE place a fourth platform is wired in
# --------------------------------------------------------------------------


def make_cursor_locator() -> CursorLocator:
    return PlatformCursorLocator()


def make_bar_locator() -> BarLocator:
    return PersistedBarLocator()


def make_display_enumerator() -> DisplayEnumerator:
    return MssDisplayEnumerator()


def make_window_probe() -> WindowProbe:
    return PlatformWindowProbe()


def make_surface_capturer() -> SurfaceCapturer:
    return NativeSurfaceCapturer()


def make_ui_text_reader() -> UiTextReader:
    return AccessibilityTextReader()


__all__ = [
    "AccessibilityTextReader",
    "BarLocator",
    "CaptureUnavailable",
    "CursorLocator",
    "DisplayEnumerator",
    "MssDisplayEnumerator",
    "NativeSurfaceCapturer",
    "PersistedBarLocator",
    "PlatformCursorLocator",
    "PlatformWindowProbe",
    "Point",
    "Rect",
    "SurfaceCapturer",
    "UiTextReader",
    "WindowProbe",
    "WindowSnapshot",
    "accessibility_permission_error",
    "capture_permission_error",
    "make_bar_locator",
    "make_cursor_locator",
    "make_display_enumerator",
    "make_surface_capturer",
    "make_ui_text_reader",
    "make_window_probe",
]
