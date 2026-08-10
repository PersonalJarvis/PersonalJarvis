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

import contextlib
import logging
import threading
import time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mouse buttons as shortcut keys (maintainer directive 2026-07-28).
#
# These three tokens are the shared vocabulary for EVERY backend — the Windows
# poller, the macOS event tap and the Linux pynput listener all translate them
# to their own primitive. They live here because this module already owns the
# canonical combo vocabulary (``_KEY_MAP`` / ``_normalize_combo``); one
# definition, three consumers, no hand-mirrored copy to drift (AP-4).
#
# Left and right button are deliberately NOT offered. Their meaning follows the
# system "swap mouse buttons" setting, so a shortcut recorded as "left" would
# fire on the physical right button for a left-handed user — and binding the
# primary click globally makes the machine unusable in the first place.
# ---------------------------------------------------------------------------
MOUSE_BUTTON_TOKENS = frozenset({"mouse_middle", "mouse_x1", "mouse_x2"})

# Friendlier spellings a hand-written jarvis.toml may use. X1/X2 are what the
# hardware calls the two side buttons; Back/Forward is what they do in a
# browser.
_MOUSE_ALIASES = {
    "mouse_back": "mouse_x1",
    "mouse_forward": "mouse_x2",
    "middle_mouse": "mouse_middle",
}

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
    # Same physical key, other names: X11 and most Linux desktops call it
    # Super, some configs spell it Meta. Folding them here means a config that
    # spells the key either way normalizes to the one token every backend
    # matches, instead of arming a combo that never fires.
    "super": "window",
    "meta": "window",
    "shift": "shift",
    **_MOUSE_ALIASES,
}

# The Windows virtual-key codes for the three buttons, as the numeric tokens
# ``global_hotkeys`` accepts (its ``_to_virtualkey`` parses a hex string when the
# name is not one of its own, and its matcher's only primitive is
# ``GetAsyncKeyState``, which reports mouse buttons). Applied at the library
# boundary only — everything above it keeps the readable token, so a log line
# and a jarvis.toml stay legible on every OS.
_MOUSE_TOKEN_TO_VK = {
    "mouse_middle": "0x04",  # VK_MBUTTON
    "mouse_x1": "0x05",      # VK_XBUTTON1 (Back)
    "mouse_x2": "0x06",      # VK_XBUTTON2 (Forward)
}


def _canonical_token(token: str) -> str:
    """Fold an alias spelling onto its canonical token (``mouse_back`` -> x1)."""
    return _MOUSE_ALIASES.get(token, token)


def _to_library_combo(normalized: str) -> str:
    """Swap the friendly mouse tokens for the numeric VK the library wants."""
    parts = [p.strip() for p in normalized.split("+") if p.strip()]
    return " + ".join(_MOUSE_TOKEN_TO_VK.get(p, p) for p in parts)

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


# --------------------------------------------------------------------------
# Registry-mutation guard.
#
# ``HotkeyChecker.run`` binds ``id_list = self.hotkeys.keys()`` ONCE and then
# iterates that LIVE dict view every 20 ms for the life of the thread. Any
# register/remove from another thread therefore raises
# ``RuntimeError: dictionary changed size during iteration`` INSIDE the poller,
# which kills it — and with it EVERY global hotkey for the rest of the process
# (live 2026-08-10 18:39:29: arming and releasing the Computer-Use cancel key
# while the voice trigger's checker was polling killed the poller, so the
# Escape kill-switch and the call/dictation shortcuts were silently dead from
# then on).
#
# The library offers no lock, so the only correct sequence is: pause the
# poller, let it leave its loop, mutate, restart. The pause is bounded by
# ``_CHECKER_SETTLE_S`` — a fraction of a second in which no hotkey fires,
# which is invisible next to losing them entirely.
# --------------------------------------------------------------------------

#: Poller loop period is 20 ms; this is comfortably more than one pass, so the
#: old thread is provably gone before the registry is touched (which also rules
#: out two live pollers double-firing a press across the restart).
_CHECKER_SETTLE_S = 0.06

#: Serializes our own registry writes against each other. Re-entrant: a nested
#: guard (register -> pre-remove) must not deadlock.
_MUTATION_LOCK = threading.RLock()


