"""What a monitor really shows: plain SDR, a wide colour gamut, or HDR.

:func:`display_color` answers per monitor, at capture time, because the mode can
change any second (Windows: Win+Alt+B, macOS: an HDR video starting). Captures
use the answer to decide whether they may stay 8-bit SDR or must read the
framebuffer at full depth, and which colour tags the saved file carries.

Per platform:

* **Windows** — the display configuration API reports the active colour mode
  (SDR / WCG / HDR, Windows 11 24H2+; older builds report "advanced colour"
  on or off), the bit depth and the SDR white level. The monitor's ICC
  profile comes from GDI.
* **macOS** — NSScreen reports extended dynamic range headroom and the
  screen's colour space (as ICC data).
* **Linux** — no portable API reports either; the answer is plain SDR with no
  profile, said honestly via ``source``.

Never raises: anything unknown is SDR, which is what every capture path
already handles.
"""

from __future__ import annotations

import logging
import struct
import sys
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

Mode = Literal["sdr", "wcg", "hdr"]
Gamut = Literal["srgb", "p3", "bt2020"]

#: scRGB 1.0 is 80 nits; Windows' default SDR white in HDR mode is 200-240.
SCRGB_NITS = 80.0
#: Windows GDI monitor names look like ``\\.\DISPLAY1``.
_GDI_PREFIX = "\\\\.\\"


@dataclass(frozen=True, slots=True)
class DisplayColor:
    mode: Mode = "sdr"
    bits_per_channel: int = 8
    #: Brightness of SDR white while the display runs HDR (Windows "SDR content
    #: brightness"). Plain SDR white is scRGB 1.0 = 80 nits by definition.
    sdr_white_nits: float = SCRGB_NITS
    #: The monitor's ICC profile, when the OS has one (SDR colour management).
    icc_profile: bytes | None = None
    #: Primaries of that profile, rounded to the nearest standard gamut.
    gamut: Gamut = "srgb"
    #: Where the answer came from — "default" means nothing could be asked.
    source: str = "default"

    @property
    def extended(self) -> bool:
        """True when the desktop holds more than 8-bit sRGB can carry."""
        return self.mode != "sdr"


SDR = DisplayColor()


def display_color(
    device_name: str | None = None, *, point: tuple[int, int] | None = None
) -> DisplayColor:
    """The colour state of one monitor, asked at capture time.

    ``device_name`` is the OS name of the monitor: the GDI name on Windows
    (``DISPLAY1`` behind the ``\\\\.\\`` prefix, what DXGI reports), the
    screen's localized name on macOS (Qt's ``QScreen.name()``). On Windows
    ``point`` — any physical desktop pixel on that monitor — finds it instead;
    Qt reports a friendly name there that no colour API knows. Neither means
    the primary monitor.
    """
    try:
        if sys.platform == "win32":
            if not (device_name or "").startswith(_GDI_PREFIX):
                device_name = device_at(point) if point is not None else None
            return _windows(device_name)
        if sys.platform == "darwin":
            return _macos(device_name)
    except Exception:  # noqa: BLE001 - detection is advisory; SDR is always safe
        log.debug("display colour detection failed", exc_info=True)
    return SDR


# ---------------------------------------------------------------------------
# ICC profile primaries
# ---------------------------------------------------------------------------


def icc_gamut(profile: bytes | None) -> Gamut:
    """The standard gamut nearest to an ICC profile's red/green/blue colorants.

    Compares the colorant triangle's area in xy with sRGB's: Display P3 is
    about 1.36x sRGB, BT.2020 about 1.9x. A profile without colorant tags
    (LUT-based) counts as sRGB.
    """
    if not profile or len(profile) < 132:
        return "srgb"
    try:
        (count,) = struct.unpack_from(">I", profile, 128)
        tags: dict[bytes, tuple[float, float]] = {}
        for i in range(min(count, 200)):
            sig, offset, size = struct.unpack_from(">4sII", profile, 132 + 12 * i)
            if sig in (b"rXYZ", b"gXYZ", b"bXYZ") and size >= 20:
                x, y, z = (v / 65536.0 for v in struct.unpack_from(">iii", profile, offset + 8))
                total = x + y + z
                if total > 0:
                    tags[sig] = (x / total, y / total)
        if len(tags) != 3:
            return "srgb"
        area = _triangle_area(tags[b"rXYZ"], tags[b"gXYZ"], tags[b"bXYZ"])
    except (struct.error, ValueError):
        return "srgb"
    # sRGB's colorants adapted to the profile white D50.
    srgb = _triangle_area((0.6484, 0.3309), (0.3212, 0.5978), (0.1559, 0.0660))
    ratio = area / srgb if srgb else 1.0
    if ratio >= 1.6:
        return "bt2020"
    if ratio >= 1.18:
        return "p3"
    return "srgb"


