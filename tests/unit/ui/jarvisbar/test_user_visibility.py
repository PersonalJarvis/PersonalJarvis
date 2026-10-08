"""User dismissal survives voice updates, startup gates and host respawns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import PetVisibilityToggleRequested
from jarvis.ui.jarvisbar import host, qt_overlay
from jarvis.ui.jarvisbar.modes import MODES
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessBarOverlay
from ui.orb.bus_bridge import OrbBusBridge


class _Window:
    def __init__(self) -> None:
        self.visible = True
        self.reveals = 0

    def winfo_ismapped(self) -> bool:
        return self.visible

    def deiconify(self) -> None:
        self.visible = True
        self.reveals += 1

    show = deiconify

    def withdraw(self) -> None:
        self.visible = False

    hide = withdraw

    def wm_attributes(self, *args: object) -> None:
        pass

    def update_idletasks(self) -> None:
        pass


@pytest.fixture(params=[JarvisBarOverlay, qt_overlay.QtJarvisBarOverlay])
def surface(request, monkeypatch):
    bar = request.param(follow_cursor_monitor=False)
    window = _Window()
    if isinstance(bar, JarvisBarOverlay):
        bar._root = window
        monkeypatch.setattr(bar, "_enqueue_ui", lambda fn: fn())
        monkeypatch.setattr(bar, "_do_reassert_z_order", lambda **kwargs: None)
    else:
        bar._window = window
        monkeypatch.setattr(bar, "_enqueue_if_started", lambda fn: fn())
        for name in (
            "_reconcile_dynamic_position_ui", "_render_frame_ui",
            "_exclude_from_capture_ui", "_raise_ui",
        ):
            monkeypatch.setattr(bar, name, lambda: None)
        monkeypatch.setattr(qt_overlay, "_qt", lambda: SimpleNamespace(
            Qt=SimpleNamespace(MouseButton=SimpleNamespace(RightButton=2)),
            QtCore=SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda *args: None)),
        ))
    return bar, window


def _right_click(bar) -> None:
    if isinstance(bar, JarvisBarOverlay):
        bar._on_right_click()
    else:
        assert bar._mouse_press_ui(SimpleNamespace(button=lambda: 2))


def test_right_click_stays_hidden_through_every_mode_until_shortcut(surface) -> None:
    bar, window = surface
    _right_click(bar)
    assert not window.visible
    for mode in MODES:
        bar.show(mode)
        assert not window.visible
        assert bar._mode == mode
    assert window.reveals == 0
    bar.toggle_visible()
    assert window.visible
    bar.toggle_visible()
    assert not window.visible


def test_explicit_restore_also_shows_a_nonpersistent_idle_bar(surface) -> None:
    bar, window = surface
    bar._persistent = False
    bar.set_visible(False)
    bar.show("idle")
    bar.toggle_visible()
    assert window.visible
    assert bar._mode == "idle"


def test_dismissal_and_restoration_respect_the_startup_gate(surface) -> None:
    bar, window = surface
    bar._startup_gated = True
    bar.set_visible(False)
    bar.show("listen")
    bar.toggle_visible()
    assert not window.visible
    bar.set_visible(False)
    bar.release_startup_gate()
    assert not window.visible
    bar.toggle_visible()
    assert window.visible


async def test_existing_global_shortcut_restores_the_real_bar_surface(surface) -> None:
    bar, window = surface
    bus = EventBus()
    bridge = OrbBusBridge(bus, bar)
    bridge.attach()
    _right_click(bar)
    await bus.publish(PetVisibilityToggleRequested(source="hotkey"))
    assert window.visible
    await bus.publish(PetVisibilityToggleRequested(source="hotkey"))
    assert not window.visible


def test_host_roundtrip_preserves_dismissal_on_respawn(monkeypatch) -> None:
    proxy = SubprocessBarOverlay()
    bar = JarvisBarOverlay()
    monkeypatch.setattr(host, "emit", lambda event, **kw: proxy._dispatch_event(
        {"event": event, **kw},
    ))
    host._wire_surface_events(bar)
    monkeypatch.setattr(proxy, "_send", lambda msg: host.dispatch(bar, msg))
    bar._on_right_click()
    proxy.show("speak")
    cfg = proxy._init_payload()
    assert cfg["user_hidden"] is True
    rebuilt = host._build_surface(cfg)
    assert rebuilt._user_hidden is True
    host._wire_surface_events(rebuilt)
    monkeypatch.setattr(proxy, "_send", lambda msg: host.dispatch(rebuilt, msg))
    proxy._reapply_desired_state()
    assert rebuilt._user_hidden is True
    proxy.toggle_visible()
    assert rebuilt._user_hidden is False
    assert proxy._init_payload()["user_hidden"] is False
    assert rebuilt._mode == "speak"


@pytest.mark.parametrize("cls", [JarvisBarOverlay, qt_overlay.QtJarvisBarOverlay])
def test_visibility_is_safe_before_start_and_with_failing_observer(cls) -> None:
    bar = cls(user_hidden=True)

    def fail(visible: bool) -> None:
        raise RuntimeError("observer unavailable")

    bar.set_on_visibility_changed(fail)
    bar.toggle_visible()
    assert not bar._user_hidden
    bar.set_visible(False)
    assert bar._user_hidden
