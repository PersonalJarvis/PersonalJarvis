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
* The Windows ctypes path is moved verbatim from ``switch_window`` (AD-7:
  behavior unchanged); macOS uses Quartz/AppKit/Accessibility and Linux/X11
  uses wmctrl. Wayland and headless sessions degrade cleanly.
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
from jarvis.platform.macos_ax import decode_ax_point, decode_ax_size
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

    ``handle`` is an opaque platform handle: Win32 ``hwnd``, macOS
    ``CGWindowID``, or an X11 window id. A degraded title-only probe uses
    ``None``.
    """

    title: str
    minimized: bool = False
    handle: int | None = None
    #: Owning process id, when the platform probe knows it. Only the macOS
    #: foreground path fills this today; it lets the CU foreground guard use
    #: app-level identity where per-window handles churn (see
    #: ``jarvis.cu.target_guard.window_signature``).
    pid: int | None = None


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
# macOS backend (Quartz/AppKit/AX; no System Events Automation grant)
# ----------------------------------------------------------------------


def _macos_window_title(entry: dict) -> str:
    return str(entry.get("kCGWindowName") or entry.get("kCGWindowOwnerName") or "")


def _macos_window_pid(entry: dict) -> int:
    try:
        return int(entry.get("kCGWindowOwnerPID", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _macos_ax_attr(element: object, attribute: str) -> object | None:
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCopyAttributeValue,
        )

        error, value = AXUIElementCopyAttributeValue(element, attribute, None)
        return value if error == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _macos_entry_bounds(entry: dict) -> tuple[float, float, float, float] | None:
    raw = entry.get("kCGWindowBounds") or {}
    try:
        rect = (
            float(raw["X"]),
            float(raw["Y"]),
            float(raw["Width"]),
            float(raw["Height"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return rect if rect[2] > 0 and rect[3] > 0 else None


def _macos_ax_window_bounds(window: object) -> tuple[float, float, float, float] | None:
    point = decode_ax_point(_macos_ax_attr(window, "AXPosition"))
    size = decode_ax_size(_macos_ax_attr(window, "AXSize"))
    if point is None or size is None or size[0] <= 0 or size[1] <= 0:
        return None
    return point[0], point[1], size[0], size[1]


def _macos_rects_match(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    *,
    tolerance: float = 3.0,
) -> bool:
    return all(
        abs(left - right) <= tolerance
        for left, right in zip(first, second, strict=True)
    )


def _select_macos_ax_window(
    windows: list[object],
    entry: dict,
    *,
    title_contains: str | None = None,
    title_equals: str | None = None,
) -> object | None:
    """Correlate a CGWindow entry to exactly one AX window.

    CGWindowID is not exposed as a public AX attribute. Position and size are
    therefore the strongest public correlation key. A unique title is a safe
    fallback for minimized windows whose geometry is unavailable; duplicate
    titles fail closed instead of focusing or resizing an arbitrary sibling.
    """
    cg_bounds = _macos_entry_bounds(entry)
    if cg_bounds is not None:
        geometry_matches = [
            window
            for window in windows
            if (
                (ax_bounds := _macos_ax_window_bounds(window)) is not None
                and _macos_rects_match(cg_bounds, ax_bounds)
            )
        ]
        if len(geometry_matches) == 1:
            return geometry_matches[0]
        if len(geometry_matches) > 1:
            return None

    contains = (title_contains or "").strip().casefold()
    equals = (title_equals or "").strip().casefold()
    title_matches: list[object] = []
    for window in windows:
        title = str(_macos_ax_attr(window, "AXTitle") or "").strip().casefold()
        if (equals and title == equals) or (contains and contains in title):
            title_matches.append(window)
    if len(title_matches) == 1:
        return title_matches[0]
    if not title_matches and len(windows) == 1:
        return windows[0]
    return None


#: Per-call ceiling for AX messages to another app, in seconds. The macOS
#: default is ~6 s PER CALL, so a busy target (Safari mid-page-load) could
#: hold a raise/focus sequence of 4-5 AX calls for well over the CU action
#: timeout — live 2026-07-21: open_app('Safari') burned the full 15 s
#: _ACT_TIMEOUT_S in exactly this path while Safari was already open.
_AX_MESSAGING_TIMEOUT_S = 1.5


def _macos_bound_ax_messaging(
    element, timeout_s: float = _AX_MESSAGING_TIMEOUT_S,
) -> None:
    """Cap the AX messaging timeout for calls through ``element`` (best-effort).

    Applies to the element it is set on (setting it on the app root bounds the
    calls made through that root). A missing symbol or native error is
    ignored — the call sequence then simply keeps the OS default.
    """
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementSetMessagingTimeout,
        )

        AXUIElementSetMessagingTimeout(element, float(timeout_s))
    except Exception:  # noqa: BLE001 — advisory bound only
        log.debug("AXUIElementSetMessagingTimeout unavailable", exc_info=True)


def _find_and_focus_macos(title_contains: str) -> tuple[bool, str]:
    """Activate and AX-raise a matching macOS window without AppleScript.

    Quartz supplies the stable owner PID/window catalogue, AppKit activates
    the owning process, and Accessibility raises the exact window. This avoids
    the separate Automation permission that System Events requires.
    """
    needle = (title_contains or "").strip().casefold()
    if not needle:
        return False, "A non-empty window title is required."

    matched_entry: dict | None = None
    for entry in _quartz_window_list(on_screen_only=False):
        try:
            if int(entry.get("kCGWindowLayer", 0) or 0) != 0:
                continue
        except (TypeError, ValueError):
            continue
        if needle in _macos_window_title(entry).casefold():
            matched_entry = entry
            break
    if matched_entry is None:
        return False, f"No window with title containing '{title_contains}' found."

    pid = _macos_window_pid(matched_entry)
    if not pid:
        return False, "The matching macOS window has no owning process identifier."

    try:
        from AppKit import (  # type: ignore[import-not-found] # noqa: PLC0415
            NSApplicationActivateAllWindows,
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCreateApplication,
            AXUIElementPerformAction,
            AXUIElementSetAttributeValue,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        return False, f"macOS window APIs are unavailable: {exc}"

    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        PermissionState,
        get_system_permission_port,
    )

    permission_port = get_system_permission_port()
    if not permission_port.runtime_access_granted(PermissionId.ACCESSIBILITY):
        accessibility_state = permission_port.state(PermissionId.ACCESSIBILITY)
        detail = (
            accessibility_state.value
            if accessibility_state is not PermissionState.GRANTED
            else "grant belongs to an unstable app identity or needs restart"
        )
        return False, (
            f"macOS Accessibility permission is not ready ({detail}) — grant it in System "
            "Settings > Privacy & Security > Accessibility so Jarvis can switch windows."
        )

    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False, "The matching macOS application exited before it could be focused."
    options = NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows
    if not bool(app.activateWithOptions_(options)):
        return False, "macOS refused to activate the matching application."

    root = AXUIElementCreateApplication(pid)
    _macos_bound_ax_messaging(root)
    windows = list(_macos_ax_attr(root, "AXWindows") or [])
    owner_name = str(matched_entry.get("kCGWindowOwnerName") or "")
    target_title = _macos_window_title(matched_entry)
    target = _select_macos_ax_window(
        windows,
        matched_entry,
        title_contains=needle if needle not in owner_name.casefold() else None,
    )
    if target is None:
        return False, (
            "The application activated, but its matching AX window was "
            "unavailable or ambiguous."
        )
    # The timeout is per-element: bound the window element too, not only root.
    _macos_bound_ax_messaging(target)
    target_title = str(_macos_ax_attr(target, "AXTitle") or target_title)

    minimized = bool(_macos_ax_attr(target, "AXMinimized") or False)
    restore_error = (
        AXUIElementSetAttributeValue(target, "AXMinimized", False)
        if minimized
        else 0
    )
    raise_error = AXUIElementPerformAction(target, "AXRaise")
    focus_error = AXUIElementSetAttributeValue(root, "AXFocusedWindow", target)
    front_error = AXUIElementSetAttributeValue(root, "AXFrontmost", True)
    if any(
        error != 0
        for error in (front_error, focus_error, restore_error, raise_error)
    ):
        return False, "macOS Accessibility refused to focus the matching window."
    return True, target_title


def _list_windows_macos() -> list[WindowInfo]:
    """Top-level normal-layer windows from Quartz, front-to-back."""
    entries: list[dict] = []
    for entry in _quartz_window_list(on_screen_only=False):
        try:
            if int(entry.get("kCGWindowLayer", 0) or 0) != 0:
                continue
            title = _macos_window_title(entry).strip()
        except (TypeError, ValueError):
            continue
        if title:
            entries.append(entry)

    minimized_by_number: dict[int, bool] = {}
    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCreateApplication,
        )

        from jarvis.platform.permissions import (  # noqa: PLC0415
            PermissionId,
            get_system_permission_port,
        )

        port = get_system_permission_port()
        if port.runtime_access_granted(PermissionId.ACCESSIBILITY):
            ax_windows_by_pid: dict[int, list[object]] = {}
            for entry in entries:
                pid = _macos_window_pid(entry)
                number = int(entry.get("kCGWindowNumber", 0) or 0)
                if not pid or not number:
                    continue
                if pid not in ax_windows_by_pid:
                    root = AXUIElementCreateApplication(pid)
                    ax_windows_by_pid[pid] = list(
                        _macos_ax_attr(root, "AXWindows") or [],
                    )
                ax_windows = ax_windows_by_pid[pid]
                title_key = _macos_window_title(entry).strip().casefold()
                target = _select_macos_ax_window(
                    ax_windows,
                    entry,
                    title_equals=title_key,
                )
                if target is not None:
                    native_minimized = _macos_ax_attr(target, "AXMinimized")
                    if native_minimized is not None:
                        minimized_by_number[number] = bool(native_minimized)
    except Exception:  # noqa: BLE001 — minimized state is advisory
        log.debug("macOS AX minimized-state lookup failed", exc_info=True)

    result: list[WindowInfo] = []
    for entry in entries:
        try:
            number = int(entry.get("kCGWindowNumber", 0) or 0)
        except (TypeError, ValueError):
            number = 0
        result.append(WindowInfo(
            title=_macos_window_title(entry).strip(),
            minimized=minimized_by_number.get(number, False),
            handle=number or None,
        ))
    return result


def _foreground_title_macos() -> str:
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found] # noqa: PLC0415

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        pid = int(app.processIdentifier())
        for entry in _quartz_window_list():
            if (
                _macos_window_pid(entry) == pid
                and int(entry.get("kCGWindowLayer", 0) or 0) == 0
            ):
                title = _macos_window_title(entry).strip()
                if title:
                    return title
        return str(app.localizedName() or "")
    except Exception:  # noqa: BLE001
        return ""


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
        encoding="utf-8",
        errors="replace",
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
        encoding="utf-8",
        errors="replace",
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
        encoding="utf-8",
        errors="replace",
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
        encoding="utf-8",
        errors="replace",
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
        # Split on BOTH separators: a Windows-style path must resolve to its
        # basename on macOS/Linux too (os.path.basename only splits the
        # host's own separator, leaving the full path as a dead token).
        base = re.split(r"[\\/]", name)[-1]
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


def _quartz_window_list(*, on_screen_only: bool = True) -> list:
    """Quartz window info dicts, front-to-back, or ``[]`` when unavailable.

    Foreground/capture callers use the default on-screen catalogue. Window
    enumeration and switching request the all-windows catalogue so minimized
    and other-Space windows can still be found and restored. Bounds and ids
    use the same global point space as Quartz input and mss capture rects.
    """
    try:
        import Quartz  # type: ignore[import-not-found] # noqa: PLC0415
    except Exception:  # noqa: BLE001 — pyobjc not installed
        return []
    try:
        scope = (
            Quartz.kCGWindowListOptionOnScreenOnly
            if on_screen_only
            else getattr(Quartz, "kCGWindowListOptionAll", 0)
        )
        return list(Quartz.CGWindowListCopyWindowInfo(
            scope | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        ) or [])
    except Exception:  # noqa: BLE001 — permission / transient CG error
        log.debug("CGWindowListCopyWindowInfo failed", exc_info=True)
        return []


def _foreground_window_macos() -> WindowInfo | None:
    """Frontmost normal-layer window with its CGWindowID.

    ``CGWindowListCopyWindowInfo`` returns windows ordered front-to-back, so
    the first entry on layer 0 is the frontmost app window (status items,
    the Dock and overlays live on higher layers). ``kCGWindowName`` needs
    the Screen-Recording permission — without it the owning app's name still
    identifies the window. Falls back to the native foreground-title probe
    (without a handle) when Quartz is unavailable.
    """
    front_pid = 0
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found] # noqa: PLC0415

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        front_pid = int(app.processIdentifier()) if app is not None else 0
    except Exception:  # noqa: BLE001
        log.debug("frontmost macOS process lookup failed", exc_info=True)
    for entry in _quartz_window_list():
        try:
            if int(entry.get("kCGWindowLayer", 0) or 0) != 0:
                continue
            if front_pid and _macos_window_pid(entry) != front_pid:
                continue
            number = int(entry.get("kCGWindowNumber", 0) or 0)
            title = str(
                entry.get("kCGWindowName")
                or entry.get("kCGWindowOwnerName")
                or ""
            )
            owner_pid = front_pid or _macos_window_pid(entry)
            return WindowInfo(
                title=title, handle=number or None, pid=owner_pid or None,
            )
        except (TypeError, ValueError):
            continue
    title = get_foreground_title()
    if not title:
        return None
    return WindowInfo(title=title, pid=front_pid or None)


def _foreground_window_linux() -> WindowInfo | None:
    """Active X11 window with its window id via xdotool.

    Wayland and headless sessions return ``None`` (no global window identity
    by design there); a missing xdotool degrades to the title-only path.
    """
    if is_wayland() or not display_present():
        return None
    if shutil.which("xdotool") is None:
        title = get_foreground_title()
        return WindowInfo(title=title) if title else None
    proc = subprocess.run(
        ["xdotool", "getactivewindow"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        return None
    try:
        win_id = int((proc.stdout or "").strip() or "0")
    except ValueError:
        return None
    if not win_id:
        return None
    name = subprocess.run(
        ["xdotool", "getwindowname", str(win_id)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    title = (name.stdout or "").strip() if name.returncode == 0 else ""
    return WindowInfo(title=title, handle=win_id)


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
    """The window's VISIBLE frame ``(left, top, width, height)`` in the
    platform's INPUT UNITS on the virtual desktop, or ``None`` when
    unavailable. This is the rect the window-centric Computer-Use pipeline
    captures and maps clicks into, so each backend MUST report the same
    coordinate space the platform's input APIs consume:

    * Windows — ``DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`` (the
      rect the user actually sees; plain ``GetWindowRect`` includes the
      invisible resize-shadow border, which would leak wallpaper into the
      capture and skew every mapped click), physical virtual-desktop pixels.
      Mixed-DPI correctness requires the calling thread to hold a
      per-monitor DPI context — CU callers invoke this inside
      :func:`jarvis.cu.geometry.input_space`.
    * macOS — Quartz ``kCGWindowBounds`` by CGWindowID: global POINTS with a
      top-left origin, exactly the space Quartz mouse events and mss rects
      use (Retina backing pixels never appear here).
    * Linux/X11 — ``xdotool getwindowgeometry`` by window id: root-window
      pixels (the client-area rect; WM decorations are not part of it).
      Wayland and headless sessions return ``None`` — callers fall back to
      monitor scope and the engine's X11/XWayland refusal explains why.
    """
    try:
        plat = detect_platform()
        if plat == "darwin":
            return _window_frame_rect_macos(win)
        if plat == "linux":
            return _window_frame_rect_linux(win)
        if plat != "win32" or not win.handle:
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


def _window_frame_rect_macos(win: WindowInfo) -> tuple[int, int, int, int] | None:
    """Window bounds by CGWindowID (or the frontmost layer-0 window when the
    :class:`WindowInfo` carries no handle). Global points, top-left origin."""
    target: dict | None = None
    for entry in _quartz_window_list():
        try:
            if win.handle:
                if int(entry.get("kCGWindowNumber", -1) or -1) == int(win.handle):
                    target = entry
                    break
            elif int(entry.get("kCGWindowLayer", 0) or 0) == 0:
                target = entry
                break
        except (TypeError, ValueError):
            continue
    if target is None:
        return None
    bounds = target.get("kCGWindowBounds") or {}
    try:
        left = int(bounds.get("X", 0))
        top = int(bounds.get("Y", 0))
        width = int(bounds.get("Width", 0))
        height = int(bounds.get("Height", 0))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (left, top, width, height)


def _window_frame_rect_linux(win: WindowInfo) -> tuple[int, int, int, int] | None:
    """Window geometry by X11 window id via ``xdotool getwindowgeometry
    --shell`` (root-relative pixels). ``None`` on Wayland/headless/no tool."""
    if is_wayland() or not display_present():
        return None
    if not win.handle or shutil.which("xdotool") is None:
        return None
    proc = subprocess.run(
        ["xdotool", "getwindowgeometry", "--shell", str(int(win.handle))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    if proc.returncode != 0:
        return None
    fields: dict[str, int] = {}
    for line in (proc.stdout or "").splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep:
            continue
        try:
            fields[key.strip().upper()] = int(value.strip())
        except ValueError:
            continue
    try:
        left, top = fields["X"], fields["Y"]
        width, height = fields["WIDTH"], fields["HEIGHT"]
    except KeyError:
        return None
    if width <= 0 or height <= 0:
        return None
    return (left, top, width, height)


def _resolve_macos_ax_window(win: WindowInfo) -> tuple[object | None, str]:
    """Resolve a Quartz ``WindowInfo`` to its owning native AX window."""
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found] # noqa: PLC0415
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementCreateApplication,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        return None, f"macOS window APIs are unavailable: {exc}"

    pid = 0
    catalog_match = False
    matched_entry: dict | None = None
    title_key = (win.title or "").strip().casefold()
    for entry in _quartz_window_list(on_screen_only=False):
        try:
            number = int(entry.get("kCGWindowNumber", 0) or 0)
            title = _macos_window_title(entry).strip().casefold()
        except (TypeError, ValueError):
            continue
        if (win.handle and number == int(win.handle)) or (
            not win.handle and title_key and title == title_key
        ):
            catalog_match = True
            pid = _macos_window_pid(entry)
            matched_entry = entry
            break
    if win.handle and not catalog_match:
        return None, "The requested macOS window no longer exists."
    if not pid:
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            pid = int(app.processIdentifier()) if app is not None else 0
        except Exception:  # noqa: BLE001
            pid = 0
    if not pid:
        return None, "The macOS window has no owning process identifier."

    root = AXUIElementCreateApplication(pid)
    windows = list(_macos_ax_attr(root, "AXWindows") or [])
    target = (
        _select_macos_ax_window(
            windows,
            matched_entry,
            title_equals=title_key,
        )
        if matched_entry is not None
        else None
    )
    if target is None and matched_entry is None and len(windows) == 1:
        target = windows[0]
    if target is None:
        return None, "The matching macOS AX window is unavailable or ambiguous."
    return target, ""


def _window_is_maximized_macos(win: WindowInfo) -> bool | None:
    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        get_system_permission_port,
    )

    if not get_system_permission_port().runtime_access_granted(
        PermissionId.ACCESSIBILITY,
    ):
        return None
    target, _error = _resolve_macos_ax_window(win)
    if target is None:
        return None
    full_screen = _macos_ax_attr(target, "AXFullScreen")
    if full_screen is not None and bool(full_screen):
        return True
    zoomed = _macos_ax_attr(target, "AXZoomed")
    if zoomed is not None:
        return bool(zoomed)
    return None if full_screen is None else bool(full_screen)


def window_is_maximized(win: WindowInfo) -> bool | None:
    """Whether the window is maximized; ``None`` when it cannot be read."""
    try:
        platform_name = detect_platform()
        if platform_name == "darwin":
            return _window_is_maximized_macos(win)
        if platform_name != "win32" or not win.handle:
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


def _maximize_window_macos(win: WindowInfo) -> tuple[bool, str]:
    """Set the native AXZoomed attribute without Apple Events automation."""
    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        PermissionState,
        get_system_permission_port,
    )

    port = get_system_permission_port()
    if not port.runtime_access_granted(PermissionId.ACCESSIBILITY):
        state = port.state(PermissionId.ACCESSIBILITY)
        detail = (
            state.value
            if state is not PermissionState.GRANTED
            else "grant belongs to an unstable app identity or needs restart"
        )
        return False, (
            "macOS Accessibility permission is not ready "
            f"({detail}); grant it in Personal Jarvis > Settings > "
            "Permissions or System Settings > Privacy & Security > "
            "Accessibility, then retry."
        )

    try:
        from ApplicationServices import (  # type: ignore[import-not-found] # noqa: PLC0415
            AXUIElementSetAttributeValue,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        return False, f"macOS window APIs are unavailable: {exc}"
    target, error = _resolve_macos_ax_window(win)
    if target is None:
        return False, error

    error = AXUIElementSetAttributeValue(target, "AXZoomed", True)
    if error != 0:
        return False, (
            "macOS Accessibility could not zoom this window; it may be a "
            "fixed-size dialog or not expose AXZoomed."
        )
    return True, f"zoomed '{(win.title or 'window')[:40]}' to fill its display"


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
            return _maximize_window_macos(win)
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
