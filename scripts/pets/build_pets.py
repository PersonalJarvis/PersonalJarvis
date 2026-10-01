#!/usr/bin/env python3
"""Generate the built-in desktop pets and the sprite-sheet template from pixel data.

Every pet is drawn procedurally into 48 x 48 cells: shape helpers build
masks, a shared shading pass gives every body the same light/dark rim, and a
per-state pose table moves the figure — a 1 px breath (the body above its
waist stretches and settles), a hop that squashes on landing, a shake. One row
per state in ``PET_STATES`` order, one ``pet.json`` per pet (``docs/pets.md``).

Two conventions the renderer relies on: the idle row's LAST cell is a blink,
declared as an accent (``accent_frames``/``accent_every``) so it plays every
few seconds instead of on every breath; and the talking row is ordered from a
closed mouth to the widest one, so the live voice level can pick the frame.

The output is deterministic: no randomness, no timestamps, a fixed PNG
encoder setting. ``tests/unit/ui/pets/test_builtin_pets.py`` regenerates
everything into a temp folder and compares it with the committed files, so
edit this script and re-run it instead of touching a PNG by hand.

Usage::

    python scripts/pets/build_pets.py              # write jarvis/ui/pets/{builtin,template}
    python scripts/pets/build_pets.py --out DIR    # write DIR/builtin and DIR/template
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Run from anywhere: import THIS checkout's jarvis, never an installed copy.
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402

from jarvis.ui.pets.states import (  # noqa: E402
    MAX_FRAMES_PER_STATE,
    ONE_SHOT_STATES,
    PET_FORMAT,
    PET_STATES,
)

CELL = 48
#: The vertical centre line of a cell lies between pixel columns 23 and 24.
CX = 24

Px = tuple[int, int]
RGBA = tuple[int, int, int, int]
Mask = set[Px]


def hexc(value: str) -> RGBA:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255)


# ---------------------------------------------------------------------------
# Shared palette: one outline and one set of effect colours for every pet.
# ---------------------------------------------------------------------------

OUTLINE = hexc("#1b1622")
FX_WHITE = hexc("#f7f7fb")
FX_DIM = hexc("#aeb6c8")
SPARK = hexc("#ffd23f")
SPARK_CORE = hexc("#fff8d6")
RED = hexc("#e5383b")
Z_BLUE = hexc("#a5c8ff")
#: Sound arcs are 1 px strokes; an outline around each would read as hatching
#: on a light desktop, so they are a mid blue that holds on dark AND light.
ARC = hexc("#3d9be0")
#: An arc on its way out: lighter, so the ripple fades instead of blinking off.
ARC_FADE = hexc("#93c8f0")
SWEAT = hexc("#9fd8ff")
SWEAT_SHINE = hexc("#e8f6ff")
BLUSH = hexc("#ff8fa3")
MOUTH_DARK = hexc("#3b1f2b")
TONGUE = hexc("#ff7a8a")


# ---------------------------------------------------------------------------
# Shapes. Every helper returns a set of (x, y) pixels in cell coordinates.
# ---------------------------------------------------------------------------


def ellipse(cx: float, cy: float, rx: float, ry: float) -> Mask:
    """Pixels whose centre lies inside the ellipse."""
    out: Mask = set()
    for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
        for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
            nx = (x + 0.5 - cx) / rx
            ny = (y + 0.5 - cy) / ry
            if nx * nx + ny * ny <= 1.0:
                out.add((x, y))
    return out


def rect(x0: int, y0: int, x1: int, y1: int) -> Mask:
    """Inclusive rectangle."""
    return {(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)}


def line(x0: int, y0: int, x1: int, y1: int) -> Mask:
    """Bresenham line, both ends included."""
    out: Mask = set()
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        out.add((x0, y0))
        if x0 == x1 and y0 == y1:
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def polyline(points: Sequence[Px]) -> Mask:
    out: Mask = set()
    for a, b in zip(points, points[1:], strict=False):
        out |= line(*a, *b)
    return out


def polygon(points: Sequence[tuple[float, float]]) -> Mask:
    """Pixels whose centre lies inside the polygon (even-odd rule)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    out: Mask = set()
    for y in range(int(min(ys)) - 1, int(max(ys)) + 2):
        for x in range(int(min(xs)) - 1, int(max(xs)) + 2):
            px, py = x + 0.5, y + 0.5
            inside = False
            for (ax, ay), (bx, by) in zip(points, [*points[1:], points[0]], strict=True):
                if (ay > py) != (by > py) and px < ax + (py - ay) * (bx - ax) / (by - ay):
                    inside = not inside
            if inside:
                out.add((x, y))
    return out


def shifted(pixels: Iterable[Px], dx: int, dy: int) -> Mask:
    return {(x + dx, y + dy) for x, y in pixels}


def mirrored(pixels: Iterable[Px], axis: int = CX) -> Mask:
    """Mirror across the vertical line between columns ``axis - 1`` and ``axis``."""
    return {(2 * axis - 1 - x, y) for x, y in pixels}


def from_rows(x: int, y: int, rows: Sequence[str], char: str = "#") -> Mask:
    """Pixels marked with ``char`` in ASCII ``rows`` placed at ``(x, y)``."""
    return {
        (x + col, y + row)
        for row, text in enumerate(rows)
        for col, mark in enumerate(text)
        if mark == char
    }


_N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def outer_ring(mask: Mask) -> Mask:
    """The 1 px outline around ``mask`` (4-neighbourhood, so corners stay round)."""
    return {(x + dx, y + dy) for x, y in mask for dx, dy in _N4} - mask


def eroded(mask: Mask, steps: int = 1) -> Mask:
    """``mask`` shrunk by ``steps`` pixels (4-neighbourhood)."""
    out = set(mask)
    for _ in range(steps):
        out = {(x, y) for x, y in out if all((x + dx, y + dy) in out for dx, dy in _N4)}
    return out


def thick(mask: Mask) -> Mask:
    """A 1 px stroke widened to 2 px (right and down)."""
    return mask | shifted(mask, 1, 0) | shifted(mask, 0, 1)


def rounded_rect(x0: int, y0: int, x1: int, y1: int, r: int = 2) -> Mask:
    """Inclusive rectangle with ``r``-step stair corners."""
    box = rect(x0, y0, x1, y1)
    for k in range(r):
        for j in range(r - k):
            box -= {(x0 + j, y0 + k), (x1 - j, y0 + k), (x0 + j, y1 - k), (x1 - j, y1 - k)}
    return box


def shade(
    mask: Mask,
    base: RGBA,
    *,
    light: RGBA | None = None,
    dark: RGBA | None = None,
    rim: RGBA | None = None,
    dark_depth: int = 2,
) -> dict[Px, RGBA]:
    """The shared shading pass.

    ``rim`` paints every inner edge pixel (dark bodies, so they read on a
    dark desktop); otherwise the top/left inner edge gets ``light`` and the
    bottom/right band ``dark_depth`` pixels deep gets ``dark``.
    """
    out: dict[Px, RGBA] = {}
    for x, y in mask:
        up = (x, y - 1) not in mask
        left = (x - 1, y) not in mask
        down = any((x, y + k) not in mask for k in range(1, dark_depth + 1))
        right = (x + 1, y) not in mask
        color = base
        if rim is not None and (up or left or right or (x, y + 1) not in mask):
            color = rim
        elif light is not None and (up or left):
            color = light
        elif dark is not None and (down or right):
            color = dark
        out[(x, y)] = color
    return out


# ---------------------------------------------------------------------------
# The frame canvas.
# ---------------------------------------------------------------------------


class Frame:
    """One 48 x 48 cell being painted, with a movable origin."""

    def __init__(self) -> None:
        self.px: dict[Px, RGBA] = {}
        self._dx = 0
        self._dy = 0

    @contextmanager
    def offset(self, dx: int, dy: int) -> Iterator[None]:
        saved = (self._dx, self._dy)
        self._dx += dx
        self._dy += dy
        try:
            yield
        finally:
            self._dx, self._dy = saved

    def paint(self, pixels: Iterable[Px], color: RGBA) -> None:
        for x, y in pixels:
            self.px[(x + self._dx, y + self._dy)] = color

    def paint_map(self, colors: Mapping[Px, RGBA]) -> None:
        for (x, y), color in colors.items():
            self.px[(x + self._dx, y + self._dy)] = color

    def erase(self, pixels: Iterable[Px]) -> None:
        for x, y in pixels:
            self.px.pop((x + self._dx, y + self._dy), None)

    def part(self, mask: Mask, fill: RGBA | Mapping[Px, RGBA], outline: RGBA = OUTLINE) -> None:
        """Outline ``mask`` over whatever is below, then fill it."""
        self.paint(outer_ring(mask), outline)
        if isinstance(fill, Mapping):
            self.paint_map(fill)
        else:
            self.paint(mask, fill)

    def glyph(self, x: int, y: int, rows: Sequence[str], palette: Mapping[str, RGBA]) -> None:
        """Paint an outlined ASCII glyph; ``.`` is transparent."""
        cells: dict[Px, RGBA] = {}
        for row, text in enumerate(rows):
            for col, mark in enumerate(text):
                if mark in palette:
                    cells[(x + col, y + row)] = palette[mark]
        self.paint(outer_ring(set(cells)), OUTLINE)
        self.paint_map(cells)

    def image(self) -> Image.Image:
        img = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
        for (x, y), color in self.px.items():
            if not (0 <= x < CELL and 0 <= y < CELL):
                raise ValueError(f"pixel {(x, y)} lies outside the {CELL} px cell")
            img.putpixel((x, y), color)
        return img


# ---------------------------------------------------------------------------
# Poses: the per-state motion every pet shares.
# ---------------------------------------------------------------------------

FRAME_COUNTS: dict[str, int] = {
    "idle": 8,
    "listening": 6,
    "thinking": 8,
    "talking": 4,
    "success": 8,
    "error": 6,
    "sleeping": 6,
    "working": 8,
    "searching": 8,
    "held": 6,
}
FPS: dict[str, int] = {
    "idle": 6,
    "listening": 8,
    "thinking": 8,
    "talking": 10,
    "success": 10,
    "error": 9,
    "sleeping": 3,
    "working": 8,
    "searching": 8,
    "held": 10,
}
#: The idle row's blink: its last cell, once every third breath.
IDLE_ACCENT = {"accent_frames": 1, "accent_every": 3}
#: One slow breath over the seven ordinary idle cells (1 = a pixel taller).
IDLE_BREATH = (0, 0, 1, 1, 1, 0, 0)
SLEEP_BREATH = (0, 0, 1, 1, 0, 0)
#: The success hop: a squash to push off, up, down, a squash to land.
HOP = (0, -2, -4, -5, -4, -2, 0, 0)
HOP_STRETCH = (-1, 1, 1, 0, 0, 1, -1, 0)
SHAKE = (-2, 2, -2, 2, -1, 0)
#: Closed to widest: the renderer picks one by the live voice level.
TALK_MOUTHS = ("closed", "half", "open", "wide")
#: Working: a small nod on every other keystroke.
WORK_BOB = (0, 0, 1, 0, 0, 0, 1, 0)
#: Searching: the magnifier sweeps from left to right and back, a full
#: swing over the row; ``SEARCH_EYES`` follows it.
SEARCH_SWEEP = (-9, -6, 0, 6, 9, 6, 0, -6)
SEARCH_EYES = tuple(
    "look_left" if x < -2 else "look_right" if x > 2 else "open" for x in SEARCH_SWEEP
)
#: Held by the mouse: dangling, swinging from side to side.
HELD_SWAY = (-1, 0, 1, 0, -1, 0)


@dataclass(frozen=True)
class Pose:
    state: str
    i: int
    dx: int = 0
    dy: int = 0
    eyes: str = "open"
    mouth: str = "rest"
    #: 1 = the body above its waist one pixel taller, -1 = one shorter.
    stretch: int = 0


def base_pose(state: str, i: int) -> Pose:
    if state == "idle":
        if i >= len(IDLE_BREATH):
            return Pose(state, i, eyes="closed")  # the blink (the accent cell)
        return Pose(state, i, stretch=IDLE_BREATH[i])
    if state == "listening":
        # Attentive: lifted and standing tall, eyes wide.
        return Pose(state, i, dy=-1, eyes="wide", stretch=1)
    if state == "thinking":
        early = i < 4
        return Pose(state, i, eyes="up_left" if early else "up_right", mouth="closed")
    if state == "talking":
        return Pose(state, i, dy=0 if i < 2 else -1, mouth=TALK_MOUTHS[i])
    if state == "success":
        return Pose(state, i, dy=HOP[i], eyes="happy", mouth="smile", stretch=HOP_STRETCH[i])
    if state == "error":
        return Pose(state, i, dx=SHAKE[i], eyes="x", mouth="frown")
    if state == "sleeping":
        return Pose(state, i, dy=1, eyes="sleep", mouth="closed", stretch=SLEEP_BREATH[i])
    if state == "working":
        return Pose(state, i, dy=WORK_BOB[i], eyes="down", mouth="closed")
    if state == "searching":
        return Pose(state, i, eyes=SEARCH_EYES[i], mouth="closed")
    if state == "held":
        return Pose(state, i, dx=HELD_SWAY[i], dy=-2, eyes="wide", mouth="half", stretch=1)
    raise KeyError(state)


# ---------------------------------------------------------------------------
# Face parts every pet draws with its own sizes and colours.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EyeStyle:
    w: int
    h: int
    iris: RGBA
    pupil: RGBA | None = None
    glint: RGBA | None = FX_WHITE
    lid: RGBA = OUTLINE


def _oval(x: int, y: int, w: int, h: int) -> Mask:
    box = rect(x, y, x + w - 1, y + h - 1)
    if w >= 3 and h >= 4:
        box -= {(x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)}
    return box


