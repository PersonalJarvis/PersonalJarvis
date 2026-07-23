"""Cross-platform primary-monitor resolution (audit G8a).

Computer-Use works on ONE monitor (the "main" one) by default. Picking it
robustly matters: a secondary screen LEFT of primary has negative virtual-X where
clicks misfire, so capturing/acting on the wrong screen breaks every mission.

The hard part is identifying the primary WITHOUT assuming it sits at virtual
origin (0,0): on Windows it always does, but on macOS/Linux the primary can be
anywhere in the virtual desktop. ``native_primary_origin`` asks the OS for the
true primary's top-left (Win ``MONITORINFOF_PRIMARY``, macOS ``CGMainDisplayID``,
X11 ``XRRGetOutputPrimary``); ``resolve_primary_monitor`` matches that against the
enumerated monitor list, with config overrides for ``largest`` or an explicit id.

Everything is best-effort and never raises: an unavailable native query degrades
to the (0,0)-origin heuristic, and an unknown override falls back to primary
rather than silently picking a wrong screen.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

# Override tokens that mean "the OS primary" (the default).
_PRIMARY_TOKENS = frozenset({"", "primary", "auto", "os", "os-primary", "main"})


def _area(m: dict) -> int:
    return int(m.get("width", 0)) * int(m.get("height", 0))


def resolve_primary_monitor(
    monitors: list[dict], *, override: str = "primary"
) -> dict:
    """Pick the monitor Jarvis treats as "main" from an mss-style ``monitors``
    list (``[0]`` = virtual bounding box, ``[1:]`` = physical screens).

    ``override``:
      * ``"primary"`` (default) — the OS primary: an ``is_primary`` flag if the
        enumerator set one, else the screen whose origin matches
        :func:`native_primary_origin`, else the screen at virtual (0,0), else the
        first physical screen.
      * ``"largest"`` — the screen with the biggest area.
      * an explicit id — a case-insensitive substring of a monitor ``name``, or a
        1-based physical index (``"1"``, ``"2"``). An unmatched id falls back to
        primary (never a silent wrong pick).

    Pure logic; the only side input is :func:`native_primary_origin`, which is
    monkeypatched in tests. Never raises."""
    if not monitors:
        raise ValueError("monitors is empty")
    if len(monitors) <= 1:
        return monitors[0]
    physical = monitors[1:]
    ov = (override or "primary").strip().lower()

    if ov == "largest":
        return max(physical, key=_area)

    if ov not in _PRIMARY_TOKENS:
        # Explicit id: name substring, then 1-based index. Unmatched -> primary.
        by_name = next(
            (m for m in physical if ov in str(m.get("name", "")).strip().lower()),
            None,
        )
        if by_name is not None:
            return by_name
        if ov.isdigit():
            idx = int(ov)
            if 1 <= idx <= len(physical):
                return physical[idx - 1]
        # fall through to primary resolution

    # Default: OS primary.
    flagged = next((m for m in physical if m.get("is_primary")), None)
    if flagged is not None:
        return flagged
    origin = native_primary_origin()
    if origin is not None:
        ox, oy = origin
        at_native = next(
            (m for m in physical
             if int(m.get("left", 0)) == ox and int(m.get("top", 0)) == oy),
            None,
        )
        if at_native is not None:
            return at_native
    at_zero = next(
        (m for m in physical
         if int(m.get("left", 0)) == 0 and int(m.get("top", 0)) == 0),
        None,
    )
    if at_zero is not None:
        return at_zero
    return physical[0]


def virtual_desktop_bounds() -> tuple[int, int, int, int] | None:
    """Bounding box ``(left, top, width, height)`` over ALL monitors, in the
    platform's input units — or ``None`` when it cannot be determined
    (headless, Wayland, missing native API).

    This is the correct scope for any "is this UI element on screen?"
    filter: clipping such a filter to the PRIMARY monitor silently drops
    every element of a window on a secondary monitor (2026-07-02 incident —
    the whole accessibility tree of a Chrome window on the left monitor was
    pruned away, so Computer-Use lost its clickable anchors, field hints and
    focus evidence there). Never raises.
    """
    try:
        if sys.platform == "win32":
            return _win_virtual_bounds()
        if sys.platform == "darwin":
            return _macos_virtual_bounds()
        from jarvis.platform.probes import is_wayland  # noqa: PLC0415

        if is_wayland():
            return None
        return _x11_virtual_bounds()
    except Exception:  # noqa: BLE001 — best-effort probe, callers keep a fallback
        return None


def _win_virtual_bounds() -> tuple[int, int, int, int] | None:
    """SM_*VIRTUALSCREEN metrics in physical pixels.

    Uses a THREAD-scoped per-monitor DPI pin (restored afterwards) so the
    metrics are not DPI-virtualized even in an unaware host process. A
    process-wide awareness flip is deliberately NOT performed here — this is
    a read-only getter reached from always-on tree sources, and mutating
    process awareness as a side effect would race pywebview's own
    declaration (2026-07-02 review finding, AP-9 class). The CU engine
    declares the process context itself at mission start.
    """
    import ctypes  # noqa: PLC0415

    set_ctx = None
    prev = None
    try:
        set_ctx = ctypes.windll.user32.SetThreadDpiAwarenessContext
        set_ctx.restype = ctypes.c_void_p
        set_ctx.argtypes = [ctypes.c_void_p]
        for context in (-4, -3):  # PER_MONITOR_AWARE_V2, then V1 (1607)
            prev = set_ctx(ctypes.c_void_p(context))
            if prev is not None:
                break
    except (OSError, AttributeError):  # pre-1607: read unpinned
        set_ctx = None
    try:
        gsm = ctypes.windll.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CX=78, SM_CY=79
        left, top, width, height = gsm(76), gsm(77), gsm(78), gsm(79)
    finally:
        if set_ctx is not None and prev is not None:
            try:
                set_ctx(ctypes.c_void_p(prev))
            except Exception:  # noqa: BLE001,S110 — best-effort DPI-context restore
                pass
    if width <= 0 or height <= 0:
        return None
    return (int(left), int(top), int(width), int(height))


def _macos_virtual_bounds() -> tuple[int, int, int, int] | None:
    """Union over ``CGGetActiveDisplayList`` bounds (global points)."""
    try:
        from Quartz import (  # noqa: PLC0415
            CGDisplayBounds,
            CGGetActiveDisplayList,
        )
    except Exception:  # noqa: BLE001 — pyobjc not installed
        return None
    err, display_ids, count = CGGetActiveDisplayList(16, None, None)
    if err or not display_ids or not count:
        return None
    rects = [CGDisplayBounds(d) for d in list(display_ids)[:count]]
    left = min(int(r.origin.x) for r in rects)
    top = min(int(r.origin.y) for r in rects)
    right = max(int(r.origin.x + r.size.width) for r in rects)
    bottom = max(int(r.origin.y + r.size.height) for r in rects)
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


def _x11_virtual_bounds() -> tuple[int, int, int, int] | None:
    """X11 root-window geometry (the root spans all monitors).

    Known limitation: on the legacy separate-X-screens ("Zaphod") multihead
    layout ``xdotool getdisplaygeometry`` reports only screen 0 — callers
    keep their conservative fallback there. Modern XRandR/Xinerama setups
    (one logical screen) report the full span.
    """
    if shutil.which("xdotool") is None:
        return None
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    proc = subprocess.run(
        ["xdotool", "getdisplaygeometry"],
        capture_output=True,
        text=True,
        timeout=3.0,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or "").split()
    if len(parts) < 2:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return (0, 0, width, height)


def work_area_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """Work area ``(left, top, width, height)`` of the monitor containing ``(x, y)``.

    The "work area" excludes the OS taskbar/panel where the platform reports it
    (Windows ``rcWork``); on macOS/X11 it degrades to the monitor's full bounds.
    Units match the platform's input space — physical pixels on a
    per-monitor-DPI-aware Windows thread (the Jarvis Bar's Tk thread is pinned
    exactly that way, so these coordinates line up with the window geometry),
    global points on macOS.

    This is the primitive the on-screen bar uses to follow the mouse across
    monitors and to pin a cross-monitor drag to the screen it lands on. Returns
    ``None`` when it cannot be determined (headless / Wayland / missing native
    API) so callers keep the bar where it is instead of guessing. Never raises.
    """
    try:
        if sys.platform == "win32":
            return _win_work_area_at(x, y)
        if sys.platform == "darwin":
            return _macos_work_area_at(x, y)
        from jarvis.platform.probes import is_wayland  # noqa: PLC0415

        if is_wayland():
            return None  # no reliable global monitor geometry under Wayland
        return _x11_work_area_at(x, y)
    except Exception:  # noqa: BLE001 — best-effort; a probe failure is never fatal
        return None


def _win_work_area_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """``rcWork`` of the monitor under ``(x, y)`` via ``MonitorFromPoint``.

    Thread-scoped per-monitor DPI pin (restored afterwards), mirroring
    :func:`_win_virtual_bounds`, so the metrics are physical pixels even in an
    unaware host — matching the DPI-aware Tk thread that calls this.
    ``MONITOR_DEFAULTTONEAREST`` keeps a point just off every monitor mapped to
    the closest one rather than failing.
    """
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
            ("rcWork", _RECT), ("dwFlags", wintypes.DWORD),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _MONITOR_DEFAULTTONEAREST = 0x00000002

    set_ctx = None
    prev = None
    try:
        set_ctx = user32.SetThreadDpiAwarenessContext
        set_ctx.restype = ctypes.c_void_p
        set_ctx.argtypes = [ctypes.c_void_p]
        for context in (-4, -3):  # PER_MONITOR_AWARE_V2, then V1 (1607)
            prev = set_ctx(ctypes.c_void_p(context))
            if prev is not None:
                break
    except (OSError, AttributeError):  # pre-1607: read unpinned
        set_ctx = None
    try:
        # HMONITOR is pointer-sized: without an explicit restype ctypes returns
        # a 32-bit c_int and TRUNCATES the handle on Win64. Set ONLY the restype
        # (stable, process-safe) and re-wrap the returned int as an HMONITOR when
        # passing it on — deliberately WITHOUT setting argtypes on the shared
        # GetMonitorInfoW, whose POINTER type would otherwise be bound to this
        # function-local _MONITORINFO class and break _win_primary_origin's own
        # call (a different local class) as well as the next call here.
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        hmon = user32.MonitorFromPoint(
            _POINT(int(x), int(y)), _MONITOR_DEFAULTTONEAREST
        )
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(wintypes.HMONITOR(hmon), ctypes.byref(info)):
            return None
        rc = info.rcWork
        left, top = int(rc.left), int(rc.top)
        width, height = int(rc.right - rc.left), int(rc.bottom - rc.top)
    finally:
        if set_ctx is not None and prev is not None:
            try:
                set_ctx(ctypes.c_void_p(prev))
            except Exception:  # noqa: BLE001,S110 — best-effort DPI-context restore
                pass
    if width <= 0 or height <= 0:
        return None
    return (left, top, width, height)


def _macos_work_area_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """Bounds of the ``CGGetActiveDisplayList`` display under ``(x, y)``.

    Full display bounds (not visible-frame): the macOS bar runs on Qt, which
    resolves the dock/menu-bar-aware available geometry itself, so this Quartz
    helper only needs to answer "which display" for any other caller. Returns
    ``None`` when Quartz is unavailable (headless) or no display matches.
    """
    try:
        from Quartz import (  # noqa: PLC0415
            CGDisplayBounds,
            CGGetActiveDisplayList,
        )
    except Exception:  # noqa: BLE001 — pyobjc not installed
        return None
    err, display_ids, count = CGGetActiveDisplayList(16, None, None)
    if err or not display_ids or not count:
        return None
    fallback: tuple[int, int, int, int] | None = None
    for display in list(display_ids)[:count]:
        r = CGDisplayBounds(display)
        left, top = int(r.origin.x), int(r.origin.y)
        width, height = int(r.size.width), int(r.size.height)
        if width <= 0 or height <= 0:
            continue
        rect = (left, top, width, height)
        if fallback is None:
            fallback = rect
        if left <= x < left + width and top <= y < top + height:
            return rect
    return fallback


def _x11_work_area_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    """Rectangle of the ``xrandr`` monitor under ``(x, y)``.

    Full monitor geometry (no panel subtraction — X11 has no single portable
    work-area query); the bar's own bottom gap keeps it clear of a bottom
    panel in practice. Returns ``None`` when ``xrandr`` is missing or no
    monitor contains the point.
    """
    import subprocess  # noqa: PLC0415

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    if shutil.which("xrandr") is None:
        return None
    try:
        out = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=3.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        ).stdout
    except Exception:  # noqa: BLE001 — xrandr missing / no display
        return None
    fallback: tuple[int, int, int, int] | None = None
    for line in out.splitlines():
        if " connected " not in line:
            continue
        for tok in line.split():
            # A geometry token looks like "3840x2160+1920+0".
            if "x" not in tok or "+" not in tok:
                continue
            try:
                size, off_x, off_y = tok.split("+")
                width, height = (int(v) for v in size.split("x"))
                left, top = int(off_x), int(off_y)
            except (IndexError, ValueError):
                break
            if width <= 0 or height <= 0:
                break
            rect = (left, top, width, height)
            if fallback is None:
                fallback = rect
            if left <= x < left + width and top <= y < top + height:
                return rect
            break  # only the first geometry token on a "connected" line
    return fallback


def native_primary_origin() -> tuple[int, int] | None:
    """Best-effort virtual-desktop top-left ``(x, y)`` of the OS primary monitor,
    or ``None`` when it cannot be determined (headless / Wayland / missing libs).

    Per-OS: Windows ``MONITORINFOF_PRIMARY``; macOS ``CGMainDisplayID`` bounds;
    X11 ``XRRGetOutputPrimary``. Never raises."""
    try:
        if sys.platform == "win32":
            return _win_primary_origin()
        if sys.platform == "darwin":
            return _macos_primary_origin()
        # Linux / other POSIX.
        from jarvis.platform.probes import is_wayland  # noqa: PLC0415

        if is_wayland():
            return None  # no reliable global monitor geometry under Wayland
        return _x11_primary_origin()
    except Exception:  # noqa: BLE001 — best-effort; a probe failure is never fatal
        return None


def _win_primary_origin() -> tuple[int, int] | None:
    """Origin of the monitor flagged ``MONITORINFOF_PRIMARY`` (always (0,0) on
    Windows by definition, but queried natively rather than assumed)."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
            ("rcWork", _RECT), ("dwFlags", wintypes.DWORD),
        ]

    _MONITORINFOF_PRIMARY = 0x1
    found: dict[str, tuple[int, int]] = {}

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(_RECT), wintypes.LPARAM,
    )

    def _cb(hmon: Any, _hdc: Any, _lprc: Any, _data: Any) -> int:
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            if info.dwFlags & _MONITORINFOF_PRIMARY:
                found["origin"] = (int(info.rcMonitor.left), int(info.rcMonitor.top))
                return 0  # stop enumeration
        return 1

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_cb), 0)
    return found.get("origin")


