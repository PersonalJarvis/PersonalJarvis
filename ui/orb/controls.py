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
# The desktop pet (``docs/pets.md``) carries its controls in the shape the
# Codex companion made familiar: a pen in its own filled disc, then ONE filled
# pill holding microphone mute, the talk orb and the speaker, with thin
# low-contrast dividers. Same hard-edge rules as the row above: every
# silhouette that meets the colour key goes through a binary mask, every glyph
# and the orb's gradient are drawn at 4x and downscaled INSIDE the opaque
# surface, so their antialiasing never meets the key.

#: The strip's actions, left to right. The voice orb's ``ACTIONS`` stay as they
#: are; the two layouts never share a hit-test.
PET_ACTIONS: tuple[str, ...] = ("compose", "mic_mute", "orb", "speaker")

#: Unscaled geometry, in logical pixels at 100 % display scaling and
#: ``pet_scale`` 1.0 — sized against the companion figure (about 180 px), so the
#: strip is roughly 0.3 x the figure's width tall, as in the Codex app.
#: ``PET_SLOT`` is the pill's height and the pen disc's diameter.
PET_SLOT = 50
PET_ICON_SLOT = 46
PET_ORB_SLOT = 52
PET_PEN_GAP = 8
PET_DIVIDER_W = 1
PET_PILL_INSET = 7
PET_STRIP_PADDING = 2
#: Vertical distance from the figure's bottom edge to the strip.
PET_STRIP_GAP_FROM_FIGURE = 10
#: The glyphs' box and stroke, as a share of ``PET_SLOT`` (a 21 px icon with a
#: 2 px stroke at 100 %).
PET_ICON_BOX = 0.42
PET_ICON_STROKE = 2.0
#: How many steps the indicator's voice level is quantised into. A handful is
#: enough to read as "moving with the voice" and bounds the cached frames.
PET_LEVEL_STEPS = 6

#: The talk control is three upright strokes — the Jarvis bar's equalizer
#: vocabulary, cut down to three. Geometry as shares of the pill's height:
#: stroke centre spacing, stroke half-width, and the shortest / tallest stroke.
PET_INDICATOR_BARS = 3
PET_INDICATOR_SPACING = 0.24
PET_INDICATOR_HALF_W = 0.068
PET_INDICATOR_MIN_H = 0.18
PET_INDICATOR_MAX_H = 0.64
#: At rest the strokes stand still and dimmed; the middle one a little taller,
#: so the three read as one control and not as three loose dots.
PET_INDICATOR_REST_H: tuple[float, ...] = (0.22, 0.32, 0.22)
PET_INDICATOR_REST_GLOW = 0.78
#: Animation steps per cycle. Voice: the strokes wobble around the level in
#: ``PET_VOICE_PHASES`` steps of ``PET_VOICE_STEP_S``. Thinking: a highlight
#: travels across the three in ``PET_THINK_PHASES`` steps per pass of
#: ``PET_THINK_PERIOD_S`` (the Jarvis bar's sweep period).
PET_VOICE_PHASES = 8
PET_VOICE_STEP_S = 0.1
PET_THINK_PHASES = 12
PET_THINK_PERIOD_S = 1.05
#: Sweep shape: gaussian width (row fractions), the unlit and the lit level.
PET_THINK_WIDTH = 0.2
PET_THINK_BASE_V = 0.21
PET_THINK_PEAK_V = 0.93
PET_THINK_DIM = 0.5

#: What the indicator is showing. ``rest``: nothing running. ``voice``: the
#: microphone or Jarvis's voice is live and the strokes follow its level.
#: ``think``: work is in flight with no signal to measure, so a highlight
#: travels — motion, never a fake level.
PET_MOTIONS: tuple[str, ...] = ("rest", "voice", "think")

