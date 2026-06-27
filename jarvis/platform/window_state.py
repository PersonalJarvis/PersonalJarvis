"""Reusable, cross-platform window state: enumerate open windows, read the
foreground title, focus a window by title, and detect whether an app is already
running.

This is the single home for the window logic that previously lived only inside
``jarvis/plugins/tool/switch_window.py``. ``switch_window`` now re-exports and
delegates to the focus helpers here, and ``open_app`` / the Computer-Use loop use
``is_app_running`` / ``focus_window`` / ``list_windows`` so CU stops re-launching
apps that are already open and can plan with awareness of what is on screen.

Design contract (matches the platform seam, AD-5/AD-6/AD-13):
* Every public function is best-effort and NEVER raises into a caller — a missing
  tool, a denied permission, a headless / Wayland session, or a native-call error
  degrades to an empty result, not an exception.
* The Windows ctypes path is moved verbatim from ``switch_window`` (AD-7: behavior
  unchanged); macOS (osascript) and Linux/X11 (wmctrl) are the cross-platform
  siblings. Wayland and headless sessions degrade cleanly.
* ``is_app_running`` is conservative: it only reports a match when an app token
  (>=3 chars) clearly appears in a window title. When uncertain it returns
  ``None`` so the caller falls through to a normal launch — a false negative just
  reproduces today's behavior; a false positive (focusing instead of launching)
  is the worse error, so we bias against it.

Import-cleanliness (HN-7): no platform-only package is imported at module scope;
``ctypes`` and friends are imported lazily inside the function bodies.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform
from jarvis.platform.probes import display_present, is_wayland

log = logging.getLogger(__name__)

_MIN_TOKEN_LEN = 3

# Known cases where the app token a user/planner says differs from the window
# title. Most friendly names ARE a substring of the title (chrome → "Google
# Chrome", spotify → "Spotify Premium"), so this stays tiny on purpose.
_TITLE_ALIASES: dict[str, tuple[str, ...]] = {
    "calc": ("calculator",),
}


@dataclass(frozen=True)
class WindowInfo:
    """A single visible top-level window.

    ``handle`` is an opaque platform handle (the Win32 ``hwnd`` on Windows,
    ``None`` on macOS/Linux where the backends address windows by title).
    """

    title: str
    minimized: bool = False
    handle: int | None = None


# ----------------------------------------------------------------------
# Windows backend (ctypes) — focus path moved verbatim from switch_window (AD-7)
# ----------------------------------------------------------------------


def _find_and_focus_windows(title_contains: str) -> tuple[bool, str]:
    """Sucht nach einem sichtbaren Fenster mit Titel-Substring und fokussiert es.

    Returns:
        (found, message). ``found`` ist True wenn ein passendes Fenster
        gefunden UND erfolgreich fokussiert wurde. ``message`` enthaelt
        entweder den Fenster-Titel oder eine Fehlerursache.
    """
    if os.name != "nt":
        raise RuntimeError("Window-Switch ist nur auf Windows verfuegbar")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible

    needle = title_contains.lower()
    found_hwnd: list[int] = []
    found_title: list[str] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        if needle in title.lower():
            found_hwnd.append(hwnd)
            found_title.append(title)
            return False  # Stop enumeration
        return True

    EnumWindows(EnumWindowsProc(_callback), 0)

    if not found_hwnd:
        return False, f"Kein sichtbares Fenster mit Titel-Substring '{title_contains}' gefunden"

    hwnd = found_hwnd[0]
    title = found_title[0]

    # Raise through the HARDENED foreground path (restore-if-minimized +
    # AttachThreadInput) — a plain SetForegroundWindow is refused under the
    # Windows foreground-lock and left an already-open app behind everything
    # (the "WhatsApp opened in the background" report). This unifies every
    # foreground site (switch_window, the CU settle fallback, the open_app
    # already-running path) on one robust mechanism.
    if not _force_foreground_windows(hwnd):
        return False, (
            f"Fenster '{title}' gefunden, aber Fokus-Setzen scheiterte "
            "(Foreground-Lock — User muss Alt+Tab manuell drücken)"
        )
    return True, title


def _force_foreground_windows(hwnd: int) -> bool:
    """Forcefully bring a window to the foreground on Windows, defeating the
    foreground-lock-steal restriction.

    ``SetForegroundWindow`` alone is refused for a background process unless
    Windows currently grants it foreground privilege (recent user input, or it
    launched the target). When it is refused, a freshly launched app is left in
    the background — and the Computer-Use foreground-following screenshot then
    captures the wrong screen. The robust path attaches our input queue to both
    the current-foreground thread and the target window's thread, raises with
    ``BringWindowToTop`` + ``SetForegroundWindow`` + ``SetActiveWindow``, and
    ALWAYS detaches both queues in ``finally`` (a leaked ``AttachThreadInput``
    deadlocks input routing).

    Returns ``True`` if the foreground was ultimately taken. Best-effort — the
    caller treats the result as advisory. Lazy ``ctypes`` import (module
    import-cleanliness, HN-7). Separate from ``_find_and_focus_windows`` so the
    verbatim ``switch_window`` path (AD-7) stays untouched.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _SW_RESTORE = 9

    get_thread = user32.GetWindowThreadProcessId
    get_thread.restype = wintypes.DWORD
    get_thread.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    # Restore if minimized, then try the cheap plain path first — it succeeds
    # whenever Jarvis launched the process and input is recent.
    user32.ShowWindow(hwnd, _SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        user32.SetActiveWindow(hwnd)
        return True

    cur_thread = kernel32.GetCurrentThreadId()
    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = get_thread(fg_hwnd, None) if fg_hwnd else 0
    target_thread = get_thread(hwnd, None)

    attached_fg = False
    attached_target = False
    try:
        if fg_thread and fg_thread != cur_thread:
            attached_fg = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        if target_thread and target_thread not in (cur_thread, fg_thread):
            attached_target = bool(
                user32.AttachThreadInput(cur_thread, target_thread, True)
            )
        user32.BringWindowToTop(hwnd)
        user32.ShowWindow(hwnd, _SW_RESTORE)
        ok = bool(user32.SetForegroundWindow(hwnd))
        user32.SetActiveWindow(hwnd)
        return ok
    finally:
        if attached_fg:
            user32.AttachThreadInput(cur_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(cur_thread, target_thread, False)


def _list_windows_windows() -> list[WindowInfo]:
    """All visible top-level windows with a non-empty title, plus minimized state."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible
    IsIconic = user32.IsIconic

    out: list[WindowInfo] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        if title.strip():
            out.append(
                WindowInfo(
                    title=title,
                    minimized=bool(IsIconic(hwnd)),
                    handle=int(hwnd) if hwnd else None,
                )
            )
        return True

    EnumWindows(EnumWindowsProc(_callback), 0)
    return out


def _foreground_title_windows() -> str:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


# ----------------------------------------------------------------------
# macOS backend (osascript) — focus path moved verbatim from switch_window (AD-7)
# ----------------------------------------------------------------------


def _find_and_focus_macos(title_contains: str) -> tuple[bool, str]:
    """Bring the first window whose title contains the substring to the front
    via AppleScript / System Events (H2, DEEP-DIVE-AUDIT-2026-06-19).

    Needs the macOS Accessibility grant; without it osascript errors out, which
    is reported as a clear onboarding message instead of a silent no-op. All
    user-facing strings are English (Output-Language Policy).
    """
    if shutil.which("osascript") is None:
        return False, "osascript not found — cannot switch windows on this macOS host."
    # Lowercase for case-insensitive matching (parity with the Linux path), then
    # escape every AppleScript string-literal metacharacter — including newlines/
    # tabs — so a crafted title cannot break out of the `contains "..."` literal
    # and inject statements into the `tell` block (review HIGH).
    needle = (
        title_contains.lower()
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    needle = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", needle)  # strip other control chars
    script = (
        'tell application "System Events"\n'
        "  repeat with proc in (every process whose visible is true)\n"
        "    repeat with w in (every window of proc)\n"
        f'      if (the lowercase of (name of w)) contains "{needle}" then\n'
        "        set frontmost of proc to true\n"
        '        perform action "AXRaise" of w\n'
        "        return name of w\n"
        "      end if\n"
        "    end repeat\n"
        "  end repeat\n"
        "end tell\n"
        'return ""\n'
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "not allowed assistive access" in err.lower() or "-1719" in err:
            return False, (
                "macOS Accessibility permission not granted — grant it in System "
                "Settings > Privacy & Security > Accessibility so Jarvis can switch windows."
            )
        return False, f"osascript window switch failed: {err or proc.returncode}"
    matched = (proc.stdout or "").strip()
    if matched:
        return True, matched
    return False, f"No visible window with title containing '{title_contains}' found."


def _list_windows_macos() -> list[WindowInfo]:
    """Titles of every visible window via System Events (best-effort)."""
    if shutil.which("osascript") is None:
        return []
    script = (
        'set out to ""\n'
        'tell application "System Events"\n'
        "  repeat with proc in (every process whose visible is true)\n"
        "    repeat with w in (every window of proc)\n"
        "      set out to out & (name of w) & linefeed\n"
        "    end repeat\n"
        "  end repeat\n"
        "end tell\n"
        "return out\n"
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        return []
    return [
        WindowInfo(title=line.strip())
        for line in (proc.stdout or "").splitlines()
        if line.strip()
    ]


def _foreground_title_macos() -> str:
    if shutil.which("osascript") is None:
        return ""
    script = (
        'tell application "System Events"\n'
        "  set frontApp to first application process whose frontmost is true\n"
        "  try\n"
        "    return name of first window of frontApp\n"
        "  on error\n"
        "    return name of frontApp\n"
        "  end try\n"
        "end tell\n"
    )
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


# ----------------------------------------------------------------------
# Linux/X11 backend (wmctrl) — focus path moved verbatim from switch_window (AD-7)
# ----------------------------------------------------------------------


def _find_and_focus_linux(title_contains: str) -> tuple[bool, str]:
    """Activate the first window whose title contains the substring via wmctrl
    on X11 (H2). Returns a clear message when wmctrl is absent (the user should
    install it, e.g. ``apt install wmctrl``) or nothing matches. Wayland/headless
    are handled by the caller before this runs.
    """
    if shutil.which("wmctrl") is None:
        return False, (
            "wmctrl not found — install it (e.g. `apt install wmctrl`) to switch "
            "windows on X11."
        )
    listing = subprocess.run(
        ["wmctrl", "-l"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if listing.returncode != 0:
        detail = (listing.stderr or "").strip() or f"exit code {listing.returncode}"
        return False, f"wmctrl could not list windows: {detail}"
    needle = title_contains.lower()
    win_id = ""
    win_title = ""
    for line in (listing.stdout or "").splitlines():
        # wmctrl -l format: <id> <desktop> <host> <title...>
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        if needle in parts[3].lower():
            win_id, win_title = parts[0], parts[3]
            break
    if not win_id:
        return False, f"No visible window with title containing '{title_contains}' found."
    activate = subprocess.run(
        ["wmctrl", "-i", "-a", win_id],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if activate.returncode != 0:
        detail = (activate.stderr or "").strip()
        return False, f"wmctrl could not activate window '{win_title}': {detail}"
    return True, win_title


def _list_windows_linux() -> list[WindowInfo]:
    if shutil.which("wmctrl") is None:
        return []
    listing = subprocess.run(
        ["wmctrl", "-l"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if listing.returncode != 0:
        return []
    out: list[WindowInfo] = []
    for line in (listing.stdout or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        out.append(WindowInfo(title=parts[3]))
    return out


def _foreground_title_linux() -> str:
    if shutil.which("xdotool") is None:
        return ""
    proc = subprocess.run(
        ["xdotool", "getactivewindow", "getwindowname"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


# ----------------------------------------------------------------------
# Public API (platform dispatch + graceful degrade; never raises)
# ----------------------------------------------------------------------


def list_windows() -> list[WindowInfo]:
    """Every visible top-level window. Empty list on headless/Wayland/error."""
    try:
        plat = detect_platform()
        if plat == "win32":
            return _list_windows_windows()
        if plat == "darwin":
            return _list_windows_macos()
        if plat == "linux":
            if is_wayland() or not display_present():
                return []
            return _list_windows_linux()
        return []
    except Exception:  # noqa: BLE001 — best-effort, must never raise into a caller
        log.debug("list_windows failed", exc_info=True)
        return []


def get_foreground_title() -> str:
    """Title of the current foreground window, or "" if unknown."""
    try:
        plat = detect_platform()
        if plat == "win32":
            return _foreground_title_windows()
        if plat == "darwin":
            return _foreground_title_macos()
        if plat == "linux":
            if is_wayland() or not display_present():
                return ""
            return _foreground_title_linux()
        return ""
    except Exception:  # noqa: BLE001
        log.debug("get_foreground_title failed", exc_info=True)
        return ""


def focus_window(title_contains: str) -> tuple[bool, str]:
    """Bring the first window whose title contains the substring to the front.

    Returns ``(found_and_focused, message)``. Degrades cleanly (no raise) on
    Wayland / headless / missing tool / native error.
    """
    try:
        plat = detect_platform()
        if plat == "win32":
            return _find_and_focus_windows(title_contains)
        if plat == "darwin":
            return _find_and_focus_macos(title_contains)
        if plat == "linux":
            if is_wayland():
                return False, (
                    "Window switching is unavailable on Wayland by OS design — "
                    "switch with the dock/overview or the app's own controls."
                )
            if not display_present():
                return False, (
                    "Window switching needs a graphical display; this looks like "
                    "a headless session."
                )
            return _find_and_focus_linux(title_contains)
        return False, f"Window switching is not supported on this platform ({plat})."
    except Exception as exc:  # noqa: BLE001
        log.debug("focus_window failed", exc_info=True)
        return False, f"Window focus failed: {exc}"


def _app_token(app_name: str) -> str:
    """The matching token for an app name — its basename stem if it looks like a
    path/exe, else the trimmed name itself."""
    name = (app_name or "").strip()
    if not name:
        return ""
    if ("\\" in name) or ("/" in name) or name.lower().endswith(".exe"):
        base = os.path.basename(name)
        stem = base.rsplit(".", 1)[0] if "." in base else base
        name = stem or name
    return name.strip()


def _match_window(app_name: str, windows: list[WindowInfo]) -> WindowInfo | None:
    """Conservative match of an app name against open window titles."""
    token = _app_token(app_name).lower()
    if len(token) < _MIN_TOKEN_LEN:
        return None
    needles = [token, *_TITLE_ALIASES.get(token, ())]
    needles = [n for n in needles if len(n) >= _MIN_TOKEN_LEN]
    for w in windows:
        title = (w.title or "").lower()
        if any(n in title for n in needles):
            return w
    return None


def is_app_running(app_name: str) -> WindowInfo | None:
    """The matching open window if ``app_name`` is already running, else ``None``.

    Conservative by design (see module docstring): an uncertain / short token
    returns ``None`` so the caller launches normally rather than wrongly focusing
    an unrelated window.
    """
    try:
        return _match_window(app_name, list_windows())
    except Exception:  # noqa: BLE001
        log.debug("is_app_running failed", exc_info=True)
        return None


def raise_window(win: WindowInfo) -> tuple[bool, str]:
    """Bring a specific already-known window to the foreground via the hardened
    path. On Windows it raises by the window handle directly (precise, no
    re-enumeration, and it works even when the title path would match a sibling
    window); off-Windows it falls back to title-based :func:`focus_window`.
    Best-effort, never raises.

    This is the already-open sibling of :func:`raise_after_launch`: the open_app
    "app is already running -> focus it" path and the CU window-switch use it so
    an already-open-but-backgrounded app (the WhatsApp report) is actually pulled
    to the front instead of left behind.
    """
    try:
        if detect_platform() == "win32" and win.handle:
            return _force_foreground_windows(int(win.handle)), win.title
        return focus_window(win.title)
    except Exception:  # noqa: BLE001 — best-effort, must never raise into a caller
        log.debug("raise_window failed", exc_info=True)
        return False, ""


#: Poll budget for raising a freshly launched app's window to the foreground.
#: A fast-raising app exits on the first poll; only a backgrounded / secondary-
#: monitor / minimized launch consumes the full budget.
_RAISE_POLL_TIMEOUT_S = 3.0
_RAISE_POLL_INTERVAL_S = 0.25


def raise_after_launch(
    app_name: str,
    *,
    timeout_s: float = _RAISE_POLL_TIMEOUT_S,
    poll_s: float = _RAISE_POLL_INTERVAL_S,
) -> tuple[bool, str]:
    """Poll until a window matching ``app_name`` appears, then actively bring it
    to the foreground. Returns ``(raised, title_or_reason)``. Best-effort, never
    raises — a miss just leaves things as they were.

    A fresh launch (``subprocess.Popen`` / ``os.startfile``) is fire-and-forget:
    the new window is frequently left in the background (Windows foreground-lock-
    steal, a secondary monitor, or restored-minimized). The Computer-Use
    screenshot follows the *foreground* window, so it then captures the wrong
    screen and the mission fails. This closes that gap. Cross-platform: Windows
    raises by ``hwnd`` through the hardened :func:`_force_foreground_windows`
    path; macOS/Linux reuse :func:`focus_window` (by title). Synchronous and
    blocking by design — an async caller wraps it in ``asyncio.to_thread`` (as
    the speech/CU paths already do for :func:`focus_window`).
    """
    token = _app_token(app_name)
    if len(token) < _MIN_TOKEN_LEN:
        return False, ""
    try:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            win = _match_window(app_name, list_windows())
            if win is not None:
                return raise_window(win)
            if time.monotonic() >= deadline:
                return False, (
                    f"no window matching '{token}' appeared within {timeout_s:.0f}s"
                )
            time.sleep(max(0.0, poll_s))
    except Exception:  # noqa: BLE001 — best-effort, must never raise into a caller
        log.debug("raise_after_launch failed", exc_info=True)
        return False, ""


__all__ = [
    "WindowInfo",
    "list_windows",
    "get_foreground_title",
    "focus_window",
    "raise_window",
    "is_app_running",
    "raise_after_launch",
]
