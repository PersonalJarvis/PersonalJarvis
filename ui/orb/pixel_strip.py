"""The pet's control strip in pixel art — the pets' own retro look.

Same controls, same places, same hit areas as :func:`ui.orb.controls.render_pet_strip`
(the geometry is :func:`ui.orb.controls.pet_strip_layout`); only the drawing
changes. Every control is a chunky pixel button in the material the thought
bubble and the done letters use: cream paper with the sprites' dark outline, a
highlight row on top, a shaded row underneath and a thick dark bottom edge, so
each button looks pressable. The glyphs are hand-placed pixel sprites in the
pets' dark ink — a bell, a microphone, a speaker, a handset — and the talk
indicator is three sky-blue pixel bars that follow the voice.

Drawn at art resolution (one art pixel = ``art_px`` screen pixels, the pet
sprite's own size) and scaled up nearest-neighbour, so every edge is a hard
step. That also suits the strip's colour-keyed window: no pixel is ever a
blend of a button and the key.
"""

from __future__ import annotations

import functools
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ui.orb import controls
from ui.orb.controls import PetStripState

_Rgb = tuple[int, int, int]

#: The pixel UI kit's palette (shared with ``pet_cards`` and ``thought_bubble``).
OUTLINE: _Rgb = (27, 22, 34)
PAPER: _Rgb = (252, 248, 237)
PAPER_HOVER: _Rgb = (255, 255, 250)
HIGHLIGHT: _Rgb = (255, 255, 255)
SHADE: _Rgb = (226, 218, 199)
DIVIDER: _Rgb = (214, 205, 184)
INK: _Rgb = (46, 36, 58)
MUTED: _Rgb = (206, 64, 72)
CALL_GO: _Rgb = (126, 214, 120)
CALL_STOP: _Rgb = (236, 104, 104)
SKY: _Rgb = (126, 186, 255)
SKY_HIGHLIGHT: _Rgb = (214, 234, 255)
NIGHT: _Rgb = (78, 98, 150)

#: Hand-placed glyphs, 11 x 11 art pixels, ``#`` is ink.
_BELL = (
    ".....#.....",
    "....###....",
    "...#####...",
    "..#######..",
    "..#######..",
    "..#######..",
    "..#######..",
    ".#########.",
    "###########",
    "...........",
    "....###....",
)
_MIC = (
    "....###....",
    "...#####...",
    "...#####...",
    "...#####...",
    ".#.#####.#.",
    ".#..###..#.",
    "..#.....#..",
    "...#####...",
    ".....#.....",
    ".....#.....",
    "...#####...",
)
_SPEAKER = (
    "....#......",
    "...##...#..",
    "..###....#.",
    "#####.#..#.",
    "#####..#.#.",
    "#####..#.#.",
    "#####.#..#.",
    "..###....#.",
    "...##...#..",
    "....#......",
    "...........",
)
_SPEAKER_QUIET = (
    "....#......",
    "...##......",
    "..###......",
    "#####......",
    "#####......",
    "#####......",
    "#####......",
    "..###......",
    "...##......",
    "....#......",
    "...........",
)
_PHONE = (
    ".##........",
    "####.......",
    "####.......",
    ".###.......",
    "..##.......",
    "..###......",
    "...###..##.",
    "....######.",
    ".....#####.",
    "......###..",
    "...........",
)
_HANGUP = (
    "...........",
    "...........",
    "...........",
    "...#####...",
    ".#########.",
    "###.....###",
    "###.....###",
    "...........",
    "...........",
    "...........",
    "...........",
)
GLYPH = 11


def _stamp(d: ImageDraw.ImageDraw, rows: tuple[str, ...], x0: int, y0: int, color: _Rgb) -> None:
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell == "#":
                d.point((x0 + x, y0 + y), fill=color)


def _slash(d: ImageDraw.ImageDraw, x0: int, y0: int, color: _Rgb) -> None:
    """A two-pixel diagonal strike from top left to bottom right."""
    for i in range(GLYPH):
        d.point((x0 + i, y0 + i), fill=color)
        if i + 1 < GLYPH:
            d.point((x0 + i + 1, y0 + i), fill=color)


def _ring(mask: Image.Image) -> Image.Image:
    return ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask)


def _button(canvas: Image.Image, shape: Image.Image, fill: _Rgb, origin: tuple[int, int]) -> None:
    """One pixel button: dark bottom edge, fill, highlight and shade rows, outline."""
    w, h = shape.size
    ox, oy = origin
    whole = Image.new("L", canvas.size, 0)
    whole.paste(shape, (ox, oy))
    # The thick bottom edge: the shape again, one art pixel lower, in ink.
    depth = Image.new("L", canvas.size, 0)
    depth.paste(shape, (ox, oy + 1))
    canvas.paste(OUTLINE, (0, 0), ImageChops.lighter(depth, _ring(depth)))
    canvas.paste(fill, (0, 0), whole)
    up = Image.new("L", canvas.size, 0)
    up.paste(shape, (ox, oy - 1))
    down = Image.new("L", canvas.size, 0)
    down.paste(shape, (ox, oy + 1))
    canvas.paste(SHADE, (0, 0), ImageChops.subtract(whole, up))
    canvas.paste(HIGHLIGHT, (0, 0), ImageChops.subtract(whole, down))
    canvas.paste(OUTLINE, (0, 0), _ring(whole))
    _ = (w, h)


