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
    """Looks for a visible window with a title substring and focuses it.

    Returns:
        (found, message). ``found`` is True when a matching window
        was found AND successfully focused. ``message`` contains
        either the window title or a failure reason.
    """
    if os.name != "nt":
        raise RuntimeError("Window switch is only available on Windows")

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
        return False, f"No visible window found with title substring '{title_contains}'"

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
            f"Window '{title}' found, but setting focus failed "
            "(foreground lock — the user must press Alt+Tab manually)"
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


# ----------------------------------------------------------------------
# G8b — move a window onto the primary monitor
# ----------------------------------------------------------------------


def window_is_on_monitor(rect: tuple[int, int, int, int], monitor: dict) -> bool:
    """True when the window's CENTER lies within ``monitor`` (an mss-style dict
    with left/top/width/height). Center-based, so a window straddling two screens
    counts as being on the one showing most of it. Pure; never raises."""
    left, top, width, height = rect
    cx, cy = left + width // 2, top + height // 2
    ml, mt = int(monitor.get("left", 0)), int(monitor.get("top", 0))
    mw, mh = int(monitor.get("width", 0)), int(monitor.get("height", 0))
    return ml <= cx < ml + mw and mt <= cy < mt + mh


def window_rect(win: WindowInfo) -> tuple[int, int, int, int] | None:
    """A window's ``(left, top, width, height)`` in virtual-desktop coordinates,
    or ``None`` when it cannot be read. Windows: ``GetWindowRect`` by hwnd.
    macOS/Linux: not read today (the move path queries position differently).
    Best-effort; never raises."""
    try:
        if detect_platform() == "win32" and win.handle:
            return _window_rect_windows(int(win.handle))
        return None
    except Exception:  # noqa: BLE001
        log.debug("window_rect failed", exc_info=True)
        return None


def foreground_window() -> WindowInfo | None:
    """The current foreground window as a :class:`WindowInfo`, or ``None``.

    Carries the platform window id in ``handle`` on ALL three platforms
    (Win32 hwnd; macOS CGWindowID via Quartz; X11 window id via xdotool) —
    the id the window-centric capture pipeline resolves rects and native
    grabs through. Hosts without the native backend (no Quartz, no xdotool,
    Wayland) degrade to a title-only WindowInfo or ``None``. Best-effort;
    never raises.
    """
    try:
        plat = detect_platform()
        if plat == "win32":
            return _foreground_window_windows()
        if plat == "darwin":
            return _foreground_window_macos()
        if plat == "linux":
            return _foreground_window_linux()
        title = get_foreground_title()
        return WindowInfo(title=title) if title else None
    except Exception:  # noqa: BLE001
        log.debug("foreground_window failed", exc_info=True)
        return None


def _foreground_window_windows() -> WindowInfo | None:
    import ctypes  # noqa: PLC0415

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = int(user32.GetWindowTextLengthW(hwnd))
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return WindowInfo(title=buf.value or "", handle=int(hwnd))


def move_window_to_primary(
    win: WindowInfo, primary: dict
) -> tuple[bool, str]:
    """Bring ``win`` onto the primary monitor ``primary`` (mss-style geometry
    dict) so Computer-Use acts on the main screen (audit G8b).

    Returns ``(moved, message)``. Windows restores a maximized window, moves it
    onto the primary, then re-maximizes there. macOS/Linux-X11 are best-effort;
    Wayland and headless sessions REFUSE honestly with a clear message rather
    than acting on the wrong screen blind. Never raises."""
    try:
        plat = detect_platform()
        if plat == "win32":
            return _move_to_primary_windows(win, primary)
        if plat == "darwin":
            # AX kAXPositionAttribute needs pyobjc + Accessibility permission; the
            # real impl is a tracked follow-up. Honest refusal, no fake pass.
            return False, (
                "Moving a window to the main screen is not supported on macOS yet "
                "— please drag it to your main display, then retry."
            )
        if plat == "linux":
            if is_wayland():
                return False, (
                    "Cannot move the window on Wayland (no global window "
                    "positioning by OS design) — drag it to your main screen "
                    "manually, then retry."
                )
            if not display_present():
                return False, (
                    "Cannot move the window: this looks like a headless session "
                    "with no display."
                )
            return _move_to_primary_linux(win, primary)
        return False, f"Moving windows is not supported on this platform ({plat})."
    except Exception as exc:  # noqa: BLE001
        log.debug("move_window_to_primary failed", exc_info=True)
        return False, f"Window move failed: {exc}"


