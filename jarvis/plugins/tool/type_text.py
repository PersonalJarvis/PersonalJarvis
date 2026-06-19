"""type_text tool: simulate keyboard input into the active window.

On Windows the native KEYEVENTF_UNICODE SendInput path is used as the PRIMARY
engine — it injects exact Unicode codepoints regardless of the active keyboard
layout (e.g. German QWERTZ) and is far more robust into webview/Tauri text
inputs than pyautogui's layout-dependent virtual-key approach. pyautogui is kept
as a best-effort fallback on Windows and remains the primary engine on other
platforms. Risk tier: safe — text input into the active window is reversible (Ctrl+Z).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


def _send_text_windows(text: str, delay_s: float) -> None:
    """Sendet Unicode-Text an das aktive Windows-Fenster via SendInput."""
    if os.name != "nt":
        raise RuntimeError("Native Text-Eingabe ohne pyautogui ist nur auf Windows verfuegbar")

    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    ULONG_PTR = wintypes.WPARAM

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class INPUT_UNION(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),)

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT

    for char in text:
        code = ord(char)
        events = (INPUT * 2)(
            INPUT(
                type=INPUT_KEYBOARD,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, ULONG_PTR(0)),
                ),
            ),
            INPUT(
                type=INPUT_KEYBOARD,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(
                        0,
                        code,
                        KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        0,
                        ULONG_PTR(0),
                    ),
                ),
            ),
        )
        sent = send_input(2, events, ctypes.sizeof(INPUT))
        if sent != 2:
            raise ctypes.WinError(ctypes.get_last_error())
        if delay_s > 0:
            time.sleep(delay_s)


class TypeTextTool:
    name: str = "type_text"
    risk_tier: str = "safe"
    description: str = (
        "Tippt Text in das gerade aktive Fenster (als wuerde der User ihn eingeben)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Zu tippender Text"},
            "delay_s": {
                "type": "number",
                "description": "Pause zwischen Tastenanschlaegen in Sekunden",
                "default": 0.02,
            },
        },
        "required": ["text"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        text = args.get("text") or ""
        delay_s = float(args.get("delay_s", 0.02))
        if not text:
            return ToolResult(success=False, output=None, error="text missing")
        # Windows: prefer the native KEYEVENTF_UNICODE SendInput path. It injects
        # the exact Unicode codepoint regardless of the active keyboard layout
        # (this machine runs German QWERTZ) and is far more robust into
        # webview/Tauri text inputs than pyautogui's layout-dependent virtual-key
        # path, which garbled characters typed into the BridgeSpace Tauri terminal
        # (CU typo bug 2026-06-15). pyautogui stays a best-effort fallback.
        if os.name == "nt":
            try:
                await asyncio.to_thread(_send_text_windows, text, delay_s)
                return ToolResult(
                    success=True,
                    output=f"Typed {len(text)} chars via native Windows Unicode input",
                )
            except Exception as native_exc:  # noqa: BLE001
                log.warning(
                    "native Unicode SendInput failed, falling back to pyautogui: %r",
                    native_exc,
                )
        try:
            import pyautogui
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"text input unavailable: pyautogui import failed: {exc}",
            )
        try:
            pyautogui.typewrite(text, interval=delay_s)
            return ToolResult(success=True, output=f"Typed {len(text)} chars")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
