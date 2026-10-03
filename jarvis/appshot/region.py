"""Select an area of the screen for a region appshot.

:func:`pick_region` starts the one-shot picker sidecar
(:mod:`jarvis.appshot.picker`), waits for the user to drag a rectangle and
returns it; :func:`selection_to_bbox` turns that into capture coordinates.

Two coordinate worlds meet here:

- **capture space** — what Screen Context grabs in: physical pixels on
  Windows (per-monitor DPI aware), points on macOS, root-window pixels on X11.
  ``monitors`` are mss-shaped dicts (``[0]`` = the virtual desktop).
- **Qt space** — the picker's logical pixels, one ``QScreen`` per monitor
  with its own ``devicePixelRatio``.

A selection never crosses between them as absolute coordinates. The picker
reports *which screen* (its Qt geometry and scale) and the selection as
*fractions of that screen*; :func:`match_monitor` finds the same screen in
capture space. Fractions survive every mixed-DPI layout, because a screen's
content scales uniformly within itself even where the virtual-desktop origins
of different screens disagree between the two worlds.

Everything above :func:`picker_capability` is pure and imports nothing
heavy; the picker itself is the only place PySide6 is loaded.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

#: A drag smaller than this (logical px, either side) is a click, not a pick.
MIN_SELECTION_PX = 4

#: The picker gives up on its own after this long without a selection.
PICK_TIMEOUT_S = 120.0
#: Grace for the picker to exit (and its overlay to leave the glass).
_EXIT_GRACE_S = 2.0
#: Extra settle time so the compositor has repainted without the dim layer.
_SETTLE_S = 0.08

Rect = tuple[int, int, int, int]
FRect = tuple[float, float, float, float]

# Hint pill over the screens — app chrome, so it follows the app's display
# language ([ui].language); a phrase table always carries every locale.
_HINTS: dict[str, str] = {
    "de": "Bereich ziehen · Fenster anklicken · Mausrad zoomt · Esc bricht ab",  # i18n-allow
    "en": "Drag to select an area · Click a window · Scroll to zoom · Esc to cancel",
    "es": "Arrastra una zona · Clic en una ventana · Rueda para zoom · Esc cancela",  # i18n-allow
}


@dataclass(frozen=True, slots=True)
class Selection:
    """A finished selection: which Qt screen, which part of it."""

    screen: dict[str, float]
    rect: FRect


class RegionUnavailable(RuntimeError):
    """No area can be selected on this host; the message says why."""


#: One picker at a time: a second shortcut press, the page button and the
#: voice tool must not stack overlays (one Esc would cancel all of them).
_picking = False


# --------------------------------------------------------------------------
# Pure geometry
# --------------------------------------------------------------------------


def selection_fractions(
    x0: float, y0: float, x1: float, y1: float, width: float, height: float
) -> FRect | None:
    """A drag from ``(x0, y0)`` to ``(x1, y1)`` on a ``width × height`` screen.

    Returns ``(fx, fy, fw, fh)`` clamped to the screen, or ``None`` when the
    drag is too small to be a selection. Works for a drag in any direction.
    """
    if width <= 0 or height <= 0:
        return None
    left = max(0.0, min(float(x0), float(x1)))
    top = max(0.0, min(float(y0), float(y1)))
    right = min(float(width), max(float(x0), float(x1)))
    bottom = min(float(height), max(float(y0), float(y1)))
    if right - left < MIN_SELECTION_PX or bottom - top < MIN_SELECTION_PX:
        return None
    return (left / width, top / height, (right - left) / width, (bottom - top) / height)


def screen_monitor_score(screen: dict, monitor: dict) -> float:
    """How far a Qt screen is from a capture-space monitor (0 = identical).

    Each plausible reading of the Qt geometry is tried — logical as-is (macOS
    points, X11 at scale 1), logical origin with physical size (Qt 6 on
    Windows), and fully physical — and the closest one counts.
    """
    x = float(screen.get("x", 0))
    y = float(screen.get("y", 0))
    w = float(screen.get("w", 0))
    h = float(screen.get("h", 0))
    dpr = float(screen.get("dpr", 1.0) or 1.0)
    ml, mt = float(monitor.get("left", 0)), float(monitor.get("top", 0))
    mw, mh = float(monitor.get("width", 0)), float(monitor.get("height", 0))
    best = float("inf")
    for rx, ry, rw, rh in (
        (x, y, w, h),
        (x, y, w * dpr, h * dpr),
        (x * dpr, y * dpr, w * dpr, h * dpr),
    ):
        # Size mismatches weigh more than origin mismatches: origins are the
        # part mixed-DPI layouts disagree about.
        score = 2.0 * (abs(rw - mw) + abs(rh - mh)) + abs(rx - ml) + abs(ry - mt)
        best = min(best, score)
    return best


def match_monitor(screen: dict, monitors: list[dict]) -> dict | None:
    """The capture-space monitor that is the picker's Qt ``screen``."""
    physical = [m for m in (monitors[1:] if len(monitors) > 1 else monitors) if isinstance(m, dict)]
    if not physical:
        return None
    return min(physical, key=lambda mon: screen_monitor_score(screen, mon))


