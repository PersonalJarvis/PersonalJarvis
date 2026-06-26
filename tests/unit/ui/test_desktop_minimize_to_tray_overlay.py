"""Closing the window (X) = minimise to tray AND clear the overlay bar.

The X on the main window is wired to "minimise to tray", not "quit" (the app
keeps running so voice stays live). Previously the persistent whisper-bar stayed
on screen after the X, which read as "the bar won't go away when I close Jarvis".

Contract:
- ``_on_window_closing`` vetoes the destroy (returns False), hides the window,
  and takes the bar off the screen too — without destroying the user's saved
  ``bar_persistent`` preference, so reopening restores it.
- a genuine quit (``_user_requested_quit``) is allowed through untouched; the
  real ``shutdown()`` tears the bar down there.
- ``_restore_overlay_for_visible_window`` puts the bar back into the user's
  configured persistence regime when the window is shown again.
- both helpers no-op on a headless host (no overlay / no bridge).
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.ui.desktop_app import DesktopApp


class FakeBar:
    def __init__(self) -> None:
        self._persistent = True
        self._mode = "idle"
        self.shown: str | None = None
        self.hidden = False

    def show(self, mode: str) -> None:
        self.shown = mode

    def hide(self) -> None:
        self.hidden = True


class FakeWindow:
    def __init__(self) -> None:
        self.hidden = False

    def hide(self) -> None:
        self.hidden = True


def _app(*, persistent: bool, orb: object, bridge: object) -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)
    app.cfg = SimpleNamespace(ui=SimpleNamespace(bar_persistent=persistent))
    app._orb = orb
    app._bridge = bridge
    return app


# --- suppress (window hidden) ------------------------------------------------


def test_suppress_before_boot_reveal_does_not_cancel_startup_bar() -> None:
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False, _boot_reveal_done=False)
    app = _app(persistent=True, orb=bar, bridge=bridge)

    app._suppress_overlay_for_hidden_window()

    assert bar.hidden is False
    assert bridge._hide_on_idle is False
    assert bar._persistent is True
    assert app.cfg.ui.bar_persistent is True


def test_suppress_hides_bar_and_flips_bridge_to_hide_on_idle() -> None:
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    app = _app(persistent=True, orb=bar, bridge=bridge)

    app._suppress_overlay_for_hidden_window()

    assert bar.hidden is True
    assert bridge._hide_on_idle is True
    assert bar._persistent is False
    # The user's saved preference must NOT be mutated — only the live regime.
    assert app.cfg.ui.bar_persistent is True


def test_suppress_is_noop_without_overlay() -> None:
    app = _app(persistent=True, orb=None, bridge=None)
    # Must not raise on a headless host.
    app._suppress_overlay_for_hidden_window()


# --- restore (window shown again) --------------------------------------------


def test_restore_shows_idle_bar_for_persistent_user() -> None:
    bar = FakeBar()
    bar._persistent = False  # left in the suppressed regime
    bridge = SimpleNamespace(_hide_on_idle=True)
    app = _app(persistent=True, orb=bar, bridge=bridge)

    app._restore_overlay_for_visible_window()

    assert bridge._hide_on_idle is False
    assert bar._persistent is True
    assert bar.shown == "idle"


def test_restore_keeps_bar_hidden_for_non_persistent_user() -> None:
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=True)
    app = _app(persistent=False, orb=bar, bridge=bridge)

    app._restore_overlay_for_visible_window()

    # Non-persistent: hide-at-idle regime stays, bar is not force-shown.
    assert bridge._hide_on_idle is True
    assert bar.shown is None


def test_restore_is_noop_without_overlay() -> None:
    app = _app(persistent=True, orb=None, bridge=None)
    app._restore_overlay_for_visible_window()


def test_restore_keeps_mascot_hide_on_idle() -> None:
    """The mascot is hide-at-idle regardless of bar_persistent (which only
    governs the whisper bar). Restoring must not pin the mascot on screen."""
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=True)
    app = _app(persistent=True, orb=bar, bridge=bridge)
    app.cfg.ui.orb_style = "mascot"

    app._restore_overlay_for_visible_window()

    assert bridge._hide_on_idle is True  # stays hide-at-idle
    assert bar.shown is None  # not force-shown


# --- the X / closing callback ------------------------------------------------


def test_window_closing_minimises_and_clears_bar() -> None:
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    app = _app(persistent=True, orb=bar, bridge=bridge)
    app._window = FakeWindow()
    app._user_requested_quit = False
    app._window_visible = True

    result = app._on_window_closing()

    assert result is False  # destroy vetoed → minimise to tray
    assert app._window.hidden is True
    assert app._window_visible is False
    assert bar.hidden is True
    assert bridge._hide_on_idle is True
    assert app.cfg.ui.bar_persistent is True  # preference untouched


def test_window_closing_allows_genuine_quit() -> None:
    bar = FakeBar()
    app = _app(persistent=True, orb=bar, bridge=SimpleNamespace(_hide_on_idle=False))
    app._window = FakeWindow()
    app._user_requested_quit = True
    app._window_visible = True

    result = app._on_window_closing()

    assert result is True  # allow the destroy → shutdown() handles teardown
    assert app._window.hidden is False  # not minimised
    assert bar.hidden is False  # suppress NOT run on a real quit
