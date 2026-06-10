"""FakeGlobalHotkeys — drop-in stand-in for the ``global_hotkeys`` module.

Convention (CLAUDE.md): fakes over mocks. The real ``global_hotkeys`` package
registers Windows-wide low-level keyboard hooks via ``win32api`` and spawns an
OS polling thread — neither of which can run on a headless Linux CI box (the
cloud-first VPS doctrine). This fake replicates the *observable contract* of
the module-level singleton so ``HotkeyTrigger`` is fully unit-testable without
touching the OS:

* ``register_hotkeys`` raises "already registered" on a duplicate combo —
  exactly like the real ``HotkeyChecker.register_hotkey`` does. This is the
  failure mode that bricked every hotkey after an in-process pipeline restart.
* ``remove_hotkeys`` expects a list of combo **strings** and runs
  ``combo.replace(" ", "")`` internally. Passing the full
  ``[combo, on_press, on_release]`` rows (the historical bug) raises
  ``AttributeError`` here just as it does in the real package.
* ``start_checking_hotkeys`` / ``stop_checking_hotkeys`` track how many checker
  loops are live so a test can prove there is never a duplicate-thread
  double-fire.

``fire(combo)`` simulates a key-chord actuation and invokes the registered
on-release handler, but only while a checker loop is running — mirroring the
real package where callbacks only fire while ``start_checking_hotkeys`` is
active.
"""
from __future__ import annotations

from collections.abc import Callable


def _norm(combo: str) -> str:
    """Mirror the real module: combos are compared with whitespace stripped."""
    return combo.replace(" ", "")


class FakeGlobalHotkeys:
    """Stateful stand-in for the ``global_hotkeys`` singleton module."""

    def __init__(self) -> None:
        self.registered: dict[str, tuple[Callable[[], None] | None, Callable[[], None] | None]] = {}
        self.register_calls: list[list] = []
        self.remove_calls: list[list] = []
        self.start_calls = 0
        self.stop_calls = 0
        self._live = 0          # currently running checker loops
        self.peak_live = 0      # high-water mark; >1 means a duplicate-thread bug
        # Test knob: when set, register_hotkeys raises this to simulate an
        # unrecoverable registration failure (e.g. an invalid combo).
        self.register_error: Exception | None = None
        # Test knob: combos (whitespace-stripped) that should raise on
        # registration, mirroring the real package raising on an unknown key
        # name — used to prove a single bad combo does NOT disable the rest.
        self.register_error_combos: set[str] = set()

    # ------------------------------------------------------------------
    # Real API surface (what HotkeyTrigger imports as ``gh``)
    # ------------------------------------------------------------------
    def register_hotkeys(self, bindings: list[list]) -> None:
        self.register_calls.append(bindings)
        if self.register_error is not None:
            raise self.register_error
        for row in bindings:
            combo = _norm(row[0])
            on_press = row[1] if len(row) > 1 else None
            on_release = row[2] if len(row) > 2 else None
            if combo in self.register_error_combos:
                # Faithful to global_hotkeys: an unknown key name raises
                # "not a valid virtual keystroke" mid-registration.
                raise Exception(f"The key in [{combo}] is not a valid virtual keystroke.")
            if combo in self.registered:
                # Faithful to global_hotkeys.HotkeyChecker.register_hotkey.
                raise Exception(f"The hotkey [{combo}] is already registered.")
            self.registered[combo] = (on_press, on_release)

    def remove_hotkeys(self, bindings: list[str]) -> None:
        self.remove_calls.append(bindings)
        for _binding in bindings:
            # Faithful to the real module: ``_binding.replace(" ", "")``.
            # A list row (the historical bug) raises AttributeError here.
            combo = _norm(_binding)
            self.registered.pop(combo, None)

    def start_checking_hotkeys(self) -> None:
        self.start_calls += 1
        self._live += 1
        self.peak_live = max(self.peak_live, self._live)

    def stop_checking_hotkeys(self) -> None:
        self.stop_calls += 1
        self._live = max(0, self._live - 1)

    def clear_hotkeys(self) -> None:
        # Real clear_hotkeys also stops the checker thread.
        self.registered.clear()
        self._live = 0

    # ------------------------------------------------------------------
    # Test helpers (not part of the real module)
    # ------------------------------------------------------------------
    @property
    def checker_running(self) -> bool:
        return self._live > 0

    def fire(self, combo: str) -> None:
        """Simulate pressing + releasing ``combo``; invoke its on-release
        handler — but only while a checker loop is live (matches reality)."""
        if not self.checker_running:
            return
        handlers = self.registered.get(_norm(combo))
        if handlers is None:
            return
        on_release = handlers[1]
        if on_release is not None:
            on_release()

    def fire_press(self, combo: str) -> None:
        """Simulate the *down* edge of ``combo`` — invokes its on-press
        handler only. Used to test push-to-talk, where the press starts the
        recording and the release (``fire_release``) submits it."""
        if not self.checker_running:
            return
        handlers = self.registered.get(_norm(combo))
        if handlers is None:
            return
        on_press = handlers[0]
        if on_press is not None:
            on_press()

    def fire_release(self, combo: str) -> None:
        """Simulate the *up* edge of ``combo`` — invokes its on-release
        handler only (same effect as :meth:`fire`, named for symmetry with
        :meth:`fire_press` in push-to-talk tests)."""
        if not self.checker_running:
            return
        handlers = self.registered.get(_norm(combo))
        if handlers is None:
            return
        on_release = handlers[1]
        if on_release is not None:
            on_release()
