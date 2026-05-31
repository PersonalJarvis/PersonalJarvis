"""FakeHotkeyBackend — drop-in stand-in for a ``HotkeyBackend`` (AD-6 seam).

Convention (CLAUDE.md): fakes over mocks. This fake satisfies the
``jarvis.trigger.backends.HotkeyBackend`` ``Protocol`` and records the lifecycle
calls so a test can prove ``HotkeyTrigger`` drives the backend correctly
(register → start → stop → unregister) without touching any OS or optional
package. ``fire``/``fire_press``/``fire_release`` simulate chord actuation and
invoke the registered handlers, but only while the backend is "started" —
mirroring the real backends, where callbacks only fire while listening.

The binding rows are the ``[normalized_combo, on_press, on_release]`` shape the
real backends receive from ``HotkeyTrigger``.
"""

from __future__ import annotations

from collections.abc import Callable


def _norm(combo: str) -> str:
    """Whitespace-insensitive combo key, matching the global_hotkeys contract."""
    return combo.replace(" ", "")


class FakeHotkeyBackend:
    """Stateful, OS-free ``HotkeyBackend`` for unit tests."""

    def __init__(self) -> None:
        self.registered: dict[str, tuple[Callable[[], None] | None, Callable[[], None] | None]] = {}
        self.register_calls: list[list] = []
        self.unregister_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self._started = False
        self._got_event = False

    # ------------------------------------------------------------------
    # HotkeyBackend protocol surface
    # ------------------------------------------------------------------
    def register(self, bindings, on_event=None) -> None:
        self.register_calls.append(list(bindings))
        for row in bindings:
            combo = _norm(row[0])
            on_press = row[1] if len(row) > 1 else None
            on_release = row[2] if len(row) > 2 else None
            self.registered[combo] = (on_press, on_release)

    def unregister(self) -> None:
        self.unregister_calls += 1
        self.registered.clear()

    def start(self) -> None:
        self.start_calls += 1
        self._started = True

    def stop(self) -> None:
        self.stop_calls += 1
        self._started = False

    def received_any_event(self) -> bool:
        return self._got_event

    # ------------------------------------------------------------------
    # Test helpers (not part of the protocol)
    # ------------------------------------------------------------------
    @property
    def started(self) -> bool:
        return self._started

    def fire(self, combo: str) -> None:
        """Actuate ``combo`` (release edge) while started."""
        self.fire_release(combo)

    def fire_press(self, combo: str) -> None:
        if not self._started:
            return
        handlers = self.registered.get(_norm(combo))
        if handlers and handlers[0] is not None:
            self._got_event = True
            handlers[0]()

    def fire_release(self, combo: str) -> None:
        if not self._started:
            return
        handlers = self.registered.get(_norm(combo))
        if handlers and handlers[1] is not None:
            self._got_event = True
            handlers[1]()
