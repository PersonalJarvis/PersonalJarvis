"""Tk render window for the "Jarvis virtual mouse".

A frameless, always-on-top, **click-through** window spanning the whole
virtual desktop. While Computer-Use acts, the real OS cursor glides to each
target (see ``jarvis/control/cursor_motion.py``) and this window paints a gold
halo that tracks it plus an expanding click pulse where it clicks — the same
"you can watch what it does" affordance as the Claude-in-Chrome cursor.

Why a separate window from the orb:
    The orb is draggable, so it is *not* input-transparent. This overlay sits
    exactly where the agent clicks, so it MUST pass every input through
    (``WS_EX_LAYERED | WS_EX_TRANSPARENT``) — otherwise it would eat the very
    clicks it is visualising.

Threading:
    Tk is single-threaded, so the window owns its own ``tk.Tk()`` root in a
    daemon thread. The public ``show_*`` methods are called from arbitrary
    worker threads (the click tools run under ``asyncio.to_thread``); they only
    mutate lock-guarded state, and the Tk ``after``-driven tick loop does all
    drawing.

Cloud-first:
    Construction is best-effort. If Tk cannot open a display (headless VPS), or
    Win32 is unavailable, :meth:`start` returns ``False`` and the process keeps
    using the :class:`NullVirtualCursor` no-op — a real click is never blocked.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

from jarvis.overlay.virtual_cursor import pulse_state, set_virtual_cursor

logger = logging.getLogger(__name__)

# Magenta chroma key — same value the orb uses, so transparent pixels are also
# click-through under ``-transparentcolor``.
COLOR_KEY_HEX = "#FF00FF"

# Brand gold (matches the existing cursor-trail / edge-glow palette).
GOLD = "#FFC700"
GOLD_DEEP = "#E7A100"
INK = "#1A1206"

# Window extended styles (winuser.h).
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_GWL_EXSTYLE = -20

# Layered-window attribute flags (winuser.h).
_LWA_COLORKEY = 0x00000001

# SetWindowPos flags (winuser.h) — commit the ex-style change without moving,
# resizing, raising, or focusing the overlay.
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

# COLORREF packs as 0x00BBGGRR. Magenta = R=FF, G=00, B=FF → 0x00FF00FF.
# Must match COLOR_KEY_HEX above (what Tk paints as bg and what
# -transparentcolor keys out).
_MAGENTA_COLORREF = 0x00FF00FF

# Visuals.
_HALO_RADIUS = 26          # gold ring radius around the cursor (px)
_PULSE_MAX_RADIUS = 46     # click pulse expands to this (px)
_PULSE_DURATION_MS = 480
_HALO_LINGER_MS = 1600     # halo fades out this long after the last update
_TICK_MS = 16              # ~60 fps


def _virtual_screen_rect() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole virtual desktop, physical px."""
    import ctypes

    user32 = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return int(left), int(top), int(width), int(height)


def _make_click_through(hwnd: int) -> None:
    """Set WS_EX_LAYERED|TRANSPARENT|TOOLWINDOW|NOACTIVATE so the whole window
    passes input through and never steals focus or shows in alt-tab.

    Win32 quirk (incident 2026-05-26): writing GWL_EXSTYLE on a layered window
    silently invalidates the cached chroma-key that Tk set via
    ``-transparentcolor`` (which internally calls SetLayeredWindowAttributes).
    Without re-applying the chroma-key the overlay paints opaque, and a
    fullscreen-virtual-desktop layered surface can drag DWM compositing down
    until every monitor goes black — only a reboot recovers, because the HWND
    survives as long as the owner ``pythonw.exe`` does. So we re-apply the
    chroma-key and force a frame-change immediately after the style flip.

    Regression guard: ``tests/overlay/test_virtual_cursor_click_through.py``.
    """
    import ctypes

    user32 = ctypes.windll.user32
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    style = get_long(hwnd, _GWL_EXSTYLE)
    style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
    set_long(hwnd, _GWL_EXSTYLE, style)

    # Re-apply the chroma-key — SetWindowLong above silently dropped it.
    user32.SetLayeredWindowAttributes(hwnd, _MAGENTA_COLORREF, 0, _LWA_COLORKEY)
    # Commit the ex-style change now so the layered surface does not render
    # stale until the next natural move/resize.
    user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    )