def fraction_to_bbox(monitor: dict, frac: FRect | list[float]) -> Rect:
    """Fractions of ``monitor`` → ``(left, top, width, height)`` in capture space."""
    ml, mt = int(monitor.get("left", 0)), int(monitor.get("top", 0))
    mw, mh = max(1, int(monitor.get("width", 1))), max(1, int(monitor.get("height", 1)))
    fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in frac)
    x0 = ml + round(fx * mw)
    y0 = mt + round(fy * mh)
    x1 = ml + round(min(1.0, fx + fw) * mw)
    y1 = mt + round(min(1.0, fy + fh) * mh)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def selection_to_bbox(selection: Selection, monitors: list[dict]) -> Rect | None:
    """The selection in capture coordinates, or ``None`` without monitors."""
    monitor = match_monitor(selection.screen, monitors)
    if monitor is None:
        return None
    return fraction_to_bbox(monitor, selection.rect)


def snap_rects_on_screen(
    screen: dict,
    size: tuple[float, float],
    monitors: list[dict],
    windows: list[list[int]],
    *,
    min_px: float,
) -> list[FRect]:
    """Capture-space window rects → the picker screen's logical pixels.

    ``size`` is the picker window's logical size. Rects are clipped to the
    screen; slivers under ``min_px`` and rects on other screens are dropped.
    Order (top-most first) is kept, so the first hit is the visible window.
    """
    monitor = match_monitor(screen, monitors)
    if monitor is None:
        return []
    ml, mt = float(monitor.get("left", 0)), float(monitor.get("top", 0))
    mw = max(1.0, float(monitor.get("width", 1)))
    mh = max(1.0, float(monitor.get("height", 1)))
    width, height = float(size[0]), float(size[1])
    sx, sy = width / mw, height / mh
    out: list[FRect] = []
    for rect in windows:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            continue
        left, top, w, h = (float(v) for v in rect)
        x0, y0 = max(0.0, (left - ml) * sx), max(0.0, (top - mt) * sy)
        x1 = min(width, (left + w - ml) * sx)
        y1 = min(height, (top + h - mt) * sy)
        if x1 - x0 >= min_px and y1 - y0 >= min_px:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


#: Magnifier zoom steps (screen pixels drawn per real pixel) for the wheel.
MAG_ZOOMS: tuple[int, ...] = (2, 3, 4, 6, 8, 12, 16, 24)
MAG_DEFAULT_ZOOM = 8
#: The magnifier's side, in logical pixels, before rounding to whole pixels.
MAG_BOX_PX = 128.0


