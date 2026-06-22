"""Swap the OS arrow cursor to a black-yellow Jarvis-branded one while
Computer-Use is acting, then restore the user's default after the mission
ends — or, as a safety net, after a 30 s idle window with no further pings.

Why this is the only way to make "Jarvis is at the wheel" visible:
    Windows draws the OS cursor on top of every window, even topmost layered
    ones. An overlay cannot visually replace it. ``SetSystemCursor`` swaps
    the system cursor handle itself, so every app in the user's session
    shows the gold Jarvis arrow while a Computer-Use mission runs.

Two activation paths feed this lifecycle:

  * :func:`ping_jarvis_cursor` — fired from
    ``jarvis.control.cursor_motion.glide_os_cursor``, i.e. on every real
    mouse-move under Jarvis. Defence-in-depth.
  * :func:`session_bracket` — wraps
    ``jarvis.harness.screenshot_only_loop.run_cu_loop`` (the new screenshot-
    only Computer-Use loop). Activates at the very first instant of the
    mission, before the first screenshot — so the user does not stare at
    their default cursor for 3-5 s while the agent "thinks" before the
    first click. Restore is instant on session exit (success / fail / crash)
    instead of waiting for the 30 s idle timer.

Safety:
    A stuck Jarvis cursor would survive a crash and confuse the user across
    sessions, so we restore on (a) explicit ``shutdown``, (b) idle timer,
    (c) ``atexit``, and (d) at every cursor build attempt we pre-run a
    restore once — so any leftover Jarvis cursor from a previous crashed
    process is cleared at boot. Tests inject the activate / restore /
    scheduler seams so this all runs on a headless CI box too.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import sys
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 30 s, not 1.5 s: even with session_bracket as the primary activation path,
# the per-action ping must not flicker back to the user's default cursor
# during natural thinking pauses (POAV plan-call latency 2-5 s, sometimes
# longer on slow providers).
DEFAULT_IDLE_MS = 30_000

# Win32 constants (winuser.h).
_OCR_NORMAL = 32512
_SPI_SETCURSORS = 0x0057
_SPIF_SENDCHANGE = 0x02


# ---------------------------------------------------------------------------
# Lifecycle — the testable, display-independent part.
# ---------------------------------------------------------------------------


class _RealTimerHandle:
    def __init__(self, timer: threading.Timer) -> None:
        self._timer = timer

    def cancel(self) -> None:
        self._timer.cancel()


def _default_schedule(ms: int, cb: Callable[[], None]) -> _RealTimerHandle:
    timer = threading.Timer(ms / 1000.0, cb)
    timer.daemon = True
    timer.start()
    return _RealTimerHandle(timer)


class JarvisSystemCursor:
    """Swap the OS arrow to a Jarvis cursor on demand, restore after idle.

    ``ping()`` is hot-path-cheap: first ping activates (calls ``activate_fn``),
    every subsequent ping just refreshes the idle timer. The timer fires
    :meth:`_idle_fire` once after ``idle_ms`` of silence; restore is
    idempotent. ``shutdown()`` is the same restore path, safe to call from
    atexit / signal handlers / on process tear-down.
    """

    def __init__(
        self,
        *,
        activate_fn: Callable[[], None],
        restore_fn: Callable[[], None],
        schedule_after: Callable[[int, Callable[[], None]], Any] = _default_schedule,
        idle_ms: int = DEFAULT_IDLE_MS,
    ) -> None:
        self._activate_fn = activate_fn
        self._restore_fn = restore_fn
        self._schedule = schedule_after
        self._idle_ms = idle_ms
        self._lock = threading.RLock()
        self._active = False
        self._timer: Any = None
        self._atexit_registered = False

    def ping(self) -> None:
        with self._lock:
            if not self._active:
                try:
                    self._activate_fn()
                except Exception:  # noqa: BLE001
                    logger.debug("Jarvis cursor activate failed", exc_info=True)
                    return
                self._active = True
                if not self._atexit_registered:
                    atexit.register(self._atexit_restore)
                    self._atexit_registered = True
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except Exception:  # noqa: BLE001
                    pass
            self._timer = self._schedule(self._idle_ms, self._idle_fire)

    def _idle_fire(self) -> None:
        with self._lock:
            if not self._active:
                return
            try:
                self._restore_fn()
            except Exception:  # noqa: BLE001
                logger.debug("Jarvis cursor restore failed", exc_info=True)
            self._active = False
            self._timer = None

    def shutdown(self) -> None:
        with self._lock:
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._timer = None
            if self._active:
                try:
                    self._restore_fn()
                except Exception:  # noqa: BLE001
                    pass
                self._active = False

    def _atexit_restore(self) -> None:
        # Interpreter shutdown — locks may be unsafe. Best-effort only.
        if self._active:
            try:
                self._restore_fn()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Process-wide accessor + helpers.
# ---------------------------------------------------------------------------


_singleton_lock = threading.Lock()
_singleton: Optional[JarvisSystemCursor] = None


def set_jarvis_system_cursor(c: Optional[JarvisSystemCursor]) -> None:
    global _singleton
    with _singleton_lock:
        _singleton = c


def get_jarvis_system_cursor() -> Optional[JarvisSystemCursor]:
    with _singleton_lock:
        return _singleton


def ping_jarvis_cursor() -> None:
    """Hot-path safe ping: no-op if no cursor lifecycle is installed."""
    c = get_jarvis_system_cursor()
    if c is None:
        return
    try:
        c.ping()
    except Exception:  # noqa: BLE001 — the overlay must never break a click
        pass


@contextlib.asynccontextmanager
async def session_bracket():
    """Activate Jarvis cursor at entry, restore at exit. Wrap a Computer-Use
    mission's main loop with this so the cursor is Jarvis from the very first
    instant — before the first screenshot — and reverts immediately when the
    mission ends (success, failure, or exception). No 3-5 s window of the
    default cursor while the agent plans its first move.

    Logs at INFO level on entry/exit so an operator can verify in the log
    that the cursor system actually fired for a given mission — the
    SetSystemCursor swap itself is silent (no log statement on success), so
    without these breadcrumbs there is no way to confirm session activation
    without running a screen recorder.

    Any failure from the installed cursor (a missing display, a Win32 panic,
    a broken activate_fn) is swallowed: the mission must never break because
    the visual indicator did.
    """
    c = get_jarvis_system_cursor()
    if c is not None:
        logger.info("session_bracket entered (Jarvis cursor armed for this mission)")
        try:
            c.ping()
        except Exception:  # noqa: BLE001
            logger.debug("session_bracket: ping failed", exc_info=True)
    else:
        logger.info("session_bracket entered (no cursor singleton — no-op)")
    try:
        yield
    finally:
        if c is not None:
            try:
                c.shutdown()
            except Exception:  # noqa: BLE001
                logger.debug("session_bracket: shutdown failed", exc_info=True)
        logger.info("session_bracket exited (Jarvis cursor restored)")


# ---------------------------------------------------------------------------
# Real Win32 + Pillow cursor builder.
# ---------------------------------------------------------------------------


def _draw_jarvis_arrow_rgba(size: int = 48) -> bytes:
    """Render the brand-aligned Jarvis cursor as RGBA bytes (top-down).

    Brand palette per ``~/.claude/brand-guidelines.md``: Charcoal ``#0e0d0c``
    + warm gold ``#e7c46e``, with a **3 px stroke** aesthetic. One solid
    charcoal arrow with one bold gold outline — no inner tip-slice, no drop
    shadow, no extra highlight. The cleanness *is* the brand: at cursor size,
    every extra mark turns into visual noise and the silhouette starts to
    read as sloppy.
    """
    from PIL import Image, ImageDraw

    GOLD = (231, 196, 110, 255)      # brand gold #e7c46e
    CHARCOAL = (14, 13, 12, 255)     # brand charcoal #0e0d0c

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Classic arrow silhouette on a 48 px canvas, hotspot (2, 2).
    # Clockwise from the tip: left edge, inner notch, tail down + back up,
    # body bottom, diagonal back to tip. Non-self-intersecting.
    arrow = [
        (2, 2),
        (2, 28),
        (11, 21),
        (16, 34),
        (21, 31),
        (15, 20),
        (28, 20),
    ]

    # Charcoal fill + 3 px gold stroke — that is the whole design.
    draw.polygon(arrow, fill=CHARCOAL, outline=GOLD, width=3)

    return img.tobytes()  # RGBA, top-down


def _create_hcursor_from_rgba(
    rgba: bytes, *, width: int = 48, height: int = 48,
    hotspot_x: int = 2, hotspot_y: int = 2,
) -> int:
    """Build an HCURSOR from RGBA pixels via ``CreateIconIndirect``.

    Uses ``CreateDIBSection`` to produce a 32-bit top-down BGRA DIB; the AND
    mask is monochrome all-zeros so Windows blends through the colour
    bitmap's alpha channel (the standard 32-bit colour-cursor convention).
    Returns 0 on any failure; the lifecycle treats that as "stay inactive".
    """
    import ctypes
    from ctypes import wintypes

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    # 64-bit ctypes correctness: GDI/USER handles come back through the HBITMAP /
    # HANDLE restypes as FULL-WIDTH Python ints. Any handle passed back into a
    # Win32 call WITHOUT ``argtypes`` is marshalled as the default ``c_int``
    # (32-bit), which OVERFLOWS for a >2^31 handle ("OverflowError: int too long
    # to convert") and made ``_create_hcursor_from_rgba`` raise at the cleanup
    # ``DeleteObject`` -- so the whole cursor swap failed silently (logged only
    # at DEBUG) and the user kept their default cursor during every CU mission.
    # Pin argtypes on the handle-consuming calls (BUG: 64-bit cursor swap).
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL

    # RGBA -> BGRA byte swap for Windows DIB order.
    bgra = bytearray(rgba)
    for i in range(0, len(bgra), 4):
        bgra[i], bgra[i + 2] = bgra[i + 2], bgra[i]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down, matches PIL.tobytes() order
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    ppv = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    hbm_color = gdi32.CreateDIBSection(
        None, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0,
    )
    if not hbm_color or not ppv.value:
        return 0
    ctypes.memmove(ppv, bytes(bgra), len(bgra))

    mask_row = ((width + 31) // 32) * 4
    mask_bytes = bytes(mask_row * height)
    gdi32.CreateBitmap.restype = wintypes.HBITMAP
    hbm_mask = gdi32.CreateBitmap(width, height, 1, 1, mask_bytes)
    if not hbm_mask:
        gdi32.DeleteObject(hbm_color)
        return 0

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    ii = ICONINFO()
    ii.fIcon = False
    ii.xHotspot = hotspot_x
    ii.yHotspot = hotspot_y
    ii.hbmMask = hbm_mask
    ii.hbmColor = hbm_color

    user32.CreateIconIndirect.restype = wintypes.HANDLE
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    hcur = user32.CreateIconIndirect(ctypes.byref(ii))

    # CreateIconIndirect copies the bitmaps; we own and must delete originals.
    gdi32.DeleteObject(hbm_color)
    gdi32.DeleteObject(hbm_mask)

    return int(hcur) if hcur else 0


def _real_activate() -> None:
    """Build the Jarvis cursor and install it as the OS arrow.

    Note: ``SetSystemCursor`` takes ownership of the HCURSOR (Windows
    destroys it on the next swap), so we rebuild every activate. Building
    a 48 px DIB is microseconds — negligible vs. the action that triggered
    the swap.
    """
    import ctypes
    from ctypes import wintypes

    rgba = _draw_jarvis_arrow_rgba(48)
    hcur = _create_hcursor_from_rgba(rgba, width=48, height=48, hotspot_x=2, hotspot_y=2)
    if not hcur:
        raise RuntimeError("CreateIconIndirect returned 0")
    user32 = ctypes.windll.user32
    # argtypes pinned so the 64-bit HCURSOR is passed as a real HANDLE, not
    # truncated to a 32-bit c_int (same OverflowError class as the DeleteObject
    # cleanup in _create_hcursor_from_rgba). Without this the swap silently fails.
    user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.UINT]
    user32.SetSystemCursor.restype = wintypes.BOOL
    if not user32.SetSystemCursor(hcur, _OCR_NORMAL):
        raise ctypes.WinError(ctypes.get_last_error())


def _real_restore() -> None:
    import ctypes

    ctypes.windll.user32.SystemParametersInfoW(
        _SPI_SETCURSORS, 0, None, _SPIF_SENDCHANGE,
    )


def build_real_jarvis_cursor(*, idle_ms: int = DEFAULT_IDLE_MS) -> Optional[JarvisSystemCursor]:
    """Build the production cursor lifecycle, or ``None`` when unavailable.

    Returns ``None`` on non-Windows and when Pillow / Win32 are not usable —
    the caller falls back to a no-op (cloud-first / headless VPS doctrine).
    Pre-runs a restore so any leftover Jarvis cursor from a previous crashed
    process is cleared at boot.
    """
    if sys.platform != "win32":
        return None
    try:
        _real_restore()
    except Exception:  # noqa: BLE001
        logger.info("SystemParametersInfo unavailable; Jarvis cursor disabled.")
        return None
    return JarvisSystemCursor(
        activate_fn=_real_activate,
        restore_fn=_real_restore,
        idle_ms=idle_ms,
    )


__all__ = [
    "DEFAULT_IDLE_MS",
    "JarvisSystemCursor",
    "build_real_jarvis_cursor",
    "get_jarvis_system_cursor",
    "ping_jarvis_cursor",
    "session_bracket",
    "set_jarvis_system_cursor",
]
