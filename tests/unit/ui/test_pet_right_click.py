"""The pet context menu offers dismissal without hiding on the opening click."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import PetVisibilityToggleRequested
from jarvis.ui.jarvisbar import host
from jarvis.ui.jarvisbar.modes import MODES
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessMascotOverlay
from jarvis.ui.pets import context_menu
from ui.orb import overlay, pet_context_menu
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

    def winfo_pointerxy(self) -> tuple[int, int]:
        return (100, 200)


class _Menu:
    def __init__(self, root, **kwargs) -> None:
        self.commands = []
        self.position = None
        self.released = False
        self.destroyed = False

    def add_command(self, **kwargs) -> None:
        self.commands.append(kwargs)

    def add_separator(self) -> None:
        pass

    def tk_popup(self, x, y) -> None:
        self.position = (x, y)

    def grab_release(self) -> None:
        self.released = True

    def destroy(self) -> None:
        self.destroyed = True

    def choose(self, label) -> None:
        next(row for row in self.commands if row["label"] == label)["command"]()


@pytest.fixture
def pet(monkeypatch):
    monkeypatch.setattr(pet_context_menu, "PetContextMenu", _Menu)
    monkeypatch.setattr(context_menu, "preferences", lambda: (
        context_menu.LABELS["en"], "Alt+Win+P",
    ))
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


def test_right_click_opens_menu_then_hide_dismisses_figure_strip_and_cards(pet) -> None:
    opened = []
    pet.set_on_show_window(lambda: opened.append(True))
    pet._on_right_click(SimpleNamespace(x_root=-400, y_root=300))
    assert not pet.pet_user_hidden
    assert pet._root.visible
    assert pet._context_menu.position == (-400, 300)
    assert pet._context_menu.released
    assert [row["label"] for row in pet._context_menu.commands] == [
        "Open app", "Reset position", "Hide pet",
    ]
    assert pet._context_menu.commands[-1]["accelerator"] == "Alt+Win+P"
    pet._context_menu.choose("Hide pet")
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
    pet._context_menu.choose("Hide pet")
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


def test_strip_right_click_opens_the_same_menu_without_hiding(pet) -> None:
    strip = PetControlStrip.__new__(PetControlStrip)
    strip._on_context = pet._on_right_click
    strip._on_context_event(None)
    assert not pet.pet_user_hidden
    assert pet._context_menu.position == (100, 200)
    pet._context_menu.choose("Hide pet")
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
    assert not proxy.pet_user_hidden
    pet._context_menu.choose("Hide pet")
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


def test_right_click_without_a_window_does_not_hide_the_pet() -> None:
    pet = OrbOverlay(style="pet")
    pet._on_right_click()
    assert not pet.pet_user_hidden


def test_menu_hide_survives_a_failed_observer(pet) -> None:

    def fail(visible):
        raise RuntimeError("observer unavailable")

    pet.set_on_visibility_changed(fail)
    pet._on_right_click()
    pet._context_menu.choose("Hide pet")
    assert pet.pet_user_hidden


def test_open_and_reset_actions_do_not_hide_the_pet(pet, monkeypatch) -> None:
    calls = []
    pet.set_on_show_window(lambda: calls.append("open"))
    monkeypatch.setattr(pet, "_on_reset_double_click", lambda: calls.append("reset"))
    pet._on_right_click()
    pet._context_menu.choose("Open app")
    pet._context_menu.choose("Reset position")
    assert calls == ["open", "reset"]
    assert not pet.pet_user_hidden


def test_repeated_right_click_replaces_the_previous_menu(pet) -> None:
    pet._on_right_click()
    previous = pet._context_menu
    pet._on_right_click()
    assert previous.destroyed
    assert pet._context_menu is not previous
    assert not pet.pet_user_hidden


def test_hiding_pet_from_another_action_also_dismisses_its_popup(pet) -> None:
    pet._on_right_click()
    menu = pet._context_menu
    pet.set_visible(False)
    assert menu.destroyed
    assert pet.pet_user_hidden


def test_failed_popup_releases_grab_without_hiding_pet(pet, monkeypatch, caplog) -> None:
    def fail(menu, x, y):
        raise overlay.tk.TclError("window unavailable")

    monkeypatch.setattr(_Menu, "tk_popup", fail)
    pet._on_right_click()
    assert pet._context_menu.released
    assert not pet.pet_user_hidden
    assert "Pet context menu could not be opened" in caplog.text


@pytest.mark.parametrize("language", context_menu.LABELS)
def test_menu_reads_current_language_and_explicit_shortcut(
    tmp_path, monkeypatch, language
) -> None:
    config = tmp_path / "menu.toml"
    config.write_text(f'[ui]\nlanguage="{language}"\n[trigger]\nhotkey_pet_toggle="ctrl+alt+p"')
    # The shortcut is spelled with each OS's own key names.
    monkeypatch.setattr(context_menu.sys, "platform", "win32")
    labels, shortcut = context_menu.preferences(config)
    assert labels == context_menu.LABELS[language]
    assert shortcut == "Ctrl+Alt+P"
    monkeypatch.setattr(context_menu.sys, "platform", "darwin")
    assert context_menu.preferences(config)[1] == "Control+Option+P"
    config.write_text('[trigger]\nhotkey_pet_toggle=""')
    assert context_menu.preferences(config)[1] == ""
