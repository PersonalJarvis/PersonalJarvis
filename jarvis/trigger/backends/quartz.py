"""macOS hotkey backend via a Quartz ``CGEventTap`` (AD-8, BUG-065).

Why not pynput on macOS: pynput's darwin keyboard listener resolves the
keyboard layout through the HIToolbox Text Services Manager
(``TISCopyCurrentKeyboardInputSource`` / ``TSMGetInputSourceProperty``) from
its own listener thread. Modern macOS asserts that those calls run on the main
dispatch queue and kills the whole process with an uncatchable SIGILL
(``dispatch_assert_queue_fail``) — observed live on macOS 15.7 during the
first Intel-Mac onboarding. The main thread belongs to pywebview, so the only
safe path is to avoid the TSM APIs entirely.

This backend listens with a listen-only ``CGEventTap`` on a dedicated
CFRunLoop thread (event taps are legal off the main thread; the BUG-058 gate
already preflights the Accessibility + Input Monitoring grants that a tap
needs) and matches chords by PHYSICAL key: a fixed ANSI virtual-keycode table
plus the modifier flags word. No layout translation, no TSM, no main-queue
assertion.

Known trade-offs (documented, honest):

* Letter tokens match the ANSI/QWERTY key positions. On a non-ANSI layout the
  chord follows the physical key, not the printed label — the same behavior
  as most native hotkey utilities.
* ``right_control`` matches either Control key (the flags word does not carry
  a portable left/right distinction).

The combo vocabulary, edge semantics (press fires on the chord-down
transition, release only for push-to-talk rows), the permission fail-closed
gate, and ``received_any_event()`` mirror ``PynputBackend`` exactly, so the
``HotkeyTrigger`` call site stays platform-agnostic (AD-7).
"""

from __future__ import annotations

import logging
import threading

from jarvis.trigger.backends.pynput import (
    _macos_hotkey_permissions_granted,
    _parse_combo_tokens,
)

log = logging.getLogger(__name__)

# Fixed ANSI virtual keycodes (Carbon kVK_ANSI_* / kVK_* constants) -> the
# canonical lowercase tokens `_parse_combo_tokens` produces. Physical-position
# matching keeps this table layout-independent and TSM-free.
_KEYCODE_TO_TOKEN: dict[int, str] = {
    0x00: "a", 0x0B: "b", 0x08: "c", 0x02: "d", 0x0E: "e", 0x03: "f",
    0x05: "g", 0x04: "h", 0x22: "i", 0x26: "j", 0x28: "k", 0x25: "l",
    0x2E: "m", 0x2D: "n", 0x1F: "o", 0x23: "p", 0x0C: "q", 0x0F: "r",
    0x01: "s", 0x11: "t", 0x20: "u", 0x09: "v", 0x0D: "w", 0x07: "x",
    0x10: "y", 0x06: "z",
    0x1D: "0", 0x12: "1", 0x13: "2", 0x14: "3", 0x15: "4", 0x17: "5",
    0x16: "6", 0x1A: "7", 0x1C: "8", 0x19: "9",
    0x31: "space", 0x24: "enter", 0x35: "esc", 0x30: "tab",
    0x33: "backspace", 0x75: "delete",
    0x7A: "f1", 0x78: "f2", 0x63: "f3", 0x76: "f4", 0x60: "f5", 0x61: "f6",
    0x62: "f7", 0x64: "f8", 0x65: "f9", 0x6D: "f10", 0x67: "f11", 0x6F: "f12",
    0x7B: "left", 0x7C: "right", 0x7D: "down", 0x7E: "up",
}

# CGEventFlags modifier masks -> canonical modifier tokens. `ctrl_r` is folded
# into `ctrl` (the portable flags word carries no left/right distinction).
_FLAG_MASK_TO_TOKEN: tuple[tuple[int, str], ...] = (
    (1 << 17, "shift"),      # kCGEventFlagMaskShift
    (1 << 18, "ctrl"),       # kCGEventFlagMaskControl
    (1 << 19, "alt"),        # kCGEventFlagMaskAlternate
    (1 << 20, "cmd"),        # kCGEventFlagMaskCommand
)


