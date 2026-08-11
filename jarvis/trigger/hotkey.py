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
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

# Relocated Windows machinery (AD-7). Imported here so callers that historically
# did ``from jarvis.trigger.hotkey import _normalize_combo`` keep working, and so
# the test-isolation hooks remain reachable at this module path. None of these
# import ``global_hotkeys`` at module scope — that import is lazy inside the
# backend's ``register`` (HN-7).
from jarvis.platform.self_input import synthetic_input_recent
from jarvis.trigger.backends import HotkeyBackend, make_hotkey_backend
from jarvis.trigger.backends import global_hotkeys as _gh_backend
from jarvis.trigger.backends.global_hotkeys import (
    _KEY_MAP,  # noqa: F401 — re-exported for backwards compatibility
    MOUSE_BUTTON_TOKENS,
    _canonical_token,
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


# Shortcut events that Jarvis's OWN synthetic keystrokes must not fire.
#
# A listener cannot tell a posted keystroke from a pressed one, so every combo
# is armed against our own actuation. On macOS that is not a corner case: the
# platform paste chord is ``cmd+v`` and the offered hold-to-dictate key is a
# bare ``cmd``, so every dictation ended by pasting its own transcript and
# tripping its own shortcut (live log 2026-08-09; the lane was still finishing,
# refused the phantom press as ``already_running``, and the Jarvis Bar wore the
# failure mark over a dictation that had pasted perfectly). During a
# Computer-Use mission the same press is worse than cosmetic: nothing is
# running to refuse it, so it STARTS a recording and takes the microphone.
#
# A DENY list, deliberately, not an allow list: everything unnamed keeps
# firing. These are exactly the edges that START something and cost nothing to
# miss for a quarter second. Every STOP gesture — ``kill`` (the kill switch),
# ``cu_cancel``, ``hangup`` — and every ``*_release`` edge is absent on
# purpose, because a suppressed release strands the latch it was meant to
# clear, and a suppressed kill switch is indefensible at any window length.
SELF_INPUT_SUPPRESSED_EVENTS: frozenset[str] = frozenset(
    {
        "ptt_press",
        "dictate_press",
        "dictate",          # legacy [dictation].mode = "toggle"
        "dictate_toggle",
        "paste_last",
    }
)

# Tokens that are modifiers, not "real" keys. A hotkey made of ONLY modifiers
# is not a usable trigger; a usable combo needs at least one real key.
_MODIFIER_TOKENS = frozenset(
    {
        "ctrl", "control", "right_ctrl", "right_control",
        "alt", "right_alt", "left_alt", "altgr",
        "shift", "win", "window",
        # macOS Command. The Quartz backend has always decoded it
        # (``_FLAG_MASK_TO_TOKEN`` maps ``kCGEventFlagMaskCommand`` -> ``cmd``),
        # but it was missing here, so the validator treated Command as an
        # ordinary KEY. Consequence: every macOS-critical chord passed —
        # ``cmd+q``, ``cmd+w``, ``cmd+c``, ``cmd+space`` were all accepted while
        # their Windows equivalents were refused, and a user could bind "quit
        # the focused app" as their dictation key.
        "cmd", "command", "meta", "super",
    }
)

# Keys that are safe to bind SOLO (no modifier, no second key): function keys
# are never produced while typing text, and the navigation cluster only fires
# during cursor navigation — a deliberate user choice, not an accident. Bare
# letters / digits / space / enter stay rejected: those fire on every
# keystroke of normal typing and would make the assistant trigger constantly.
_SOLO_SAFE_KEYS = frozenset({f"f{i}" for i in range(1, 25)}) | frozenset(
    {
        "up", "down", "left", "right",
        "home", "end", "page_up", "page_down",
        "insert", "delete",
    }
)


# Keys the OS keeps for itself no matter what the user wants.
#   * F12 is permanently reserved for the debugger — Microsoft documents that it
#     must not be registered as a hot key even when nothing is being debugged.
_RESERVED_SOLO_KEYS = frozenset({"f12"})

# Command-modified chords macOS assigns to universal system actions. Binding one
# means the focused app keeps doing that action too (Cmd+Q quits it, Cmd+W closes
# its window, Cmd+Space opens Spotlight) — worth a caution, never a refusal.
_MACOS_CRITICAL_KEYS = frozenset({"c", "v", "x", "z", "q", "w", "a", "s", "space", "tab"})

# The Windows-shell equivalent of the list above. It did not exist while
# Win-key combos were refused outright; now that they are allowed, a user who
# binds Win+D deserves the same warning a Mac user gets for Cmd+W — the shell
# keeps showing the desktop on top of triggering the shortcut.
_WINDOWS_CRITICAL_KEYS = frozenset(
    {"d", "e", "l", "r", "x", "i", "s", "v", "a", "p", "tab", "left", "right", "up", "down"}
)


@dataclass(frozen=True)
class HotkeyVerdict:
    """The result of :func:`validate_hotkey`: a verdict plus soft cautions.

    Why this shape and not a three-element tuple: every existing caller does
    ``ok, reason = validate_hotkey(...)``. Appending a third element to the
    tuple would turn each of them into a ``ValueError: too many values to
    unpack`` at runtime — a silent-in-review, loud-in-production break across
    the settings route, the setup wizard and four test modules. A small object
    that ITERATES as the historical two-tuple keeps every one of those call
    sites byte-identical, while ``verdict.cautions`` is there for the UI that
    wants it. It is opt-in by construction: a caller who never asks for a
    caution cannot accidentally receive one in place of the reason.

    ``ok`` is False only for input that means nothing at all (an empty combo).
    Everything the validator used to refuse — a modifiers-only chord, a bare
    typing key, an OS-critical shortcut — is now ACCEPTED with a caution,
    because the user, not the validator, owns the keyboard.
    """

    ok: bool
    reason: str = ""
    cautions: tuple[str, ...] = ()

    @property
    def caution(self) -> str:
        """Every caution as one user-facing sentence block (``""`` when none)."""
        return " ".join(self.cautions)

    def __bool__(self) -> bool:
        return self.ok

    def __iter__(self) -> Iterator[Any]:
        # The historical ``(ok, reason)`` contract — see the class docstring.
        yield self.ok
        yield self.reason

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> Any:
        return (self.ok, self.reason)[index]


def validate_hotkey(combo: str, *, platform: str | None = None) -> HotkeyVerdict:
    """Validate a user-supplied hotkey string (call / hangup / dictate).

    Returns a :class:`HotkeyVerdict` that still unpacks as the historical
    ``(ok, reason)`` pair. The policy (maintainer directive 2026-07-28): **any
    key combination is selectable**, mouse buttons included. The validator
    refuses only what is genuinely meaningless — an empty combo — and reports
    everything else as a NON-BLOCKING caution the UI can show next to the
    saved shortcut:

      * a modifiers-only chord (``ctrl+win``) also fires when any further key
        joins it, so it triggers on Ctrl+Win+Left, Ctrl+Win+D and friends;
      * a bare typing key fires while you type ordinary text;
      * an OS-critical combo (Alt+F4, Ctrl+C, the macOS Command shortcuts,
        F12) is still delivered to the focused app, which keeps doing its
        thing on top of the shortcut;
      * a Command chord on a machine that has no Command key cannot fire
        there — honest, and harmless for a config that travels to a Mac;
      * a mouse button does not swallow the click.

    The two refusals that used to live here — "a combo of only Ctrl/Alt/Shift
    cannot be a trigger" and "Windows / Super key combos are reserved by the
    OS" — are gone because both were untrue for this codebase: the Windows
    backend polls ``GetAsyncKeyState`` (it never calls ``RegisterHotKey``, so
    the shell cannot claim the chord first), and Win/Super/Command are
    first-class tokens in all three backends. What they cost the user was
    real; what they protected against is a caution, not a wall.

    ``platform`` (``"win32"`` / ``"darwin"`` / ``"linux"``) picks the
    platform-specific cautions; it defaults to the live platform and exists so
    tests are deterministic. The combo vocabulary itself stays
    platform-neutral: a config file may legitimately travel between a Mac and
    a PC.
    """
    if not combo or not combo.strip():
        return HotkeyVerdict(False, "Hotkey is empty.")
    parts = [_canonical_token(p.strip().lower()) for p in combo.split("+") if p.strip()]
    if not parts:
        return HotkeyVerdict(False, "Hotkey is empty.")

    modifiers = [p for p in parts if p in _MODIFIER_TOKENS]
    non_modifiers = [p for p in parts if p not in _MODIFIER_TOKENS]
    mouse_buttons = [p for p in parts if p in MOUSE_BUTTON_TOKENS]
    cautions: list[str] = []

    if not non_modifiers:
        cautions.append(
            "This shortcut is modifier keys only, so it also fires the moment "
            "any other key joins them — Ctrl+Win would trigger on Ctrl+Win+Left "
            "(switch virtual desktop), Ctrl+Win+D and every other Ctrl+Win "
            "shortcut too."
        )
    elif not modifiers and len(parts) < 2 and parts[0] not in _SOLO_SAFE_KEYS and not mouse_buttons:
        cautions.append(
            "This key also fires while you type normal text — every press of it "
            "triggers the shortcut. Add Ctrl/Alt/Shift or a second key if that "
            "is not what you want."
        )

    reserved = sorted(set(non_modifiers) & _RESERVED_SOLO_KEYS)
    if reserved:
        cautions.append(
            f"{reserved[0].upper()} is claimed by the system debugger — some "
            "applications will take it before the shortcut sees it."
        )

    _CTRL = ("ctrl", "control", "right_ctrl", "right_control")
    _ALT = ("alt", "right_alt", "left_alt", "altgr")
    _CMD = ("cmd", "command", "meta", "super")
    _WIN = ("win", "window", "super", "meta")
    alt_held = any(p in _ALT for p in modifiers)
    # "X-only" means X-family modifiers and nothing else — so the exact OS
    # shortcut is cautioned while a richer combo that merely contains it (e.g.
    # Ctrl+Shift+C) stays silent.
    ctrl_only = bool(modifiers) and all(p in _CTRL for p in modifiers)
    cmd_only = bool(modifiers) and all(p in _CMD for p in modifiers)
    win_only = bool(modifiers) and all(p in _WIN for p in modifiers)
    if alt_held and "f4" in non_modifiers:
        cautions.append(_os_shortcut_caution("Alt+F4", "closes the active window"))
    if ctrl_only and non_modifiers == ["c"]:
        cautions.append(_os_shortcut_caution("Ctrl+C", "copies, and interrupts a terminal"))
    if win_only and len(non_modifiers) == 1 and non_modifiers[0] in _WINDOWS_CRITICAL_KEYS:
        cautions.append(
            _os_shortcut_caution(
                f"Win+{non_modifiers[0].upper()}",
                "is a system shortcut on Windows (show the desktop, lock the "
                "screen, the emoji picker and friends)",
            )
        )
    if cmd_only and len(non_modifiers) == 1 and non_modifiers[0] in _MACOS_CRITICAL_KEYS:
        cautions.append(
            _os_shortcut_caution(
                f"Command+{non_modifiers[0].upper()}",
                "is a system shortcut on macOS (copy, quit, close, Spotlight "
                "and friends)",
            )
        )

    if any(p in ("cmd", "command") for p in modifiers):
        host = platform if platform is not None else _detect_platform()
        if host not in (None, "darwin"):
            cautions.append(
                "There is no Command key on this computer, so this shortcut "
                "cannot fire here. (It works on macOS — a shortcut set on a "
                "Mac keeps working when the config travels back.)"
            )

    if mouse_buttons:
        cautions.append(
            "A mouse button does not swallow the click: the button keeps doing "
            "its normal job in whatever you are pointing at while it also "
            "triggers this shortcut."
        )

    return HotkeyVerdict(True, "", tuple(cautions))


def _os_shortcut_caution(pretty: str, does_what: str) -> str:
    """One caution sentence for a combo the focused app also acts on."""
    return (
        f"{pretty} {does_what} — the app you are working in still receives it, "
        "so you get both at once."
    )


def _detect_platform() -> str | None:
    """The live platform id, or ``None`` when it cannot be determined.

    Lazy + fail-open: a hotkey the validator cannot place is better accepted
    (the backend then degrades honestly, per-combo) than rejected on a guess.
    """
    try:
        from jarvis.platform import detect_platform

        return detect_platform()
    except Exception:  # noqa: BLE001 — validation must never depend on a probe
        return None


def normalized_combo_tokens(combo: str) -> frozenset[str]:
    """The key set a combo ACTUALLY registers as, after normalization.

    ``ctrl+right_alt+j`` and ``ctrl+left_alt+j`` are different strings and the
    identical registration: the Windows backend folds every Alt variant onto
    the generic ``alt`` (it cannot tell the sides apart), and win/super/meta
    all fold onto ``window``. Any comparison of two shortcuts has to happen on
    THIS set — comparing the raw tokens accepts two combos the OS sees as one.
    """
    return frozenset(
        p.strip() for p in _normalize_combo(combo).split("+") if p.strip()
    )


def combos_collide(a: str, b: str) -> bool:
    """Whether two shortcuts cannot coexist — normalized, not raw.

    The bug this closes: the settings route compared RAW token sets, so
    ``ctrl+left_alt+j`` and ``ctrl+right_alt+j`` passed the overlap check as
    two different shortcuts and then normalized to the SAME registration. The
    second one lost the race inside ``register`` and died with a log line
    nobody sees, leaving the user with two bound-looking rows, one of them
    silently dead.

    Two relations collide, both evaluated on the normalized key sets:

    * identical sets — the same registration under two spellings;
    * subset / superset — the polling backends match a chord as soon as its
      keys are down, so ``f1`` and ``f1+f2`` fire each other.

    An unbound (empty) combo never collides: an empty set is a subset of
    everything, and every save would be refused the moment one action is
    cleared.
    """
    tokens_a = normalized_combo_tokens(a)
    tokens_b = normalized_combo_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    return tokens_a <= tokens_b or tokens_b <= tokens_a


def mouse_hotkeys_available(platform: str | None = None) -> tuple[bool, str]:
    """Can this host bind a MOUSE BUTTON as a shortcut? ``(ok, reason)``.

    A capability probe, never a platform name test (AP-21/AP-23). ``reason``
    is an English sentence for the user when the answer is no, naming what is
    missing and what still works — the UI shows it instead of offering a
    control that would do nothing:

    * Windows — always: the backend polls ``GetAsyncKeyState``, which reports
      mouse buttons alongside keys.
    * macOS — needs pyobjc's ``Quartz`` (the event tap already exists; it just
      listens for mouse events too now) plus the Accessibility / Input
      Monitoring grants the backend already checks at ``start``.
    * Linux/X11 — needs ``pynput`` (the opt-in ``[desktop-linux]`` extra).
      Wayland has no global button grabs at all, the same reason keyboard
      shortcuts degrade there.

    Kept import-light and lazy so it never runs on the boot path (AP-26).
    """
    host = platform if platform is not None else _detect_platform()
    if host == "win32":
        return True, ""
    if host == "darwin":
        if _module_present("Quartz"):
            return True, ""
        return False, (
            "Mouse-button shortcuts need the pyobjc Quartz package, which is "
            "not installed — install the [full] profile. Key combinations "
            "still work."
        )
    if host == "linux":
        try:
            from jarvis.platform.probes import is_wayland

            wayland = is_wayland()
        except Exception:  # noqa: BLE001 — a probe failure must never hard-fail
            wayland = False
        if wayland:
            return False, (
                "Wayland does not let an application watch the mouse buttons "
                "globally, so a mouse-button shortcut cannot work here. Use a "
                "key combination, or bind a compositor shortcut to the Jarvis "
                "CLI."
            )
        if _module_present("pynput"):
            return True, ""
        return False, (
            "Mouse-button shortcuts need the pynput package, which is not "
            "installed — install the [desktop-linux] extra. Key combinations "
            "still work."
        )
    return False, (
        "This system has no way to watch the mouse buttons globally — use a "
        "key combination instead."
    )


def _module_present(name: str) -> bool:
    """Is ``name`` importable, without importing it (cheap, AP-26)."""
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001 — a broken meta-path must not raise here
        return False


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
            if event_name in SELF_INPUT_SUPPRESSED_EVENTS and synthetic_input_recent():
                log.debug(
                    "Hotkey %r ignored: Jarvis is synthesizing input right now.",
                    event_name,
                )
                return
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

    async def rearm(
        self,
        bindings: dict[str, list[str]],
        push_to_talk: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        """Re-register the live bindings in place — a keybind change WITHOUT an
        app restart.

        Root cause of "I set a key but pressing it does nothing": the bindings
        were armed once at ``__aenter__`` and never re-read, so a UI/toml change
        only took effect on the next voice boot. This mirrors the ``set_wake_plan``
        live-apply contract: tear the backend's OS registration down and bring it
        back up with the new combos, reusing the exact lifecycle calls
        ``__aenter__``/``__aexit__`` use (so the relocated remove-by-string +
        refcount invariants are preserved). The single shared checker is stopped
        then restarted, keeping the refcount balanced (one stop, one start).

        Degrade-safe (AD-OE6): a failed re-arm logs and leaves hotkeys inactive
        rather than propagating — a keybind hiccup must never crash voice.
        """
        self._bindings_cfg = bindings
        self._ptt_events = frozenset(push_to_talk)
        backend = self._backend
        if backend is None:
            return  # entered degraded (no package) — nothing to re-arm

        new_bindings, combo_strings = self._build_bindings()
        try:
            backend.stop()
            backend.unregister()
            backend.register(new_bindings)
            if (
                type(backend).__name__ == "GlobalHotkeysBackend"
                and getattr(backend, "_gh", None) is None
            ):
                # Every new combo failed to register: leave it degraded but do
                # not start an empty checker.
                self._registered = []
                self._combo_strings = []
                log.warning("Hotkey re-arm degraded — no combo could be registered.")
                return
            backend.start()
            self._registered = new_bindings
            self._combo_strings = combo_strings
            log.info(
                "🔁 Hotkey-Live-Reload — re-armed: %s",
                ", ".join(
                    f"{name}=[{', '.join(combos)}]"
                    for name, combos in self._bindings_cfg.items()
                ),
            )
        except Exception:  # noqa: BLE001 — never crash voice on a re-arm hiccup
            log.error(
                "Hotkey re-arm failed — hotkeys may be inactive until the next "
                "restart; voice still works via wake word / mascot click.",
                exc_info=True,
            )

    async def events(self) -> AsyncIterator[str]:
        """Yield event names ("call" / "hangup" / ...) on every press."""
        while True:
            name = await self._queue.get()
            yield name


__all__ = [
    "MOUSE_BUTTON_TOKENS",
    "SELF_INPUT_SUPPRESSED_EVENTS",
    "HotkeyTrigger",
    "HotkeyVerdict",
    "combos_collide",
    "mouse_hotkeys_available",
    "normalized_combo_tokens",
    "validate_hotkey",
    "_normalize_combo",
    "_reset_checker_state_for_tests",
]
