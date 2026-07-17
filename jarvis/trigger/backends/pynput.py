"""macOS / Linux-X11 hotkey backend via ``pynput`` (AD-8).

``pynput`` provides cross-platform global keyboard listeners. This backend maps
the jarvis combo vocabulary onto pynput key objects and tracks the live
pressed-key set so a chord fires its press handler on the down edge and its
release handler on the up edge — push-to-talk (down=start recording, up=submit)
works exactly like the Windows ``global-hotkeys`` path.

Combo translation
-----------------
``HotkeyTrigger`` hands every backend rows whose ``row[0]`` is the combo already
normalized to the ``global-hotkeys`` form (``"control + alt + j"``). That keeps
the Windows path byte-identical (AD-7); here we deterministically translate that
form into the pynput key tokens the listener matches against.

A combo is considered *down* when every one of its keys is currently held; the
press handler fires on the transition into that state and the release handler on
the transition out of it. This is the documented robust both-edges pynput
pattern (a bare ``Listener`` with manual state), avoiding the press-only
limitation of ``GlobalHotKeys``.

macOS permission hint (AD-8 / AD-13)
------------------------------------
On macOS a global listener silently fires nothing until the user grants
Input-Monitoring / Accessibility permission. ``received_any_event()`` reports
whether any bound chord has actually fired, so the wizard can detect the
"registered but zero events" state and surface the grant message instead of
leaving the user with a dead hotkey and no explanation.

Import-cleanliness (HN-7): ``pynput`` is imported lazily inside ``start`` so
``import jarvis.trigger.backends.pynput`` succeeds on a box without the package.
A tiny module-level refcount keeps two ``HotkeyTrigger``s (pipeline +
kill-switch) from spawning two OS listeners on the same process.
"""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger(__name__)


def _macos_hotkey_permissions_granted() -> bool:
    """Probe both native grants required by a macOS keyboard event tap."""
    from jarvis.platform.permissions import (  # noqa: PLC0415
        get_system_permission_port,
    )

    return get_system_permission_port().runtime_feature_ready("global_hotkeys")


def _macos_layout_guard_ready() -> bool:
    """Prime + install the TIS main-thread layout guard (BUG-065).

    macOS 15 kills the process with an uncatchable SIGILL when pynput's
    listener thread calls the TIS keyboard-layout APIs off the main thread.
    ``True`` only when a main-thread layout snapshot exists and pynput is
    patched to use it, i.e. the listener is safe to start.
    """
    from jarvis.platform.macos_input_source import (  # noqa: PLC0415
        ensure_pynput_layout_guard,
    )

    return ensure_pynput_layout_guard()

# global-hotkeys modifier token -> canonical pynput modifier name (the attribute
# on ``pynput.keyboard.Key``). Anything not here is a literal key (e.g. "j") or
# an F-key (e.g. "f1", which is also a ``Key`` attribute).
_MODIFIER_TO_KEY_ATTR = {
    "control": "ctrl",
    "right_control": "ctrl_r",
    "alt": "alt",
    "shift": "shift",
    "window": "cmd",
}

# --------------------------------------------------------------------------
# Module-level single-listener guard (mirrors the Windows refcount intent):
# two live HotkeyTriggers must share ONE listener thread, else every press
# fires twice.
# --------------------------------------------------------------------------
_LISTENER_LOCK = threading.Lock()
_LISTENER_REFCOUNT = 0


def _reset_listener_state_for_tests() -> None:
    """Reset the shared listener refcount — test-isolation hook only."""
    global _LISTENER_REFCOUNT
    with _LISTENER_LOCK:
        _LISTENER_REFCOUNT = 0


def _parse_combo_tokens(normalized: str) -> tuple[str, ...]:
    """``"control + alt + j"`` -> ``("ctrl", "alt", "j")`` (canonical tokens).

    Modifiers fold to their ``Key`` attribute name; everything else is lowercased
    and kept as a literal token the listener compares case-insensitively.
    """
    parts = [p.strip().lower() for p in normalized.split("+") if p.strip()]
    return tuple(_MODIFIER_TO_KEY_ATTR.get(p, p) for p in parts)


