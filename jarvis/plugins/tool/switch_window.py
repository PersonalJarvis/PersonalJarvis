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
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


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

        if os.name != "nt":
            return ToolResult(
                success=False, output=None,
                error=f"switch_window ist nur auf Windows verfuegbar (current: {os.name})",
            )

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
