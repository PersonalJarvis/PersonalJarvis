"""Windows hotkey backend — the relocated ``global-hotkeys`` logic (AD-7).

This module owns the Windows-only hotkey machinery that used to live inline in
``jarvis/trigger/hotkey.py``. It was **relocated verbatim** — the behaviour is
not refactored — because every line here carries a hard-won BUG fix that the
F1+F2 / F3+F4 shortcuts depend on:

1. ``remove_hotkeys`` is fed combo **strings** (``self._combo_strings``), never
   the ``[combo, on_press, on_release]`` rows — the historical ``__aexit__`` bug
   passed the rows, which raises ``AttributeError`` and left a stale registration
   that bricked every hotkey on the next re-entry.
2. ``register`` pre-removes its own combos before registering, so a stale
   registration left by a crashed previous lifecycle never bricks re-entry with
   "already registered".
3. A module-level refcount starts the single shared checker on the first live
   backend and stops it on the last, so two concurrent triggers (voice pipeline
   + kill-switch) never spawn two checker threads that double-fire every press.
4. A registration failure (missing ``global_hotkeys`` package, or an invalid
   combo) degrades to a no-op instead of crashing the voice pipeline — voice
   still works via wake word / mascot click (cloud-first doctrine + AD-OE6).

``global_hotkeys`` is imported lazily inside ``register`` (HN-7): importing this
module on a host without the package must stay clean.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

# Map jarvis.toml combo syntax -> global-hotkeys syntax. global-hotkeys only
# knows the generic "alt"/"control" — no left/right distinction except for
# "right_control" — so we fold right_alt/left_alt onto "alt".
_KEY_MAP = {
    "ctrl": "control",
    "right_ctrl": "right_control",
    "right_alt": "alt",
    "left_alt": "alt",
    "altgr": "alt",
    "win": "window",
    "shift": "shift",
}

# --------------------------------------------------------------------------
# Module-level single-checker guard.
#
# ``global_hotkeys.start_checking_hotkeys`` unconditionally spawns a new polling
# thread, and the registry it iterates is shared process-wide. If two triggers
# each start a checker, BOTH threads see every press and the callbacks fire
# twice. We therefore reference-count active triggers and start/stop the single
# shared checker on the 0<->1 boundary.
# --------------------------------------------------------------------------
_CHECKER_LOCK = threading.Lock()
_CHECKER_REFCOUNT = 0


def _start_checker_once(gh) -> None:
    """Start the shared checker on the 0->1 transition.

    Starts BEFORE incrementing so that a failing ``start_checking_hotkeys``
    leaves the refcount at its previous value (no desync).
    """
    global _CHECKER_REFCOUNT
    with _CHECKER_LOCK:
        if _CHECKER_REFCOUNT == 0:
            gh.start_checking_hotkeys()
        _CHECKER_REFCOUNT += 1


def _stop_checker_once(gh) -> None:
    """Stop the shared checker on the 1->0 transition."""
    global _CHECKER_REFCOUNT
    with _CHECKER_LOCK:
        if _CHECKER_REFCOUNT <= 0:
            return  # already stopped; never call stop without a matching start
        _CHECKER_REFCOUNT -= 1
        if _CHECKER_REFCOUNT == 0:
            gh.stop_checking_hotkeys()


def _reset_checker_state_for_tests() -> None:
    """Reset the shared refcount — test-isolation hook only."""
    global _CHECKER_REFCOUNT
    with _CHECKER_LOCK:
        _CHECKER_REFCOUNT = 0


def _normalize_combo(combo: str) -> str:
    """`ctrl+right_alt+j` -> `control + alt + j`."""
    parts = [p.strip().lower() for p in combo.split("+")]
    return " + ".join(_KEY_MAP.get(p, p) for p in parts)


class GlobalHotkeysBackend:
    """Windows ``global-hotkeys`` backend (relocated logic, AD-7).

    Satisfies the ``HotkeyBackend`` ``Protocol``. The binding rows it receives
    are already normalized ``[combo_str, on_press, on_release]`` rows produced by
    ``HotkeyTrigger`` (the trigger remains the single producer of those rows so
    the relocation is behaviour-preserving). This backend only relocates the
    ``global_hotkeys`` calls + the refcount/remove-by-string/pre-remove sequence.
    """

    def __init__(self) -> None:
        # Module handle captured at register; ``None`` when registration degraded.
        self._gh = None
        # The ``global_hotkeys`` rows ``[combo, on_press, on_release]``.
        self._registered: list[list] = []
        # The normalized combo STRINGS — the format ``remove_hotkeys`` needs.
        self._combo_strings: list[str] = []
        self._started = False

    def register(self, bindings, on_event=None) -> None:
        """Arm ``bindings``; degrade to a no-op (``_gh=None``) on any failure."""
        try:
            import global_hotkeys as gh  # type: ignore[import-untyped]
        except Exception as exc:  # noqa: BLE001 — optional [desktop] extra
            log.warning(
                "global_hotkeys unavailable (%s) — hotkeys disabled; voice "
                "still works via wake word / mascot click. Install the "
                "[desktop] extra to enable F1+F2 / F3+F4.",
                exc,
            )
            self._gh = None
            return

        combo_strings = [row[0] for row in bindings]

        # Idempotent (re-)registration. A previous lifecycle in this process
        # may have left these combos in the shared singleton (historically the
        # broken __aexit__ never cleaned them up). Pre-removing makes re-entry
        # safe — otherwise register_hotkeys raises "already registered" and
        # EVERY hotkey dies. Removing an unregistered combo is a no-op.
        try:
            gh.remove_hotkeys(combo_strings)
        except Exception:  # noqa: BLE001 — best-effort cleanup of stale state
            log.debug("Pre-register cleanup of stale hotkeys skipped",
                      exc_info=True)

        # Guard registration: an unrecoverable failure (invalid combo, internal
        # global_hotkeys error) must NOT propagate — that would crash the whole
        # voice pipeline at ``async with HotkeyTrigger(...)``. Degrade instead.
        try:
            gh.register_hotkeys(list(bindings))
        except Exception:  # noqa: BLE001 — degrade, never crash the pipeline
            log.error(
                "register_hotkeys failed — hotkeys disabled for this session; "
                "voice still works via wake word / mascot click.",
                exc_info=True,
            )
            self._gh = None
            return

        self._gh = gh
        self._registered = list(bindings)
        self._combo_strings = combo_strings

    def start(self) -> None:
        """Start the single shared checker (only on a successful register)."""
        if self._gh is None or self._started:
            return
        _start_checker_once(self._gh)  # only on a successful registration
        self._started = True

    def stop(self) -> None:
        """Stop the shared checker on the 1->0 boundary. Idempotent."""
        if self._gh is None or not self._started:
            return
        _stop_checker_once(self._gh)
        self._started = False

    def unregister(self) -> None:
        """Remove every armed combo by STRING (never the binding rows)."""
        gh = self._gh
        if gh is None:
            self._registered = []
            self._combo_strings = []
            return
        if self._combo_strings:
            try:
                # Correct format: a list of combo STRINGS (NOT the binding
                # rows). Passing the rows is the bug that left stale state.
                gh.remove_hotkeys(self._combo_strings)
            except Exception:  # noqa: BLE001 — teardown must never propagate
                log.debug("remove_hotkeys during teardown failed (non-fatal)",
                          exc_info=True)
        self._registered = []
        self._combo_strings = []
        self._gh = None

    def received_any_event(self) -> bool:
        """Windows fires reliably once registered — no zero-event ambiguity.

        Unlike macOS (where a missing Input-Monitoring grant yields a silent
        "registered but zero events" state), a successful ``register_hotkeys`` on
        Windows means the checker will fire, so this hook is moot here and simply
        reports whether the backend is live.
        """
        return self._gh is not None and self._started


__all__ = [
    "GlobalHotkeysBackend",
    "_KEY_MAP",
    "_normalize_combo",
    "_start_checker_once",
    "_stop_checker_once",
    "_reset_checker_state_for_tests",
    "_CHECKER_LOCK",
]
