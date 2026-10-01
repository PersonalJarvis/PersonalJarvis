"""The voice orb's control row — the desktop twin of the in-app bubble's buttons.

``components/agentic/VoiceBubble.tsx`` puts four equal circles under the orb:
attach a file, start/end the conversation, put the voice away, mute the
assistant's voice. On the desktop those capabilities existed only as invisible
gestures (double-double-click, right-click, drop-on-orb), which is the same as
not existing. This module draws that row and routes its clicks, so the orb ON
the desktop offers what the orb IN the window offers.

Two constraints shape the implementation, and neither is cosmetic:

* **Hard circular edges.** The overlay windows key out one exact colour
  (magenta) to become transparent. A pixel that is a *blend* of button and key
  colour survives as a pink fringe, so every disc is composed through a BINARY
  mask — the antialiasing lives strictly inside the disc, where the glyph is.
  Same rule as ``ui.orb.voice_orb``.
* **Supersampled glyphs.** A 4 px stroke drawn directly at 28 px aliases into a
  staircase. Glyphs are drawn at 4x on their own layer and downscaled with
  LANCZOS, exactly as ``jarvis.ui.jarvisbar.renderer`` does for its mic.

The palette is the app's dark theme (``frontend/src/index.css``) resolved
against the opaque background the app composites onto, because Tk has no
per-pixel alpha here: what the web calls ``bg-background/85`` is one flat
colour on the desktop.
"""

from __future__ import annotations

import functools
import inspect
import logging
import math
import os
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

# --- Palette (the app's dark theme, flattened) ------------------------------
#: Resting disc: ``bg-background/85`` over the desktop reads as a near-black
#: pebble; solid here because a layered Tk window has no partial alpha.
BTN_BG = (19, 19, 19)
#: ``border-border/50``, opened up a little. In the app a ``shadow-lg`` does
#: the separating; a colour-keyed window cannot cast one (a soft edge blends
#: with the key and survives as a pink fringe), so the rim has to carry that
#: job alone — measured against a black terminal, where the app's own value
#: disappeared completely.
BTN_BORDER = (52, 52, 52)
#: ``text-muted-foreground``.
BTN_ICON = (143, 143, 143)
#: Hover: ``hover:bg-secondary hover:text-foreground``.
BTN_BG_HOVER = (26, 26, 26)
BTN_ICON_HOVER = (244, 244, 245)
#: Engaged (a live conversation on the mic button): ``bg-primary/15``,
#: ``border-primary/50``, ``text-primary``.
BTN_BG_ON = (47, 41, 10)
BTN_BORDER_ON = (133, 112, 10)
BTN_ICON_ON = (255, 214, 10)
#: Muted speaker: ``bg-destructive/10``, ``border-destructive/40``.
BTN_BG_OFF = (33, 16, 16)
BTN_BORDER_OFF = (102, 33, 33)
BTN_ICON_OFF = (239, 68, 68)
#: Unavailable action (``disabled:opacity-40``) — dimmed, never hidden: a
#: control that vanishes teaches the user nothing about why.
BTN_ICON_DISABLED = (74, 74, 74)

#: One disc, in pixels. Matches the in-app row's ``h-8 w-8``.
BUTTON_SIZE = 28
#: Gap between discs (``gap-2``).
BUTTON_GAP = 8
#: Breathing room around the row, so a disc never touches the window edge.
ROW_PADDING = 4
#: Vertical distance from the orb's bottom edge to the row.
ROW_GAP_FROM_ORB = 6
#: Supersampling factor for every glyph.
_SS = 4

#: The row's actions, left to right — the in-app order (attach, mic, close,
#: speaker). Kept as a tuple so the hit-test, the renderer and the caller's
#: dispatch can never disagree about which disc is which.
ACTIONS: tuple[str, ...] = ("attach", "mic", "close", "speaker")


