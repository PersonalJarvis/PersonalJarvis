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

    # GUI apps (Chrome, Word, ...) — resolve to an absolute exe so Popen can
    # actually launch them. App Paths first (covers apps that are NOT on PATH),
    # then PATH (covers notepad/calc/explorer/wt and dev tools like `code`).
    canonical = _EXE_ALIASES_WIN.get(normalized_without_exe, normalized_without_exe)
    full_path = _resolve_via_app_paths(canonical + ".exe")
    if full_path is None:
        full_path = shutil.which(canonical) or shutil.which(normalized)
    if full_path:
        return LaunchTarget("executable", full_path)

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
