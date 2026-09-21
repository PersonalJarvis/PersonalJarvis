"""Resize border for a frameless pywebview window on Windows.

The desktop window is created with ``FormBorderStyle.None``. Windows then
refuses edge resizing, and a maximized window is laid out over the taskbar.
HTML draws the caption buttons and drags via pywebview's drag region, so this
module never returns ``HTCAPTION`` and never paints caption buttons. Only the
outer border (8px) is a resize grip.

``caption_hit`` is pure and runs on every OS. ``window_is_maximized`` and
``install_resize_frame`` are Win32-only: on macOS and Linux they return false
instead of raising, so a headless box can import the module.

64-bit window procedures are pointer-sized. ``GetWindowLongW`` truncates the
procedure address and the next message crashes the process, so the subclass
uses ``GetWindowLongPtrW`` / ``SetWindowLongPtrW`` / ``CallWindowProcW`` with
``ctypes.c_longlong`` results and ``ctypes.c_void_p`` window handles. The
``WINFUNCTYPE`` thunk is pinned for the process lifetime: if it is collected
while Windows still holds it, the process crashes on the next message.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "caption_hit",
    "install_resize_frame",
    "native_hwnd",
    "window_is_maximized",
]

# Win32 hit-test codes. None from caption_hit means HTCLIENT: the page handles it.
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084

# Sized-frame bits. Windows ignores WS_THICKFRAME unless the caption family is
# present too. WM_NCCALCSIZE then returns the whole rectangle as client area,
# so the native title bar (and its HTCAPTION strip) never appears.
_WS_THICKFRAME = 0x00040000
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_RESIZE_STYLE = _WS_THICKFRAME | _WS_CAPTION | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX

_GWL_STYLE = -16
_GWLP_WNDPROC = -4

_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_SWP_FRAME_ONLY = _SWP_FRAMECHANGED | _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER

_MONITOR_DEFAULTTONEAREST = 2
_SHADOW_MARGIN = 1

# Serializes installation only. SetWindowPos sends WM_NCCALCSIZE on this same
# thread before it returns, so the window procedure must not take this lock.
_install_lock = threading.Lock()
_api_lock = threading.Lock()
_api: _Win32 | None = None
_pinned: dict[int, _PinnedSubclass] = {}


@dataclass(slots=True)
class _PinnedSubclass:
    """Keeps one subclass alive for the process lifetime."""

    callback: Any
    previous: int


@dataclass(slots=True)
class _Win32:
    """Private user32/dwmapi bindings.

    A private ``WinDLL`` is required: setting pointer-sized ``argtypes`` on the
    shared ``ctypes.windll.user32`` would change every other caller in the
    process, and several of those still pass plain integers.
    """

    ctypes: Any
    user32: Any
    kernel32: Any
    wndproc_type: Any
    point: Any
    rect: Any
    monitor_info: Any
    margins: Any
    dwm_extend: Any


def caption_hit(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    border: int = 8,
    maximized: bool = False,
) -> int | None:
    """Map a client point to a resize ``HT*`` code, or None for the page.

    Corners are tested before edges so a diagonal grip wins over a single
    edge. A maximized window and a non-positive client size have no grip:
    those clicks belong to the page (``HTCLIENT``).
    """
    if maximized or width <= 0 or height <= 0:
        return None
    on_left = x < border
    on_right = x >= width - border
    on_top = y < border
    on_bottom = y >= height - border
    if on_top and on_left:
        return _HTTOPLEFT
    if on_top and on_right:
        return _HTTOPRIGHT
    if on_bottom and on_left:
        return _HTBOTTOMLEFT
    if on_bottom and on_right:
        return _HTBOTTOMRIGHT
    if on_left:
        return _HTLEFT
    if on_right:
        return _HTRIGHT
    if on_top:
        return _HTTOP
    if on_bottom:
        return _HTBOTTOM
    return None


def window_is_maximized(hwnd: int) -> bool:
    """Return whether Windows reports ``hwnd`` as zoomed (maximized).

    Non-Windows, a null handle, and any native failure are false. Never raises.
    """
    if os.name != "nt":
        return False
    key = _coerce_hwnd(hwnd)
    if not key:
        return False
    try:
        api = _load_api()
        return bool(api.user32.IsZoomed(key))
    except Exception:  # noqa: BLE001 - maximized state is a probe, not a crash
        _log_exception("IsZoomed failed")
        return False


def native_hwnd(window: Any) -> int | None:
    """Return the Win32 HWND from a pywebview window, or None.

    ``Handle.ToInt64()`` keeps a full 64-bit pointer. ``int(handle)`` is only
    the fallback for a host that already exposes a plain integer. Missing
    pieces and failures are None so callers on macOS and Linux can probe
    without a guard. Never raises.
    """
    try:
        native = getattr(window, "native", None)
        if native is None:
            return None
        handle = getattr(native, "Handle", None)
    except Exception:  # noqa: BLE001 - a window probe must not raise
        _log_exception("native window handle is unavailable")
        return None
    if handle is None:
        return None
    try:
        to_int64 = getattr(handle, "ToInt64", None)
        if callable(to_int64):
            return int(to_int64())
        return int(handle)
    except Exception:  # noqa: BLE001 - a window probe must not raise
        _log_exception("native window handle is unavailable")
        return None


def install_resize_frame(hwnd: int) -> bool:
    """Subclass ``hwnd`` so the outer border resizes and maximize keeps the taskbar.

    Returns true only when the subclass is installed. A second call for the
    same hwnd returns true and does not stack another procedure. Non-Windows,
    a null handle, and a handle that is not a real window return false.
    Never raises. A shadow (1px DWM margins) is best-effort: if it fails the
    subclass still stands.
    """
    if os.name != "nt":
        return False
    key = _coerce_hwnd(hwnd)
    if not key:
        return False
    with _install_lock:
        if key in _pinned:
            return True
        try:
            from jarvis.core.win32_dpi import ensure_dpi_awareness  # noqa: PLC0415

            # Work-area and hit-test pixels must match the window's own pixels.
            ensure_dpi_awareness()
            api = _load_api()
            if not api.user32.IsWindow(key):
                logger.debug("resize frame not installed; hwnd %s is not a window", key)
                return False
            return _install_locked(api, key)
        except Exception:  # noqa: BLE001 - install is a capability probe
            _log_exception("install_resize_frame failed")
            return False


def _as_int(value: object) -> int:
    """Return ``value`` when it is a real int.

    Win32 callback arguments arrive as ints. ``bool`` is rejected because it
    is an ``int`` subclass and must not become hwnd 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an integer, got {type(value).__name__}")
    return value