def _alpha_to_stipple(alpha: float) -> str:
    """Map an alpha (0..1) to a Tk stipple bitmap — Tk canvas has no real
    per-item alpha, so density-stippling fakes the fade."""
    if alpha >= 0.75:
        return ""           # solid
    if alpha >= 0.5:
        return "gray75"
    if alpha >= 0.25:
        return "gray50"
    return "gray25"


class TkVirtualCursor:
    """Display-backed :class:`VirtualCursor`. Registers itself as the active
    cursor on :meth:`start` and restores the no-op on :meth:`shutdown`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._origin = (0, 0)               # virtual-screen left/top
        self._cursor_canvas: tuple[int, int] | None = None
        self._cursor_ts_ms: float = 0.0
        # Active click pulses: (start_ms, canvas_x, canvas_y).
        self._pulses: list[tuple[float, int, int]] = []
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._ok = False                    # True only if the window came up
        self._stop = threading.Event()
        self._root = None  # tk.Tk
        self._canvas = None
        # Captured in _run so shutdown can call ShowWindow(SW_HIDE) as a
        # cross-thread last resort if the Tk loop is wedged.
        self._hwnd: int = 0

    # -- lifecycle ------------------------------------------------------------

    def start(self, *, timeout_s: float = 5.0) -> bool:
        """Start the Tk thread. Returns True once the window is up."""
        if sys.platform == "darwin":
            # Aqua-Tk is main-thread-only on macOS; a Tk root on this worker
            # thread aborts the whole process natively (BUG-057 class).
            logger.info(
                "Virtual cursor overlay not started: macOS allows Tk windows "
                "on the main thread only — no-op."
            )
            return False
        if self._thread is not None:
            return self._ready.is_set()
        self._thread = threading.Thread(
            target=self._run, name="virtual-cursor", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=timeout_s)
        if self._ok:
            set_virtual_cursor(self)
        return self._ok

    def shutdown(self) -> None:
        """Tear down the overlay.

        Drives ``root.destroy()`` from the Tk thread via ``after`` so it
        happens on the *next* idle slot, not on the next render tick — the
        old code waited for ``_tick`` which never fired if DWM was wedged,
        leaving a persistent black overlay until reboot (incident 2026-05-26).

        If the Tk thread does not exit within the join window, falls back to
        a direct ``ShowWindow(HWND, SW_HIDE)`` Win32 call: ``DestroyWindow``
        is not safe cross-thread, but ``ShowWindow`` is documented thread-safe
        and at least removes the overlay from the screen.
        """
        self._stop.set()
        set_virtual_cursor(None)

        # Step 1: ask Tk to destroy itself from its own thread. ``after(0,
        # ...)`` is thread-safe (Tcl's event queue accepts cross-thread
        # appends) and runs ASAP — beating the render tick.
        root = self._root
        if root is not None:
            try:
                root.after(0, self._tk_destroy)
            except Exception:  # noqa: BLE001 — root may already be torn down
                logger.debug("virtual-cursor after(0, destroy) failed", exc_info=True)

        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

        # Step 2: defense-in-depth. If the Tk thread is wedged, the daemon
        # will be hard-killed on process exit — but on Windows the HWND only
        # vanishes when the owning *process* dies, not when the thread does.
        # Hide it now so it cannot remain visible as a black overlay even if
        # something else keeps pythonw.exe alive (audio listener, asyncio
        # task, etc.).
        if thread is not None and thread.is_alive() and self._hwnd:
            logger.warning(
                "virtual-cursor Tk thread did not exit within 5s; hiding "
                "HWND %#x via ShowWindow(SW_HIDE) as a last resort.",
                self._hwnd,
            )
            try:
                import ctypes

                _SW_HIDE = 0
                ctypes.windll.user32.ShowWindow(self._hwnd, _SW_HIDE)
            except Exception:  # noqa: BLE001 — best-effort
                logger.debug("ShowWindow(SW_HIDE) fallback failed", exc_info=True)

        self._thread = None

    def _tk_destroy(self) -> None:
        """Tear the root window down from the Tk thread.

        Scheduled via ``root.after`` from :meth:`shutdown`, so this always
        runs on the Tk event-loop thread (Tcl's invariant).
        """
        try:
            if self._root is not None:
                self._root.destroy()
        except Exception:  # noqa: BLE001 — already torn down
            pass

    # -- VirtualCursor API (called from worker threads) -----------------------

    def _to_canvas(self, x: int, y: int) -> tuple[int, int]:
        ox, oy = self._origin
        return int(x) - ox, int(y) - oy

    def show_move(self, x: int, y: int, *, monitor: int = 0) -> None:
        with self._lock:
            self._cursor_canvas = self._to_canvas(x, y)
            self._cursor_ts_ms = time.monotonic() * 1000.0

    def show_path_point(self, x: int, y: int) -> None:
        self.show_move(x, y)

    def show_click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
        monitor: int = 0,
    ) -> None:
        cx, cy = self._to_canvas(x, y)
        now = time.monotonic() * 1000.0
        with self._lock:
            self._cursor_canvas = (cx, cy)
            self._cursor_ts_ms = now
            self._pulses.append((now, cx, cy))
            if double:
                self._pulses.append((now + 90.0, cx, cy))

    def clear(self) -> None:
        with self._lock:
            self._cursor_canvas = None
            self._pulses.clear()

    # -- Tk thread ------------------------------------------------------------

    def _run(self) -> None:
        try:
            import tkinter as tk

            left, top, width, height = _virtual_screen_rect()
            self._origin = (left, top)

            root = tk.Tk()
            root.title("JarvisVirtualCursor")
            root.overrideredirect(True)
            try:
                root.tk.call("tk", "scaling", 1.0)  # 1:1 px, avoid DPI rescale
            except Exception:  # noqa: BLE001
                pass
            root.wm_attributes("-topmost", True)
            root.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
            root.configure(bg=COLOR_KEY_HEX)
            root.geometry(f"{width}x{height}+{left}+{top}")

            canvas = tk.Canvas(
                root, bg=COLOR_KEY_HEX, highlightthickness=0, borderwidth=0,
                width=width, height=height,
            )
            canvas.pack(fill="both", expand=True)

            self._root = root
            self._canvas = canvas

            root.update_idletasks()
            try:
                hwnd = int(root.winfo_id())
                self._hwnd = hwnd  # captured for shutdown's cross-thread fallback
                _make_click_through(hwnd)
            except Exception:  # noqa: BLE001
                logger.debug("virtual-cursor click-through setup failed", exc_info=True)

            self._ok = True
            self._ready.set()
            self._schedule_tick()
            root.mainloop()
        except Exception:  # noqa: BLE001 — headless / no display: stay a no-op
            logger.info("virtual-cursor window unavailable; staying no-op", exc_info=True)
            self._ok = False
            self._ready.set()  # unblock start(); caller falls back to NullVirtualCursor

    def _schedule_tick(self) -> None:
        if self._root is not None and not self._stop.is_set():
            self._root.after(_TICK_MS, self._tick)

    def _tick(self) -> None:
        if self._stop.is_set():
            try:
                self._root.destroy()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            self._render()
        except Exception:  # noqa: BLE001
            logger.debug("virtual-cursor render error", exc_info=True)
        self._schedule_tick()

    def _render(self) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        now = time.monotonic() * 1000.0
        with self._lock:
            cursor = self._cursor_canvas
            cursor_age = now - self._cursor_ts_ms
            # Drop expired pulses.
            self._pulses = [
                p for p in self._pulses if (now - p[0]) <= _PULSE_DURATION_MS
            ]
            pulses = list(self._pulses)

        canvas.delete("all")

        # Halo ring tracking the cursor — fades out after the last update so it
        # does not sit on screen forever once the agent stops acting.
        if cursor is not None and cursor_age <= _HALO_LINGER_MS:
            cx, cy = cursor
            halo_alpha = 1.0 - (cursor_age / _HALO_LINGER_MS)
            stipple = _alpha_to_stipple(halo_alpha)
            r = _HALO_RADIUS
            # Dark backing ring (readable on light UIs) + gold ring on top.
            canvas.create_oval(
                cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1,
                outline=INK, width=4, stipple=stipple,
            )
            canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline=GOLD, width=3, stipple=stipple,
            )
            # Small center dot marks the exact hotspot.
            canvas.create_oval(
                cx - 3, cy - 3, cx + 3, cy + 3, fill=GOLD, outline=GOLD_DEEP,
            )

        # Click pulses — expanding, fading rings.
        for start_ms, px, py in pulses:
            state = pulse_state(
                now - start_ms,
                duration_ms=_PULSE_DURATION_MS,
                max_radius=_PULSE_MAX_RADIUS,
            )
            if state is None:
                continue
            radius, alpha = state
            if radius < 1:
                continue
            stipple = _alpha_to_stipple(alpha)
            canvas.create_oval(
                px - radius, py - radius, px + radius, py + radius,
                outline=GOLD, width=3, stipple=stipple,
            )


__all__ = ["TkVirtualCursor", "COLOR_KEY_HEX"]