def row_size(count: int = len(ACTIONS)) -> tuple[int, int]:
    """Pixel size of the window holding ``count`` discs."""
    width = count * BUTTON_SIZE + max(0, count - 1) * BUTTON_GAP + 2 * ROW_PADDING
    return width, BUTTON_SIZE + 2 * ROW_PADDING


def button_centers(count: int = len(ACTIONS)) -> list[float]:
    """Horizontal centre of each disc inside the row window."""
    step = BUTTON_SIZE + BUTTON_GAP
    first = ROW_PADDING + BUTTON_SIZE / 2.0
    return [first + index * step for index in range(count)]


def hit_test(x: float, y: float, count: int = len(ACTIONS)) -> str | None:
    """Which action a click at ``(x, y)`` lands on, or ``None`` between discs.

    The gaps deliberately resolve to nothing. Every one of these buttons does
    something the user would rather not undo by a near-miss — hanging up is the
    obvious one — so a click has to land ON a disc, the same rule the Jarvis
    Bar applies to its close-X (``jarvis.ui.jarvisbar.interaction``).
    """
    radius = BUTTON_SIZE / 2.0
    cy = ROW_PADDING + radius
    for index, cx in enumerate(button_centers(count)):
        if math.hypot(x - cx, y - cy) <= radius:
            return ACTIONS[index] if index < len(ACTIONS) else None
    return None


@dataclass(frozen=True)
class ControlState:
    """Everything the row needs to know to paint itself truthfully."""

    #: A conversation is running (``listen`` / ``think`` / ``speak``).
    active: bool = False
    #: The assistant's voice is muted for this session.
    speaker_muted: bool = False
    #: Which disc the pointer is over, if any.
    hovered: str | None = None
    #: Attaching needs somewhere to attach TO; without it the disc is dimmed.
    can_attach: bool = True


# --- Glyphs -----------------------------------------------------------------
# Each takes the supersampled draw context, the disc centre and radius in
# SUPERSAMPLED coordinates, the stroke colour and width. They draw line art
# only: a filled glyph at this size reads as a blob.


def _draw_paperclip(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, w: int) -> None:
    """A paper clip, drawn upright; the caller tilts it.

    Two nested loops: an outer capsule (down the left, round the bottom, up the
    right, closed across the top) and an inner one that stops short — which is
    exactly the wire path of a real clip, and the only version of this shape
    that survives being 17 px wide.
    """
    outer = r * 0.40
    inner = r * 0.17
    top = cy - r * 0.86
    bottom = cy + r * 0.86
    inner_bottom = cy + r * 0.50
    # Outer loop.
    d.line([(cx - outer, top + outer), (cx - outer, bottom - outer)], fill=color, width=w)
    d.arc(
        [cx - outer, bottom - 2 * outer, cx + outer, bottom],
        start=0,
        end=180,
        fill=color,
        width=w,
    )
    d.line([(cx + outer, top + outer), (cx + outer, bottom - outer)], fill=color, width=w)
    d.arc(
        [cx - outer, top, cx + outer, top + 2 * outer],
        start=180,
        end=360,
        fill=color,
        width=w,
    )
    # Inner loop — shorter at the bottom, open at the top, where the wire ends.
    d.line(
        [(cx - inner, top + outer * 1.6), (cx - inner, inner_bottom - inner)],
        fill=color,
        width=w,
    )
    d.arc(
        [cx - inner, inner_bottom - 2 * inner, cx + inner, inner_bottom],
        start=0,
        end=180,
        fill=color,
        width=w,
    )
    d.line(
        [(cx + inner, top + outer * 0.4), (cx + inner, inner_bottom - inner)],
        fill=color,
        width=w,
    )


