"""The persistent JarvisBar is visible immediately at boot.

Regression: the boot wiring used to build the persistent bar WITHDRAWN
(``start_hidden=True``) and reveal it only once voice was ready
(``VoiceBootStatus(ready=True)``). On the serve-first fast-boot path that reveal
was late and/or lost the topmost z-order, so the bar stayed hidden until the
first wake word incidentally re-showed it. The bar's boot visibility must be
decoupled from the voice/wake path: a persistent bar maps its window at boot,
always; a non-persistent bar still pops on a real session.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from jarvis.ui.desktop_app import DesktopApp
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


def _app(*, bar_persistent: bool) -> DesktopApp:
    app = DesktopApp.__new__(DesktopApp)  # bypass heavy __init__
    app.cfg = SimpleNamespace(
        ui=SimpleNamespace(
            orb_style="jarvis_bar",
            bar_persistent=bar_persistent,
            bar_accent="#e7c46e",
            orb_mascot_path="",
        )
    )
    return app


def test_boot_persistent_bar_is_visible_not_withdrawn(monkeypatch) -> None:
    # Don't spawn a real Tk window in the unit test.
    monkeypatch.setattr(
        JarvisBarOverlay, "start_in_thread", lambda self, timeout=3.0: None
    )
    surface = _app(bar_persistent=True)._build_overlay_surface("jarvis_bar")

    assert isinstance(surface, JarvisBarOverlay)
    assert surface._persistent is True
    # Visible at boot — NOT gated behind VoiceBootStatus(ready=True) / a wake word.
    assert surface._should_start_withdrawn() is False
    assert "start_hidden" not in inspect.signature(JarvisBarOverlay).parameters
    assert "start_hidden" not in inspect.signature(
        DesktopApp._build_overlay_surface
    ).parameters


def test_boot_non_persistent_bar_still_starts_withdrawn(monkeypatch) -> None:
    monkeypatch.setattr(
        JarvisBarOverlay, "start_in_thread", lambda self, timeout=3.0: None
    )
    surface = _app(bar_persistent=False)._build_overlay_surface("jarvis_bar")

    # A non-persistent bar still pops on a real session (unchanged).
    assert surface._should_start_withdrawn() is True
