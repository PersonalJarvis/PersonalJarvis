"""Desktop boot keeps the Jarvis Bar hidden until voice is usable.

Regression history: an earlier ready-gated implementation only withdrew the Tk
window initially. Its reveal could lose z-order, so the bar appeared only when a
wake event happened to show it again. The emergency fix then mapped persistent
bars immediately, which advertised voice while the app still said "starting".

The current contract separates initialization from visibility: boot creates a
fully painted, startup-gated bar; the bus bridge releases that gate directly on
the genuine ``VoiceBootStatus`` event. Runtime builds remain immediate.
"""

from __future__ import annotations

from types import SimpleNamespace

import jarvis.ui.desktop_app as desktop_app_module
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


def _disable_real_tk(monkeypatch) -> None:
    # These tests exercise the in-process Tk lifecycle specifically. The real
    # macOS desktop selects SubprocessBarOverlay; its mirror/gate contract is
    # covered in test_subprocess_overlay.py and the Qt host-selection tests.
    monkeypatch.setattr(desktop_app_module.sys, "platform", "linux")
    monkeypatch.setattr(JarvisBarOverlay, "start_in_thread", lambda self, timeout=3.0: None)


def test_boot_persistent_bar_is_initialized_but_startup_gated(monkeypatch) -> None:
    _disable_real_tk(monkeypatch)

    surface = _app(bar_persistent=True)._build_overlay_surface(
        "jarvis_bar", gate_until_voice_ready=True
    )

    assert isinstance(surface, JarvisBarOverlay)
    assert surface._persistent is True
    assert surface._startup_gated is True
    assert surface._should_start_withdrawn() is True


def test_runtime_persistent_bar_keeps_immediate_visibility(monkeypatch) -> None:
    _disable_real_tk(monkeypatch)

    surface = _app(bar_persistent=True)._build_overlay_surface("jarvis_bar")

    assert surface._startup_gated is False
    assert surface._should_start_withdrawn() is False


def test_boot_non_persistent_bar_releases_to_normal_session_behavior(monkeypatch) -> None:
    _disable_real_tk(monkeypatch)

    surface = _app(bar_persistent=False)._build_overlay_surface(
        "jarvis_bar", gate_until_voice_ready=True
    )

    assert surface._startup_gated is True
    assert surface._should_start_withdrawn() is True
    assert surface.release_startup_gate() is True
    # Non-persistent bars remain withdrawn while idle and pop on a real session.
    assert surface._should_start_withdrawn() is True