def draw_eye(f: Frame, style: str, x: int, y: int, e: EyeStyle, *, right: bool) -> None:
    """One eye in its ``e.w x e.h`` box at ``(x, y)``; ``right`` mirrors asymmetric looks."""
    w, h = e.w, e.h
    mid = y + h // 2
    if style in ("down", "look_left", "look_right"):
        f.paint(_oval(x, y, w, h), e.iris)
        pw, ph = max(1, w // 2), max(1, (h + 1) // 2)
        if style == "down":
            px, py = x + (w - pw) // 2, y + h - ph
        else:
            px = x if style == "look_left" else x + w - pw
            py = y + (h - ph) // 2 + (1 if h >= 5 else 0)
        if e.pupil is not None:
            f.paint(rect(px, py, px + pw - 1, py + ph - 1), e.pupil)
        elif e.glint is not None:
            f.paint({(px + pw - 1, py) if style == "look_right" else (px, py)}, e.glint)
        if style == "down":
            # A lowered lid: eyes on the screen.
            f.paint(rect(x, y, x + w - 1, y), e.lid)
        return
    if style in ("open", "wide", "up_left", "up_right"):
        top, height = (y - 1, h + 1) if style == "wide" else (y, h)
        f.paint(_oval(x, top, w, height), e.iris)
        if e.pupil is not None:
            pw, ph = max(1, w // 2), max(1, (h + 1) // 2)
            if style == "wide":
                ph = max(1, ph - 1)
            px = x + (w - pw) // 2
            py = top + height - ph - 1
            if style.startswith("up"):
                py = top + 1
                px = x if style == "up_left" else x + w - pw
            f.paint(rect(px, py, px + pw - 1, py + ph - 1), e.pupil)
            if e.glint is not None:
                f.paint({(px + pw - 1, py)}, e.glint)
        elif e.glint is not None:
            gx = x + w - 2 if w >= 3 else x + w - 1
            gy = top + 1 if not style.startswith("up") else top
            if style == "up_left":
                gx = x
            f.paint({(gx, gy)}, e.glint)
    elif style == "closed":
        f.paint(rect(x, mid, x + w - 1, mid), e.lid)
    elif style == "sleep":
        f.paint(
            {(x, mid - 1), (x + w - 1, mid - 1)} | rect(x + 1, mid, x + w - 2, mid)
            if w >= 3
            else rect(x, mid, x + w - 1, mid),
            e.lid,
        )
    elif style == "happy":
        f.paint(
            {(x, mid), (x + w - 1, mid)} | rect(x + 1, mid - 1, x + w - 2, mid - 1)
            if w >= 3
            else rect(x, mid - 1, x + w - 1, mid - 1),
            e.lid,
        )
    elif style == "x":
        rows = min(h, 5) | 1
        top = y + (h - rows) // 2
        pixels: Mask = set()
        for r in range(rows):
            step = min(r, rows - 1 - r)
            for c in (step, step + 1):
                if c < w:
                    pixels.add((x + w - 1 - c, top + r) if right else (x + c, top + r))
        f.paint(pixels, e.lid)
    else:
        raise KeyError(style)


def draw_eyes(f: Frame, style: str, left: Px, right: Px, e: EyeStyle) -> None:
    draw_eye(f, style, *left, e, right=False)
    draw_eye(f, style, *right, e, right=True)


def draw_mouth(
    f: Frame,
    style: str,
    x: int,
    y: int,
    w: int,
    color: RGBA = OUTLINE,
    inside: RGBA = MOUTH_DARK,
) -> None:
    """A mouth ``w`` px wide whose top row is ``y``."""
    if style in ("rest", "smile"):
        f.paint({(x, y), (x + w - 1, y)} | rect(x + 1, y + 1, x + w - 2, y + 1), color)
    elif style == "closed":
        f.paint(rect(x, y + 1, x + w - 1, y + 1), color)
    elif style == "frown":
        f.paint({(x, y + 1), (x + w - 1, y + 1)} | rect(x + 1, y, x + w - 2, y), color)
    elif style == "half":
        f.paint(rect(x, y, x + w - 1, y + 1), inside)
        f.paint(rect(x, y, x + w - 1, y), color)
    elif style == "open":
        f.paint(rect(x, y, x + w - 1, y + 2) - {(x, y + 2), (x + w - 1, y + 2)}, inside)
        f.paint(rect(x + 1, y + 2, x + w - 2, y + 2), TONGUE)
        f.paint(rect(x, y, x + w - 1, y), color)
    elif style == "wide":
        f.paint(rect(x, y, x + w - 1, y + 3) - {(x, y + 3), (x + w - 1, y + 3)}, inside)
        f.paint(rect(x + 1, y + 2, x + w - 2, y + 3), TONGUE)
        f.paint(rect(x, y, x + w - 1, y), color)
    else:
        raise KeyError(style)


# ---------------------------------------------------------------------------
# Effects: sound arcs, thinking dots, sparkles, z marks, the red "!".
# ---------------------------------------------------------------------------

_ARC_S = ((0, -2), (1, -1), (1, 0), (0, 1))
_ARC_M = ((0, -3), (1, -2), (2, -1), (2, 0), (1, 1), (0, 2))
_ARC_L = ((0, -4), (1, -3), (2, -2), (2, -1), (2, 0), (2, 1), (1, 2), (0, 3))
_ARCS = ((0, _ARC_S), (3, _ARC_M), (6, _ARC_L))
#: Which arcs show in listening frame i, and whether each is fading: a ripple
#: that grows outward, fades from the inside and leaves a beat of silence.
_ARC_PHASES: tuple[tuple[tuple[int, bool], ...], ...] = (
    ((0, False),),
    ((0, False), (1, False)),
    ((0, False), (1, False), (2, False)),
    ((0, True), (1, False), (2, False)),
    ((1, True), (2, True)),
    (),
)


def fx_arcs(f: Frame, i: int, left: Px, right: Px) -> None:
    """Sound arcs rippling out from ``left`` (leftwards) and ``right`` (rightwards)."""
    for index, fading in _ARC_PHASES[i % len(_ARC_PHASES)]:
        off, arc = _ARCS[index]
        shown = {(right[0] + off + ax, right[1] + ay) for ax, ay in arc}
        shown |= {(left[0] - off - ax, left[1] + ay) for ax, ay in arc}
        f.paint(shown, ARC_FADE if fading else ARC)


def fx_dots(f: Frame, i: int, x: int, y: int) -> None:
    """Three thinking dots orbiting a point above the head (``(x, y)`` is the
    old row's left end; the orbit centres where its middle dot sat).

    One full turn takes eight frames; a dot on the near side of the orbit is
    bright, one going round the back dims — a little planet of thoughts.
    """
    cx, cy = x + 4.5, y + 0.5
    dots: dict[Px, RGBA] = {}
    for k in range(3):
        angle = 2.0 * math.pi * (i / 8.0 + k / 3.0)
        dx = round(math.cos(angle) * 7.0, 6)
        dy = round(math.sin(angle) * 2.0, 6)
        px, py = math.floor(cx + dx), math.floor(cy + dy)
        color = FX_WHITE if dy >= 0 else FX_DIM
        for p in rect(px, py, px + 1, py + 1):
            dots[p] = color
    f.part(set(dots), dots)


_SPARK_S = (".#.", "#o#", ".#.")
_SPARK_L = ("..#..", "..#..", "##o##", "..#..", "..#..")
_SPARK_PALETTE = {"#": SPARK, "o": SPARK_CORE}


def fx_sparkles(
    f: Frame,
    i: int,
    spots: Sequence[tuple[int, int, int]],
    palette: Mapping[str, RGBA] = _SPARK_PALETTE,
) -> None:
    """Sparkles at ``(x, y, first_frame)`` centres: small, big, small, gone."""
    for x, y, start in spots:
        age = i - start
        if age < 0 or age > 4:
            continue
        rows = _SPARK_L if age in (1, 2) else _SPARK_S
        half = len(rows) // 2
        f.glyph(x - half, y - half, rows, palette)


_Z_SMALL = ("####", "..#.", ".#..", "####")
_Z_BIG = ("#####", "...#.", "..#..", ".#...", "#####")
_Z_PALETTE = {"#": Z_BLUE}
#: (dx, dy, glyph) of each z relative to the anchor, lowest first.
_ZS = ((0, 0, _Z_SMALL), (5, -6, _Z_BIG), (2, -12, _Z_SMALL))
#: Which z marks show in sleeping frame i: they rise one by one, drift up a
#: pixel while they float, and the oldest leaves first.
_Z_PHASES = ((0,), (0,), (0, 1), (0, 1), (1, 2), (2,))
_Z_DRIFT = (0, -1, 0, -1, 0, -1)


def fx_zzz(f: Frame, i: int, x: int, y: int) -> None:
    drift = _Z_DRIFT[i % len(_Z_DRIFT)]
    for index in _Z_PHASES[i % len(_Z_PHASES)]:
        dx, dy, rows = _ZS[index]
        f.glyph(x + dx, y + dy + drift, rows, _Z_PALETTE)


_BANG = ("##", "##", "##", "##", "..", "##")


def fx_bang(f: Frame, x: int, y: int) -> None:
    f.glyph(x, y, _BANG, {"#": RED})


_DROP = (".#.", "###", "#o#", ".#.")


def fx_sweat(f: Frame, i: int, x: int, y: int) -> None:
    """A sweat drop that slides down the side of the head, a pixel a frame."""
    f.glyph(x, y + min(i, 4), _DROP, {"#": SWEAT, "o": SWEAT_SHINE})


LAPTOP_LID = hexc("#3a4150")
LAPTOP_LID_LIGHT = hexc("#5d6780")
LAPTOP_BASE = hexc("#c9cfdb")
LAPTOP_BASE_DARK = hexc("#8d95a6")
LAPTOP_LOGO = hexc("#8fd3ff")
#: The glow of the screen on the pet's side: brighter on a keystroke.
SCREEN_GLOW = hexc("#bfe6ff")
CODE_CHIP = hexc("#7ee0a1")
MAGNIFIER_RIM = hexc("#d7dbe4")
MAGNIFIER_RIM_DARK = hexc("#8d95a6")
MAGNIFIER_HANDLE = hexc("#8a5a3c")
MAGNIFIER_GLINT = hexc("#ffffff")

#: Keystroke ticks above the keyboard per working frame: (dx, dy) from the
#: lid's top-left, or nothing on a pause between bursts.
_KEY_TICKS: tuple[tuple[Px, ...], ...] = (
    ((3, -2),),
    ((9, -2),),
    ((5, -2), (11, -3)),
    (),
    ((7, -2),),
    ((2, -3), (10, -2)),
    ((6, -2),),
    (),
)
_CODE_GLYPHS = (("#.#", "#.#", ".#."), ("##.", ".##", "##."), (".#.", "##.", ".#."))


def fx_laptop(f: Frame, i: int, x: int, y: int) -> None:
    """A laptop in front of the pet, lid towards the viewer: keys tick above
    the keyboard and little code chips rise off the screen.

    ``(x, y)`` is the lid's top-left; the lid is 14 x 8, the base below it
    18 px wide.
    """
    lid = rounded_rect(x, y, x + 13, y + 7, 1)
    f.part(lid, shade(lid, LAPTOP_LID, light=LAPTOP_LID_LIGHT, dark=LAPTOP_LID, dark_depth=1))
    f.paint(rect(x + 6, y + 3, x + 7, y + 4), LAPTOP_LOGO if i % 4 != 3 else LAPTOP_LID_LIGHT)
    base = rect(x - 2, y + 8, x + 15, y + 9)
    f.part(base, {p: (LAPTOP_BASE if p[1] == y + 8 else LAPTOP_BASE_DARK) for p in base})
    # The screen's light spills over the lid's top edge.
    if _KEY_TICKS[i % len(_KEY_TICKS)]:
        f.paint(rect(x + 2, y - 1, x + 11, y - 1), SCREEN_GLOW)
    for dx, dy in _KEY_TICKS[i % len(_KEY_TICKS)]:
        f.paint({(x + dx, y + dy - 1), (x + dx, y + dy - 2)}, FX_WHITE)
    # One code chip rises from the lid's right corner every four frames.
    age = i % 4
    glyph = _CODE_GLYPHS[(i // 4) % len(_CODE_GLYPHS)]
    f.glyph(x + 15, y - 3 - 2 * age, glyph, {"#": CODE_CHIP})


def fx_magnifier(f: Frame, i: int, cx: int, cy: int) -> None:
    """A magnifying glass sweeping across ``(cx, cy)``; at the far right it
    catches the light."""
    gx = cx + SEARCH_SWEEP[i % len(SEARCH_SWEEP)]
    ring = ellipse(gx, cy, 4, 4) - ellipse(gx, cy, 2.6, 2.6)
    handle = thick(line(gx + 3, cy + 3, gx + 6, cy + 6))
    f.part(handle, MAGNIFIER_HANDLE)
    f.part(ring, shade(ring, MAGNIFIER_RIM, dark=MAGNIFIER_RIM_DARK, dark_depth=1))
    f.paint({(gx - 2, cy - 2), (gx - 1, cy - 2)}, MAGNIFIER_GLINT)
    if SEARCH_SWEEP[i % len(SEARCH_SWEEP)] == max(SEARCH_SWEEP):
        fx_sparkles(f, 1, ((gx + 4, cy - 5, 0),))


def fx_held(f: Frame, i: int, left: Px, right: Px) -> None:
    """Swing streaks on the trailing side and a startled pair of marks."""
    sway = HELD_SWAY[i % len(HELD_SWAY)]
    if sway < 0:
        f.paint(line(right[0], right[1], right[0], right[1] + 4), ARC_FADE)
        f.paint(line(right[0] + 2, right[1] + 1, right[0] + 2, right[1] + 3), ARC_FADE)
    elif sway > 0:
        f.paint(line(left[0], left[1], left[0], left[1] + 4), ARC_FADE)
        f.paint(line(left[0] - 2, left[1] + 1, left[0] - 2, left[1] + 3), ARC_FADE)
    if i % 3 != 2:
        f.paint(line(left[0] + 2, left[1] - 6, left[0], left[1] - 8), ARC)
        f.paint(line(right[0] - 2, right[1] - 6, right[0], right[1] - 8), ARC)


@dataclass(frozen=True)
class Anchors:
    """Where a pet's effects go (cell coordinates, before the pose offset)."""

    arcs_left: Px
    arcs_right: Px
    dots: Px
    zzz: Px
    bang: Px
    sparkles: tuple[tuple[int, int, int], ...]
    #: Where the error's sweat drop starts (its top-left); next to the "!"
    #: unless a pet says otherwise.
    sweat: Px | None = None
    #: The laptop lid's top-left while working.
    laptop: Px = (17, 34)
    #: The centre the magnifier sweeps across while searching.
    magnifier: Px = (CX, 24)


def draw_effects(f: Frame, pose: Pose, a: Anchors) -> None:
    """The state's shared effect; it moves with the body's bob but not its hop."""
    if pose.state == "listening":
        with f.offset(pose.dx, pose.dy):
            fx_arcs(f, pose.i, a.arcs_left, a.arcs_right)
    elif pose.state == "thinking":
        with f.offset(pose.dx, pose.dy):
            fx_dots(f, pose.i, *a.dots)
    elif pose.state == "success":
        fx_sparkles(f, pose.i, a.sparkles)
    elif pose.state == "error":
        fx_bang(f, *a.bang)
        sx, sy = a.sweat if a.sweat is not None else (a.bang[0] - 6, a.bang[1] + 7)
        with f.offset(pose.dx, 0):
            fx_sweat(f, pose.i, sx, sy)
    elif pose.state == "sleeping":
        fx_zzz(f, pose.i, *a.zzz)
    elif pose.state == "working":
        fx_laptop(f, pose.i, *a.laptop)
    elif pose.state == "searching":
        with f.offset(pose.dx, pose.dy):
            fx_magnifier(f, pose.i, *a.magnifier)
    elif pose.state == "held":
        with f.offset(pose.dx, pose.dy):
            fx_held(f, pose.i, a.arcs_left, a.arcs_right)


# ---------------------------------------------------------------------------
# The pets.
# ---------------------------------------------------------------------------


Effect = Callable[[Frame, Pose], None]


@dataclass(frozen=True)
class PetDesign:
    id: str
    name: str
    description: str
    draw: Callable[[Frame, Pose], None]
    anchors: Anchors
    pose: Callable[[str, int], Pose] = base_pose
    frame_counts: Mapping[str, int] | None = None
    fps: Mapping[str, int] | None = None
    #: Per-state effects that replace the shared one (a pet's own motif).
    fx: Mapping[str, Effect] = field(default_factory=dict)
    #: The waist: the row the breath stretches the body above. Everything
    #: below it (feet, paws, the foot of a teapot) stays planted.
    waist: int = 31
    #: Optional pixel pass over the finished body (before the effects), for
    #: looks no mask can draw: glitch slices, a dithered fade.
    post: Callable[[Image.Image, Pose], Image.Image] | None = None

    def counts(self) -> Mapping[str, int]:
        return {**FRAME_COUNTS, **(self.frame_counts or {})}


# -- Gigi: the Jarvis ghost ---------------------------------------------------
#
# Drawn after the brand mark (``assets/icons/jarvis-gigi-256.png``, docs/BRAND.md):
# a black body with a white edge, a rounded head with straight sides, white
# oval eyes with black pupils, grey cheek marks, two grey bands with a small
# "o" mouth between them, thin hanging arms, three sharp teeth along the hem
# and loose grey bits around it. A ghost floats, so Gigi bobs instead of
# breathing, and the hem flows sideways one pixel a frame.

GIGI_BODY = hexc("#111114")
GIGI_WHITE = hexc("#f4f4f6")
GIGI_PUPIL = hexc("#0b0b0d")
GIGI_BAND = hexc("#4b4d55")
GIGI_CHEEK = hexc("#5c5f68")
GIGI_BIT = hexc("#9a9ca4")
GIGI_EYES = EyeStyle(w=4, h=6, iris=GIGI_WHITE, pupil=GIGI_PUPIL, glint=None, lid=GIGI_WHITE)
GIGI_SCAN = hexc("#6b6e78")
#: Talking: the two bands light up with the mouth, like a level meter.
_GIGI_TALK_BANDS = {
    "half": hexc("#6b6e78"),
    "open": hexc("#9a9ca4"),
    "wide": hexc("#d0d2d8"),
}
#: Brand sparkles: white with a bright core instead of the shared gold.
GIGI_SPARK = {"#": hexc("#d9dbe2"), "o": hexc("#ffffff")}

#: The body box: columns 11-36 (symmetric about CX), head from row 9, full
#: width down to row 36, then the toothed hem.
_GIGI_X0, _GIGI_X1, _GIGI_TOP, _GIGI_SKIRT = 11, 36, 9, 36
_GIGI_RADIUS = 9
#: Hem depth per column within one tooth (period 9): sharp tips, narrow valleys,
#: about three teeth across the body like the brand mark.
_GIGI_TOOTH = (1, 2, 3, 5, 6, 5, 3, 2, 1)
#: The idle float: up a pixel and back over the seven ordinary idle cells.
_GIGI_FLOAT = (0, 0, -1, -1, -1, 0, 0)
_GIGI_THINK_FLOAT = (0, 0, -1, -1, -1, -1, 0, 0)


def _gigi_body(phase: int) -> Mask:
    """The silhouette; ``phase`` slides the hem's teeth sideways."""
    body: Mask = set()
    left_c = (_GIGI_X0 + _GIGI_RADIUS, _GIGI_TOP + _GIGI_RADIUS)
    right_c = (_GIGI_X1 + 1 - _GIGI_RADIUS, _GIGI_TOP + _GIGI_RADIUS)
    for y in range(_GIGI_TOP, _GIGI_SKIRT + 1):
        for x in range(_GIGI_X0, _GIGI_X1 + 1):
            px, py = x + 0.5, y + 0.5
            if py < left_c[1]:
                centre = left_c if px < left_c[0] else right_c if px > right_c[0] else None
                if centre is not None and math.hypot(px - centre[0], py - centre[1]) > _GIGI_RADIUS:
                    continue
            body.add((x, y))
    period = len(_GIGI_TOOTH)
    for x in range(_GIGI_X0, _GIGI_X1 + 1):
        depth = _GIGI_TOOTH[(x - _GIGI_X0 + phase) % period]
        body |= {(x, _GIGI_SKIRT + k) for k in range(1, depth + 1)}
    return body


def _gigi_hem_phase(pose: Pose) -> int:
    """One pixel of drift per frame, looping cleanly over each looping row.

    Talking frames are picked by the voice level, not in order, and the error
    shake is busy enough: both keep the hem still.
    """
    if pose.state in ("talking", "error"):
        return 0
    if pose.state == "held":
        return pose.i * 3 % len(_GIGI_TOOTH)  # a fast flutter: it is dangling
    counts = {**FRAME_COUNTS, **_GIGI_FRAMES}
    frames = len(_GIGI_FLOAT) if pose.state == "idle" else counts[pose.state]
    return pose.i * len(_GIGI_TOOTH) // frames


#: Gigi's rows for the action states and holding (frames, fps).
_GIGI_FRAMES = {"working": 8, "searching": 8, "held": 6}
_GIGI_FPS = {"working": 8, "searching": 8, "held": 10}


#: Thin arms hanging from the upper band, left side (the right one is mirrored).
#: The first pixel sits in the body's outline column so the arm joins the edge.
_GIGI_ARMS = {
    "rest": frozenset({(10, 26), (9, 26), (8, 27), (7, 28), (7, 29), (7, 30)}),
    "up": frozenset({(10, 26), (9, 26), (8, 25), (7, 24), (7, 23), (7, 22)}),
    "droop": frozenset({(10, 27), (9, 28), (9, 29), (9, 30)}),
    #: Listening: hands cupped beside the head, like a hand to an ear.
    "cup": frozenset({(10, 24), (9, 23), (8, 22), (8, 21), (9, 20)}),
}


#: Working: the right hand taps the terminal, two key strokes a loop.
_GIGI_TYPE = (
    frozenset({(37, 26), (38, 26), (39, 26), (40, 25), (41, 25)}),
    frozenset({(37, 26), (38, 26), (39, 27), (40, 27), (41, 27)}),
)


def _gigi_arms(pose: Pose) -> tuple[Mask, Mask]:
    """The left and the right arm for this pose."""
    if pose.state == "working":
        return set(_GIGI_ARMS["rest"]), set(_GIGI_TYPE[pose.i % 2])
    if pose.state == "searching":
        return set(_GIGI_ARMS["rest"]), mirrored(_GIGI_ARMS["up"])
    if pose.state == "held":
        left = _GIGI_ARMS["up" if pose.i % 2 == 0 else "cup"]
        right = _GIGI_ARMS["cup" if pose.i % 2 == 0 else "up"]
        return set(left), mirrored(right)
    arm = _GIGI_ARMS[_gigi_arm_style(pose)]
    return set(arm), mirrored(arm)


def _gigi_arm_style(pose: Pose) -> str:
    if pose.state == "success":
        return "up"
    if pose.state == "error":
        return "up" if pose.i % 2 == 0 else "rest"  # flailing
    if pose.state == "sleeping":
        return "droop"
    if pose.state == "listening":
        return "cup"
    return "rest"


#: The loose bits around the brand mark.
_GIGI_BITS: tuple[Mask, ...] = (
    rect(6, 19, 7, 19),
    rect(4, 26, 4, 27),
    {(8, 35)},
    rect(40, 21, 41, 22),
    {(42, 28)},
    rect(39, 36, 40, 36),
)


def _gigi_bits(f: Frame, pose: Pose) -> None:
    """Idle bits twinkle one or two at a time; thinking and errors make them jitter."""
    if pose.state in ("success", "sleeping", "working", "held"):
        return
    for k, bit in enumerate(_GIGI_BITS):
        if pose.state in ("thinking", "error", "searching"):
            f.paint(shifted(bit, (pose.i + 2 * k) % 3 - 1, 0), GIGI_BIT)
        elif (pose.i + k) % 4 != 0:
            f.paint(bit, GIGI_BIT)


#: Searching: where the lens is in each of the eight frames (cell coords).
_GIGI_LENS = tuple(
    (24 + round(9 * math.sin(2 * math.pi * k / 8)), 21 + round(2 * math.cos(2 * math.pi * k / 8)))
    for k in range(8)
)
#: Held: the swing of a ghost dangling from the cursor.
_GIGI_SWAY = (-1, 0, 1, 1, 0, -1)


def gigi_pose(state: str, i: int) -> Pose:
    if state == "working":
        # Shifted left to make room for its terminal; eyes on the screen.
        return Pose(
            state, i, dx=-4, dy=-1 if i in (2, 3, 6, 7) else 0, eyes="look:1", mouth="closed"
        )
    if state == "searching":
        look = (_GIGI_LENS[i][0] > 26) - (_GIGI_LENS[i][0] < 22)
        return Pose(state, i, dy=_GIGI_THINK_FLOAT[i], eyes=f"look:{look}", mouth="rest")
    if state == "held":
        return Pose(state, i, dx=_GIGI_SWAY[i], dy=-3, eyes="wide", mouth=("wide", "open")[i % 2])
    pose = base_pose(state, i)
    if state == "idle" and i < len(_GIGI_FLOAT):
        return replace(pose, dy=_GIGI_FLOAT[i], stretch=0)
    if state == "listening":
        return replace(pose, dy=-1 - (1 if 1 <= i <= 3 else 0), stretch=0)
    if state == "thinking":
        return replace(pose, dy=_GIGI_THINK_FLOAT[i])
    return pose


def draw_gigi(f: Frame, pose: Pose) -> None:
    with f.offset(pose.dx, pose.dy):
        body = _gigi_body(_gigi_hem_phase(pose))
        f.part(body, shade(body, GIGI_BODY, rim=GIGI_WHITE))
        for side in _gigi_arms(pose):
            f.paint(outer_ring(side) - body, OUTLINE)
            f.paint(side, GIGI_WHITE)
        band = _GIGI_TALK_BANDS.get(pose.mouth, GIGI_BAND) if pose.state == "talking" else GIGI_BAND
        f.paint(rect(12, 26, 35, 26) | rect(12, 34, 35, 34), band)
        cheek = rect(13, 22, 15, 23)
        f.paint(cheek | mirrored(cheek), GIGI_CHEEK)
        if pose.state == "thinking":
            # A scan line sweeps down the body, behind the face.
            scan = 11 + 3 * pose.i
            f.paint({p for p in eroded(body) if p[1] == scan}, GIGI_SCAN)
        if pose.eyes.startswith("look:"):
            _gigi_eyes_looking(f, int(pose.eyes[5:]))
        else:
            draw_eyes(f, pose.eyes, (17, 18), (27, 18), GIGI_EYES)
        _gigi_mouth(f, pose.mouth)
        _gigi_bits(f, pose)


def _gigi_eyes_looking(f: Frame, look: int) -> None:
    """Open eyes whose pupils look left (-1), ahead (0) or right (1)."""
    for x in (17, 27):
        f.paint(_oval(x, 18, GIGI_EYES.w, GIGI_EYES.h), GIGI_WHITE)
        px = x + 1 + look
        f.paint(rect(px, 21, px + 1, 22), GIGI_PUPIL)


def _gigi_mouth(f: Frame, style: str) -> None:
    """The brand's small "o", white on the black body, between the two bands."""
    if style in ("rest", "open"):
        f.paint(from_rows(22, 28, (".##.", "#..#", "#..#", ".##.")), GIGI_WHITE)
    elif style == "closed":
        f.paint(rect(22, 30, 25, 30), GIGI_WHITE)
    elif style == "half":
        f.paint(from_rows(22, 29, (".##.", "#..#", ".##.")), GIGI_WHITE)
    elif style == "wide":
        rows = (".####.", "#....#", "#....#", "#....#", ".####.")
        f.paint(from_rows(21, 28, rows), GIGI_WHITE)
    elif style == "smile":
        f.paint(from_rows(21, 29, ("#....#", ".####.")), GIGI_WHITE)
    elif style == "frown":
        f.paint(from_rows(21, 29, (".####.", "#....#")), GIGI_WHITE)
    else:
        raise KeyError(style)


#: Success: the loose bits burst outwards in eight directions.
_GIGI_BURST_DIRS = tuple((math.cos(k * math.pi / 4), math.sin(k * math.pi / 4)) for k in range(8))


def _gigi_burst(f: Frame, i: int) -> None:
    if not 1 <= i <= 5:
        return
    rx, ry = 13 + 3 * i, 16 + 3 * i
    size = 2 if i <= 3 else 1
    color = GIGI_WHITE if i <= 3 else GIGI_BIT
    for ux, uy in _GIGI_BURST_DIRS:
        x, y = round(CX + ux * rx - size / 2), round(25 + uy * ry - size / 2)
        bit = {p for p in rect(x, y, x + size - 1, y + size - 1)}
        if all(1 <= px < CELL - 1 and 1 <= py < CELL - 1 for px, py in bit):
            f.part(bit, color)


def _gigi_success_fx(f: Frame, pose: Pose) -> None:
    _gigi_burst(f, pose.i)
    fx_sparkles(f, pose.i, GIGI_ANCHORS.sparkles, GIGI_SPARK)


def glitch_slices(
    img: Image.Image, slices: Sequence[tuple[int, int, int]], fringe: RGBA
) -> Image.Image:
    """Shift the rows ``y0..y1`` of each ``(y0, y1, dx)`` slice sideways.

    Where the body was and no longer is, a ``fringe`` colour stays behind —
    the torn-signal look of a bit ghost.
    """
    out = img.copy()
    for y0, y1, dx in slices:
        for y in range(max(0, y0), min(CELL, y1 + 1)):
            row = [img.getpixel((x, y)) for x in range(CELL)]
            for x in range(CELL):
                sx = x - dx
                moved = row[sx] if 0 <= sx < CELL else (0, 0, 0, 0)
                if moved[3] == 0 and row[x][3]:
                    moved = fringe
                out.putpixel((x, y), moved)
    return out


def dither_fade(img: Image.Image, top: int) -> Image.Image:
    """Fade everything below ``top`` with an ordered dither: a quarter of the
    pixels drop out over three rows, then half (alpha on screen is binary)."""
    out = img.copy()
    for y in range(max(0, top), CELL):
        depth = y - top
        for x in range(CELL):
            gone = (x + 2 * y) % 4 == 0 if depth < 3 else (x + y) % 2 == 0
            if gone:
                out.putpixel((x, y), (0, 0, 0, 0))
    return out


#: Error glitch slices per frame, ``(y0, y1, dx)`` in body coordinates.
_GIGI_ERROR_GLITCH = (
    ((14, 19, 3), (30, 33, -2)),
    ((22, 26, -3),),
    ((10, 13, 2), (35, 38, 3)),
    ((26, 29, -2),),
    ((18, 22, 2),),
    (),
)
GIGI_GLITCH_RED = hexc("#e5383b")
GIGI_SCREEN = hexc("#16181e")

#: Working: code lines on the terminal (lengths), scrolling up one a frame.
_GIGI_CODE = (5, 3, 7, 4, 2, 6, 5, 3)
_GIGI_CODE_ROWS = 6


def _gigi_terminal_fx(f: Frame, pose: Pose) -> None:
    """A floating terminal to Gigi's right: code scrolls up, the cursor blinks."""
    with f.offset(0, pose.dy):
        screen = rect(36, 17, 46, 32)
        f.part(screen, shade(screen, GIGI_SCREEN, rim=GIGI_SCAN))
        for slot in range(_GIGI_CODE_ROWS):
            y = 19 + 2 * slot
            length = _GIGI_CODE[(slot + pose.i) % len(_GIGI_CODE)]
            indent = 1 if (slot + pose.i) % 3 == 1 else 0
            newest = slot == _GIGI_CODE_ROWS - 1
            if newest:
                length = min(length, 2 + pose.i % 4)  # being typed
            color = GIGI_WHITE if newest else GIGI_BIT
            f.paint(rect(38 + indent, y, 37 + indent + length, y), color)
            if newest and pose.i % 2 == 0:
                f.paint({(39 + indent + length, y)}, GIGI_WHITE)  # the cursor


def _gigi_lens_disc(cx: int, cy: int) -> Mask:
    return {
        (x, y)
        for y in range(cy - 4, cy + 5)
        for x in range(cx - 4, cx + 5)
        if (x - cx) ** 2 + (y - cy) ** 2 <= 16
    }


def _gigi_lens_fx(f: Frame, pose: Pose) -> None:
    """The magnifier's rim and handle, over the magnified face below."""
    cx, cy = _GIGI_LENS[pose.i]
    with f.offset(pose.dx, pose.dy):
        disc = _gigi_lens_disc(cx, cy)
        f.part(outer_ring(disc), GIGI_WHITE)
        handle = thick(line(cx + 4, cy + 4, cx + 7, cy + 7))
        f.part(handle - outer_ring(disc) - disc, GIGI_SCAN)


def _gigi_held_fx(f: Frame, pose: Pose) -> None:
    """Speed lines under the dangling hem."""
    with f.offset(pose.dx, pose.dy):
        for k, x in enumerate((15, 24, 33)):
            top = 44 + (pose.i + k) % 2
            f.paint(rect(x, top, x, top + 1), GIGI_BIT)


def magnify(img: Image.Image, cx: int, cy: int, radius: int = 4, zoom: int = 2) -> Image.Image:
    """Show the pixels around ``(cx, cy)`` ``zoom`` times larger inside a disc."""
    out = img.copy()
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius or not (
                0 <= x < CELL and 0 <= y < CELL
            ):
                continue
            sx = cx + (x - cx) // zoom
            sy = cy + (y - cy) // zoom
            if 0 <= sx < CELL and 0 <= sy < CELL:
                out.putpixel((x, y), img.getpixel((sx, sy)))
    return out


def gigi_post(img: Image.Image, pose: Pose) -> Image.Image:
    if pose.state == "error":
        slices = [(y0 + pose.dy, y1 + pose.dy, dx) for y0, y1, dx in _GIGI_ERROR_GLITCH[pose.i]]
        return glitch_slices(img, slices, GIGI_GLITCH_RED)
    if pose.state == "idle" and pose.i == len(_GIGI_FLOAT):
        # The accent cell: a blink with a short signal tear across the eyes.
        return glitch_slices(img, [(18 + pose.dy, 21 + pose.dy, 2)], GIGI_BIT)
    if pose.state == "sleeping":
        # Asleep, the ghost's tail fades out.
        return dither_fade(img, _GIGI_SKIRT + 1 + pose.dy)
    if pose.state == "searching":
        # A real magnifier: what lies under the lens shows twice as large.
        cx, cy = _GIGI_LENS[pose.i]
        return magnify(img, cx + pose.dx, cy + pose.dy)
    return img


GIGI_ANCHORS = Anchors(
    arcs_left=(9, 16),
    arcs_right=(38, 16),
    dots=(19, 4),
    zzz=(37, 15),
    bang=(40, 5),
    sparkles=((6, 14, 1), (41, 11, 2), (5, 34, 3), (42, 32, 3)),
)

GIGI = PetDesign(
    id="gigi",
    name="Gigi",
    description="The Jarvis ghost, eight bits tall: it floats, glitches while it thinks.",
    draw=draw_gigi,
    pose=gigi_pose,
    anchors=GIGI_ANCHORS,
    # An empty row between the mouth and the lower band: the hop's squash and
    # stretch repeat or drop it without touching the face.
    waist=33,
    post=gigi_post,
    frame_counts=_GIGI_FRAMES,
    fps=_GIGI_FPS,
    fx={
        "success": _gigi_success_fx,
        "working": _gigi_terminal_fx,
        "searching": _gigi_lens_fx,
        "held": _gigi_held_fx,
    },
)


def _shade(mask: Mask, base: RGBA, light: RGBA, dark: RGBA, depth: int = 2) -> dict[Px, RGBA]:
    return shade(mask, base, light=light, dark=dark, dark_depth=depth)


# -- Miso: the cat ------------------------------------------------------------
#
# A ginger tabby that sits upright. Its own body language carries most of the
# states: the tail sways while it idles, curls into a question mark while it
# thinks and stands straight up on a happy hop; the ears perk and swivel to
# listen and flatten on a hiss. It kneads the keys of a little laptop while it
# works, stalks low with a wiggling rump while it searches, and hangs limp and
# unimpressed when it is picked up by the scruff.

MISO_FUR = hexc("#f2a65a")
MISO_LIGHT = hexc("#ffd08f")
MISO_DARK = hexc("#c8743a")
MISO_CREAM = hexc("#fff0d9")
MISO_EAR = hexc("#ff9fb2")
MISO_STRIPE = hexc("#a95a26")
MISO_NOSE = hexc("#ff7a8a")
MISO_EYES = EyeStyle(w=3, h=4, iris=hexc("#6fcf7f"), pupil=hexc("#123524"), glint=FX_WHITE)
#: The muzzle lit by the laptop screen on a keystroke.
MISO_GLOW = hexc("#cfeaff")
#: The scent trail a stalking cat follows, and the hiss marks of an angry one.
MISO_SCENT = hexc("#5fbf86")
MISO_SCENT_FADE = hexc("#a6e3bd")
MISO_HEART = hexc("#ff6f91")
MISO_HEART_SHINE = hexc("#ffd3de")
MISO_DUST = hexc("#c9bfb3")

#: Left ear tip per look; the ear's base sits on the head outline. A right
#: ear uses the same table and is mirrored.
_MISO_EAR_TIPS: dict[str, tuple[float, float]] = {
    "rest": (13.5, 5.5),
    "perk": (12.5, 2.5),
    #: Turned outwards: listening to the side, or a twitch.
    "swivel": (9.5, 4.5),
    "flick": (10.5, 7.5),
    "back": (9.5, 8.5),
    "flat": (8.5, 11.5),
}
#: Listening: the ears take turns rotating forward and out (left, right).
_MISO_LISTEN_EARS = (
    ("perk", "swivel"),
    ("perk", "perk"),
    ("swivel", "perk"),
    ("swivel", "perk"),
    ("perk", "perk"),
    ("perk", "swivel"),
)
#: Idle: the tail tip's slow sway over the seven breathing cells (x offset of
#: the last two points); the accent cell flicks it.
_MISO_IDLE_TAIL = ((0, 0), (0, 0), (0, 1), (1, 2), (1, 2), (1, 1), (0, 0))
#: Working: the tail swishes behind the desk.
_MISO_WORK_TAIL = ((0, 0), (0, 1), (1, 2), (1, 2), (0, 1), (0, 0), (-1, -1), (-1, -1))
#: Working: which front paw is pressed down on the keys (left, right) per frame;
#: two beats of kneading, then a pause.
_MISO_KNEAD = (
    (True, False),
    (False, True),
    (True, False),
    (False, False),
    (False, True),
    (True, False),
    (False, True),
    (False, False),
)
#: Searching: the hindquarters wiggle before a pounce (x offset of the rump).
_MISO_WIGGLE = (0, 0, 0, 1, -1, 1, -1, 0)
#: Held: the swing of a cat dangling from the scruff; legs and tail lag behind.
_MISO_SWAY = (-1, 0, 1, 1, 0, -1)


def miso_pose(state: str, i: int) -> Pose:
    pose = base_pose(state, i)
    if state == "error":
        # Startled wide eyes and an open, hissing mouth with fangs.
        return replace(pose, eyes="wide", mouth="hiss")
    if state == "held":
        # Limp and unimpressed: half-lidded eyes, swinging gently.
        return Pose(state, i, dx=_MISO_SWAY[i % len(_MISO_SWAY)], eyes="half", mouth="closed")
    return pose


def _miso_ear(tip: tuple[float, float]) -> Mask:
    return polygon([(11.5, 18.0), tip, (21.5, 12.5)])


def _miso_tail(sway: int, tip: int | None = None) -> Mask:
    tip = sway if tip is None else tip
    return thick(polyline([(33, 41), (37, 40), (40, 37), (41 + sway, 33), (40 + tip, 29)]))


def _miso_fur(f: Frame, mask: Mask, depth: int = 2) -> None:
    f.part(mask, _shade(mask, MISO_FUR, MISO_LIGHT, MISO_DARK, depth))


def _miso_paw(f: Frame, cx: float, cy: float, rx: float = 3, ry: float = 2) -> None:
    f.part(ellipse(cx, cy, rx, ry), MISO_CREAM)


def _miso_head(
    f: Frame,
    pose: Pose,
    ears: tuple[str, str] = ("rest", "rest"),
    *,
    whiskers: int = 0,
    glow: bool = False,
    nose_dx: int = 0,
) -> None:
    """Ears, head and face of the sitting cat (head centred on (CX, 22))."""
    left, right = ears
    for mask in (_miso_ear(_MISO_EAR_TIPS[left]), mirrored(_miso_ear(_MISO_EAR_TIPS[right]))):
        _miso_fur(f, mask, 1)
        f.paint(eroded(mask, 2), MISO_EAR)
    head = ellipse(CX, 22, 13, 10)
    _miso_fur(f, head)
    stripes = rect(23, 13, 24, 15) | rect(19, 14, 20, 15)
    f.paint(stripes | mirrored(stripes), MISO_STRIPE)
    muzzle = ellipse(CX, 27, 5, 3)
    f.paint(muzzle, MISO_CREAM)
    if glow:
        # The screen below lights the chin.
        f.paint({p for p in muzzle if p[1] >= 28}, MISO_GLOW)
    # ``whiskers`` lifts the outer ends: they rise with an open mouth.
    lift = whiskers
    left_whiskers = line(12, 25 - lift, 16, 26) | line(12, 28 - lift, 16, 27)
    f.paint(left_whiskers | mirrored(left_whiskers), MISO_DARK)
    if pose.eyes == "half":
        draw_eyes(f, "open", (16, 19), (29, 19), MISO_EYES)
        # Heavy lids over the top half: a deadpan stare.
        f.paint(rect(16, 19, 18, 20) | rect(29, 19, 31, 20), MISO_DARK)
        f.paint(rect(16, 21, 18, 21) | rect(29, 21, 31, 21), OUTLINE)
    else:
        draw_eyes(f, pose.eyes, (16, 19), (29, 19), MISO_EYES)
    f.paint(rect(23 + nose_dx, 25, 24 + nose_dx, 25), MISO_NOSE)
    if pose.mouth == "hiss":
        f.paint(rect(21, 26, 26, 29) - {(21, 29), (26, 29)}, MOUTH_DARK)
        f.paint(rect(21, 26, 26, 26), OUTLINE)
        f.paint(rect(23, 29, 24, 29), TONGUE)
        f.paint({(22, 27), (25, 27)}, FX_WHITE)  # the fangs
    else:
        draw_mouth(f, pose.mouth, 22, 26, 4)


def _miso_bristle(f: Frame, i: int) -> None:
    """Puffed-up fur: tufts poke out of the outline all round the top of the
    silhouette, and they tremble from frame to frame."""
    filled = dict(f.px)
    for (x, y), color in filled.items():
        if color != OUTLINE or y > 40 or (2 * x + 3 * y + i) % 6 != 0:
            continue
        for dx, dy in ((0, -1), (-1, 0), (1, 0)):
            tip = (x + dx, y + dy)
            if tip in filled or not (1 <= tip[0] < CELL - 1 and 1 <= tip[1] < CELL - 1):
                continue
            inside = (x - dx, y - dy)
            if filled.get(inside) in (None, OUTLINE):
                break
            far = (x + 2 * dx, y + 2 * dy)
            if far in filled or not (0 <= far[0] < CELL and 0 <= far[1] < CELL):
                break
            fur = MISO_LIGHT if dy < 0 or dx < 0 else MISO_DARK
            f.px[(x, y)] = fur
            f.px[tip] = fur
            f.px[far] = OUTLINE
            for side in ((dy, dx), (-dy, -dx)):
                beside = (tip[0] + side[0], tip[1] + side[1])
                if beside not in filled:
                    f.px[beside] = OUTLINE
            break


def draw_miso(f: Frame, pose: Pose) -> None:
    if pose.state == "sleeping":
        _draw_miso_curled(f, pose)
    elif pose.state == "searching":
        _draw_miso_stalking(f, pose)
    elif pose.state == "held":
        _draw_miso_scruffed(f, pose)
    else:
        _draw_miso_sitting(f, pose)


def _miso_tail_for(pose: Pose) -> Mask:
    state, i = pose.state, pose.i
    if state == "idle":
        if i >= len(_MISO_IDLE_TAIL):
            return _miso_tail(-1, -3)  # the accent's flick
        sway, tip = _MISO_IDLE_TAIL[i]
        return _miso_tail(sway, tip)
    if state == "working":
        # Swishing on the left, out of the way of the paw prints.
        sway, tip = _MISO_WORK_TAIL[i % len(_MISO_WORK_TAIL)]
        return mirrored(_miso_tail(sway, tip))
    if state == "thinking":
        # A question mark: up the side, over the top and a curl that bobs.
        curl = (0, 0, 1, 1, 0, 0, -1, -1)[i % 8]
        return thick(
            polyline(
                [(33, 41), (38, 40), (41, 36), (42, 31), (42, 27), (40, 24)]
                + [(37, 24), (36, 26 + curl)]
            )
        )
    if state == "success":
        # Tail straight up: a happy cat.
        hook = 1 if pose.dy < -2 else 0
        return thick(polyline([(33, 41), (37, 39), (39, 35), (40, 29), (40, 24), (42 - hook, 21)]))
    if state == "error":
        # Bottle-brush: arched up and three pixels thick.
        return thick(thick(polyline([(32, 40), (37, 39), (40, 35), (41, 29), (40, 25)])))
    if state in ("listening", "talking"):
        sway = (0, 1, 1, 0)[(i // 2) % 4]
        return _miso_tail(sway)
    return _miso_tail(0)


def _draw_miso_sitting(f: Frame, pose: Pose) -> None:
    state, i = pose.state, pose.i
    with f.offset(pose.dx, pose.dy):
        tail = _miso_tail_for(pose)
        _miso_fur(f, tail, 1)
        body = ellipse(CX, 37, 10, 7)
        _miso_fur(f, body)
        f.paint(ellipse(CX, 38, 5, 4), MISO_CREAM)
        paws_up = state == "success" and pose.dy <= -4
        if state == "thinking":
            _miso_paw(f, 28.5, 43)  # the other paw is at the chin
        elif state != "working" and not paws_up:
            _miso_paw(f, 19.5, 43)
            _miso_paw(f, 28.5, 43)
        ears = ("rest", "rest")
        whiskers = 0
        if state == "listening":
            ears = _MISO_LISTEN_EARS[i % len(_MISO_LISTEN_EARS)]
        elif state == "error":
            ears = ("flat", "flat")
        elif state == "idle" and i >= len(_MISO_IDLE_TAIL):
            ears = ("flick", "rest")  # the accent: a blink and an ear twitch
        elif state == "idle" and i == 5:
            whiskers = 1  # a little sniff while the tail sways
        elif state == "talking":
            whiskers = (0, 1, 1, 2)[i % 4]
            ears = ("rest", "rest") if i < 3 else ("swivel", "swivel")
        elif state == "success" and pose.dy >= -1:
            ears = ("perk", "perk")  # perked on the ground, laid back in the air
        elif state == "working":
            ears = ("rest", "flick") if i in (3, 4) else ("rest", "rest")
        glow = state == "working" and any(_MISO_KNEAD[i % len(_MISO_KNEAD)])
        _miso_head(f, pose, ears, whiskers=whiskers, glow=glow)
        if state == "thinking":
            # A paw to the chin that taps while it ponders.
            tap = 1 if i % 4 in (1, 2) else 0
            arm = thick(line(17, 38, 18, 33 + tap))
            _miso_fur(f, arm, 1)
            _miso_paw(f, 19.5, 32 + tap)
        if paws_up:
            # Hooray: both front paws in the air at the top of the hop.
            for arm, cx in (
                (thick(line(16, 34, 12, 29)), 11.5),
                (thick(line(31, 34, 35, 29)), 36.5),
            ):
                _miso_fur(f, arm, 1)
                _miso_paw(f, cx, 28)
    if state == "error":
        _miso_bristle(f, i)


def _draw_miso_stalking(f: Frame, pose: Pose) -> None:
    """Searching: crouched low, rump up and wiggling, nose to the ground."""
    i = pose.i
    wiggle = _MISO_WIGGLE[i % len(_MISO_WIGGLE)]
    twitch = (0, 1, 0, -1, 0, 1, 0, -1)[i % 8]
    with f.offset(pose.dx, pose.dy):
        tail = thick(polyline([(39, 34), (42, 32), (43, 28), (43 + twitch, 24)]))
        with f.offset(wiggle, 0):
            _miso_fur(f, tail, 1)
            rump = ellipse(36, 34, 6, 9)
            _miso_fur(f, rump)
            f.paint(rect(36, 27, 37, 28) | rect(39, 29, 40, 30) | rect(39, 33, 40, 34), MISO_STRIPE)
        body = ellipse(26, 41, 12, 4)
        _miso_fur(f, body)
        _miso_paw(f, 14.5, 44)
        _miso_paw(f, 21.5, 44)
        look = SEARCH_SWEEP[i % len(SEARCH_SWEEP)]
        ears = ("swivel", "perk") if look < 0 else ("perk", "swivel") if look > 0 else ("perk",) * 2
        with f.offset(-3, 9):
            _miso_head(f, pose, ears, nose_dx=-1 if i % 2 else 0)


def _draw_miso_scruffed(f: Frame, pose: Pose) -> None:
    """Held: hanging by the scruff, body long and limp, legs and tail dangling."""
    lag = -pose.dx  # the dangling parts trail the swing
    with f.offset(pose.dx, 0):
        tail = thick(polyline([(27, 39), (29, 42), (30 + lag, 44)]))
        _miso_fur(f, tail, 1)
        for x in (21, 26):
            _miso_fur(f, thick(line(x, 38, x + lag, 42)), 1)
            _miso_paw(f, x + 1 + lag, 44.5, 1.8, 2.2)
        body = ellipse(CX, 31, 6, 9)
        _miso_fur(f, body)
        f.paint(ellipse(CX, 33, 3.5, 5), MISO_CREAM)
        for x, end in ((19, 17), (28, 30)):
            _miso_fur(f, thick(line(x, 27, end + lag, 32)), 1)
            _miso_paw(f, end + 1 + lag, 34.5, 1.8, 2.2)
        with f.offset(0, -5):
            # The pinched scruff: a tuft of fur pulled up between the ears.
            tuft = polygon([(21.5, 13.5), (24.0, 8.5), (26.5, 13.5)])
            _miso_fur(f, tuft, 1)
            _miso_head(f, pose, ("back", "back"))
            f.paint(rect(23, 11, 24, 13), MISO_STRIPE)


#: Sleeping: the tail tip twitches in a dream, and one ear flicks once.
_MISO_SLEEP_TIP = ((19, 43), (19, 43), (18, 42), (18, 41), (19, 43), (19, 43))


def _draw_miso_curled(f: Frame, pose: Pose) -> None:
    """Asleep: a loaf with the head tucked on the front paws and the tail wrapped round."""
    i = pose.i
    with f.offset(pose.dx, pose.dy):
        loaf = ellipse(CX, 37, 16, 7)
        _miso_fur(f, loaf)
        f.paint(rect(29, 32, 30, 34) | rect(34, 33, 35, 35), MISO_STRIPE)
        tip = _MISO_SLEEP_TIP[i % len(_MISO_SLEEP_TIP)]
        tail = thick(polyline([(39, 38), (36, 42), (28, 43), tip]))
        _miso_fur(f, tail, 1)
        right_tip = (24.5, 23.5) if i == 4 else (23.5, 21.5)
        for ear in (
            polygon([(8.5, 30.5), (9.5, 22.5), (14.5, 26.5)]),
            polygon([(19.5, 26.5), right_tip, (25.5, 29.5)]),
        ):
            _miso_fur(f, ear, 1)
            f.paint(eroded(ear, 2), MISO_EAR)
        head = ellipse(17, 32, 9, 7)
        _miso_fur(f, head)
        f.paint(ellipse(16.5, 35, 4, 2), MISO_CREAM)
        draw_eye(f, "sleep", 11, 30, MISO_EYES, right=False)
        draw_eye(f, "sleep", 19, 30, MISO_EYES, right=True)
        f.paint(rect(16, 34, 17, 34), MISO_NOSE)


# -- Miso's effects -----------------------------------------------------------

_MISO_HEART_S = ("#.#", "###", ".#.")
_MISO_HEART_L = ("##.##", "#o###", "#####", ".###.", "..#..")
_MISO_HEART_PALETTE = {"#": MISO_HEART, "o": MISO_HEART_SHINE}
#: Hearts ``(x, y, first_frame)`` that float up from a happy hop.
_MISO_HEARTS = ((7, 15, 1), (40, 11, 2), (5, 29, 3), (42, 27, 4))


def _miso_success_fx(f: Frame, pose: Pose) -> None:
    """Little hearts float up, small, big, small; the landing kicks up dust."""
    for x, y, start in _MISO_HEARTS:
        age = pose.i - start
        if not 0 <= age <= 3:
            continue
        rows = _MISO_HEART_L if age in (1, 2) else _MISO_HEART_S
        half = len(rows) // 2
        f.glyph(x - half, y - half - age, rows, _MISO_HEART_PALETTE)
    if pose.i in (6, 7):
        spread = pose.i - 6
        for x in (11 - spread, 35 + spread):
            puff = ellipse(x + 0.5, 44.5, 1.6 - 0.5 * spread, 1.6 - 0.5 * spread)
            f.part(puff, MISO_DUST)


#: The hiss: zigzag marks spat out on either side of the face.
_MISO_HISS = ((0, 0), (1, 1), (2, 0), (3, 1), (4, 0))


def _miso_error_fx(f: Frame, pose: Pose) -> None:
    """The red "!" and jagged hiss marks that pulse out from the open mouth."""
    fx_bang(f, *MISO_ANCHORS.bang)
    reach = (0, 1, 2, 1, 2, 0)[pose.i % 6]
    with f.offset(pose.dx, 0):
        for row in (25, 29):
            f.paint({(9 - reach - x, row + y) for x, y in _MISO_HISS}, RED)
        for row in (16, 20):
            f.paint(
                {(39 + reach + x, row + y) for x, y in _MISO_HISS if 39 + reach + x < CELL}, RED
            )


_MISO_PRINT = ("#.#.#", ".....", ".###.", ".###.")


def _miso_working_fx(f: Frame, pose: Pose) -> None:
    """A laptop on the desk, lid towards the viewer: Miso kneads the keys with
    both front paws, ticks fly off each pressed paw, the screen lights the
    lid's edge and tiny paw prints rise off it like typed code."""
    i = pose.i
    x, y = 17, 33
    lid = rounded_rect(x, y, x + 13, y + 7, 1)
    f.part(lid, shade(lid, LAPTOP_LID, light=LAPTOP_LID_LIGHT, dark=LAPTOP_LID, dark_depth=1))
    fish = from_rows(x + 4, y + 3, (".##.#", "#####", ".##.#"))
    f.paint(fish, LAPTOP_LOGO if i % 4 != 3 else LAPTOP_LID_LIGHT)
    base = rect(x - 3, y + 8, x + 16, y + 9)
    f.part(base, {p: (LAPTOP_BASE if p[1] == y + 8 else LAPTOP_BASE_DARK) for p in base})
    pressed = _MISO_KNEAD[i % len(_MISO_KNEAD)]
    if any(pressed):
        f.paint(rect(x + 2, y - 1, x + 11, y - 1), SCREEN_GLOW)
    for down, cx, side in ((pressed[0], 15.5, -1), (pressed[1], 32.5, 1)):
        if down:
            _miso_paw(f, cx, 40.5, 3.2, 1.6)  # squashed on the keys
        else:
            _miso_paw(f, cx, 37 + pose.dy, 2.6, 2)
        if down:
            tx = int(cx) + side * 4
            f.paint({(tx, 37), (tx + side, 36), (tx, 40), (tx + side, 41)}, ARC)
    # A paw print rises off the screen every four frames.
    age = i % 4
    f.glyph(37, 30 - 3 * age, _MISO_PRINT, {"#": CODE_CHIP})


def _miso_searching_fx(f: Frame, pose: Pose) -> None:
    """A wavy scent trail drifts in towards the nose; sniff ticks beside it."""
    i = pose.i
    for k in range(3):
        x = 1 + (4 * k + i) % 11
        y = 42 + math.floor(round(1.5 * math.sin(x * 0.8), 6))
        f.part(rect(x, y, x + 1, y + 1), MISO_SCENT if x > 3 else MISO_SCENT_FADE)
    if i % 2:
        # Sniff, sniff: two puffs beside the cheek.
        f.paint({(6, 31), (5, 32), (4, 34), (3, 35)}, ARC_FADE)


def _miso_held_fx(f: Frame, pose: Pose) -> None:
    """Lift marks at the pinched scruff and swing streaks trailing the cat."""
    sway = pose.dx
    with f.offset(sway, 0):
        if pose.i % 3 != 2:
            f.paint({(20, 3), (19, 2), (27, 3), (28, 2)}, ARC)
        if sway != 0:
            x = 33 if sway < 0 else 14
            step = 2 if sway < 0 else -2
            f.paint(line(x, 28, x, 34), ARC_FADE)
            f.paint(line(x + step, 30, x + step, 33), ARC_FADE)


def _miso_sleeping_fx(f: Frame, pose: Pose) -> None:
    """The z marks, and a nose bubble that swells with each breath and pops."""
    fx_zzz(f, pose.i, *MISO_ANCHORS.zzz)
    size = (1.6, 2.2, 2.8, 3.4, 3.9, 0.0)[pose.i % 6]
    with f.offset(pose.dx, pose.dy):
        if size == 0.0:
            # Pop: a ring of droplets where the bubble was.
            f.paint({(10, 34), (14, 33), (9, 38), (15, 39), (12, 41)}, SWEAT)
            return
        cx, cy = 15.0 - size, 36.0 + size / 2
        bubble = ellipse(cx, cy, size, size)
        f.part(bubble, {p: SWEAT_SHINE if p in eroded(bubble) else SWEAT for p in bubble})
        gx, gy = math.floor(cx - size / 2), math.floor(cy - size / 2)
        if size > 2:
            f.paint({(gx, gy)}, FX_WHITE)


MISO_ANCHORS = Anchors(
    arcs_left=(10, 18),
    arcs_right=(37, 18),
    dots=(19, 3),
    zzz=(31, 22),
    bang=(42, 3),
    sweat=(33, 12),
    sparkles=((6, 12, 1), (41, 9, 2), (5, 32, 3), (42, 30, 3)),
)

MISO = PetDesign(
    id="miso",
    name="Miso",
    description="A ginger cat whose ears perk up when you talk and who naps curled in a loaf.",
    draw=draw_miso,
    pose=miso_pose,
    waist=33,
    anchors=MISO_ANCHORS,
    fx={
        "success": _miso_success_fx,
        "error": _miso_error_fx,
        "sleeping": _miso_sleeping_fx,
        "working": _miso_working_fx,
        "searching": _miso_searching_fx,
        "held": _miso_held_fx,
    },
)


# -- Brew: the teapot -----------------------------------------------------------
#
# A round teapot with a handle on the left, a long spout on the right and a
# domed lid with a gold knob. Brew's moods live in its lid (it lifts, tilts like
# an ear, rattles, clicks like a key), its steam (wisps, spirals, a whistle jet,
# a heart) and its tilt: the whole pot leans forward to pour, a column shear
# applied after drawing so the outline stays intact.

BREW_BASE = hexc("#6cc7b3")
BREW_LIGHT = hexc("#a9ecdc")
BREW_DARK = hexc("#3e8f80")
BREW_RIM = hexc("#2c6d62")
BREW_GOLD = hexc("#ffcf45")
BREW_GOLD_DARK = hexc("#c9952a")
BREW_STEAM = hexc("#e8eef7")
#: A thin wisp of steam: a 1 px mid tone that holds on dark AND light.
BREW_WISP = hexc("#8ea6c0")
BREW_BLUSH = hexc("#ff8fa3")
BREW_INK = hexc("#22303a")
BREW_EYES = EyeStyle(w=3, h=4, iris=BREW_INK, glint=FX_WHITE, lid=BREW_INK)
#: Asleep the glaze cools down: the same pot, dimmed.
BREW_GLAZE = (BREW_BASE, BREW_LIGHT, BREW_DARK)
BREW_GLAZE_COOL = (hexc("#4f9d8f"), hexc("#7fc2b3"), hexc("#30695f"))
BREW_TEA = hexc("#c07a32")
BREW_TEA_LIGHT = hexc("#f2b765")
BREW_CUP = hexc("#f4efe6")
BREW_CUP_DARK = hexc("#c9c0b2")
BREW_HEART = hexc("#ff6f91")
BREW_HEART_LIGHT = hexc("#ffc2d1")
BREW_COSY = hexc("#d9576b")
BREW_COSY_DARK = hexc("#a83a50")
BREW_WOOL = hexc("#f6e7c8")
BREW_LENS = hexc("#8fd3ff")
#: Brew's face is centred on column 22 (the spout takes the right side).
BREW_AXIS = 22
#: The spout's opening, the knob's centre (cell coordinates, no pose offset).
BREW_SPOUT = (43, 19)
BREW_KNOB = (BREW_AXIS, 16)

#: How far the lid lifts per frame of a state (pixels).
_BREW_LID_LIFT: dict[str, tuple[int, ...]] = {
    "idle": (0, 0, 0, 0, 0, 1, 0, 0),
    "talking": (0, 0, 1, 2),
    "thinking": (0, 1, 1, 0, 0, 1, 1, 0),
    "success": (0, 0, 3, 5, 4, 2, 0, 0),
    "error": (3, 5, 2, 4, 1, 2),
    "working": (0, 1, 0, 0, 1, 0, 1, 0),
    "held": (1, 2, 1, 2, 1, 2),
}
#: Listening: the lid tips up on the spout side like an ear turned to you.
_BREW_EAR_TILT = (1, 2, 2, 2, 1, 1)
#: Held: the lid rattles sideways as the pot swings.
_BREW_LID_RATTLE = (0, 1, 0, -1, 0, 1)
#: The whole pot's forward tilt per frame (pixels of drop per column from
#: the pivot); positive dips the spout.
_BREW_TILT: dict[str, tuple[float, ...]] = {
    "working": (0.2,) * 8,
    "held": (-0.1, 0.0, 0.1, 0.1, 0.0, -0.1),
}
#: Working: pushed left to make room for the cup it pours into.
_BREW_WORK_DX = -2
#: Held: the pendulum swing, in step with the tilt.
_BREW_HELD_SWAY = (-1, 0, 1, 1, 0, -1)
#: Idle: the glint's left edge sweeping across the glaze (None = no glint).
_BREW_GLINT = (None, None, None, 13, 18, 24, None, None)


def brew_pose(state: str, i: int) -> Pose:
    if state == "working":
        return Pose(state, i, dx=_BREW_WORK_DX, eyes="look_right", mouth="closed")
    if state == "held":
        mouth = ("half", "open")[i % 2]
        return Pose(state, i, dx=_BREW_HELD_SWAY[i], dy=-2, eyes="wide", mouth=mouth, stretch=1)
    if state == "searching":
        return Pose(state, i, eyes=SEARCH_EYES[i], mouth="closed")
    return base_pose(state, i)


def _brew_tilt(pose: Pose) -> float:
    return _BREW_TILT.get(pose.state, (0.0,) * 8)[pose.i]


def _brew_drop(x: int, pose: Pose) -> int:
    """How far column ``x`` (cell coordinates) sinks when the pot tilts."""
    k = _brew_tilt(pose)
    return math.floor(round((x - BREW_AXIS - pose.dx) * k + 0.5, 6))


def _brew_lid_lift(pose: Pose) -> int:
    lifts = _BREW_LID_LIFT.get(pose.state)
    return lifts[pose.i] if lifts is not None and pose.i < len(lifts) else 0


def _brew_lid(pose: Pose) -> tuple[Mask, Mask]:
    """The lid's dome and knob, lifted, tilted and rattled for this pose."""
    lift = _brew_lid_lift(pose)
    side = _BREW_LID_RATTLE[pose.i] if pose.state == "held" else 0
    tilt = _BREW_EAR_TILT[pose.i] if pose.state == "listening" else 0

    def place(mask: Mask) -> Mask:
        return {(x + side, y - lift - max(0, x - 13) * tilt // 10) for x, y in mask}

    dome = {p for p in ellipse(BREW_AXIS, 23, 9, 5) if p[1] <= 21}
    return place(dome), place(ellipse(*BREW_KNOB, 2, 2))


def _brew_glint(x0: int) -> Mask:
    """A soft diagonal shine, two pixels wide, leaning to the right."""
    stroke = line(x0, 39, x0 + 7, 25)
    return stroke | shifted(stroke, 1, 0)


#: Error: the glaze cracks from the rim down when it boils over.
_BREW_CRACK = polyline([(15, 24), (13, 26), (14, 27), (12, 29), (12, 30)])


def draw_brew(f: Frame, pose: Pose) -> None:
    base, light, dark = BREW_GLAZE_COOL if pose.state == "sleeping" else BREW_GLAZE
    with f.offset(pose.dx, pose.dy):
        ring = ellipse(9, 31, 6, 6) - ellipse(9, 31, 3, 3)
        handle = {p for p in ring if p[0] <= 11}
        f.part(handle, _shade(handle, base, light, dark, 1))
        spout = polygon([(32, 29), (35, 34), (44.5, 22.5), (42.5, 19.5)])
        f.part(spout, _shade(spout, base, light, dark, 1))
        f.paint({(43, 20), (42, 20)}, BREW_RIM)
        foot = rect(16, 41, 28, 43)
        f.part(foot, _shade(foot, base, light, dark, 1))
        body = ellipse(BREW_AXIS, 32, 13, 10)
        f.part(body, _shade(body, base, light, dark, 3))
        f.paint({(14, 26), (15, 25), (16, 25)}, FX_WHITE)
        if pose.state == "idle" and _BREW_GLINT[pose.i] is not None:
            f.paint(_brew_glint(_BREW_GLINT[pose.i]) & eroded(body, 2), light)
        if pose.state == "error" and pose.i >= 2:
            f.paint(_BREW_CRACK, BREW_INK)
        f.paint(rect(13, 22, 30, 23), BREW_RIM)
        if pose.state == "sleeping":
            _brew_cosy(f, pose)
        else:
            dome, knob = _brew_lid(pose)
            f.part(dome, _shade(dome, base, light, dark, 1))
            if pose.state == "searching":
                _brew_periscope(f, pose)
            else:
                f.part(knob, BREW_GOLD)
        draw_eyes(f, pose.eyes, (15, 28), (26, 28), BREW_EYES)
        blush = rect(12, 33, 13, 33) | rect(30, 33, 31, 33)
        if pose.state in ("success", "held"):
            blush |= rect(11, 33, 11, 33) | rect(32, 33, 32, 33)
        f.paint(blush, BREW_BLUSH)
        draw_mouth(f, pose.mouth, 20, 34, 4, color=BREW_INK)


def _brew_cosy(f: Frame, pose: Pose) -> None:
    """Asleep: a knitted tea cosy pulled over the lid like a nightcap, its
    tip flopping to the side; the pom-pom nods with each breath."""
    band = rect(12, 19, 32, 22)
    cap = polygon([(13.0, 19.5), (17.0, 13.0), (24.0, 10.0), (31.0, 11.0), (33.0, 19.5)])
    tip = thick(polyline([(30, 11), (34, 11), (37, 13)]))
    knit = cap | tip
    stripes = {p: (BREW_COSY if (p[0] + p[1]) // 3 % 2 else BREW_COSY_DARK) for p in knit}
    f.part(knit, stripes)
    f.part(band, {p: BREW_WOOL if p[0] % 2 else BREW_CUP_DARK for p in band})
    nod = SLEEP_BREATH[pose.i]
    f.part(ellipse(38.5, 15 + nod, 2.2, 2.2), BREW_WOOL)


#: Searching: which way the periscope's lens faces per frame.
_BREW_SCOPE = tuple((x > 2) - (x < -2) for x in SEARCH_SWEEP)


def _brew_periscope(f: Frame, pose: Pose) -> None:
    """A brass periscope rises out of the knob and turns with the search."""
    look = _BREW_SCOPE[pose.i]
    lift = 1 if pose.i % 4 in (1, 2) else 0
    top = 7 - lift
    pipe = rect(21, top + 4, 22, 16)
    f.part(pipe, {p: BREW_GOLD if p[0] == 21 else BREW_GOLD_DARK for p in pipe})
    head = rounded_rect(18 + look, top, 25 + look, top + 4, 1)
    f.part(head, _shade(head, BREW_GOLD, SPARK_CORE, BREW_GOLD_DARK, 1))
    if look == 0:
        f.part(ellipse(21.5, top + 2.5, 1.6, 1.6), BREW_LENS)
        f.paint({(21, top + 2)}, FX_WHITE)
    else:
        x = 18 + look if look < 0 else 24 + look
        lens = rect(x, top + 1, x + 1, top + 3)
        f.paint(lens, BREW_LENS)
        f.paint({(x + (1 if look > 0 else 0), top + 1)}, FX_WHITE)


def _brew_puffs(f: Frame, puffs: Iterable[tuple[int, int, bool]]) -> None:
    for x, y, big in puffs:
        f.part(ellipse(x, y, 1.6, 1.6) if big else rect(x, y, x + 1, y + 1), BREW_STEAM)


def _brew_wisp(f: Frame, x: int, y: int, length: int, color: RGBA = BREW_WISP) -> None:
    """A thin S-shaped thread of steam rising from ``(x, y)``."""
    pts = {(x + (0, 1, 1, 0, -1, -1)[k % 6], y - k) for k in range(length)}
    f.paint(pts, color)


def brew_idle(f: Frame, pose: Pose) -> None:
    """A lazy wisp curls up from the spout and thins out; then it rests."""
    if pose.i > 3:
        return
    with f.offset(pose.dx, pose.dy - pose.stretch):
        sx, sy = BREW_SPOUT
        _brew_wisp(f, sx, sy - 2 - 2 * pose.i, 4 - pose.i // 2)


#: Thinking: the steam rises in a slow spiral (x offsets around the spout).
_BREW_SPIRAL = (0, 1, 2, 2, 1, 0, -1, -1)


def brew_thinking(f: Frame, pose: Pose) -> None:
    """Thinking dots, a spiral of steam from the spout and little bubbles
    escaping under the jiggling lid."""
    with f.offset(pose.dx, pose.dy):
        fx_dots(f, pose.i, 14, 7)
        puffs = []
        for k in range(3):
            phase = (pose.i + 3 * k) % 8
            if phase < 6:
                x = BREW_SPOUT[0] - 1 + _BREW_SPIRAL[(phase + pose.i) % 8]
                puffs.append((x, 16 - 2 * phase, phase >= 2))
        _brew_puffs(f, puffs)
        if _brew_lid_lift(pose):
            age = pose.i % 4 - 1  # 0 just out, 1 floated up a pixel
            for x in (12, 32):
                bubble = rect(x, 19 - age, x, 19 - age)
                f.part(bubble, BREW_STEAM)


def brew_talking(f: Frame, pose: Pose) -> None:
    """The whistle: a quick jet of steam from the spout while the lid rattles."""
    with f.offset(pose.dx, pose.dy):
        # More steam the wider the mouth: nothing, a wisp, a puff, a jet.
        jets = (
            (),
            ((43, 17, False),),
            ((43, 16, True), (45, 12, False)),
            ((43, 16, True), (44, 12, True), (45, 8, False)),
        )
        _brew_puffs(f, jets[pose.i])
        if pose.i >= 2:
            # Rattle ticks beside the lifted lid.
            for x in (11, 32):
                f.paint(line(x, 17, x + (1 if x > 20 else -1), 15), ARC)
        if pose.i == 3:
            f.paint(line(9, 19, 8, 17) | line(34, 19, 35, 17), ARC_FADE)


_BREW_HEART = (".#.#.", "##o##", "#####", ".###.", "..#..")


def brew_success(f: Frame, pose: Pose) -> None:
    """A heart puffs out of the spout and floats up through the steam."""
    fx_sparkles(f, pose.i, BREW_ANCHORS.sparkles)
    if pose.i < 2:
        return
    age = pose.i - 2
    sx, sy = BREW_SPOUT
    with f.offset(0, pose.dy):
        puffs = [(sx, sy - 2, age < 3)]
        if age >= 1:
            puffs.append((sx + 1, sy - 5, False))
        _brew_puffs(f, puffs)
    hx, hy = sx - 5 + (age % 2), sy - 11 - 2 * age
    palette = {"#": BREW_HEART if age < 5 else BREW_HEART_LIGHT, "o": BREW_HEART_LIGHT}
    f.glyph(hx, max(1, hy), _BREW_HEART, palette)


#: Error: steam bursting out from under the lid, (dx, dy, big) per frame from
#: the lid's left and right edge (mirrored), plus the spout.
_BREW_BOIL = (
    ((0, 0, False),),
    ((-2, -1, True), (0, 1, False)),
    ((-4, -3, True), (-1, 0, True)),
    ((-6, -5, False), (-3, -2, True)),
    ((-5, -6, False), (-2, -3, False)),
    ((-3, -2, False),),
)


def brew_error(f: Frame, pose: Pose) -> None:
    """Boiling over: puffs burst out under the jumping lid and from the spout."""
    fx_bang(f, *BREW_ANCHORS.bang)
    with f.offset(pose.dx, pose.dy):
        puffs = []
        for dx, dy, big in _BREW_BOIL[pose.i]:
            puffs += [(11 + dx, 19 + dy, big), (33 - dx, 19 + dy, big)]
        _brew_puffs(f, puffs)
        sx, sy = BREW_SPOUT
        jet = ((sx - 1, sy - 3, True), (sx - 1, sy - 7, pose.i % 2 == 0))
        _brew_puffs(f, jet[: 1 + (pose.i < 4)])


def brew_sleeping(f: Frame, pose: Pose) -> None:
    """Z marks drifting off the left and one faint, slow wisp: cooling down."""
    fx_zzz(f, pose.i, 5, 15)
    with f.offset(pose.dx, pose.dy):
        sx, sy = BREW_SPOUT
        if pose.i % 3 != 2:
            _brew_wisp(f, sx, sy - 2 - pose.i % 3, 3, BREW_WISP)


_BREW_CODE = (("#.#", "#.#", ".#."), ("##.", ".##", "##."), (".#.", "##.", ".#."))
#: The cup in front of the spout while working (top-left of its rim).
_BREW_CUP_AT = (37, 38)


def brew_working(f: Frame, pose: Pose) -> None:
    """Brew pours: a stream of tea runs from the dipped spout into a cup, the
    lid clicks like a key and code curls up out of the lid in the steam."""
    cx, cy = _BREW_CUP_AT
    # The spout's opening after the tilt; the tea spills over its lip.
    rx = BREW_SPOUT[0] - 1 + pose.dx
    ry = BREW_SPOUT[1] + 1 + _brew_drop(rx, pose)
    stream = {(rx + 1, ry - 1), (rx + 2, ry - 1)} | rect(rx + 2, ry, rx + 3, cy)
    f.part(stream, BREW_TEA)
    for k in range(3):
        y = ry + (pose.i * 2 + 5 * k) % (cy - ry)
        f.paint({(rx + 2, y), (rx + 3, y + 1)} & stream, BREW_TEA_LIGHT)
    # The cup, with a handle on the right and a little splash on the tea.
    cup = rounded_rect(cx, cy, cx + 7, cy + 6, 2) | {(cx, cy), (cx + 7, cy)}
    f.part(cup, _shade(cup, BREW_CUP, FX_WHITE, BREW_CUP_DARK, 1))
    f.paint(rect(cx + 1, cy, cx + 6, cy) - stream, BREW_TEA)
    handle = {(cx + 8, cy + 2), (cx + 9, cy + 2), (cx + 9, cy + 3), (cx + 8, cy + 4)}
    f.part(handle - cup, BREW_CUP_DARK)
    if pose.i % 2 == 0:
        f.paint({(rx, cy - 1), (rx + 5, cy - 2)}, BREW_TEA_LIGHT)
    with f.offset(pose.dx, 0):
        # Code chips curl up out of the lid's steam, one every four frames.
        age = pose.i % 4
        glyph = _BREW_CODE[(pose.i // 4) % len(_BREW_CODE)]
        f.glyph(28 + age // 2, 11 - 3 * age, glyph, {"#": CODE_CHIP})
        # Key clicks: ticks beside the knob whenever the lid taps down.
        if _brew_lid_lift(pose):
            kx = BREW_KNOB[0]
            f.paint(line(kx - 4, 13, kx - 5, 11) | line(kx + 3, 13, kx + 4, 11), FX_WHITE)


def brew_searching(f: Frame, pose: Pose) -> None:
    """A dotted line of sight from the periscope's lens; a glint when it finds
    something at the far right."""
    look = _BREW_SCOPE[pose.i]
    if look == 0:
        return
    top = 7 - (1 if pose.i % 4 in (1, 2) else 0)
    with f.offset(pose.dx, pose.dy):
        x = 18 + look - 2 if look < 0 else 25 + look + 2
        dots = {(x + look * 3 * k, top + 2) for k in range(3)}
        f.paint({p for p in dots if 0 <= p[0] < CELL}, ARC_FADE if pose.i % 2 else ARC)
        if SEARCH_SWEEP[pose.i] == max(SEARCH_SWEEP):
            fx_sparkles(f, 1, ((40, 4, 0),))


#: Held: tea drops flung from the spout as it dips, (x, y) per frame.
_BREW_DROPS: tuple[tuple[Px, ...], ...] = (
    ((10, 14),),
    ((8, 11),),
    ((44, 18),),
    ((44, 14), (40, 16)),
    ((44, 20),),
    ((12, 15),),
)


def brew_held(f: Frame, pose: Pose) -> None:
    """Tea sloshes out: drops fly from the spout and the lid's gap, and
    the lid rattles."""
    with f.offset(pose.dx, pose.dy):
        for x, y in _BREW_DROPS[pose.i]:
            drop = rect(x, y, x + 1, y + 1)
            f.part(drop, BREW_TEA)
            f.paint({(x, y)}, BREW_TEA_LIGHT)
        if _brew_lid_lift(pose) == 2:
            # The lid rattles: ticks beside it.
            f.paint(line(11, 18, 10, 16) | line(33, 18, 34, 16), ARC)


def brew_post(img: Image.Image, pose: Pose) -> Image.Image:
    """Tilt the pot: shift each column down by its drop (a column shear)."""
    if _brew_tilt(pose) == 0:
        return img
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for x in range(CELL):
        drop = _brew_drop(x, pose)
        for y in range(CELL):
            color = img.getpixel((x, y))
            if color[3] and 0 <= y + drop < CELL:
                out.putpixel((x, y + drop), color)
    return out


BREW_ANCHORS = Anchors(
    arcs_left=(11, 17),
    arcs_right=(32, 17),
    dots=(14, 7),
    zzz=(5, 15),
    bang=(41, 3),
    sweat=(31, 21),
    sparkles=((5, 14, 1), (30, 6, 2), (4, 40, 3), (41, 34, 3)),
)

BREW = PetDesign(
    id="brew",
    name="Brew",
    description="A little teapot that steams while it thinks and whistles when it answers.",
    draw=draw_brew,
    pose=brew_pose,
    anchors=BREW_ANCHORS,
    post=brew_post,
    fx={
        "idle": brew_idle,
        "thinking": brew_thinking,
        "talking": brew_talking,
        "success": brew_success,
        "error": brew_error,
        "sleeping": brew_sleeping,
        "working": brew_working,
        "searching": brew_searching,
        "held": brew_held,
    },
    waist=39,
)


# -- Bolt: the battery ----------------------------------------------------------
#
# A battery with a face, stubby metal arms and a charge window. The window is
# Bolt's mood ring: it fills while thinking, meters the voice while talking,
# turns into a radar while searching, sloshes when carried and runs flat
# (and recharges on a cable) while asleep.

BOLT_CASE = (hexc("#3fa7d6"), hexc("#86d3f2"), hexc("#2a78a3"))
#: Asleep the battery is flat: the same casing, dimmed.
BOLT_CASE_FLAT = (hexc("#2f6f90"), hexc("#4f8fae"), hexc("#23516a"))
BOLT_METAL = hexc("#cfd4de")
BOLT_METAL_DARK = hexc("#8d95a6")
BOLT_WINDOW = hexc("#1c2733")
BOLT_GREEN = hexc("#6ee07a")
#: A segment catching the light, or the one that just charged.
BOLT_GREEN_LIGHT = hexc("#c4f7bf")
BOLT_GREEN_MID = hexc("#4fb05e")
BOLT_GREEN_DIM = hexc("#2f6b3b")
#: Fully charged and over the top: the segments glow white-hot.
BOLT_GLOW = hexc("#f2ffe8")
#: The radar screen's faint ring.
BOLT_RADAR_RING = hexc("#2a5536")
BOLT_RED_LIGHT = hexc("#ff9a9b")
#: Success rays: a deep gold that holds on a light desktop without an outline.
BOLT_RAY = hexc("#e8a200")
BOLT_CABLE = hexc("#6b7385")
BOLT_SMOKE = hexc("#9aa0ad")
BOLT_SMOKE_LIGHT = hexc("#c9cdd6")
BOLT_DISPLAY = hexc("#1d2533")
BOLT_EYES = EyeStyle(w=4, h=5, iris=FX_WHITE, pupil=OUTLINE, glint=None, lid=OUTLINE)
BOLT_THINK_FRAMES = 8
#: Charge segments lit per state (0-4); the other states compute theirs.
BOLT_LEVELS: dict[str, int] = {
    "idle": 3,
    "listening": 3,
    "success": 4,
}
#: Thinking fills up frame by frame (the user's favourite: keep it).
BOLT_THINK_LEVELS = (1, 1, 2, 2, 3, 3, 4, 4)
#: Working: every burst of typing drains a segment, the pause tops it up.
BOLT_WORK_LEVELS = (4, 3, 3, 2, 2, 3, 4, 4)
#: Working: Bolt steps left to make room for its laptop.
BOLT_WORK_DX = -8
#: Working: the right hand presses a key on these frames.
_BOLT_KEYSTROKE = (True, False, True, True, False, True, False, False)
#: Sleeping: the bottom segment breathes dim, mid, bright, bright, mid, dim.
_BOLT_SLEEP_PULSE = (
    BOLT_GREEN_DIM,
    BOLT_GREEN_MID,
    BOLT_GREEN,
    BOLT_GREEN,
    BOLT_GREEN_MID,
    BOLT_GREEN_DIM,
)
#: Searching: the radar sweep's angle per frame (degrees, clockwise from east).
_BOLT_RADAR_ANGLES = tuple(45 * k for k in range(8))
#: Held: the liquid's tilt per frame, lagging behind the swing.
_BOLT_SLOSH = (3, 1, -3, -1, 3, 1)


def _bolt_radar_eyes(i: int) -> str:
    x = round(math.cos(math.radians(_BOLT_RADAR_ANGLES[i])), 6)
    return "look_right" if x > 0.3 else "look_left" if x < -0.3 else "open"


def bolt_pose(state: str, i: int) -> Pose:
    if state == "thinking":
        early = i < BOLT_THINK_FRAMES // 2
        return Pose(
            state, i, dy=0 if early else -1, eyes="up_left" if early else "up_right", mouth="closed"
        )
    if state == "working":
        return Pose(
            state,
            i,
            dx=BOLT_WORK_DX,
            dy=1 if _BOLT_KEYSTROKE[i] and i % 2 == 0 else 0,
            eyes="look_right",
            mouth="closed",
        )
    if state == "searching":
        return Pose(state, i, eyes=_bolt_radar_eyes(i), mouth="closed")
    if state == "held":
        return Pose(
            state,
            i,
            dx=HELD_SWAY[i],
            dy=-2,
            eyes="happy" if i in (2, 5) else "wide",
            mouth="open" if i % 2 == 0 else "smile",
            stretch=1,
        )
    return base_pose(state, i)


def _bolt_segment(k: int) -> Mask:
    top = 38 - 4 * k
    return rect(18, top, 29, top + 2)


def _bolt_path(points: Sequence[Px]) -> list[Px]:
    """The pixels of a polyline in drawing order (for things travelling along it)."""
    out: list[Px] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            if not out or out[-1] != (x0, y0):
                out.append((x0, y0))
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
    return out


def _bolt_in_cell(pixels: Iterable[Px]) -> Mask:
    return {(x, y) for x, y in pixels if 0 <= x < CELL and 0 <= y < CELL}


def _bolt_part(f: Frame, mask: Mask, fill: RGBA | Mapping[Px, RGBA]) -> None:
    """``Frame.part`` for absolute coordinates, clipped to the cell (smoke near the edge)."""
    f.paint(_bolt_in_cell(outer_ring(mask)), OUTLINE)
    if isinstance(fill, Mapping):
        f.paint_map({p: c for p, c in fill.items() if p in _bolt_in_cell(fill)})
    else:
        f.paint(_bolt_in_cell(mask), fill)


#: The three arm poses (left arm; the right one is mirrored).
_BOLT_ARM_REST = rounded_rect(9, 26, 12, 31, 1)
_BOLT_ARM_UP = rounded_rect(8, 17, 11, 23, 1)
_BOLT_ARM_SWING = rounded_rect(8, 23, 11, 28, 1)
_BOLT_ARM_DROOP = rounded_rect(10, 28, 13, 33, 1)


def _bolt_arms(pose: Pose) -> tuple[Mask | None, Mask | None]:
    """The left and the right arm (``None``: drawn by the state's effect)."""
    rest, up, swing = _BOLT_ARM_REST, _BOLT_ARM_UP, _BOLT_ARM_SWING
    s, i = pose.state, pose.i
    if s == "success":
        return up, mirrored(up)
    if s == "error" or s == "held":
        return (up, mirrored(rest)) if i % 2 == 0 else (rest, mirrored(up))
    if s == "sleeping":
        return _BOLT_ARM_DROOP, mirrored(_BOLT_ARM_DROOP)
    if s == "working":
        return rest, None
    if s == "idle" and i in (4, 5):
        return rest, mirrored(swing)  # a little stretch of the arm
    if s == "talking":
        return (swing if i == 3 else rest), mirrored(swing if i >= 2 else rest)
    return rest, mirrored(rest)


def _bolt_feet(pose: Pose) -> Mask:
    foot = rect(16, 44, 20, 45)
    if pose.state == "held":
        # Kicking: one foot dangles lower, the other pulls up.
        kick = 2 if pose.i % 2 == 0 else 0
        return shifted(foot, -1, kick) | shifted(mirrored(foot), 1, 2 - kick)
    return foot | mirrored(foot)


def _bolt_light(color: RGBA) -> RGBA:
    return {BOLT_GREEN: BOLT_GREEN_LIGHT, SPARK: SPARK_CORE, RED: BOLT_RED_LIGHT}.get(color, color)


def _bolt_level_color(level: int) -> RGBA:
    return BOLT_GREEN if level >= 3 else SPARK if level == 2 else RED


def _bolt_glint(x0: int, segments: int) -> Mask:
    """A slanted glint across the lit segments, as if light slid over the glass."""
    out: Mask = set()
    for k in range(segments):
        for y in range(38 - 4 * k, 41 - 4 * k):
            x = x0 + (40 - y) // 4
            out |= {(x, y), (x + 1, y)} & _bolt_segment(k)
    return out


def _bolt_charge(pose: Pose) -> dict[Px, RGBA]:
    """The charge window's contents for this pose."""
    s, i = pose.state, pose.i
    if s == "searching":
        return _bolt_radar(i)
    if s == "held":
        return _bolt_slosh(i)
    out: dict[Px, RGBA] = {}

    def fill(level: int, color: RGBA) -> None:
        for k in range(level):
            out.update(dict.fromkeys(_bolt_segment(k), color))

    if s == "thinking":
        level = BOLT_THINK_LEVELS[i]
        color = _bolt_level_color(level)
        fill(level, color)
        if i % 2 == 0:  # the segment that just charged flashes
            out.update(dict.fromkeys(_bolt_segment(level - 1), _bolt_light(color)))
    elif s == "talking":
        # A level meter: one more segment per step of loudness, the top one lit.
        fill(i + 1, BOLT_GREEN)
        out.update(dict.fromkeys(_bolt_segment(i), BOLT_GREEN_LIGHT))
    elif s == "error":
        if i % 2 == 0:  # low battery: the last red sliver blinks
            fill(1, RED)
        else:  # in between, a short circuit arcs across the empty window
            zig = polyline([(18, 31), (20, 28), (22, 33), (25, 29), (27, 34), (29, 31)])
            out.update(dict.fromkeys(zig, ARC if i != 5 else ARC_FADE))
    elif s == "sleeping":
        fill(1, _BOLT_SLEEP_PULSE[i])
    elif s == "working":
        level = BOLT_WORK_LEVELS[i]
        color = _bolt_level_color(level)
        fill(level, color)
        if i in (5, 6):  # topping up in the pause
            out.update(dict.fromkeys(_bolt_segment(level - 1), _bolt_light(color)))
    elif s == "success":
        fill(4, BOLT_GLOW if 1 <= i <= 4 else BOLT_GREEN)
        if i >= 5:
            out.update(dict.fromkeys(_bolt_glint(16 + 4 * (i - 5), 4), BOLT_GREEN_LIGHT))
    else:
        level = BOLT_LEVELS.get(s, 3)
        fill(level, _bolt_level_color(level))
        if s == "idle" and i < 4:
            out.update(dict.fromkeys(_bolt_glint(16 + 4 * i, level), BOLT_GREEN_LIGHT))
        if s == "listening":
            # Receiving: a light ripple climbs the lit segments.
            out.update(dict.fromkeys(_bolt_segment(i % 3), BOLT_GREEN_LIGHT))
    return out


#: Radar blips: (x, y, frame the sweep passes them).
_BOLT_BLIPS = ((27, 29, 7), (20, 37, 3))
_BOLT_RADAR_C = (24, 33)


def _bolt_radar(i: int) -> dict[Px, RGBA]:
    """Searching: the charge window turns into a radar screen; the sweep turns
    clockwise and leaves a fading wedge behind, blips light up as it passes."""
    cx, cy = _BOLT_RADAR_C
    sweep = _BOLT_RADAR_ANGLES[i]
    out: dict[Px, RGBA] = {}
    for x, y in ellipse(cx, cy, 7.5, 7.5):
        dx, dy = x + 0.5 - cx, y + 0.5 - cy
        if dx * dx + dy * dy > 6.5 * 6.5:
            out[(x, y)] = BOLT_RADAR_RING
            continue
        behind = (sweep - math.degrees(math.atan2(dy, dx))) % 360
        if behind < 22:
            out[(x, y)] = BOLT_GREEN_MID
        elif behind < 50:
            out[(x, y)] = BOLT_GREEN_DIM
    a = math.radians(sweep)
    ex = math.floor(round(cx + 6.4 * math.cos(a), 6))
    ey = math.floor(round(cy + 6.4 * math.sin(a), 6))
    out.update(
        dict.fromkeys(
            line(cx if ex >= cx else cx - 1, cy if ey >= cy else cy - 1, ex, ey), BOLT_GREEN_LIGHT
        )
    )
    out.update(dict.fromkeys(rect(cx - 1, cy - 1, cx, cy), BOLT_GREEN))
    for bx, by, hit in _BOLT_BLIPS:
        age = (i - hit) % len(_BOLT_RADAR_ANGLES)
        if age < 3:
            color = (BOLT_GLOW, BOLT_GREEN_LIGHT, BOLT_GREEN_MID)[age]
            out.update(dict.fromkeys(rect(bx, by, bx + 1, by + 1), color))
    return out


def _bolt_slosh(i: int) -> dict[Px, RGBA]:
    """Held: the charge is a liquid that sloshes against the swing."""
    tilt = _BOLT_SLOSH[i]
    lit = set().union(*(_bolt_segment(k) for k in range(4)))
    out: dict[Px, RGBA] = {}
    for x in range(18, 30):
        surface = 30 + tilt * (x - 23.5) / 6
        column = sorted(y for (px, y) in lit if px == x and y >= surface)
        for y in column:
            out[(x, y)] = BOLT_GREEN_LIGHT if y == column[0] else BOLT_GREEN
    if abs(tilt) == 3:
        # A droplet thrown up on the high side.
        out[(19 if tilt > 0 else 28, 26)] = BOLT_GREEN_LIGHT
    return out


def _bolt_antenna(f: Frame, tip: RGBA) -> None:
    """A little antenna on the cap: listening and searching tune in with it."""
    stalk = rect(23, 5, 24, 6)
    f.part(stalk, BOLT_METAL_DARK)
    ball = rect(22, 3, 25, 4)
    f.part(ball, tip)


#: Sleeping: the charger cable from the wall plug (bottom left) to the cap.
_BOLT_SLEEP_CABLE = _bolt_path([(4, 37), (4, 14), (5, 9), (8, 5), (13, 2), (20, 2), (23, 3)])


def draw_bolt(f: Frame, pose: Pose) -> None:
    base, light, dark = BOLT_CASE_FLAT if pose.state == "sleeping" else BOLT_CASE
    with f.offset(pose.dx, pose.dy):
        if pose.state == "sleeping":
            _bolt_charger(f, pose.i)
        for arm in _bolt_arms(pose):
            if arm is not None:
                f.part(arm, _shade(arm, BOLT_METAL, FX_WHITE, BOLT_METAL_DARK, 1))
        f.part(_bolt_feet(pose), BOLT_METAL_DARK)
        if pose.state == "listening":
            _bolt_antenna(f, ARC if pose.i % 2 == 0 else ARC_FADE)
        elif pose.state == "searching":
            _bolt_antenna(f, RED if pose.i % 2 == 0 else BOLT_RED_LIGHT)
        cap = rounded_rect(19, 7, 28, 10, 1)
        f.part(cap, _shade(cap, BOLT_METAL, FX_WHITE, BOLT_METAL_DARK, 1))
        if pose.state == "idle" and pose.i in (5, 6):
            f.paint({(22 + pose.i - 5, 8)}, SPARK)  # the terminal tingles
        case = rounded_rect(13, 11, 34, 43, 2)
        f.part(case, _shade(case, base, light, dark))
        f.paint(rounded_rect(16, 24, 31, 41, 1), BOLT_WINDOW)
        f.paint_map(_bolt_charge(pose))
        draw_eyes(f, pose.eyes, (17, 14), (27, 14), BOLT_EYES)
        draw_mouth(f, pose.mouth, 22, 20, 4)


def _bolt_charger(f: Frame, i: int) -> None:
    """Asleep: plugged into a charger brick, a spark of current creeping up the
    cable into the cap."""
    cable = set(_BOLT_SLEEP_CABLE)
    f.part(cable, BOLT_CABLE)
    head = rect(22, 4, 25, 5)
    f.part(head, BOLT_METAL_DARK)
    brick = rounded_rect(1, 38, 7, 45, 1)
    f.part(brick, _shade(brick, LAPTOP_LID, LAPTOP_LID_LIGHT, LAPTOP_LID, 1))
    f.paint({(5, 40), (4, 41), (5, 41), (4, 42)}, SPARK)  # the charger's bolt mark
    f.paint({(3, 43)}, BOLT_GREEN if i % 2 == 0 else BOLT_GREEN_DIM)  # its LED
    n = len(_BOLT_SLEEP_CABLE)
    k = min(n - 3, 2 + i * (n - 4) // 5)
    f.paint(set(_BOLT_SLEEP_CABLE[k : k + 3]), BOLT_GREEN_LIGHT)


_BOLT_GLYPH = ("..##", ".##.", "####", ".##.", "##..", "#...")
_BOLT_PLUS = (".#.", "#o#", ".#.")


def bolt_idle(f: Frame, pose: Pose) -> None:
    """Idle micro-actions: a tiny spark hops off the cap and drops back."""
    with f.offset(pose.dx, pose.dy - pose.stretch):
        if pose.i == 5:
            f.glyph(24, 3, _BOLT_PLUS, _SPARK_PALETTE)
        elif pose.i == 6:
            f.glyph(27, 4, ("#",), {"#": SPARK})


def bolt_listening(f: Frame, pose: Pose) -> None:
    """Signal bars like a phone's, filling up while the voice comes in."""
    lit = (1, 2, 3, 4, 4, 3)[pose.i]
    with f.offset(pose.dx, pose.dy):
        bars = [rect(37 + 2 * k, 18 - 2 * k, 37 + 2 * k, 19) for k in range(4)]
        f.paint(outer_ring(set().union(*bars)), OUTLINE)
        for k, bar in enumerate(bars):
            f.paint(bar, ARC if k < lit else FX_DIM)


def bolt_thinking(f: Frame, pose: Pose) -> None:
    """A flickering lightning bolt that charges the battery through a crackling arc."""
    with f.offset(pose.dx, pose.dy):
        tint = SPARK if pose.i % 2 == 0 else SPARK_CORE
        f.glyph(37, 3, _BOLT_GLYPH, {"#": tint})
        if pose.i % 2 == 0:
            arc = polyline([(29, 8), (31, 6), (32, 8), (34, 6), (36, 7)])
        else:
            arc = polyline([(29, 7), (30, 8), (32, 6), (33, 7), (35, 5)])
        f.paint(arc, ARC if pose.i % 2 == 0 else ARC_FADE)
        if pose.i >= 6:  # full: the cap crackles
            spot = (16, 5) if pose.i == 6 else (31, 4)
            f.glyph(spot[0] - 1, spot[1] - 1, _BOLT_PLUS, _SPARK_PALETTE)


def bolt_talking(f: Frame, pose: Pose) -> None:
    """At full volume, sparks jump off the cap."""
    if pose.i == 3:
        with f.offset(pose.dx, pose.dy):
            f.glyph(15, 4, _BOLT_PLUS, _SPARK_PALETTE)
            f.glyph(30, 4, _BOLT_PLUS, _SPARK_PALETTE)


#: Success rays: (angle in degrees, first frame); they shoot outwards.
_BOLT_RAYS = ((180, 2), (0, 2), (150, 3), (30, 3), (210, 3), (330, 3), (125, 4), (55, 4))


def bolt_success(f: Frame, pose: Pose) -> None:
    """Fully charged: lightning flashes either side, rays burst out, sparkles."""
    i = pose.i
    with f.offset(0, pose.dy):
        if 1 <= i <= 3:
            tint = {"#": SPARK_CORE if i == 1 else SPARK}
            f.glyph(3, 11, _BOLT_GLYPH, tint)
            f.glyph(41, 11, _BOLT_GLYPH, tint)
        for angle, start in _BOLT_RAYS:
            age = i - start
            if not 0 <= age <= 2:
                continue
            a = math.radians(angle)
            ux, uy = round(math.cos(a), 6), round(math.sin(a), 6)
            r0 = 18 + 2 * age
            p0 = (math.floor(CX + ux * r0), math.floor(29 + uy * r0 * 0.9))
            p1 = (math.floor(CX + ux * (r0 + 3)), math.floor(29 + uy * (r0 + 3) * 0.9))
            f.paint(_bolt_in_cell(line(*p0, *p1)), BOLT_RAY if age < 2 else SPARK)
    fx_sparkles(f, i, ((6, 38, 4), (42, 36, 5), (8, 4, 6)))


#: A smoke puff per age: blobs (cx, cy, rx, ry) that swell and drift up-left.
_BOLT_SMOKE_PUFFS = (
    ((24, 5, 1.6, 1.5),),
    ((21, 4, 2.2, 2), (24, 5, 1.6, 1.4)),
    ((16, 3, 2.8, 2.2), (20, 4, 2.2, 1.8)),
)
_BOLT_LOW = ("#######.", "#rkkkk##", "#rkkkk##", "#######.")


def bolt_error(f: Frame, pose: Pose) -> None:
    """A short circuit: sparks jump off the cap, smoke puffs up, low battery blinks."""
    i, dx = pose.i, pose.dx
    red = RED if i % 2 == 0 else BOLT_WINDOW
    f.glyph(37, 4, _BOLT_LOW, {"#": FX_WHITE, "r": red, "k": BOLT_WINDOW})
    with f.offset(dx, 0):
        if i in (0, 1, 3):
            left = i != 1
            pts = (
                [(19, 8), (17, 6), (18, 5), (16, 3)]
                if left
                else [(28, 8), (30, 6), (29, 5), (31, 3)]
            )
            f.paint(polyline(pts), ARC)
            end = pts[-1]
            f.glyph(end[0] - 1, end[1] - 2, _BOLT_PLUS, _SPARK_PALETTE)
    # The smoke drifts up and off to the left on its own; it does not shake.
    for start in (1, 3):
        age = i - start
        if 0 <= age < len(_BOLT_SMOKE_PUFFS):
            puff = set().union(*(ellipse(*blob) for blob in _BOLT_SMOKE_PUFFS[age]))
            color = BOLT_SMOKE_LIGHT if age >= 2 else BOLT_SMOKE
            _bolt_part(f, puff, color)


def bolt_sleeping(f: Frame, pose: Pose) -> None:
    """The shared z marks, lifted clear of the cable."""
    fx_zzz(f, pose.i, *BOLT_ANCHORS.zzz)


#: Working: the laptop to Bolt's right, a three-quarter view of its screen.
_BOLT_SCREEN = polygon([(31, 28), (46.5, 26), (46.5, 40), (31, 41)])
_BOLT_DECK = polygon([(31, 41), (46.5, 40), (45, 46.5), (26, 46.5)])
#: Code line lengths on the screen, scrolling up one a frame.
_BOLT_CODE = (7, 4, 9, 5, 3, 8, 6, 4)
#: The power cord from Bolt's cap (shifted left) to the back of the laptop.
_BOLT_WORK_CABLE = _bolt_path([(15, 6), (15, 3), (20, 1), (31, 1), (38, 3), (42, 8), (43, 26)])


def bolt_working(f: Frame, pose: Pose) -> None:
    """Typing at a little laptop it powers itself: current pulses run along the
    cord, keys spark under the hand and the code scrolls."""
    i = pose.i
    cable = set(_BOLT_WORK_CABLE)
    f.paint(outer_ring(cable) - _BOLT_SCREEN, OUTLINE)
    f.paint(cable, BOLT_CABLE)
    n = len(_BOLT_WORK_CABLE)
    for k in range(3):
        at = (i * n // 8 + k * n // 3) % n
        f.paint(set(_BOLT_WORK_CABLE[at : at + 2]), SPARK)
    screen = _BOLT_SCREEN
    f.part(screen, shade(screen, LAPTOP_LID, light=LAPTOP_LID_LIGHT, dark_depth=1))
    display = eroded(screen, 1)
    press = _BOLT_KEYSTROKE[i]
    f.paint(display, BOLT_DISPLAY)
    for slot in range(5):
        y = 30 + 2 * slot
        length = _BOLT_CODE[(slot + i) % len(_BOLT_CODE)]
        newest = slot == 4
        if newest:
            length = min(length, 2 + i % 4)
        indent = 2 if (slot + i) % 3 == 1 else 0
        row = rect(33 + indent, y, 32 + indent + length, y) & display
        f.paint(row, CODE_CHIP if newest else FX_DIM)
        if newest and i % 2 == 0:
            f.paint({(34 + indent + length, y)} & display, FX_WHITE)  # the cursor
    deck = _BOLT_DECK
    f.part(deck, {p: LAPTOP_BASE if p[1] < 44 else LAPTOP_BASE_DARK for p in deck})
    f.paint({(x, 43) for x in range(31, 45, 2)} & deck, LAPTOP_BASE_DARK)  # the keys
    # The typing hand hops between two keys and presses down on a keystroke.
    hx = 27 + (2 if i % 4 >= 2 else 0)
    hand = rounded_rect(hx, 37 + press, hx + 3, 42 + press, 1)
    f.part(hand, _shade(hand, BOLT_METAL, FX_WHITE, BOLT_METAL_DARK, 1))
    if press:
        f.glyph(hx + 4, 36, _BOLT_PLUS, _SPARK_PALETTE)


#: A radio ping beside the antenna tip (left side; the right is mirrored).
_BOLT_PING = ((0, 2), (-1, 3), (-1, 4), (0, 5))


def bolt_searching(f: Frame, pose: Pose) -> None:
    """Pings ripple off the antenna while the radar in the window sweeps."""
    age = pose.i % 4
    if age >= 3:
        return
    with f.offset(pose.dx, pose.dy):
        x0 = 20 - 2 * age
        ping = {(x0 + px, py) for px, py in _BOLT_PING}
        f.paint(ping | mirrored(ping), ARC_FADE if age == 2 else ARC)


def bolt_held(f: Frame, pose: Pose) -> None:
    """Static crackles at the flailing hands."""
    i = pose.i
    with f.offset(pose.dx, pose.dy):
        up_left = i % 2 == 0
        hand = (9, 15) if up_left else (38, 15)
        f.glyph(hand[0] - 1, hand[1] - 2, _BOLT_PLUS, _SPARK_PALETTE)
        low = (8, 33) if not up_left else (39, 33)
        zig = [(low[0], low[1]), (low[0] + 1, low[1] + 1), (low[0], low[1] + 2)]
        f.paint(polyline(zig), ARC)


BOLT_ANCHORS = Anchors(
    arcs_left=(12, 15),
    arcs_right=(35, 15),
    dots=(19, 2),
    zzz=(36, 14),
    bang=(38, 3),
    sweat=(35, 9),
    sparkles=((6, 10, 1), (41, 8, 2), (5, 36, 3), (42, 34, 3)),
)

BOLT = PetDesign(
    id="bolt",
    name="Bolt",
    description="A plucky battery that charges up while it thinks and runs flat when it sleeps.",
    draw=draw_bolt,
    waist=23,
    anchors=BOLT_ANCHORS,
    pose=bolt_pose,
    frame_counts={"thinking": BOLT_THINK_FRAMES},
    fx={
        "idle": bolt_idle,
        "listening": bolt_listening,
        "thinking": bolt_thinking,
        "talking": bolt_talking,
        "success": bolt_success,
        "error": bolt_error,
        "sleeping": bolt_sleeping,
        "working": bolt_working,
        "searching": bolt_searching,
        "held": bolt_held,
    },
)


# -- Mochi: the jelly blob --------------------------------------------------------
#
# A soft daifuku of pink jelly. Everything Mochi does is squash and stretch:
# it jiggles when it types, grows a periscope to look around, pulls out like
# dough when it is dragged and melts a little when something goes wrong.

MOCHI_BASE = hexc("#ff9fcb")
MOCHI_LIGHT = hexc("#ffd3e8")
MOCHI_DARK = hexc("#e06aa2")
MOCHI_BLUSH = hexc("#ff5f9a")
MOCHI_INK = hexc("#3b1f2b")
MOCHI_EYES = EyeStyle(w=3, h=4, iris=MOCHI_INK, glint=FX_WHITE, lid=MOCHI_INK)
#: The periscope's glass and the faint line of sight it casts.
MOCHI_LENS = hexc("#5fc4ff")
MOCHI_LENS_DARK = hexc("#2f7fb8")

#: (wider, lower, sway) per listening frame: the wobble.
_MOCHI_WOBBLE = ((0, 0, 0), (1, 1, 1), (1, 1, 1), (0, 0, 0), (-1, -1, -1), (-1, -1, -1))
#: (wider, lower) per talking frame, closed to widest: the louder, the squishier.
_MOCHI_SQUISH = ((0, 0), (1, 0), (1, 1), (2, 2))
#: Idle micro-actions over the seven loop cells: a shine slides over the
#: dome (x of the shine), then a jiggle runs through the jelly:
#: (wider, lower, sway) per cell.
_MOCHI_SHINE = (None, 18, 22, 26, 30, None, None)
_MOCHI_IDLE_JIGGLE = ((0, 0, 0),) * 5 + ((1, 1, 1), (-1, -1, -1))
#: Listening: the dome leans towards the sound on the right.
_MOCHI_LEAN = (1, 2, 2, 2, 1, 0)
#: Thinking: a slow jiggle while bubbles rise through the jelly.
_MOCHI_THINK_SWAY = (0, 0, 1, 1, 0, 0, -1, -1)
#: A bubble's path by age, left stream (the right one is mirrored); at age
#: 6 it leaves the dome as a thought bubble and pops at age 7.
_MOCHI_BUBBLE_PATH = ((12, 40), (12, 37), (12, 34), (13, 31), (14, 28), (17, 24))
#: Success: (wider, lower) and the height of the bounce per frame — a deep
#: squash, a tall stretch on the way up, round at the top, a splat on landing.
_MOCHI_HOP_SHAPE = ((3, 3), (-2, -3), (-1, -2), (0, 0), (0, -1), (-2, -3), (4, 4), (1, 1))
_MOCHI_HOP_DY = (0, -3, -6, -7, -6, -3, 0, 0)
#: Success: where the two droplets that pop off the top fly (left one).
_MOCHI_DROPLETS = {1: (19, 13), 2: (15, 9), 3: (11, 7), 4: (9, 10), 5: (10, 17), 6: (12, 27)}
#: Error: the blob melts — (wider, lower) per frame — and drips grow under it.
_MOCHI_MELT = ((0, 0), (1, 0), (1, 1), (2, 1), (2, 2), (3, 2))
#: (x, first frame) of each drip along the bottom edge.
_MOCHI_DRIPS = ((15, 1), (31, 2), (22, 3))
#: Working: which nub taps a key per frame (L, R or a pause).
_MOCHI_TAPS = "LRL.RLR."
#: Searching: where the periscope looks per frame (-1 left, 0 ahead, 1 right).
_MOCHI_SCOPE = (-1, -1, -1, 0, 1, 1, 1, 0)
_MOCHI_SCOPE_TOP = (14, 13, 13, 13, 13, 13, 13, 14)
#: Held: the sagging bottom swings, the dough neck stretches and recoils.
_MOCHI_HELD_SWAY = (-2, -1, 1, 2, 1, -1)
_MOCHI_HELD_TOP = (5, 3, 4, 6, 4, 3)


def mochi_pose(state: str, i: int) -> Pose:
    pose = base_pose(state, i)
    if state == "talking":
        # Mochi squishes instead of bobbing: the body gets lower, not higher.
        return Pose(state, i, mouth=pose.mouth)
    if state == "success":
        mouth = "open" if 2 <= i <= 4 else "smile"
        return Pose(state, i, dy=_MOCHI_HOP_DY[i], eyes="happy", mouth=mouth)
    if state == "listening":
        return replace(pose, eyes="look_right" if i < 5 else "open")
    if state == "working":
        return replace(pose, dy=0)
    if state == "searching":
        look = _MOCHI_SCOPE[i]
        eyes = "look_left" if look < 0 else "look_right" if look > 0 else "up_left"
        return replace(pose, eyes=eyes, mouth="rest" if look else "closed")
    if state == "held":
        return Pose(state, i, eyes="happy" if i % 3 else "wide", mouth="open")
    return pose


def _mochi_shape(pose: Pose) -> tuple[int, int, int]:
    """(wider, lower, sway) of the blob for this frame."""
    i = pose.i
    if pose.state == "idle":
        return _MOCHI_IDLE_JIGGLE[i] if i < len(_MOCHI_IDLE_JIGGLE) else (0, 0, 0)
    if pose.state == "listening":
        wider, lower, _ = _MOCHI_WOBBLE[i % len(_MOCHI_WOBBLE)]
        return (wider, lower, _MOCHI_LEAN[i % len(_MOCHI_LEAN)])
    if pose.state == "thinking":
        return (0, 0, _MOCHI_THINK_SWAY[i % len(_MOCHI_THINK_SWAY)])
    if pose.state == "talking":
        return (*_MOCHI_SQUISH[i % len(_MOCHI_SQUISH)], 0)
    if pose.state == "success":
        return (*_MOCHI_HOP_SHAPE[i % len(_MOCHI_HOP_SHAPE)], 0)
    if pose.state == "error":
        return (*_MOCHI_MELT[i % len(_MOCHI_MELT)], 0)
    if pose.state == "sleeping":
        return (4, 4, 0)
    if pose.state == "working":
        # Every keystroke squishes the jelly a pixel.
        return (1, 1, 0) if _MOCHI_TAPS[i % len(_MOCHI_TAPS)] != "." else (0, 0, 0)
    if pose.state == "searching":
        return (1, 1, 0)
    return (0, 0, 0)


def _mochi_body(wider: int, lower: int, sway: int) -> Mask:
    """A daifuku-round blob; ``sway`` leans its soft dome sideways."""
    blob = {p for p in ellipse(CX, 33, 15 + wider, 11 - lower) if p[1] <= 43}
    dome = ellipse(CX + sway, 28 + lower, 11 + max(0, wider), 7)
    return blob | dome


def _mochi_paint_body(f: Frame, body: Mask) -> None:
    f.part(body, _shade(body, MOCHI_BASE, MOCHI_LIGHT, MOCHI_DARK, 3))


def _mochi_face(f: Frame, pose: Pose, eye_y: int, dx: int = 0) -> None:
    draw_eyes(f, pose.eyes, (17 + dx, eye_y), (28 + dx, eye_y), MOCHI_EYES)
    blush = rect(13 + dx, eye_y + 4, 15 + dx, eye_y + 4)
    f.paint(blush | shifted(mirrored(blush), 2 * dx, 0), MOCHI_BLUSH)
    draw_mouth(f, pose.mouth, 22 + dx, eye_y + 5, 4, color=MOCHI_INK)


def _mochi_top(body: Mask, x: int) -> int:
    """The first row of ``body`` in column ``x``."""
    return min(y for px, y in body if px == x)


def draw_mochi(f: Frame, pose: Pose) -> None:
    if pose.state == "held":
        _draw_mochi_dough(f, pose)
        return
    wider, lower, sway = _mochi_shape(pose)
    with f.offset(pose.dx, pose.dy):
        body = _mochi_body(wider, lower, sway)
        if pose.state == "listening":
            # A little jelly nub cupped towards the sound, like a hand to an ear.
            lean = _MOCHI_LEAN[pose.i % len(_MOCHI_LEAN)]
            body |= ellipse(35 + lean, 24 + lower, 2.4, 2.6)
        if pose.state == "searching":
            body |= _mochi_periscope(pose.i)
        if pose.state == "error":
            body |= _mochi_drips(pose.i)
        _mochi_paint_body(f, body)
        inner = eroded(body, 1)
        glint = {(15, 25 + lower), (16, 24 + lower), (17, 24 + lower), (14, 26 + lower)}
        f.paint(glint & inner, FX_WHITE)
        if pose.state == "idle" and pose.i < len(_MOCHI_SHINE):
            _mochi_shine(f, body, _MOCHI_SHINE[pose.i])
        if pose.state == "thinking":
            _mochi_bubbles(f, pose.i, inner)
        if pose.state == "searching":
            _mochi_lens(f, pose.i)
        eye_y = 30 + max(0, lower) // 2
        if pose.state == "working" and _MOCHI_TAPS[pose.i % len(_MOCHI_TAPS)] != ".":
            # The screen's light on Mochi's chin, brighter on a keystroke.
            f.paint(rect(19, eye_y + 6, 28, eye_y + 6) & inner, MOCHI_LIGHT)
        _mochi_face(f, pose, eye_y)
    if pose.state == "working":
        _mochi_laptop(f, pose.i)


def _mochi_shine(f: Frame, body: Mask, x: int | None) -> None:
    """Idle: a glossy streak sliding over the dome, one step a frame."""
    if x is None:
        return
    for px in (x - 1, x, x + 1):
        y = _mochi_top(body, px) + 1
        f.paint({(px, y)}, FX_WHITE)
        if px != x + 1:
            f.paint({(px, y + 1)}, FX_WHITE if px == x else MOCHI_LIGHT)


def _mochi_bubbles(f: Frame, i: int, inner: Mask) -> None:
    """Thinking: air bubbles rising inside the jelly, two streams, half a loop apart."""
    for stream, start in ((0, 0), (1, 4)):
        age = (i - start) % 8
        if age >= len(_MOCHI_BUBBLE_PATH):
            continue
        x, y = _MOCHI_BUBBLE_PATH[age]
        if stream:
            x = 2 * CX - 1 - x - (2 if age >= 2 else 1) + 1
        if age == 0:
            bubble: Mask = {(x, y)}
        elif age == 1:
            bubble = rect(x, y, x + 1, y + 1)
        else:
            bubble = from_rows(x, y, (".#.", "#.#", ".#."))
        f.paint(bubble & inner, MOCHI_LIGHT if age < 2 else FX_WHITE)


def mochi_thinking(f: Frame, pose: Pose) -> None:
    """The bubbles that reach the top leave the dome as thought bubbles and pop."""
    for stream, start in ((0, 0), (1, 4)):
        age = (pose.i - start) % 8
        x0 = 18 if stream == 0 else 26
        if age == 6:
            bubble = ellipse(x0 + 0.5 + (1 if stream else -1), 17.5, 2.6, 2.6)
            ring = bubble - eroded(bubble, 1)
            f.part(ring, FX_WHITE)
            f.paint({(min(x for x, _ in ring) + 1, 16)}, FX_WHITE)
        elif age == 7:
            cx, cy = x0 + (2 if stream else -2), 13
            pops = {(cx, cy - 3), (cx, cy + 3), (cx - 3, cy), (cx + 3, cy)}
            pops |= {(cx - 2, cy - 2), (cx + 2, cy - 2), (cx - 2, cy + 2), (cx + 2, cy + 2)}
            f.paint(pops, FX_DIM)


def _mochi_periscope(i: int) -> Mask:
    """Searching: a jelly stalk out of the dome with a sideways head on top."""
    look = _MOCHI_SCOPE[i % len(_MOCHI_SCOPE)]
    top = _MOCHI_SCOPE_TOP[i % len(_MOCHI_SCOPE_TOP)]
    stalk = rect(22, top, 25, 25)
    head = ellipse(CX + 2 * look, top, 4.6 if look else 3.6, 2.8)
    return stalk | head


def _mochi_lens(f: Frame, i: int) -> None:
    """The periscope's glass, on the side it looks to."""
    look = _MOCHI_SCOPE[i % len(_MOCHI_SCOPE)]
    top = _MOCHI_SCOPE_TOP[i % len(_MOCHI_SCOPE_TOP)]
    head = ellipse(CX + 2 * look, top, 4.6 if look else 3.6, 2.8)
    if look == 0:
        lens = rect(22, top - 1, 25, top) - {(22, top - 1), (25, top - 1)}
        f.paint(lens, MOCHI_LENS_DARK)
        f.paint({(23, top - 1)}, FX_WHITE)
        f.paint({(23, top), (24, top)}, MOCHI_LENS)
        return
    edge = min(x for x, _ in head) if look < 0 else max(x for x, _ in head)
    inward = 1 if look < 0 else -1
    f.paint(rect(edge, top - 1, edge, top + 1), MOCHI_LENS)
    f.paint(rect(edge + inward, top - 1, edge + inward, top + 1), MOCHI_LENS_DARK)
    f.paint({(edge, top - 1)}, FX_WHITE)


def mochi_searching(f: Frame, pose: Pose) -> None:
    """A dotted line of sight from the periscope's glass; at the far right it finds something."""
    i = pose.i
    look = _MOCHI_SCOPE[i % len(_MOCHI_SCOPE)]
    if look == 0:
        return
    top = _MOCHI_SCOPE_TOP[i % len(_MOCHI_SCOPE_TOP)]
    head = ellipse(CX + 2 * look, top, 4.6, 2.8)
    edge = min(x for x, _ in head) if look < 0 else max(x for x, _ in head)
    for k, step in enumerate((3, 5, 7)):
        x = edge + look * (step + i % 2)
        f.paint({(x, top)}, ARC if k == 0 else ARC_FADE)
    if i == 6:
        fx_sparkles(f, 1, ((edge + 7, top - 5, 0),))


def _mochi_drips(i: int) -> Mask:
    """Error: drips of melting jelly growing out of the bottom edge."""
    out: Mask = set()
    for x, start in _MOCHI_DRIPS:
        length = min(3, i - start + 1)
        if length <= 0:
            continue
        out |= rect(x, 43, x + 1, 43 + length)
        if length >= 2:
            out |= rect(x - 1, 43 + length, x + 2, 43 + length) - {(x - 1, 44), (x + 2, 44)}
    return out


def mochi_error(f: Frame, pose: Pose) -> None:
    """The red "!" over a melting, shaking blob."""
    fx_bang(f, 39, 6)


def _mochi_droplet(f: Frame, x: int, y: int) -> None:
    drop = ellipse(x + 0.5, y + 0.5, 1.7, 1.7)
    f.part(drop, MOCHI_BASE)
    f.paint({(x - 1, y - 1)} & drop or {(x, y - 1)}, MOCHI_LIGHT)


def mochi_success(f: Frame, pose: Pose) -> None:
    """Two droplets pop off the top, fly out and drop back into the jelly."""
    spot = _MOCHI_DROPLETS.get(pose.i)
    if spot is not None:
        x, y = spot
        _mochi_droplet(f, x, y)
        _mochi_droplet(f, 2 * CX - 1 - x, y)
    fx_sparkles(f, pose.i, ((5, 38, 2), (42, 36, 3), (CX, 5, 3)))


def mochi_talking(f: Frame, pose: Pose) -> None:
    """Louder speech squishes harder: little boing marks at both sides."""
    if pose.mouth not in ("open", "wide"):
        return
    color = ARC if pose.mouth == "wide" else ARC_FADE
    marks = line(5, 31, 5, 35) | line(42, 31, 42, 35)
    if pose.mouth == "wide":
        marks = line(3, 29, 3, 37) | line(44, 29, 44, 37) | line(5, 31, 5, 35)
        marks |= line(42, 31, 42, 35)
    f.paint(marks, color)


def mochi_sleeping(f: Frame, pose: Pose) -> None:
    """A snot bubble that swells and shrinks with each breath, z marks drifting up."""
    r = (1.6, 2.2, 2.8, 3.4, 2.8, 2.2)[pose.i % 6]
    cx, cy = 27.5 + r, 37.5 - r * 0.6
    bubble = ellipse(cx, cy, r, r)
    f.part(bubble, SWEAT)
    f.paint({p for p in bubble if p[1] == min(y for _, y in bubble)}, SWEAT_SHINE)
    fx_zzz(f, pose.i, 36, 20)


def _mochi_capsule(a: Px, b: Px, r: float) -> Mask:
    """A soft jelly limb: discs of radius ``r`` stamped along a line."""
    out: Mask = set()
    for x, y in line(*a, *b):
        out |= ellipse(x + 0.5, y + 0.5, r, r)
    return out


def _mochi_laptop(f: Frame, i: int) -> None:
    """Working: a small laptop in front, the two jelly nubs tap its keys."""
    lid = rounded_rect(18, 38, 29, 43, 1)
    f.part(lid, shade(lid, LAPTOP_LID, light=LAPTOP_LID_LIGHT, dark=LAPTOP_LID, dark_depth=1))
    heart = from_rows(22, 39, ("#..#", "####", ".##."))
    f.paint(heart, MOCHI_BLUSH if i % 4 != 3 else LAPTOP_LID_LIGHT)
    base = rect(15, 44, 32, 45)
    f.part(base, {p: (LAPTOP_BASE if p[1] == 44 else LAPTOP_BASE_DARK) for p in base})
    tap = _MOCHI_TAPS[i % len(_MOCHI_TAPS)]
    for side in "LR":
        down = tap == side
        tip = (17, 42) if down else (16, 39)
        arm = _mochi_capsule((11, 37), tip, 1.6) | ellipse(tip[0] + 0.5, tip[1] + 0.5, 1.9, 1.6)
        if side == "R":
            arm = mirrored(arm)
        f.part(arm, _shade(arm, MOCHI_BASE, MOCHI_LIGHT, MOCHI_DARK, 1))
        if down:
            # The key lights up under the nub.
            key = (17, 44) if side == "L" else (30, 44)
            f.paint({key}, FX_WHITE)


#: Working: code chips rising off the screen, one every four frames.
_MOCHI_CHIPS = (("#.#", "#.#", ".#."), ("##.", ".##", "##."), (".#.", "##.", ".#."))


def mochi_working(f: Frame, pose: Pose) -> None:
    """A code chip rises off the screen on the right; key ticks pop above the lid."""
    age = pose.i % 4
    glyph = _MOCHI_CHIPS[(pose.i // 4) % len(_MOCHI_CHIPS)]
    f.glyph(41, 34 - 3 * age, glyph, {"#": CODE_CHIP})
    tap = _MOCHI_TAPS[pose.i % len(_MOCHI_TAPS)]
    if tap != ".":
        x = 15 if tap == "L" else 32
        f.paint({(x, 42), (x - 1 if tap == "L" else x + 1, 41)}, FX_WHITE)


def _mochi_dough_mask(i: int) -> Mask:
    """Held: pulled up like mochi dough — a long thin neck from the cursor down
    to a heavy, sagging bottom that swings."""
    sway = _MOCHI_HELD_SWAY[i % len(_MOCHI_HELD_SWAY)]
    top = _MOCHI_HELD_TOP[i % len(_MOCHI_HELD_TOP)]
    rx, ry = (13, 7) if i % 2 == 0 else (12, 7.6)
    blob = ellipse(CX + sway, 38, rx, ry)
    neck: Mask = set()
    bottom = 34
    for y in range(top, bottom + 1):
        t = (y - top) / (bottom - top)
        hw = 1.6 + 8.0 * t * t
        cx = CX + sway * t
        neck |= {(x, y) for x in range(4, 44) if abs(x + 0.5 - cx) <= hw}
    return blob | neck


def _draw_mochi_dough(f: Frame, pose: Pose) -> None:
    sway = _MOCHI_HELD_SWAY[pose.i % len(_MOCHI_HELD_SWAY)]
    body = _mochi_dough_mask(pose.i)
    _mochi_paint_body(f, body)
    top = _MOCHI_HELD_TOP[pose.i % len(_MOCHI_HELD_TOP)]
    # Shine down the stretched neck: it is pulled thin and glossy.
    f.paint({(23, top + 2), (23, top + 3)} & eroded(body, 1), FX_WHITE)
    glint = {(15 + sway, 34), (16 + sway, 33), (17 + sway, 33)}
    f.paint(glint & eroded(body, 1), FX_WHITE)
    _mochi_face(f, pose, 34, sway)


def mochi_held(f: Frame, pose: Pose) -> None:
    """Swing streaks on the trailing side of the sagging bottom and a pinch at the top."""
    sway = _MOCHI_HELD_SWAY[pose.i % len(_MOCHI_HELD_SWAY)]
    prev = _MOCHI_HELD_SWAY[(pose.i - 1) % len(_MOCHI_HELD_SWAY)]
    moving = sway - prev
    if moving:
        x = CX + sway + (-17 if moving > 0 else 16)
        step = -2 if moving > 0 else 2
        f.paint(line(x, 35, x, 40), ARC_FADE)
        f.paint(line(x + step, 36, x + step, 39), ARC_FADE)
    top = _MOCHI_HELD_TOP[pose.i % len(_MOCHI_HELD_TOP)]
    if top <= 3:
        # Stretched to the limit: tiny strain marks beside the pinch.
        f.paint({(20, top + 1), (19, top), (28, top + 1), (29, top)}, ARC)


MOCHI_ANCHORS = Anchors(
    arcs_left=(8, 24),
    arcs_right=(39, 17),
    dots=(19, 15),
    zzz=(36, 20),
    bang=(39, 6),
    sweat=(33, 22),
    sparkles=((5, 38, 2), (42, 36, 3), (CX, 5, 3)),
)


def mochi_listening(f: Frame, pose: Pose) -> None:
    """Sound arcs only on the side Mochi leans and cups its nub towards."""
    x, y = MOCHI_ANCHORS.arcs_right
    for index, fading in _ARC_PHASES[pose.i % len(_ARC_PHASES)]:
        off, arc = _ARCS[index]
        f.paint({(x + off + ax, y + pose.dy + ay) for ax, ay in arc}, ARC_FADE if fading else ARC)


MOCHI = PetDesign(
    id="mochi",
    name="Mochi",
    description="A soft jelly blob that wobbles when it listens and squishes when it talks.",
    draw=draw_mochi,
    anchors=MOCHI_ANCHORS,
    pose=mochi_pose,
    waist=39,
    fx={
        "listening": mochi_listening,
        "thinking": mochi_thinking,
        "talking": mochi_talking,
        "success": mochi_success,
        "error": mochi_error,
        "sleeping": mochi_sleeping,
        "working": mochi_working,
        "searching": mochi_searching,
        "held": mochi_held,
    },
)


# -- Shelly: the snail ------------------------------------------------------------
#
# A snail facing left: a soft green foot, an orange shell with a dark spiral
# and two eye stalks that do most of the acting. The stalks bob, lean,
# telescope, droop and curl; the spiral spins while Shelly thinks or works.

SHELLY_BODY = hexc("#c3de7a")
SHELLY_BODY_LIGHT = hexc("#e3f4a9")
SHELLY_BODY_DARK = hexc("#8eae4c")
SHELLY_SHELL = hexc("#e08a4f")
SHELLY_SHELL_LIGHT = hexc("#f5b884")
SHELLY_SHELL_DARK = hexc("#a95a2c")
SHELLY_SPIRAL = hexc("#6b3417")
SHELLY_BLUSH = hexc("#ff8fa3")
SHELLY_EYES = EyeStyle(w=5, h=5, iris=FX_WHITE, pupil=OUTLINE, glint=None, lid=OUTLINE)
#: The slime trail: a mid sea-green that holds on dark AND light desktops.
SHELLY_SLIME = hexc("#7cc7b0")
#: The loading comet that runs round the shell while working.
SHELLY_SPIN = hexc("#fff3d6")
#: Asleep, the heart of the spiral glows like a night light.
SHELLY_GLOW = hexc("#ffd46e")
SHELLY_MUG = hexc("#f2ece4")
SHELLY_COFFEE = hexc("#6b3417")
SHELLY_FLAG = hexc("#8fb4ff")
SHELLY_CLOUD = hexc("#f4f6fb")
SHELLY_CLOUD_DARK = hexc("#c4cad8")
SHELLY_CLOUD_DOT = hexc("#5d6476")

#: Eye-stalk tips (left, right) per look; the stalks grow from the head top.
_SHELLY_TIPS: dict[str, tuple[Px, Px]] = {
    "rest": ((8, 15), (17, 14)),
    "perk": ((7, 12), (18, 11)),
    "droop": ((5, 20), (19, 19)),
}
_SHELLY_STALK_BASES: tuple[Px, Px] = ((10, 26), (15, 26))
#: Pupil offset inside the 5 x 5 eyeball per look; Shelly faces left. A
#: pupil never sits in a corner, where it would merge with the outline.
_SHELLY_PUPILS: dict[str, Px] = {
    "open": (1, 2),
    "wide": (1, 2),
    "up_left": (0, 1),
    "up_right": (3, 1),
    "up": (1, 0),
    "look_left": (0, 2),
    "look_right": (3, 2),
    "down": (2, 3),
    "down_left": (0, 2),
}
#: Idle: the stalks bob one after the other, (left, right) lift per cell.
_SHELLY_IDLE_BOB = ((0, 0), (-1, 0), (-1, 0), (0, 0), (0, -1), (0, -1), (0, 0))
#: Listening: both stalks lean towards the voice, then settle back.
_SHELLY_LEAN = (0, -1, -2, -2, -1, 0)
#: Talking: stalk lift per voice level, closed to widest.
_SHELLY_TALK_LIFT = (0, 1, 2, 3)
#: Searching: each stalk telescopes and swivels on its own,
#: (left tip, left look, right tip, right look) per frame.
_SHELLY_SCAN: tuple[tuple[Px, str, Px, str], ...] = (
    ((7, 15), "look_left", (17, 14), "look_right"),
    ((5, 13), "look_left", (19, 12), "up_right"),
    ((4, 10), "up_left", (21, 10), "look_right"),
    ((6, 8), "up_left", (22, 7), "up_right"),
    ((9, 8), "up", (19, 7), "up"),
    ((8, 10), "look_right", (16, 9), "look_left"),
    ((5, 12), "look_left", (19, 11), "look_right"),
    ((6, 14), "look_left", (18, 13), "up_right"),
)
#: Where each look sends the scanning glance (a dotted line out of the eye).
_SHELLY_GLANCE: dict[str, Px] = {
    "look_left": (-1, 0),
    "look_right": (1, 0),
    "up_left": (-1, -1),
    "up_right": (1, -1),
    "up": (0, -1),
}
#: Error: the spiral wobbles back and forth while the stalks pull in.
_SHELLY_WOBBLE = (0.7, -0.6, 0.45, -0.3, 0.15, 0.0)
#: Working: how far the head and shell move right to make room for the laptop.
_SHELLY_DESK = 4
#: Held: the swing from the cursor, and which way the stalks curl.
_SHELLY_SWAY = (-1, 0, 1, 1, 0, -1)
#: Sleeping: how bright the spiral's heart glows (0 off, 1 half, 2 full).
_SHELLY_GLOW_PHASE = (0, 1, 2, 2, 1, 0)


def _spiral(cx: float, cy: float, phase: float, r_max: float = 8.5, turns: float = 2.25) -> Mask:
    """A 1 px Archimedean spiral; values are rounded before flooring so the
    same pixels come out on every platform's libm."""
    out: Mask = set()
    steps = 160
    for s in range(steps + 1):
        t = s / steps
        r = 1.0 + (r_max - 1.0) * t
        a = phase + 2.0 * math.pi * turns * t
        x = math.floor(round(cx + r * math.cos(a), 6))
        y = math.floor(round(cy + r * math.sin(a), 6))
        out.add((x, y))
    return out


def _shelly_polar(cx: float, cy: float, r: float, a: float) -> Px:
    return (math.floor(round(cx + r * math.cos(a), 6)), math.floor(round(cy + r * math.sin(a), 6)))


def shelly_pose(state: str, i: int) -> Pose:
    if state == "working":
        return Pose(state, i, eyes="down_left", mouth="closed")
    if state == "searching":
        return Pose(state, i, eyes="scan", mouth="closed")
    if state == "held":
        return Pose(state, i, dx=_SHELLY_SWAY[i], dy=-3, eyes="wide", mouth="half")
    pose = base_pose(state, i)
    if state == "idle" and i >= len(IDLE_BREATH):
        return replace(pose, eyes="blink")  # one stalk blinks, slowly
    return pose


def _shelly_stalks(pose: Pose) -> tuple[tuple[Px, Px], tuple[str, str]]:
    """The two stalk tips and the look of each eye for this pose."""
    state, i = pose.state, pose.i
    looks = (pose.eyes, pose.eyes)
    if state == "searching":
        left, left_look, right, right_look = _SHELLY_SCAN[i % len(_SHELLY_SCAN)]
        return (left, right), (left_look, right_look)
    if state == "working":
        nod = WORK_BOB[i % len(WORK_BOB)]
        return ((4, 20 + nod), (10, 17 + nod)), looks
    look = {"listening": "perk", "success": "perk", "error": "droop"}.get(state, "rest")
    (lx, ly), (rx, ry) = _SHELLY_TIPS[look]
    if state == "idle":
        if i < len(_SHELLY_IDLE_BOB):
            ly += _SHELLY_IDLE_BOB[i][0]
            ry += _SHELLY_IDLE_BOB[i][1]
        else:
            ly += 1  # the blinking stalk sags a little
            looks = ("closed", "open")
    elif state == "listening":
        lean = _SHELLY_LEAN[i % len(_SHELLY_LEAN)]
        lx, rx = lx + lean, rx + lean
        ly, ry = ly - (lean < -1), ry - (lean < -1)
    elif state == "thinking":
        lift = 1 if 2 <= i <= 5 else 0
        ly, ry = ly - lift, ry - lift
    elif state == "talking":
        lift = _SHELLY_TALK_LIFT[i % len(_SHELLY_TALK_LIFT)]
        lx, ly, rx, ry = lx - lift // 2, ly - lift, rx + lift // 2, ry - lift
    elif state == "success":
        if pose.dy <= -4:
            ly, ry = ly - 2, ry - 2  # a happy stretch at the top of the hop
    elif state == "error":
        # Drooping, then pulling in towards the head.
        t = 0.55 * min(i, 5) / 5
        (bx0, by0), (bx1, by1) = _SHELLY_STALK_BASES
        lx, ly = round(lx + (bx0 - lx) * t), round(ly + (by0 - 4 - ly) * t)
        rx, ry = round(rx + (bx1 - rx) * t), round(ry + (by1 - 4 - ry) * t)
    return ((lx, ly), (rx, ry)), looks


def _shelly_eye(f: Frame, style: str, tip: Px, *, right: bool) -> None:
    x, y = tip[0] - 2, tip[1] - 2
    ball = _oval(x, y, 5, 5)
    if style in _SHELLY_PUPILS:
        f.part(ball, FX_WHITE)
        dx, dy = _SHELLY_PUPILS[style]
        f.paint(rect(x + dx, y + dy, x + dx + 1, y + dy + 1), OUTLINE)
        return
    f.part(ball, SHELLY_BODY)
    draw_eye(f, style, x, y, SHELLY_EYES, right=right)


def _shelly_stalk(f: Frame, base: Px, tip: Px, *, rings: bool = False) -> None:
    """One eye stalk; ``rings`` marks the joints of a telescoping stalk."""
    path = line(*base, *tip)
    if not rings:
        f.part(path, SHELLY_BODY)
        return
    # Telescoping: a wide lower tube, a thin upper one, a ring at the joint.
    ordered = sorted(path, key=lambda p: (p[0] - base[0]) ** 2 + (p[1] - base[1]) ** 2)
    lower = thick(set(ordered[: len(ordered) // 2]))
    f.part(path | lower, SHELLY_BODY)
    f.paint(lower & {(x, y) for x, y in lower if (x - 1, y) not in lower}, SHELLY_BODY_LIGHT)
    joint = ordered[len(ordered) // 2 - 1]
    f.paint(rect(joint[0], joint[1], joint[0] + 1, joint[1]), SHELLY_BODY_DARK)


def _shelly_phase(pose: Pose) -> float:
    """The spiral's turn: it spins while thinking and working, whirls on a
    success and wobbles on an error."""
    if pose.state in ("thinking", "working"):
        return -pose.i * (2.0 * math.pi / 8)
    if pose.state == "success":
        return -pose.i * (2.0 * math.pi / 4)
    if pose.state == "error":
        return _SHELLY_WOBBLE[pose.i % len(_SHELLY_WOBBLE)]
    return 0.0


def draw_shelly(f: Frame, pose: Pose) -> None:
    if pose.state == "sleeping":
        _draw_shelly_withdrawn(f, pose)
        return
    if pose.state == "held":
        _draw_shelly_held(f, pose)
        return
    tips, looks = _shelly_stalks(pose)
    working = pose.state == "working"
    ox = _SHELLY_DESK if working else 0
    nod = WORK_BOB[pose.i % len(WORK_BOB)] if working else 0
    with f.offset(pose.dx, pose.dy):
        for base, tip in zip(_SHELLY_STALK_BASES, tips, strict=True):
            base = (base[0] + ox, base[1] + nod)
            _shelly_stalk(f, base, (tip[0] + ox, tip[1]), rings=pose.state == "searching")
        foot = ellipse(26 + ox / 2, 41, 17 - ox / 2, 3.2)
        body = foot | ellipse(13 + ox, 33 + nod, 6, 8)
        f.part(body, _shade(body, SHELLY_BODY, SHELLY_BODY_LIGHT, SHELLY_BODY_DARK, 1))
        scx = 28 + ox
        shell = ellipse(scx, 28, 11, 11)
        f.part(shell, _shade(shell, SHELLY_SHELL, SHELLY_SHELL_LIGHT, SHELLY_SHELL_DARK))
        inner = eroded(shell, 1)
        f.paint(_spiral(float(scx), 28.0, _shelly_phase(pose)) & inner, SHELLY_SPIRAL)
        if working:
            _shelly_loading(f, pose.i, scx)
            _shelly_mug(f, pose.i, scx)
        f.paint(rect(12 + ox, 37 + nod, 13 + ox, 37 + nod), SHELLY_BLUSH)
        draw_mouth(f, pose.mouth, 8 + ox, 34 + nod, 3)
        _shelly_eye(f, looks[0], (tips[0][0] + ox, tips[0][1]), right=False)
        _shelly_eye(f, looks[1], (tips[1][0] + ox, tips[1][1]), right=True)


def _shelly_loading(f: Frame, i: int, scx: int) -> None:
    """A bright comet runs round the shell's rim: the spiral as a loading wheel."""
    head = -math.pi / 2 + i * (2.0 * math.pi / 8)
    for k, color in enumerate((SHELLY_SHELL_LIGHT, SHELLY_SPIN, FX_WHITE)[::-1]):
        x, y = _shelly_polar(scx - 0.5, 27.5, 8.0, head - 0.4 * k)
        f.paint(rect(x, y, x + 1, y + 1) if k < 2 else {(x, y)}, color)


def _shelly_mug(f: Frame, i: int, scx: int) -> None:
    """The shell is the desk: a little mug of coffee on top, steaming."""
    x = scx + 1
    mug = rect(x, 13, x + 3, 16)
    f.part(mug, SHELLY_MUG)
    f.paint(rect(x, 13, x + 3, 13), SHELLY_COFFEE)
    f.paint({(x + 5, 14), (x + 5, 15)}, OUTLINE)
    wisp = ({(x + 1, 11), (x + 2, 10)}, {(x + 2, 11), (x + 1, 10)}, {(x + 1, 10)}, set())
    f.paint(wisp[i % 4], FX_DIM)


def _draw_shelly_withdrawn(f: Frame, pose: Pose) -> None:
    """Asleep: tucked into the shell, only a sliver of foot peeking out, a
    little night flag on top and the spiral's heart glowing softly."""
    with f.offset(pose.dx, pose.dy):
        foot = ellipse(26, 43, 13, 1.6)
        f.part(foot, SHELLY_BODY_DARK)
        _shelly_flag(f, pose.i)
        shell = ellipse(26, 32, 11, 10.5)
        f.part(shell, _shade(shell, SHELLY_SHELL, SHELLY_SHELL_LIGHT, SHELLY_SHELL_DARK))
        spiral = _spiral(26.0, 32.0, 0.0, r_max=8.0) & eroded(shell, 1)
        f.paint(spiral, SHELLY_SPIRAL)
        glow = _SHELLY_GLOW_PHASE[pose.i % len(_SHELLY_GLOW_PHASE)]
        if glow:
            reach = 2.6 if glow == 1 else 4.6
            f.paint(
                {p for p in spiral if (p[0] - 25.5) ** 2 + (p[1] - 31.5) ** 2 <= reach**2},
                SHELLY_GLOW,
            )
        f.paint(ellipse(17.5, 38.5, 2, 2) & shell, SHELLY_SPIRAL)


#: The night flag's pennant, waving between two shapes.
_SHELLY_PENNANTS = (("..###", "#####", "..###"), ("...##", "#####", ".####"))


def _shelly_flag(f: Frame, i: int) -> None:
    """A little night flag on a pole, its pennant waving slowly."""
    pole = rect(23, 14, 23, 22)
    f.part(pole, SHELLY_SPIRAL)
    f.glyph(18, 14, _SHELLY_PENNANTS[(i // 2) % 2], {"#": SHELLY_FLAG})
    f.paint({(23, 13)}, SHELLY_GLOW)


def _draw_shelly_held(f: Frame, pose: Pose) -> None:
    """Held: half withdrawn into the shell, the foot dangling and wriggling,
    the stalks curled up in surprise."""
    i = pose.i
    with f.offset(pose.dx, pose.dy):
        # The foot hangs below the shell and wriggles from side to side.
        foot: Mask = set()
        for y in range(33, 45):
            depth = y - 33
            half = 4 if depth < 5 else 3 if depth < 9 else 2
            wave = round(1.6 * math.sin(round(depth * 0.7 - i * math.pi / 3, 6)))
            cx = 25 + (wave if depth > 2 else 0)
            foot |= rect(cx - half, y, cx + half - 1, y)
        head = ellipse(17, 31, 5, 5.5)
        curl = 1 if i % 2 == 0 else -1
        stalks = (
            polyline([(15, 27), (12, 24), (10, 21), (9 + curl, 18)]),
            polyline([(18, 27), (18, 23), (17 - curl, 19)]),
        )
        for stalk in stalks:
            f.part(stalk, SHELLY_BODY)
        body = foot | head
        f.part(body, _shade(body, SHELLY_BODY, SHELLY_BODY_LIGHT, SHELLY_BODY_DARK, 1))
        shell = ellipse(29, 26, 11, 11)
        f.part(shell, _shade(shell, SHELLY_SHELL, SHELLY_SHELL_LIGHT, SHELLY_SHELL_DARK))
        dizzy = -i * (2.0 * math.pi / 6)
        f.paint(_spiral(29.0, 26.0, dizzy) & eroded(shell, 1), SHELLY_SPIRAL)
        f.paint(rect(17, 34, 18, 34), SHELLY_BLUSH)
        draw_mouth(f, pose.mouth, 13, 31, 3)
        _shelly_eye(f, pose.eyes, (9 + curl, 17), right=False)
        _shelly_eye(f, pose.eyes, (17 - curl, 17), right=True)


# -- Shelly's effects ---------------------------------------------------------


def _shelly_idle_fx(f: Frame, pose: Pose) -> None:
    """A slime trail behind the foot; now and then a glint slides along it."""
    trail = {(x, 45) for x in range(30, 47) if x % 4 != 1}
    f.paint(trail, SHELLY_SLIME)
    if pose.i < len(IDLE_BREATH) and pose.i in (2, 3, 4):
        x = 44 - 5 * (pose.i - 2)
        f.paint({(x, 45), (x - 1, 45)}, FX_WHITE)


#: Listening: sound arcs rolling in from the front, (x, fading) per frame;
#: they fade as they reach the face.
_SHELLY_INCOMING: tuple[tuple[tuple[int, bool], ...], ...] = (
    ((0, False),),
    ((1, False),),
    ((2, False), (-1, False)),
    ((3, True), (0, False)),
    ((1, False),),
    ((2, True),),
)


def _shelly_listening_fx(f: Frame, pose: Pose) -> None:
    """Sound arcs roll in from the front towards the face while both stalks
    lean in to catch them."""
    with f.offset(pose.dx, pose.dy):
        for x, fading in _SHELLY_INCOMING[pose.i % len(_SHELLY_INCOMING)]:
            arc = {(x + ax, 31 + ay) for ax, ay in _ARC_L}
            f.paint({p for p in arc if p[0] >= 0}, ARC_FADE if fading else ARC)


def _shelly_thinking_fx(f: Frame, pose: Pose) -> None:
    """Thought bubbles float up from the stalks into a little cloud where
    three dots fill up one by one."""
    with f.offset(pose.dx, pose.dy):
        rise = pose.i % 4
        small = (20 + rise // 2, 10 - rise // 2)
        f.part(rect(*small, small[0] + 1, small[1] + 1), SHELLY_CLOUD)
        if rise < 3:
            big = (23, 6 - rise // 2)
            f.part(rect(*big, big[0] + 2, big[1] + 2) - {big}, SHELLY_CLOUD)
        cloud = ellipse(31, 7, 4, 3.5) | ellipse(36.5, 5.5, 4.5, 4) | ellipse(41, 7.5, 3.5, 3)
        cloud |= rect(29, 8, 42, 10)
        f.part(cloud, {p: SHELLY_CLOUD_DARK if p[1] >= 10 else SHELLY_CLOUD for p in cloud})
        for k in range(min(3, pose.i % 4 + 1) if pose.i % 4 != 3 else 3):
            x = 31 + 4 * k
            f.paint(rect(x, 6, x + 1, 7), SHELLY_CLOUD_DOT)


def _shelly_talking_fx(f: Frame, pose: Pose) -> None:
    """Little voice arcs come out of the mouth, more of them the louder."""
    with f.offset(pose.dx, pose.dy):
        if pose.i >= 2:
            f.paint({(5, 33), (4, 34), (4, 35), (5, 36)}, ARC)
        if pose.i >= 3:
            f.paint({(3, 31), (2, 32), (1, 33), (1, 34), (1, 35), (1, 36), (2, 37), (3, 38)}, ARC)


def _shelly_success_fx(f: Frame, pose: Pose) -> None:
    """A sparkle orbits the whirling shell with a golden trail behind it."""
    if 1 <= pose.i <= 6:
        cx, cy = 27.5, 27.5 + pose.dy
        for k in (2, 1, 0):
            if pose.i - k < 1:
                continue
            a = -0.8 * math.pi + (pose.i - k - 1) * (0.26 * math.pi)
            x, y = _shelly_polar(cx, cy, 15.0, a)
            if k == 0:
                f.glyph(x - 1, y - 1, _SPARK_S, _SPARK_PALETTE)
            else:
                f.part(rect(x, y, x + 2 - k, y + 2 - k), SPARK if k == 1 else SPARK_CORE)
    fx_sparkles(f, pose.i, ((4, 24, 4), (43, 33, 5), (40, 8, 3)))


def _shelly_laptop_fx(f: Frame, pose: Pose) -> None:
    """A tiny laptop seen from the side, screen turned to Shelly: keys tick
    when the head nods onto them and code chips float off the screen."""
    i = pose.i
    base = rect(2, 42, 12, 43)
    f.part(base, {p: LAPTOP_BASE if p[1] == 42 else LAPTOP_BASE_DARK for p in base})
    f.paint({(x, 42) for x in range(3, 12, 2)}, LAPTOP_BASE_DARK)
    lid = rect(2, 31, 4, 41)
    colors: dict[Px, RGBA] = {}
    for x, y in lid:
        if x == 2:
            colors[(x, y)] = LAPTOP_LID
        elif x == 3:
            colors[(x, y)] = LAPTOP_LID_LIGHT if y == 36 else LAPTOP_LID
        else:
            colors[(x, y)] = CODE_CHIP if (y + i) % 3 == 0 else SCREEN_GLOW
    f.part(lid, colors)
    ticks = _KEY_TICKS[i % len(_KEY_TICKS)]
    if WORK_BOB[i % len(WORK_BOB)]:
        f.paint(rect(10, 42, 11, 42), FX_WHITE)  # the key under the chin
    for dx, _dy in ticks:
        tx = 6 + dx % 4
        f.paint({(tx, 38), (tx, 37)}, ARC)
    age = i % 4
    glyph = _CODE_GLYPHS[(i // 4) % len(_CODE_GLYPHS)]
    f.glyph(2, 26 - 2 * age, glyph, {"#": CODE_CHIP})


def _shelly_searching_fx(f: Frame, pose: Pose) -> None:
    """Dotted glances out of each scanning eye; a find sparkles up high."""
    _left, left_look, _right, right_look = _SHELLY_SCAN[pose.i % len(_SHELLY_SCAN)]
    for tip, look in ((_left, left_look), (_right, right_look)):
        ux, uy = _SHELLY_GLANCE.get(look, (0, 0))
        if (ux, uy) == (0, 0) or (left_look, right_look) == ("look_right", "look_left"):
            continue
        dots = {(tip[0] + ux * (4 + 2 * k), tip[1] + uy * (4 + 2 * k)) for k in range(3)}
        f.paint({p for p in dots if 0 <= p[0] < CELL and 0 <= p[1] < CELL}, ARC_FADE)
    fx_sparkles(f, pose.i, ((30, 5, 3),))


def _shelly_held_fx(f: Frame, pose: Pose) -> None:
    """Swing streaks on the trailing side and a nervous sweat bead flying off."""
    sway = _SHELLY_SWAY[pose.i % len(_SHELLY_SWAY)]
    with f.offset(pose.dx, pose.dy):
        if sway < 0:
            f.paint(line(43, 20, 43, 26) | line(45, 22, 45, 25), ARC_FADE)
        elif sway > 0:
            f.paint(line(9, 24, 9, 30) | line(7, 26, 7, 29), ARC_FADE)
        if pose.i % 3 != 2:
            f.paint(line(10, 15, 8, 13) | line(20, 14, 21, 12), ARC)
        k = pose.i % 3
        f.glyph(5 - k, 30 + 2 * k, (".#.", "###", "#o#", ".#."), {"#": SWEAT, "o": SWEAT_SHINE})


SHELLY = PetDesign(
    id="shelly",
    name="Shelly",
    description="A patient snail whose shell spins while it thinks and who hides inside to sleep.",
    draw=draw_shelly,
    pose=shelly_pose,
    waist=38,
    anchors=Anchors(
        arcs_left=(8, 6),
        arcs_right=(21, 7),
        dots=(27, 8),
        zzz=(35, 15),
        bang=(40, 4),
        sweat=(17, 24),
        sparkles=((3, 26, 1), (41, 10, 2), (3, 40, 3), (44, 28, 3)),
    ),
    fx={
        "idle": _shelly_idle_fx,
        "listening": _shelly_listening_fx,
        "thinking": _shelly_thinking_fx,
        "talking": _shelly_talking_fx,
        "success": _shelly_success_fx,
        "working": _shelly_laptop_fx,
        "searching": _shelly_searching_fx,
        "held": _shelly_held_fx,
    },
)


PETS: tuple[PetDesign, ...] = (GIGI, MISO, BREW, BOLT, MOCHI, SHELLY)


# ---------------------------------------------------------------------------
# Sheets, manifests and the template.
# ---------------------------------------------------------------------------


def breathe(body: Image.Image, stretch: int, waist: int) -> Image.Image:
    """Stretch (``1``) or squash (``-1``) everything above ``waist`` by a pixel.

    Stretching lifts the rows above the waist and repeats the waist row under
    them; squashing drops the waist row and lowers the rows above it. The
    feet never move, and a 1 px change reads as a breath at any scale.
    """
    if stretch == 0 or not 2 <= waist < body.height:
        return body
    out = body.copy()
    if stretch > 0:
        out.paste(body.crop((0, 1, body.width, waist)), (0, 0))
    else:
        out.paste(body.crop((0, 0, body.width, waist - 1)), (0, 1))
        out.paste((0, 0, 0, 0), (0, 0, body.width, 1))
    return out


def render_frame(pet: PetDesign, state: str, i: int) -> Image.Image:
    pose = pet.pose(state, i)
    body = Frame()
    pet.draw(body, pose)
    image = breathe(body.image(), pose.stretch, pet.waist + pose.dy)
    if pet.post is not None:
        image = pet.post(image, pose)
    fx = Frame()
    own = pet.fx.get(state)
    if own is not None:
        own(fx, pose)
    else:
        draw_effects(fx, pose, pet.anchors)
    image.alpha_composite(fx.image())
    return image


def build_sheet(pet: PetDesign) -> Image.Image:
    sheet = Image.new("RGBA", (CELL * MAX_FRAMES_PER_STATE, CELL * len(PET_STATES)), (0, 0, 0, 0))
    counts = pet.counts()
    for row, state in enumerate(PET_STATES):
        for i in range(counts[state]):
            sheet.paste(render_frame(pet, state, i), (i * CELL, row * CELL))
    return sheet


def build_manifest(pet: PetDesign) -> dict:
    counts = pet.counts()
    fps = {**FPS, **(pet.fps or {})}
    return {
        "format": PET_FORMAT,
        "id": pet.id,
        "name": pet.name,
        "description": pet.description,
        "frame_size": CELL,
        "sheet": "sheet.png",
        "animations": {
            state: _animation(state, row, counts, fps) for row, state in enumerate(PET_STATES)
        },
    }


def _animation(state: str, row: int, counts: Mapping[str, int], fps: Mapping[str, int]) -> dict:
    spec = {
        "row": row,
        "frames": counts[state],
        "fps": fps[state],
        "loop": state not in ONE_SHOT_STATES,
    }
    if state == "idle":
        spec.update(IDLE_ACCENT)
    return spec


#: Row tints of the template, one per state (alpha stays below the loader's
#: opacity threshold, so an untouched cell reads as empty).
TEMPLATE_TINTS: dict[str, tuple[int, int, int]] = {
    "idle": (148, 163, 184),
    "listening": (56, 189, 248),
    "thinking": (167, 139, 250),
    "talking": (52, 211, 153),
    "success": (250, 204, 21),
    "error": (248, 113, 113),
    "sleeping": (129, 140, 248),
    "working": (45, 212, 191),
    "searching": (251, 146, 60),
    "held": (244, 114, 182),
}
TEMPLATE_ALPHA = (40, 64)


def build_template() -> tuple[Image.Image, dict]:
    """A transparent sheet with a faint tint band per state row and alternating cells."""
    sheet = Image.new("RGBA", (CELL * MAX_FRAMES_PER_STATE, CELL * len(PET_STATES)), (0, 0, 0, 0))
    for row, state in enumerate(PET_STATES):
        for col in range(MAX_FRAMES_PER_STATE):
            alpha = TEMPLATE_ALPHA[(row + col) % 2]
            cell = Image.new("RGBA", (CELL, CELL), (*TEMPLATE_TINTS[state], alpha))
            sheet.paste(cell, (col * CELL, row * CELL))
    manifest = {
        "format": PET_FORMAT,
        "id": "template",
        "name": "My pet",
        "description": "Draw one state per row, frames from left to right.",
        "frame_size": CELL,
        "sheet": "template.png",
        "animations": {
            state: _animation(state, row, FRAME_COUNTS, FPS) for row, state in enumerate(PET_STATES)
        },
    }
    return sheet, manifest


def save_png(image: Image.Image, path: Path) -> None:
    """Fixed encoder settings and no metadata chunks: the same pixels give the same bytes."""
    image.save(path, format="PNG", optimize=False, compress_level=9)


def save_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def build_all(out_dir: Path) -> list[Path]:
    """Write ``out_dir/builtin/<id>/`` for every pet and ``out_dir/template/``."""
    written: list[Path] = []
    for pet in PETS:
        folder = out_dir / "builtin" / pet.id
        folder.mkdir(parents=True, exist_ok=True)
        save_png(build_sheet(pet), folder / "sheet.png")
        save_json(build_manifest(pet), folder / "pet.json")
        written += [folder / "sheet.png", folder / "pet.json"]
    folder = out_dir / "template"
    folder.mkdir(parents=True, exist_ok=True)
    sheet, manifest = build_template()
    save_png(sheet, folder / "template.png")
    save_json(manifest, folder / "template.json")
    written += [folder / "template.png", folder / "template.json"]
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "jarvis" / "ui" / "pets",
        help="folder that receives builtin/ and template/ (default: the package)",
    )
    args = parser.parse_args(argv)
    for path in build_all(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