class QuartzHotkeyBackend:
    """Quartz event-tap hotkey backend for macOS (AD-8).

    Satisfies the ``HotkeyBackend`` ``Protocol``: receives the same normalized
    ``[combo_str, on_press, on_release]`` rows as every other backend.
    """

    def __init__(self) -> None:
        # Each entry: tokens (frozenset), on_press, on_release, down (bool).
        self._combos: list[dict] = []
        self._started = False
        self._got_event = False
        self._held: set[str] = set()
        self._permission_check = _macos_hotkey_permissions_granted
        self._tap: object | None = None
        self._runloop: object | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Registration + chord matching (mirrors PynputBackend semantics)
    # ------------------------------------------------------------------

    def register(self, bindings, on_event=None) -> None:
        """Stash binding rows as token-set combos. Never raises (AD-6)."""
        combos: list[dict] = []
        for row in bindings:
            combo = row[0]
            on_press = row[1] if len(row) > 1 else None
            on_release = row[2] if len(row) > 2 else None
            tokens = {
                # This backend cannot tell right from left Control.
                "ctrl" if t == "ctrl_r" else t
                for t in _parse_combo_tokens(combo)
            }
            combos.append(
                {
                    "tokens": frozenset(tokens),
                    "on_press": on_press,
                    "on_release": on_release,
                    "down": False,
                }
            )
        self._combos = combos

    def _reconcile(self) -> None:
        """Fire press/release handlers on chord down/up transitions."""
        try:
            permitted = self._permission_check()
        except Exception:  # noqa: BLE001 — the probe must never kill the tap
            permitted = False
        if permitted is not True:
            # A grant can be revoked while the tap thread is alive: clear every
            # partial chord and refuse the callback (same contract as pynput).
            self._held.clear()
            for combo in self._combos:
                combo["down"] = False
            return
        for combo in self._combos:
            is_down = combo["tokens"].issubset(self._held)
            if is_down and not combo["down"]:
                combo["down"] = True
                self._got_event = True
                handler = combo["on_press"] or combo["on_release"]
                if handler is not None:
                    handler()
            elif not is_down and combo["down"]:
                combo["down"] = False
                self._got_event = True
                # Only a push-to-talk binding (both edges set) fires on release.
                if combo["on_press"] is not None and combo["on_release"] is not None:
                    combo["on_release"]()

    def _handle_key_down(self, keycode: int) -> None:
        token = _KEYCODE_TO_TOKEN.get(keycode)
        if token is None:
            return
        self._held.add(token)
        self._reconcile()

    def _handle_key_up(self, keycode: int) -> None:
        token = _KEYCODE_TO_TOKEN.get(keycode)
        if token is None:
            return
        self._reconcile()  # check release edges BEFORE dropping the token
        self._held.discard(token)
        self._reconcile()

    def _handle_flags(self, flags: int) -> None:
        """Sync the modifier subset of the held-set from the flags word."""
        for mask, token in _FLAG_MASK_TO_TOKEN:
            if flags & mask:
                self._held.add(token)
            else:
                # Release edges must be observed before the token drops.
                if token in self._held:
                    self._reconcile()
                self._held.discard(token)
        self._reconcile()

    # ------------------------------------------------------------------
    # Tap lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create the listen-only event tap on its own CFRunLoop thread.

        Degrades to a logged no-op on any failure — missing pyobjc/Quartz,
        missing permissions, or a tap-creation refusal (AD-6).
        """
        if self._started:
            return

        granted = False
        try:
            granted = self._permission_check()
        except Exception:  # noqa: BLE001 — the probe must never crash the trigger
            granted = False
        if granted is not True:
            log.warning(
                "Global hotkeys disabled on macOS: the Accessibility "
                "and Input Monitoring permissions are not both granted. "
                "Use Personal Jarvis > Settings > Permissions, then "
                "re-arm the shortcut or restart Jarvis — voice still "
                "works via the wake word.",
            )
            return

        try:
            import Quartz  # type: ignore[import-untyped]  # lazy (HN-7)
        except Exception as exc:  # noqa: BLE001 — optional [desktop-macos] extra
            log.warning(
                "pyobjc Quartz unavailable (%s) — hotkeys disabled; voice "
                "still works via wake word. Install the [full] profile to "
                "enable global hotkeys.",
                exc,
            )
            return

        def _callback(_proxy, event_type, event, _refcon):
            try:
                if event_type == Quartz.kCGEventKeyDown:
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    self._handle_key_down(int(keycode))
                elif event_type == Quartz.kCGEventKeyUp:
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    self._handle_key_up(int(keycode))
                elif event_type == Quartz.kCGEventFlagsChanged:
                    self._handle_flags(int(Quartz.CGEventGetFlags(event)))
                elif event_type in (
                    Quartz.kCGEventTapDisabledByTimeout,
                    Quartz.kCGEventTapDisabledByUserInput,
                ):
                    tap = self._tap
                    if tap is not None:
                        Quartz.CGEventTapEnable(tap, True)
            except Exception:  # noqa: BLE001 — a callback error must not kill the tap
                log.debug("Quartz hotkey callback error (non-fatal)", exc_info=True)
            return event

        try:
            mask = (
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            )
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                mask,
                _callback,
                None,
            )
            if tap is None:
                raise RuntimeError(
                    "CGEventTapCreate returned None (permission or session "
                    "restriction)"
                )
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        except Exception:  # noqa: BLE001 — degrade, never crash the pipeline
            log.error(
                "Quartz event tap could not be created — hotkeys disabled for "
                "this session; voice still works via wake word.",
                exc_info=True,
            )
            self._tap = None
            return

        self._tap = tap
        ready = threading.Event()

        def _run() -> None:
            loop = Quartz.CFRunLoopGetCurrent()
            self._runloop = loop
            Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            ready.set()
            Quartz.CFRunLoopRun()

        thread = threading.Thread(
            target=_run, name="jarvis-hotkey-tap", daemon=True
        )
        thread.start()
        ready.wait(timeout=5.0)
        self._thread = thread
        self._started = True

    def stop(self) -> None:
        """Stop the run loop and drop the tap. Idempotent, never raises."""
        runloop = self._runloop
        if runloop is not None:
            try:
                import Quartz  # type: ignore[import-untyped]

                Quartz.CFRunLoopStop(runloop)
            except Exception:  # noqa: BLE001 — teardown must never propagate
                log.debug("CFRunLoopStop failed (non-fatal)", exc_info=True)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._runloop = None
        self._thread = None
        self._tap = None
        self._held.clear()
        for combo in self._combos:
            combo["down"] = False
        self._started = False

    def unregister(self) -> None:
        """Drop the armed bindings. The tap is torn down in ``stop``."""
        self._combos = []

    def received_any_event(self) -> bool:
        """True once any bound chord has fired (AD-8 macOS permission hint)."""
        return self._got_event


__all__ = ["QuartzHotkeyBackend"]
