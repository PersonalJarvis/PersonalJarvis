"""Per-pixel alpha for a Tk toplevel on Windows (``UpdateLayeredWindow``).

Tk's ``-transparentcolor`` keys ONE colour out: a pixel is either fully shown
or fully gone, so soft edges and see-through controls are impossible, and a
keyed pixel does not take clicks. A layered window fed through
``UpdateLayeredWindow`` instead shows a premultiplied BGRA bitmap with real
per-pixel alpha, and every pixel whose alpha is above zero still receives the
mouse — so a control can be practically invisible (alpha 1/255) and clickable.

Windows only, behind :func:`per_pixel_alpha_supported`; every other platform
gets ``False`` from each call and keeps its own path. Once a window is fed
through ``UpdateLayeredWindow`` it must never also get
``SetLayeredWindowAttributes`` (Tk's ``-transparentcolor`` / ``-alpha``): the
two modes exclude each other.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from PIL import Image, ImageChops

log = logging.getLogger(__name__)

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_DIB_RGB_COLORS = 0

_api: Any | None = None


def per_pixel_alpha_supported() -> bool:
    """True where :func:`update_layered` can work (Windows)."""
    return sys.platform == "win32"


def _load() -> Any:
    """ctypes bindings, built once. Explicit argtypes: handles are 64-bit."""
    global _api
    if _api is not None:
        return _api
    import ctypes  # noqa: PLC0415 — Windows-only path
    from ctypes import wintypes  # noqa: PLC0415

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

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_ubyte),
            ("BlendFlags", ctypes.c_ubyte),
            ("SourceConstantAlpha", ctypes.c_ubyte),
            ("AlphaFormat", ctypes.c_ubyte),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    handle = ctypes.c_void_p
    user32.GetParent.argtypes = [handle]
    user32.GetParent.restype = handle
    user32.GetDC.argtypes = [handle]
    user32.GetDC.restype = handle
    user32.ReleaseDC.argtypes = [handle, handle]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.GetWindowLongPtrW.argtypes = [handle, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [handle, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.UpdateLayeredWindow.argtypes = [
        handle,
        handle,
        ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE),
        handle,
        ctypes.POINTER(wintypes.POINT),
        wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.DeleteDC.argtypes = [handle]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = [
        handle,
        ctypes.POINTER(BITMAPINFOHEADER),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        handle,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.SelectObject.restype = handle
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteObject.restype = wintypes.BOOL

    class Api:
        pass

    api = Api()
    api.ctypes = ctypes
    api.wintypes = wintypes
    api.user32 = user32
    api.gdi32 = gdi32
    api.BITMAPINFOHEADER = BITMAPINFOHEADER
    api.BLENDFUNCTION = BLENDFUNCTION
    _api = api
    return api


def tk_toplevel_hwnd(top: Any) -> int:
    """The real top-level HWND of a Tk toplevel (``winfo_id`` is its child)."""
    if not per_pixel_alpha_supported():
        return 0
    try:
        inner = int(top.winfo_id())
        parent = _load().user32.GetParent(inner)
        return int(parent or inner)
    except Exception:  # noqa: BLE001 — a missing handle degrades to "no alpha"
        log.debug("could not resolve the toplevel HWND", exc_info=True)
        return 0


def enable_per_pixel_alpha(hwnd: int) -> bool:
    """Mark ``hwnd`` as a layered window. True on success."""
    if not per_pixel_alpha_supported() or not hwnd:
        return False
    try:
        user32 = _load().user32
        style = user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
        if not style & _WS_EX_LAYERED:
            user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED)
        return True
    except Exception:  # noqa: BLE001 — degrade to the colour-key path
        log.debug("could not make hwnd=%s layered", hwnd, exc_info=True)
        return False


def premultiplied_bgra(image: Image.Image) -> bytes:
    """``image`` as top-down premultiplied BGRA bytes, the layout GDI wants."""
    rgba = image.convert("RGBA")
    r, g, b, a = rgba.split()
    return Image.merge(
        "RGBA",
        (ImageChops.multiply(b, a), ImageChops.multiply(g, a), ImageChops.multiply(r, a), a),
    ).tobytes()


def update_layered(hwnd: int, image: Image.Image) -> bool:
    """Show ``image`` (RGBA, real alpha) as the whole content of ``hwnd``.

    Leaves the window's position alone (Tk keeps placing it) and sets its
    size to the image's. True when Windows accepted the frame.
    """
    if not per_pixel_alpha_supported() or not hwnd:
        return False
    try:
        api = _load()
    except Exception:  # noqa: BLE001
        log.debug("layered-window bindings unavailable", exc_info=True)
        return False
    ctypes, wintypes = api.ctypes, api.wintypes
    width, height = image.size
    data = premultiplied_bgra(image)
    screen = api.user32.GetDC(None)
    memdc = api.gdi32.CreateCompatibleDC(screen)
    bitmap = None
    old = None
    try:
        header = api.BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(api.BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = api.gdi32.CreateDIBSection(
            screen, ctypes.byref(header), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits.value:
            return False
        ctypes.memmove(bits, data, len(data))
        old = api.gdi32.SelectObject(memdc, bitmap)
        size = wintypes.SIZE(width, height)
        origin = wintypes.POINT(0, 0)
        blend = api.BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
        ok = api.user32.UpdateLayeredWindow(
            hwnd,
            screen,
            None,
            ctypes.byref(size),
            memdc,
            ctypes.byref(origin),
            0,
            ctypes.byref(blend),
            _ULW_ALPHA,
        )
        if not ok:
            log.debug("UpdateLayeredWindow refused hwnd=%s (err %s)", hwnd, ctypes.get_last_error())
        return bool(ok)
    finally:
        if old:
            api.gdi32.SelectObject(memdc, old)
        if bitmap:
            api.gdi32.DeleteObject(bitmap)
        api.gdi32.DeleteDC(memdc)
        api.user32.ReleaseDC(None, screen)