class PynputBackend:
    """``pynput`` global-hotkey backend for macOS / Linux-X11 (AD-8).

    Satisfies the ``HotkeyBackend`` ``Protocol``. Receives the same normalized
    ``[combo_str, on_press, on_release]`` rows the Windows backend does, so the
    ``HotkeyTrigger`` call site is platform-agnostic.
    """

    def __init__(self) -> None:
        # Each entry: (token_set, on_press|None, on_release|None, currently_down).
        self._combos: list[dict] = []
        # pynput ``keyboard.Listener`` once started; ``None`` until / on degrade.
        # Typed ``object | None`` to avoid a hard module-scope pynput type import.
        self._listener: object | None = None
        self._started = False
        self._got_event = False
        self._incremented = False
        # The live set of canonical tokens currently held down.
        self._held: set[str] = set()
        self._permission_check = lambda: True

    def register(self, bindings, on_event=None) -> None:
        """Stash binding rows as token-set combos. Never raises (AD-6).

        The real OS listener is built in ``start`` (pynput owns its own thread),
        so here we only translate the rows into the matcher's combo records.
        """
        combos: list[dict] = []
        for row in bindings:
            combo = row[0]
            on_press = row[1] if len(row) > 1 else None
            on_release = row[2] if len(row) > 2 else None
            combos.append(
                {
                    "tokens": frozenset(_parse_combo_tokens(combo)),
                    "on_press": on_press,
                    "on_release": on_release,
                    "down": False,
                }
            )
        self._combos = combos

    def _token_for(self, key) -> str | None:
        """Map a pynput key event to our canonical token, or ``None``."""
        # ``KeyCode`` for character keys exposes ``.char``; ``Key`` enum members
        # (modifiers, F-keys) expose ``.name``.
        char = getattr(key, "char", None)
        if char:
            return char.lower()
        name = getattr(key, "name", None)
        if name:
            return name.lower()
        return None

    def _on_press_key(self, key) -> None:
        token = self._token_for(key)
        if token is None:
            return
        self._held.add(token)
        self._reconcile()

    def _on_release_key(self, key) -> None:
        token = self._token_for(key)
        if token is None:
            return
        self._reconcile()  # check release edges BEFORE dropping the token
        self._held.discard(token)
        self._reconcile()

    def _reconcile(self) -> None:
        """Fire press/release handlers on chord down/up transitions."""
        if not self._permission_check():
            # A grant can be revoked while the listener thread is alive. Clear
            # every partial chord and refuse the callback before it can activate
            # voice; a later re-grant starts from a clean key state.
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

    def start(self) -> None:
        """Build + start the shared pynput listener. Degrades, never raises."""
        if self._started:
            return
        try:
            from pynput import keyboard  # type: ignore[import-untyped]  # lazy (HN-7)
        except Exception as exc:  # noqa: BLE001 — optional [desktop] extra
            log.warning(
                "pynput unavailable (%s) — hotkeys disabled; voice still works "
                "via wake word. Install the [full] profile to enable global "
                "hotkeys.",
                exc,
            )
            self._listener = None
            return

        if sys.platform == "darwin":
            # pynput's darwin backend creates a Quartz event tap on its own
            # internal thread; without Accessibility and Input Monitoring that native
            # init is useless at best and a process-level abort at worst
            # (uncatchable, BUG-058 class). Preflight the non-prompting
            # native preflights and fail CLOSED instead of touching pynput.
            granted = False
            try:
                granted = _macos_hotkey_permissions_granted()
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
                self._listener = None
                return
            self._permission_check = _macos_hotkey_permissions_granted

            # BUG-065: pynput's listener thread calls the TIS keyboard-layout
            # APIs (HIToolbox) as it starts; on macOS 15 an off-main-thread
            # TIS call is an uncatchable process kill (SIGILL, "Personal
            # Jarvis quit unexpectedly"). Only start the listener once a
            # main-thread layout snapshot is cached and pynput is patched to
            # reuse it; otherwise degrade — voice still works via wake word.
            guard_ready = False
            try:
                guard_ready = _macos_layout_guard_ready()
            except Exception:  # noqa: BLE001 — the guard must never crash the trigger
                guard_ready = False
            if not guard_ready:
                log.warning(
                    "Global hotkeys disabled on macOS: the keyboard-layout "
                    "snapshot could not be captured on the main thread, and "
                    "starting the listener without it would crash the app "
                    "(TIS main-thread assertion). Voice still works via the "
                    "wake word; restarting Jarvis re-attempts the snapshot.",
                )
                self._listener = None
                return

        try:
            listener = keyboard.Listener(
                on_press=self._on_press_key,
                on_release=self._on_release_key,
            )
            listener.start()
        except Exception:  # noqa: BLE001 — degrade, never crash the pipeline
            log.error(
                "pynput Listener failed to start — hotkeys disabled for this "
                "session; voice still works via wake word.",
                exc_info=True,
            )
            self._listener = None
            return
        self._listener = listener

        with _LISTENER_LOCK:
            global _LISTENER_REFCOUNT
            _LISTENER_REFCOUNT += 1
            self._incremented = True
        self._started = True

    def stop(self) -> None:
        """Stop the pynput listener. Idempotent."""
        listener = self._listener
        if listener is not None:
            try:
                listener.stop()  # type: ignore[attr-defined]  # pynput Listener
            except Exception:  # noqa: BLE001 — teardown must never propagate
                log.debug("pynput listener.stop() failed (non-fatal)",
                          exc_info=True)
        self._listener = None
        self._held.clear()
        for combo in self._combos:
            combo["down"] = False
        if self._incremented:
            with _LISTENER_LOCK:
                global _LISTENER_REFCOUNT
                _LISTENER_REFCOUNT = max(0, _LISTENER_REFCOUNT - 1)
            self._incremented = False
        self._started = False

    def unregister(self) -> None:
        """Drop the armed bindings. The listener is torn down in ``stop``."""
        self._combos = []

    def received_any_event(self) -> bool:
        """True once any bound chord has fired (AD-8 macOS permission hint)."""
        return self._got_event


__all__ = ["PynputBackend", "_parse_combo_tokens", "_reset_listener_state_for_tests"]