def _draw_mic(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    color,
    w: int,
    *,
    slashed: bool,
) -> None:
    """Outline microphone: capsule head, cradle bow, stand. Mirrors the bar's."""
    head_w = r * 0.30
    d.rounded_rectangle(
        [cx - head_w, cy - r * 0.72, cx + head_w, cy + r * 0.06],
        radius=head_w,
        outline=color,
        width=w,
    )
    bow = r * 0.55
    d.arc(
        [cx - bow, cy - r * 0.40, cx + bow, cy + r * 0.48],
        start=15,
        end=165,
        fill=color,
        width=w,
    )
    d.line([(cx, cy + r * 0.44), (cx, cy + r * 0.74)], fill=color, width=w)
    foot = r * 0.30
    d.line([(cx - foot, cy + r * 0.74), (cx + foot, cy + r * 0.74)], fill=color, width=w)
    if slashed:
        s = r * 0.72
        d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color, width=w)


def _draw_close(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, w: int) -> None:
    s = r * 0.56
    d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=color, width=w)
    d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color, width=w)


def _draw_speaker(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    color,
    w: int,
    *,
    muted: bool,
) -> None:
    """Speaker cone plus either two sound arcs or the mute cross."""
    left = cx - r * 0.62
    throat = cx - r * 0.18
    body = r * 0.26
    mouth = r * 0.52
    d.polygon(
        [
            (left, cy - body),
            (throat, cy - body),
            (throat + r * 0.34, cy - mouth),
            (throat + r * 0.34, cy + mouth),
            (throat, cy + body),
            (left, cy + body),
        ],
        outline=color,
        width=w,
    )
    if muted:
        s = r * 0.26
        mx = cx + r * 0.50
        d.line([(mx - s, cy - s), (mx + s, cy + s)], fill=color, width=w)
        d.line([(mx - s, cy + s), (mx + s, cy - s)], fill=color, width=w)
        return
    for scale in (0.32, 0.60):
        span = r * scale
        d.arc(
            [cx + r * 0.24 - span, cy - span, cx + r * 0.24 + span, cy + span],
            start=-55,
            end=55,
            fill=color,
            width=w,
        )


_Rgb = tuple[int, int, int]


def _disc_colors(action: str, state: ControlState) -> tuple[_Rgb, _Rgb, _Rgb]:
    """(fill, border, icon) for one disc in the given state."""
    hovered = state.hovered == action
    if action == "mic" and state.active:
        return BTN_BG_ON, BTN_BORDER_ON, BTN_ICON_ON
    if action == "speaker" and state.speaker_muted:
        return BTN_BG_OFF, BTN_BORDER_OFF, BTN_ICON_OFF
    if action == "attach" and not state.can_attach:
        return BTN_BG, BTN_BORDER, BTN_ICON_DISABLED
    if hovered:
        icon = BTN_ICON_OFF if action == "close" else BTN_ICON_HOVER
        return BTN_BG_HOVER, BTN_BORDER, icon
    return BTN_BG, BTN_BORDER, BTN_ICON


def _binary_disc_mask(diameter: int) -> Image.Image:
    """Aliased disc mask. Binary on purpose — see the module docstring."""
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter - 1, diameter - 1], fill=255)
    return mask


_MASK_CACHE: dict[int, Image.Image] = {}


def _disc_mask(diameter: int) -> Image.Image:
    mask = _MASK_CACHE.get(diameter)
    if mask is None:
        mask = _binary_disc_mask(diameter)
        _MASK_CACHE[diameter] = mask
    return mask


#: How far the clip leans. A paper clip drawn bolt upright reads as a safety
#: pin; the tilt is what makes it recognisable at this size.
_CLIP_TILT_DEG = 35.0


