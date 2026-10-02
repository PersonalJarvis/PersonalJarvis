"""The pet's thought bubble: what Jarvis is thinking, above the pet's head.

Drawn in the pets' own pixel-art language (``jarvis/ui/pets/builtin``): a
puffy white cloud with a dark one-pixel outline, a shaded bottom row and a
highlight in the corner, plus two small puffs trailing down to the pet's head
— a comic thought bubble at the pet's pixel size, so it reads as part of the
figure rather than as an app panel.

* While Jarvis only thinks, three pixel dots bob in turn inside the cloud.
* While it works through a step ("Search the web"), the step is the cloud's
  text, in the dark ink of the pets' outlines.
* The puffs pop in one after the other, then the cloud; on the way out the
  order reverses. The cloud floats up and down by one art pixel.

Pure rendering (:func:`render_bubble`) and one window (:class:`PetThoughtBubble`).
Pixel art is drawn at art resolution and scaled by a whole number with
nearest-neighbour, exactly like the pet sprites, so its edges stay crisp.
"""

from __future__ import annotations

import functools
import logging
import math
import time
import tkinter as tk
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ui.orb import pet_cards
from ui.orb.layered_surface import AlphaWindow

log = logging.getLogger("jarvis.orb")

_Rgb = tuple[int, int, int]

#: The pets' palette: the dark outline every built-in sprite uses, a white
#: cloud, its shaded underside and its highlight, and the ink of the text.
OUTLINE: _Rgb = (27, 22, 34)
FILL: _Rgb = (250, 249, 253)
SHADE: _Rgb = (222, 220, 234)
HIGHLIGHT: _Rgb = (255, 255, 255)
INK: _Rgb = (46, 36, 58)

#: Art-pixel geometry (one art pixel = ``art_px`` screen pixels).
PAD_X = 5
PAD_Y = 3
MIN_BODY_H = 11
DOT = 2
DOT_GAP = 2
DOTS = 3
#: The trailing puffs: (radius, dx from the cloud's left edge, dy below it).
PUFFS: tuple[tuple[float, float, float], ...] = ((2.6, 6.0, 3.6), (1.7, 2.5, 8.2))
#: Room around the art for the float and the outline.
MARGIN = 2

#: Animation timing.
DOT_STEP_S = 0.22
BOB_PERIOD_S = 1.8
ENTER_S = 0.36
LEAVE_S = 0.24
FRAME_MS = 60
#: Text size, logical px at 100 % (times the strip scale).
TEXT_PX = 13.0
#: The widest the cloud's text may get, logical px at 100 %.
MAX_TEXT_W = 230


@dataclass(frozen=True)
class BubbleLayout:
    """Where things sit in a rendered bubble, in screen pixels."""

    size: tuple[int, int]
    #: The cloud body's box (x0, y0, x1, y1).
    body: tuple[int, int, int, int]
    #: Where the smallest puff ends: the point that touches the pet's head.
    tip: tuple[int, int]