#: Fill of the pen disc and the pill: a blue-black only a breath away from a
#: dark desktop, so the controls read as glyphs floating in a faint shadow
#: rather than as solid buttons; a shade lighter under the pointer.
PET_FILL = (13, 17, 23)
PET_FILL_HOVER = (27, 32, 43)
#: Glyphs: near-white; a muted control turns red-ish.
PET_ICON = (232, 233, 238)
PET_ICON_MUTED = (248, 113, 113)
#: The divider between pill slots — barely above the fill, never a hard line.
PET_DIVIDER = (24, 28, 37)
#: The indicator strokes look like a bright sky: a light blue body with soft
#: white clouds drifting through it. Two clear colours that meet in feathered
#: edges — never one muddy average of both.
PET_INDICATOR_SKY = (126, 186, 255)
PET_INDICATOR_CLOUD = (255, 255, 255)
#: The clouds in one stroke, as (x, y, radius) in stroke units: x from -1
#: (left edge) to 1 (right edge), y from 0 (top) to 1 (bottom), radius in
#: stroke half-widths. One set per stroke, so the three never look stamped.
PET_CLOUD_PUFFS: tuple[tuple[tuple[float, float, float], ...], ...] = (
    ((-0.4, 0.16, 1.9), (0.7, 0.52, 1.5), (-0.6, 0.90, 1.7)),
    ((0.5, 0.10, 1.7), (-0.6, 0.46, 1.8), (0.6, 0.86, 1.5)),
    ((-0.5, 0.30, 1.6), (0.6, 0.66, 1.9), (-0.2, 1.02, 1.4)),
)
#: How far a cloud's edge is feathered, as a share of its radius: a small
#: white core, then a long soft fade into the sky.
PET_CLOUD_SOFTNESS = 0.8
#: A dimmed stroke sinks toward a deep night blue, never toward grey, so a
#: resting or unlit stroke still reads as sky.
PET_INDICATOR_NIGHT = (24, 44, 84)


def _spx(value: float, scale: float) -> int:
    """One unscaled length at ``scale``, never below one pixel."""
    return max(1, int(round(value * max(0.1, float(scale)))))


@dataclass(frozen=True)
class PetStripLayout:
    """Where everything sits inside the strip window, at one scale."""

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
    """The strip's geometry at ``scale`` (DPI ratio times ``pet_scale``)."""
    pad = _spx(PET_STRIP_PADDING, scale)
    slot = _spx(PET_SLOT, scale)
    gap = _spx(PET_PEN_GAP, scale)
    divider = _spx(PET_DIVIDER_W, scale)
    inset = _spx(PET_PILL_INSET, scale)
    widths = {
        "mic_mute": _spx(PET_ICON_SLOT, scale),
        "orb": _spx(PET_ORB_SLOT, scale),
        "speaker": _spx(PET_ICON_SLOT, scale),
    }
    pen_r = slot / 2.0
    pen = (pad + pen_r, pad + pen_r, pen_r)
    pill_x0 = pad + slot + gap
    pill_w = 2 * inset + sum(widths.values()) + 2 * divider
    pill = (pill_x0, pad, pill_x0 + pill_w, pad + slot)
    slots: list[tuple[str, int, int]] = []
    dividers: list[int] = []
    x = pill_x0 + inset
    for index, action in enumerate(PET_ACTIONS[1:]):
        slots.append((action, x, x + widths[action]))
        x += widths[action]
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
    #: A conversation is running — the talk control hangs up instead of
    #: starting one.
    active: bool = False
    #: The voice level step (``quantize_level``); drawn only in ``voice``.
    level: int = 0
    #: What the indicator shows (``PET_MOTIONS``) and its animation step.
    motion: str = "rest"
    phase: int = 0
    #: Which control the pointer is over, if any.
    hovered: str | None = None


# -- glyphs ------------------------------------------------------------------
# Drawn on a 24-unit grid (the line-icon convention the app's lucide icons use)
# mapped onto a square box centred on the slot, with round caps and joins.