def _render_disc(action: str, state: ControlState) -> Image.Image:
    """One disc as an opaque square; the caller applies the circular mask."""
    fill, border, icon = _disc_colors(action, state)
    size = BUTTON_SIZE * _SS
    layer = Image.new("RGB", (size, size), fill)
    d = ImageDraw.Draw(layer)
    stroke = max(1, round(1.0 * _SS))
    inset = stroke
    d.ellipse(
        [inset, inset, size - 1 - inset, size - 1 - inset],
        outline=border,
        width=stroke,
    )
    centre = size / 2.0
    radius = size * 0.30
    glyph_w = max(1, round(1.35 * _SS))

    # The glyph goes on its own transparent layer so it can be rotated before
    # it meets the disc. Drawing straight onto the disc would make a tilted
    # glyph impossible without tilting the disc with it.
    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glyph)
    colour = (*icon, 255)
    if action == "attach":
        _draw_paperclip(gd, centre, centre, radius, colour, glyph_w)
        glyph = glyph.rotate(
            _CLIP_TILT_DEG, resample=Image.Resampling.BICUBIC, center=(centre, centre)
        )
    elif action == "mic":
        # The in-app bubble shows a live mic while a conversation runs and a
        # slashed one at rest, because the button STARTS the conversation. The
        # desktop row says the same thing with the same glyph.
        _draw_mic(gd, centre, centre, radius, colour, glyph_w, slashed=not state.active)
    elif action == "close":
        _draw_close(gd, centre, centre, radius, colour, glyph_w)
    else:
        _draw_speaker(gd, centre, centre, radius, colour, glyph_w, muted=state.speaker_muted)
    layer.paste(glyph, (0, 0), glyph)
    return layer.resize((BUTTON_SIZE, BUTTON_SIZE), Image.Resampling.LANCZOS)


def render_row(
    state: ControlState,
    *,
    color_key: tuple[int, int, int] = (255, 0, 255),
    actions: Sequence[str] = ACTIONS,
) -> Image.Image:
    """The whole row as a colour-keyed RGB frame, ready for a layered window."""
    width, height = row_size(len(actions))
    frame = Image.new("RGB", (width, height), color_key)
    mask = _disc_mask(BUTTON_SIZE)
    top = ROW_PADDING
    for action, cx in zip(actions, button_centers(len(actions)), strict=False):
        disc = _render_disc(action, state)
        frame.paste(disc, (int(round(cx - BUTTON_SIZE / 2.0)), top), mask)
    return frame


# --- The pet's control strip ------------------------------------------------
# The desktop pet (``docs/pets.md``) carries its controls in a different shape:
# a pen disc on its own, then ONE pill holding microphone mute, the talk orb and
# the speaker, separated by thin dividers. Same hard-edge rules as the row
# above: every silhouette that meets the colour key goes through a binary mask,
# every glyph is drawn at 4x and downscaled INSIDE its opaque surface.

#: The strip's actions, left to right. The voice orb's ``ACTIONS`` stay as they
#: are; the two layouts never share a hit-test.
PET_ACTIONS: tuple[str, ...] = ("compose", "mic_mute", "orb", "speaker")

#: Unscaled geometry, in pixels at 100 % display scaling.
PET_SLOT = 28
PET_PEN_GAP = 6
PET_DIVIDER_W = 1
PET_PILL_INSET = 4
PET_STRIP_PADDING = 4
#: Vertical distance from the figure's bottom edge to the strip.
PET_STRIP_GAP_FROM_FIGURE = 4
#: How many steps the orb's pulse is quantised into. A handful is enough to
#: read as "breathing with the voice" and bounds how often the strip repaints.
PET_LEVEL_STEPS = 6

#: The talk orb's colours: a small bluish sphere, brighter while engaged.
PET_ORB_REST = (74, 124, 230)
PET_ORB_ACTIVE = (98, 152, 255)
PET_ORB_HIGHLIGHT = (170, 200, 255)
#: The divider between pill slots — a shade above the border so it reads.
PET_DIVIDER = (44, 44, 44)


def _spx(value: float, scale: float) -> int:
    """One unscaled length at ``scale``, never below one pixel."""
    return max(1, int(round(value * max(0.1, float(scale)))))


