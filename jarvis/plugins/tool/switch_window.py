"""switch_window-Tool: bringt ein Fenster nach Titel-Substring in den Vordergrund.

The window enumeration / focus logic now lives in
``jarvis.platform.window_state`` (so ``open_app`` and the Computer-Use loop can
reuse it). This tool delegates to the per-OS focus helpers there and keeps its
exact user-facing readback strings (AD-7: the Windows path is unchanged).

Risk-Tier: ``monitor`` — Fenster-Switches sind reversibel, aber der Fokus-
Wechsel kann ungewollte Aktionen ausloesen, wenn der User gerade tippt.
"""
from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.platform import detect_platform
from jarvis.platform.probes import display_present, is_wayland
from jarvis.platform.window_state import (
    _find_and_focus_linux,
    _find_and_focus_macos,
    _find_and_focus_windows,
)


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
