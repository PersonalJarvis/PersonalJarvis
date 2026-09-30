"""The "both Alt keys at once" gesture.

The shared hotkey backends fold left and right Alt into one token (the
Windows library knows no difference), so a chord of the two Alt keys cannot be
expressed there. This module watches exactly that gesture with the cheapest
primitive each OS offers — a key-state read every 50 ms, no hook, no tap:

* Windows: ``GetAsyncKeyState`` on ``VK_LMENU`` / ``VK_RMENU`` (AltGr raises
  ``VK_RMENU`` too, so the gesture works on AltGr layouts).
* macOS: ``CGEventSourceKeyState`` on the left/right Option key codes. Needs
  the same Input Monitoring grant as every other global shortcut; without it
  the read stays false and nothing fires.
* Linux/X11: ``XQueryKeymap`` through python-xlib (pynput's own dependency).
* Wayland, headless, missing packages: :func:`make_probe` returns ``None``
  with the reason, and the caller reports the shortcut as unavailable.
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

Probe = Callable[[], tuple[bool, bool] | None]


def _windows_probe() -> Probe | None:
    import ctypes  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_state = user32.GetAsyncKeyState
    get_state.argtypes = [ctypes.c_int]
    get_state.restype = ctypes.c_short
    vk_lmenu, vk_rmenu = 0xA4, 0xA5

    def probe() -> tuple[bool, bool]:
        return bool(get_state(vk_lmenu) & 0x8000), bool(get_state(vk_rmenu) & 0x8000)

    return probe


def _macos_probe() -> Probe | None:
    import Quartz  # type: ignore[import-untyped]  # noqa: PLC0415

    source = Quartz.kCGEventSourceStateHIDSystemState
    key_state = Quartz.CGEventSourceKeyState
    left_option, right_option = 58, 61

    def probe() -> tuple[bool, bool]:
        return bool(key_state(source, left_option)), bool(key_state(source, right_option))

    return probe


def _x11_probe() -> Probe | None:
    from Xlib import XK  # type: ignore[import-untyped]  # noqa: PLC0415
    from Xlib.display import Display  # type: ignore[import-untyped]  # noqa: PLC0415

    display = Display()
    left = display.keysym_to_keycode(XK.XK_Alt_L)
    rights = {
        code
        for code in (
            display.keysym_to_keycode(XK.XK_Alt_R),
            display.keysym_to_keycode(XK.string_to_keysym("ISO_Level3_Shift")),
        )
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


def make_probe() -> tuple[Probe | None, str]:
    """The key-state reader for this host, or ``None`` and why not."""
    from jarvis.platform import detect_platform  # noqa: PLC0415
    from jarvis.platform.probes import display_present, is_wayland  # noqa: PLC0415

    platform = detect_platform()
    try:
        if platform == "win32":
            return _windows_probe(), ""
        if not display_present():
            return None, "No display on this computer, so there is no keyboard to watch."
        if platform == "darwin":
            return _macos_probe(), ""
        if is_wayland():
            return None, (
                "Wayland does not let apps watch global keys. Pick a different "
                "shortcut in an X11 session, or ask for an appshot by voice."
            )
        probe = _x11_probe()
        if probe is None:
            return None, "This keyboard layout has no second Alt key."
        return probe, ""
    except ImportError as exc:  # the missing package IS the answer; the caller shows it
        return None, f"The key reader is not installed ({exc.name})."
    except Exception as exc:  # noqa: BLE001 - an unreadable keyboard is "unavailable"
        log.warning("appshot: key-state probe failed to start", exc_info=True)
        return None, f"The keyboard could not be read ({type(exc).__name__})."


class BothAltWatcher:
    """Calls ``on_fire`` once each time both Alt keys go down together."""

    def __init__(
        self,
        on_fire: Callable[[], None],
        *,
        probe: Probe,
        poll_s: float = POLL_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time  # noqa: PLC0415

        self._on_fire = on_fire
        self._probe = probe
        self._poll_s = poll_s
        self._clock = clock or time.monotonic
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._armed = True
        self._last_fire = float("-inf")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="appshot-alt-alt", daemon=True)
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
            return False
        if left and right and self._armed:
            self._armed = False
            now = self._clock()
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


__all__ = ["POLL_S", "BothAltWatcher", "make_probe"]
