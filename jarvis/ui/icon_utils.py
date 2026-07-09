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

# A per-install copy of ``pythonw.exe`` next to the interpreter, carrying the
# Jarvis mascot as its EMBEDDED executable icon. On Windows the taskbar button of
# a running app takes the icon of the LAUNCHING EXECUTABLE — not the window icon,
# class icon, AUMID, Start-Menu shortcut, registry, or icon cache (all verified
# to have no effect on the button). A bare ``pythonw.exe`` launch therefore shows
# the Python logo on the taskbar no matter how much window-icon work we do; the
# ONLY fix is to launch from an exe whose embedded icon is the mascot. See
# ``ensure_branded_launcher_exe`` + ``maybe_reexec_through_branded_launcher``.
BRANDED_LAUNCHER_EXE_NAME = "PersonalJarvis.exe"
# Set in the child's env when we re-exec through the branded exe, so the child
# does not re-exec again (loop guard).
_BRANDED_LAUNCH_ENV = "JARVIS_BRANDED_LAUNCH"


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


def _replace_exe_icon(exe_path: Path, ico_path: Path) -> bool:
    """Overwrite ``exe_path``'s embedded application icon with ``ico_path``.

    Rewrites the ``RT_ICON`` images + the primary ``RT_GROUP_ICON`` (id 1, the
    group Explorer uses as the app icon for ``pythonw.exe``) via the Win32
    ``*UpdateResource`` API — no external tool (rcedit/PyInstaller) needed. The
    file must not be running. Returns ``True`` on success.
    """
    import ctypes
    import struct
    from ctypes import wintypes

    try:
        data = ico_path.read_bytes()
        _reserved, _itype, count = struct.unpack("<HHH", data[:6])
        entries = []
        off = 6
        for _ in range(count):
            w, h, cc, _r, planes, bc, size, imgoff = struct.unpack(
                "<BBBBHHII", data[off : off + 16]
            )
            entries.append(
                {
                    "w": w, "h": h, "cc": cc, "planes": planes, "bc": bc,
                    "img": data[imgoff : imgoff + size], "size": size,
                }
            )
            off += 16
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not parse .ico for exe branding: {}", exc)
        return False

    RT_ICON, RT_GROUP_ICON, LANG = 3, 14, 0x0409
    k = ctypes.windll.kernel32
    k.BeginUpdateResourceW.restype = wintypes.HANDLE
    k.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k.UpdateResourceW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.WORD, wintypes.LPVOID, wintypes.DWORD,
    ]
    k.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]

    def _res_id(i: int):  # MAKEINTRESOURCE
        return ctypes.cast(ctypes.c_void_p(i), wintypes.LPCWSTR)

    handle = k.BeginUpdateResourceW(str(exe_path), False)
    if not handle:
        logger.debug("BeginUpdateResource failed for {}", exe_path)
        return False
    try:
        for i, e in enumerate(entries):
            buf = ctypes.create_string_buffer(e["img"], len(e["img"]))
            if not k.UpdateResourceW(
                handle, _res_id(RT_ICON), _res_id(1 + i), LANG, buf, len(e["img"])
            ):
                logger.debug("UpdateResource RT_ICON {} failed", i)
        grp = struct.pack("<HHH", 0, 1, len(entries))
        for i, e in enumerate(entries):
            grp += struct.pack(
                "<BBBBHHIH", e["w"] & 0xFF, e["h"] & 0xFF, e["cc"], 0,
                e["planes"] or 1, e["bc"] or 32, e["size"], 1 + i,
            )
        gbuf = ctypes.create_string_buffer(grp, len(grp))
        if not k.UpdateResourceW(
            handle, _res_id(RT_GROUP_ICON), _res_id(1), LANG, gbuf, len(grp)
        ):
            logger.debug("UpdateResource RT_GROUP_ICON failed")
        return bool(k.EndUpdateResourceW(handle, False))
    except Exception as exc:  # noqa: BLE001
        logger.debug("exe icon resource update failed: {}", exc)
        try:
            k.EndUpdateResourceW(handle, True)  # discard
        except Exception:  # noqa: BLE001
            pass
        return False


