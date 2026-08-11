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

# Control characters KEYEVENTF_UNICODE cannot deliver, mapped to the virtual
# key that actually produces them. Windows edit controls act on the KEY, not on
# a synthetic WM_CHAR: a U+000A sent as a unicode "scan code" types NOTHING, so
# type_text("line1\nline2") landed as one run-on line and tabs vanished — while
# the pynput backend on macOS/Linux pressed Enter/Tab for the same string. VK
# 0x0D/0x09 are the main-block keys and deliberately NOT extended (E0 belongs to
# numpad Enter).
_TEXT_CONTROL_VKS: dict[str, int] = {"\n": 0x0D, "\r": 0x0D, "\t": 0x09}

# Punctuation keys, by the character the UNSHIFTED key produces. The shared
# Computer-Use vocabulary (``base.is_known_key_name``) accepts any single
# printable character, and the pynput backend types it happily — but this
# backend resolved only a-z and 0-9, so every punctuation shortcut raised
# "Unknown key" and the action failed outright: Ctrl+- / Ctrl+= (zoom out/in,
# the single most common CU shortcut after copy/paste), Ctrl+, (settings in
# VS Code, Chrome, Slack), Ctrl+/ (comment/shortcut help), Ctrl+[ / Ctrl+]
# (indent, browser back/forward).
#
# VK_OEM_PLUS/MINUS/COMMA/PERIOD are defined by Microsoft as the +, -, , and .
# key "for any country/region", so those four are layout-independent. VK_OEM_1
# and 3..7 are documented as US-layout positions and may sit under a different
# character elsewhere — still strictly better than today's hard failure, and
# applications bind zoom/comment shortcuts to these VKs by position anyway.
# Shifted characters (+, _, {, ...) are deliberately absent: emitting the
# unshifted key for them would send input the caller did not ask for, and a
# loud "Unknown key" beats a wrong keystroke.
_OEM_PUNCTUATION_VKS: dict[str, int] = {
    ";": 0xBA,    # VK_OEM_1   (US)
    "=": 0xBB,    # VK_OEM_PLUS   (any layout)
    ",": 0xBC,    # VK_OEM_COMMA  (any layout)
    "-": 0xBD,    # VK_OEM_MINUS  (any layout)
    ".": 0xBE,    # VK_OEM_PERIOD (any layout)
    "/": 0xBF,    # VK_OEM_2   (US)
    "`": 0xC0,    # VK_OEM_3   (US)
    "[": 0xDB,    # VK_OEM_4   (US)
    "\\": 0xDC,   # VK_OEM_5   (US)
    "]": 0xDD,    # VK_OEM_6   (US)
    "'": 0xDE,    # VK_OEM_7   (US)
}

# Extended keys (E0 prefix) — without KEYEVENTF_EXTENDEDKEY a standalone tap
# is rejected or misrouted.
_EXTENDED_VKS = frozenset({
    0x5B, 0x5C, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
    0x24, 0x23, 0x21, 0x22, 0x6F,
})


