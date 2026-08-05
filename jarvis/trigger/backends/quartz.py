"""macOS hotkey backend via a Quartz ``CGEventTap`` (AD-8, BUG-077).

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
* Mouse buttons (middle and the two side buttons) join the same tap, and only
  when a bound combo actually contains one — the tap never asks for the
  primary click.

The combo vocabulary, edge semantics (``on_press`` fires on the chord-down
transition, ``on_release`` on the chord-up transition — each edge only ever
runs its OWN handler), the permission fail-closed gate, and
``received_any_event()`` mirror ``PynputBackend`` exactly, so the
``HotkeyTrigger`` call site stays platform-agnostic (AD-7).
"""

from __future__ import annotations

import logging
import threading
import time

from jarvis.trigger.backends.global_hotkeys import MOUSE_BUTTON_TOKENS
from jarvis.trigger.backends.pynput import (
    _macos_hotkey_permissions_granted,
    _parse_combo_tokens,
)

log = logging.getLogger(__name__)

# Fixed ANSI virtual keycodes (Carbon kVK_ANSI_* / kVK_* constants) -> the
# canonical lowercase tokens `_parse_combo_tokens` produces. Physical-position
# matching keeps this table layout-independent and TSM-free.
#
# COMPLETENESS IS A CONTRACT, not a nicety. The recorder offers a key, the
# validator accepts it, the route saves it and the row renders it as bound —
# and then a key MISSING here simply never enters ``self._held``, so the chord
# can never match. There is no error anywhere in that chain: the user sees a
# saved shortcut that does nothing, on this OS only. That is exactly what
# happened to the whole nav cluster (Home/End/PageUp/PageDown/Insert), the
# entire numpad and F13-F20, every one of which the picker draws as a live,
# clickable cap (``keyboardLayout.ts`` NAV_ROWS, ``codeToKeyToken``'s
# ``Numpad*`` / ``F13+`` branches) while the Windows backend registers them
# fine.
#
# So the rule for this table: it must cover every token
# ``codeToKeyToken`` / ``_NAMED_KEY_TOKENS`` can emit. Adding a bindable cap to
# the picker without adding its keycode here is the same silent-dead-shortcut
# bug again. ``tests/unit/trigger/test_quartz_backend.py`` pins the parity.
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
    # F13-F20 live on the full-size Apple keyboard (and on every PC keyboard
    # plugged into a Mac). They are the SAFEST keys to bind — nothing else
    # claims them — and ``_SOLO_SAFE_KEYS`` allows f1..f24 solo for that reason.
    0x69: "f13", 0x6B: "f14", 0x71: "f15", 0x6A: "f16",
    0x40: "f17", 0x4F: "f18", 0x50: "f19", 0x5A: "f20",
    0x7B: "left", 0x7C: "right", 0x7D: "down", 0x7E: "up",
    # Nav cluster. ``kVK_Help`` is the physical key an external PC keyboard
    # sends for Insert, which is how the picker labels that cap.
    0x72: "insert", 0x73: "home", 0x74: "page_up",
    0x77: "end", 0x79: "page_down",
    # Numpad. The digits are ``numpad_N`` and the operators carry the
    # ``*_key`` names, both matching what ``codeToKeyToken`` emits and what the
    # Windows library registers. Keypad Enter folds onto the same ``enter``
    # token the main Return key uses — the picker maps ``NumpadEnter`` there
    # too, so one spelling covers both physical keys.
    0x52: "numpad_0", 0x53: "numpad_1", 0x54: "numpad_2", 0x55: "numpad_3",
    0x56: "numpad_4", 0x57: "numpad_5", 0x58: "numpad_6", 0x59: "numpad_7",
    0x5B: "numpad_8", 0x5C: "numpad_9",
    0x45: "add_key", 0x4E: "subtract_key", 0x43: "multiply_key",
    0x4B: "divide_key", 0x41: "decimal_key", 0x4C: "enter",
}

# CGEventFlags modifier masks -> canonical modifier tokens. `ctrl_r` is folded
# into `ctrl` (the portable flags word carries no left/right distinction).
_FLAG_MASK_TO_TOKEN: tuple[tuple[int, str], ...] = (
    (1 << 17, "shift"),      # kCGEventFlagMaskShift
    (1 << 18, "ctrl"),       # kCGEventFlagMaskControl
    (1 << 19, "alt"),        # kCGEventFlagMaskAlternate
    (1 << 20, "cmd"),        # kCGEventFlagMaskCommand
)