class _Pen:
    """Maps 24-unit icon coordinates onto a supersampled layer."""

    def __init__(self, d: ImageDraw.ImageDraw, cx: float, cy: float, box: float, width: float):
        self.d = d
        self.cx = cx
        self.cy = cy
        self.unit = box / 24.0
        self.width = max(1, int(round(width)))

    def p(self, u: float, v: float) -> tuple[float, float]:
        return (self.cx + (u - 12.0) * self.unit, self.cy + (v - 12.0) * self.unit)

    def cap(self, point: tuple[float, float], color: _Rgb) -> None:
        r = self.width / 2.0
        x, y = point
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def lines(self, points: Sequence[tuple[float, float]], color: _Rgb) -> None:
        mapped = [self.p(u, v) for u, v in points]
        self.d.line(mapped, fill=color, width=self.width, joint="curve")
        self.cap(mapped[0], color)
        self.cap(mapped[-1], color)

    def arc(self, cu: float, cv: float, r: float, start: float, end: float, color: _Rgb) -> None:
        x, y = self.p(cu, cv)
        rr = r * self.unit
        self.d.arc([x - rr, y - rr, x + rr, y + rr], start, end, fill=color, width=self.width)
        for angle in (start, end):
            a = math.radians(angle)
            self.cap((x + rr * math.cos(a), y + rr * math.sin(a)), color)


def _glyph_compose(pen: _Pen, color: _Rgb) -> None:
    """A square with a pencil writing into its corner — "new message"."""
    pen.lines([(12, 3), (5, 3)], color)
    pen.arc(5, 5, 2, 180, 270, color)
    pen.lines([(3, 5), (3, 19)], color)
    pen.arc(5, 19, 2, 90, 180, color)
    pen.lines([(5, 21), (19, 21)], color)
    pen.arc(19, 19, 2, 0, 90, color)
    pen.lines([(21, 19), (21, 12)], color)
    pen.lines(
        [(18.4, 2.6), (21.4, 5.6), (12.4, 14.6), (8.6, 15.4), (9.4, 11.6), (18.4, 2.6)], color
    )


def _glyph_mic(pen: _Pen, color: _Rgb) -> None:
    pen.arc(12, 5, 3, 180, 360, color)
    pen.lines([(9, 5), (9, 12)], color)
    pen.lines([(15, 5), (15, 12)], color)
    pen.arc(12, 12, 3, 0, 180, color)
    pen.lines([(19, 10), (19, 12)], color)
    pen.arc(12, 12, 7, 0, 180, color)
    pen.lines([(5, 12), (5, 10)], color)
    pen.lines([(12, 19), (12, 22)], color)


def _glyph_speaker(pen: _Pen, color: _Rgb, *, waves: bool) -> None:
    pen.lines([(11, 5), (6, 9), (2, 9), (2, 15), (6, 15), (11, 19), (11, 5)], color)
    if waves:
        pen.arc(12, 12, 5, -45, 45, color)
        pen.arc(12, 12, 10, -45, 45, color)


def _glyph_slash(pen: _Pen, color: _Rgb) -> None:
    pen.lines([(3, 3), (21, 21)], color)


def _draw_glyph(
    action: str,
    d: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    box: float,
    stroke: float,
    state: PetStripState,
) -> None:
    if action == "compose":
        _glyph_compose(_Pen(d, cx, cy, box, stroke), PET_ICON)
    elif action == "mic_mute":
        color = PET_ICON_MUTED if state.mic_muted else PET_ICON
        pen = _Pen(d, cx, cy, box, stroke)
        _glyph_mic(pen, color)
        if state.mic_muted:
            _glyph_slash(pen, color)
    elif action == "speaker":
        color = PET_ICON_MUTED if state.speaker_muted else PET_ICON
        pen = _Pen(d, cx, cy, box, stroke)
        _glyph_speaker(pen, color, waves=not state.speaker_muted)
        if state.speaker_muted:
            _glyph_slash(pen, color)


