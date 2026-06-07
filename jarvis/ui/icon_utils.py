"""Win32-Helper zum Setzen des Fenster-Icons einer pywebview-Instanz.

Warum braucht Jarvis das? pywebview ``create_window`` hat auf Windows keinen
``icon``-Parameter — das Taskbar- und Titlebar-Icon erbt daher vom Prozess
(``python.exe`` / ``pythonw.exe``), also das generische Python-Logo. Wir setzen
es nach dem ``shown``-Event per ``WM_SETICON`` direkt gegen das Window-Handle.

Alle Funktionen sind No-Ops auf Nicht-Windows-Plattformen.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger

_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1

_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040

# Class-Icon-Slots (negative Indices fuer SetClassLongPtrW). Das Class-Icon
# wird von Windows fuer den Taskbar-Eintrag verwendet, wenn beim ersten
# Anzeigen noch kein Window-Icon (WM_SETICON) gesetzt ist. Ohne Class-Icon
# faellt die Taskbar auf das Process-Icon (pythonw.exe → Python-Logo) zurueck
# und cached die Zuordnung fuer den Rest der Session.
_GCLP_HICON = -14
_GCLP_HICONSM = -34

APP_USER_MODEL_ID = "PersonalJarvis.PersonalJarvis"


def ensure_windows_app_identity(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Set a stable Windows AppUserModelID for taskbar grouping.

    Without this, a ``pythonw.exe -m ...`` desktop app can be grouped under
    Python and inherit the Python taskbar icon before Jarvis can set WM_SETICON.
    This must run before the first pywebview window is created.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("AppUserModelID konnte nicht gesetzt werden: {}", exc)
        return False


def _apply_icon_to_hwnd(hwnd: int, ico_path: Path) -> bool:
    """Set window + class icon on a known HWND. Returns True on success."""
    if sys.platform != "win32":
        return False
    if not hwnd:
        return False
    if not ico_path.is_file():
        logger.warning("Icon-Datei fehlt: {}", ico_path)
        return False

    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes nicht verfügbar")
        return False

    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.restype = ctypes.c_long
    # On 64-bit Windows SetClassLongPtrW is the correct variant.
    user32.SetClassLongPtrW.argtypes = [
        wintypes.HWND, ctypes.c_int, ctypes.c_void_p,
    ]
    user32.SetClassLongPtrW.restype = ctypes.c_void_p

    path_str = str(ico_path)
    hicon_big = user32.LoadImageW(
        None, path_str, _IMAGE_ICON, 32, 32, _LR_LOADFROMFILE | _LR_DEFAULTSIZE
    )
    hicon_small = user32.LoadImageW(
        None, path_str, _IMAGE_ICON, 16, 16, _LR_LOADFROMFILE | _LR_DEFAULTSIZE
    )
    if not hicon_big or not hicon_small:
        logger.warning("LoadImageW schlug fehl für {}", path_str)
        return False

    # WM_SETICON: titlebar + Alt-Tab switcher.
    user32.SendMessageW(hwnd, _WM_SETICON, _ICON_BIG, hicon_big)
    user32.SendMessageW(hwnd, _WM_SETICON, _ICON_SMALL, hicon_small)
    # Class icon: drives the taskbar group. Without it Windows falls back to
    # the process icon (pythonw.exe → Python logo). Each Tk/pywebview/Qt
    # window registers its own class, so we only affect Jarvis windows.
    user32.SetClassLongPtrW(hwnd, _GCLP_HICON, hicon_big)
    user32.SetClassLongPtrW(hwnd, _GCLP_HICONSM, hicon_small)
    logger.debug("Icon gesetzt (window+class): hwnd={} path={}", hwnd, path_str)
    return True


def set_window_icon_by_hwnd(hwnd: int, ico_path: Path) -> bool:
    """Set taskbar + titlebar icon for a window whose HWND is already known.

    Used by Tkinter (``root.winfo_id()``) and Qt (``window.winId()``) where
    the toolkit hands us the HWND directly — no need to scan windows by title.
    """
    return _apply_icon_to_hwnd(hwnd, ico_path)


def set_window_icon_by_title(
    title: str, ico_path: Path, *, quiet: bool = False
) -> bool:
    """Setzt Taskbar- und Titlebar-Icon des Fensters mit passendem ``title``.

    Benötigt, weil pywebview das HWND nicht stabil exposed. ``FindWindowW``
    gegen den Titel ist ein pragmatischer Weg — der Jarvis-Window-Title ist
    konstant ("Personal Jarvis") und einzigartig.

    Args:
        title: Window-Titel exakt wie von pywebview gesetzt.
        ico_path: Pfad zur ``.ico``-Datei.
        quiet: Wenn True, werden "hwnd nicht gefunden"-Hinweise auf debug
            geloggt statt warning. Fuer Polling-Loops, bei denen das Fenster
            erwartungsgemaess erst nach einigen Iterationen auftaucht.

    Returns:
        True wenn beide Icons gesetzt werden konnten.
    """
    if sys.platform != "win32":
        return False
    if not ico_path.is_file():
        logger.warning("Icon-Datei fehlt: {}", ico_path)
        return False

    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes nicht verfügbar")
        return False

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        if quiet:
            logger.debug("Fenster '{}' (noch) nicht gefunden", title)
        else:
            logger.warning("Fenster '{}' nicht gefunden — Icon nicht gesetzt", title)
        return False
    return _apply_icon_to_hwnd(int(hwnd), ico_path)


def load_ico_as_pil_image(ico_path: Path, size: int = 64) -> Any | None:
    """Lädt ``.ico`` als ``PIL.Image`` für pystray-Tray-Icon.

    pystray braucht ein Image-Objekt, keine Datei-Referenz. Wir laden die
    größte verfügbare Repräsentation und skalieren auf ``size``.
    """
    if not ico_path.is_file():
        return None
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("Pillow nicht verfügbar")
        return None
    try:
        img = Image.open(ico_path)
        # .ico enthält typischerweise mehrere Größen — Pillow wählt die erste,
        # wir forcen eine saubere Zielgröße via resize.
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        return img
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ICO-Load schlug fehl: {}", ico_path)
        return None


def project_icon_path() -> Path:
    """Auflösung des Standard-Icon-Pfads: ``<projekt-root>/assets/icons/jarvis.ico``.

    Die Datei liegt außerhalb des ``jarvis``-Packages. Wir gehen von
    ``jarvis/ui/icon_utils.py`` drei Ebenen hoch zum Projekt-Root.
    """
    return Path(__file__).resolve().parents[2] / "assets" / "icons" / "jarvis.ico"
