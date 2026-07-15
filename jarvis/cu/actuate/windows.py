"""Windows input backend — SendInput, absolute virtual-desktop positioning.

Consolidates the proven Win32 primitives that previously lived duplicated
across the click/type_text/hotkey/scroll tools, with two hard rules learned
from live bugs:

* Every INPUT union declares MOUSEINPUT (the largest member) so
  ``sizeof(INPUT)`` is 40 bytes on x64 — an undersized struct makes
  ``SendInput`` reject every event with ERROR_INVALID_PARAMETER (the
  Google-Flights silent-typing bug, 2026-06-22).
* Pointer positioning uses ``MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK``
  (0..65535 normalized over the whole virtual desktop), never
  ``SetCursorPos`` — SetCursorPos returns unreliable results and misplaces
  across the primary boundary onto negative-origin monitors (the "CU clicks
  void on the left monitor" bug).

All calls run inside :func:`jarvis.cu.geometry.input_space` so metrics,
cursor read-back and event normalization share one per-monitor-DPI-aware
coordinate space even after pywebview flips the process awareness.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from jarvis.cu.actuate.base import Actuator, is_known_key_name
from jarvis.cu.geometry import input_space

logger = logging.getLogger(__name__)

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_ABS_MOVE_FLAGS = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x01000
_WHEEL_DELTA = 120

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_EXTENDEDKEY = 0x0001

_MOUSE_FLAGS_DOWN: dict[str, int] = {
    "left": 0x0002, "right": 0x0008, "middle": 0x0020,
}
_MOUSE_FLAGS_UP: dict[str, int] = {
    "left": 0x0004, "right": 0x0010, "middle": 0x0040,
}

# Virtual-key codes (Microsoft "Virtual Key Codes"); lowercase aliases included.
_VK_TABLE: dict[str, int] = {
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12, "option": 0x12, "menu": 0x12,
    "win": 0x5B, "windows": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "cmd": 0x5B, "command": 0x5B, "meta": 0x5B, "super": 0x5B,
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
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
}

# Extended keys (E0 prefix) — without KEYEVENTF_EXTENDEDKEY a standalone tap
# is rejected or misrouted.
_EXTENDED_VKS = frozenset({
    0x5B, 0x5C, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
    0x24, 0x23, 0x21, 0x22, 0x6F,
})


def resolve_vk(key: str) -> int | None:
    """Virtual-key code for a key name, or ``None`` for an unknown key."""
    k = key.strip().lower()
    if not k:
        return None
    if k in _VK_TABLE:
        return _VK_TABLE[k]
    if len(k) == 1:
        if "a" <= k <= "z":
            return ord(k.upper())
        if "0" <= k <= "9":
            return ord(k)
    return None


def expand_combo_keys(keys: list[str]) -> list[str]:
    """Split combined tokens like ``"ctrl+v"`` into ``["ctrl", "v"]``.

    LLM callers constantly emit whole shortcuts as one token. A token is only
    split when every part resolves to a known key, so a literal '+' key still
    errors loudly instead of vanishing.
    """
    out: list[str] = []
    for token in keys:
        t = str(token).strip()
        if "+" in t and len(t) > 1:
            parts = [p.strip() for p in t.split("+") if p.strip()]
            if len(parts) >= 2 and all(is_known_key_name(p) for p in parts):
                out.extend(parts)
                continue
        out.append(str(token))
    return out


def normalize_virtualdesk(
    x: int, y: int, vx: int, vy: int, vw: int, vh: int,
) -> tuple[int, int]:
    """Map an absolute virtual-desktop pixel to the 0..65535 space
    ``MOUSEEVENTF_ABSOLUTE | VIRTUALDESK`` expects. The virtual origin
    ``(vx, vy)`` may be negative; folding it into the normalization is what
    makes a left-of-primary monitor come out as a valid coordinate."""
    dw = max(1, vw - 1)
    dh = max(1, vh - 1)
    nx = min(65535, max(0, round((int(x) - vx) * 65535 / dw)))
    ny = min(65535, max(0, round((int(y) - vy) * 65535 / dh)))
    return nx, ny


def _structs() -> SimpleNamespace:
    """ctypes INPUT structs, built lazily so the module imports off-Windows."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

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
        # MOUSEINPUT is the largest member and MUST be present — it sizes the
        # union so SendInput's cbSize check passes (see module docstring).
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT))

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send_input = user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT

    return SimpleNamespace(
        ctypes=ctypes, wintypes=wintypes, user32=user32,
        send_input=send_input, ULONG_PTR=ULONG_PTR,
        KEYBDINPUT=KEYBDINPUT, MOUSEINPUT=MOUSEINPUT,
        INPUT_UNION=INPUT_UNION, INPUT=INPUT,
    )


