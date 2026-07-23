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
import sys
import time
from typing import Any

from jarvis.cu.actuate.base import (
    LANDING_TOLERANCE,
    ActuationUnavailable,
    Actuator,
)

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
        "alt": "alt", "option": "alt", "menu": "alt",
        # "win" is the Super/Command key off-Windows.
        "win": "cmd", "windows": "cmd", "lwin": "cmd", "cmd": "cmd",
        "command": "cmd", "meta": "cmd", "super": "cmd",
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

            if sys.platform == "darwin":
                # BUG-065: ``keyboard.Controller()`` builds its keycode map
                # via the TIS APIs, and on macOS 15 an off-main-thread TIS
                # call kills the whole process (SIGILL). The guard makes the
                # constructor reuse the main-thread layout snapshot — or
                # raise an ordinary exception that drops us to the pyautogui
                # fallback below instead of crashing the app.
                from jarvis.platform.macos_input_source import (  # noqa: PLC0415
                    ensure_pynput_layout_guard,
                )

                ensure_pynput_layout_guard()
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
            if sys.platform == "darwin":
                # Use the same Quartz global coordinate space as CGEventPost,
                # CGDisplayBounds/mss and AX window geometry. pynput's macOS
                # getter converts NSEvent coordinates through the main
                # display's pixel height, which can diverge on Retina and
                # vertically arranged multi-monitor desktops.
                import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

                point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
                return (int(point.x), int(point.y))
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
        if sys.platform == "darwin":
            import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

            event = Quartz.CGEventCreateMouseEvent(
                None,
                Quartz.kCGEventMouseMoved,
                (int(x), int(y)),
                Quartz.kCGMouseButtonLeft,
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            return
        if self._mouse is not None:
            self._mouse.position = (int(x), int(y))
        else:
            self._pyautogui.moveTo(int(x), int(y))

    def click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
    ) -> None:
        self.move(x, y)
        self.click_at_cursor(
            button=button,
            double=double,
            expected=(int(x), int(y)),
        )

    def click_at_cursor(
        self,
        *,
        button: str = "left",
        double: bool = False,
        expected: tuple[int, int] | None = None,
    ) -> None:
        """Press and release at the current, already-verified cursor position."""
        b = button.lower()
        if b not in ("left", "right", "middle"):
            raise ValueError(
                f"Unknown mouse button: {button!r}. Allowed: left/right/middle",
            )
        current = self.cursor_pos()
        if current is None or (
            expected is not None
            and (
                abs(current[0] - expected[0]) > LANDING_TOLERANCE
                or abs(current[1] - expected[1]) > LANDING_TOLERANCE
            )
        ):
            raise RuntimeError(
                "cursor moved after landing verification; refusing to click",
            )
        if sys.platform == "darwin":
            self._quartz_click(current, b, double=double)
            return
        if self._mouse is not None:
            self._mouse.click(self._buttons[b], 2 if double else 1)
        else:
            self._pyautogui.click(
                clicks=2 if double else 1, button=b,
            )

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        self.move(x1, y1)
        self.drag_from_cursor(x1, y1, x2, y2, duration_s=duration_s)

    def drag_from_cursor(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        """Drag from the current, already-verified position to ``(x2, y2)``."""
        current = self.cursor_pos()
        if current is None or (
            abs(current[0] - int(x1)) > LANDING_TOLERANCE
            or abs(current[1] - int(y1)) > LANDING_TOLERANCE
        ):
            raise RuntimeError(
                "cursor moved after drag-start verification; refusing to drag",
            )
        if sys.platform == "darwin":
            self._quartz_drag(current, (int(x2), int(y2)), duration_s)
            return
        if self._mouse is None:
            self._pyautogui.dragTo(
                int(x2), int(y2), duration=max(0.0, duration_s), button="left",
            )
            return
        steps = max(2, min(40, int(duration_s * 60)))
        pause = max(0.0, duration_s) / steps
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
        if sys.platform == "darwin":
            import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

            event = Quartz.CGEventCreateScrollWheelEvent(
                None,
                Quartz.kCGScrollEventUnitLine,
                2,
                dy,
                dx,
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            return
        if self._mouse is not None:
            self._mouse.scroll(dx, dy)
        elif dx:
            self._pyautogui.hscroll(dx)
        else:
            self._pyautogui.scroll(dy)

    def key_combo(self, keys: list[str]) -> None:
        # Accept "ctrl+t" combined tokens like the Windows backend. The
        # self_input stamp keeps a Jarvis-typed Esc from tripping the
        # Escape-to-cancel listener (jarvis.cu.indicator).
        from jarvis.cu.actuate.windows import expand_combo_keys  # noqa: PLC0415
        from jarvis.cu.indicator.self_input import stamp_if_escape  # noqa: PLC0415

        expanded = expand_combo_keys([str(k) for k in keys])
        stamp_if_escape(expanded)
        if self._keyboard is None:
            aliases = {"cmd", "command", "meta", "super", "win", "windows", "lwin"}
            mapped: list[str] = []
            for key in expanded:
                normalized = key.strip().lower()
                if normalized in aliases:
                    mapped.append("command" if sys.platform == "darwin" else "winleft")
                elif normalized == "option":
                    mapped.append("alt")
                else:
                    mapped.append(normalized)
            self._pyautogui.hotkey(*mapped)
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

    @staticmethod
    def _quartz_button_spec(quartz: Any, button: str) -> tuple[Any, Any, Any, Any]:
        return {
            "left": (
                quartz.kCGEventLeftMouseDown,
                quartz.kCGEventLeftMouseUp,
                quartz.kCGEventLeftMouseDragged,
                quartz.kCGMouseButtonLeft,
            ),
            "right": (
                quartz.kCGEventRightMouseDown,
                quartz.kCGEventRightMouseUp,
                quartz.kCGEventRightMouseDragged,
                quartz.kCGMouseButtonRight,
            ),
            "middle": (
                quartz.kCGEventOtherMouseDown,
                quartz.kCGEventOtherMouseUp,
                quartz.kCGEventOtherMouseDragged,
                quartz.kCGMouseButtonCenter,
            ),
        }[button]

    @classmethod
    def _quartz_click(
        cls,
        point: tuple[int, int],
        button: str,
        *,
        double: bool,
    ) -> None:
        """Post macOS button events in Quartz global display coordinates."""
        import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

        down, up, _dragged, button_id = cls._quartz_button_spec(Quartz, button)
        for click_state in range(1, 3 if double else 2):
            for event_type in (down, up):
                event = Quartz.CGEventCreateMouseEvent(
                    None, event_type, point, button_id,
                )
                Quartz.CGEventSetIntegerValueField(
                    event,
                    Quartz.kCGMouseEventClickState,
                    click_state,
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    @classmethod
    def _quartz_drag(
        cls,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_s: float,
    ) -> None:
        """Post a left-button drag entirely in Quartz global coordinates."""
        import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

        down, up, dragged, button_id = cls._quartz_button_spec(Quartz, "left")
        press = Quartz.CGEventCreateMouseEvent(None, down, start, button_id)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, press)
        steps = max(2, min(40, int(max(0.0, duration_s) * 60)))
        pause = max(0.0, duration_s) / steps
        try:
            for index in range(1, steps + 1):
                point = (
                    int(start[0] + (end[0] - start[0]) * index / steps),
                    int(start[1] + (end[1] - start[1]) * index / steps),
                )
                event = Quartz.CGEventCreateMouseEvent(
                    None, dragged, point, button_id,
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
                if pause:
                    time.sleep(pause)
        finally:
            release = Quartz.CGEventCreateMouseEvent(None, up, end, button_id)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, release)

    def type_text(self, text: str, *, delay_s: float = 0.02) -> int:  # type: ignore[override]
        """Type Unicode text into the focused control.

        Returns the number of requested characters that could NOT actually be
        typed on this backend (0 on full success). The pyautogui fallback —
        always used on Linux, where the desktop extras skip pynput — silently
        DROPS every non-ASCII character; when those cannot be routed through
        ``xdotool`` either, they are counted and reported here so the caller
        can stay honest about a partial/failed type instead of claiming a full
        success (the base contract widens ``None`` -> dropped-count).
        """
        if self._keyboard is None:
            # pyautogui.typewrite silently DROPS every non-ASCII character
            # (umlauts, eszett, accents, CJK), and Linux always runs on this fallback (the
            # desktop extras skip pynput there — evdev would compile against
            # kernel headers). Route non-ASCII through `xdotool type`, which
            # synthesizes arbitrary Unicode on X11 and is provisioned by the
            # installer since deep-dive 2026-07-15 H-01.
            if not text.isascii() and self._xdotool_type(text, delay_s=delay_s):
                return 0
            dropped = 0
            if not text.isascii():
                # Only non-ASCII codepoints are lost; pyautogui still types the
                # ASCII portion of a mixed string, so count exactly the losses.
                dropped = sum(1 for char in text if not char.isascii())
                logger.warning(
                    "[cu] typing non-ASCII text via pyautogui — %d character(s) "
                    "outside ASCII will be dropped (install xdotool or pynput "
                    "for Unicode input).",
                    dropped,
                )
            self._pyautogui.typewrite(text, interval=delay_s)
            return dropped
        if delay_s <= 0:
            self._keyboard.type(text)
            return 0
        for char in text:
            self._keyboard.type(char)
            time.sleep(delay_s)
        return 0

    @staticmethod
    def _xdotool_type(text: str, *, delay_s: float) -> bool:
        """Type ``text`` via ``xdotool type`` on X11. Returns False when the
        binary is missing or the call fails, so the caller can fall back."""
        if not sys.platform.startswith("linux"):
            return False
        import shutil  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        if shutil.which("xdotool") is None:
            return False
        try:
            from jarvis.core.process_utils import (  # noqa: PLC0415
                NO_WINDOW_CREATIONFLAGS,
            )

            delay_ms = max(1, int(delay_s * 1000)) if delay_s > 0 else 1
            proc = subprocess.run(
                ["xdotool", "type", "--delay", str(delay_ms), "--", text],
                capture_output=True,
                timeout=max(30.0, 0.5 * len(text)),
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except Exception:  # noqa: BLE001 — fall back to pyautogui
            logger.debug("xdotool type failed", exc_info=True)
            return False
        return proc.returncode == 0
