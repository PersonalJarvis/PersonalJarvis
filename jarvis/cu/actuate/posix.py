"""macOS / Linux-X11 input backend — pynput preferred, pyautogui fallback.

Why pynput first: it drives Quartz (macOS) and XTest/Xlib (Linux) directly,
positions in the SAME units the OS reports (logical points on macOS — which
matches the mss capture rects the CoordinateMapper is built from — and X11
root pixels on Linux), and does NOT clamp coordinates to the primary screen.
pyautogui is kept as a fallback because it is already a desktop-extra
dependency, but it clamps moves to the primary monitor on multi-monitor
setups (upstream issue #413) — a warning is logged once when the fallback is
active so a misbehaving multi-monitor Linux/macOS setup is diagnosable.

Wayland is refused upstream in ``get_actuator`` (synthetic input is blocked
there); this module only ever runs on X11 or macOS.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from jarvis.cu.actuate.base import ActuationUnavailable, Actuator

logger = logging.getLogger(__name__)

_NO_BACKEND_MSG = (
    "No input backend importable on this host: neither pynput nor pyautogui "
    "is installed. Install the desktop extras "
    "(pip install 'personal-jarvis[desktop]')."
)


def _pynput_key_table(keyboard: Any) -> dict[str, Any]:
    """Map the CU key vocabulary onto pynput ``Key`` members.

    Built dynamically because some members are platform-dependent
    (``Key.insert`` is absent on macOS) — a missing member simply drops out
    of the table and surfaces as a clean "Unknown key" error.
    """
    key = keyboard.Key
    names = {
        "ctrl": "ctrl", "control": "ctrl",
        "shift": "shift",
        "alt": "alt", "menu": "alt",
        # "win" is the Super/Command key off-Windows.
        "win": "cmd", "windows": "cmd", "lwin": "cmd", "cmd": "cmd",
        "command": "cmd", "super": "cmd",
        "esc": "esc", "escape": "esc",
        "enter": "enter", "return": "enter",
        "tab": "tab",
        "space": "space", "spacebar": "space",
        "backspace": "backspace", "back": "backspace",
        "delete": "delete", "del": "delete",
        "insert": "insert", "ins": "insert",
        "home": "home", "end": "end",
        "pageup": "page_up", "pgup": "page_up",
        "pagedown": "page_down", "pgdn": "page_down",
        "left": "left", "up": "up", "right": "right", "down": "down",
        "capslock": "caps_lock",
        **{f"f{i}": f"f{i}" for i in range(1, 13)},
    }
    table: dict[str, Any] = {}
    for alias, member in names.items():
        target = getattr(key, member, None)
        if target is not None:
            table[alias] = target
    return table


class PosixActuator(Actuator):
    """pynput-based backend (pyautogui fallback) for macOS and Linux/X11."""

    name = "posix-pynput"

    def __init__(self) -> None:
        self._mouse: Any = None
        self._keyboard: Any = None
        self._keys: dict[str, Any] = {}
        self._buttons: dict[str, Any] = {}
        self._pyautogui: Any = None
        try:
            from pynput import keyboard, mouse  # noqa: PLC0415

            self._mouse = mouse.Controller()
            self._keyboard = keyboard.Controller()
            self._keys = _pynput_key_table(keyboard)
            self._buttons = {
                "left": mouse.Button.left,
                "right": mouse.Button.right,
                "middle": mouse.Button.middle,
            }
            return
        except Exception as exc:  # noqa: BLE001 — pynput missing/unusable here
            logger.debug("pynput unavailable, trying pyautogui: %s", exc)
        try:
            import pyautogui  # noqa: PLC0415

            # CU runs its own perceive->verify loop; pyautogui's corner
            # failsafe would abort a legitimate top-left click mid-mission.
            pyautogui.FAILSAFE = False
            self._pyautogui = pyautogui
            self.name = "posix-pyautogui"
            logger.warning(
                "[cu] input backend is pyautogui (pynput unavailable) — "
                "multi-monitor coordinates may clamp to the primary screen; "
                "install pynput for full multi-monitor support.",
            )
        except Exception as exc:  # noqa: BLE001
            raise ActuationUnavailable(_NO_BACKEND_MSG) from exc

    # -- Actuator API -------------------------------------------------------

    def cursor_pos(self) -> tuple[int, int] | None:
        try:
            if self._mouse is not None:
                x, y = self._mouse.position
            else:
                pos = self._pyautogui.position()
                x, y = pos.x, pos.y
            return (int(x), int(y))
        except Exception:  # noqa: BLE001 — read-back is best-effort
            logger.debug("cursor_pos failed", exc_info=True)
            return None

    def move(self, x: int, y: int) -> None:
        if self._mouse is not None:
            self._mouse.position = (int(x), int(y))
        else:
            self._pyautogui.moveTo(int(x), int(y))

    def click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
    ) -> None:
        b = button.lower()
        if b not in ("left", "right", "middle"):
            raise ValueError(
                f"Unknown mouse button: {button!r}. Allowed: left/right/middle",
            )
        self.move(x, y)
        if self._mouse is not None:
            self._mouse.click(self._buttons[b], 2 if double else 1)
        else:
            self._pyautogui.click(
                x=int(x), y=int(y), clicks=2 if double else 1, button=b,
            )

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        if self._mouse is None:
            self._pyautogui.moveTo(int(x1), int(y1))
            self._pyautogui.dragTo(
                int(x2), int(y2), duration=max(0.0, duration_s), button="left",
            )
            return
        steps = max(2, min(40, int(duration_s * 60)))
        pause = max(0.0, duration_s) / steps
        self._mouse.position = (int(x1), int(y1))
        self._mouse.press(self._buttons["left"])
        try:
            for i in range(1, steps + 1):
                mx = x1 + (x2 - x1) * i / steps
                my = y1 + (y2 - y1) * i / steps
                self._mouse.position = (int(mx), int(my))
                if pause:
                    time.sleep(pause)
        finally:
            # Never leave the button pressed — a stuck drag wedges the desktop.
            self._mouse.release(self._buttons["left"])

    def scroll(
        self, direction: str, notches: int,
        *, x: int | None = None, y: int | None = None,
    ) -> None:
        d = direction.lower()
        if d not in ("up", "down", "left", "right"):
            raise ValueError(
                f"Unknown direction: {direction!r}. Allowed: up/down/left/right",
            )
        n = abs(int(notches))
        if x is not None and y is not None:
            self.move(int(x), int(y))
        dx = n if d == "right" else -n if d == "left" else 0
        dy = n if d == "up" else -n if d == "down" else 0
        if self._mouse is not None:
            self._mouse.scroll(dx, dy)
        elif dx:
            self._pyautogui.hscroll(dx)
        else:
            self._pyautogui.scroll(dy)

    def key_combo(self, keys: list[str]) -> None:
        # Accept "ctrl+t" combined tokens like the Windows backend.
        from jarvis.cu.actuate.windows import expand_combo_keys  # noqa: PLC0415

        expanded = expand_combo_keys([str(k) for k in keys])
        if self._keyboard is None:
            self._pyautogui.hotkey(*[k.lower() for k in expanded])
            return
        resolved: list[Any] = []
        for k in expanded:
            kl = k.strip().lower()
            if kl in self._keys:
                resolved.append(self._keys[kl])
            elif len(kl) == 1:
                resolved.append(kl)
            else:
                raise ValueError(f"Unknown key: {k!r}")
        for r in resolved:
            self._keyboard.press(r)
        for r in reversed(resolved):
            self._keyboard.release(r)

    def type_text(self, text: str, *, delay_s: float = 0.02) -> None:
        if self._keyboard is None:
            self._pyautogui.typewrite(text, interval=delay_s)
            return
        if delay_s <= 0:
            self._keyboard.type(text)
            return
        for char in text:
            self._keyboard.type(char)
            time.sleep(delay_s)