def _coerce_hwnd(hwnd: object) -> int | None:
    try:
        return _as_int(hwnd)
    except TypeError:
        _log_exception("window handle is not an integer")
        return None


def _log_exception(message: str) -> None:
    """Log the active exception without letting logging itself escape.

    Called only from an ``except`` block. A window procedure that raises is
    process-fatal, and ``install_resize_frame`` is documented to never raise.
    """
    try:
        logger.exception(message)
    except Exception:  # logging itself failed; the caller still must not raise
        return


def _load_api() -> _Win32:
    global _api
    cached = _api
    if cached is not None:
        return cached
    with _api_lock:
        cached = _api
        if cached is not None:
            return cached
        built = _build_api()
        _api = built
        return built


def _build_api() -> _Win32:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    # Private copies so pointer-sized prototypes stay local to this module.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    get_long = getattr(user32, "GetWindowLongPtrW", None)
    set_long = getattr(user32, "SetWindowLongPtrW", None)
    if get_long is None or set_long is None:
        # GetWindowLongW is the 32-bit macro and truncates a 64-bit procedure.
        raise OSError("GetWindowLongPtrW is required; refusing GetWindowLongW")

    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_long.restype = ctypes.c_longlong
    set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
    set_long.restype = ctypes.c_longlong

    user32.CallWindowProcW.argtypes = [
        ctypes.c_longlong,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_longlong,
        ctypes.c_longlong,
    ]
    user32.CallWindowProcW.restype = ctypes.c_longlong
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_int
    user32.IsZoomed.argtypes = [ctypes.c_void_p]
    user32.IsZoomed.restype = ctypes.c_int
    user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_int

    kernel32.SetLastError.argtypes = [ctypes.c_ulong]
    kernel32.SetLastError.restype = None

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    class MARGINS(ctypes.Structure):
        _fields_ = [
            ("cxLeftWidth", ctypes.c_int),
            ("cxRightWidth", ctypes.c_int),
            ("cyTopHeight", ctypes.c_int),
            ("cyBottomHeight", ctypes.c_int),
        ]

    user32.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(POINT)]
    user32.ScreenToClient.restype = ctypes.c_int
    user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = ctypes.c_int
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = ctypes.c_int

    # LRESULT and LPARAM are signed so WM_NCHITTEST screen coordinates survive.
    # WPARAM uses the same signed width: forwarding an unsigned high bit through
    # CallWindowProcW's c_longlong would raise OverflowError inside the callback.
    wndproc_type = ctypes.WINFUNCTYPE(
        ctypes.c_longlong,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_longlong,
        ctypes.c_longlong,
    )

    return _Win32(
        ctypes=ctypes,
        user32=user32,
        kernel32=kernel32,
        wndproc_type=wndproc_type,
        point=POINT,
        rect=RECT,
        monitor_info=MONITORINFO,
        margins=MARGINS,
        dwm_extend=_load_dwm_extend(ctypes, MARGINS),
    )