def _cloud_mask(w: int, h: int) -> Image.Image:
    """The cloud's silhouette at art resolution: a body with puffy bumps on top.

    Drawn without antialiasing — every pixel is in or out, like a sprite.
    """
    bump = max(3, h // 3)
    mask = Image.new("L", (w, h + bump), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, bump, w - 1, bump + h - 1], radius=min(4, h // 2), fill=255)
    # Evenly spaced bumps along the whole top edge make it a cloud, not a box
    # with a hat: one about every eleven art pixels, alternating in size.
    count = max(2, round(w / 11))
    span = w - 2 * bump
    for i in range(count):
        cx = bump + span * (i + 0.5) / count
        r = bump + (0 if i % 2 else 1)
        rx = max(r, span / count * 0.62)
        d.ellipse([cx - rx, bump - r + 1, cx + rx, bump + r + 1], fill=255)
    return mask


def _outline(mask: Image.Image) -> Image.Image:
    """The one-pixel ring around ``mask`` (4-connected, like hand-drawn sprites)."""
    grown = mask.filter(ImageFilter.MaxFilter(3))
    return ImageChops.subtract(grown, mask)


@functools.lru_cache(maxsize=64)
def _art(body_w: int, body_h: int, puffs_shown: int, cloud_shown: bool) -> tuple[Image.Image, int]:
    """The bubble at art resolution (RGBA) and the cloud's top bump height."""
    bump = max(3, body_h // 3)
    cloud = _cloud_mask(body_w, body_h)
    tail_h = int(max(dy + r for r, _dx, dy in PUFFS)) + 2
    w = body_w + 2 * MARGIN
    h = cloud.height + tail_h + 2 * MARGIN
    shape = Image.new("L", (w, h), 0)
    if cloud_shown:
        shape.paste(cloud, (MARGIN, MARGIN))
    d = ImageDraw.Draw(shape)
    body_bottom = MARGIN + cloud.height
    for index, (r, dx, dy) in enumerate(PUFFS):
        if index < len(PUFFS) - puffs_shown:
            continue  # the pop-in starts with the smallest puff, nearest the head
        cx, cy = MARGIN + dx, body_bottom + dy
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    # Fill, then the shaded underside (the bottom art row of every blob), a
    # corner highlight, then the outline around everything.
    art = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    art.paste((*FILL, 255), (0, 0), shape)
    lifted = Image.new("L", (w, h), 0)
    lifted.paste(shape, (0, -1))
    bottom_row = ImageChops.subtract(shape, lifted)
    art.paste((*SHADE, 255), (0, 0), bottom_row)
    if cloud_shown:
        hl = ImageDraw.Draw(art)
        x0, y0 = MARGIN + 3, MARGIN + bump + 2
        hl.point([(x0, y0), (x0 + 1, y0), (x0, y0 + 1)], fill=(*HIGHLIGHT, 255))
    art.paste((*OUTLINE, 255), (0, 0), _outline(shape))
    return art, bump


def bubble_layout(
    text_w: int, line_h: int, *, art_px: int, dots: bool
) -> tuple[int, int, int, int]:
    """``(body_w, body_h, content_w, content_h)`` in art pixels."""
    u = max(1, art_px)
    if dots:
        content_w = DOTS * DOT + (DOTS - 1) * DOT_GAP
        content_h = DOT + 1
    else:
        content_w = math.ceil(text_w / u)
        content_h = math.ceil(line_h / u)
    body_w = content_w + 2 * PAD_X
    body_h = max(MIN_BODY_H, content_h + 2 * PAD_Y)
    return body_w, body_h, content_w, content_h


def render_bubble(
    text: str,
    *,
    art_px: int,
    scale: float,
    now: float,
    puffs_shown: int = len(PUFFS),
    cloud_shown: bool = True,
) -> tuple[Image.Image, BubbleLayout]:
    """The bubble as RGBA at screen size, and where its parts sit.

    An empty ``text`` shows the bobbing dots. ``puffs_shown`` and
    ``cloud_shown`` drive the pop-in and pop-out.
    """
    u = max(1, int(art_px))
    font = pet_cards.font("semibold", max(9, int(round(TEXT_PX * scale))))
    dots = not text
    shown_text = "" if dots else pet_cards.ellipsize(text, font, MAX_TEXT_W * scale)
    text_w = int(math.ceil(font.getlength(shown_text))) if shown_text else 0
    ascent, descent = font.getmetrics()
    body_w, body_h, content_w, content_h = bubble_layout(
        text_w, ascent + descent, art_px=u, dots=dots
    )
    art, bump = _art(body_w, body_h, puffs_shown, cloud_shown)
    art = art.copy()
    body_x0, body_y0 = MARGIN, MARGIN + bump
    if cloud_shown and dots:
        d = ImageDraw.Draw(art)
        phase = int(now / DOT_STEP_S) % (DOTS + 1)
        cx0 = body_x0 + (body_w - content_w) // 2
        cy = body_y0 + (body_h - DOT) // 2
        for i in range(DOTS):
            lift = 1 if i == phase else 0
            x = cx0 + i * (DOT + DOT_GAP)
            d.rectangle([x, cy - lift, x + DOT - 1, cy - lift + DOT - 1], fill=(*INK, 255))
    image = art.resize((art.width * u, art.height * u), Image.Resampling.NEAREST)
    if cloud_shown and shown_text:
        d = ImageDraw.Draw(image)
        cx = (body_x0 + body_w / 2.0) * u
        cy = (body_y0 + body_h / 2.0) * u
        d.text((cx, cy), shown_text, font=font, fill=(*INK, 255), anchor="mm")
    small_r, small_dx, small_dy = PUFFS[-1]
    cloud_bottom = MARGIN + bump + body_h
    tip = (int((MARGIN + small_dx) * u), int((cloud_bottom + small_dy + small_r) * u))
    body = (body_x0 * u, (body_y0 - bump) * u, (body_x0 + body_w) * u, (body_y0 + body_h) * u)
    return image, BubbleLayout(size=image.size, body=body, tip=tip)


def entrance(age_s: float, leaving: bool) -> tuple[int, bool]:
    """``(puffs_shown, cloud_shown)`` at ``age_s`` into the pop-in (or pop-out)."""
    span = LEAVE_S if leaving else ENTER_S
    t = max(0.0, min(1.0, age_s / span))
    if leaving:
        t = 1.0 - t
    steps = len(PUFFS) + 1
    shown = int(t * steps + 1e-9)
    # In: smallest puff (nearest the head) first, then the bigger one, then
    # the cloud. ``PUFFS`` is listed big to small, so count from its end.
    puffs = min(len(PUFFS), shown)
    return puffs, shown >= steps


class PetThoughtBubble:
    """The bubble's own window. Every method runs on the Tk thread."""

    def __init__(self, parent: tk.Misc, *, art_px: int = 3, scale: float = 1.0) -> None:
        self._art_px = max(1, int(art_px))
        self._scale = max(0.25, float(scale))
        self._text = ""
        self._visible = False
        self._leaving = False
        self._since = 0.0
        self._anchor: tuple[int, int, int] | None = None
        self._after: str | None = None
        self._clear_after: str | None = None
        self._window = AlphaWindow(parent, (8, 8), backdrop=FILL, name="thought bubble")

    @property
    def window(self) -> tk.Toplevel | None:
        return self._window.top if self._window.shown else None

    @property
    def showing(self) -> bool:
        return self._visible and not self._leaving

    def set_look(self, *, art_px: int, scale: float) -> None:
        self._art_px = max(1, int(art_px))
        self._scale = max(0.25, float(scale))
        if self._visible:
            self._paint()

    def set_anchor(self, head_x: int, head_y: int, screen_w: int) -> None:
        """Where the trailing puffs end: just above the pet's head."""
        self._anchor = (int(head_x), int(head_y), int(screen_w))
        if self._visible:
            self._paint()

    def set_text(self, text: str) -> None:
        """Show the bubble with ``text`` (``""``: the thinking dots)."""
        self._cancel("_clear_after")
        self._text = " ".join(str(text or "").split())
        if not self._visible or self._leaving:
            self._visible = True
            self._leaving = False
            self._since = time.monotonic()
        self._tick()

    def clear(self, linger_s: float = 1.5) -> None:
        """Pop the bubble away after ``linger_s`` seconds (at once when 0)."""
        if not self._visible or self._leaving or self._clear_after is not None:
            return
        top = self._window.top
        delay = max(0, int(float(linger_s) * 1000))
        if delay == 0 or top is None:
            self._start_leaving()
            return
        self._clear_after = top.after(delay, self._start_leaving)

    def hide(self) -> None:
        """Gone at once (the pet was hidden)."""
        self._cancel("_clear_after")
        self._cancel("_after")
        self._visible = False
        self._leaving = False
        self._window.withdraw()

    def destroy(self) -> None:
        self.hide()
        self._window.destroy()

    def _start_leaving(self) -> None:
        self._clear_after = None
        if not self._visible or self._leaving:
            return
        self._leaving = True
        self._since = time.monotonic()
        self._tick()

    def _tick(self) -> None:
        self._cancel("_after")
        top = self._window.top
        if top is None or not self._visible:
            return
        age = time.monotonic() - self._since
        if self._leaving and age >= LEAVE_S:
            self.hide()
            return
        self._paint()
        self._after = top.after(FRAME_MS, self._tick)

    def _paint(self) -> None:
        if self._anchor is None:
            return
        now = time.monotonic()
        puffs, cloud = entrance(now - self._since, self._leaving)
        image, layout = render_bubble(
            self._text,
            art_px=self._art_px,
            scale=self._scale,
            now=now,
            puffs_shown=puffs,
            cloud_shown=cloud,
        )
        head_x, head_y, screen_w = self._anchor
        bob = self._art_px if int(now / (BOB_PERIOD_S / 2)) % 2 else 0
        x = head_x - layout.tip[0]
        y = head_y - layout.tip[1] - bob
        x = max(4, min(x, screen_w - image.width - 4))
        y = max(4, y)
        self._window.resize(image.size)
        self._window.move(x, y)
        self._window.present(image)
        self._window.show()

    def _cancel(self, name: str) -> None:
        after_id = getattr(self, name)
        top = self._window.top
        if after_id is not None and top is not None:
            try:
                top.after_cancel(after_id)
            except tk.TclError:
                log.debug("thought bubble timer cancel failed", exc_info=True)
        setattr(self, name, None)


__all__ = ["BubbleLayout", "PetThoughtBubble", "entrance", "render_bubble"]