@dataclass(frozen=True)
class PetStripLayout:
    """Where everything sits inside the strip window, at one display scale."""

    width: int
    height: int
    #: The pen disc: centre and radius.
    pen: tuple[float, float, float]
    #: The pill's bounding box ``(x0, y0, x1, y1)``, x1/y1 exclusive.
    pill: tuple[int, int, int, int]
    #: Each pill action's horizontal span ``(x0, x1)`` inside the window.
    slots: tuple[tuple[str, int, int], ...]
    #: Divider x positions (their left edge) inside the window.
    dividers: tuple[int, ...]


@functools.lru_cache(maxsize=16)
def pet_strip_layout(scale: float = 1.0) -> PetStripLayout:
    """The strip's geometry at ``scale`` (the monitor's DPI ratio)."""
    pad = _spx(PET_STRIP_PADDING, scale)
    slot = _spx(PET_SLOT, scale)
    gap = _spx(PET_PEN_GAP, scale)
    divider = _spx(PET_DIVIDER_W, scale)
    inset = _spx(PET_PILL_INSET, scale)
    pen_r = slot / 2.0
    pen = (pad + pen_r, pad + pen_r, pen_r)
    pill_x0 = pad + slot + gap
    pill_w = 2 * inset + 3 * slot + 2 * divider
    pill = (pill_x0, pad, pill_x0 + pill_w, pad + slot)
    slots: list[tuple[str, int, int]] = []
    dividers: list[int] = []
    x = pill_x0 + inset
    for index, action in enumerate(PET_ACTIONS[1:]):
        slots.append((action, x, x + slot))
        x += slot
        if index < 2:
            dividers.append(x)
            x += divider
    width = pill[2] + pad
    height = slot + 2 * pad
    return PetStripLayout(
        width=width,
        height=height,
        pen=pen,
        pill=pill,
        slots=tuple(slots),
        dividers=tuple(dividers),
    )


def pet_strip_size(scale: float = 1.0) -> tuple[int, int]:
    """Pixel size of the pet strip window at ``scale``."""
    layout = pet_strip_layout(scale)
    return layout.width, layout.height


def _inside_stadium(x: float, y: float, box: tuple[int, int, int, int]) -> bool:
    """Is ``(x, y)`` inside the pill (a rectangle with fully rounded ends)?"""
    x0, y0, x1, y1 = box
    r = (y1 - y0) / 2.0
    cy = y0 + r
    if y < y0 or y >= y1:
        return False
    if x0 + r <= x <= x1 - r:
        return True
    cx = x0 + r if x < x0 + r else x1 - r
    return math.hypot(x - cx, y - cy) <= r


def pet_hit_test(x: float, y: float, scale: float = 1.0) -> str | None:
    """Which pet action a click at ``(x, y)`` lands on, or ``None``.

    Outside the pen disc and the pill is nothing — that empty area is where the
    user grabs the strip to drag the pet (the only handle when the pet is
    "None"). Inside the pill every pixel belongs to a slot: a divider resolves
    to the nearer neighbour instead of eating the click.
    """
    layout = pet_strip_layout(scale)
    pcx, pcy, pr = layout.pen
    if math.hypot(x - pcx, y - pcy) <= pr:
        return "compose"
    if not _inside_stadium(x, y, layout.pill):
        return None
    best: str | None = None
    best_distance = math.inf
    for action, x0, x1 in layout.slots:
        if x0 <= x < x1:
            return action
        distance = min(abs(x - x0), abs(x - x1))
        if distance < best_distance:
            best, best_distance = action, distance
    return best


def quantize_level(level: float | None) -> int:
    """A 0..1 audio level as one of ``PET_LEVEL_STEPS + 1`` pulse steps."""
    if level is None:
        return 0
    try:
        value = float(level)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    value = max(0.0, min(1.0, value))
    return int(round(value * PET_LEVEL_STEPS))


@dataclass(frozen=True)
class PetStripState:
    """Everything the pet strip needs to paint itself truthfully."""

    #: Jarvis's microphone is muted (``VoiceMuteChanged``).
    mic_muted: bool = False
    #: The assistant's voice is muted for this session.
    speaker_muted: bool = False
    #: A conversation is running — the orb hangs up instead of starting one.
    active: bool = False
    #: The orb's pulse step (``quantize_level``); only drawn while ``active``.
    level: int = 0
    #: Which control the pointer is over, if any.
    hovered: str | None = None


