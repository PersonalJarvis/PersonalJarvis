"""Win32-Helper zum Setzen des Fenster-Icons einer pywebview-Instanz.

Warum braucht Jarvis das? pywebview ``create_window`` hat auf Windows keinen
``icon``-Parameter — das Taskbar- und Titlebar-Icon erbt daher vom Prozess
(``python.exe`` / ``pythonw.exe``), also das generische Python-Logo. Wir setzen
es nach dem ``shown``-Event per ``WM_SETICON`` direkt gegen das Window-Handle.

Alle Funktionen sind No-Ops auf Nicht-Windows-Plattformen.
"""
from __future__ import annotations

import os
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

# The friendly name Windows shows on taskbar hover and in the jump-list header.
# This is a *different* layer from the AUMID grouping key above: the key only
# groups the button. The name is resolved by matching the running window's AUMID
# to a **Start-Menu shortcut** carrying the same ``System.AppUserModel.ID`` and
# using that shortcut's file name + icon (see ``ensure_start_menu_shortcut``).
# Without such a shortcut the shell falls back to the process ``FileDescription``
# (``pythonw.exe`` -> "Python"), which is the "taskbar says Python" symptom.
# (The HKCU ``DisplayName`` registered below is the *toast-notification*
# identity, a separate surface — it does NOT name the taskbar button.)
APP_DISPLAY_NAME = "Personal Jarvis"

# Start-Menu shortcut whose *file name* becomes the taskbar button name. The
# launcher module is the relaunch target so a fresh click reopens the app.
START_MENU_SHORTCUT_NAME = "Personal Jarvis.lnk"
_LAUNCHER_MODULE = "jarvis.ui.web.launcher"
# IID_IPropertyStore — the COM interface for reading/writing a .lnk's AUMID.
_IID_IPROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"