def _macos_primary_origin() -> tuple[int, int] | None:
    """Origin of ``CGMainDisplayID``. Needs Quartz (``[desktop-macos]`` extra);
    returns ``None`` when absent (e.g. headless)."""
    try:
        from Quartz import (  # noqa: PLC0415
            CGDisplayBounds,
            CGMainDisplayID,
        )
    except Exception:  # noqa: BLE001 — pyobjc not installed
        return None
    bounds = CGDisplayBounds(CGMainDisplayID())
    return (int(bounds.origin.x), int(bounds.origin.y))


def _x11_primary_origin() -> tuple[int, int] | None:
    """Origin of the XRandR primary output. Uses ``xrandr`` if present; returns
    ``None`` when unavailable. Avoids a hard Xlib dependency (cloud-first base)."""
    import subprocess  # noqa: PLC0415

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    try:
        out = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=3.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        ).stdout
    except Exception:  # noqa: BLE001 — xrandr missing / no display
        return None
    # A primary line looks like: "DP-1 connected primary 3840x2160+1920+0 ..."
    for line in out.splitlines():
        if " connected primary " not in line:
            continue
        for tok in line.split():
            if "+" in tok and "x" in tok:
                try:
                    geom = tok.split("+")
                    return (int(geom[1]), int(geom[2]))
                except (IndexError, ValueError):
                    return None
    return None