def _lerp(a: _Rgb, b: _Rgb, t: float) -> _Rgb:
    t = max(0.0, min(1.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def indicator_phase(motion: str, t: float) -> int:
    """The animation step for ``motion`` at clock time ``t`` (0 at rest)."""
    if motion == "voice":
        return int(t / PET_VOICE_STEP_S) % PET_VOICE_PHASES
    if motion == "think":
        cycle = (t % PET_THINK_PERIOD_S) / PET_THINK_PERIOD_S
        return int(cycle * PET_THINK_PHASES) % PET_THINK_PHASES
    return 0


def _sweep_gain(index: int, phase: int) -> float:
    """Brightness of stroke ``index`` under the thinking highlight.

    Positions sit on a ring (``index / bars``), so the highlight leaves on
    the right and comes back on the left without a jump.
    """
    pos = index / PET_INDICATOR_BARS
    head = (phase % PET_THINK_PHASES) / PET_THINK_PHASES
    d = abs(pos - head)
    d = min(d, 1.0 - d)
    return math.exp(-(d * d) / (2.0 * PET_THINK_WIDTH * PET_THINK_WIDTH))


def indicator_bars(state: PetStripState) -> list[tuple[float, float]]:
    """Each stroke as ``(height share of the pill, glow 0..1)``, left to right.

    Pure in the state, so every look is testable and cacheable.
    """
    lo, hi = PET_INDICATOR_MIN_H, PET_INDICATOR_MAX_H
    if state.motion == "voice":
        level = max(0.0, min(1.0, state.level / PET_LEVEL_STEPS))
        angle = 2.0 * math.pi * (state.phase % PET_VOICE_PHASES) / PET_VOICE_PHASES
        out = []
        for i in range(PET_INDICATOR_BARS):
            wobble = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(angle + i * 2.1))
            out.append((lo + (hi - lo) * level * wobble, 0.62 + 0.38 * level))
        return out
    if state.motion == "think":
        out = []
        for i in range(PET_INDICATOR_BARS):
            g = _sweep_gain(i, state.phase)
            v = PET_THINK_BASE_V + (PET_THINK_PEAK_V - PET_THINK_BASE_V) * g
            out.append((lo + (hi - lo) * v, PET_THINK_DIM + (1.0 - PET_THINK_DIM) * g))
        return out
    return [(h, PET_INDICATOR_REST_GLOW) for h in PET_INDICATOR_REST_H]


def _cloud_cover(u: float, v: float, w: float, h: float, puffs) -> float:
    """How white the sky is at ``(u, v)`` inside a stroke (0 sky, 1 cloud).

    ``u`` runs -1..1 across the stroke and ``v`` 0..1 down it; ``w`` and ``h``
    are the stroke's half-width and height in pixels, so the puffs stay round
    on tall and short strokes alike.
    """
    cover = 0.0
    for px, py, pr in puffs:
        dx = (u - px) * w
        dy = (v - py) * h
        r = pr * w
        dist = math.sqrt(dx * dx + dy * dy) / r
        if dist >= 1.0:
            continue
        core = 1.0 - PET_CLOUD_SOFTNESS
        # Solid in the core, a smoothstep feather over the rim.
        if dist <= core:
            a = 1.0
        else:
            f = 1.0 - (dist - core) / PET_CLOUD_SOFTNESS
            a = f * f * (3.0 - 2.0 * f)
        # Overlapping puffs build up like real cloud, never past pure white.
        cover = 1.0 - (1.0 - cover) * (1.0 - a)
    return cover


@functools.lru_cache(maxsize=12)
def _cloud_texture(index: int, width: int, height: int) -> Image.Image:
    """The sky-and-clouds fill of stroke ``index`` at its tallest, as RGB.

    Painted once per stroke and size (the per-pixel feathering is the costly
    part); every frame then only crops it to the stroke's current height.
    Cropping from the middle keeps the clouds the same size whatever the
    level, so they read as a sky seen through the stroke, not as a stretched
    pattern.
    """
    tex = Image.new("RGB", (max(1, width), max(1, height)), PET_INDICATOR_SKY)
    half_w = width / 2.0
    puffs = PET_CLOUD_PUFFS[index % len(PET_CLOUD_PUFFS)]
    pixels = tex.load()
    for y in range(height):
        v = (y + 0.5) / height
        for x in range(width):
            u = (x + 0.5 - half_w) / half_w
            cover = _cloud_cover(u, v, half_w, height, puffs)
            if cover > 0.0:
                pixels[x, y] = _lerp(PET_INDICATOR_SKY, PET_INDICATOR_CLOUD, cover)
    return tex


@functools.lru_cache(maxsize=64)
def _stroke_mask(width: int, height: int) -> Image.Image:
    """A soft-edged rounded stroke; the layer is downscaled, so it ends smooth."""
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=(width - 1) / 2.0, fill=255
    )
    return mask


