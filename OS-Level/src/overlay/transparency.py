"""ctypes-Wrappers fuer Win32-Window-Flags.

Plan §12.1 / §12.4: Transparente Click-Through Layered-Windows mit
``WDA_EXCLUDEFROMCAPTURE`` damit Screen-Sharing und OBS das Overlay nicht
mit-aufnehmen. Qt setzt ``WindowTransparentForInput`` selbst, der ctypes-Pfad
hier ist (a) Defense-in-Depth fuer Affinity, (b) bereitet Phase 9.6 vor
(Mascot braucht selektiv ``WS_EX_NOACTIVATE``).

Auf Nicht-Windows-Hosts sind alle Funktionen No-Ops, damit Tests headless
auf jeder Plattform durchlaufen koennen.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# Win32-Konstanten — Plan §12.1, §12.2.
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

# WDA_EXCLUDEFROMCAPTURE: Win10 2004+ / Win11 — Plan §12.4.
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def _is_windows() -> bool:
    return sys.platform == "win32"


def get_user32():  # pragma: no cover — Plattform-Branch
    """Lazy-Loader. Nur auf Windows aufrufen."""
    if not _is_windows():
        raise RuntimeError("user32 ist Windows-only")
    return ctypes.windll.user32


def apply_click_through(hwnd: int) -> None:
    """Ergaenzt ``WS_EX_LAYERED | WS_EX_TRANSPARENT`` an einem HWND.

    Idempotent: bestehende Style-Bits werden mit OR ergaenzt, nichts geloescht.
    """
    if not _is_windows():
        return
    user32 = get_user32()
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    h = wintypes.HWND(hwnd)
    current = user32.GetWindowLongW(h, GWL_EXSTYLE)
    desired = current | WS_EX_LAYERED | WS_EX_TRANSPARENT
    if desired != current:
        user32.SetWindowLongW(h, GWL_EXSTYLE, desired)


def apply_mascot_styles(hwnd: int) -> None:
    """Mascot-Window — Plan §12.2: Layered + NoActivate + Toolwindow.

    Bewusst KEIN ``WS_EX_TRANSPARENT``, damit Klicks/Drag durchkommen.
    Phase 9.6 nutzt das.
    """
    if not _is_windows():
        return
    user32 = get_user32()
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    h = wintypes.HWND(hwnd)
    current = user32.GetWindowLongW(h, GWL_EXSTYLE)
    desired = current | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    if desired != current:
        user32.SetWindowLongW(h, GWL_EXSTYLE, desired)


def exclude_from_capture(hwnd: int) -> bool:
    """``SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)``.

    Returnt ``True`` bei Erfolg, ``False`` falls unsupported (alte Win10-Builds)
    oder Aufruf auf Nicht-Windows.
    """
    if not _is_windows():
        return False
    user32 = get_user32()
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    result = user32.SetWindowDisplayAffinity(
        wintypes.HWND(hwnd), wintypes.DWORD(WDA_EXCLUDEFROMCAPTURE)
    )
    return bool(result)


def reapply_capture_affinity(hwnd: int) -> bool:
    """Plan §18.1 — Reapply WDA_EXCLUDEFROMCAPTURE.

    Identisch zu ``exclude_from_capture``, aber als named entry-point
    fuer Window-Hooks (showEvent, screenChanged, screenAdded). DWM
    cacht das Affinity-Flag nicht ueber Re-Composite-Boundaries; nach
    DPI-Wechseln oder Monitor-Hotplug hat das Flag schon mal verloren
    gewirkt. Daher idempotent re-applien.
    """
    return exclude_from_capture(hwnd)


def set_per_monitor_dpi_awareness() -> None:
    """``SetProcessDpiAwareness(2)`` — PROCESS_PER_MONITOR_DPI_AWARE.

    Muss VOR der QApplication-Erzeugung laufen (Plan §12.3). Schluckt alle
    Fehler, weil der Aufruf bei bereits gesetztem Awareness-Level
    ``E_ACCESSDENIED`` zurueckgibt.
    """
    if not _is_windows():
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):  # pragma: no cover — best effort
        pass