def _load_dwm_extend(ctypes: Any, margins: Any) -> Any:
    """Return DwmExtendFrameIntoClientArea, or None when dwmapi is missing."""
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        extend = dwmapi.DwmExtendFrameIntoClientArea
        extend.argtypes = [ctypes.c_void_p, ctypes.POINTER(margins)]
        extend.restype = ctypes.c_long
    except (OSError, AttributeError):
        _log_exception("dwmapi shadow is unavailable")
        return None
    return extend


def _install_locked(api: _Win32, hwnd: int) -> bool:
    old_style = _get_long(api, hwnd, _GWL_STYLE)
    if old_style is None:
        return False
    style_bits = old_style & 0xFFFFFFFF
    new_style = style_bits | _RESIZE_STYLE
    if not _set_long(api, hwnd, _GWL_STYLE, new_style):
        return False
    if not _subclass(api, hwnd):
        # Put the previous bits back so a failed install does not leave a caption.
        _set_long(api, hwnd, _GWL_STYLE, style_bits)
        return False
    # Subclass first, then ask Windows to recalculate the frame, so our
    # WM_NCCALCSIZE — not the old procedure — drops the title bar.
    if not _frame_changed(api, hwnd):
        logger.error(
            "SetWindowPos failed to refresh the frame hwnd=%s; resize subclass stays installed",
            hwnd,
        )
    _extend_shadow(api, hwnd)
    return True


def _subclass(api: _Win32, hwnd: int) -> bool:
    previous = _get_long(api, hwnd, _GWLP_WNDPROC)
    if previous is None:
        return False
    if previous == 0:
        logger.error("window procedure pointer is null hwnd=%s", hwnd)
        return False
    callback = api.wndproc_type(_make_proc(api, previous))
    raw = api.ctypes.cast(callback, api.ctypes.c_void_p).value
    if not raw:
        logger.error("resize window procedure thunk is null hwnd=%s", hwnd)
        return False
    # Pin before the swap. A message can arrive on this thread as soon as
    # SetWindowLongPtrW returns, and a collected thunk crashes the process.
    _pinned[hwnd] = _PinnedSubclass(callback=callback, previous=previous)
    try:
        installed = _set_long(api, hwnd, _GWLP_WNDPROC, _signed_long_ptr(int(raw)))
    except Exception:
        _pinned.pop(hwnd, None)
        raise
    if not installed:
        _pinned.pop(hwnd, None)
        return False
    return True


def _make_proc(api: _Win32, previous: int):
    user32 = api.user32

    def proc(hwnd, msg, wparam, lparam):
        try:
            result = _dispatch(api, hwnd, msg, wparam, lparam)
            if result is not None:
                return result
        except Exception:  # noqa: BLE001 - a window procedure must not raise into Win32
            _log_exception("frameless resize wndproc failed")
        try:
            return user32.CallWindowProcW(previous, hwnd, msg, wparam, lparam)
        except Exception:  # noqa: BLE001 - a window procedure must not raise into Win32
            _log_exception("CallWindowProcW failed")
            return 0

    return proc


def _dispatch(api: _Win32, hwnd: object, msg: object, wparam: object, lparam: object) -> int | None:
    message = _as_int(msg)
    if message == _WM_NCCALCSIZE and _as_int(wparam):
        # Returning 0 without the old procedure is what removes the title bar.
        # Falling through would let DefWindowProc reserve the caption strip.
        try:
            if hwnd is not None:
                _apply_client_area(api, _as_int(hwnd), lparam)
        except Exception:  # noqa: BLE001 - keep the borderless client area anyway
            _log_exception("WM_NCCALCSIZE client-area adjustment failed")
        return 0
    if message == _WM_NCHITTEST and hwnd is not None:
        return _resize_hit(api, _as_int(hwnd), lparam)
    return None


def _resize_hit(api: _Win32, hwnd: int, lparam: object) -> int | None:
    point_xy = _screen_point(lparam)
    if point_xy is None:
        return None
    point = api.point(point_xy[0], point_xy[1])
    api.kernel32.SetLastError(0)
    if not api.user32.ScreenToClient(hwnd, api.ctypes.byref(point)):
        logger.error(
            "ScreenToClient failed hwnd=%s error=%s",
            hwnd,
            api.ctypes.get_last_error(),
        )
        return None
    rect = api.rect()
    api.kernel32.SetLastError(0)
    if not api.user32.GetClientRect(hwnd, api.ctypes.byref(rect)):
        logger.error(
            "GetClientRect failed hwnd=%s error=%s",
            hwnd,
            api.ctypes.get_last_error(),
        )
        return None
    return caption_hit(
        int(point.x),
        int(point.y),
        int(rect.right) - int(rect.left),
        int(rect.bottom) - int(rect.top),
        maximized=bool(api.user32.IsZoomed(hwnd)),
    )


