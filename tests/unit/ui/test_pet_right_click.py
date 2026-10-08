"""The pet's right-click hides the whole surface until its global shortcut."""

from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import PetVisibilityToggleRequested
from jarvis.ui.jarvisbar import host
from jarvis.ui.jarvisbar.modes import MODES
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessMascotOverlay
from ui.orb.bus_bridge import OrbBusBridge
from ui.orb.overlay import OrbOverlay, PetControlStrip


class _Window:
    def __init__(self) -> None:
        self.visible = True

    def withdraw(self) -> None:
        self.visible = False

    hide = withdraw

    def deiconify(self) -> None:
        self.visible = True

    def winfo_ismapped(self) -> bool:
        return self.visible

    def lift(self) -> None:
        pass


@pytest.fixture
def pet(monkeypatch):
    surface = OrbOverlay(style="pet", pet_id="ember")
    surface._mac_transparent = False
    surface._root = _Window()
    surface._controls = _Window()
    surface._notices = _Window()
    surface._thought = _Window()
    monkeypatch.setattr(surface, "_enqueue_ui", lambda fn: fn())
    monkeypatch.setattr(surface, "_ensure_frame_loop", lambda: None)
    monkeypatch.setattr(surface, "_reassert_topmost", lambda **kwargs: None)
    monkeypatch.setattr(surface, "_sync_controls_visibility", lambda: None)
    return surface


def test_right_click_hides_figure_strip_and_cards_without_opening_window(pet) -> None:
    opened = []
    pet.set_on_show_window(lambda: opened.append(True))
    pet._on_right_click()
    assert pet.pet_user_hidden
    assert not opened
    assert all(not window.visible for window in (
        pet._root, pet._controls, pet._notices, pet._thought,
    ))


async def test_voice_updates_stay_hidden_until_existing_hotkey_event(pet) -> None:
    bus = EventBus()
    bridge = OrbBusBridge(bus, pet)
    bridge.attach()
    pet._on_right_click()
    for mode in MODES:
        pet.show(mode)
        assert not pet._root.visible
        assert pet._mode == mode
    await bus.publish(PetVisibilityToggleRequested(source="hotkey"))
    assert pet._root.visible
    assert not pet.pet_user_hidden
    await bus.publish(PetVisibilityToggleRequested(source="hotkey"))
    assert not pet._root.visible
    assert pet.pet_user_hidden


def test_strip_right_click_uses_the_same_pet_dismissal(pet) -> None:
    strip = PetControlStrip.__new__(PetControlStrip)
    strip._on_context = pet._on_right_click
    strip._on_context_event(None)
    assert pet.pet_user_hidden
    assert not pet._root.visible


def test_host_reports_right_click_and_restores_hidden_pet_after_respawn(pet, monkeypatch) -> None:
    proxy = SubprocessMascotOverlay(style="pet")
    monkeypatch.setattr(host, "emit", lambda event, **kw: proxy._dispatch_event(
        {"event": event, **kw},
    ))
    monkeypatch.setattr(proxy, "_send", lambda msg: host.dispatch(pet, msg))
    host._wire_surface_events(pet)
    pet._on_right_click()
    assert proxy.pet_user_hidden
    replay = []
    monkeypatch.setattr(proxy, "_send", replay.append)
    proxy._reapply_desired_state()
    assert {"op": "set_visible", "visible": False} in replay
    monkeypatch.setattr(proxy, "_send", lambda msg: host.dispatch(pet, msg))
    proxy.toggle_visible()
    assert not proxy.pet_user_hidden
    assert not pet.pet_user_hidden
    assert pet._root.visible


def test_right_click_is_safe_without_window_or_with_failed_observer() -> None:
    pet = OrbOverlay(style="pet")

    def fail(visible):
        raise RuntimeError("observer unavailable")

    pet.set_on_visibility_changed(fail)
    pet._on_right_click()
    assert pet.pet_user_hidden
