"""hotkey tool: simulates key combos like Ctrl+T, Alt+Tab, Win+R.

Uses Win32 ``SendInput`` with virtual-key codes as the primary path. Falls
back to ``pyautogui.hotkey`` when the Win32 subsystem is missing — so tests
keep running on Linux/Mac and headless CI, even though real keystrokes only
work on Windows.

Risk tier: ``monitor`` — a key combo can trigger non-reversible actions
(Ctrl+W closes a tab, Ctrl+S opens a dialog). Toast notification, but no
approval required.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

# Key-name resolution + combined-token expansion ("ctrl+v" -> ["ctrl", "v"])
# live in the shared CU v2 Windows backend; re-exported here so callers and
# tests keep their import path. The vocabulary is unchanged.
from jarvis.cu.actuate.windows import (  # noqa: E402
    expand_combo_keys as _expand_combo_keys,
)
from jarvis.cu.actuate.windows import (
    resolve_vk as _resolve_vk,
)


def _send_hotkey_windows(keys: list[str]) -> None:
    """Sends a key combination as a Win32 SendInput sequence.

    Order: all keys DOWN one after another, then UP in reverse order — the
    canonical hotkey choreography. Delegates to the shared CU v2 Windows
    backend (correct 40-byte INPUT sizing, extended-key flags, thread DPI
    pin); the key vocabulary is identical.
    """
    if os.name != "nt":
        raise RuntimeError("Native hotkey input is only available on Windows")

    from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

    WindowsActuator().key_combo(keys)


class HotkeyTool:
    name: str = "hotkey"
    risk_tier: str = "monitor"
    description: str = (
        "Sends a key combination to the active window (e.g. ['ctrl','t'] "
        "for a new tab, ['alt','tab'] for window switch, ['ctrl','shift','t'] "
        "to restore a closed tab)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of keys in press order. Modifiers (ctrl, shift, "
                    "alt, win) first, then the action key. Letters individually, "
                    "special keys by name (enter, tab, esc, f1-f12, left, right, "
                    "up, down, home, end, pageup, pagedown, delete, backspace)."
                ),
            },
        },
        "required": ["keys"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        keys = args.get("keys")
        if not keys or not isinstance(keys, list):
            return ToolResult(
                success=False,
                output=None,
                error="keys is missing or not a list (example: ['ctrl', 't'])",
            )
        keys_str = [str(k) for k in keys]
        # Tolerate a combined shortcut string ("ctrl+v") in place of the
        # documented list form (["ctrl", "v"]) — LLM callers emit it constantly.
        keys_str = _expand_combo_keys(keys_str)

        # Pre-validation for a better error message — otherwise only
        # "Unknown key 'X'" comes out of the ctypes path.
        for k in keys_str:
            if _resolve_vk(k) is None:
                return ToolResult(
                    success=False,
                    output=None,
                    error=(
                        f"Unknown key: {k!r}. Known modifiers: ctrl, shift, "
                        f"alt, win. Known keys: a-z, 0-9, f1-f12, enter, tab, "
                        f"esc, space, backspace, delete, home, end, pageup, "
                        f"pagedown, left, right, up, down."
                    ),
                )

        # pyautogui path as a platform-independent fallback (tests/CI on
        # Linux). On Windows we prefer the native path — pyautogui
        # is slower and needs extra setup work for hotkey
        # mapping (e.g. 'win' is called 'winleft' there).
        if os.name == "nt":
            try:
                await asyncio.to_thread(_send_hotkey_windows, keys_str)
                return ToolResult(
                    success=True,
                    output=f"Hotkey sent: {'+'.join(keys_str)}",
                )
            except (ValueError, OSError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Hotkey '{'+'.join(keys_str)}' failed: {exc}",
                )

        from jarvis.cu.actuate import ActuationUnavailable, get_actuator

        try:
            actuator = get_actuator()
            await asyncio.to_thread(actuator.key_combo, keys_str)
            return ToolResult(
                success=True,
                output=f"Hotkey sent ({actuator.name}): {'+'.join(keys_str)}",
            )
        except ActuationUnavailable as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
