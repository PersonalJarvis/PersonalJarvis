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


def _build_windows_input_types() -> Any:
    """Build the ctypes structs for SendInput keyboard injection.

    The ``INPUT`` union MUST be sized to its LARGEST member (``MOUSEINPUT``) so
    that ``sizeof(INPUT)`` matches the size Windows expects for ``cbSize``. A
    union that declares only ``ki`` (``KEYBDINPUT``) is too small (32 vs 40 bytes
    on x64), which makes ``SendInput`` reject every call with
    ``ERROR_INVALID_PARAMETER`` ("Falscher Parameter"); every keystroke then
    silently falls back to pyautogui's layout-dependent path, which does not
    register in web inputs (Google-Flights typing bug, 2026-06-22).

    Lazily imported so the module still imports on non-Windows hosts.
    """
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    ULONG_PTR = wintypes.WPARAM

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class INPUT_UNION(ctypes.Union):
        # MOUSEINPUT is the LARGEST member and MUST be present: it sizes the
        # union (and therefore INPUT.cbSize) to what Windows expects. Omitting
        # it undersizes INPUT and SendInput rejects every call (see docstring).
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT))

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    return SimpleNamespace(
        KEYBDINPUT=KEYBDINPUT,
        MOUSEINPUT=MOUSEINPUT,
        INPUT_UNION=INPUT_UNION,
        INPUT=INPUT,
        INPUT_KEYBOARD=1,
        KEYEVENTF_KEYUP=0x0002,
        KEYEVENTF_UNICODE=0x0004,
        ULONG_PTR=ULONG_PTR,
    )


def _send_text_windows(text: str, delay_s: float) -> None:
    """Sendet Unicode-Text an das aktive Windows-Fenster via SendInput."""
    if os.name != "nt":
        raise RuntimeError("Native Text-Eingabe ohne pyautogui ist nur auf Windows verfuegbar")

    import ctypes

    t = _build_windows_input_types()
    INPUT, KEYBDINPUT, INPUT_UNION = t.INPUT, t.KEYBDINPUT, t.INPUT_UNION

    # use_last_error=True so ``ctypes.get_last_error()`` reflects SendInput's real
    # Win32 error (ctypes.windll does NOT track it -> misleading error codes).
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send_input = user32.SendInput
    send_input.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = ctypes.c_uint

    for char in text:
        code = ord(char)
        events = (INPUT * 2)(
            INPUT(
                type=t.INPUT_KEYBOARD,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(0, code, t.KEYEVENTF_UNICODE, 0, t.ULONG_PTR(0)),
                ),
            ),
            INPUT(
                type=t.INPUT_KEYBOARD,
                union=INPUT_UNION(
                    ki=KEYBDINPUT(
                        0,
                        code,
                        t.KEYEVENTF_UNICODE | t.KEYEVENTF_KEYUP,
                        0,
                        t.ULONG_PTR(0),
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
