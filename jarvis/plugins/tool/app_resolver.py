"""Alias resolution for local app launches.

Windows has two "find a program" mechanisms: the ``PATH`` (used by
``subprocess``) and the **App Paths registry**
(``HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\<exe>``) used
by the Win+R Run dialog and the shell ``start`` verb. GUI apps such as Chrome
register *only* in App Paths and are absent from ``PATH``; worse,
``os.startfile("chrome")`` performs a ShellExecute that silently does nothing
(no exception) when the bare name resolves to neither a file nor a verb.

That silent no-op was the root cause of "Jarvis says it opened Chrome but
nothing happens". The resolver therefore looks an app up in App Paths / PATH
*before* falling back to ``os.startfile``, handing the launcher an absolute,
verified executable so ``subprocess.Popen`` actually starts it (and raises a
real error when it can't).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Literal

from jarvis.platform import detect_platform

try:  # Windows-only stdlib module; the package must still import on a Linux VPS.
    import winreg  # type: ignore[import]
except ImportError:  # pragma: no cover - exercised only on non-Windows runtimes
    winreg = None  # type: ignore[assignment]


# ``startfile``/``executable`` are the Windows verbs (kept untouched per AD-7);
# ``open_a`` is the macOS ``open -a <AppName>`` launcher and ``xdg_open`` is the
# Linux ``xdg-open <name>`` MIME/.desktop handler. Every kind is OS-agnostic at
# the type level — the launcher in open_app.py branches on the chosen kind.
LaunchKind = Literal["startfile", "executable", "open_a", "xdg_open"]


@dataclass(frozen=True, slots=True)
class LaunchTarget:
    kind: LaunchKind
    value: str


_TERMINAL_ALIASES = {
    "terminal",
    "windows terminal",
    "windowsterminal",
}

_DIRECT_EXECUTABLES = {
    "cmd",
    "powershell",
    "pwsh",
}

# Built-in Windows Store / UWP apps register a protocol URI, NOT an .exe on
# PATH or in App Paths — ``os.startfile("ms-windows-store:")`` launches them.
# Without this the resolver fell through to ``startfile("microsoft store")``
# which the open_app plausibility gate rejected as "not found", forcing the
# computer-use loop into a clumsy taskbar-search detour (live 2026-06-22: "open
# the Microsoft Store" → rejected → Windows-search workaround → double-typed the
# query). The protocol launch is direct and reliable. Checked BEFORE App
# Paths/PATH so a stray ``store.exe`` cannot shadow the real Store.
_UWP_PROTOCOLS = {
    "microsoft store": "ms-windows-store:",
    "windows store": "ms-windows-store:",
    "store": "ms-windows-store:",
}

# Voice/whitelist alias -> the executable *basename* the OS actually knows.
# (Chrome's exe is ``chrome.exe`` so it needs no entry; Edge/Word/PowerPoint
# carry historic names that differ from how a user says them.) The map is
# platform-conditional (AD-15): on macOS a voice alias maps to the ``.app``
# display name handed to ``open -a`` (``vscode`` -> "Visual Studio Code"); on
# Linux it maps to the executable on ``PATH`` (``vscode`` -> "code").
_EXE_ALIASES_WIN = {
    "edge": "msedge",
    "word": "winword",
    "powerpoint": "powerpnt",
    "vscode": "code",
    "calculator": "calc",
}

_EXE_ALIASES_DARWIN = {
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "calc": "Calculator",
    "chrome": "Google Chrome",
    "terminal": "Terminal",
    "finder": "Finder",
}

_EXE_ALIASES_LINUX = {
    "vscode": "code",
    "calc": "gnome-calculator",
    "calculator": "gnome-calculator",
    "chrome": "google-chrome",
    "terminal": "gnome-terminal",
    "files": "nautilus",
}


def _exe_aliases() -> dict[str, str]:
    """Return the active voice-alias -> executable map for this platform."""
    plat = detect_platform()
    if plat == "win32":
        return _EXE_ALIASES_WIN
    if plat == "darwin":
        return _EXE_ALIASES_DARWIN
    return _EXE_ALIASES_LINUX


# Backwards-compatible module attribute: existing tests/readers reference the
# Windows alias map by name. The active map is selected at call time via
# ``_exe_aliases()``; this constant stays the Windows view.
_EXE_ALIASES = _EXE_ALIASES_WIN

_APP_PATHS_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def _resolve_via_app_paths(exe_name: str) -> str | None:
    """Return the absolute path of ``exe_name`` from the App Paths registry.

    This is the only place GUI apps like Chrome register their launch path —
    they are not on ``PATH``. Checks HKCU first (per-user installs) then HKLM
    (machine-wide). Returns ``None`` when not registered, when the registered
    path no longer exists on disk, or on a non-Windows runtime.
    """
    if winreg is None:
        return None
    subkey = _APP_PATHS_SUBKEY + "\\" + exe_name
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, None)  # default value = full path
        except OSError:
            continue
        path = str(value).strip().strip('"')
        if path and os.path.exists(path):
            return path
    return None


def _start_menu_roots() -> list[str]:
    """Return the existing Start Menu ``Programs`` roots (per-user + machine-wide).

    These trees hold the ``.lnk`` shortcuts Windows installers create and that
    the Start Menu / search box use to launch apps by friendly name. Many apps
    register ONLY a Start Menu shortcut — per-user Squirrel/Electron installs
    (Discord, Slack) land in ``%LOCALAPPDATA%`` and appear in neither App Paths
    nor ``PATH``. Returns only roots that exist on disk; empty on a non-Windows
    runtime or when the env vars are unset.
    """
    roots: list[str] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs"))
    programdata = os.environ.get("ProgramData")
    if programdata:
        roots.append(os.path.join(programdata, "Microsoft", "Windows", "Start Menu", "Programs"))
    return [r for r in roots if os.path.isdir(r)]


def _resolve_via_start_menu(candidates: set[str], roots: list[str] | None = None) -> str | None:
    """Return the absolute path of a Start Menu ``.lnk`` whose file stem matches
    one of ``candidates`` (case-insensitive, EXACT stem), else ``None``.

    Walks the per-user and machine-wide ``Programs`` trees. Exact-stem matching
    keeps it deterministic and avoids launching the wrong app: ``chrome`` must
    not match ``Chrome Remote Desktop.lnk`` (Chrome resolves earlier via App
    Paths anyway), and a prefix like ``disc`` must not match ``Discord.lnk``.
    The resolved shortcut is launched via ``os.startfile`` by the caller, which
    follows the ``.lnk`` to its real target. Never raises.
    """
    wanted = {c.strip().lower() for c in candidates if c and c.strip()}
    if not wanted:
        return None
    if roots is None:
        roots = _start_menu_roots()
    for root in roots:
        try:
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    if fn.lower().endswith(".lnk") and fn[:-4].strip().lower() in wanted:
                        return os.path.join(dirpath, fn)
        except OSError:
            continue
    return None


def resolve_app_launch_target(app_name: str) -> LaunchTarget:
    """Resolve a voice alias / app name to a concrete, launchable target.

    The URL/path escape hatch and the Spotify protocol case are OS-agnostic and
    run on every platform. The GUI-app resolution then branches on
    ``detect_platform()`` (AD-6, AD-15): the Windows path (App Paths + ``.exe``
    + ``startfile`` last resort) is kept verbatim (AD-7); macOS prefers ``open
    -a <AppName>`` and falls back to ``shutil.which`` for CLI tools; Linux uses
    ``shutil.which`` for direct executables and otherwise hands the name to
    ``xdg-open``. No branch raises (AD-6).
    """
    raw = app_name.strip()
    normalized = raw.lower()
    normalized_without_exe = normalized.removesuffix(".exe")

    # Explicit URLs and filesystem paths go straight to the shell — os.startfile
    # opens both correctly and we must not mangle them.
    if raw.startswith(("http://", "https://", "file://")) or any(
        sep in raw for sep in (":\\", ":/")
    ) or raw.startswith((".", "\\", "/")):
        return LaunchTarget("startfile", raw)

    # Protocol launchers (Spotify desktop registers the spotify: URI scheme).
    if normalized_without_exe == "spotify":
        return LaunchTarget("startfile", "spotify:")

    plat = detect_platform()
    if plat == "win32":
        return _resolve_windows(normalized, normalized_without_exe, raw)
    if plat == "darwin":
        return _resolve_darwin(normalized_without_exe)
    return _resolve_linux(normalized_without_exe)


def _resolve_windows(
    normalized: str, normalized_without_exe: str, raw: str
) -> LaunchTarget:
    """Windows resolution (kept verbatim per AD-7 — App Paths + PATH + startfile)."""
    if normalized in _TERMINAL_ALIASES:
        return LaunchTarget("executable", "wt")

    if normalized in _DIRECT_EXECUTABLES:
        return LaunchTarget("executable", normalized)

    # Built-in Store / UWP apps launch via a protocol URI (no .exe to find).
    # Checked before App Paths/PATH so a stray ``store.exe`` cannot shadow the
    # real Microsoft Store (live 2026-06-22).
    uwp = _UWP_PROTOCOLS.get(normalized_without_exe)
    if uwp is not None:
        return LaunchTarget("startfile", uwp)

    # GUI apps (Chrome, Word, ...) — resolve to an absolute exe so Popen can
    # actually launch them. App Paths first (covers apps that are NOT on PATH),
    # then PATH (covers notepad/calc/explorer/wt and dev tools like `code`).
    canonical = _EXE_ALIASES_WIN.get(normalized_without_exe, normalized_without_exe)
    full_path = _resolve_via_app_paths(canonical + ".exe")
    if full_path is None:
        full_path = shutil.which(canonical) or shutil.which(normalized)
    if full_path:
        return LaunchTarget("executable", full_path)

    # Start Menu shortcut fallback (live 2026-06-09). Apps like Discord/Slack
    # install a per-user Squirrel build registered ONLY as a Start Menu .lnk —
    # absent from both App Paths and PATH. Without this step the resolver fell
    # through to os.startfile("discord"), which raises FileNotFoundError
    # ("Anwendung 'discord' nicht gefunden"), and the computer-use loop was then
    # forced into unreliable taskbar pixel-clicking (it hit Spotify, the icon
    # next to Discord). Match the friendly name AND the canonical alias to a
    # shortcut and hand the launcher that .lnk (os.startfile follows it).
    lnk = _resolve_via_start_menu({normalized_without_exe, canonical})
    if lnk:
        return LaunchTarget("startfile", lnk)

    # Last resort: hand the raw name to the shell. The plausibility gate in
    # open_app has already vetted that this is a whitelisted/known name, so this
    # only fires for entries the OS resolver couldn't pin to a path.
    return LaunchTarget("startfile", raw)


def _resolve_darwin(normalized_without_exe: str) -> LaunchTarget:
    """macOS resolution: prefer ``open -a <AppName>``, fall back to PATH CLIs.

    ``open -a`` resolves ``.app`` bundles by display name, so a CLI tool that is
    on ``PATH`` (e.g. ``python``, ``git``) is launched directly while everything
    else is handed to the system app-launcher. No branch raises (AD-6).
    """
    canonical = _EXE_ALIASES_DARWIN.get(normalized_without_exe, normalized_without_exe)
    # Direct CLI tool on PATH -> launch it as an executable.
    on_path = shutil.which(normalized_without_exe)
    if on_path:
        return LaunchTarget("executable", on_path)
    # Otherwise let `open -a` resolve the .app bundle by display name.
    return LaunchTarget("open_a", canonical)


def _resolve_linux(normalized_without_exe: str) -> LaunchTarget:
    """Linux resolution: PATH executable first, else ``xdg-open <name>``.

    A direct executable on ``PATH`` (``firefox``, ``code``, ``nautilus``) is
    launched as such; anything else is handed to ``xdg-open`` for .desktop/MIME
    handling. No branch raises (AD-6).
    """
    canonical = _EXE_ALIASES_LINUX.get(normalized_without_exe, normalized_without_exe)
    full_path = shutil.which(canonical) or shutil.which(normalized_without_exe)
    if full_path:
        return LaunchTarget("executable", full_path)
    return LaunchTarget("xdg_open", canonical)