def step_zoom(current: int, steps: int) -> int:
    """Move ``steps`` notches along :data:`MAG_ZOOMS` (positive = closer)."""
    try:
        index = MAG_ZOOMS.index(current)
    except ValueError:  # an unknown zoom restarts from the default
        index = MAG_ZOOMS.index(MAG_DEFAULT_ZOOM)
    return MAG_ZOOMS[max(0, min(len(MAG_ZOOMS) - 1, index + steps))]


def magnifier_layout(zoom: int, scale: float, box_px: float = MAG_BOX_PX) -> tuple[int, float]:
    """``(source pixels per side, logical px per source pixel)`` for a zoom.

    ``scale`` is device pixels per logical pixel, so one source pixel is drawn
    ``zoom`` device pixels wide whatever the display scaling. The count covers
    the whole box (the edge pixels are clipped, so the box keeps one size at
    every zoom) and is odd, so one pixel sits exactly under the pointer.
    """
    import math  # noqa: PLC0415

    cell = max(1.0, float(zoom)) / max(0.1, float(scale))
    count = max(3, math.ceil(box_px / cell))
    if count % 2 == 0:
        count += 1
    return count, cell


def parse_selection(payload: dict[str, Any]) -> Selection | None:
    """A picker ``selection`` event → :class:`Selection` (``None`` = cancelled)."""
    if payload.get("cancelled"):
        return None
    screen, rect = payload.get("screen"), payload.get("rect")
    if not isinstance(screen, dict) or not isinstance(rect, list) or len(rect) != 4:
        return None
    try:
        info = {key: float(screen[key]) for key in ("x", "y", "w", "h", "dpr")}
        frac = tuple(max(0.0, min(1.0, float(v))) for v in rect)
    except (KeyError, TypeError, ValueError):  # a malformed selection is refused via None
        return None
    if frac[2] <= 0.0 or frac[3] <= 0.0:
        return None
    return Selection(screen=info, rect=frac)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The picker process
# --------------------------------------------------------------------------


def picker_capability() -> tuple[bool, str]:
    """Whether an area can be selected on this host, and why not."""
    try:
        from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

        if not display_present():
            return False, "there is no screen on this computer"
        if is_wayland():
            return False, "Wayland does not let apps draw a selection over other windows"
    except Exception:  # noqa: BLE001 - probes missing: let the picker itself decide
        log.debug("appshot: platform probes unavailable", exc_info=True)
    if importlib.util.find_spec("PySide6") is None:
        return False, "the selection overlay needs PySide6 (the [desktop] extra)"
    return True, ""


def _hint() -> str:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        language = str(getattr(load_config().ui, "language", "") or "").lower()[:2]
        return _HINTS.get(language, _HINTS["en"])
    except Exception:  # noqa: BLE001 - the hint is decoration; English is honest
        log.debug("appshot: picker hint language unresolved", exc_info=True)
        return _HINTS["en"]


def _is_cloaked(handle: int | None) -> bool:
    """Windows only: a DWM-cloaked window (hidden UWP shells) is no target."""
    if os.name != "nt" or not handle:
        return False
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        dwmapi = ctypes.WinDLL("dwmapi")
        cloaked = wintypes.DWORD(0)
        result = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(handle), 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return result == 0 and bool(cloaked.value)
    except Exception:  # noqa: BLE001 - unknown cloak state keeps the window
        return False


def snap_layout() -> dict[str, Any]:
    """Monitors and window rectangles for the picker's window snapping.

    Read BEFORE the picker exists, so its own overlay never becomes a target.
    Rects are in capture coordinates, top-most window first; an empty window
    list (Wayland, an unreadable desktop) only switches snapping off.
    """
    from jarvis.screen_context.ports import make_display_enumerator  # noqa: PLC0415

    monitors = make_display_enumerator().monitors()
    windows: list[list[int]] = []
    try:
        from jarvis.platform import window_state as ws  # noqa: PLC0415
        from jarvis.screen_context.ports import _input_space  # noqa: PLC0415

        with _input_space():
            for win in ws.list_windows():
                if getattr(win, "minimized", False) or _is_cloaked(getattr(win, "handle", None)):
                    continue
                rect = ws.window_frame_rect(win) or ws.window_rect(win)
                if rect and rect[2] > 0 and rect[3] > 0:
                    windows.append([int(v) for v in rect])
    except Exception:  # noqa: BLE001 - snapping is a convenience; dragging still works
        log.debug("appshot: window list for snapping unavailable", exc_info=True)
    return {"monitors": [dict(m) for m in monitors], "windows": windows}