def _draw_pen(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, w: int) -> None:
    """A pencil on the diagonal, tip at the bottom left — "write something"."""
    dx, dy = math.cos(-math.pi / 4), math.sin(-math.pi / 4)
    nx, ny = -dy, dx
    half = r * 0.17

    def p(u: float, v: float) -> tuple[float, float]:
        return (cx + u * r * dx + v * nx, cy + u * r * dy + v * ny)

    d.polygon(
        [p(0.70, -half), p(0.70, half), p(-0.45, half), p(-0.78, 0.0), p(-0.45, -half)],
        outline=color,
        width=w,
    )
    # The ferrule: where the eraser meets the body.
    d.line([p(0.42, -half), p(0.42, half)], fill=color, width=w)


def _draw_orb(
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    *,
    active: bool,
    level: int,
) -> None:
    """The talk orb: a small sphere that swells with the voice while engaged."""
    pulse = (level / PET_LEVEL_STEPS) if active else 0.0
    radius = r * (0.46 + 0.30 * pulse)
    body = PET_ORB_ACTIVE if active else PET_ORB_REST
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=body)
    shine = radius * 0.38
    sx, sy = cx - radius * 0.32, cy - radius * 0.32
    d.ellipse([sx - shine, sy - shine, sx + shine, sy + shine], fill=PET_ORB_HIGHLIGHT)


def _slash(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color, w: int) -> None:
    s = r * 0.74
    d.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color, width=w)


def _pet_icon_color(action: str, state: PetStripState) -> _Rgb:
    if state.hovered == action:
        return BTN_ICON_HOVER
    return BTN_ICON


def _render_pen_disc(state: PetStripState, diameter: int) -> Image.Image:
    size = diameter * _SS
    hovered = state.hovered == "compose"
    layer = Image.new("RGB", (size, size), BTN_BG_HOVER if hovered else BTN_BG)
    d = ImageDraw.Draw(layer)
    stroke = max(1, round(1.0 * _SS))
    d.ellipse(
        [stroke, stroke, size - 1 - stroke, size - 1 - stroke], outline=BTN_BORDER, width=stroke
    )
    glyph_w = max(1, round(1.35 * _SS))
    _draw_pen(d, size / 2.0, size / 2.0, size * 0.30, _pet_icon_color("compose", state), glyph_w)
    return layer.resize((diameter, diameter), Image.Resampling.LANCZOS)


def _render_pill(state: PetStripState, layout: PetStripLayout) -> Image.Image:
    x0, y0, x1, y1 = layout.pill
    width, height = x1 - x0, y1 - y0
    w_ss, h_ss = width * _SS, height * _SS
    layer = Image.new("RGB", (w_ss, h_ss), BTN_BG)
    d = ImageDraw.Draw(layer)
    stroke = max(1, round(1.0 * _SS))
    glyph_w = max(1, round(1.35 * _SS))
    for action, sx0, sx1 in layout.slots:
        if state.hovered == action:
            d.rectangle([(sx0 - x0) * _SS, 0, (sx1 - x0) * _SS - 1, h_ss - 1], fill=BTN_BG_HOVER)
    for dx, (_action, next_x0, _next_x1) in zip(layout.dividers, layout.slots[1:], strict=False):
        d.rectangle(
            [(dx - x0) * _SS, h_ss * 0.22, (next_x0 - x0) * _SS - 1, h_ss * 0.78],
            fill=PET_DIVIDER,
        )
    d.rounded_rectangle(
        [stroke, stroke, w_ss - 1 - stroke, h_ss - 1 - stroke],
        radius=(h_ss - 2 * stroke) / 2.0,
        outline=BTN_BORDER,
        width=stroke,
    )
    cy = h_ss / 2.0
    for action, sx0, sx1 in layout.slots:
        cx = ((sx0 + sx1) / 2.0 - x0) * _SS
        radius = (sx1 - sx0) * _SS * 0.30
        colour = _pet_icon_color(action, state)
        if action == "mic_mute":
            _draw_mic(d, cx, cy, radius, colour, glyph_w, slashed=False)
            if state.mic_muted:
                _slash(d, cx, cy, radius, BTN_ICON_OFF, glyph_w)
        elif action == "orb":
            _draw_orb(d, cx, cy, radius * 1.4, active=state.active, level=state.level)
        else:
            _draw_speaker(d, cx, cy, radius, colour, glyph_w, muted=False)
            if state.speaker_muted:
                _slash(d, cx, cy, radius, BTN_ICON_OFF, glyph_w)
    return layer.resize((width, height), Image.Resampling.LANCZOS)