def _apply_client_area(api: _Win32, hwnd: int, lparam: object) -> None:
    """Keep the client area equal to the window, or to the work area when zoomed.

    ``rgrc[0]`` arrives as the proposed window rectangle. Leaving it in place
    means there is no native title bar. A zoomed window's proposal covers the
    taskbar, so that case is replaced with the monitor work area.
    """
    if not api.user32.IsZoomed(hwnd):
        return
    monitor = api.user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
    if not monitor:
        logger.error("MonitorFromWindow returned no monitor hwnd=%s", hwnd)
        return
    info = api.monitor_info()
    info.cbSize = api.ctypes.sizeof(info)
    api.kernel32.SetLastError(0)
    if not api.user32.GetMonitorInfoW(monitor, api.ctypes.byref(info)):
        logger.error(
            "GetMonitorInfoW failed hwnd=%s error=%s",
            hwnd,
            api.ctypes.get_last_error(),
        )
        return
    address = _pointer_bits(lparam)
    if address == 0:
        logger.error("WM_NCCALCSIZE lParam is null hwnd=%s", hwnd)
        return
    # rgrc[0] is the first field. Copying the work rectangle onto it avoids
    # the ctypes nested-array copy that drops field writes.
    api.ctypes.memmove(address, api.ctypes.byref(info.rcWork), api.ctypes.sizeof(api.rect))


def _screen_point(lparam: object) -> tuple[int, int] | None:
    """Unpack a WM_NCHITTEST lParam into signed screen pixels."""
    if lparam is None:
        return None
    packed = _pointer_bits(lparam) & 0xFFFFFFFF
    return _signed_i16(packed & 0xFFFF), _signed_i16(packed >> 16)


def _signed_i16(value: int) -> int:
    if value >= 0x8000:
        return value - 0x10000
    return value


def _pointer_bits(value: object) -> int:
    """Unsigned 64-bit pointer bits from a signed LONG_PTR or NULL."""
    if value is None:
        return 0
    bits = _as_int(value)
    if bits < 0:
        bits += 1 << 64
    return bits


def _signed_long_ptr(bits: int) -> int:
    """Fit pointer bits into the signed LONG_PTR SetWindowLongPtrW stores."""
    bits &= (1 << 64) - 1
    if bits >= 1 << 63:
        return bits - (1 << 64)
    return bits


def _get_long(api: _Win32, hwnd: int, index: int) -> int | None:
    api.kernel32.SetLastError(0)
    value = int(api.user32.GetWindowLongPtrW(hwnd, index))
    err = api.ctypes.get_last_error()
    # A zero previous value is success only when the thread error was cleared.
    if value == 0 and err != 0:
        logger.error(
            "GetWindowLongPtrW failed hwnd=%s index=%s error=%s",
            hwnd,
            index,
            err,
        )
        return None
    return value


def _set_long(api: _Win32, hwnd: int, index: int, value: int) -> bool:
    api.kernel32.SetLastError(0)
    previous = int(api.user32.SetWindowLongPtrW(hwnd, index, int(value)))
    err = api.ctypes.get_last_error()
    if previous == 0 and err != 0:
        logger.error(
            "SetWindowLongPtrW failed hwnd=%s index=%s error=%s",
            hwnd,
            index,
            err,
        )
        return False
    return True


def _frame_changed(api: _Win32, hwnd: int) -> bool:
    api.kernel32.SetLastError(0)
    ok = api.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FRAME_ONLY)
    if ok:
        return True
    logger.error(
        "SetWindowPos failed hwnd=%s error=%s",
        hwnd,
        api.ctypes.get_last_error(),
    )
    return False


def _extend_shadow(api: _Win32, hwnd: int) -> None:
    """Ask DWM for a 1px frame shadow. Failure leaves the subclass in place."""
    extend = api.dwm_extend
    if extend is None:
        return
    margins = api.margins(_SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN, _SHADOW_MARGIN)
    try:
        hr = int(extend(hwnd, api.ctypes.byref(margins)))
    except Exception:  # noqa: BLE001 - the shadow is optional
        _log_exception("DwmExtendFrameIntoClientArea failed")
        return
    if hr < 0:
        logger.error(
            "DwmExtendFrameIntoClientArea failed hwnd=%s hresult=%#010x",
            hwnd,
            hr & 0xFFFFFFFF,
        )