def _spawn(hint: str) -> subprocess.Popen[str]:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    return subprocess.Popen(
        [sys.executable, "-m", "jarvis.appshot.picker", "--hint", hint],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        env=os.environ.copy(),
    )


def _read_result(proc: subprocess.Popen[str]) -> dict[str, Any] | None:
    """Block until the picker reports its selection; ``None`` on EOF."""
    from jarvis.appshot import picker as wire  # noqa: PLC0415

    assert proc.stdout is not None
    for line in proc.stdout:
        payload = wire.decode(line)
        if payload is not None and payload.get("event") == wire.EVENT_SELECTION:
            return payload
    return None


def _send_layout(proc: subprocess.Popen[str], layout: dict[str, Any]) -> None:
    from jarvis.appshot import picker as wire  # noqa: PLC0415

    with contextlib.suppress(Exception):  # a closed pipe means it already ended
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.write(wire.encode({"cmd": wire.CMD_LAYOUT, **layout}))
            proc.stdin.flush()


def _cancel(proc: subprocess.Popen[str]) -> None:
    from jarvis.appshot import picker as wire  # noqa: PLC0415

    with contextlib.suppress(Exception):  # a closed pipe means it already ended
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.write(wire.encode({"cmd": wire.CMD_CANCEL}))
            proc.stdin.flush()


def _reap(proc: subprocess.Popen[str]) -> int | None:
    try:
        proc.wait(timeout=_EXIT_GRACE_S)
    except subprocess.TimeoutExpired:
        log.info("appshot: area picker did not exit in time; stopping it")
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=1.0)
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None:
            with contextlib.suppress(Exception):
                pipe.close()
    return proc.returncode


async def _escape_cancels(proc: subprocess.Popen[str]) -> None:
    """A global Escape also cancels: the picker may not get keyboard focus.

    Windows refuses to hand the foreground to a background process, so the
    picker's own Esc handler can stay deaf until the first click; the shared
    hotkey backend is not.
    """
    try:
        from jarvis.platform.probes import has_hotkey  # noqa: PLC0415

        if not has_hotkey():
            return
        from jarvis.trigger.hotkey import HotkeyTrigger  # noqa: PLC0415

        key = "escape" if sys.platform == "win32" else "esc"
        trigger = HotkeyTrigger({"appshot_region_cancel": [key]})
        async with trigger:
            async for name in trigger.events():
                if name == "appshot_region_cancel":
                    await asyncio.to_thread(_cancel, proc)
                    return
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the picker's own Esc and right-click still cancel
        log.debug("appshot: global Escape for the area picker unavailable", exc_info=True)


def preview_capture_allowed() -> bool:
    """A picker helper may read its grant silently, but must never request one."""
    from jarvis.platform.screen_access import screen_recording_state, state_allows_capture

    return state_allows_capture(screen_recording_state(deep=False))


def grab_preview(screen: Any) -> Any | None:
    """Read a preview only with this helper's grant; no Qt import or prompt."""
    try:
        if not preview_capture_allowed():
            return None
        pixmap = screen.grabWindow(0)
        return None if pixmap.isNull() or pixmap.width() <= 0 else pixmap
    except Exception:  # noqa: BLE001 - the picker can dim the live desktop instead
        log.debug("appshot: preview unavailable; using a live overlay", exc_info=True)
        return None