class WindowsActuator(Actuator):
    """SendInput-based backend. All coordinates: physical virtual-desktop px."""

    name = "windows-sendinput"

    def __init__(self) -> None:
        self._t = _structs()

    # -- helpers ----------------------------------------------------------

    def _virtual_bounds(self) -> tuple[int, int, int, int]:
        gsm = self._t.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CX=78, SM_CY=79
        return (gsm(76), gsm(77), gsm(78), gsm(79))

    def _mouse_input(self, dx: int, dy: int, data: int, flags: int):
        t = self._t
        return t.INPUT(
            type=_INPUT_MOUSE,
            union=t.INPUT_UNION(
                mi=t.MOUSEINPUT(dx, dy, data, flags, 0, t.ULONG_PTR(0)),
            ),
        )

    def _key_input(self, vk: int, scan: int, flags: int):
        t = self._t
        return t.INPUT(
            type=_INPUT_KEYBOARD,
            union=t.INPUT_UNION(
                ki=t.KEYBDINPUT(vk, scan, flags, 0, t.ULONG_PTR(0)),
            ),
        )

    def _send(self, events: list) -> None:
        t = self._t
        arr = (t.INPUT * len(events))(*events)
        sent = t.send_input(len(events), arr, t.ctypes.sizeof(t.INPUT))
        if sent != len(events):
            err = t.ctypes.get_last_error()
            raise t.ctypes.WinError(err if err else None)

    def _abs_move_event(self, x: int, y: int):
        vx, vy, vw, vh = self._virtual_bounds()
        nx, ny = normalize_virtualdesk(int(x), int(y), vx, vy, vw, vh)
        return self._mouse_input(nx, ny, 0, _ABS_MOVE_FLAGS)

    # -- Actuator API -------------------------------------------------------

    def cursor_pos(self) -> tuple[int, int] | None:
        t = self._t
        pt = t.wintypes.POINT()
        with input_space():
            if not t.user32.GetCursorPos(t.ctypes.byref(pt)):
                return None
        return (int(pt.x), int(pt.y))

    def move(self, x: int, y: int) -> None:
        with input_space():
            self._send([self._abs_move_event(x, y)])

    def click(
        self, x: int, y: int, *, button: str = "left", double: bool = False,
    ) -> None:
        b = button.lower()
        if b not in _MOUSE_FLAGS_DOWN:
            raise ValueError(
                f"Unknown mouse button: {button!r}. Allowed: left/right/middle",
            )
        with input_space():
            events = [self._abs_move_event(x, y)]
            for _ in range(2 if double else 1):
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_DOWN[b]))
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_UP[b]))
            self._send(events)

    def click_at_cursor(
        self,
        *,
        button: str = "left",
        double: bool = False,
        expected: tuple[int, int] | None = None,
    ) -> None:
        """Press+release at the CURRENT cursor position (no positioning).

        For callers that already positioned the cursor themselves (e.g. the
        visible glide animation) and only need the button events.
        """
        b = button.lower()
        if b not in _MOUSE_FLAGS_DOWN:
            raise ValueError(
                f"Unknown mouse button: {button!r}. Allowed: left/right/middle",
            )
        current = self.cursor_pos()
        if current is None or (
            expected is not None
            and (
                abs(current[0] - expected[0]) > 2
                or abs(current[1] - expected[1]) > 2
            )
        ):
            raise RuntimeError(
                "cursor moved after landing verification; refusing to click",
            )
        with input_space():
            events = []
            for _ in range(2 if double else 1):
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_DOWN[b]))
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_UP[b]))
            self._send(events)

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        self.move(x1, y1)
        self.drag_from_cursor(x1, y1, x2, y2, duration_s=duration_s)

    def drag_from_cursor(
        self, x1: int, y1: int, x2: int, y2: int, *, duration_s: float = 0.4,
    ) -> None:
        """Drag from the current, already-verified cursor position."""
        current = self.cursor_pos()
        if current is None or abs(current[0] - x1) > 2 or abs(current[1] - y1) > 2:
            raise RuntimeError(
                "cursor moved after drag-start verification; refusing to drag",
            )
        steps = max(2, min(40, int(duration_s * 60)))
        pause = max(0.0, duration_s) / steps
        with input_space():
            self._send([self._mouse_input(0, 0, 0, _MOUSE_FLAGS_DOWN["left"])])
            try:
                for i in range(1, steps + 1):
                    mx = x1 + (x2 - x1) * i / steps
                    my = y1 + (y2 - y1) * i / steps
                    self._send([self._abs_move_event(int(mx), int(my))])
                    if pause:
                        time.sleep(pause)
            finally:
                # The button must come back up even if a mid-drag move fails —
                # a stuck pressed button makes the whole desktop unusable.
                self._send([self._mouse_input(0, 0, 0, _MOUSE_FLAGS_UP["left"])])

    def scroll(
        self, direction: str, notches: int,
        *, x: int | None = None, y: int | None = None,
    ) -> None:
        d = direction.lower()
        if d not in ("up", "down", "left", "right"):
            raise ValueError(
                f"Unknown direction: {direction!r}. Allowed: up/down/left/right",
            )
        delta = abs(int(notches)) * _WHEEL_DELTA
        if d in ("down", "left"):
            delta = -delta
        flag = _MOUSEEVENTF_HWHEEL if d in ("left", "right") else _MOUSEEVENTF_WHEEL
        data = delta & 0xFFFFFFFF  # two's-complement DWORD
        with input_space():
            events = []
            if x is not None and y is not None:
                events.append(self._abs_move_event(int(x), int(y)))
            events.append(self._mouse_input(0, 0, data, flag))
            self._send(events)

    def key_combo(self, keys: list[str]) -> None:
        expanded = expand_combo_keys([str(k) for k in keys])
        # A Jarvis-typed Esc must not trip the Escape-to-cancel listener
        # (jarvis.cu.indicator) — stamp BEFORE the OS sees the keystroke.
        from jarvis.cu.indicator.self_input import stamp_if_escape  # noqa: PLC0415

        stamp_if_escape(expanded)
        vk_codes: list[int] = []
        for k in expanded:
            vk = resolve_vk(k)
            if vk is None:
                raise ValueError(f"Unknown key: {k!r}")
            vk_codes.append(vk)

        def flags(vk: int, *, keyup: bool) -> int:
            f = _KEYEVENTF_KEYUP if keyup else 0
            if vk in _EXTENDED_VKS:
                f |= _KEYEVENTF_EXTENDEDKEY
            return f

        events = [
            self._key_input(vk, 0, flags(vk, keyup=False)) for vk in vk_codes
        ] + [
            self._key_input(vk, 0, flags(vk, keyup=True))
            for vk in reversed(vk_codes)
        ]
        with input_space():
            self._send(events)

    def type_text(self, text: str, *, delay_s: float = 0.02) -> None:
        with input_space():
            for char in text:
                # KEYEVENTF_UNICODE takes UTF-16 code UNITS; astral-plane
                # characters (emoji) need their surrogate pair sent as two
                # events each — a bare ord() overflows the WORD wScan field.
                units = char.encode("utf-16-le")
                events = []
                for i in range(0, len(units), 2):
                    code = int.from_bytes(units[i:i + 2], "little")
                    events.append(self._key_input(0, code, _KEYEVENTF_UNICODE))
                    events.append(
                        self._key_input(
                            0, code, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
                        ),
                    )
                self._send(events)
                if delay_s > 0:
                    time.sleep(delay_s)
