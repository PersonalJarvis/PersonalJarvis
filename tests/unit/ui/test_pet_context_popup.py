"""Branded popup geometry, keyboard/mouse selection and focus/grab cleanup."""

from types import SimpleNamespace

import pytest
from PIL import ImageColor

from jarvis.platform import monitors
from jarvis.ui.pets import context_menu, menu_renderer
from jarvis.ui.theme import POPOVER_COLORS
from ui.orb import pet_context_menu as popup


class _Top:
    def __init__(self, parent):
        self.parent = parent
        self.grabbed = False
        self.focus = self
        self.pending = None
        self.cancelled = []

    def bind(self, *args):
        pass

    def grab_set(self):
        self.grabbed = True

    def grab_current(self):
        return self if self.grabbed else None

    def grab_release(self):
        self.grabbed = False

    def focus_force(self):
        pass

    def focus_displayof(self):
        return self.focus

    def winfo_toplevel(self):
        return self

    def after_idle(self, fn):
        self.pending = fn
        return "focus-check"

    def after_cancel(self, identifier):
        self.cancelled.append(identifier)

    def wait_window(self):
        self.parent.on_wait()

    def winfo_rootx(self):
        return self.x

    def winfo_rooty(self):
        return self.y


class _Surface:
    def __init__(self, parent, size, **kwargs):
        self.top = _Top(parent)
        self.canvas = SimpleNamespace(focus_set=lambda: None)
        self.frames = []
        self.destroyed = False
        parent.surface = self

    def bind(self, *args):
        pass

    def move(self, x, y):
        self.top.x, self.top.y = x, y

    def present(self, frame):
        self.frames.append(frame)

    def show(self):
        pass

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def menu(monkeypatch):
    monkeypatch.setattr(popup, "AlphaWindow", _Surface)
    monkeypatch.setattr(popup, "appearance", lambda: "dark")
    monkeypatch.setattr(monitors, "work_area_at", lambda x, y: (-1920, 0, 1920, 1080))
    parent = SimpleNamespace(on_wait=lambda: None)
    panel = popup.PetContextMenu(parent, scale=1.5)
    calls = []
    panel.add_command(label="Open app", command=lambda: calls.append("open"), state="disabled")
    panel.add_command(label="Reset position", command=lambda: calls.append("reset"))
    panel.add_separator()
    panel.add_command(
        label="Hide pet",
        accelerator="Alt+Win+P",
        command=lambda: calls.append(("hide", parent.surface.destroyed)),
    )
    return panel, parent, calls


def test_keyboard_skips_disabled_rows_and_separator_and_closes_before_action(menu):
    panel, parent, calls = menu

    def interact():
        for expected in (1, 3, 1):
            panel._key(SimpleNamespace(keysym="Down"))
            assert panel.selected == expected
        panel._key(SimpleNamespace(keysym="End"))
        panel._key(SimpleNamespace(keysym="Return"))
        panel._activate(3)

    parent.on_wait = interact
    panel.tk_popup(-100, 1000)
    assert calls == [("hide", True)]
    assert not parent.surface.top.grabbed
    assert panel.surface is None


def test_outside_click_dismisses_without_running_a_command(menu):
    panel, parent, calls = menu
    parent.on_wait = lambda: panel._click(SimpleNamespace(x_root=300, y_root=300))
    panel.tk_popup(-200, 200)
    assert parent.surface.destroyed
    assert not parent.surface.top.grabbed
    assert calls == []


def test_mouse_hit_testing_uses_scaled_global_coordinates(menu):
    panel, parent, calls = menu

    def interact():
        top = parent.surface.top
        event = SimpleNamespace(x_root=top.x + 50 * panel.scale, y_root=top.y + 100 * panel.scale)
        panel._motion(event)
        assert panel.selected == 3
        panel._click(event)

    parent.on_wait = interact
    panel.tk_popup(-800, 200)
    assert calls == [("hide", True)]


@pytest.mark.parametrize("key", ["Escape", "Tab"])
def test_cancel_keys_never_invoke_an_action(menu, key):
    panel, parent, calls = menu
    parent.on_wait = lambda: panel._key(SimpleNamespace(keysym=key))
    panel.tk_popup(-800, 200)
    assert parent.surface.destroyed
    assert calls == []


def test_focus_loss_closes_and_cancels_pending_idle_callback(menu):
    panel, parent, calls = menu

    def interact():
        panel._focus_out(None)
        top = parent.surface.top
        top.pending()
        assert panel.surface is not None  # A child receiving focus keeps the popup.
        panel._focus_out(None)
        top.focus = None
        top.pending()
        assert parent.surface.destroyed

    parent.on_wait = interact
    panel.tk_popup(-800, 200)
    assert not parent.surface.top.grabbed
    assert calls == []


def test_paint_failure_releases_all_popup_resources(menu, monkeypatch):
    panel, parent, calls = menu

    def fail():
        raise RuntimeError("paint failed")

    monkeypatch.setattr(panel, "_paint", fail)
    with pytest.raises(RuntimeError, match="paint failed"):
        panel.tk_popup(-800, 200)
    assert parent.surface.destroyed
    assert panel.surface is None


def test_popup_clamps_to_secondary_monitor_work_area():
    assert popup.popup_position(-10, 1070, (360, 198), (-1920, 0, 1920, 1080)) == (-368, 874)


def test_unsupported_transparency_uses_neutral_corners(menu, monkeypatch):
    panel, parent, _ = menu
    monkeypatch.setattr(popup.sys, "platform", "linux")
    panel.tk_popup(-800, 200)
    assert parent.surface.frames[0].getpixel((0, 0)) == (28, 28, 28, 255)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_actual_renderer_uses_app_tokens_and_transparent_corners(theme):
    entries = [
        menu_renderer.MenuEntry("Open app"),
        menu_renderer.MenuEntry("Reset position", icon="reset"),
        None,
        menu_renderer.MenuEntry("Hide pet", "Alt+Win+P", icon="hide"),
    ]
    frame = menu_renderer.render_menu(entries, theme, selected=1)
    assert frame.size == menu_renderer.menu_size(entries)
    assert frame.getpixel((0, 0))[3] == 0
    assert frame.getpixel((120, 5))[:3] == ImageColor.getrgb(POPOVER_COLORS[theme]["background"])
    assert frame.getpixel((120, 47))[:3] == ImageColor.getrgb(POPOVER_COLORS[theme]["hover"])


def test_current_appearance_follows_settings_and_system_mode(tmp_path, monkeypatch):
    path = tmp_path / "menu.toml"
    path.write_text('[ui]\ntheme="light"')
    assert context_menu.appearance(path) == "light"
    path.write_text('[ui]\ntheme="system"')
    monkeypatch.setattr("jarvis.ui.theme.detect_os_theme", lambda: "dark")
    assert context_menu.appearance(path) == "dark"