@contextlib.contextmanager
def _registry_mutation(gh):
    """Hold the shared checker still while the hotkey registry is written."""
    with _MUTATION_LOCK:
        paused = False
        with _CHECKER_LOCK:
            if _CHECKER_REFCOUNT > 0:
                try:
                    gh.stop_checking_hotkeys()
                    paused = True
                except Exception:  # noqa: BLE001 — never block the mutation
                    log.debug("Could not pause the hotkey checker", exc_info=True)
        if paused:
            time.sleep(_CHECKER_SETTLE_S)
        try:
            yield
        finally:
            if paused:
                with _CHECKER_LOCK:
                    # Only restart while someone still wants the checker — a
                    # stop() during the mutation must not resurrect it.
                    if _CHECKER_REFCOUNT > 0:
                        try:
                            gh.start_checking_hotkeys()
                        except Exception:  # noqa: BLE001
                            log.error(
                                "Hotkey checker could not be restarted after a "
                                "registry change — shortcuts are dead until the "
                                "next re-arm.",
                                exc_info=True,
                            )


def _normalize_combo(combo: str) -> str:
    """`ctrl+right_alt+j` -> `control + alt + j`."""
    parts = [p.strip().lower() for p in combo.split("+")]
    return " + ".join(_KEY_MAP.get(p, p) for p in parts)