def register_windows_app_user_model_id(
    app_id: str = APP_USER_MODEL_ID,
    *,
    display_name: str = APP_DISPLAY_NAME,
    icon_path: Path | None = None,
) -> bool:
    """Register the AUMID's ``DisplayName`` (+ icon) under HKCU for *toasts*.

    This names the AUMID for the **toast-notification / Action-Center** surface
    only. It does NOT name the taskbar button — that is resolved from an
    AUMID-tagged Start-Menu shortcut (see ``ensure_start_menu_shortcut``).
    Registering the AUMID under
    ``HKCU\\Software\\Classes\\AppUserModelId\\<app_id>`` with a ``DisplayName``
    (and optional ``IconResource``) is the documented way to give a custom AUMID
    a friendly toast identity instead of the ``pythonw.exe`` description.

    Idempotent (a re-register just rewrites the same values), Windows-only,
    best-effort — it never raises and never blocks boot. Returns ``True`` only
    when the registration was written.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        subkey = rf"Software\Classes\AppUserModelId\{app_id}"
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
            if icon_path is not None:
                # "<path>,<index>" lets Explorer pick the icon frame; index 0 is
                # the first/largest. REG_EXPAND_SZ matches the shell convention.
                winreg.SetValueEx(
                    key,
                    "IconResource",
                    0,
                    winreg.REG_EXPAND_SZ,
                    f"{icon_path},0",
                )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("AUMID DisplayName could not be registered: {}", exc)
        return False


def _pythonw_executable() -> Path | None:
    """Best-effort ``pythonw.exe`` next to the running interpreter.

    ``pythonw`` (GUI subsystem) avoids a console window when the shortcut is
    clicked; falls back to ``python.exe`` if the windowless variant is absent.
    """
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    if cand.exists():
        return cand
    return exe if exe.exists() else None


def _default_start_menu_programs_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def ensure_start_menu_shortcut(
    *,
    aumid: str = APP_USER_MODEL_ID,
    display_name: str = APP_DISPLAY_NAME,
    icon_path: Path | None = None,
    programs_dir: Path | None = None,
) -> bool:
    """Create/maintain the AUMID-tagged Start-Menu shortcut that NAMES the button.

    This — not the HKCU ``DisplayName`` — is the mechanism Windows uses to label
    a grouped taskbar button and its jump-list header: it matches the running
    window's process AUMID (set by ``SetCurrentProcessExplicitAppUserModelID``)
    to a Start-Menu shortcut carrying the same ``System.AppUserModel.ID`` and
    shows that shortcut's **file name** ("Personal Jarvis") and **icon**. A
    shortcut-less ``pythonw`` app falls back to the process description
    ("Python") — the exact symptom the user reported. The shortcut only needs to
    *exist* in the Start Menu; Windows resolves it regardless of how the app was
    launched, and the resolution happens when the taskbar button is created, so
    a *fresh* launch picks it up (an already-grouped button is not retroactively
    renamed).

    Idempotent (an existing shortcut already carrying ``aumid`` is left alone),
    Windows-only, best-effort — it never raises and never blocks boot. Returns
    ``True`` only when a matching shortcut is present afterwards.
    """
    if sys.platform != "win32":
        return False
    programs = programs_dir or _default_start_menu_programs_dir()
    if programs is None:
        return False
    try:
        import pywintypes
        from win32com.client import Dispatch
        from win32com.propsys import propsys, pscon
    except Exception as exc:  # noqa: BLE001
        logger.debug("pywin32 unavailable; Start-Menu shortcut not ensured: {}", exc)
        return False

    pythonw = _pythonw_executable()
    if pythonw is None:
        return False
    ico = icon_path or project_icon_path()
    lnk = programs / START_MENU_SHORTCUT_NAME
    iid = pywintypes.IID(_IID_IPROPERTYSTORE)

    # Idempotent: an existing shortcut already tagged with this AUMID is enough.
    if lnk.is_file():
        try:
            ro_store = propsys.SHGetPropertyStoreFromParsingName(
                str(lnk), None, 0, iid  # GPS_DEFAULT
            )
            existing = ro_store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
            if existing == aumid:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not read existing shortcut AUMID, rewriting: {}", exc)

    try:
        programs.mkdir(parents=True, exist_ok=True)
        shell = Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(lnk))
        sc.TargetPath = str(pythonw)
        sc.Arguments = f"-m {_LAUNCHER_MODULE}"
        sc.WorkingDirectory = str(Path.home())
        if ico.is_file():
            sc.IconLocation = f"{ico},0"
        sc.Description = display_name
        sc.WindowStyle = 1
        sc.Save()
        # Embed the AUMID so Windows matches the running window to this shortcut.
        rw_store = propsys.SHGetPropertyStoreFromParsingName(
            str(lnk), None, 2, iid  # GPS_READWRITE
        )
        rw_store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(aumid))
        rw_store.Commit()
        logger.debug("Start-Menu shortcut ensured: {}", lnk)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Start-Menu shortcut could not be written: {}", exc)
        return False


def ensure_windows_app_identity(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Pin a stable Windows app identity for taskbar grouping AND name.

    Three layers, each a different shell surface:
      1. ``SetCurrentProcessExplicitAppUserModelID`` — groups every Jarvis
         window under one taskbar button (the grouping *key*) instead of under
         "Python".
      2. ``ensure_start_menu_shortcut`` — the AUMID-tagged Start-Menu shortcut
         that gives that key a *name* + icon, so the button/jump-list header
         read "Personal Jarvis" instead of the ``pythonw.exe`` description. This
         is the layer that actually fixed the "taskbar says Python" report;
         layer 1 alone leaves the button nameless.
      3. ``register_windows_app_user_model_id`` — HKCU ``DisplayName`` for the
         *toast-notification* identity (a separate surface from the taskbar).

    Must run before the first window is created (idempotent across the desktop,
    orb and overlay processes, which all call this early). The return value
    reflects only step 1; steps 2 and 3 are best-effort side effects.
    """
    if sys.platform != "win32":
        return False
    ico = project_icon_path()
    ico_arg = ico if ico.is_file() else None
    # Name the taskbar button (shortcut) + the toast identity (registry). Both
    # best-effort and must be in place before the AUMID is set + the window
    # appears, so Explorer resolves them on first button creation.
    ensure_start_menu_shortcut(aumid=app_id, icon_path=ico_arg)
    register_windows_app_user_model_id(app_id, icon_path=ico_arg)
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


def set_window_icon_for_pid(pid: int, ico_path: Path) -> bool:
    """Set the icon on the largest visible top-level window owned by ``pid``.

    A title-independent companion to :func:`set_window_icon_by_title`. pywebview's
    WebView2 host window does not reliably carry ``WINDOW_TITLE`` at the moment the
    icon-setter polls (the title is applied late, and ``FindWindowW`` only matches
    an *exact* title), so we also locate the window by *our own* process id and pick
    its biggest top-level window. Returns True when an icon was applied.
    """
    if sys.platform != "win32":
        return False
    if not ico_path.is_file():
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes nicht verfügbar")
        return False

    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]

    best = [0, 0]  # [hwnd, area]
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):  # noqa: ANN001
        wp = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wp))
        if wp.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best[1]:
                best[0], best[1] = int(hwnd), area
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    if not best[0]:
        return False
    return _apply_icon_to_hwnd(best[0], ico_path)


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