# ``kCGMouseEventButtonNumber`` -> the shared mouse token. Quartz numbers the
# buttons in hardware order: 0 left, 1 right, 2 middle, 3 and 4 the side pair.
# Left and right are deliberately absent (see ``MOUSE_BUTTON_TOKENS``), which is
# also why the tap only ever asks for the ``OtherMouse`` events — the primary
# click never even reaches this backend.
_MOUSE_BUTTON_TO_TOKEN: dict[int, str] = {
    2: "mouse_middle",
    3: "mouse_x1",
    4: "mouse_x2",
}

# How long a grant verdict stays good inside the event-tap callback. Short
# enough that revoking Accessibility mid-session stops the shortcuts about as
# fast as a human can switch back from System Settings; long enough that
# ordinary typing does not re-probe TCC natively dozens of times a second.
# See :meth:`QuartzHotkeyBackend._permitted`.
_PERMISSION_TTL_S = 1.0


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
        # Last grant verdict + when it was taken (see ``_permitted``). ``None``
        # means "never probed", so the first event of a session always asks.
        self._permission_cache: bool | None = None
        self._permission_checked_at = 0.0
        self._tap: object | None = None
        self._source: object | None = None
        self._runloop: object | None = None
        self._thread: threading.Thread | None = None
        self._teardown_failed = False
        # Widen the tap to mouse events only when a bound combo needs one: a
        # narrower mask is fewer events crossing the tap for every click the
        # user makes all day.
        self._needs_mouse = False

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
        self._needs_mouse = any(
            combo["tokens"] & MOUSE_BUTTON_TOKENS for combo in combos
        )

    def _permitted(self) -> bool:
        """The grant verdict, re-probed at most every ``_PERMISSION_TTL_S``.

        The probe itself is deliberately uncached one level down
        (``permissions.py::_state``) so a revoked grant is seen live — but it
        is a native TCC call (``AXIsProcessTrusted`` + ``IOHIDCheckAccess``),
        and it used to run on EVERY reconcile. That put two ObjC round trips
        into the event-tap callback for every keystroke the machine sees —
        not just ours, since a session tap observes the whole system. Typing
        an ordinary sentence therefore fired dozens of native probes a second
        inside a callback macOS holds the input pipeline on.

        That is not merely slow, it is how the shortcuts go dead: a tap whose
        callback overruns its deadline is DISABLED by the OS
        (``kCGEventTapDisabledByTimeout``). We re-enable it above, but the
        events during the gap are gone — which is exactly the reported "the
        shortcut works sometimes, or not at all" on a Mac.

        A short TTL keeps the fail-closed contract intact: a grant revoked
        mid-session still takes effect within a second, and a revocation also
        kills the tap outright, so this check is the belt to that suspenders.
        """
        now = time.monotonic()
        if (
            self._permission_cache is not None
            and now - self._permission_checked_at < _PERMISSION_TTL_S
        ):
            return self._permission_cache
        try:
            permitted = self._permission_check() is True
        except Exception:  # noqa: BLE001 — the probe must never kill the tap
            permitted = False
        self._permission_cache = permitted
        self._permission_checked_at = now
        return permitted

    def _reconcile(self) -> None:
        """Fire press/release handlers on chord down/up transitions."""
        permitted = self._permitted()
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
                # Each edge fires ONLY its own handler. A toggle binding carries
                # on_release alone (HotkeyTrigger._build_bindings) so a held key
                # fires exactly once on the up edge; falling back to it here made
                # every toggle fire on key-DOWN on this backend while the Windows
                # path fired on key-up — one config, two behaviours.
                if combo["on_press"] is not None:
                    combo["on_press"]()
            elif not is_down and combo["down"]:
                combo["down"] = False
                self._got_event = True
                # Push-to-talk (both edges set) and toggle (release only) alike.
                if combo["on_release"] is not None:
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

    def _handle_mouse_down(self, button_number: int) -> None:
        token = _MOUSE_BUTTON_TO_TOKEN.get(button_number)
        if token is None:
            return
        self._held.add(token)
        self._reconcile()

    def _handle_mouse_up(self, button_number: int) -> None:
        token = _MOUSE_BUTTON_TO_TOKEN.get(button_number)
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
        if self._teardown_failed:
            log.warning(
                "Quartz hotkeys remain disabled after an event-tap teardown "
                "failure; restart Jarvis to re-arm them safely. Voice still "
                "works via the wake word."
            )
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

        # Resolved once, tolerantly: a pyobjc build without the mouse constants
        # must degrade to "keyboard shortcuts only", never make every event
        # raise inside the tap callback.
        mouse_down_type = getattr(Quartz, "kCGEventOtherMouseDown", None)
        mouse_up_type = getattr(Quartz, "kCGEventOtherMouseUp", None)
        button_field = getattr(Quartz, "kCGMouseEventButtonNumber", None)
        mouse_ready = (
            mouse_down_type is not None
            and mouse_up_type is not None
            and button_field is not None
        )
        if self._needs_mouse and not mouse_ready:
            log.warning(
                "Mouse-button shortcuts are unavailable on this system (the "
                "installed Quartz build exposes no mouse event constants) — "
                "shortcuts that use a key combination still work, and voice "
                "still works via the wake word."
            )

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
                elif mouse_ready and event_type == mouse_down_type:
                    self._handle_mouse_down(
                        int(Quartz.CGEventGetIntegerValueField(event, button_field))
                    )
                elif mouse_ready and event_type == mouse_up_type:
                    self._handle_mouse_up(
                        int(Quartz.CGEventGetIntegerValueField(event, button_field))
                    )
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
            if self._needs_mouse and mouse_ready:
                mask |= Quartz.CGEventMaskBit(mouse_down_type)
                mask |= Quartz.CGEventMaskBit(mouse_up_type)
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
        self._source = source
        ready = threading.Event()
        startup_failed = threading.Event()

        def _run() -> None:
            try:
                loop = Quartz.CFRunLoopGetCurrent()
                self._runloop = loop
                Quartz.CFRunLoopAddSource(
                    loop, source, Quartz.kCFRunLoopCommonModes
                )
                Quartz.CGEventTapEnable(tap, True)
                ready.set()
                Quartz.CFRunLoopRun()
            except Exception:  # noqa: BLE001 — native hook failure must degrade
                startup_failed.set()
                log.error("Quartz event tap thread failed", exc_info=True)
                ready.set()

        thread = threading.Thread(
            target=_run, name="jarvis-hotkey-tap", daemon=True
        )
        self._thread = thread
        thread.start()
        if (
            not ready.wait(timeout=5.0)
            or startup_failed.is_set()
            or not thread.is_alive()
        ):
            log.error(
                "Quartz event tap thread did not become ready — hotkeys "
                "disabled for this session; voice still works via wake word."
            )
            self.stop()
            return
        self._started = True

    def stop(self) -> None:
        """Stop the run loop and drop the tap. Idempotent, never raises."""
        tap = self._tap
        source = self._source
        runloop = self._runloop
        if tap is not None or runloop is not None:
            try:
                import Quartz  # type: ignore[import-untyped]
            except Exception:  # noqa: BLE001 — teardown must never propagate
                Quartz = None  # type: ignore[assignment,misc]
                log.debug("Quartz teardown import failed (non-fatal)", exc_info=True)
            if Quartz is not None:
                # Re-arm used to call only CFRunLoopStop. A sleeping run loop
                # could survive the two-second join, leaving its event tap live;
                # after three Settings saves one physical key-up therefore
                # produced four PTT release callbacks. Run every cleanup step
                # independently so one native failure cannot skip stop+wake.
                teardown_steps = []
                if tap is not None:
                    teardown_steps.append(
                        ("disable", lambda: Quartz.CGEventTapEnable(tap, False))
                    )
                if runloop is not None and source is not None:
                    teardown_steps.append(
                        (
                            "remove source",
                            lambda: Quartz.CFRunLoopRemoveSource(
                                runloop, source, Quartz.kCFRunLoopCommonModes
                            ),
                        )
                    )
                if tap is not None:
                    teardown_steps.append(
                        ("invalidate", lambda: Quartz.CFMachPortInvalidate(tap))
                    )
                if runloop is not None:
                    teardown_steps.extend(
                        [
                            ("stop", lambda: Quartz.CFRunLoopStop(runloop)),
                            ("wake", lambda: Quartz.CFRunLoopWakeUp(runloop)),
                        ]
                    )
                for step_name, step in teardown_steps:
                    try:
                        step()
                    except Exception:  # noqa: BLE001 — continue remaining steps
                        log.debug(
                            "Quartz event tap %s failed (non-fatal)",
                            step_name,
                            exc_info=True,
                        )
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                self._teardown_failed = True
                log.warning(
                    "Quartz hotkey listener did not stop within two seconds; "
                    "the replacement listener will not be started."
                )
        self._runloop = None
        self._thread = None
        self._tap = None
        self._source = None
        self._held.clear()
        for combo in self._combos:
            combo["down"] = False
        self._started = False
        # A re-arm must ask TCC again rather than trust a verdict taken before
        # the user was sent to System Settings.
        self._permission_cache = None
        self._permission_checked_at = 0.0

    def unregister(self) -> None:
        """Drop the armed bindings. The tap is torn down in ``stop``."""
        self._combos = []
        self._needs_mouse = False

    def received_any_event(self) -> bool:
        """True once any bound chord has fired (AD-8 macOS permission hint)."""
        return self._got_event


__all__ = ["QuartzHotkeyBackend"]