def _registration_combos(normalized: str) -> list[str]:
    """Every chord that has to be armed so ``normalized`` can actually fire.

    Windows reports MORE modifier keys as held than the user physically chose,
    and the library's matcher refuses a chord while any modifier it does not
    itself name is down (``non_allowed_modifiers`` in ``HotkeyChecker.run``).
    Two keys are affected, and both produced a shortcut that saved, displayed
    in the UI, and then never fired once — the "I picked my keys and nothing
    happens" report:

    * **AltGr.** On a German, French, Nordic, Polish, Brazilian (…) layout the
      keyboard driver raises ``VK_CONTROL`` alongside ``VK_MENU`` whenever
      AltGr goes down. A combo the user recorded as ``right_alt+j`` folds to
      ``alt + j``, which does not name Ctrl — so the phantom Ctrl the driver
      injected disqualified the chord on every single press. The shipped
      defaults all happened to spell an explicit ``ctrl``, which is why this
      never showed up in the shortcuts we ourselves ship.
    * **The right Ctrl key.** It raises the generic ``VK_CONTROL`` as well as
      ``VK_RCONTROL``, and ``right_control`` alone names only the specific one.

    They need different repairs, because only one of them is ambiguous:

    * ``right_control`` is a CORRECTION — the generic Ctrl is always up when
      the right Ctrl is down, so ``control + right_control + …`` is simply the
      truthful chord. It still fires exclusively on the right-hand key, since
      the left one never raises ``VK_RCONTROL``.
    * ``alt`` is a VARIANT. By the time a combo reaches this module, every Alt
      spelling has folded onto the one token the library knows, so we can no
      longer tell "the user chose AltGr" from "the user chose the left Alt" —
      and the two produce different physical key states. So both chords are
      armed. They are mutually exclusive by construction: the plain one is
      refused while Ctrl is down, the Ctrl-carrying one requires it, so a press
      matches exactly one of them and a handler never runs twice.

    Returns the primary chord first; anything after it is a compatibility
    variant the caller registers in a second pass, so a chord some other action
    chose EXPLICITLY always wins the registration.
    """
    tokens = [p.strip() for p in normalized.split("+") if p.strip()]
    if "right_control" in tokens and "control" not in tokens:
        tokens = ["control", *tokens]
    combos = [" + ".join(tokens)]
    if "alt" in tokens and "control" not in tokens:
        combos.append(" + ".join(["control", *tokens]))
    return combos


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

        # The rows arrive with the shared vocabulary (``mouse_x2``); the library
        # speaks virtual-key numbers for the mouse. Translate ONCE here, and use
        # the translated strings for registration AND removal — the two must be
        # byte-identical or ``remove_hotkeys`` leaves a stale registration that
        # bricks the next re-entry.
        #
        # ``_registration_combos`` may hand back a SECOND chord for the same
        # binding (the AltGr repair — see its docstring). Those go in their own
        # list and are registered afterwards, so a chord another action chose
        # explicitly always claims the registration ahead of a compatibility
        # variant.
        primary: list[list] = []
        compat: list[list] = []
        for row in bindings:
            variants = _registration_combos(row[0])
            primary.append([_to_library_combo(variants[0]), *row[1:]])
            compat.extend([_to_library_combo(v), *row[1:]] for v in variants[1:])
        bindings = primary
        combo_strings = [row[0] for row in primary + compat]

        # Idempotent (re-)registration. A previous lifecycle in this process
        # may have left these combos in the shared singleton (historically the
        # broken __aexit__ never cleaned them up). Pre-removing makes re-entry
        # safe — otherwise register_hotkeys raises "already registered" and
        # EVERY hotkey dies. Removing an unregistered combo is a no-op. Done
        # per-combo so one un-removable combo cannot abort the cleanup of the
        # others (the real remove_hotkeys raises on an unknown key mid-list).
        #
        # Every write below happens under ``_registry_mutation``: a second
        # trigger's poller may be running right now, and the library iterates a
        # LIVE view of the very dict these calls resize (see the guard).
        registered_rows: list[list] = []
        registered_strings: list[str] = []
        with _registry_mutation(gh):
            for cs in combo_strings:
                try:
                    gh.remove_hotkeys([cs])
                except Exception:  # noqa: BLE001 — best-effort cleanup of stale state
                    log.debug("Pre-register cleanup of %r skipped", cs, exc_info=True)

            # Register each binding INDIVIDUALLY. The old single
            # ``register_hotkeys(all)`` was all-or-nothing: one unknown key name
            # (e.g. a numpad combo emitted under the wrong token) raised and took
            # EVERY hotkey down with it — the "I set a key and now nothing works"
            # report. Now a bad combo is skipped and logged; the rest stay live. An
            # exception must never propagate — it would crash the whole voice
            # pipeline at ``async with HotkeyTrigger(...)`` — so we degrade instead.
            for row in bindings:
                try:
                    gh.register_hotkeys([row])
                except Exception:  # noqa: BLE001 — skip the bad combo, keep the rest
                    log.error(
                        "Hotkey combo %r could not be registered (unknown key / "
                        "invalid combo) — skipping it; the other hotkeys stay active.",
                        row[0],
                        exc_info=True,
                    )
                    continue
                registered_rows.append(row)
                registered_strings.append(row[0])

            # Second pass: the compatibility chords. Skipping one is normal, not a
            # fault — it means the same chord is already armed, either because
            # another action named it outright or because two bindings produced the
            # same variant. The library keys its registry off the token ORDER, so a
            # hand-written jarvis.toml could spell an existing chord differently and
            # slip past its duplicate check; comparing the token SETS ourselves is
            # what keeps that from arming one physical press twice.
            taken = {
                frozenset(c.replace(" ", "").split("+")) for c in registered_strings
            }
            for row in compat:
                keys = frozenset(row[0].replace(" ", "").split("+"))
                if keys in taken:
                    continue
                try:
                    gh.register_hotkeys([row])
                except Exception:  # noqa: BLE001 — the primary chord is what matters
                    log.debug(
                        "Compatibility chord %r not armed (already registered).",
                        row[0],
                        exc_info=True,
                    )
                    continue
                taken.add(keys)
                registered_rows.append(row)
                registered_strings.append(row[0])

        if not registered_rows:
            # Package present but NOT ONE combo registered — degrade like a hard
            # failure so the checker never starts on an empty registration;
            # voice still works via wake word / mascot click.
            log.error(
                "No hotkey combo could be registered — hotkeys disabled for "
                "this session; voice still works via wake word / mascot click."
            )
            self._gh = None
            return

        self._gh = gh
        self._registered = registered_rows
        self._combo_strings = registered_strings

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
            with _registry_mutation(gh):
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
    "MOUSE_BUTTON_TOKENS",
    "GlobalHotkeysBackend",
    "_KEY_MAP",
    "_canonical_token",
    "_normalize_combo",
    "_to_library_combo",
    "_start_checker_once",
    "_stop_checker_once",
    "_reset_checker_state_for_tests",
    "_registry_mutation",
    "_CHECKER_LOCK",
    "_CHECKER_SETTLE_S",
]