def _window_rect_windows(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _move_to_primary_windows(win: WindowInfo, primary: dict) -> tuple[bool, str]:
    """Restore-if-maximized → SetWindowPos onto the primary → re-maximize there."""
    if not win.handle:
        return False, "no window handle to move"
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32
    hwnd = int(win.handle)

    class _WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.UINT), ("flags", wintypes.UINT),
            ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
            ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", wintypes.RECT),
        ]

    _SW_RESTORE, _SW_MAXIMIZE, _SW_SHOWMAXIMIZED = 9, 3, 3
    _SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010

    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    was_maximized = False
    if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        was_maximized = wp.showCmd == _SW_SHOWMAXIMIZED
    if was_maximized:
        user32.ShowWindow(hwnd, _SW_RESTORE)
    # Inset a little so the title bar is fully on the primary (never off-screen).
    px = int(primary.get("left", 0)) + 40
    py = int(primary.get("top", 0)) + 40
    moved = bool(user32.SetWindowPos(
        hwnd, 0, px, py, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE,
    ))
    if was_maximized:
        user32.ShowWindow(hwnd, _SW_MAXIMIZE)  # re-maximizes on the primary now
    if not moved:
        return False, "SetWindowPos failed"
    return True, f"moved '{(win.title or 'window')[:40]}' onto the primary monitor"


# ----------------------------------------------------------------------
# Window-scoped Computer-Use helpers (industry practice: the model sees the
# TARGET WINDOW, not a whole monitor) — precise frame rect, shell detection,
# and in-place maximize. All best-effort; never raise into a caller.
# ----------------------------------------------------------------------

#: Top-level window classes that are the Windows shell itself (desktop /
#: taskbar). Capturing or "normalizing" these would act on the wallpaper.
_SHELL_WINDOW_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "SHELLDLL_DefView",
})


def _window_class_windows(hwnd: int) -> str:
    import ctypes  # noqa: PLC0415

    buf = ctypes.create_unicode_buffer(256)
    if not ctypes.windll.user32.GetClassNameW(hwnd, buf, 256):
        return ""
    return buf.value or ""


def is_shell_window(win: WindowInfo) -> bool:
    """True when ``win`` is the desktop/taskbar shell rather than an app window.

    Clicking "into" these means clicking the wallpaper — a Computer-Use
    capture or window-normalize must fall back to monitor scope instead.
    """
    try:
        if detect_platform() == "win32" and win.handle:
            return _window_class_windows(int(win.handle)) in _SHELL_WINDOW_CLASSES
        return (win.title or "").strip() == "Program Manager"
    except Exception:  # noqa: BLE001
        log.debug("is_shell_window failed", exc_info=True)
        return False


def window_frame_rect(win: WindowInfo) -> tuple[int, int, int, int] | None:
    """The window's VISIBLE frame ``(left, top, width, height)`` in
    virtual-desktop coordinates, or ``None`` when unavailable.

    Windows: ``DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`` — the rect
    the user actually sees. Plain ``GetWindowRect`` includes the invisible
    DWM resize-shadow border (~7 px per side), which would leak wallpaper
    into a window-scoped capture and skew every mapped click. Falls back to
    ``GetWindowRect`` when DWM is unavailable.

    NOTE: physical-pixel correctness on mixed-DPI desktops requires the
    calling thread to hold a per-monitor DPI context — Computer-Use callers
    invoke this inside :func:`jarvis.cu.geometry.input_space`.

    macOS/Linux return ``None`` today — callers keep their monitor-scope
    fallback (same graceful degrade as :func:`window_rect`).
    """
    try:
        if detect_platform() != "win32" or not win.handle:
            return None
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        hwnd = int(win.handle)
        rect = wintypes.RECT()
        _DWMWA_EXTENDED_FRAME_BOUNDS = 9
        try:
            res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.wintypes.DWORD(_DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
        except (OSError, AttributeError):
            res = 1  # dwmapi missing — fall through to GetWindowRect
        if res != 0 and not ctypes.windll.user32.GetWindowRect(
            hwnd, ctypes.byref(rect)
        ):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return (rect.left, rect.top, width, height)
    except Exception:  # noqa: BLE001
        log.debug("window_frame_rect failed", exc_info=True)
        return None


def window_is_maximized(win: WindowInfo) -> bool | None:
    """Whether the window is maximized; ``None`` when it cannot be read
    (non-Windows today — callers must treat that as "don't touch")."""
    try:
        if detect_platform() != "win32" or not win.handle:
            return None
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT),
            ]

        _SW_SHOWMAXIMIZED = 3
        wp = _WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
        if not ctypes.windll.user32.GetWindowPlacement(
            int(win.handle), ctypes.byref(wp)
        ):
            return None
        return wp.showCmd == _SW_SHOWMAXIMIZED
    except Exception:  # noqa: BLE001
        log.debug("window_is_maximized failed", exc_info=True)
        return None