def _binary_stadium_mask(width: int, height: int) -> Image.Image:
    """Aliased pill mask. Binary on purpose — see the module docstring."""
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=(height - 1) / 2.0, fill=255
    )
    return mask


@functools.lru_cache(maxsize=96)
def render_pet_strip(
    state: PetStripState,
    scale: float = 1.0,
    color_key: tuple[int, int, int] = (255, 0, 255),
) -> Image.Image:
    """The pet strip as a colour-keyed RGB frame, ready for a layered window.

    Cached: the state space is small (a few flags, a hovered action and a
    quantised pulse step), so a strip at rest costs one render per look and a
    pulsing orb cycles through a handful of cached frames. Callers must not
    mutate the returned image.
    """
    layout = pet_strip_layout(scale)
    frame = Image.new("RGB", (layout.width, layout.height), color_key)
    pcx, pcy, pr = layout.pen
    diameter = int(round(pr * 2))
    pen = _render_pen_disc(state, diameter)
    frame.paste(pen, (int(round(pcx - pr)), int(round(pcy - pr))), _disc_mask(diameter))
    x0, y0, x1, y1 = layout.pill
    pill = _render_pill(state, layout)
    frame.paste(pill, (x0, y0), _binary_stadium_mask(x1 - x0, y1 - y0))
    return frame


def toggle_speaker_mute(source: str = "orb") -> bool | None:
    """Mute / unmute the assistant's voice for this session. Returns the new state.

    Session-only by design, exactly like the in-app speaker button: a mute the
    user forgot about must not survive a restart and leave Jarvis mysteriously
    silent tomorrow. Nothing is written to ``jarvis.toml``.

    ``source`` names the control for the pipeline's ``VoiceSpeakerMuteChanged``
    broadcast (``"orb"``, ``"pet"``).

    Returns ``None`` when there is no live pipeline to talk to (a headless or
    still-booting host), so the caller can leave the icon alone instead of
    lying about a mute that never happened.
    """
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline
    except Exception:  # noqa: BLE001 — importable everywhere in practice
        return None
    pipeline = get_speech_pipeline()
    if pipeline is None:
        return None
    setter = getattr(pipeline, "set_tts_volume", None)
    if not callable(setter):
        return None
    # Read-modify-write under one lock: the Tk thread (this disc) and a REST
    # worker (the in-app button) can toggle at the same moment, and two
    # interleaved reads would both "mute" and lose the remembered volume.
    with _SPEAKER_TOGGLE_LOCK:
        current = _current_tts_volume(pipeline)
        if current > 0.0:
            _remember_volume(pipeline, current)
            _apply_volume(setter, 0.0, source)
            return True
        _apply_volume(setter, _remembered_volume(pipeline), source)
        return False


def _apply_volume(setter: Callable[..., object], volume: float, source: str) -> None:
    """Call ``set_tts_volume`` with ``source`` when the pipeline takes one."""
    try:
        params = inspect.signature(setter).parameters
    except (TypeError, ValueError):  # a builtin or C callable: no signature
        params = {}
    takes_source = "source" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if takes_source:
        setter(volume, source=source)
    else:
        setter(volume)


