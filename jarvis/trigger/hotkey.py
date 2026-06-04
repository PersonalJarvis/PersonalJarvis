"""Global hotkey trigger with multi-binding support (Call + Hangup).

The hotkey machinery is split behind a cross-platform seam (Wave 1.4, AD-6/AD-8):
``HotkeyTrigger`` is the OS-agnostic orchestrator — it builds the binding rows,
owns the asyncio event queue, and drives the backend lifecycle — while the
per-OS work lives in ``jarvis/trigger/backends/``:

  * Windows keeps the battle-tested ``global-hotkeys`` package
    (``backends/global_hotkeys.py`` — the ``_KEY_MAP``, the single-checker
    refcount, the remove-by-string + pre-remove-on-reentry sequence — relocated
    **verbatim** because each line carries a hard-won BUG fix, AD-7).
  * macOS / Linux-X11 use ``pynput`` (``backends/pynput.py``).
  * Wayland / no-hotkey hosts get ``backends/noop.py`` (logged-once no-op, AD-8).

The backend is chosen by ``make_hotkey_backend()`` from the shared
``jarvis.platform`` capability layer. ``HotkeyTrigger`` never imports a
platform-only package at module scope (HN-7); the lazy import lives inside each
backend's ``register``/``start``.

Default bindings (Phase 1 Jarvis):
  - "call"   -> Ctrl+RightAlt+J  OR  F3+F4
  - "hangup" -> F1+F2

F-keys without a modifier work in global-hotkeys — it registers pure key
combinations, not just modifier combos.

Lifecycle contract (the bug this module kept re-introducing)
------------------------------------------------------------
``global_hotkeys`` is a process-wide *singleton*: ``register_hotkeys`` raises
"already registered" on a duplicate combo, and ``remove_hotkeys`` takes a list
of combo **strings**. The four invariants that keep the shortcuts working
permanently now live inside ``GlobalHotkeysBackend`` (relocated verbatim):

1. teardown removes by combo **string** (never the binding rows);
2. registration pre-removes its own combos so a stale registration left by a
   crashed previous lifecycle never bricks re-entry;
3. a module-level refcount runs a single shared checker so two concurrent
   triggers never double-fire;
4. registration failure (missing package / invalid combo) degrades to a no-op
   instead of crashing the voice pipeline — voice still works via wake word /
   mascot click (cloud-first doctrine + AD-OE6).

``validate_hotkey`` and ``_normalize_combo`` stay importable from this module
for backwards compatibility (the wizard / settings UI import them here).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

# Relocated Windows machinery (AD-7). Imported here so callers that historically
# did ``from jarvis.trigger.hotkey import _normalize_combo`` keep working, and so
# the test-isolation hooks remain reachable at this module path. None of these
# import ``global_hotkeys`` at module scope — that import is lazy inside the
# backend's ``register`` (HN-7).
from jarvis.trigger.backends import HotkeyBackend, make_hotkey_backend
from jarvis.trigger.backends import global_hotkeys as _gh_backend
from jarvis.trigger.backends.global_hotkeys import (
    _KEY_MAP,  # noqa: F401 — re-exported for backwards compatibility
    _normalize_combo,
    _reset_checker_state_for_tests,
)

log = logging.getLogger(__name__)


def __getattr__(name: str):
    """Proxy the relocated refcount so ``hk._CHECKER_REFCOUNT`` reads live.

    The single-checker refcount is canonical in the relocated
    ``GlobalHotkeysBackend`` module; existing regression tests read it via
    ``jarvis.trigger.hotkey._CHECKER_REFCOUNT``. A module ``__getattr__`` keeps
    that attribute live (a plain re-bound int would freeze at import time).
    """
    if name in ("_CHECKER_REFCOUNT", "_CHECKER_LOCK"):
        return getattr(_gh_backend, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Tokens that are modifiers, not "real" keys. A hotkey made of ONLY modifiers
# is not a usable trigger; a usable combo needs at least one real key.
_MODIFIER_TOKENS = frozenset(
    {
        "ctrl", "control", "right_ctrl", "right_control",
        "alt", "right_alt", "left_alt", "altgr",
        "shift", "win", "window",
    }
)


def validate_hotkey(combo: str) -> tuple[bool, str]:
    """Validate a user-supplied push-to-talk hotkey string.

    Returns ``(ok, reason)``. ``reason`` is an English, user-facing sentence
    when ``ok`` is False (the UI surfaces it). The rules encode the CLAUDE.md
    hotkey guidance so a bad combo can never reach the hotkey backend (where an
    invalid registration would silently disable EVERY hotkey):

      * non-empty and parseable (``mod+mod+key`` syntax),
      * at least one non-modifier key (a combo of only Ctrl/Alt/Shift is dead),
      * a modifier OR a second key — a single bare key (``j``) as a global
        hotkey fires on every keystroke while typing,
      * no Windows-key combos (reserved by the OS),
      * not an OS-critical shortcut (Alt+F4 closes windows, Ctrl+C is
        copy/interrupt).
    """
    if not combo or not combo.strip():
        return False, "Hotkey is empty."
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return False, "Hotkey is empty."

    modifiers = [p for p in parts if p in _MODIFIER_TOKENS]
    non_modifiers = [p for p in parts if p not in _MODIFIER_TOKENS]

    if not non_modifiers:
        return False, "Add a real key — a combo of only Ctrl/Alt/Shift cannot be a trigger."
    if not modifiers and len(parts) < 2:
        return False, (
            "Add a modifier (Ctrl/Alt/Shift) or a second key — a single key "
            "would trigger every time you type it."
        )
    if any(p in ("win", "window") for p in modifiers):
        return False, "Windows-key combos are reserved by the OS — pick Ctrl/Alt/Shift."

    _CTRL = ("ctrl", "control", "right_ctrl", "right_control")
    _ALT = ("alt", "right_alt", "left_alt", "altgr")
    alt_held = any(p in _ALT for p in modifiers)
    # "X-only" means X-family modifiers and nothing else — so the exact OS
    # shortcut is blocked while a richer combo that merely contains it (e.g.
    # Ctrl+Shift+C) stays allowed.
    ctrl_only = bool(modifiers) and all(p in _CTRL for p in modifiers)
    if alt_held and "f4" in non_modifiers:
        return False, "Alt+F4 closes the active window — choose another combo."
    if ctrl_only and non_modifiers == ["c"]:
        return False, "Ctrl+C is the copy / interrupt shortcut — choose another combo."

    return True, ""


class HotkeyTrigger:
    """Manages several named hotkey bindings at once.

    Usage:
        trig = HotkeyTrigger(
            {
                "ptt":    ["ctrl+right_alt+j"],  # push-to-talk (both edges)
                "call":   ["f3+f4"],             # toggle (release only)
                "hangup": ["f1+f2"],             # toggle (release only)
            },
            push_to_talk={"ptt"},
        )
        async with trig:
            async for event_name in trig.events():
                if event_name == "ptt_press":     # key down → start recording
                    ...
                elif event_name == "ptt_release":  # key up → submit recording
                    ...
                elif event_name == "call":
                    ...
                elif event_name == "hangup":
                    ...

    A binding named in ``push_to_talk`` emits ``<name>_press`` / ``<name>_release``
    on the two key edges; every other binding emits its bare name on release.
    """

    def __init__(
        self,
        bindings: dict[str, list[str]],
        push_to_talk: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        self._bindings_cfg = bindings
        # Event names that should fire on BOTH key edges (push-to-talk): such
        # a binding emits ``<name>_press`` on the down edge and
        # ``<name>_release`` on the up edge, so the consumer can start
        # recording on press and submit on release. Every other binding keeps
        # the legacy contract (on_release only → a held key fires once).
        self._ptt_events = frozenset(push_to_talk)
        # One shared queue — we yield (event_name) on every press.
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._loop: asyncio.AbstractEventLoop | None = None
        # The per-OS backend (chosen at enter). ``None`` until __aenter__.
        self._backend: HotkeyBackend | None = None
        # Normalized binding rows ``[combo, on_press, on_release]`` handed to
        # the backend; kept for introspection / debugging.
        self._registered: list[list] = []
        # The normalized combo STRINGS — for debugging / parity with the old API.
        self._combo_strings: list[str] = []

    @property
    def _gh(self):
        """Back-compat: the live ``global_hotkeys`` module handle, or ``None``.

        Historically ``HotkeyTrigger`` stored the module here and tests assert
        ``trig._gh is None`` on a degraded enter. The handle now lives on the
        Windows backend; expose it transparently. Non-Windows backends have no
        ``_gh`` attribute, so this reads ``None`` for them — preserving the
        "degraded → None" contract everywhere.
        """
        return getattr(self._backend, "_gh", None)

    def _make_handler(self, event_name: str):
        def _on_press() -> None:
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._push_nowait, event_name)
        return _on_press

    def _push_nowait(self, event_name: str) -> None:
        try:
            self._queue.put_nowait(event_name)
        except asyncio.QueueFull:
            # Many events already pending — drop the OLDEST so the newest
            # intent still lands.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event_name)
            except asyncio.QueueEmpty:
                pass

    def _build_bindings(self) -> tuple[list[list], list[str]]:
        """Build the normalized ``[combo, on_press, on_release]`` rows.

        Backend-agnostic: the combo is normalized to the ``global-hotkeys`` form
        (the Windows path stays byte-identical, AD-7); the ``pynput`` backend
        translates that form to its own key tokens.
        """
        bindings: list[list] = []
        combo_strings: list[str] = []
        for event_name, combos in self._bindings_cfg.items():
            if event_name in self._ptt_events:
                # Push-to-talk: observe BOTH edges. The down edge starts the
                # recording, the up edge submits it. on_press fires repeatedly
                # while the chord is held down (key-repeat polling), so the
                # consumer of ``<name>_press`` must be idempotent.
                on_press = self._make_handler(f"{event_name}_press")
                on_release = self._make_handler(f"{event_name}_release")
            else:
                # Toggle binding: the handler goes on on_release so a held key
                # fires exactly once (no key-repeat storm).
                on_press = None
                on_release = self._make_handler(event_name)
            for combo in combos:
                normalized = _normalize_combo(combo)
                bindings.append([normalized, on_press, on_release])
                combo_strings.append(normalized)
        return bindings, combo_strings

    async def __aenter__(self) -> HotkeyTrigger:
        self._loop = asyncio.get_running_loop()

        # Choose the per-OS backend (Windows global-hotkeys / pynput / no-op).
        # The factory itself never raises (AD-6); a missing optional package is
        # surfaced as a degrade INSIDE the backend's ``register``.
        try:
            backend = make_hotkey_backend()
        except Exception:  # noqa: BLE001 — degrade, never crash the pipeline
            log.error(
                "Hotkey backend selection failed — hotkeys disabled for this "
                "session; voice still works via wake word / mascot click.",
                exc_info=True,
            )
            self._backend = None
            return self

        bindings, combo_strings = self._build_bindings()

        # ``register`` degrades internally (logs + leaves the backend inert) on a
        # missing package or an unrecoverable registration failure — it never
        # raises, so the voice pipeline at ``async with HotkeyTrigger(...)``
        # stays alive (AD-OE6).
        backend.register(bindings)
        if (
            type(backend).__name__ == "GlobalHotkeysBackend"
            and getattr(backend, "_gh", None) is None
        ):
            # The Windows backend degraded (no package / register failure):
            # mirror the historical "no hotkeys" state and skip starting so the
            # single-checker refcount is never incremented on a failed enter.
            self._backend = backend
            return self
        backend.start()

        self._backend = backend
        self._registered = bindings
        self._combo_strings = combo_strings
        log.info(
            "Hotkey-Trigger armed (%s): %s",
            type(backend).__name__,
            ", ".join(f"{name}=[{', '.join(combos)}]"
                      for name, combos in self._bindings_cfg.items()),
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        backend = self._backend
        if backend is None:
            return  # never created (degraded) — nothing to tear down
        try:
            backend.stop()
            backend.unregister()
        except Exception:  # noqa: BLE001 — teardown must never propagate
            log.debug("Hotkey backend teardown failed (non-fatal)", exc_info=True)
        self._registered = []
        self._combo_strings = []
        self._backend = None

    async def events(self) -> AsyncIterator[str]:
        """Yield event names ("call" / "hangup" / ...) on every press."""
        while True:
            name = await self._queue.get()
            yield name


__all__ = [
    "HotkeyTrigger",
    "validate_hotkey",
    "_normalize_combo",
    "_reset_checker_state_for_tests",
]