def _triangle_area(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

_QDC_ONLY_ACTIVE_PATHS = 2
_GET_SOURCE_NAME = 1
_GET_ADVANCED_COLOR_INFO = 9
_GET_SDR_WHITE_LEVEL = 11
_GET_ADVANCED_COLOR_INFO_2 = 15


def _windows(device_name: str | None) -> DisplayColor:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32")

    class LUID(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]

    class Rational(ctypes.Structure):
        _fields_ = [("num", ctypes.c_uint32), ("den", ctypes.c_uint32)]

    class PathSource(ctypes.Structure):
        _fields_ = [
            ("adapter", LUID), ("id", ctypes.c_uint32),
            ("mode_index", ctypes.c_uint32), ("flags", ctypes.c_uint32),
        ]

    class PathTarget(ctypes.Structure):
        _fields_ = [
            ("adapter", LUID), ("id", ctypes.c_uint32), ("mode_index", ctypes.c_uint32),
            ("technology", ctypes.c_uint32), ("rotation", ctypes.c_uint32),
            ("scaling", ctypes.c_uint32), ("refresh", Rational),
            ("scanline", ctypes.c_uint32), ("available", wintypes.BOOL),
            ("flags", ctypes.c_uint32),
        ]

    class DisplayPath(ctypes.Structure):
        _fields_ = [("source", PathSource), ("target", PathTarget), ("flags", ctypes.c_uint32)]

    class ModeInfo(ctypes.Structure):
        _fields_ = [
            ("kind", ctypes.c_uint32), ("id", ctypes.c_uint32),
            ("adapter", LUID), ("info", ctypes.c_byte * 48),
        ]

    class Header(ctypes.Structure):
        _fields_ = [
            ("kind", ctypes.c_uint32), ("size", ctypes.c_uint32),
            ("adapter", LUID), ("id", ctypes.c_uint32),
        ]

    class SourceName(ctypes.Structure):
        _fields_ = [("header", Header), ("name", wintypes.WCHAR * 32)]

    class AdvancedColor(ctypes.Structure):
        _fields_ = [
            ("header", Header), ("flags", ctypes.c_uint32),
            ("encoding", ctypes.c_uint32), ("bits", ctypes.c_uint32),
        ]

    class AdvancedColor2(ctypes.Structure):
        _fields_ = [
            ("header", Header), ("flags", ctypes.c_uint32), ("encoding", ctypes.c_uint32),
            ("bits", ctypes.c_uint32), ("mode", ctypes.c_uint32),
        ]

    class WhiteLevel(ctypes.Structure):
        _fields_ = [("header", Header), ("level", ctypes.c_ulong)]

    def ask(info, kind: int, adapter, target_id: int) -> bool:
        info.header.kind = kind
        info.header.size = ctypes.sizeof(info)
        info.header.adapter = adapter
        info.header.id = target_id
        return user32.DisplayConfigGetDeviceInfo(ctypes.byref(info)) == 0

    path_count, mode_count = ctypes.c_uint32(), ctypes.c_uint32()
    if user32.GetDisplayConfigBufferSizes(
        _QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), ctypes.byref(mode_count)
    ):
        return SDR
    paths = (DisplayPath * max(1, path_count.value))()
    modes = (ModeInfo * max(1, mode_count.value))()
    if user32.QueryDisplayConfig(
        _QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), paths,
        ctypes.byref(mode_count), modes, None,
    ):
        return SDR
    wanted = (device_name or _primary_device_name()).casefold()
    for path in paths[: path_count.value]:
        source = SourceName()
        if not ask(source, _GET_SOURCE_NAME, path.source.adapter, path.source.id):
            continue
        if source.name.casefold() != wanted:
            continue
        adapter, target = path.target.adapter, path.target.id
        mode: Mode = "sdr"
        bits = 8
        info2 = AdvancedColor2()
        if ask(info2, _GET_ADVANCED_COLOR_INFO_2, adapter, target):
            mode = {1: "wcg", 2: "hdr"}.get(info2.mode, "sdr")
            bits = int(info2.bits) or 8
        else:  # before Windows 11 24H2: advanced colour on means HDR
            info = AdvancedColor()
            if ask(info, _GET_ADVANCED_COLOR_INFO, adapter, target):
                enabled = bool(info.flags & 0b10)
                wide = bool(info.flags & 0b100)
                mode = "hdr" if enabled else ("wcg" if wide else "sdr")
                bits = int(info.bits) or 8
        white = SCRGB_NITS
        level = WhiteLevel()
        if mode != "sdr" and ask(level, _GET_SDR_WHITE_LEVEL, adapter, target) and level.level:
            white = level.level * SCRGB_NITS / 1000.0
        profile = _windows_icc(source.name)
        return DisplayColor(
            mode=mode,
            bits_per_channel=bits,
            sdr_white_nits=white,
            icc_profile=profile,
            gamut=icc_gamut(profile),
            source="windows-displayconfig",
        )
    return SDR


