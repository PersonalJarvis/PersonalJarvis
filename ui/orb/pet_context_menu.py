"""A small keyboard-accessible pet popup using the app's native RGBA surface."""

from __future__ import annotations

import logging
import sys
import tkinter as tk

from PIL import Image, ImageColor

from jarvis.ui.pets.context_menu import appearance
from jarvis.ui.pets.menu_renderer import MenuEntry, hit_test, menu_size, render_menu
from jarvis.ui.theme import POPOVER_COLORS
from ui.orb.layered_surface import AlphaWindow

log = logging.getLogger(__name__)


def popup_position(x, y, size, area):
    """Keep the complete panel on the pointer's monitor, including negative origins."""
    left, top, width, height = area
    return (
        max(left + 8, min(x, left + width - size[0] - 8)),
        max(top + 8, min(y, top + height - size[1] - 8)),
    )


class PetContextMenu:
    def __init__(self, parent, *, scale=1.0, **_kwargs):
        self.parent = parent
        self.scale = max(0.75, min(3.0, float(scale)))
        self.entries: list[MenuEntry | None] = []
        self.commands = []
        self.surface = None
        self.selected = None
        self.keyboard = False
        self._focus_check = None
        self.theme = appearance()

    def add_command(self, *, label, command, accelerator="", state="normal"):
        index = sum(entry is not None for entry in self.entries)
        icon = ("open", "reset", "hide")[min(index, 2)]
        self.entries.append(MenuEntry(label, accelerator, state != "disabled", icon))
        self.commands.append(command)

    def add_separator(self):
        self.entries.append(None)
        self.commands.append(None)

    def tk_popup(self, x, y):
        from jarvis.platform.monitors import work_area_at

        size = menu_size(self.entries, self.scale)
        area = work_area_at(x, y) or (
            0,
            0,
            self.parent.winfo_screenwidth(),
            self.parent.winfo_screenheight(),
        )
        x, y = popup_position(x, y, size, area)
        self.surface = AlphaWindow(
            self.parent,
            size,
            backdrop=ImageColor.getrgb(POPOVER_COLORS[self.theme]["background"]),
            name="pet context menu",
        )
        surface = self.surface
        try:
            surface.move(x, y)
            surface.bind("<Motion>", self._motion)
            surface.bind("<Leave>", self._leave)
            surface.bind("<ButtonPress-1>", self._click)
            surface.bind("<ButtonPress-3>", lambda event: self.destroy())
            surface.bind("<KeyPress>", self._key)
            surface.top.bind("<FocusOut>", self._focus_out)
            self._paint()
            surface.show()
            surface.top.grab_set()
            surface.top.focus_force()
            surface.canvas.focus_set()
            # Tk keeps servicing its event queue while the popup owns keyboard input.
            surface.top.wait_window()
        finally:
            self.destroy()

    def _paint(self):
        if self.surface is not None:
            frame = render_menu(
                self.entries,
                self.theme,
                selected=self.selected,
                keyboard=self.keyboard,
                scale=self.scale,
            )
            if sys.platform not in ("win32", "darwin"):
                # Tk on X11/Wayland has no Windows colour key or Cocoa clear backing.
                # Opaque neutral corners are preferable to exposing the magenta key.
                backing = Image.new("RGBA", frame.size, POPOVER_COLORS[self.theme]["background"])
                backing.alpha_composite(frame)
                frame = backing
            self.surface.present(frame)

    def _hit(self, event):
        if self.surface is None or self.surface.top is None:
            return None
        top = self.surface.top
        x = (event.x_root - top.winfo_rootx()) / self.scale
        y = (event.y_root - top.winfo_rooty()) / self.scale
        return hit_test(self.entries, x, y, menu_size(self.entries)[0])

    def _motion(self, event):
        selected = self._hit(event)
        if selected != self.selected or self.keyboard:
            self.selected, self.keyboard = selected, False
            self._paint()

    def _leave(self, _event):
        self.selected = None
        self._paint()

    def _click(self, event):
        selected = self._hit(event)
        if selected is None:
            self.destroy()
        else:
            self._activate(selected)
        return "break"

    def _key(self, event):
        key = event.keysym
        if key in ("Escape", "Tab"):
            self.destroy()
        elif key in ("Return", "space") and self.selected is not None:
            self._activate(self.selected)
        elif key in ("Up", "Down", "Home", "End"):
            enabled = [
                i for i, entry in enumerate(self.entries) if entry is not None and entry.enabled
            ]
            if not enabled:
                return "break"
            if key in ("Home", "End") or self.selected not in enabled:
                self.selected = enabled[-1] if key in ("Up", "End") else enabled[0]
            else:
                self.selected = enabled[
                    (enabled.index(self.selected) + (1 if key == "Down" else -1)) % len(enabled)
                ]
            self.keyboard = True
            self._paint()
        return "break"

    def _activate(self, index):
        entry = self.entries[index]
        if entry is None or not entry.enabled or self.surface is None:
            return
        command = self.commands[index]
        self.destroy()
        command()

    def _focus_out(self, _event):
        if self.surface is not None and self._focus_check is None:
            self._focus_check = self.surface.top.after_idle(self._close_if_unfocused)

    def _close_if_unfocused(self):
        self._focus_check = None
        if self.surface is None:
            return
        top = self.surface.top
        focus = top.focus_displayof()
        if focus is None or focus.winfo_toplevel() != top:
            self.destroy()

    def grab_release(self):
        if self.surface is not None and self.surface.top is not None:
            try:
                if self.surface.top.grab_current() == self.surface.top:
                    self.surface.top.grab_release()
            except tk.TclError:
                log.debug("Pet popup grab already released", exc_info=True)

    def destroy(self):
        surface = self.surface
        if surface is None:
            return
        self.grab_release()
        if self._focus_check is not None:
            try:
                surface.top.after_cancel(self._focus_check)
            except tk.TclError:
                log.debug("Pet popup focus check already cancelled", exc_info=True)
            self._focus_check = None
        self.surface = None
        surface.destroy()