def _disc(diameter: int) -> Image.Image:
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter - 1, diameter - 1], fill=255)
    return mask


def _stadium(width: int, height: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=height // 2, fill=255
    )
    return mask


def _wobble(angle: float) -> int:
    """A ringing glyph shakes by one art pixel toward its swing."""
    if abs(angle) < 2.0:
        return 0
    return 1 if angle > 0 else -1


@functools.lru_cache(maxsize=256)
def render_pet_strip_pixel(
    state: PetStripState,
    scale: float = 1.0,
    art_px: int = 3,
    color_key: tuple[int, int, int] = (255, 0, 255),
) -> Image.Image:
    """The pet strip as a colour-keyed RGB frame, in pixel art.

    Same size and the same control places as ``controls.render_pet_strip``,
    so ``controls.pet_hit_test`` stays right. Cached per state like the
    original: a strip at rest costs one render per look.
    """
    layout = controls.pet_strip_layout(scale)
    u = max(1, int(art_px))
    aw, ah = math.ceil(layout.width / u), math.ceil(layout.height / u)
    art = Image.new("RGB", (aw, ah), color_key)
    d = ImageDraw.Draw(art)

    def a(value: float) -> int:
        return int(round(value / u))

    # Room for the outline above and the bottom edge below every button.
    top = 1
    body_h = max(GLYPH + 4, ah - 3)

    def disc_at(cx: float, action: str, fill: _Rgb) -> tuple[int, int]:
        size = body_h
        x0 = a(cx) - size // 2
        _button(art, _disc(size), fill, (x0, top))
        return x0 + (size - GLYPH) // 2, top + (size - GLYPH) // 2

    hovered = state.hovered
    # The bell.
    bx, by = disc_at(layout.pen[0], "bell", PAPER_HOVER if hovered == "bell" else PAPER)
    bell_ink = MUTED if state.notify_off else INK
    _stamp(d, _BELL, bx + _wobble(controls.ring_angle(state.ring)), by, bell_ink)
    if state.notify_off:
        _slash(d, bx, by, MUTED)

    # The pill: microphone, talk bars, speaker.
    px0, _py0, px1, _py1 = layout.pill
    pill_w = a(px1) - a(px0)
    _button(art, _stadium(pill_w, body_h), PAPER, (a(px0), top))
    for action, sx0, sx1 in layout.slots:
        if hovered == action:
            hx0, hx1 = a(sx0) + 1, a(sx1) - 1
            d.rectangle([hx0, top + 2, hx1, top + body_h - 3], fill=PAPER_HOVER)
    for dx in layout.dividers:
        x = a(dx)
        for y in range(top + 3, top + body_h - 3, 2):  # a dotted seam
            d.point((x, y), fill=DIVIDER)
    gy = top + (body_h - GLYPH) // 2
    for action, sx0, sx1 in layout.slots:
        gx = (a(sx0) + a(sx1)) // 2 - GLYPH // 2
        if action == "mic_mute":
            ink = MUTED if state.mic_muted else INK
            _stamp(d, _MIC, gx, gy, ink)
            if state.mic_muted:
                _slash(d, gx, gy, MUTED)
        elif action == "speaker":
            ink = MUTED if state.speaker_muted else INK
            _stamp(d, _SPEAKER_QUIET if state.speaker_muted else _SPEAKER, gx, gy, ink)
            if state.speaker_muted:
                _slash(d, gx, gy, MUTED)
        elif action == "orb":
            _bars(d, (a(sx0) + a(sx1)) // 2, top + body_h // 2, body_h, state)

    # The phone: green to call, red to hang up, under the pointer.
    if hovered == "call":
        call_fill = CALL_STOP if state.active else CALL_GO
    else:
        call_fill = PAPER
    cx, cy = disc_at(layout.call[0], "call", call_fill)
    shake = _wobble(controls.call_ring_angle(state.call_ring))
    _stamp(d, _HANGUP if state.active else _PHONE, cx + shake, cy, INK)

    big = art.resize((aw * u, ah * u), Image.Resampling.NEAREST)
    return big.crop((0, 0, layout.width, layout.height))


def _bars(d: ImageDraw.ImageDraw, cx: int, cy: int, body_h: int, state: PetStripState) -> None:
    """The talk indicator: three pixel bars of sky, as tall as the voice."""
    tallest = max(3, body_h - 6)
    for i, (share, glow) in enumerate(controls.indicator_bars(state)):
        h = max(2, int(round(tallest * share / controls.PET_INDICATOR_MAX_H)))
        h = min(tallest, h)
        # Two art pixels wide, five apart: each bar keeps its own outline.
        x = cx - 6 + i * 5
        y0 = cy - h // 2
        y1 = y0 + h - 1
        t = max(0.0, min(1.0, glow))
        body = tuple(int(round(n + (s - n) * t)) for n, s in zip(NIGHT, SKY, strict=True))
        d.rectangle([x - 1, y0 - 1, x + 2, y1 + 1], fill=OUTLINE)
        d.rectangle([x, y0, x + 1, y1], fill=body)
        d.point((x, y0), fill=SKY_HIGHLIGHT if t > 0.7 else body)


__all__ = ["render_pet_strip_pixel"]