def device_at(point: tuple[int, int] | None) -> str | None:
    """Windows: the GDI name of the monitor holding physical ``point``."""
    if sys.platform != "win32":
        return None
    try:
        return _monitor_device(point or (0, 0))
    except Exception:  # noqa: BLE001 - advisory, like the rest of this module
        log.debug("monitor lookup failed", exc_info=True)
        return None


def _primary_device_name() -> str:
    return _monitor_device((0, 0))


def _monitor_device(point: tuple[int, int]) -> str:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
            ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32),
        ]

    user32 = ctypes.WinDLL("user32")
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    handle = user32.MonitorFromPoint(
        wintypes.POINT(int(point[0]), int(point[1])), 2
    )  # MONITOR_DEFAULTTONEAREST
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return r"\\.\DISPLAY1"
    return info.device


def _windows_icc(device_name: str) -> bytes | None:
    """The ICC profile Windows colour-manages this monitor with, if any."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.CreateDCW.restype = wintypes.HDC
    gdi32.CreateDCW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p
    ]
    gdi32.GetICMProfileW.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    hdc = gdi32.CreateDCW("DISPLAY", device_name, None, None)
    if not hdc:
        return None
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not gdi32.GetICMProfileW(hdc, ctypes.byref(size), buffer):
            return None
        path = Path(buffer.value)
    finally:
        gdi32.DeleteDC(hdc)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    # The generic sRGB profile adds nothing a plain sRGB file does not say.
    return data if len(data) >= 132 and data[36:40] == b"acsp" else None


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def _macos(device_name: str | None) -> DisplayColor:
    from AppKit import NSScreen  # noqa: PLC0415

    screens = list(NSScreen.screens() or [])
    if not screens:
        return SDR
    screen = screens[0]
    if device_name:
        for candidate in screens:
            name = getattr(candidate, "localizedName", None)
            if callable(name) and str(name()) == device_name:
                screen = candidate
                break
    headroom = float(screen.maximumExtendedDynamicRangeColorComponentValue())
    potential = float(screen.maximumPotentialExtendedDynamicRangeColorComponentValue())
    space = screen.colorSpace()
    data = space.ICCProfileData() if space is not None else None
    profile = bytes(data) if data is not None else None
    gamut = icc_gamut(profile)
    if headroom > 1.0 or potential > 1.0:
        mode: Mode = "hdr"
    elif gamut != "srgb":
        mode = "wcg"
    else:
        mode = "sdr"
    return DisplayColor(
        mode=mode,
        bits_per_channel=10 if mode != "sdr" else 8,
        icc_profile=profile,
        gamut=gamut,
        source="macos-nsscreen",
    )
