"""click-Tool: simuliert Mausklicks an einer Bildschirm-Koordinate.

Win32-nativ via ``SetCursorPos`` + ``SendInput`` (Mouse-Event). Faellt
auf ``pyautogui.click`` zurueck, falls Win32 nicht verfuegbar — damit
laufen die Tests auf Linux/Mac, auch wenn echte Klicks nur auf Windows
funktional sind.

Risk-Tier: ``monitor`` — Maus-Klicks sind oft nicht reversibel
(Buttons, Form-Submits, Datei-Operationen). Toast-Notification sichtbar,
kein Approval-Dialog.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.control.cursor_motion import glide_os_cursor
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.overlay.virtual_cursor import get_virtual_cursor

# Mouse-Button-Mapping fuer Win32 (siehe MOUSEEVENTF_*-Flags).
_MOUSE_FLAGS_DOWN: dict[str, int] = {
    "left": 0x0002,    # MOUSEEVENTF_LEFTDOWN
    "right": 0x0008,   # MOUSEEVENTF_RIGHTDOWN
    "middle": 0x0020,  # MOUSEEVENTF_MIDDLEDOWN
}
_MOUSE_FLAGS_UP: dict[str, int] = {
    "left": 0x0004,    # MOUSEEVENTF_LEFTUP
    "right": 0x0010,   # MOUSEEVENTF_RIGHTUP
    "middle": 0x0040,  # MOUSEEVENTF_MIDDLEUP
}

# Absolute virtual-desktop positioning flags. A click positioned this way lands
# on its exact target on EVERY monitor — including one LEFT of primary (negative
# virtual-desktop X) — decoupled from SetCursorPos, which returns 0 / lands wrong
# when the cursor crosses the primary boundary (the "CU clicks void on the left
# monitor" bug). The coordinates are 0..65535 normalized over the whole virtual
# desktop, so the negative origin is folded into the normalization.
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_ABS_MOVE_FLAGS = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK


def _normalize_virtualdesk(
    x: int, y: int, vx: int, vy: int, vw: int, vh: int
) -> tuple[int, int]:
    """Map an absolute virtual-desktop pixel ``(x, y)`` to the 0..65535 space
    ``MOUSEEVENTF_ABSOLUTE | VIRTUALDESK`` expects, given the virtual-screen
    bounds (origin ``vx,vy``, size ``vw x vh`` — ``vx``/``vy`` may be negative).
    Pure + clamped; the offset by the virtual origin is what makes a monitor left
    of primary (x < 0) come out as a valid positive coordinate."""
    dw = max(1, vw - 1)
    dh = max(1, vh - 1)
    nx = min(65535, max(0, round((int(x) - vx) * 65535 / dw)))
    ny = min(65535, max(0, round((int(y) - vy) * 65535 / dh)))
    return nx, ny


def _virtualdesk_abs(x: int, y: int) -> tuple[int, int]:
    """:func:`_normalize_virtualdesk` against the live virtual-screen metrics."""
    import ctypes

    gsm = ctypes.windll.user32.GetSystemMetrics
    # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
    return _normalize_virtualdesk(x, y, gsm(76), gsm(77), gsm(78), gsm(79))


def _send_click(button: str, double: bool, abs_xy: tuple[int, int] | None = None) -> None:
    """Press a mouse button via Win32 SendInput.

    When ``abs_xy`` is given the input stream is prefixed with an ABSOLUTE
    virtual-desktop move to that pixel, so the click lands exactly there on any
    monitor (negative coords included) regardless of where SetCursorPos left the
    cursor. Without it, the click fires at the current cursor position (the legacy
    behaviour; positioning done beforehand by :func:`glide_os_cursor`).
    """
    button_l = button.lower()
    if button_l not in _MOUSE_FLAGS_DOWN:
        raise ValueError(f"Unbekannter Mausbutton: {button!r}. Erlaubt: left/right/middle")

    import ctypes
    from ctypes import wintypes

    INPUT_MOUSE = 0
    ULONG_PTR = wintypes.WPARAM

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
        _fields_ = (("mi", MOUSEINPUT),)

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    user32 = ctypes.windll.user32
    send_input = user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT

    flag_down = _MOUSE_FLAGS_DOWN[button_l]
    flag_up = _MOUSE_FLAGS_UP[button_l]
    n_clicks = 2 if double else 1

    events: list[INPUT] = []
    if abs_xy is not None:
        nx, ny = _virtualdesk_abs(abs_xy[0], abs_xy[1])
        events.append(
            INPUT(
                type=INPUT_MOUSE,
                union=INPUT_UNION(mi=MOUSEINPUT(nx, ny, 0, _ABS_MOVE_FLAGS, 0, ULONG_PTR(0))),
            )
        )
    for _ in range(n_clicks):
        events.append(
            INPUT(
                type=INPUT_MOUSE,
                union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, flag_down, 0, ULONG_PTR(0))),
            )
        )
        events.append(
            INPUT(
                type=INPUT_MOUSE,
                union=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, flag_up, 0, ULONG_PTR(0))),
            )
        )
    arr = (INPUT * len(events))(*events)
    sent = send_input(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


def _click_windows(x: int, y: int, button: str, double: bool) -> None:
    """Click at an absolute screen coordinate, with a visible cursor glide.

    The real OS cursor glides to ``(x, y)`` (so the user can watch where
    Computer-Use is acting), the virtual-cursor overlay fires a click pulse at
    the target, and only then does the actual button press go out via
    SendInput. ``glide_os_cursor`` lands the cursor exactly on the target, so
    the click never misses — even across a multi-monitor virtual desktop.
    """
    if os.name != "nt":
        raise RuntimeError("Native Mausklick ist nur auf Windows verfuegbar")

    button_l = button.lower()
    if button_l not in _MOUSE_FLAGS_DOWN:
        raise ValueError(f"Unbekannter Mausbutton: {button!r}. Erlaubt: left/right/middle")

    glide_os_cursor(int(x), int(y))
    try:
        get_virtual_cursor().show_click(int(x), int(y), button=button_l, double=double)
    except Exception:  # noqa: BLE001 — overlay must never break a real click
        pass
    # Click ABSOLUTELY on the virtual desktop (negative-X monitors included) so a
    # flaky SetCursorPos during the glide can't make the button-press land on the
    # wrong screen — the glide is now purely the visible cursor animation.
    _send_click(button_l, double, abs_xy=(int(x), int(y)))


class ClickTool:
    name: str = "click"
    risk_tier: str = "monitor"
    description: str = (
        "Klickt mit der Maus an eine Bildschirm-Koordinate. Optional "
        "rechte/mittlere Maustaste, Doppelklick. Koordinaten sind absolut "
        "(0,0 = obere linke Ecke des primaeren Monitors)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X-Koordinate (Pixel)"},
            "y": {"type": "integer", "description": "Y-Koordinate (Pixel)"},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "default": "left",
            },
            "double": {
                "type": "boolean",
                "default": False,
                "description": "Doppelklick statt Einfachklick",
            },
        },
        "required": ["x", "y"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        try:
            x = int(args["x"])
            y = int(args["y"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(
                success=False,
                output=None,
                error="x und y muessen Integer-Koordinaten sein",
            )
        button = str(args.get("button", "left")).lower()
        double = bool(args.get("double", False))

        if button not in _MOUSE_FLAGS_DOWN:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unbekannter button={button!r}. Erlaubt: left/right/middle",
            )

        if os.name == "nt":
            try:
                await asyncio.to_thread(_click_windows, x, y, button, double)
                kind = "Doppelklick" if double else "Klick"
                return ToolResult(
                    success=True,
                    output=f"{kind} ({button}) an ({x}, {y})",
                )
            except (ValueError, OSError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Klick an ({x},{y}) fehlgeschlagen: {exc}",
                )

        try:
            import pyautogui
        except ImportError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Plattform nicht Windows ({os.name}) und pyautogui fehlt: {exc}. "
                    "pip install pyautogui"
                ),
            )
        try:
            clicks = 2 if double else 1
            pyautogui.click(x=x, y=y, clicks=clicks, button=button)
            return ToolResult(
                success=True,
                output=f"{'Doppel' if double else ''}Klick (pyautogui) an ({x},{y})",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
