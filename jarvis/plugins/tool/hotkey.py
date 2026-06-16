"""hotkey-Tool: simuliert Tastenkombinationen wie Strg+T, Alt+Tab, Win+R.

Nutzt Win32 ``SendInput`` mit Virtual-Key-Codes als primaeren Pfad. Faellt
bei fehlendem Win32-Subsystem auf ``pyautogui.hotkey`` zurueck — damit
laufen Tests auf Linux/Mac und Headless-CI weiter, auch wenn die echten
Tastenanschlaege nur auf Windows produktiv sind.

Risk-Tier: ``monitor`` — eine Tastenkombi kann nicht-reversible Aktionen
ausloesen (Strg+W schliesst Tab, Strg+S oeffnet Dialog). Toast-Notification,
aber kein Approval-Required.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


# Virtual-Key-Codes — Auszug aus Microsoft "Virtual Key Codes" Doku.
# Siehe https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes
# Wir mappen sowohl Lowercase-Aliase ("ctrl") als auch die offiziellen Namen
# ("control"); fuer Buchstaben/Ziffern berechnen wir den VK on-the-fly.
_VK_TABLE: dict[str, int] = {
    # Modifier
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12, "menu": 0x12,
    "win": 0x5B, "windows": 0x5B, "lwin": 0x5B,
    "rwin": 0x5C,
    # Funktions- + Steuertasten
    "esc": 0x1B, "escape": 0x1B,
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "space": 0x20, "spacebar": 0x20,
    "backspace": 0x08, "back": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "capslock": 0x14,
    # F-Tasten
    **{f"f{i}": 0x6F + i for i in range(1, 13)},  # F1 = 0x70 ... F12 = 0x7B
    # Numpad
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
}


def _resolve_vk(key: str) -> int | None:
    """Liefert den Virtual-Key-Code fuer einen Tasten-Namen, oder None."""
    k = key.strip().lower()
    if not k:
        return None
    if k in _VK_TABLE:
        return _VK_TABLE[k]
    if len(k) == 1:
        # A-Z (0x41-0x5A) und 0-9 (0x30-0x39) — VK == ASCII-Wert.
        if "a" <= k <= "z":
            return ord(k.upper())
        if "0" <= k <= "9":
            return ord(k)
    return None


def _expand_combo_keys(keys: list[str]) -> list[str]:
    """Split combined hotkey strings like ``"ctrl+v"`` into ``["ctrl", "v"]``.

    LLM callers (notably the screenshot-only Computer-Use loop) frequently emit
    a whole shortcut as ONE token — ``"ctrl+v"``, ``"ctrl+shift+t"`` — instead
    of the documented list form. Without this, ``_resolve_vk`` looks up a key
    literally named "ctrl+v", fails, and the paste/shortcut never fires (live
    failure 2026-06-16: three ``ctrl+v`` rejections sank a Discord-post mission).

    '+' is the canonical separator. A token is only split when EVERY resulting
    part resolves to a known key; otherwise it is kept verbatim so a literal
    '+' key (or an unknown combo) still surfaces the normal "Unbekannte Taste"
    error instead of silently vanishing.
    """
    out: list[str] = []
    for token in keys:
        t = token.strip()
        if "+" in t and len(t) > 1:
            parts = [p.strip() for p in t.split("+") if p.strip()]
            if len(parts) >= 2 and all(_resolve_vk(p) is not None for p in parts):
                out.extend(parts)
                continue
        out.append(token)
    return out


def _send_hotkey_windows(keys: list[str]) -> None:
    """Sendet eine Tastenkombination als Win32-SendInput-Sequenz.

    Reihenfolge: alle Keys nacheinander DOWN, dann in umgekehrter Reihenfolge UP.
    Das ist die kanonische Hotkey-Choreographie — moderne Apps interpretieren
    das als gleichzeitiges Druecken.
    """
    if os.name != "nt":
        raise RuntimeError("Native Hotkey-Eingabe ist nur auf Windows verfuegbar")

    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_EXTENDEDKEY = 0x0001
    ULONG_PTR = wintypes.WPARAM

    # Extended keys (E0 prefix). Without KEYEVENTF_EXTENDEDKEY a standalone tap
    # of these is rejected / misrouted by Windows. Main Enter (0x0D) is NOT
    # extended; numpad Enter would be, but we map "enter" to the main key.
    _EXTENDED_VKS = {
        0x5B, 0x5C,                       # lwin, rwin
        0x25, 0x26, 0x27, 0x28,           # left, up, right, down
        0x2D, 0x2E,                       # insert, delete
        0x24, 0x23,                       # home, end
        0x21, 0x22,                       # pageup, pagedown
        0x6F,                             # divide (numpad /)
    }

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

    # CRITICAL: the union MUST contain the largest member (MOUSEINPUT) so that
    # ``ctypes.sizeof(INPUT)`` equals the real Win32 ``INPUT`` size (40 bytes on
    # x64). The previous version declared only ``ki``, giving sizeof(INPUT)=32;
    # ``SendInput`` then received cbSize=32, rejected every event (returned 0),
    # and surfaced as a misleading "WinError 0 / Falscher Parameter". This made
    # the native hotkey path fail for EVERY key (enter, win, ctrl+...) on x64.
    class INPUT_UNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT))

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    # use_last_error=True so ctypes.get_last_error() returns the real GetLastError
    # from SendInput (the old ``ctypes.windll`` path left it at 0 -> bogus error).
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send_input = user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT

    vk_codes: list[int] = []
    for k in keys:
        vk = _resolve_vk(k)
        if vk is None:
            raise ValueError(f"Unbekannte Taste: {k!r}")
        vk_codes.append(vk)

    def _flags(vk: int, *, keyup: bool) -> int:
        flags = KEYEVENTF_KEYUP if keyup else 0
        if vk in _EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        return flags

    # DOWN-Events: in der angegebenen Reihenfolge
    down_events = [
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(vk, 0, _flags(vk, keyup=False), 0, ULONG_PTR(0))),
        )
        for vk in vk_codes
    ]
    # UP-Events: in umgekehrter Reihenfolge
    up_events = [
        INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(ki=KEYBDINPUT(vk, 0, _flags(vk, keyup=True), 0, ULONG_PTR(0))),
        )
        for vk in reversed(vk_codes)
    ]

    all_events = down_events + up_events
    arr = (INPUT * len(all_events))(*all_events)
    sent = send_input(len(all_events), arr, ctypes.sizeof(INPUT))
    if sent != len(all_events):
        err = ctypes.get_last_error()
        raise ctypes.WinError(err if err else None)


class HotkeyTool:
    name: str = "hotkey"
    risk_tier: str = "monitor"
    description: str = (
        "Sendet eine Tastenkombination an das aktive Fenster (z.B. ['ctrl','t'] "
        "fuer neuen Tab, ['alt','tab'] fuer Window-Switch, ['ctrl','shift','t'] "
        "fuer geschlossenen Tab wiederherstellen)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Liste der Tasten in Druck-Reihenfolge. Modifier (ctrl, shift, "
                    "alt, win) zuerst, dann die Aktionstaste. Buchstaben einzeln, "
                    "Sondertasten als Name (enter, tab, esc, f1-f12, left, right, "
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
                error="keys fehlt oder ist keine Liste (Beispiel: ['ctrl', 't'])",
            )
        keys_str = [str(k) for k in keys]
        # Tolerate a combined shortcut string ("ctrl+v") in place of the
        # documented list form (["ctrl", "v"]) — LLM callers emit it constantly.
        keys_str = _expand_combo_keys(keys_str)

        # Vorab-Validierung fuer bessere Fehlermeldung — sonst kommt
        # nur "Unbekannte Taste 'X'" aus dem ctypes-Pfad.
        for k in keys_str:
            if _resolve_vk(k) is None:
                return ToolResult(
                    success=False,
                    output=None,
                    error=(
                        f"Unbekannte Taste: {k!r}. Bekannte Modifier: ctrl, shift, "
                        f"alt, win. Bekannte Tasten: a-z, 0-9, f1-f12, enter, tab, "
                        f"esc, space, backspace, delete, home, end, pageup, "
                        f"pagedown, left, right, up, down."
                    ),
                )

        # pyautogui-Pfad als plattform-unabhaengiger Fallback (Tests/CI auf
        # Linux). Auf Windows bevorzugen wir den nativen Pfad — pyautogui
        # ist langsamer und braucht zusaetzliche Setup-Arbeit beim Hotkey-
        # Mapping (z.B. 'win' heisst dort 'winleft').
        if os.name == "nt":
            try:
                await asyncio.to_thread(_send_hotkey_windows, keys_str)
                return ToolResult(
                    success=True,
                    output=f"Hotkey gesendet: {'+'.join(keys_str)}",
                )
            except (ValueError, OSError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Hotkey '{'+'.join(keys_str)}' fehlgeschlagen: {exc}",
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
            # pyautogui.hotkey nimmt einzelne string-Args, nicht eine Liste
            pyautogui.hotkey(*keys_str)
            return ToolResult(
                success=True,
                output=f"Hotkey gesendet (pyautogui): {'+'.join(keys_str)}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
