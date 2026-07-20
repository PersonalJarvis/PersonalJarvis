"""hotkey tool: simulate platform-native keyboard combinations.

Windows uses ``SendInput``; macOS maps Command/Option aliases through the
native Quartz-capable desktop actuator; X11 uses the available pynput or
pyautogui backend. Unsupported and headless hosts fail with an actionable
error.

Risk tier: ``monitor`` — a key combo can trigger non-reversible actions
(Ctrl+W closes a tab, Ctrl+S opens a dialog). Toast notification, but no
approval required.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

# Combined-token expansion ("ctrl+v" -> ["ctrl", "v"]) lives in the shared
# CU backend. Validation is platform-neutral off Windows so Command/Meta can
# reach the macOS actuator instead of being rejected by a Win32-only table.
from jarvis.cu.actuate.windows import (  # noqa: E402
    expand_combo_keys as _expand_combo_keys,
)
from jarvis.cu.actuate.windows import (
    resolve_vk as _resolve_vk,
)


class _ForegroundTargetChanged(RuntimeError):
    """Raised before a shortcut when its screenshot-bound target changed."""


def _send_for_expected_window(
    sender: Any,
    keys: list[str],
    expected_signature: tuple[Any, ...] | None,
) -> None:
    if expected_signature is not None:
        from jarvis.cu.target_guard import (  # noqa: PLC0415
            foreground_matches_or_same_app,
        )

        if not foreground_matches_or_same_app(expected_signature):
            raise _ForegroundTargetChanged(
                "foreground window changed after the screenshot"
            )
    sender(keys)


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
                    "alt/option, win, cmd/command/meta) first, then the action key. "
                    "Use cmd for standard macOS shortcuts and ctrl on Windows/Linux. "
                    "Letters individually, "
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
        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None and not isinstance(expected_raw, (list, tuple)):
            return ToolResult(
                success=False,
                output=None,
                error="Refusing hotkey: invalid captured-window identity.",
            )
        expected_signature = (
            tuple(expected_raw) if expected_raw is not None else None
        )

        from jarvis.cu.actuate.base import is_known_key_name

        # Keep Windows validation exact; off Windows use the common vocabulary
        # and let the selected backend perform its final capability check.
        for k in keys_str:
            known = _resolve_vk(k) is not None if os.name == "nt" else is_known_key_name(k)
            if not known:
                return ToolResult(
                    success=False,
                    output=None,
                    error=(
                        f"Unknown key: {k!r}. Known modifiers: ctrl, shift, "
                        f"alt/option, win, cmd/command/meta. Known keys: printable "
                        f"characters, f1-f12, enter, tab, "
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
                await asyncio.to_thread(
                    _send_for_expected_window,
                    _send_hotkey_windows,
                    keys_str,
                    expected_signature,
                )
                return ToolResult(
                    success=True,
                    output=f"Hotkey sent: {'+'.join(keys_str)}",
                )
            except _ForegroundTargetChanged as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Refusing hotkey: {exc}.",
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
            await asyncio.to_thread(
                _send_for_expected_window,
                actuator.key_combo,
                keys_str,
                expected_signature,
            )
            return ToolResult(
                success=True,
                output=f"Hotkey sent ({actuator.name}): {'+'.join(keys_str)}",
            )
        except ActuationUnavailable as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except _ForegroundTargetChanged as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"Refusing hotkey: {exc}.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