def _draw_indicator(
    layer: Image.Image, cx: float, cy: float, pill_h: float, state: PetStripState
) -> None:
    """Three rounded strokes of light-blue sky with white clouds, dimmed by glow.

    Drawn on the supersampled pill layer, so the clouds keep their feathered
    edges after the downscale. The frame is RGB (the colour key needs it), so
    "dim" is a mix toward a night blue, which keeps a resting stroke reading
    as sky instead of turning grey.
    """
    width = max(2, int(round(2.0 * pill_h * PET_INDICATOR_HALF_W)))
    tallest = max(width, int(round(pill_h * PET_INDICATOR_MAX_H)))
    step = pill_h * PET_INDICATOR_SPACING
    first = cx - step * (PET_INDICATOR_BARS - 1) / 2.0
    for i, (share, glow) in enumerate(indicator_bars(state)):
        h = min(tallest, max(width, int(round(pill_h * share))))
        tex = _cloud_texture(i, width, tallest)
        top = (tallest - h) // 2
        stroke = tex.crop((0, top, width, top + h))
        if glow < 1.0:
            night = Image.new("RGB", stroke.size, PET_INDICATOR_NIGHT)
            stroke = Image.blend(night, stroke, max(0.0, glow))
        x0 = int(round(first + i * step - width / 2.0))
        y0 = int(round(cy - h / 2.0))
        layer.paste(stroke, (x0, y0), _stroke_mask(width, h))


def _render_pen_disc(state: PetStripState, diameter: int, scale: float) -> Image.Image:
    size = diameter * _SS
    hovered = state.hovered == "compose"
    layer = Image.new("RGB", (size, size), PET_FILL_HOVER if hovered else PET_FILL)
    d = ImageDraw.Draw(layer)
    box = _spx(PET_SLOT, scale) * PET_ICON_BOX * _SS
    stroke = PET_ICON_STROKE * max(0.5, scale) * _SS
    _draw_glyph("compose", d, size / 2.0, size / 2.0, box, stroke, state)
    return layer.resize((diameter, diameter), Image.Resampling.LANCZOS)


def _render_pill(state: PetStripState, layout: PetStripLayout, scale: float) -> Image.Image:
    x0, y0, x1, y1 = layout.pill
    width, height = x1 - x0, y1 - y0
    w_ss, h_ss = width * _SS, height * _SS
    layer = Image.new("RGB", (w_ss, h_ss), PET_FILL)
    d = ImageDraw.Draw(layer)
    for action, sx0, sx1 in layout.slots:
        if state.hovered == action:
            # A soft round lift under the pointer, like the pen disc's.
            hx = ((sx0 + sx1) / 2.0 - x0) * _SS
            hr = h_ss * 0.46
            d.ellipse([hx - hr, h_ss / 2.0 - hr, hx + hr, h_ss / 2.0 + hr], fill=PET_FILL_HOVER)
    for dx, (_action, next_x0, _next_x1) in zip(layout.dividers, layout.slots[1:], strict=False):
        d.rectangle(
            [(dx - x0) * _SS, h_ss * 0.28, (next_x0 - x0) * _SS - 1, h_ss * 0.72],
            fill=PET_DIVIDER,
        )
    cy = h_ss / 2.0
    box = height * PET_ICON_BOX * _SS
    stroke = PET_ICON_STROKE * max(0.5, scale) * _SS
    for action, sx0, sx1 in layout.slots:
        cx = ((sx0 + sx1) / 2.0 - x0) * _SS
        if action == "orb":
            _draw_indicator(layer, cx, cy, h_ss, state)
        else:
            _draw_glyph(action, d, cx, cy, box, stroke, state)
    return layer.resize((width, height), Image.Resampling.LANCZOS)


def _binary_stadium_mask(width: int, height: int) -> Image.Image:
    """Aliased pill mask. Binary on purpose — see the module docstring."""
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=(height - 1) / 2.0, fill=255
    )
    return mask


@functools.lru_cache(maxsize=256)
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
    pen = _render_pen_disc(state, diameter, scale)
    frame.paste(pen, (int(round(pcx - pr)), int(round(pcy - pr))), _disc_mask(diameter))
    x0, y0, x1, y1 = layout.pill
    pill = _render_pill(state, layout, scale)
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
