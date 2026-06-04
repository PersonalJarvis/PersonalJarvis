"""Hand-built overlay fakes for the Wave-2 orb seam (EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. These let the
``OverlaySurface`` lifecycle, the factory selection, and the tray state-mapping be
exercised on a headless Windows/CI box **without** creating a real ``tk.Tk()``
window or spinning up a real pystray thread:

* :class:`FakeOrb` — duck-typed ``ui.orb.overlay.OrbOverlay``: it records
  ``start_in_thread`` / ``show`` / ``hide`` / ``set_mode`` calls so a test can
  assert ``TkColorKeyOverlay`` delegates and maps states correctly.
* :class:`FakeTray` — duck-typed ``jarvis.ui.tray.JarvisTray``: records the
  ``JarvisState`` values passed to ``set_state`` so a test can assert
  ``TrayOnlySurface`` maps orb states onto the tray enum.
* :class:`FakeOverlaySurface` — a generic ``OverlaySurface`` implementation
  recording its own lifecycle, for tests that need an injected inner surface.
"""

from __future__ import annotations


class FakeOrb:
    """Structurally compatible with ``ui.orb.overlay.OrbOverlay`` (the live Tk orb).

    Only the methods :class:`~jarvis.overlay.surface.TkColorKeyOverlay` drives are
    implemented: ``start_in_thread``, ``show``, ``hide``, ``set_mode``. Every call
    is recorded for assertions; nothing touches Tk.
    """

    def __init__(self) -> None:
        self.started = False
        self.visible = False
        self.mode: str | None = None
        self.shown_modes: list[str] = []
        self.hide_calls = 0
        self.set_mode_calls: list[str] = []

    def start_in_thread(self, auto_demo: bool = False, timeout: float = 3.0) -> None:  # noqa: ARG002
        self.started = True

    def show(self, mode: str = "listen") -> None:
        self.visible = True
        self.mode = mode
        self.shown_modes.append(mode)

    def hide(self) -> None:
        self.visible = False
        self.hide_calls += 1

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.set_mode_calls.append(mode)


class FakeTray:
    """Structurally compatible with ``jarvis.ui.tray.JarvisTray``.

    Records the ``JarvisState`` values passed to ``set_state`` (so a test can
    assert the orb-state → tray-state mapping) and the start/stop lifecycle.
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.states: list[object] = []  # JarvisState values, in order.

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def set_state(self, state: object) -> None:
        self.states.append(state)

    @property
    def last_state(self) -> object | None:
        return self.states[-1] if self.states else None


class FakeOverlaySurface:
    """A generic ``OverlaySurface`` that records its own lifecycle.

    Useful as an injected inner surface (e.g. into ``LinuxBestEffortOverlay``) so
    a test can assert the wrapper forwards ``start``/``stop``/``set_state`` and
    reflects ``is_visible``.
    """

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.states: list[str] = []
        self._visible = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True
        self._visible = False

    def set_state(self, state: str) -> None:
        self.states.append(state)
        self._visible = state in ("listening", "thinking", "speaking")

    def is_visible(self) -> bool:
        return self._visible


__all__ = ["FakeOrb", "FakeTray", "FakeOverlaySurface"]
