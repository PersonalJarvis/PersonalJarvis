"""The "both X keys at once" gestures: both Alt, both Shift, both Ctrl.

The shared hotkey backends fold the left and right modifier into one token
(the Windows library knows no difference), so a chord of the two Alt (or
Shift, or Ctrl) keys cannot be expressed there. This module watches exactly
that gesture with the cheapest primitive each OS offers — a key-state read
every 50 ms, no hook, no tap:

* Windows: ``GetAsyncKeyState`` on the left/right virtual keys (AltGr raises
  ``VK_RMENU`` too, so the Alt gesture works on AltGr layouts).
* macOS: ``CGEventSourceKeyState`` on the left/right key codes (Option, Shift,
  Control). Whether this read needs a privacy grant (Input Monitoring) is
  UNVERIFIED: the repo contradicts itself (``CGEventSourceFlagsState`` is
  documented in ``jarvis/trigger/hotkey.py`` as needing none) and neither was
  measured on a Mac. It is therefore not treated as an Input Monitoring
  feature and never triggers a permission request; if the gesture does nothing
  the status text (``jarvis/appshot/hotkey.py``) points to another key
  combination.
* Linux/X11: ``XQueryKeymap`` through python-xlib (pynput's own dependency).
* Wayland, headless, missing packages: :func:`make_probe` returns ``None``
  with the reason, and the caller reports the shortcut as unavailable.

Shift and Ctrl are held constantly while typing, so for them the second key
must follow the first within :data:`TOGETHER_S` — a deliberate press of both,
not a capital letter typed with one Shift while the other hand reaches over.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

#: Poll period. A deliberate two-key press lasts far longer than this.
POLL_S = 0.05
#: A second gesture within this window is the same press, not a new one.
_REFIRE_GUARD_S = 0.6
#: Shift/Ctrl: both keys must go down within this long of each other.
TOGETHER_S = 0.5

#: Gesture spelling → the modifier family it watches.
GESTURES: dict[str, str] = {"alt+alt": "alt", "shift+shift": "shift", "ctrl+ctrl": "ctrl"}

Probe = Callable[[], tuple[bool, bool] | None]

# (left, right) virtual keys / key codes per family.
_WIN_VK = {"alt": (0xA4, 0xA5), "shift": (0xA0, 0xA1), "ctrl": (0xA2, 0xA3)}
_MAC_KEYCODES = {"alt": (58, 61), "shift": (56, 60), "ctrl": (59, 62)}
_X11_KEYSYMS = {
    "alt": ("Alt_L", ("Alt_R", "ISO_Level3_Shift")),
    "shift": ("Shift_L", ("Shift_R",)),
    "ctrl": ("Control_L", ("Control_R",)),
}


def _windows_probe(family: str) -> Probe | None:
    import ctypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_state = user32.GetAsyncKeyState
    get_state.argtypes = [ctypes.c_int]
    get_state.restype = ctypes.c_short
    left_vk, right_vk = _WIN_VK[family]

    def probe() -> tuple[bool, bool]:
        return bool(get_state(left_vk) & 0x8000), bool(get_state(right_vk) & 0x8000)

    return probe


def _macos_probe(family: str) -> Probe | None:
    import Quartz  # type: ignore[import-untyped]  # noqa: PLC0415

    source = Quartz.kCGEventSourceStateHIDSystemState
    key_state = Quartz.CGEventSourceKeyState
    left_code, right_code = _MAC_KEYCODES[family]

    def probe() -> tuple[bool, bool]:
        return bool(key_state(source, left_code)), bool(key_state(source, right_code))

    return probe


def _x11_probe(family: str) -> Probe | None:
    from Xlib import XK  # type: ignore[import-untyped]  # noqa: PLC0415
    from Xlib.display import Display  # type: ignore[import-untyped]  # noqa: PLC0415

    display = Display()
    left_name, right_names = _X11_KEYSYMS[family]
    left = display.keysym_to_keycode(XK.string_to_keysym(left_name))
    rights = {
        code
        for code in (display.keysym_to_keycode(XK.string_to_keysym(n)) for n in right_names)
        if code
    }
    if not left or not rights:
        display.close()
        return None

    def down(keymap: list[int], code: int) -> bool:
        return bool(keymap[code // 8] & (1 << (code % 8)))

    def probe() -> tuple[bool, bool]:
        keymap = display.query_keymap()
        return down(keymap, left), any(down(keymap, code) for code in rights)

    return probe


def make_probe(family: str = "alt") -> tuple[Probe | None, str]:
    """The key-state reader for ``family`` on this host, or ``None`` and why not."""
    from jarvis.platform import detect_platform  # noqa: PLC0415
    from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

    platform = detect_platform()
    try:
        if platform == "win32":
            return _windows_probe(family), ""
        if not display_present():
            return None, "No display on this computer, so there is no keyboard to watch."
        if platform == "darwin":
            return _macos_probe(family), ""
        if is_wayland():
            return None, (
                "Wayland does not let apps watch global keys. Pick a different "
                "shortcut in an X11 session, or ask for an appshot by voice."
            )
        probe = _x11_probe(family)
        if probe is None:
            return None, "This keyboard layout has no second key of that kind."
        return probe, ""
    except ImportError as exc:  # the missing package IS the answer; the caller shows it
        return None, f"The key reader is not installed ({exc.name})."
    except Exception as exc:  # noqa: BLE001 - an unreadable keyboard is "unavailable"
        log.warning("appshot: key-state probe failed to start", exc_info=True)
        return None, f"The keyboard could not be read ({type(exc).__name__})."


class BothKeysWatcher:
    """Calls ``on_fire`` once each time both keys of a pair go down together."""

    def __init__(
        self,
        on_fire: Callable[[], None],
        *,
        probe: Probe,
        poll_s: float = POLL_S,
        clock: Callable[[], float] | None = None,
        together_s: float | None = None,
    ) -> None:
        import time  # noqa: PLC0415

        self._on_fire = on_fire
        self._probe = probe
        self._poll_s = poll_s
        self._clock = clock or time.monotonic
        self._together_s = together_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._armed = True
        self._last_fire = float("-inf")
        self._first_down: float | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="appshot-both-keys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def step(self) -> bool:
        """One poll. ``True`` when this poll fired. Exposed for tests."""
        state = self._probe()
        if state is None:
            return False
        left, right = state
        if not (left or right):
            self._armed = True
            self._first_down = None
            return False
        now = self._clock()
        if self._first_down is None:
            self._first_down = now
        if left and right and self._armed:
            self._armed = False
            if self._together_s is not None and now - self._first_down > self._together_s:
                return False  # one key held for a while, the other joined: typing
            if now - self._last_fire < _REFIRE_GUARD_S:
                return False
            from jarvis.platform.self_input import synthetic_input_recent  # noqa: PLC0415

            if synthetic_input_recent():
                return False
            self._last_fire = now
            self._on_fire()
            return True
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            try:
                self.step()
            except Exception:  # noqa: BLE001 - one bad read must not kill the shortcut
                log.debug("appshot: key-state read failed", exc_info=True)


#: The original name; the both-Alt gesture is the same watcher.
BothAltWatcher = BothKeysWatcher


def together_window(family: str) -> float | None:
    """Alt keeps its original any-order behaviour; Shift/Ctrl need a joint press."""
    return None if family == "alt" else TOGETHER_S


__all__ = [
    "GESTURES",
    "POLL_S",
    "TOGETHER_S",
    "BothAltWatcher",
    "BothKeysWatcher",
    "make_probe",
    "together_window",
]
