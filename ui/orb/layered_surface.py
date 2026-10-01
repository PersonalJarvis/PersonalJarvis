"""A frameless always-on-top Tk window that shows one RGBA frame at a time.

The pet's cards and its thought bubble are drawn in PIL with real alpha. This
window puts such a frame on the desktop the best way the platform allows:

* **Windows** — ``UpdateLayeredWindow`` (:mod:`jarvis.platform.layered_window`):
  real per-pixel alpha, so soft shadows, see-through surfaces and fades work,
  and fully transparent pixels let clicks through.
* **macOS** — a transparent Tk window; the frame is flattened to hard edges
  first (Aqua composites RGBA images without clearing the previous frame).
* **Elsewhere**, or when Windows refuses a frame — the overlay's colour key:
  the frame is flattened onto opaque colour with hard edges, so no blended
  pixel survives as a pink fringe.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from collections.abc import Callable

from PIL import Image, ImageTk

from jarvis.platform import layered_window

log = logging.getLogger("jarvis.orb")

#: A frame's pixels at least this opaque survive flattening; the rest are keyed.
FLATTEN_ALPHA_CUTOFF = 200


def flatten_for_key(
    image: Image.Image, key: tuple[int, int, int], backdrop: tuple[int, int, int]
) -> Image.Image:
    """``image`` for a colour-keyed window: opaque where it is mostly covered.

    Pixels at least :data:`FLATTEN_ALPHA_CUTOFF` covered become the frame over
    ``backdrop``; the rest become the key — a hard edge, never a fringe.
    """
    alpha = image.getchannel("A")
    solid = Image.new("RGBA", image.size, (*backdrop, 255))
    solid.alpha_composite(image)
    hard = alpha.point(lambda v: 255 if v >= FLATTEN_ALPHA_CUTOFF else 0)
    out = Image.new("RGB", image.size, key)
    out.paste(solid.convert("RGB"), (0, 0), hard)
    return out


class AlphaWindow:
    """One frameless window showing RGBA frames. Every method runs on the Tk thread."""

    def __init__(
        self,
        parent: tk.Misc,
        size: tuple[int, int],
        *,
        backdrop: tuple[int, int, int],
        name: str = "pet surface",
    ) -> None:
        from ui.orb import overlay as _ov  # noqa: PLC0415 — overlay imports this module

        self._ov = _ov
        self._name = name
        self._backdrop = backdrop
        self._size = (max(1, int(size[0])), max(1, int(size[1])))
        self._alpha_hwnd = 0
        self._mac_transparent = False
        self._shown = False
        self._photo: ImageTk.PhotoImage | None = None
        self._image_id: int | None = None
        top = tk.Toplevel(parent)
        top.overrideredirect(True)
        top.wm_attributes("-topmost", True)
        if sys.platform == "darwin":
            try:
                top.wm_attributes("-transparent", True)
                top.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                log.warning("macOS -transparent unsupported — the %s renders opaque", name)
                top.configure(bg=_ov.COLOR_KEY_HEX)
        else:
            if layered_window.per_pixel_alpha_supported():
                top.update_idletasks()
                hwnd = layered_window.tk_toplevel_hwnd(top)
                # The first frame goes in now: Windows refuses the very first
                # UpdateLayeredWindow on a window that was already withdrawn.
                if layered_window.enable_per_pixel_alpha(hwnd) and layered_window.update_layered(
                    hwnd, self.blank()
                ):
                    self._alpha_hwnd = hwnd
            if not self._alpha_hwnd:
                try:
                    top.wm_attributes("-transparentcolor", _ov.COLOR_KEY_HEX)
                except tk.TclError:
                    log.debug("no colour key on this window system", exc_info=True)
            top.configure(bg=_ov.COLOR_KEY_HEX)
        _ov._hide_tk_window_from_task_switcher(top)  # noqa: SLF001 — shared overlay helper
        _ov._exclude_tk_window_from_capture(top)  # noqa: SLF001 — shared overlay helper
        top.withdraw()
        canvas = tk.Canvas(
            top,
            width=self._size[0],
            height=self._size[1],
            bg="systemTransparent" if self._mac_transparent else _ov.COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        if self._mac_transparent:
            top.update_idletasks()
            _ov.apply_macos_clear_backing()
        self.top: tk.Toplevel | None = top
        self.canvas: tk.Canvas | None = canvas

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def shown(self) -> bool:
        return self._shown

    def blank(self) -> Image.Image:
        return Image.new("RGBA", self._size, (0, 0, 0, 0))

    def bind(self, sequence: str, handler: Callable[[tk.Event], object]) -> None:
        if self.canvas is not None:
            self.canvas.bind(sequence, handler)

    def set_cursor(self, cursor: str) -> None:
        if self.canvas is None:
            return
        try:
            self.canvas.configure(cursor=cursor)
        except tk.TclError:
            log.debug("%s cursor change failed", self._name, exc_info=True)

    def resize(self, size: tuple[int, int]) -> None:
        size = (max(1, int(size[0])), max(1, int(size[1])))
        if size == self._size:
            return
        self._size = size
        if self.canvas is not None:
            try:
                self.canvas.configure(width=size[0], height=size[1])
            except tk.TclError:
                log.debug("%s resize failed", self._name, exc_info=True)

    def move(self, x: int, y: int) -> None:
        if self.top is None:
            return
        w, h = self._size
        try:
            self.top.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
        except tk.TclError:
            log.debug("%s placement failed", self._name, exc_info=True)

    def present(self, frame: Image.Image) -> None:
        """Put ``frame`` (RGBA, the window's size) on screen."""
        _ov = self._ov
        if self._alpha_hwnd:
            if layered_window.update_layered(self._alpha_hwnd, frame):
                return
            log.warning("per-pixel alpha refused; the %s falls back to the colour key", self._name)
            self._alpha_hwnd = 0
            if self.top is not None:
                try:
                    self.top.wm_attributes("-transparentcolor", _ov.COLOR_KEY_HEX)
                except tk.TclError:
                    log.debug("colour-key fallback failed", exc_info=True)
        canvas = self.canvas
        if canvas is None:
            return
        key = (int(_ov.COLOR_KEY_RGB[0]), int(_ov.COLOR_KEY_RGB[1]), int(_ov.COLOR_KEY_RGB[2]))
        flat = flatten_for_key(frame, key, self._backdrop)
        image = _ov.key_to_alpha(flat) if self._mac_transparent else flat
        try:
            self._photo = ImageTk.PhotoImage(image, master=canvas)
            if self._image_id is None:
                self._image_id = canvas.create_image(0, 0, anchor="nw", image=self._photo)
            else:
                canvas.itemconfigure(self._image_id, image=self._photo)
        except tk.TclError:
            log.debug("%s frame paint failed", self._name, exc_info=True)

    def show(self) -> None:
        if self.top is None or self._shown:
            return
        try:
            self.top.deiconify()
            self.top.lift()
        except tk.TclError:
            log.debug("%s show failed", self._name, exc_info=True)
            return
        self._shown = True

    def withdraw(self) -> None:
        if self._alpha_hwnd:
            # A layered window keeps its last bitmap; leave nothing behind.
            layered_window.update_layered(self._alpha_hwnd, self.blank())
        if self.top is not None and self._shown:
            try:
                self.top.withdraw()
            except tk.TclError:
                log.debug("%s withdraw failed", self._name, exc_info=True)
        self._shown = False

    def destroy(self) -> None:
        top, self.top, self.canvas = self.top, None, None
        self._photo = None
        self._shown = False
        if top is not None:
            try:
                top.destroy()
            except tk.TclError:
                log.debug("%s destroy failed", self._name, exc_info=True)


__all__ = ["FLATTEN_ALPHA_CUTOFF", "AlphaWindow", "flatten_for_key"]
