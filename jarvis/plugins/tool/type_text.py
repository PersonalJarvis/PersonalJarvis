"""type_text tool: simulate keyboard input into the active window.

On Windows the native KEYEVENTF_UNICODE SendInput path is used as the PRIMARY
engine — it injects exact Unicode codepoints regardless of the active keyboard
layout (e.g. German QWERTZ) and is far more robust into webview/Tauri text
inputs than pyautogui's layout-dependent virtual-key approach. pyautogui is kept
as a best-effort fallback on Windows and remains the primary engine on other
platforms. Risk tier: safe — text input into the active window is reversible
(Ctrl+Z). That rationale leans on the foreground guard pinning the RIGHT
window: the CU engine holds a strict per-frame window signature and only
relaxes it for same-app churn after an action of its own batch (see
``jarvis.cu.target_guard``); this tool's own re-check mirrors that contract.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class _ForegroundTargetChanged(RuntimeError):
    """Raised before input when the screenshot-bound window is no longer active."""


def _type_for_expected_window(
    sender: Any,
    text: str,
    delay_s: float,
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
    sender(text, delay_s=delay_s)


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
    from types import SimpleNamespace

    # Fixed-width types matching the Win32 ABI on every host. ctypes.wintypes
    # aliases (LONG=c_long, DWORD=c_ulong) inflate to 8 bytes on LP64
    # macOS/Linux, silently changing the struct layout the cross-platform
    # cbSize parity test guards. On Windows these are byte-identical to the
    # wintypes definitions (AD-7).
    WORD = ctypes.c_uint16
    DWORD = ctypes.c_uint32
    LONG = ctypes.c_int32
    ULONG_PTR = (
        ctypes.c_uint64
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else ctypes.c_uint32
    )

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", WORD),
            ("wScan", WORD),
            ("dwFlags", DWORD),
            ("time", DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", LONG),
            ("dy", LONG),
            ("mouseData", DWORD),
            ("dwFlags", DWORD),
            ("time", DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class INPUT_UNION(ctypes.Union):
        # MOUSEINPUT is the LARGEST member and MUST be present: it sizes the
        # union (and therefore INPUT.cbSize) to what Windows expects. Omitting
        # it undersizes INPUT and SendInput rejects every call (see docstring).
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT))

    class INPUT(ctypes.Structure):
        _fields_ = (("type", DWORD), ("union", INPUT_UNION))

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
    """Sends Unicode text to the active Windows window via SendInput.

    Delegates to the shared CU v2 Windows backend, which adds the thread DPI
    pin and correct UTF-16 surrogate-pair handling for astral-plane
    characters (emoji) on top of the proven KEYEVENTF_UNICODE path.
    """
    if os.name != "nt":
        raise RuntimeError("Native text input without pyautogui is only available on Windows")

    from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

    WindowsActuator().type_text(text, delay_s=delay_s)


class TypeTextTool:
    name: str = "type_text"
    risk_tier: str = "safe"
    description: str = (
        "Types text into the currently active window (as if the user typed it)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type"},
            "delay_s": {
                "type": "number",
                "description": "Pause between keystrokes in seconds",
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
        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None and not isinstance(expected_raw, (list, tuple)):
            return ToolResult(
                success=False,
                output=None,
                error="Refusing text input: invalid captured-window identity.",
            )
        expected_signature = (
            tuple(expected_raw) if expected_raw is not None else None
        )
        # Windows: prefer the native KEYEVENTF_UNICODE SendInput path. It injects
        # the exact Unicode codepoint regardless of the active keyboard layout
        # (this machine runs German QWERTZ) and is far more robust into
        # webview/Tauri text inputs than pyautogui's layout-dependent virtual-key
        # path, which garbled characters typed into the BridgeSpace Tauri terminal
        # (CU typo bug 2026-06-15). pyautogui stays a best-effort fallback.
        if os.name == "nt":
            try:
                await asyncio.to_thread(
                    _type_for_expected_window,
                    lambda value, *, delay_s: _send_text_windows(value, delay_s),
                    text,
                    delay_s,
                    expected_signature,
                )
                return ToolResult(
                    success=True,
                    output=f"Typed {len(text)} chars via native Windows Unicode input",
                )
            except _ForegroundTargetChanged as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Refusing text input: {exc}.",
                )
            except Exception as native_exc:  # noqa: BLE001
                log.warning(
                    "native Unicode SendInput failed, falling back to pyautogui: %r",
                    native_exc,
                )
            # A second SendInput attempt would fail identically, so the
            # Windows fallback stays pyautogui (a DIFFERENT input mechanism).
            try:
                import pyautogui  # noqa: PLC0415
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"text input unavailable: pyautogui import failed: {exc}",
                )
            try:
                await asyncio.to_thread(
                    _type_for_expected_window,
                    lambda value, *, delay_s: pyautogui.typewrite(
                        value,
                        interval=delay_s,
                    ),
                    text,
                    delay_s,
                    expected_signature,
                )
                return ToolResult(success=True, output=f"Typed {len(text)} chars")
            except _ForegroundTargetChanged as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Refusing text input: {exc}.",
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(success=False, output=None, error=str(exc))

        from jarvis.cu.actuate import ActuationUnavailable, get_actuator

        try:
            actuator = get_actuator()
            await asyncio.to_thread(
                _type_for_expected_window,
                actuator.type_text,
                text,
                delay_s,
                expected_signature,
            )
            return ToolResult(
                success=True,
                output=f"Typed {len(text)} chars ({actuator.name})",
            )
        except ActuationUnavailable as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"text input unavailable: {exc}",
            )
        except _ForegroundTargetChanged as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"Refusing text input: {exc}.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
