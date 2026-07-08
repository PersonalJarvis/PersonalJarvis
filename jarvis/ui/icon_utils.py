"""Win32 helper for setting the window icon of a pywebview instance.

Why does Jarvis need this? pywebview's ``create_window`` has no ``icon``
parameter on Windows — the taskbar and titlebar icon therefore inherits from
the process (``python.exe`` / ``pythonw.exe``), i.e. the generic Python logo.
We set it after the ``shown`` event via ``WM_SETICON`` directly against the
window handle.

All functions are no-ops on non-Windows platforms.
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

# Class icon slots (negative indices for SetClassLongPtrW). Windows uses the
# class icon for the taskbar entry when no window icon (WM_SETICON) has been
# set yet at first display. Without a class icon, the taskbar
# falls back to the process icon (pythonw.exe → Python logo)
# and caches that mapping for the rest of the session.
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
        logger.debug("AppUserModelID could not be set: {}", exc)
        return False


def _apply_icon_to_hwnd(hwnd: int, ico_path: Path) -> bool:
    """Set window + class icon on a known HWND. Returns True on success."""
    if sys.platform != "win32":
        return False
    if not hwnd:
        return False
    if not ico_path.is_file():
        logger.warning("Icon file missing: {}", ico_path)
        return False

    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes not available")
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
        logger.warning("LoadImageW failed for {}", path_str)
        return False

    # WM_SETICON: titlebar + Alt-Tab switcher.
    user32.SendMessageW(hwnd, _WM_SETICON, _ICON_BIG, hicon_big)
    user32.SendMessageW(hwnd, _WM_SETICON, _ICON_SMALL, hicon_small)
    # Class icon: drives the taskbar group. Without it Windows falls back to
    # the process icon (pythonw.exe → Python logo). Each Tk/pywebview/Qt
    # window registers its own class, so we only affect Jarvis windows.
    user32.SetClassLongPtrW(hwnd, _GCLP_HICON, hicon_big)
    user32.SetClassLongPtrW(hwnd, _GCLP_HICONSM, hicon_small)
    logger.debug("Icon set (window+class): hwnd={} path={}", hwnd, path_str)
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
    """Sets the taskbar and titlebar icon of the window matching ``title``.

    Needed because pywebview doesn't stably expose the HWND. ``FindWindowW``
    against the title is a pragmatic way — the Jarvis window title is
    constant ("Personal Jarvis") and unique.

    Args:
        title: Window title exactly as set by pywebview.
        ico_path: Path to the ``.ico`` file.
        quiet: If True, "hwnd not found" notices are logged at debug
            instead of warning. For polling loops where the window is
            expected to appear only after a few iterations.

    Returns:
        True if both icons could be set.
    """
    if sys.platform != "win32":
        return False
    if not ico_path.is_file():
        logger.warning("Icon file missing: {}", ico_path)
        return False

    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ctypes not available")
        return False

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        if quiet:
            logger.debug("Window '{}' not found (yet)", title)
        else:
            logger.warning("Window '{}' not found — icon not set", title)
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
        logger.opt(exception=exc).warning("ctypes not available")
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


# The Linux window-class token the XDG ``.desktop`` pins via ``StartupWMClass``
# (jarvis/autostart/linux.py). Keep the two in lock-step: the desktop maps a
# running window to its launcher entry — and thus shows the entry's ``Icon=`` on
# the taskbar/dock — only when the window's WM_CLASS matches ``StartupWMClass``.
LINUX_WM_CLASS = "personal-jarvis"


def pin_linux_wm_class(name: str = LINUX_WM_CLASS) -> bool:
    """Pin the X11/Wayland window-class of subsequently-created windows.

    Must run BEFORE the GUI toolkit creates its first window. Without it, a
    ``python3 -m …`` launch leaves the window's WM_CLASS as ``python3`` — so the
    Linux taskbar/dock shows the generic interpreter icon even when the
    ``.desktop`` entry carries the Jarvis ``Icon=`` (they only bind when the
    WM_CLASS matches ``StartupWMClass``). Sets GLib's program name, which GTK
    (pywebview's default Linux backend) uses to derive WM_CLASS.

    No-op on non-Linux and best-effort on Linux (a Qt backend or missing PyGObject
    derives its class differently): never raises, so it can never block the
    window. Returns ``True`` only when the program name was set.
    """
    if sys.platform != "linux":
        return False
    try:
        from gi.repository import GLib  # type: ignore[import-not-found]

        GLib.set_prgname(name)
        return True
    except Exception as exc:  # noqa: BLE001 — WM-class pin is a nicety, never load-bearing
        logger.debug("Linux WM_CLASS could not be pinned: {}", exc)
        return False


def load_ico_as_pil_image(ico_path: Path, size: int = 64) -> Any | None:
    """Loads a ``.ico`` as a ``PIL.Image`` for the pystray tray icon.

    pystray needs an Image object, not a file reference. We load the
    largest available representation and scale it to ``size``.
    """
    if not ico_path.is_file():
        return None
    try:
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("Pillow not available")
        return None
    try:
        img = Image.open(ico_path)
        # .ico typically contains multiple sizes — Pillow picks the first,
        # we force a clean target size via resize.
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        return img
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).warning("ICO load failed: {}", ico_path)
        return None


def project_icon_path() -> Path:
    """Resolve the desktop/taskbar icon (``jarvis.ico``), install-layout agnostic.

    Every Win32 icon surface (window class icon, AUMID icon, Start-Menu shortcut,
    taskbar name, tray) resolves the icon through this one function — so if it
    returns a non-existent path, ALL of them silently fall back to the
    ``pythonw.exe`` Python logo. That is exactly the "taskbar shows Python on a
    fresh machine" symptom: the icon historically lived only at
    ``<repo-root>/assets/icons/jarvis.ico`` (``parents[2]``), which resolves only
    for a run *from the project folder*; a real ``pip install`` relocates the
    package to ``site-packages`` where that repo-root ``assets/`` is absent.

    Resolution order (first existing wins):
      1. the **bundled** in-package copy ``jarvis/assets/icons/jarvis.ico`` — ships
         with the code via ``package-data``, so it is present on every install;
      2. the legacy ``<repo-root>/assets/icons/jarvis.ico`` — the dev/editable and
         build-tool copy (PyInstaller spec, ``install_shortcuts.py``).

    Falls back to the bundled path (even if missing) so callers get a stable,
    descriptive path in log warnings.
    """
    try:
        from jarvis.assets import bundled_app_icon

        bundled = bundled_app_icon()
        if bundled is not None:
            return bundled
    except Exception as exc:  # noqa: BLE001 — never let icon resolution crash boot
        logger.debug("bundled_app_icon lookup failed, trying repo-root: {}", exc)

    repo_root = Path(__file__).resolve().parents[2] / "assets" / "icons" / "jarvis.ico"
    if repo_root.is_file():
        return repo_root

    # Nothing found — return the bundled location for a descriptive warning.
    return Path(__file__).resolve().parent.parent / "assets" / "icons" / "jarvis.ico"
