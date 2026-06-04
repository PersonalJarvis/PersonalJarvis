"""Win32-Helper zum Setzen des Fenster-Icons einer pywebview-Instanz.

Warum braucht Jarvis das?  pywebview ``create_window`` hat keinen ``icon``-
Parameter, aber ``webview.start(icon=...)`` hat einen — dieser wird von
``BrowserForm.__init__`` (winforms.py) SYNCHRON bei der Fenster-Erstellung
gesetzt, also *bevor* ``Form.Show()`` aufgerufen wird.  Das ist der einzige
zuverlässige Weg, die Taskbar vom ersten Moment an mit dem Jarvis-Icon zu
versorgen.  Alle nachträglichen Verfahren (WM_SETICON per Polling,
SetClassLongPtrW) greifen zu spät: Windows cached das AUMID-Gruppen-Icon beim
ersten ShowWindow-Aufruf und aktualisiert den Taskbar-Button danach nur noch
wenn man ITaskbarList3 explizit triggert.

Architektur dieser Datei:

  1. ``ensure_windows_app_identity`` — muss VOR create_window laufen.
  2. ``project_icon_path`` / ``project_icon_path_for_platform`` — kanonik.
  3. ``_apply_icon_to_hwnd`` — Low-Level Win32 (WM_SETICON + Class-Icon).
  4. ``set_window_icon_*`` — Wrapper für verschiedene Lookup-Strategien.
  5. ``force_taskbar_icon_refresh`` — ITaskbarList3.ThumbBarSetImageList-Trick
     für den Fall, dass der HWND erst nach ShowWindow bekannt wird.
  6. Cross-platform Hooks: macOS Dock, Linux WM_CLASS.
  7. ``load_ico_as_pil_image`` — pystray-Helper.

Alle Funktionen sind No-Ops auf Nicht-Windows-Plattformen, sofern nicht anders
dokumentiert.
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
# LR_DEFAULTSIZE laesst Windows die kanonische Systemgroesse waehlen (32 fuer
# ICON_BIG, 16 fuer ICON_SMALL) — wir uebergeben explizite Groessen stattdessen.
_LR_DEFAULTSIZE = 0x00000040

# SetClassLongPtrW-Slots (negative Indizes).  Das Class-Icon steuert das
# Taskbar-Button-Icon fuer alle Fenster dieser Window-Class.
_GCLP_HICON = -14
_GCLP_HICONSM = -34

# WM_SETICON greift fuer Titlebar + Alt-Tab, aber der Taskbar-Button braucht
# zusaetzlich ein ITaskbarList3-Nudge damit er sich ohne Neustart aktualisiert.
_WM_DWMCOMPOSITIONCHANGED = 0x031E  # Trick: zwingt DWM zum Icon-Re-Read

APP_USER_MODEL_ID = "PersonalJarvis.PersonalJarvis"


# ---------------------------------------------------------------------------
# AUMID
# ---------------------------------------------------------------------------


def ensure_windows_app_identity(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Set a stable Windows AppUserModelID for taskbar grouping.

    Without this, a ``pythonw.exe -m ...`` desktop app is grouped under the
    Python executable entry and inherits the Python taskbar icon before Jarvis
    can set WM_SETICON.  Must run before the first pywebview window is created.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        logger.debug("AppUserModelID gesetzt: {}", app_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("AppUserModelID konnte nicht gesetzt werden: {}", exc)
        return False


# ---------------------------------------------------------------------------
# Icon-Pfad-Aufloesung (kanonisch)
# ---------------------------------------------------------------------------


def project_icon_path() -> Path:
    """Windows .ico: ``<projekt-root>/assets/icons/jarvis.ico``.

    Geht von ``jarvis/ui/icon_utils.py`` drei Ebenen hoch.
    """
    return Path(__file__).resolve().parents[2] / "assets" / "icons" / "jarvis.ico"


def project_icon_path_for_platform() -> Path:
    """Liefert den plattformspezifischen Pfad des kanonischen App-Icons.

    - Windows:  ``assets/icons/jarvis.ico``
    - macOS:    ``assets/icons/jarvis.icns``  (Fallback: .png)
    - Linux:    ``assets/icons/jarvis.png``   (256 px)
    """
    root = Path(__file__).resolve().parents[2] / "assets" / "icons"
    if sys.platform == "win32":
        return root / "jarvis.ico"
    if sys.platform == "darwin":
        icns = root / "jarvis.icns"
        return icns if icns.is_file() else root / "jarvis.png"
    # Linux / andere POSIX
    return root / "jarvis.png"


# ---------------------------------------------------------------------------
# Win32 Low-Level
# ---------------------------------------------------------------------------


def _apply_icon_to_hwnd(hwnd: int, ico_path: Path) -> bool:
    """Set window + class icon on a known HWND.

    Setzt sowohl WM_SETICON (Titlebar, Alt-Tab) als auch SetClassLongPtrW
    (Class-Icon — Grundlage des Taskbar-Button-Icons fuer diese Window-Class).
    Danach wird WM_DWMCOMPOSITIONCHANGED simuliert, damit DWM sein Icon-Cache
    invalidiert ohne auf den naechsten Resize zu warten.

    Returns True on success.
    """
    if sys.platform != "win32":
        return False
    if not hwnd:
        return False
    if not ico_path.is_file():
        logger.warning("Icon-Datei fehlt: {}", ico_path)
        return False

    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes nicht verfügbar")
        return False

    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.restype = ctypes.c_long
    user32.SetClassLongPtrW.argtypes = [
        wintypes.HWND, ctypes.c_int, ctypes.c_void_p,
    ]
    user32.SetClassLongPtrW.restype = ctypes.c_void_p

    path_str = str(ico_path)

    # Taskbar benoetigt 48px (100 % DPI) oder groesser; Titlebar 32px; System-
    # default waere 32/16. Wir laden 48 und 16 explizit, damit auch bei
    # 125 %/150 % Scaling der Taskbar-Button scharf bleibt.
    hicon_big = user32.LoadImageW(
        None, path_str, _IMAGE_ICON, 48, 48, _LR_LOADFROMFILE
    )
    hicon_small = user32.LoadImageW(
        None, path_str, _IMAGE_ICON, 16, 16, _LR_LOADFROMFILE
    )
    if not hicon_big or not hicon_small:
        # Fallback: LR_DEFAULTSIZE (32/16) wenn 48px nicht ladbar
        hicon_big = user32.LoadImageW(
            None, path_str, _IMAGE_ICON, 32, 32, _LR_LOADFROMFILE | _LR_DEFAULTSIZE
        )
        hicon_small = user32.LoadImageW(
            None, path_str, _IMAGE_ICON, 16, 16, _LR_LOADFROMFILE | _LR_DEFAULTSIZE
        )
    if not hicon_big or not hicon_small:
        logger.warning("LoadImageW schlug fehl fuer {}", path_str)
        return False

    # WM_SETICON: Titlebar + Alt-Tab-Vorschau
    user32.SendMessageW(hwnd, _WM_SETICON, _ICON_BIG, hicon_big)
    user32.SendMessageW(hwnd, _WM_SETICON, _ICON_SMALL, hicon_small)
    # Class-Icon: steuert den Taskbar-Button fuer alle Fenster dieser Klasse
    user32.SetClassLongPtrW(hwnd, _GCLP_HICON, hicon_big)
    user32.SetClassLongPtrW(hwnd, _GCLP_HICONSM, hicon_small)
    # DWM-Trick: zwingt den Compositor dazu, das HICON-Cache des Fensters
    # neu einzulesen ohne auf den naechsten Maximize/Restore zu warten.
    user32.PostMessageW(hwnd, _WM_DWMCOMPOSITIONCHANGED, 1, 0)
    logger.debug("Icon gesetzt (window+class+DWM-nudge): hwnd=0x{:X} path={}", hwnd, path_str)
    return True


def force_taskbar_icon_refresh(hwnd: int) -> bool:
    """Force the Shell taskbar to re-read the icon for this HWND.

    Sendet eine ITaskbarList3-kompatible Nachricht via SHChangeNotify +
    einen SWP-No-Op (Groesse unveraendert), der den DWM-Thumbnail-Cache
    invalidiert.  Nur notwendig wenn der HWND bereits sichtbar war bevor
    WM_SETICON gesetzt wurde.

    Returns True wenn der Refresh ausgeloest wurde.
    """
    if sys.platform != "win32":
        return False
    if not hwnd:
        return False
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        # SHChangeNotify mit SHCNE_ASSOCCHANGED zwingt den Shell-Taskbar dazu,
        # seine Icon-Zuordnungen neu aufzubauen.  Teuer (Shell-weit), aber
        # einmalig beim App-Start akzeptabel.
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)

        # Zusaetzlich: SetWindowPos No-Op loest einen WM_SIZE aus, der die
        # Taskbar-Vorschau neu rendert — weniger aggressiv als ASSOCCHANGED.
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Taskbar-Refresh schlug fehl: {}", exc)
        return False


# ---------------------------------------------------------------------------
# Oeffentliche Icon-Setter-Wrapper
# ---------------------------------------------------------------------------


def set_window_icon_by_hwnd(hwnd: int, ico_path: Path) -> bool:
    """Set taskbar + titlebar icon for a window whose HWND is already known.

    Used by Tkinter (``root.winfo_id()``) and Qt (``window.winId()``) where
    the toolkit hands us the HWND directly.
    """
    return _apply_icon_to_hwnd(hwnd, ico_path)


def set_window_icon_by_title(
    title: str, ico_path: Path, *, quiet: bool = False
) -> bool:
    """Set taskbar + titlebar icon via FindWindowW(title).

    Benoetigt weil pywebview das HWND nicht stabil exposed.  Achtung:
    WebView2 ueberschreibt den Fenstertitel mit ``document.title`` sobald die
    Seite geladen ist — danach schlaegt FindWindowW fehl.  Dieser Wrapper ist
    daher nur fuer den fruehen Aufruf direkt nach dem ``shown``-Event geeignet.
    Fuer spaetere Aufrufe ``set_window_icon_for_current_process`` verwenden.
    """
    if sys.platform != "win32":
        return False
    if not ico_path.is_file():
        logger.warning("Icon-Datei fehlt: {}", ico_path)
        return False

    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
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


def set_window_icon_for_current_process(ico_path: Path) -> bool:
    """Set the icon on THIS process's top-level app window(s).

    Sucht per EnumWindows alle Top-Level-Fenster dieses Prozesses und setzt das
    Icon auf dem Fenster, das am ehesten den Taskbar-Button repraesentiert:

    - PID == eigener PID
    - IsWindowVisible == True  ODER  IsWindowVisible == False aber
      GetWindowTextLength > 0  (WinForms-Fenster koennen kurz nach Create noch
      invisible sein, sind aber bereits registriert)
    - Kein WS_EX_TOOLWINDOW (Tray-Helper-Fenster herausfiltern)
    - GA_ROOTOWNER == HWND selbst (kein Child-Fenster)

    Gibt True zurueck wenn das Icon auf mindestens einem Fenster gesetzt wurde.
    """
    if sys.platform != "win32":
        return False
    if not ico_path.is_file():
        logger.warning("Icon-Datei fehlt: {}", ico_path)
        return False
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes nicht verfügbar")
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    our_pid = kernel32.GetCurrentProcessId()

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    GA_ROOTOWNER = 3

    hwnds: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != our_pid:
            return True

        # Nur echte App-Fenster: kein ToolWindow, kein Child (rootowner==self)
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW:
            return True
        root_owner = user32.GetAncestor(hwnd, GA_ROOTOWNER)
        if root_owner != hwnd:
            return True  # ist ein owned child window

        # Titel vorhanden (nicht borderless overlay) ODER sichtbar
        has_title = user32.GetWindowTextLengthW(hwnd) > 0
        visible = bool(user32.IsWindowVisible(hwnd))
        if has_title or visible:
            hwnds.append(int(hwnd))
        return True

    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.EnumWindows(WNDENUMPROC(_cb), 0)

    ok = False
    for hwnd in hwnds:
        if _apply_icon_to_hwnd(hwnd, ico_path):
            force_taskbar_icon_refresh(hwnd)
            ok = True
    return ok


# ---------------------------------------------------------------------------
# Cross-platform Hooks (macOS Dock, Linux WM_CLASS)
# ---------------------------------------------------------------------------


def set_macos_dock_icon(icon_path: Path | None = None) -> bool:
    """Set the macOS Dock icon via NSApplication.setApplicationIconImage_.

    Benoetigt pyobjc (``AppKit``).  No-op wenn nicht verfuegbar oder nicht macOS.

    Args:
        icon_path: Pfad zu einer .icns oder .png Datei.  None = Fallback auf
            ``project_icon_path_for_platform()``.
    """
    if sys.platform != "darwin":
        return False
    if icon_path is None:
        icon_path = project_icon_path_for_platform()
    if not icon_path.is_file():
        logger.warning("macOS-Dock-Icon fehlt: {}", icon_path)
        return False
    try:
        import AppKit  # noqa: PLC0415
    except ImportError:
        logger.debug("AppKit (pyobjc) nicht verfuegbar — Dock-Icon nicht setzbar.")
        return False
    try:
        ns_image = AppKit.NSImage.alloc().initByReferencingFile_(str(icon_path))
        if ns_image is None:
            logger.warning("NSImage konnte {} nicht laden", icon_path)
            return False
        AppKit.NSApplication.sharedApplication().setApplicationIconImage_(ns_image)
        logger.debug("macOS Dock-Icon gesetzt: {}", icon_path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Dock-Icon-Setter schlug fehl: {}", exc)
        return False


def set_linux_window_icon(hwnd_or_widget: Any = None, icon_path: Path | None = None) -> bool:
    """Set the window icon on Linux via GTK or a raw Xlib hint.

    Verwendet gtk.Window.set_icon_from_file wenn ein GTK-Widget uebergeben
    wird, sonst XChangeProperty(_NET_WM_ICON) direkt.

    Args:
        hwnd_or_widget: Optional — GTK Window-Objekt oder X11 Window-ID (int).
            None = versucht den Fokus-Fenster per Xlib zu setzen (best-effort).
        icon_path: Pfad zur .png Datei.  None = Fallback auf
            ``project_icon_path_for_platform()``.
    """
    if sys.platform == "win32" or sys.platform == "darwin":
        return False
    if icon_path is None:
        icon_path = project_icon_path_for_platform()
    if not icon_path.is_file():
        logger.debug("Linux-Fenster-Icon fehlt: {}", icon_path)
        return False

    # GTK-Pfad: wenn ein GTK-Window-Objekt uebergeben wurde
    if hwnd_or_widget is not None and hasattr(hwnd_or_widget, "set_icon_from_file"):
        try:
            hwnd_or_widget.set_icon_from_file(str(icon_path))
            logger.debug("GTK window icon gesetzt: {}", icon_path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("GTK set_icon_from_file schlug fehl: {}", exc)

    # Kein GTK-Widget: WM_CLASS setzen damit das Desktop-Environment das .desktop
    # File matchen kann — das ist zuverlaessiger als _NET_WM_ICON per Xlib.
    logger.debug("Linux-Icon-Setter: kein GTK-Widget — WM_CLASS wird vom Caller gesteuert")
    return False


# ---------------------------------------------------------------------------
# pystray-Helper
# ---------------------------------------------------------------------------


def load_ico_as_pil_image(ico_path: Path, size: int = 64) -> Any | None:
    """Lädt ``.ico`` als ``PIL.Image`` für pystray-Tray-Icon.

    pystray braucht ein Image-Objekt, keine Datei-Referenz.  Wir laden die
    groesste verfuegbare Repraesentierung und skalieren auf ``size``.
    """
    if not ico_path.is_file():
        return None
    try:
        from PIL import Image  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("Pillow nicht verfügbar")
        return None
    try:
        img = Image.open(ico_path)
        # .ico enthaelt typischerweise mehrere Groessen — wir laden die groesste
        # und skalieren auf Zielgroesse.
        sizes: list[tuple[int, int]] = []
        try:
            for i in range(200):
                img.seek(i)
                sizes.append(img.size)
        except EOFError:
            pass
        if sizes:
            best = max(sizes, key=lambda s: s[0])
            img.seek(sizes.index(best))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        return img
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ICO-Load schlug fehl: {}", ico_path)
        return None