class SendInputRefused(OSError):
    """``SendInput`` accepted only part of an input batch (or none of it).

    An ``OSError`` subclass so existing ``except OSError`` / ``except
    Exception`` handlers around actuation keep working unchanged. ``injected``
    is the extra fact they could not get before: how many events the OS took
    before it stopped. Pointer callers need exactly that number to tell whether
    a button-DOWN is still held and a recovery release is owed.
    """

    def __init__(self, message: str, *, injected: int, total: int) -> None:
        super().__init__(message)
        self.injected = injected
        self.total = total


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
        if k in _OEM_PUNCTUATION_VKS:
            return _OEM_PUNCTUATION_VKS[k]
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
        sent = int(t.send_input(len(events), arr, t.ctypes.sizeof(t.INPUT)))
        if sent == len(events):
            return
        err = t.ctypes.get_last_error()
        # Read the code ctypes SAVED for us, and say so plainly when there is
        # none. ``WinError(None)`` reads the THREAD's last-error instead, which
        # the ``use_last_error=True`` wrapper has already restored to whatever
        # it was before the call — so a refused injection used to surface as
        # "OSError: [Errno 0] The operation completed successfully", the least
        # diagnosable message a failed actuation could carry.
        reason = t.ctypes.FormatError(err).strip() if err else "no Win32 error reported"
        raise SendInputRefused(
            f"SendInput injected {sent} of {len(events)} events "
            f"(last_error={err}: {reason})",
            injected=sent,
            total=len(events),
        )

    def _send_pointer_batch(
        self, events: list, button_states: list[int], *, button: str,
    ) -> None:
        """Send a batch that presses ``button``, never leaving it held down.

        ``button_states[i]`` is ``+1`` when ``events[i]`` presses the button,
        ``-1`` when it releases, ``0`` otherwise.

        SendInput fills a batch element by element and stops at the first
        rejection — UIPI blocks the rest the moment a higher-integrity window
        takes the foreground mid-batch. A partly accepted ``[move, down, up]``
        therefore lands the DOWN and drops its UP: the physical button stays
        pressed and the whole desktop is unusable until the user clicks to
        clear it. :meth:`drag_from_cursor` already pays for that lesson in a
        try/finally; the click paths raised straight through it.

        The recovery release is sent ONLY when the injected prefix actually
        leaves the button down. A batch refused outright must not inject a
        stray WM_*BUTTONUP that no press ever preceded.
        """
        try:
            self._send(events)
        except SendInputRefused as exc:
            if sum(button_states[: exc.injected]) <= 0:
                raise
            try:
                self._send([self._mouse_input(0, 0, 0, _MOUSE_FLAGS_UP[button])])
                logger.warning(
                    "SendInput refused a %s-click batch after the button went "
                    "down; released it again to keep the desktop usable.",
                    button,
                )
            except Exception:  # noqa: BLE001 — recovery is best-effort by nature
                logger.error(
                    "Could not release the %s mouse button after a refused click "
                    "batch — it may still be held down.", button, exc_info=True,
                )
            raise

    def _send_press_release(self, downs: list, ups: list) -> None:
        """Press ``downs`` in order, release in reverse — even when a press fails.

        ``ups[i]`` MUST be the release event for ``downs[i]`` (same order, same
        length); the reverse release order is applied here, so callers describe
        key pairs instead of hand-rolling two mirrored lists.

        ``SendInput`` reports how many events it actually inserted, and a
        partial insert is a real outcome: UIPI truncates injection the moment
        the foreground window belongs to a higher-integrity process (Task
        Manager, an elevated app, the secure desktop appearing mid-combo), and
        so does any other Win32 failure inside the batch. Sending the downs and
        the ups as ONE array meant that failure raised with the modifiers
        already in the input stream and their key-ups never emitted — so Ctrl /
        Alt / Shift / Win stayed logically HELD for the rest of the session:
        every later click became a modified click, typing turned into
        shortcuts, and nothing but tapping the physical key could clear it.

        The mouse path in this same backend already guards exactly this case
        ("a stuck pressed button makes the whole desktop unusable",
        :meth:`drag_from_cursor` and :meth:`_send_pointer_batch`); the keyboard
        path did not.

        Like the pointer recovery, this releases only the keys the OS actually
        took (``downs[:injected]``): a batch refused outright must not inject a
        stray WM_KEYUP for a key that was never pressed, which applications
        watching raw key transitions do react to.
        """
        try:
            self._send(downs)
        except SendInputRefused as exc:
            held = list(reversed(ups[: exc.injected]))
            if not held:
                raise
            try:
                self._send(held)
                logger.warning(
                    "SendInput refused a key batch after %d key(s) went down; "
                    "released them again to keep the keyboard usable.", len(held),
                )
            except Exception:  # noqa: BLE001 — recovery is best-effort by nature
                logger.error(
                    "Could not release %d key(s) after a refused batch — a "
                    "modifier may still be held down.", len(held), exc_info=True,
                )
            raise
        self._send(list(reversed(ups)))

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
            states = [0]    # the move neither presses nor releases
            for _ in range(2 if double else 1):
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_DOWN[b]))
                states.append(1)
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_UP[b]))
                states.append(-1)
            self._send_pointer_batch(events, states, button=b)

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
            states: list[int] = []
            for _ in range(2 if double else 1):
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_DOWN[b]))
                states.append(1)
                events.append(self._mouse_input(0, 0, 0, _MOUSE_FLAGS_UP[b]))
                states.append(-1)
            self._send_pointer_batch(events, states, button=b)

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
        # Two self-input stamps, two questions. A Jarvis-typed Esc must not
        # trip the Escape-to-cancel listener (jarvis.cu.indicator), and a
        # Jarvis-typed chord must not fire Jarvis's OWN global shortcuts
        # (jarvis.platform.self_input) — ``ctrl`` as the dictation key sits
        # right next to the ``ctrl+v`` paste. Both stamp BEFORE the OS sees the
        # keystroke.
        from jarvis.cu.indicator.self_input import stamp_if_escape  # noqa: PLC0415
        from jarvis.platform.self_input import synthetic_input  # noqa: PLC0415

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

        # Presses and releases go out as two batches rather than one array, so
        # a refused press can still be undone — see _send_press_release. The
        # emitted order is unchanged: modifiers first, released last.
        downs = [self._key_input(vk, 0, flags(vk, keyup=False)) for vk in vk_codes]
        ups = [self._key_input(vk, 0, flags(vk, keyup=True)) for vk in vk_codes]
        with input_space(), synthetic_input():
            self._send_press_release(downs, ups)

    def type_text(self, text: str, *, delay_s: float = 0.02) -> None:
        from jarvis.platform.self_input import synthetic_input  # noqa: PLC0415

        with input_space(), synthetic_input():
            pending_cr = False
            for char in text:
                # A CRLF pair is ONE line break, not two Enter presses.
                if pending_cr and char == "\n":
                    pending_cr = False
                    continue
                pending_cr = char == "\r"

                vk = _TEXT_CONTROL_VKS.get(char)
                if vk is not None:
                    events = [
                        self._key_input(vk, 0, 0),
                        self._key_input(vk, 0, _KEYEVENTF_KEYUP),
                    ]
                else:
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