def maximize_window(win: WindowInfo) -> tuple[bool, str]:
    """Maximize ``win`` ON ITS CURRENT monitor (never a cross-monitor move —
    moving across a DPI boundary is what shrinks/mangles windows).

    Windows: only windows that advertise a maximize box are touched — a fixed
    dialog ("Save as…") must never be blown up. macOS/Linux-X11 are
    best-effort; Wayland/headless refuse honestly. Returns ``(ok, message)``,
    never raises.
    """
    try:
        plat = detect_platform()
        if plat == "win32":
            if not win.handle:
                return False, "no window handle to maximize"
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32
            hwnd = int(win.handle)
            _GWL_STYLE, _WS_MAXIMIZEBOX = -16, 0x00010000
            _SW_MAXIMIZE = 3
            user32.GetWindowLongW.restype = ctypes.c_long
            style = int(user32.GetWindowLongW(hwnd, _GWL_STYLE))
            if not style & _WS_MAXIMIZEBOX:
                return False, "window has no maximize box (fixed-size dialog)"
            user32.ShowWindow(hwnd, _SW_MAXIMIZE)
            return True, f"maximized '{(win.title or 'window')[:40]}'"
        if plat == "darwin":
            if shutil.which("osascript") is None:
                return False, "osascript not found"
            # AXZoomed = the native green-button "fill the screen" zoom (NOT
            # the separate macOS full-screen Space). Apps without the
            # attribute error out -> honest failure, window untouched.
            script = (
                'tell application "System Events"\n'
                "  set frontProc to first application process whose frontmost is true\n"
                '  set value of attribute "AXZoomed" of front window of frontProc to true\n'
                "end tell\n"
            )
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if proc.returncode != 0:
                return False, "could not zoom the front window (Accessibility?)"
            return True, "zoomed the front window to fill the screen"
        if plat == "linux":
            if is_wayland() or not display_present():
                return False, "cannot maximize on Wayland/headless"
            if shutil.which("wmctrl") is None:
                return False, "wmctrl not installed"
            res = subprocess.run(
                ["wmctrl", "-r", (win.title or ""),
                 "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True, timeout=5.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if res.returncode != 0:
                return False, "wmctrl could not maximize the window"
            return True, f"maximized '{(win.title or 'window')[:40]}' (X11)"
        return False, f"maximize not supported on {plat}"
    except Exception as exc:  # noqa: BLE001
        log.debug("maximize_window failed", exc_info=True)
        return False, f"maximize failed: {exc}"


def normalize_foreground_window() -> tuple[bool, str]:
    """Bring the CURRENT foreground window into the state professional
    computer-use setups act on: maximized on its own monitor.

    Every reference harness normalizes its environment before pixel-grounded
    clicking (OpenAI CUA: fixed viewport; Anthropic: one small display with
    the app filling it; Microsoft UFO: per-application scope). On a live
    desktop the equivalent is "the target window fills its monitor": a small
    floating window on a 4K screen turns into stamp-sized UI in the model's
    downscaled frame, and every grounding error lands on the wallpaper
    (live incident 2026-07-02: three clicks in a row hit the desktop next
    to a restored Chrome and stole its focus).

    Shell windows and windows that cannot maximize are left untouched.
    Synchronous — CU calls it via ``asyncio.to_thread``. Never raises.
    """
    try:
        win = foreground_window()
        if win is None:
            return False, "no foreground window"
        if is_shell_window(win):
            return False, "foreground is the desktop shell"
        if window_is_maximized(win) is not False:
            return False, "already maximized (or unknown state)"
        return maximize_window(win)
    except Exception as exc:  # noqa: BLE001
        log.debug("normalize_foreground_window failed", exc_info=True)
        return False, f"normalize failed: {exc}"


def _move_to_primary_linux(win: WindowInfo, primary: dict) -> tuple[bool, str]:
    """Best-effort X11 move via ``wmctrl`` (un-maximize, then reposition by title).
    Unverified on a real desktop; returns honest status."""
    import subprocess  # noqa: PLC0415

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

    title = (win.title or "").strip()
    if not title:
        return False, "no window title to target"
    px, py = int(primary.get("left", 0)), int(primary.get("top", 0))
    try:
        subprocess.run(
            ["wmctrl", "-r", title, "-b", "remove,maximized_vert,maximized_horz"],
            capture_output=True, timeout=3.0, creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        res = subprocess.run(
            ["wmctrl", "-r", title, "-e", f"0,{px},{py},-1,-1"],
            capture_output=True, timeout=3.0, creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except FileNotFoundError:
        return False, "wmctrl not installed — cannot move the window on X11"
    except Exception as exc:  # noqa: BLE001
        return False, f"wmctrl move failed: {exc}"
    if res.returncode != 0:
        return False, f"wmctrl could not find/move a window titled '{title[:40]}'"
    return True, f"moved '{title[:40]}' onto the primary monitor (X11/wmctrl)"


__all__ = [
    "WindowInfo",
    "list_windows",
    "get_foreground_title",
    "focus_window",
    "raise_window",
    "is_app_running",
    "raise_after_launch",
    "window_is_on_monitor",
    "window_rect",
    "foreground_window",
    "move_window_to_primary",
]