async def pick_region(
    *, timeout_s: float = PICK_TIMEOUT_S, trace_id: UUID | None = None,
) -> Selection | None:
    """Let the user drag a rectangle. ``None`` = cancelled or timed out.

    Raises :class:`RegionUnavailable` when no picker can run on this host.
    """
    global _picking
    ok, reason = picker_capability()
    if not ok:
        raise RegionUnavailable(f"An area cannot be selected here: {reason}.")
    if _picking:
        raise RegionUnavailable("An area is already being selected. Finish or press Esc first.")
    _picking = True
    try:
        # This is the user gesture. Ask in the parent before even enumerating
        # windows or starting a helper that freezes the desktop (AP-35).
        from jarvis.platform.screen_access import require_screen_recording_async

        await require_screen_recording_async("appshot", trace_id=trace_id)
        payload, code, timed_out = await _run_picker(timeout_s)
    finally:
        _picking = False
    if payload is None and not timed_out and code not in (0, None):
        log.warning("appshot: area picker exited with code %s and no selection", code)
        raise RegionUnavailable(_exit_message(code))
    selection = parse_selection(payload) if payload is not None else None
    if selection is not None:
        await asyncio.sleep(_SETTLE_S)
    return selection


def _exit_message(code: int) -> str:
    from jarvis.appshot.picker import EXIT_NO_GUI  # noqa: PLC0415

    if code == EXIT_NO_GUI:
        return "An area cannot be selected here: the selection overlay found no usable screen."
    return f"The selection overlay stopped unexpectedly (exit code {code}). Nothing was captured."


async def _run_picker(timeout_s: float) -> tuple[dict[str, Any] | None, int | None, bool]:
    """Spawn, wait for one selection, always reap. ``(payload, exit code, timed out)``."""
    hint = await asyncio.to_thread(_hint)
    try:
        layout = await asyncio.to_thread(snap_layout)
    except Exception:  # noqa: BLE001 - no snapping; dragging still works
        log.debug("appshot: snap layout unavailable", exc_info=True)
        layout = None
    # Shielded: a caller cancelled mid-spawn must still get the process reaped,
    # or its overlay would cover the screens until the stdin timeout.
    spawn = asyncio.ensure_future(asyncio.to_thread(_spawn, hint))
    try:
        proc = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):  # a failed spawn leaves nothing to reap
            late = await spawn
            await asyncio.to_thread(_cancel, late)
            await asyncio.to_thread(_reap, late)
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as unavailable
        log.warning("appshot: area picker could not be started", exc_info=True)
        raise RegionUnavailable("The selection overlay could not be started.") from exc
    if layout is not None:
        await asyncio.to_thread(_send_layout, proc, layout)
    escape = asyncio.get_running_loop().create_task(
        _escape_cancels(proc), name="appshot-region-esc"
    )
    reader = asyncio.ensure_future(asyncio.to_thread(_read_result, proc))
    timed_out = False
    payload: dict[str, Any] | None = None
    try:
        try:
            payload = await asyncio.wait_for(asyncio.shield(reader), timeout=timeout_s)
        except TimeoutError:  # the timeout is reported through timed_out
            timed_out = True
            await asyncio.to_thread(_cancel, proc)
    finally:
        escape.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await escape
        if payload is None and not timed_out:
            await asyncio.to_thread(_cancel, proc)  # a cancelled caller closes the overlay
        code = await asyncio.to_thread(_reap, proc)
        with contextlib.suppress(Exception):  # EOF after the reap ends the reader
            await reader
    return payload, code, timed_out


__all__ = [
    "MAG_DEFAULT_ZOOM",
    "MAG_ZOOMS",
    "MIN_SELECTION_PX",
    "RegionUnavailable",
    "Selection",
    "fraction_to_bbox",
    "magnifier_layout",
    "match_monitor",
    "parse_selection",
    "pick_region",
    "picker_capability",
    "screen_monitor_score",
    "selection_fractions",
    "selection_to_bbox",
    "snap_layout",
    "snap_rects_on_screen",
    "step_zoom",
]
