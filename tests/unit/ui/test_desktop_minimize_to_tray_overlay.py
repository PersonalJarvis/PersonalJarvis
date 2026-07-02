"""Closing the window (X) = QUIT EVERYTHING; MINIMISE keeps Jarvis running.

User mandate (2026-07-01): the X on the main window must tear the WHOLE app
down — tray icon, JarvisBar overlay, voice pipeline, backend server, child
processes and the process itself — not merely hide to tray. To keep Jarvis
alive in the background (so "Hey Jarvis" stays live) the user MINIMISES the
window instead of closing it.

Contract:
- ``_on_window_closing`` marks the quit and returns True → pywebview destroys
  the window → ``run_window_only`` runs ``shutdown()`` (tears every surface
  down) and then a hard-exit backstop that guarantees the process dies even if
  teardown wedges.
- ``_suppress_overlay_for_hidden_window`` / ``_restore_overlay_for_visible_window``
  still govern the bar's persistence regime when the window is HIDDEN/SHOWN via
  the tray "Open" or a focus request — ``bar_persistent`` (the "show bar at all
  times" choice) is honoured, never overridden. Both no-op on a headless host.
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


def test_suppress_is_noop_for_persistent_bar() -> None:
    """'Show at all times' wins: minimising the window must NOT clear an
    always-on bar (the 'bar vanishes after a while / only the wake word brings
    it back' regression). The live regime stays persistent, untouched."""
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    app = _app(persistent=True, orb=bar, bridge=bridge)

    app._suppress_overlay_for_hidden_window()

    assert bar.hidden is False  # always-on bar stays on screen
    assert bridge._hide_on_idle is False  # regime untouched
    assert bar._persistent is True
    assert app.cfg.ui.bar_persistent is True


def test_suppress_hides_non_persistent_bar() -> None:
    """A non-persistent bar (hide-at-idle) is taken off the screen on minimise
    so the desktop is clean. Only this regime is suppressed."""
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    app = _app(persistent=False, orb=bar, bridge=bridge)

    app._suppress_overlay_for_hidden_window()

    assert bar.hidden is True
    assert bridge._hide_on_idle is True
    assert bar._persistent is False


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
    governs the jarvis bar). Restoring must not pin the mascot on screen."""
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=True)
    app = _app(persistent=True, orb=bar, bridge=bridge)
    app.cfg.ui.orb_style = "mascot"

    app._restore_overlay_for_visible_window()

    assert bridge._hide_on_idle is True  # stays hide-at-idle
    assert bar.shown is None  # not force-shown


# --- the X / closing callback = FULL QUIT ------------------------------------


def test_window_closing_quits_and_does_not_minimise() -> None:
    """User mandate: the X fully quits — it no longer hides to tray. It marks
    the quit and allows the destroy; ``shutdown()`` (run by ``run_window_only``)
    tears every surface — tray, bar, voice, server — down afterwards."""
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    app = _app(persistent=True, orb=bar, bridge=bridge)
    app._window = FakeWindow()
    app._user_requested_quit = False
    app._window_visible = True

    result = app._on_window_closing()

    assert result is True  # destroy allowed → full shutdown, not minimise
    assert app._user_requested_quit is True
    assert app._window.hidden is False  # NOT hidden — the window is destroyed
    # The bar is torn down in shutdown(), never on the closing callback itself.
    assert bar.hidden is False


def test_window_closing_quits_regardless_of_bar_persistence() -> None:
    """A non-persistent bar quits exactly the same way — closing always quits."""
    bar = FakeBar()
    bridge = SimpleNamespace(_hide_on_idle=False)
    app = _app(persistent=False, orb=bar, bridge=bridge)
    app._window = FakeWindow()
    app._user_requested_quit = False
    app._window_visible = True

    result = app._on_window_closing()

    assert result is True
    assert app._user_requested_quit is True
    assert app._window.hidden is False


def test_window_closing_allows_genuine_quit() -> None:
    """An already-marked quit (tray 'Quit' / restart) still returns True."""
    bar = FakeBar()
    app = _app(persistent=True, orb=bar, bridge=SimpleNamespace(_hide_on_idle=False))
    app._window = FakeWindow()
    app._user_requested_quit = True
    app._window_visible = True

    result = app._on_window_closing()

    assert result is True  # allow the destroy → shutdown() handles teardown
    assert app._window.hidden is False  # not minimised
    assert bar.hidden is False  # suppress NOT run on a real quit