def _base_pythonw_executable() -> Path | None:
    """The BASE interpreter's ``pythonw.exe`` (``sys.base_prefix``), or ``None``.

    This — not the venv ``pythonw.exe`` — is the process that actually OWNS the
    window: a venv launcher is a thin redirector that re-spawns the base
    interpreter, and Windows takes the taskbar-button icon from that final
    window-owning exe. So the mascot must be branded onto a copy of the *base*
    pythonw, not the venv stub.
    """
    base = Path(sys.base_prefix)
    cand = base / "pythonw.exe"
    if cand.exists():
        return cand
    alt = base / "python.exe"
    return alt if alt.exists() else None


def _branded_launcher_path() -> Path | None:
    """Where the mascot-branded base-``pythonw`` copy lives (in the base dir).

    Placed NEXT TO the base interpreter so it finds ``pythonXX.dll`` + the stdlib
    landmark; the venv is re-attached at launch via ``__PYVENV_LAUNCHER__``.
    """
    base = _base_pythonw_executable()
    if base is None:
        return None
    return base.with_name(BRANDED_LAUNCHER_EXE_NAME)


def ensure_branded_launcher_exe() -> Path | None:
    """Create/refresh a mascot-icon copy of the BASE ``pythonw`` and return it.

    The taskbar button takes its icon from the window-owning executable, which is
    the *base* interpreter (the venv ``pythonw`` only redirects to it). So we copy
    ``<base_prefix>/pythonw.exe`` to ``<base_prefix>/PersonalJarvis.exe`` (same
    dir ⇒ it finds ``pythonXX.dll`` + the stdlib) and stamp the Jarvis ``.ico`` as
    its embedded icon. The venv is re-attached at launch via ``__PYVENV_LAUNCHER__``
    (see ``maybe_reexec_through_branded_launcher``), so the branded base copy runs
    the app with the venv's packages while OWNING the window ⇒ mascot on the
    taskbar.

    Idempotent + self-healing (rebuilds only when missing or older than the
    icon/source exe). Returns ``None`` — caller falls back to bare ``pythonw``,
    taskbar keeps the Python logo — when branding is impossible:

    * **MS Store Python**: its base exe is a 0-byte app-execution alias in a
      read-only ``WindowsApps`` package that cannot be copied or branded. Source
      runs there keep the Python icon; the shipped PyInstaller build is the
      supported branded path on such machines.
    * a read-only base dir (a system-wide ``Program Files`` install without write
      access), or any copy/resource-write failure.
    """
    if sys.platform != "win32":
        return None
    src = _base_pythonw_executable()
    target = _branded_launcher_path()
    if src is None or target is None:
        return None
    ico = project_icon_path()
    if not ico.is_file():
        return None
    try:
        # MS Store base exe is a 0-byte alias → unbrandable.
        if src.stat().st_size == 0:
            logger.debug("base pythonw is a 0-byte alias (MS Store); cannot brand")
            return None
        fresh = (
            target.is_file()
            and target.stat().st_mtime
            >= max(ico.stat().st_mtime, src.stat().st_mtime)
        )
        if fresh:
            return target
        # Do not clobber a running copy (best-effort self-heal, not load-bearing).
        base_exe = getattr(sys, "_base_executable", "") or ""
        if target.is_file() and Path(base_exe).name.lower() == target.name.lower():
            return target
        import shutil

        shutil.copy2(src, target)
        if not _replace_exe_icon(target, ico):
            # A copy without the branded icon is pointless (still shows Python);
            # remove it so the caller cleanly falls back to bare pythonw.
            try:
                target.unlink()
            except Exception:  # noqa: BLE001
                pass
            return None
        logger.debug("Branded launcher exe ready: {}", target)
        return target
    except PermissionError as exc:
        logger.debug("base dir not writable; cannot brand launcher: {}", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("branded launcher exe could not be built: {}", exc)
        return target if target.is_file() else None


def maybe_reexec_through_branded_launcher(argv: list[str]) -> int | None:
    """Re-exec the launcher through the mascot-branded exe; return an exit code.

    The taskbar button icon is the launching exe's embedded icon, so a bare
    ``pythonw.exe`` start shows the Python logo regardless of every window-icon /
    AUMID / shortcut effort. Relaunching the SAME launcher module through
    ``PersonalJarvis.exe`` (a pythonw copy carrying the mascot icon) is the only
    thing that brands the taskbar button — and it covers every entry point at one
    chokepoint (``run.bat``, the Start-Menu/pinned shortcut, the autostart task,
    the tray self-restart), because they all funnel through ``main()``.

    Returns an exit code when it re-exec'd (the caller must return it and let this
    process exit), or ``None`` to continue booting in-process (already branded,
    non-Windows, a console/debug run, or branding unavailable — graceful
    fallback: the app still runs, the taskbar just keeps the Python logo).
    """
    if sys.platform != "win32":
        return None
    # Loop guard: the env marker (set on the re-exec child) is authoritative —
    # under ``__PYVENV_LAUNCHER__`` ``sys.executable`` is the venv pythonw, so the
    # real running image is ``sys._base_executable`` (our branded copy).
    if os.environ.get(_BRANDED_LAUNCH_ENV) == "1":
        return None
    base_exe = getattr(sys, "_base_executable", "") or ""
    if Path(base_exe).name.lower() == BRANDED_LAUNCHER_EXE_NAME.lower():
        return None
    # A visible-console/debug run wants python.exe's console; re-exec'ing through
    # a windowless pythonw copy would swallow it. Leave those alone.
    if os.environ.get("JARVIS_DEBUG") == "1":
        return None
    branded = ensure_branded_launcher_exe()
    if branded is None:
        return None
    try:
        import subprocess

        env = dict(os.environ)
        env[_BRANDED_LAUNCH_ENV] = "1"
        # Re-attach THIS venv inside the base-python-copy branded exe, so it runs
        # the app with the venv's packages while owning the window itself. This is
        # exactly the mechanism a venv launcher uses to redirect into the venv.
        venv_pythonw = Path(sys.executable).with_name("pythonw.exe")
        if venv_pythonw.is_file():
            env["__PYVENV_LAUNCHER__"] = str(venv_pythonw)
        # DETACHED_PROCESS | CREATE_NO_WINDOW — same idiom as jarvis.ui.relauncher:
        # cut the child loose from the parent's console/process group and keep
        # pythonw from flashing a console. Redirect all three std streams to
        # DEVNULL: DETACHED_PROCESS leaves them as INVALID handles otherwise, and
        # a boot-time write to stdout/stderr then crashes the child before its
        # window ever appears (observed: the re-exec'd app silently never came up
        # until stdio was given valid handles). The app logs to its own file sink.
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell, our own exe
            [str(branded), "-m", _LAUNCHER_MODULE, *argv],
            env=env,
            close_fds=True,
            creationflags=detached | no_window,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug("Re-exec'd launcher through branded exe: {}", branded)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("branded re-exec failed, continuing in-process: {}", exc)
        return None


def _default_start_menu_programs_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _shortcut_paths_are_live(lnk: Path) -> bool:
    """True only if the ``.lnk``'s icon file AND target file both exist on disk.

    The taskbar renders an AUMID-grouped button from its Start-Menu shortcut's
    icon; a dangling ``IconLocation`` (install moved/renamed) silently degrades
    to the target's icon (``pythonw.exe`` -> Python logo). An empty
    ``IconLocation`` means "use the target's icon", which is exactly the Python
    fallback, so that counts as NOT live. Best-effort: any read failure returns
    ``False`` so the caller rewrites rather than trusting a shortcut it cannot
    verify. Windows-only helper (``WScript.Shell`` is a shell COM object).
    """
    if sys.platform != "win32":
        return False
    try:
        from win32com.client import Dispatch
    except Exception as exc:  # noqa: BLE001
        logger.debug("pywin32 unavailable; cannot verify shortcut paths: {}", exc)
        return False
    try:
        sc = Dispatch("WScript.Shell").CreateShortcut(str(lnk))
        # IconLocation is "<path>,<index>"; an empty path == inherit the target
        # icon == the pythonw.exe fallback, so treat it as not-live.
        icon_path = (sc.IconLocation or "").rsplit(",", 1)[0].strip().strip('"')
        target = (sc.TargetPath or "").strip().strip('"')
        icon_ok = bool(icon_path) and Path(icon_path).is_file()
        target_ok = bool(target) and Path(target).is_file()
        return icon_ok and target_ok
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read shortcut target/icon, treating as stale: {}", exc)
        return False


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

    # Idempotent BUT self-healing: leave an existing shortcut alone ONLY if it
    # still carries this AUMID *and* its icon + target resolve to real files.
    #
    # A plain "AUMID matches -> return" check was a latent Python-logo bug: a
    # shortcut written against an earlier install location (a moved/renamed repo,
    # a throwaway ``.venv``) keeps a **dangling** ``IconLocation``. Windows then
    # renders the whole AUMID-grouped taskbar button from the shortcut's target
    # icon (``pythonw.exe`` -> the Python logo) even though the live window's
    # class icon is the mascot — the button icon is resolved from the shortcut,
    # not the window. Re-validating the paths repairs it on the next launch, so
    # the fix reaches every machine whose install moved (why it "works on one
    # machine, not another"). Verifying the target too keeps a fresh relaunch
    # click pointed at a real interpreter.
    if lnk.is_file():
        try:
            ro_store = propsys.SHGetPropertyStoreFromParsingName(
                str(lnk), None, 0, iid  # GPS_DEFAULT
            )
            existing = ro_store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
            if existing == aumid and _shortcut_paths_are_live(lnk):
                return True
            logger.debug("stale/broken Start-Menu shortcut, rewriting: {}", lnk)
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not read existing shortcut AUMID, rewriting: {}", exc)

    try:
        programs.mkdir(parents=True, exist_ok=True)
        shell = Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(lnk))
        # Target the venv pythonw (which re-execs through the branded base copy in
        # main()); the branded exe itself needs __PYVENV_LAUNCHER__ to find the
        # venv, so it is not a valid direct shortcut target. IconLocation below
        # still brands the pinned/Start-Menu icon itself.
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


