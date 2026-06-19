"""switch_window-Tool: bringt ein Fenster nach Titel-Substring in den Vordergrund.

Win32-Pfad nutzt ``EnumWindows`` + ``GetWindowText`` + ``SetForegroundWindow``.
Der Caller gibt einen Substring an (case-insensitive), und das erste sichtbare
Top-Level-Fenster, dessen Titel den Substring enthaelt, wird fokussiert.

Risk-Tier: ``monitor`` — Fenster-Switches sind reversibel, aber der Fokus-
Wechsel kann ungewollte Aktionen ausloesen, wenn der User gerade tippt.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.platform import detect_platform
from jarvis.platform.probes import display_present, is_wayland


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
    SetForegroundWindow = user32.SetForegroundWindow
    ShowWindow = user32.ShowWindow

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

    # Wenn das Fenster minimiert ist, erst restoren (SW_RESTORE = 9).
    ShowWindow(hwnd, 9)
    if not SetForegroundWindow(hwnd):
        # SetForegroundWindow scheitert manchmal wegen Foreground-Lock-Timeout —
        # in dem Fall ist das Fenster zwar sichtbar gemacht, aber nicht fokussiert.
        return False, (
            f"Fenster '{title}' gefunden, aber Fokus-Setzen scheiterte "
            "(Foreground-Lock-Timeout — User muss Alt+Tab manuell drucken)"
        )
    return True, title


def _find_and_focus_macos(title_contains: str) -> tuple[bool, str]:
    """Bring the first window whose title contains the substring to the front
    via AppleScript / System Events (H2, DEEP-DIVE-AUDIT-2026-06-19).

    Needs the macOS Accessibility grant; without it osascript errors out, which
    is reported as a clear onboarding message instead of a silent no-op. New
    non-Windows sibling — the Windows ctypes path is untouched (AD-7). All
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


def _find_and_focus_linux(title_contains: str) -> tuple[bool, str]:
    """Activate the first window whose title contains the substring via wmctrl
    on X11 (H2). Returns a clear message when wmctrl is absent (the user should
    install it, e.g. ``apt install wmctrl``) or nothing matches. New non-Windows
    sibling — the Windows path is untouched (AD-7). Wayland/headless are handled
    by the caller before this runs.
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


class SwitchWindowTool:
    name: str = "switch_window"
    risk_tier: str = "monitor"
    description: str = (
        "Wechselt zu einem Top-Level-Fenster, dessen Titel den uebergebenen "
        "Substring enthaelt. Case-insensitive. Restored minimierte Fenster."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title_contains": {
                "type": "string",
                "description": "Substring, der im Fenstertitel vorkommen muss",
            },
        },
        "required": ["title_contains"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        title = args.get("title_contains")
        if not isinstance(title, str) or not title.strip():
            return ToolResult(
                success=False, output=None,
                error="title_contains fehlt oder leer",
            )

        plat = detect_platform()
        if plat != "win32":
            return await self._execute_non_windows(plat, title)

        # --- Windows path (unchanged, AD-7) ---
        try:
            found, msg = await asyncio.to_thread(_find_and_focus_windows, title)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False, output=None,
                error=f"Window-Enumeration fehlgeschlagen: {exc}",
            )

        if found:
            return ToolResult(success=True, output=f"Fokus auf Fenster: {msg}")
        return ToolResult(success=False, output=None, error=msg)

    async def _execute_non_windows(self, plat: str, title: str) -> ToolResult:
        """macOS/Linux window switching behind the platform seam (H2). New
        siblings to the Windows path; all user-facing strings are English.
        Wayland and headless sessions degrade to a clear message instead of a
        hard failure (AD-13).
        """
        if plat == "darwin":
            focus_fn = _find_and_focus_macos
        elif plat == "linux":
            if is_wayland():
                return ToolResult(
                    success=False, output=None,
                    error=(
                        "Window switching is unavailable on Wayland by OS design — "
                        "switch with the dock/overview or the app's own controls."
                    ),
                )
            if not display_present():
                return ToolResult(
                    success=False, output=None,
                    error=(
                        "Window switching needs a graphical display; this looks "
                        "like a headless session."
                    ),
                )
            focus_fn = _find_and_focus_linux
        else:
            return ToolResult(
                success=False, output=None,
                error=f"Window switching is not supported on this platform ({plat}).",
            )

        try:
            found, msg = await asyncio.to_thread(focus_fn, title)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=f"Window switch failed: {exc}")

        if found:
            return ToolResult(success=True, output=f"Focused window: {msg}")
        return ToolResult(success=False, output=None, error=msg)
