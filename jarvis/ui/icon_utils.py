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


def set_window_appusermodel_icon(
    hwnd: int,
    app_id: str,
    ico_path: Path,
    *,
    relaunch_command: str | None = None,
    relaunch_display_name: str = "Personal Jarvis",
) -> bool:
    """Set per-window AppUserModel properties via IPropertyStore.

    WHY this is required:
    When a process calls SetCurrentProcessExplicitAppUserModelID the taskbar
    groups its windows under that AUMID.  For a scripting-host process
    (pythonw.exe) the taskbar derives the AUMID-group button icon from the
    *process executable* (pythonw.exe -> Python snake) — NOT from WM_SETICON
    on the hosted window.  The only documented Win32 mechanism to override the
    button icon for a specific HWND inside such a group is to set the per-window
    IPropertyStore property PKEY_AppUserModel_RelaunchIconResource via
    SHGetPropertyStoreForWindow.  WM_SETICON alone, SetClassLongPtrW alone, or
    any combination thereof cannot fix this: they update the titlebar and the
    Alt-Tab thumbnail but the taskbar button continues to render the process
    executable icon.

    Properties written:
      PKEY_AppUserModel_ID                   (pid 5)  — ties the window to our AUMID
      PKEY_AppUserModel_RelaunchIconResource (pid 3)  — icon for the taskbar button
      PKEY_AppUserModel_RelaunchCommand      (pid 2)  — "relaunch" command (optional)
      PKEY_AppUserModel_RelaunchDisplayNameResource (pid 4) — display name

    All properties use fmtid {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}.

    Args:
        hwnd: The top-level window handle (must be the taskbar-representative
              window; for pywebview this is the WS_EX_APPWINDOW BrowserForm).
        app_id: AUMID string, e.g. ``"PersonalJarvis.PersonalJarvis"``.
        ico_path: Absolute path to the .ico file.
        relaunch_command: Optional relaunch command string shown in taskbar
            jump-list / pin dialog.  Defaults to the current sys.executable
            invocation when None.
        relaunch_display_name: Human-readable app name in the taskbar group.

    Returns:
        True when all SetValue + Commit calls returned S_OK.
    """
    if sys.platform != "win32":
        return False
    if not hwnd:
        return False
    if not ico_path.is_file():
        logger.debug(
            "set_window_appusermodel_icon: .ico nicht gefunden: {}", ico_path
        )
        return False

    try:
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes as wt  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.debug("ctypes nicht verfuegbar: {}", exc)
        return False

    # ------------------------------------------------------------------ COM structures

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def _guid(d1: int, d2: int, d3: int, d4: list[int]) -> GUID:
        g = GUID()
        g.Data1 = d1
        g.Data2 = d2
        g.Data3 = d3
        for i, b in enumerate(d4):
            g.Data4[i] = b
        return g

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]

    def _pkey(d1: int, d2: int, d3: int, d4: list[int], pid: int) -> PROPERTYKEY:
        pk = PROPERTYKEY()
        pk.fmtid = _guid(d1, d2, d3, d4)
        pk.pid = pid
        return pk

    # All AppUserModel keys share this fmtid
    _D4 = [0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3]
    _PKEY_ID    = _pkey(0x9F4C2855, 0x9F79, 0x4B39, _D4, 5)
    _PKEY_ICON  = _pkey(0x9F4C2855, 0x9F79, 0x4B39, _D4, 3)
    _PKEY_CMD   = _pkey(0x9F4C2855, 0x9F79, 0x4B39, _D4, 2)
    _PKEY_DNAME = _pkey(0x9F4C2855, 0x9F79, 0x4B39, _D4, 4)

    # PROPVARIANT: 16 bytes on 64-bit (vt + 3 reserved WORDs + 8-byte data union)
    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt",         ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
            ("wReserved2", ctypes.c_ushort),
            ("wReserved3", ctypes.c_ushort),
            ("data",       ctypes.c_ulonglong),
        ]

    VT_LPWSTR = 31  # VARIANT type for LPWSTR

    # IID_IPropertyStore {886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}
    _IID_IPS = _guid(
        0x886D8EEB, 0x8CF2, 0x4446,
        [0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99],
    )

    # vtable function types for IPropertyStore
    # COM vtable order: QI(0) AddRef(1) Release(2) GetCount(3) GetAt(4)
    #                   GetValue(5) SetValue(6) Commit(7)
    _P = ctypes.c_void_p
    _HRESULT = ctypes.HRESULT
    _FN_RELEASE  = ctypes.WINFUNCTYPE(ctypes.c_ulong, _P)
    _FN_SETVALUE = ctypes.WINFUNCTYPE(
        _HRESULT, _P,
        ctypes.POINTER(PROPERTYKEY),
        ctypes.POINTER(PROPVARIANT),
    )
    _FN_COMMIT = ctypes.WINFUNCTYPE(_HRESULT, _P)

    # ------------------------------------------------------------------ get store

    propsys = ctypes.windll.propsys
    propsys.SHGetPropertyStoreForWindow.restype = ctypes.HRESULT

    store = ctypes.c_void_p()
    hr = propsys.SHGetPropertyStoreForWindow(
        hwnd, ctypes.byref(_IID_IPS), ctypes.byref(store)
    )
    if hr != 0 or not store.value:
        logger.debug(
            "SHGetPropertyStoreForWindow fehlgeschlagen: hr=0x{:08X}", hr & 0xFFFFFFFF
        )
        return False

    # ------------------------------------------------------------------ vtable helpers

    vtbl_base = ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0]

    def _vtfn(idx: int, ftype: type) -> object:
        fn_ptr = ctypes.cast(vtbl_base, ctypes.POINTER(ctypes.c_void_p))[idx]
        return ftype(fn_ptr)

    set_value = _vtfn(6, _FN_SETVALUE)
    commit    = _vtfn(7, _FN_COMMIT)
    release   = _vtfn(2, _FN_RELEASE)

    # ------------------------------------------------------------------ set properties

    # String buffers must outlive all SetValue calls — collect them here.
    _string_bufs: list[ctypes.Array] = []

    def _pv_str(s: str) -> PROPVARIANT:
        pv = PROPVARIANT()
        pv.vt = VT_LPWSTR
        buf = ctypes.create_unicode_buffer(s)
        _string_bufs.append(buf)
        pv.data = ctypes.cast(buf, ctypes.c_void_p).value
        return pv

    all_ok = True
    failed_keys: list[str] = []

    # PKEY_AppUserModel_ID
    hr = set_value(store, ctypes.byref(_PKEY_ID), ctypes.byref(_pv_str(app_id)))
    if hr != 0:
        failed_keys.append(f"ID hr=0x{hr & 0xFFFFFFFF:08X}")
        all_ok = False

    # PKEY_AppUserModel_RelaunchIconResource — "<abs_path>,0"
    icon_resource = f"{ico_path},0"
    hr = set_value(store, ctypes.byref(_PKEY_ICON), ctypes.byref(_pv_str(icon_resource)))
    if hr != 0:
        failed_keys.append(f"Icon hr=0x{hr & 0xFFFFFFFF:08X}")
        all_ok = False

    # PKEY_AppUserModel_RelaunchDisplayNameResource
    hr = set_value(store, ctypes.byref(_PKEY_DNAME), ctypes.byref(_pv_str(relaunch_display_name)))
    if hr != 0:
        failed_keys.append(f"DisplayName hr=0x{hr & 0xFFFFFFFF:08X}")
        # Non-fatal — icon is what matters for the taskbar button

    # PKEY_AppUserModel_RelaunchCommand (optional — improves pin-to-taskbar UX)
    if relaunch_command:
        hr = set_value(store, ctypes.byref(_PKEY_CMD), ctypes.byref(_pv_str(relaunch_command)))
        if hr != 0:
            failed_keys.append(f"Cmd hr=0x{hr & 0xFFFFFFFF:08X}")
            # Non-fatal

    # ------------------------------------------------------------------ commit

    hr = commit(store)
    release(store)

    if hr != 0:
        logger.debug(
            "IPropertyStore.Commit fehlgeschlagen: hr=0x{:08X}", hr & 0xFFFFFFFF
        )
        return False

    if failed_keys:
        logger.debug(
            "IPropertyStore: einige SetValue-Aufrufe fehlgeschlagen: {}", failed_keys
        )

    logger.debug(
        "IPropertyStore gesetzt (AUMID={} Icon={}): hwnd=0x{:X}",
        app_id, icon_resource, hwnd,
    )
    return all_ok


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

    # IPropertyStore: PKEY_AppUserModel_RelaunchIconResource — das ist die
    # einzig dokumentierte Methode, den Taskbar-Button-Icon fuer ein
    # pythonw.exe-gehostetes Fenster zu setzen.  WM_SETICON allein reicht nicht:
    # Windows zieht das Icon des AUMID-Groups vom Process-Executable (pythonw →
    # Python-Logo) und ignoriert WM_SETICON fuer den Taskbar-Button.
    set_window_appusermodel_icon(hwnd, APP_USER_MODEL_ID, ico_path)

    logger.debug(
        "Icon gesetzt (WM_SETICON+Class+IPropertyStore): hwnd=0x{:X} path={}",
        hwnd, path_str,
    )
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