# System.AppUserModel.* property keys (fmtid + pid) for the per-WINDOW property
# store. ``RelaunchIconResource`` is THE documented mechanism for an app hosted
# by a shared interpreter exe (pythonw) to give its taskbar button its own icon.
_APPUSERMODEL_FMTID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_PID_RELAUNCH_COMMAND = 2
_PID_RELAUNCH_ICON = 3
_PID_RELAUNCH_NAME = 4
_PID_AUMID = 5

# HWNDs already stamped with relaunch properties this session — the icon-setter
# thread re-polls every 300 ms and the COM property-store dance is not free.
_RELAUNCH_STAMPED: set[int] = set()


def set_window_relaunch_properties(
    hwnd: int,
    *,
    ico_path: Path | None = None,
    aumid: str = APP_USER_MODEL_ID,
    display_name: str = APP_DISPLAY_NAME,
) -> bool:
    """Stamp per-window AppUserModel Relaunch* properties → taskbar shows OUR icon.

    THE universal taskbar-icon fix, and the one that finally covers every install
    (verified live on an MS-Store-Python machine, where exe branding is
    impossible): without explicit window properties, the Windows taskbar renders
    a button with the icon of the window-owning EXECUTABLE — for a source run
    that is ``pythonw.exe`` → the Python logo, no matter what ``WM_SETICON`` /
    class icon / AUMID / Start-Menu shortcut say (all verified ineffective on the
    button). ``SHGetPropertyStoreForWindow`` +
    ``System.AppUserModel.RelaunchIconResource`` exists precisely for
    interpreter-hosted apps: it tells the shell, per window, which icon (and
    name/relaunch command, used when the button is pinned) the button carries.
    Takes effect immediately on a live window — no restart, no exe copy.

    Idempotent per HWND (session-cached), Windows-only, best-effort: any COM /
    pywin32 hiccup returns ``False`` and the window keeps whatever the other
    layers achieved. Returns ``True`` when the properties were committed.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    if hwnd in _RELAUNCH_STAMPED:
        return True
    try:
        import pywintypes
        from win32com.propsys import propsys

        try:
            # The icon-setter poll runs on a plain daemon thread with no COM
            # apartment; initialize one (idempotent, "already init" is fine).
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:  # noqa: BLE001 — already initialized / free-threaded
            pass

        ico = ico_path or project_icon_path()
        fmtid = pywintypes.IID(_APPUSERMODEL_FMTID)
        store = propsys.SHGetPropertyStoreForWindow(
            hwnd, propsys.IID_IPropertyStore
        )
        store.SetValue((fmtid, _PID_AUMID), propsys.PROPVARIANTType(aumid))
        if ico.is_file():
            store.SetValue(
                (fmtid, _PID_RELAUNCH_ICON), propsys.PROPVARIANTType(f"{ico},0")
            )
        store.SetValue(
            (fmtid, _PID_RELAUNCH_NAME), propsys.PROPVARIANTType(display_name)
        )
        pythonw = _pythonw_executable()
        if pythonw is not None:
            store.SetValue(
                (fmtid, _PID_RELAUNCH_COMMAND),
                propsys.PROPVARIANTType(f'"{pythonw}" -m {_LAUNCHER_MODULE}'),
            )
        store.Commit()
        _RELAUNCH_STAMPED.add(hwnd)
        logger.debug("Relaunch properties stamped on hwnd={}", hwnd)
        return True
    except Exception as exc:  # noqa: BLE001 — cosmetic layer, never load-bearing
        logger.debug("relaunch properties could not be stamped: {}", exc)
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

    # Per-window relaunch properties FIRST — the only layer the taskbar button
    # honours on every install (incl. MS-Store Python, where the branded-exe
    # re-exec cannot run). The class/WM_SETICON work below still covers the
    # titlebar + Alt-Tab surfaces.
    set_window_relaunch_properties(hwnd, ico_path=ico_path)

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


def apply_tk_window_icon(root: Any) -> None:
    """Give a Tkinter root/Toplevel the Jarvis mascot icon on **every** OS.

    Tkinter registers its window class *without* a class-icon slot, so a
    ``python -m …`` launch leaves every Tk window inheriting the interpreter's
    process icon: on Windows the taskbar/titlebar falls back to
    ``pythonw.exe`` → the blue/yellow Python logo, on Linux to the generic
    ``python3`` interpreter icon. Both are the same "shows Python, not Jarvis"
    symptom (BUG #UI-Pin-2026-05-05). Any Tk surface — the JarvisBar, the orb,
    any future Tk dialog — must call this once, right after creating its root,
    or it will visibly regress to the Python logo.

    Two OS-specific paths, because the toolkits read different surfaces:

    **Windows** — the taskbar renders the window *class* icon, and the
    highest-fidelity source is the multi-resolution ``jarvis.ico``:

      1. ``ensure_windows_app_identity`` — group this process under the Jarvis
         taskbar button (idempotent across processes).
      2. ``iconbitmap(default=.ico)`` — Tk-level icon for all toplevels.
      3. ``WM_SETICON`` + ``SetClassLongPtrW`` — the Win32 class-icon override,
         the only surface the taskbar actually reads.

    ``iconphoto`` (the PNG path) is deliberately NOT used on Windows: Tk
    re-asserts a ``PhotoImage``-derived class icon on later map/update cycles,
    which races and overwrites our ``SetClassLongPtrW`` — the live window ended
    up with a blank/greyed class icon. The ``.ico`` + Win32 path is the proven
    one (BUG #UI-Pin-2026-05-05).

    **Linux / macOS** — Tk exposes no class-icon slot to Win32, but its portable
    ``root.iconphoto`` sets ``_NET_WM_ICON``, which is exactly what the
    dock/taskbar reads. It needs a PNG (most Linux desktops and Tk cannot decode
    a Windows ``.ico``). The ``PhotoImage`` is stashed on the root because Tk
    keeps no reference — without it Python garbage-collects the image and the
    icon silently reverts to the generic ``python3`` interpreter icon.

    Every step is wrapped: the bar/orb are cosmetic and must never crash — or
    block their Tk mainloop — on an icon hiccup. Must run on the Tk thread that
    owns ``root`` (``winfo_id`` / ``PhotoImage`` are thread-affine).
    """
    if sys.platform == "win32":
        ensure_windows_app_identity()
        ico_path = project_icon_path()
        if not ico_path.is_file():
            return
        try:
            root.iconbitmap(default=str(ico_path))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Tk iconbitmap could not be applied: {}", exc)
        try:
            hwnd = int(root.winfo_id())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Tk winfo_id() unavailable; class icon not set: {}", exc)
            return
        set_window_icon_by_hwnd(hwnd, ico_path)
        return

    # Linux / macOS — portable Tk icon via the PNG (_NET_WM_ICON).
    try:
        from jarvis.assets import bundled_app_icon_png

        png = bundled_app_icon_png()
    except Exception as exc:  # noqa: BLE001
        logger.debug("bundled PNG icon lookup failed: {}", exc)
        png = None
    if png is None or not png.is_file():
        return
    try:
        import tkinter as tk

        photo = tk.PhotoImage(file=str(png), master=root)
        root.iconphoto(True, photo)
        # Tk holds no reference to the image; pin it to the root so it is not
        # garbage-collected out from under the window icon.
        root._jarvis_icon_photo = photo  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.debug("Tk iconphoto could not be applied: {}", exc)


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