#: Where the pre-mute volume is parked, keyed per pipeline instance so a
#: restarted pipeline cannot inherit a stale value.
_PRE_MUTE_VOLUME: dict[int, float] = {}

#: Serialises :func:`toggle_speaker_mute` across the Tk and REST threads.
_SPEAKER_TOGGLE_LOCK = threading.Lock()


def _current_tts_volume(pipeline: object) -> float:
    getter = getattr(pipeline, "get_tts_volume", None)
    if callable(getter):
        try:
            return max(0.0, min(1.0, float(getter())))
        except Exception:  # noqa: BLE001 — a refusing getter is not a failure
            logging.getLogger("jarvis.orb").debug(
                "pipeline.get_tts_volume raised; probing the attribute instead",
                exc_info=True,
            )
    for attribute in ("_tts_volume", "tts_volume"):
        value = getattr(pipeline, attribute, None)
        if isinstance(value, int | float):
            return max(0.0, min(1.0, float(value)))
    # Unknown volume — treat as audible, same assumption the in-app button
    # makes when its GET fails. The first click then simply mutes.
    return 1.0


def _remember_volume(pipeline: object, volume: float) -> None:
    _PRE_MUTE_VOLUME[id(pipeline)] = volume


def _remembered_volume(pipeline: object) -> float:
    """The volume to unmute to.

    The value parked by this toggle wins. Without one (the voice was silenced
    elsewhere, e.g. the in-app slider at 0) the configured ``[tts].volume``
    is restored, so unmuting never jumps to full volume for someone who keeps
    the voice quiet; only a configured 0 falls back to 1.0.
    """
    remembered = _PRE_MUTE_VOLUME.get(id(pipeline))
    if remembered is not None and remembered > 0.0:
        return remembered
    config = getattr(pipeline, "_config", None)
    configured = getattr(getattr(config, "tts", None), "volume", None)
    if isinstance(configured, int | float) and configured > 0.0:
        return max(0.0, min(1.0, float(configured)))
    return 1.0


def speaker_is_muted() -> bool:
    """Best-effort read of "the assistant's voice is currently silenced"."""
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline
    except Exception:  # noqa: BLE001
        return False
    pipeline = get_speech_pipeline()
    if pipeline is None:
        return False
    return _current_tts_volume(pipeline) <= 0.0


def pick_and_dispatch_files(parent: object, on_error: Callable[[str], None] | None = None) -> int:
    """Open a file chooser and hand the picks to the conversation.

    Same destination as dropping a file onto the orb — ``drop_bridge`` — so the
    macOS companion process needs no new IPC of its own: its drop forwarding
    already carries this to the parent where the brain lives.

    Returns the number of files handed over (0 when the user cancelled).
    """
    # Same guard as ``OrbOverlay.start``: a modal file chooser opened by a test
    # run blocks the whole suite until somebody notices and closes it by hand.
    # Dead code in the live app — pytest is never imported there.
    if "pytest" in sys.modules and not os.environ.get("JARVIS_GUI_TESTS"):
        return 0
    try:
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 — no Tk, no dialog
        if on_error is not None:
            on_error(f"file dialog unavailable: {exc}")
        return 0
    try:
        picked = filedialog.askopenfilenames(parent=parent, title="Attach to the conversation")
    except Exception as exc:  # noqa: BLE001 — a cancelled/broken dialog is not fatal
        if on_error is not None:
            on_error(f"file dialog failed: {exc}")
        return 0
    paths = [str(path) for path in (picked or ()) if str(path).strip()]
    if not paths:
        return 0
    try:
        from jarvis.overlay.drop_bridge import dispatch_drop

        dispatch_drop(paths, "")
    except Exception as exc:  # noqa: BLE001
        if on_error is not None:
            on_error(f"attach dispatch failed: {exc}")
        return 0
    return len(paths)
