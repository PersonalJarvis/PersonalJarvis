#!/usr/bin/env python3
"""Generate the built-in desktop pets and the sprite-sheet template from pixel data.

Every pet is drawn procedurally into 48 x 48 cells: shape helpers build
masks, a shared shading pass gives every body the same light/dark rim, and a
per-state pose table moves the figure (bob, hop, shake). One row per state in
``PET_STATES`` order, one ``pet.json`` per pet (``docs/pets.md``).

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
from dataclasses import dataclass, field
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
    "listening": 4,
    "thinking": 6,
    "talking": 4,
    "success": 6,
    "error": 4,
    "sleeping": 4,
}
FPS: dict[str, int] = {
    "idle": 4,
    "listening": 8,
    "thinking": 8,
    "talking": 10,
    "success": 10,
    "error": 8,
    "sleeping": 2,
}
IDLE_BOB = (0, 0, -1, -1, 0, 0, -1, -1)
IDLE_BLINK_FRAME = 5
HOP = (0, -3, -5, -3, 0, 0)
SHAKE = (-2, 2, -2, 0)
TALK_MOUTHS = ("closed", "half", "open", "half")


@dataclass(frozen=True)
class Pose:
    state: str
    i: int
    dx: int = 0
    dy: int = 0
    eyes: str = "open"
    mouth: str = "rest"


def base_pose(state: str, i: int) -> Pose:
    if state == "idle":
        eyes = "closed" if i == IDLE_BLINK_FRAME else "open"
        return Pose(state, i, dy=IDLE_BOB[i], eyes=eyes)
    if state == "listening":
        return Pose(state, i, dy=-1, eyes="wide")
    if state == "thinking":
        return Pose(
            state, i, dy=0 if i < 3 else -1, eyes="up_left" if i < 3 else "up_right", mouth="closed"
        )
    if state == "talking":
        return Pose(state, i, dy=0 if i % 2 == 0 else -1, mouth=TALK_MOUTHS[i])
    if state == "success":
        return Pose(state, i, dy=HOP[i], eyes="happy", mouth="smile")
    if state == "error":
        return Pose(state, i, dx=SHAKE[i], eyes="x", mouth="frown")
    if state == "sleeping":
        return Pose(state, i, dy=1, eyes="sleep", mouth="closed")
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
    else:
        raise KeyError(style)


# ---------------------------------------------------------------------------
# Effects: sound arcs, thinking dots, sparkles, z marks, the red "!".
# ---------------------------------------------------------------------------

_ARC_S = ((0, -2), (1, -1), (1, 0), (0, 1))
_ARC_M = ((0, -3), (1, -2), (2, -1), (2, 0), (1, 1), (0, 2))
_ARC_L = ((0, -4), (1, -3), (2, -2), (2, -1), (2, 0), (2, 1), (1, 2), (0, 3))
_ARCS = ((0, _ARC_S), (3, _ARC_M), (6, _ARC_L))
#: Which arcs show in listening frame i: a ripple moving outward.
_ARC_PHASES = ((0,), (0, 1), (0, 1, 2), (1, 2))


def fx_arcs(f: Frame, i: int, left: Px, right: Px) -> None:
    """Sound arcs rippling out from ``left`` (leftwards) and ``right`` (rightwards)."""
    shown: Mask = set()
    for index in _ARC_PHASES[i % len(_ARC_PHASES)]:
        off, arc = _ARCS[index]
        shown |= {(right[0] + off + ax, right[1] + ay) for ax, ay in arc}
        shown |= {(left[0] - off - ax, left[1] + ay) for ax, ay in arc}
    f.paint(shown, ARC)


def fx_dots(f: Frame, i: int, x: int, y: int) -> None:
    """Three thinking dots starting at ``(x, y)``; one hops per frame."""
    active = i % 3
    dots: dict[Px, RGBA] = {}
    for k in range(3):
        lift = 1 if k == active else 0
        color = FX_WHITE if k == active else FX_DIM
        for p in rect(x + 4 * k, y - lift, x + 4 * k + 1, y - lift + 1):
            dots[p] = color
    f.part(set(dots), dots)


_SPARK_S = (".#.", "#o#", ".#.")
_SPARK_L = ("..#..", "..#..", "##o##", "..#..", "..#..")
_SPARK_PALETTE = {"#": SPARK, "o": SPARK_CORE}


def fx_sparkles(f: Frame, i: int, spots: Sequence[tuple[int, int, int]]) -> None:
    """Sparkles at ``(x, y, first_frame)`` centres: small, big, then small again."""
    for x, y, start in spots:
        age = i - start
        if age < 0:
            continue
        rows = _SPARK_L if age in (1, 2) else _SPARK_S
        half = len(rows) // 2
        f.glyph(x - half, y - half, rows, _SPARK_PALETTE)


_Z_SMALL = ("####", "..#.", ".#..", "####")
_Z_BIG = ("#####", "...#.", "..#..", ".#...", "#####")
_Z_PALETTE = {"#": Z_BLUE}
#: (dx, dy, glyph) of each z relative to the anchor, lowest first.
_ZS = ((0, 0, _Z_SMALL), (5, -6, _Z_BIG), (2, -12, _Z_SMALL))
#: Which z marks show in sleeping frame i: they rise one by one.
_Z_PHASES = ((0,), (0, 1), (0, 1, 2), (1, 2))


def fx_zzz(f: Frame, i: int, x: int, y: int) -> None:
    for index in _Z_PHASES[i % len(_Z_PHASES)]:
        dx, dy, rows = _ZS[index]
        f.glyph(x + dx, y + dy, rows, _Z_PALETTE)


_BANG = ("##", "##", "##", "##", "..", "##")


def fx_bang(f: Frame, x: int, y: int) -> None:
    f.glyph(x, y, _BANG, {"#": RED})


@dataclass(frozen=True)
class Anchors:
    """Where a pet's effects go (cell coordinates, before the pose offset)."""

    arcs_left: Px
    arcs_right: Px
    dots: Px
    zzz: Px
    bang: Px
    sparkles: tuple[tuple[int, int, int], ...]


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
    elif pose.state == "sleeping":
        fx_zzz(f, pose.i, *a.zzz)


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

    def counts(self) -> Mapping[str, int]:
        return {**FRAME_COUNTS, **(self.frame_counts or {})}


# -- Gigi: the Jarvis ghost ---------------------------------------------------

GIGI_BODY = hexc("#262a33")
GIGI_RIM = hexc("#e2ad3a")
GIGI_PLATE = hexc("#14161c")
GIGI_GLOSS = hexc("#4d5465")
GIGI_BELT = hexc("#9a7a30")
GIGI_GOLD = hexc("#ffcf45")
GIGI_EYES = EyeStyle(
    w=4, h=6, iris=GIGI_GOLD, pupil=hexc("#0b0c10"), glint=hexc("#fffbe6"), lid=GIGI_GOLD
)


def _gigi_body(phase: int) -> Mask:
    dome = {p for p in ellipse(CX, 25, 14, 14) if p[1] <= 24}
    torso = rect(10, 25, 37, 38)
    teeth: Mask = set()
    for k in range(4):
        apex = 13 + 7 * k + phase
        for r in range(4):
            for x in range(apex - (3 - r), apex + (3 - r) + 1):
                if 10 <= x <= 37:
                    teeth.add((x, 39 + r))
    return dome | torso | teeth


_GIGI_ARMS = {
    "rest": from_rows(6, 29, ("###.", "####", "####", ".###")),
    "up": from_rows(5, 19, ("##..", "###.", ".###", "..##")),
    "droop": from_rows(7, 32, ("###", "###", "###", ".##")),
}


def _gigi_arm_style(pose: Pose) -> str:
    return {"success": "up", "sleeping": "droop"}.get(pose.state, "rest")


def draw_gigi(f: Frame, pose: Pose) -> None:
    phase = (pose.i // 2) % 2 if pose.state in ("idle", "sleeping") else pose.i % 2
    with f.offset(pose.dx, pose.dy):
        arm = _GIGI_ARMS[_gigi_arm_style(pose)]
        f.part(arm, GIGI_GOLD)
        f.part(mirrored(arm), GIGI_GOLD)
        body = _gigi_body(phase)
        f.part(body, shade(body, GIGI_BODY, rim=GIGI_RIM))
        f.paint(ellipse(CX, 22, 10, 7), GIGI_PLATE)
        f.paint({(14, 16), (15, 15), (16, 14), (17, 14), (18, 13)}, GIGI_GLOSS)
        f.paint(rect(11, 31, 36, 31), GIGI_BELT)
        draw_eyes(f, pose.eyes, (17, 18), (27, 18), GIGI_EYES)
        _gigi_mouth(f, pose.mouth)


def _gigi_mouth(f: Frame, style: str) -> None:
    ring = from_rows(22, 34, (".##.", "#..#", ".##."))
    if style == "rest":
        f.paint(ring, GIGI_GOLD)
    elif style == "closed":
        f.paint(rect(22, 35, 25, 35), GIGI_GOLD)
    elif style == "half":
        f.paint(ring, GIGI_GOLD)
        f.paint(rect(23, 35, 24, 35), GIGI_PLATE)
    elif style == "open":
        big = from_rows(22, 33, (".##.", "#..#", "#..#", "#..#", ".##."))
        f.paint(rect(23, 34, 24, 36), GIGI_PLATE)
        f.paint(big, GIGI_GOLD)
    elif style == "smile":
        f.paint(from_rows(21, 34, ("#....#", ".####.")), GIGI_GOLD)
    elif style == "frown":
        f.paint(from_rows(21, 34, (".####.", "#....#")), GIGI_GOLD)
    else:
        raise KeyError(style)


GIGI = PetDesign(
    id="gigi",
    name="Gigi",
    description="The Jarvis ghost, eight bits tall, with glowing golden eyes.",
    draw=draw_gigi,
    anchors=Anchors(
        arcs_left=(10, 15),
        arcs_right=(37, 15),
        dots=(19, 5),
        zzz=(37, 14),
        bang=(40, 5),
        sparkles=((6, 12, 1), (41, 9, 2), (5, 32, 3), (42, 30, 3)),
    ),
)


def _shade(mask: Mask, base: RGBA, light: RGBA, dark: RGBA, depth: int = 2) -> dict[Px, RGBA]:
    return shade(mask, base, light=light, dark=dark, dark_depth=depth)


# -- Miso: the cat ------------------------------------------------------------

MISO_FUR = hexc("#f2a65a")
MISO_LIGHT = hexc("#ffd08f")
MISO_DARK = hexc("#c8743a")
MISO_CREAM = hexc("#fff0d9")
MISO_EAR = hexc("#ff9fb2")
MISO_STRIPE = hexc("#a95a26")
MISO_NOSE = hexc("#ff7a8a")
MISO_EYES = EyeStyle(w=3, h=4, iris=hexc("#6fcf7f"), pupil=hexc("#123524"), glint=FX_WHITE)

#: Left ear tip per look; the ear's base sits on the head outline.
_MISO_EAR_TIPS: dict[str, tuple[float, float]] = {
    "rest": (13.5, 5.5),
    "perk": (12.5, 2.5),
    "flat": (8.5, 11.5),
}


def _miso_ear(tip: tuple[float, float]) -> Mask:
    return polygon([(11.5, 18.0), tip, (21.5, 12.5)])


def _miso_tail(sway: int) -> Mask:
    return thick(polyline([(33, 41), (37, 40), (40, 37), (41 + sway, 33), (40 + sway, 29)]))


def draw_miso(f: Frame, pose: Pose) -> None:
    if pose.state == "sleeping":
        _draw_miso_curled(f, pose)
        return
    ear_look = {"listening": "perk", "error": "flat"}.get(pose.state, "rest")
    swaying = pose.state in ("idle", "listening", "talking")
    sway = (0, 1, 1, 0)[(pose.i // 2) % 4] if swaying else 0
    with f.offset(pose.dx, pose.dy):
        tail = _miso_tail(sway)
        f.part(tail, _shade(tail, MISO_FUR, MISO_LIGHT, MISO_DARK, 1))
        body = ellipse(CX, 37, 10, 7)
        f.part(body, _shade(body, MISO_FUR, MISO_LIGHT, MISO_DARK))
        f.paint(ellipse(CX, 38, 5, 4), MISO_CREAM)
        paw = ellipse(19.5, 43, 3, 2)
        f.part(paw, MISO_CREAM)
        f.part(mirrored(paw), MISO_CREAM)
        ear = _miso_ear(_MISO_EAR_TIPS[ear_look])
        for mask in (ear, mirrored(ear)):
            f.part(mask, _shade(mask, MISO_FUR, MISO_LIGHT, MISO_DARK, 1))
            f.paint(eroded(mask, 2), MISO_EAR)
        head = ellipse(CX, 22, 13, 10)
        f.part(head, _shade(head, MISO_FUR, MISO_LIGHT, MISO_DARK))
        stripes = rect(23, 13, 24, 15) | rect(19, 14, 20, 15)
        f.paint(stripes | mirrored(stripes), MISO_STRIPE)
        f.paint(ellipse(CX, 27, 5, 3), MISO_CREAM)
        whiskers = line(12, 25, 16, 26) | line(12, 28, 16, 27)
        f.paint(whiskers | mirrored(whiskers), MISO_DARK)
        draw_eyes(f, pose.eyes, (16, 19), (29, 19), MISO_EYES)
        f.paint(rect(23, 25, 24, 25), MISO_NOSE)
        draw_mouth(f, pose.mouth, 22, 26, 4)


def _draw_miso_curled(f: Frame, pose: Pose) -> None:
    """Asleep: a loaf with the head tucked on the front paws and the tail wrapped round."""
    with f.offset(pose.dx, pose.dy):
        loaf = ellipse(CX, 37, 16, 7)
        f.part(loaf, _shade(loaf, MISO_FUR, MISO_LIGHT, MISO_DARK))
        f.paint(rect(29, 32, 30, 34) | rect(34, 33, 35, 35), MISO_STRIPE)
        tail = thick(polyline([(39, 38), (36, 42), (28, 43), (19, 43)]))
        f.part(tail, _shade(tail, MISO_FUR, MISO_LIGHT, MISO_DARK, 1))
        for ear in (
            polygon([(8.5, 30.5), (9.5, 22.5), (14.5, 26.5)]),
            polygon([(19.5, 26.5), (23.5, 21.5), (25.5, 29.5)]),
        ):
            f.part(ear, _shade(ear, MISO_FUR, MISO_LIGHT, MISO_DARK, 1))
            f.paint(eroded(ear, 2), MISO_EAR)
        head = ellipse(17, 32, 9, 7)
        f.part(head, _shade(head, MISO_FUR, MISO_LIGHT, MISO_DARK))
        f.paint(ellipse(16.5, 35, 4, 2), MISO_CREAM)
        draw_eye(f, "sleep", 11, 30, MISO_EYES, right=False)
        draw_eye(f, "sleep", 19, 30, MISO_EYES, right=True)
        f.paint(rect(16, 34, 17, 34), MISO_NOSE)


MISO = PetDesign(
    id="miso",
    name="Miso",
    description="A ginger cat whose ears perk up when you talk and who naps curled in a loaf.",
    draw=draw_miso,
    anchors=Anchors(
        arcs_left=(10, 18),
        arcs_right=(37, 18),
        dots=(19, 3),
        zzz=(31, 22),
        bang=(40, 4),
        sparkles=((6, 12, 1), (41, 9, 2), (5, 32, 3), (42, 30, 3)),
    ),
)


# -- Brew: the teapot -----------------------------------------------------------

BREW_BASE = hexc("#6cc7b3")
BREW_LIGHT = hexc("#a9ecdc")
BREW_DARK = hexc("#3e8f80")
BREW_RIM = hexc("#2c6d62")
BREW_GOLD = hexc("#ffcf45")
BREW_STEAM = hexc("#e8eef7")
BREW_BLUSH = hexc("#ff8fa3")
BREW_INK = hexc("#22303a")
BREW_EYES = EyeStyle(w=3, h=4, iris=BREW_INK, glint=FX_WHITE, lid=BREW_INK)
#: Brew's face is centred on column 22 (the spout takes the right side).
BREW_AXIS = 22


def _brew_lid_lift(pose: Pose) -> int:
    if pose.state == "talking" and pose.i % 2 == 1:
        return 2
    if pose.state == "success" and pose.i in (1, 2, 3):
        return 2
    return 0


def draw_brew(f: Frame, pose: Pose) -> None:
    with f.offset(pose.dx, pose.dy):
        ring = ellipse(9, 31, 6, 6) - ellipse(9, 31, 3, 3)
        handle = {p for p in ring if p[0] <= 11}
        f.part(handle, _shade(handle, BREW_BASE, BREW_LIGHT, BREW_DARK, 1))
        spout = polygon([(32, 29), (35, 34), (44.5, 22.5), (42.5, 19.5)])
        f.part(spout, _shade(spout, BREW_BASE, BREW_LIGHT, BREW_DARK, 1))
        f.paint({(43, 20), (42, 20)}, BREW_RIM)
        foot = rect(16, 41, 28, 43)
        f.part(foot, _shade(foot, BREW_BASE, BREW_LIGHT, BREW_DARK, 1))
        body = ellipse(BREW_AXIS, 32, 13, 10)
        f.part(body, _shade(body, BREW_BASE, BREW_LIGHT, BREW_DARK, 3))
        f.paint({(14, 26), (15, 25), (16, 25)}, FX_WHITE)
        f.paint(rect(13, 22, 30, 23), BREW_RIM)
        with f.offset(0, -_brew_lid_lift(pose)):
            dome = {p for p in ellipse(BREW_AXIS, 23, 9, 5) if p[1] <= 21}
            f.part(dome, _shade(dome, BREW_BASE, BREW_LIGHT, BREW_DARK, 1))
            f.part(ellipse(BREW_AXIS, 16, 2, 2), BREW_GOLD)
        draw_eyes(f, pose.eyes, (15, 28), (26, 28), BREW_EYES)
        f.paint(rect(12, 33, 13, 33) | rect(30, 33, 31, 33), BREW_BLUSH)
        draw_mouth(f, pose.mouth, 20, 34, 4, color=BREW_INK)


def _brew_puffs(f: Frame, puffs: Iterable[tuple[int, int, bool]]) -> None:
    for x, y, big in puffs:
        f.part(ellipse(x, y, 1.6, 1.6) if big else rect(x, y, x + 1, y + 1), BREW_STEAM)


def brew_thinking(f: Frame, pose: Pose) -> None:
    """Thinking dots plus slow steam rising from the spout."""
    with f.offset(pose.dx, pose.dy):
        fx_dots(f, pose.i, 14, 7)
        puffs = []
        for k in range(3):
            phase = (pose.i + 2 * k) % 6
            puffs.append((42 + phase % 2, 16 - 2 * phase, phase >= 2))
        _brew_puffs(f, puffs)


def brew_talking(f: Frame, pose: Pose) -> None:
    """The whistle: a quick jet of steam from the spout while the lid rattles."""
    with f.offset(pose.dx, pose.dy):
        if pose.i % 2:
            _brew_puffs(f, ((43, 16, False), (44, 12, True)))
        else:
            _brew_puffs(f, ((43, 16, True), (45, 12, False)))


BREW = PetDesign(
    id="brew",
    name="Brew",
    description="A little teapot that steams while it thinks and whistles when it answers.",
    draw=draw_brew,
    anchors=Anchors(
        arcs_left=(11, 17),
        arcs_right=(32, 17),
        dots=(14, 7),
        zzz=(31, 14),
        bang=(38, 4),
        sparkles=((5, 14, 1), (40, 10, 2), (4, 40, 3), (41, 34, 3)),
    ),
    fx={"thinking": brew_thinking, "talking": brew_talking},
)


# -- Bolt: the battery ----------------------------------------------------------

BOLT_CASE = (hexc("#3fa7d6"), hexc("#86d3f2"), hexc("#2a78a3"))
#: Asleep the battery is flat: the same casing, dimmed.
BOLT_CASE_FLAT = (hexc("#2f6f90"), hexc("#4f8fae"), hexc("#23516a"))
BOLT_METAL = hexc("#cfd4de")
BOLT_METAL_DARK = hexc("#8d95a6")
BOLT_WINDOW = hexc("#1c2733")
BOLT_GREEN = hexc("#6ee07a")
BOLT_EYES = EyeStyle(w=4, h=5, iris=FX_WHITE, pupil=OUTLINE, glint=None, lid=OUTLINE)
BOLT_THINK_FRAMES = 8
#: Charge segments lit per state (0-4); thinking fills up frame by frame.
BOLT_LEVELS: dict[str, int] = {
    "idle": 3,
    "listening": 3,
    "talking": 3,
    "success": 4,
    "error": 1,
    "sleeping": 0,
}
BOLT_THINK_LEVELS = (1, 1, 2, 2, 3, 3, 4, 4)


def bolt_pose(state: str, i: int) -> Pose:
    if state == "thinking":
        early = i < BOLT_THINK_FRAMES // 2
        return Pose(
            state, i, dy=0 if early else -1, eyes="up_left" if early else "up_right", mouth="closed"
        )
    return base_pose(state, i)


def _bolt_segment(k: int) -> Mask:
    top = 38 - 4 * k
    return rect(18, top, 29, top + 2)


def draw_bolt(f: Frame, pose: Pose) -> None:
    base, light, dark = BOLT_CASE_FLAT if pose.state == "sleeping" else BOLT_CASE
    if pose.state == "thinking":
        level = BOLT_THINK_LEVELS[pose.i % len(BOLT_THINK_LEVELS)]
    else:
        level = BOLT_LEVELS[pose.state]
    with f.offset(pose.dx, pose.dy):
        if pose.state == "success":
            arm = rounded_rect(8, 17, 11, 23, 1)  # arms up
        else:
            arm = rounded_rect(9, 26, 12, 31, 1)
        f.part(arm, _shade(arm, BOLT_METAL, FX_WHITE, BOLT_METAL_DARK, 1))
        f.part(mirrored(arm), _shade(mirrored(arm), BOLT_METAL, FX_WHITE, BOLT_METAL_DARK, 1))
        feet = rect(16, 44, 20, 45)
        f.part(feet | mirrored(feet), BOLT_METAL_DARK)
        cap = rounded_rect(19, 7, 28, 10, 1)
        f.part(cap, _shade(cap, BOLT_METAL, FX_WHITE, BOLT_METAL_DARK, 1))
        case = rounded_rect(13, 11, 34, 43, 2)
        f.part(case, _shade(case, base, light, dark))
        f.paint(rounded_rect(16, 24, 31, 41, 1), BOLT_WINDOW)
        color = BOLT_GREEN if level >= 3 else SPARK if level == 2 else RED
        for k in range(level):
            f.paint(_bolt_segment(k), color)
        if pose.state == "sleeping" and pose.i % 2 == 0:
            f.paint(outer_ring(eroded(_bolt_segment(0))) & _bolt_segment(0), RED)
        draw_eyes(f, pose.eyes, (17, 14), (27, 14), BOLT_EYES)
        draw_mouth(f, pose.mouth, 22, 20, 4)


_BOLT_GLYPH = ("..##", ".##.", "####", ".##.", "##..", "#...")


def bolt_thinking(f: Frame, pose: Pose) -> None:
    """A little lightning bolt that flickers while the charge fills up."""
    with f.offset(pose.dx, pose.dy):
        tint = SPARK if pose.i % 2 == 0 else SPARK_CORE
        f.glyph(37, 3, _BOLT_GLYPH, {"#": tint})


BOLT = PetDesign(
    id="bolt",
    name="Bolt",
    description="A plucky battery that charges up while it thinks and runs flat when it sleeps.",
    draw=draw_bolt,
    anchors=Anchors(
        arcs_left=(12, 15),
        arcs_right=(35, 15),
        dots=(19, 2),
        zzz=(36, 14),
        bang=(38, 3),
        sparkles=((6, 10, 1), (41, 8, 2), (5, 36, 3), (42, 34, 3)),
    ),
    pose=bolt_pose,
    frame_counts={"thinking": BOLT_THINK_FRAMES},
    fx={"thinking": bolt_thinking},
)


# -- Mochi: the jelly blob --------------------------------------------------------

MOCHI_BASE = hexc("#ff9fcb")
MOCHI_LIGHT = hexc("#ffd3e8")
MOCHI_DARK = hexc("#e06aa2")
MOCHI_BLUSH = hexc("#ff5f9a")
MOCHI_INK = hexc("#3b1f2b")
MOCHI_EYES = EyeStyle(w=3, h=4, iris=MOCHI_INK, glint=FX_WHITE, lid=MOCHI_INK)

#: (wider, lower, sway) per listening frame: the wobble.
_MOCHI_WOBBLE = ((0, 0, 0), (1, 1, 1), (0, 0, 0), (-1, -1, -1))
#: (wider, lower) per talking frame: squish with every syllable.
_MOCHI_SQUISH = ((0, 0), (1, 1), (2, 2), (1, 1))


def mochi_pose(state: str, i: int) -> Pose:
    pose = base_pose(state, i)
    if state == "talking":
        # Mochi squishes instead of bobbing: the body gets lower, not higher.
        return Pose(state, i, mouth=pose.mouth)
    return pose


def _mochi_shape(pose: Pose) -> tuple[int, int, int]:
    """(wider, lower, sway) of the blob for this frame."""
    if pose.state == "listening":
        return _MOCHI_WOBBLE[pose.i % len(_MOCHI_WOBBLE)]
    if pose.state == "talking":
        return (*_MOCHI_SQUISH[pose.i % len(_MOCHI_SQUISH)], 0)
    if pose.state == "success":
        if pose.dy < 0:
            return (-1, -2, 0)
        return (2, 2, 0) if pose.i == 4 else (0, 0, 0)
    if pose.state == "sleeping":
        return (2, 3, 0)
    if pose.state == "idle" and pose.dy < 0:
        return (-1, -1, 0)
    return (0, 0, 0)


def _mochi_body(wider: int, lower: int, sway: int) -> Mask:
    """A daifuku-round blob; ``sway`` leans its soft dome sideways."""
    blob = {p for p in ellipse(CX, 33, 15 + wider, 11 - lower) if p[1] <= 43}
    dome = ellipse(CX + sway, 28 + lower, 11 + max(0, wider), 7)
    return blob | dome


def draw_mochi(f: Frame, pose: Pose) -> None:
    wider, lower, sway = _mochi_shape(pose)
    with f.offset(pose.dx, pose.dy):
        body = _mochi_body(wider, lower, sway)
        f.part(body, _shade(body, MOCHI_BASE, MOCHI_LIGHT, MOCHI_DARK, 3))
        glint = {(15, 25 + lower), (16, 24 + lower), (17, 24 + lower), (14, 26 + lower)}
        f.paint(glint & eroded(body, 1), FX_WHITE)
        eye_y = 30 + max(0, lower) // 2
        draw_eyes(f, pose.eyes, (17, eye_y), (28, eye_y), MOCHI_EYES)
        blush = rect(13, eye_y + 4, 15, eye_y + 4)
        f.paint(blush | mirrored(blush), MOCHI_BLUSH)
        draw_mouth(f, pose.mouth, 22, eye_y + 5, 4, color=MOCHI_INK)


MOCHI = PetDesign(
    id="mochi",
    name="Mochi",
    description="A soft jelly blob that wobbles when it listens and squishes when it talks.",
    draw=draw_mochi,
    anchors=Anchors(
        arcs_left=(8, 24),
        arcs_right=(39, 24),
        dots=(19, 15),
        zzz=(34, 17),
        bang=(39, 7),
        sparkles=((6, 14, 1), (41, 12, 2), (4, 38, 3), (43, 36, 3)),
    ),
    pose=mochi_pose,
)


# -- Shelly: the snail ------------------------------------------------------------

SHELLY_BODY = hexc("#c3de7a")
SHELLY_BODY_LIGHT = hexc("#e3f4a9")
SHELLY_BODY_DARK = hexc("#8eae4c")
SHELLY_SHELL = hexc("#e08a4f")
SHELLY_SHELL_LIGHT = hexc("#f5b884")
SHELLY_SHELL_DARK = hexc("#a95a2c")
SHELLY_SPIRAL = hexc("#6b3417")
SHELLY_BLUSH = hexc("#ff8fa3")
SHELLY_EYES = EyeStyle(w=5, h=5, iris=FX_WHITE, pupil=OUTLINE, glint=None, lid=OUTLINE)

#: Eye-stalk tips (left, right) per look; the stalks grow from the head top.
_SHELLY_TIPS: dict[str, tuple[Px, Px]] = {
    "rest": ((8, 15), (17, 14)),
    "perk": ((7, 12), (18, 11)),
    "droop": ((5, 20), (19, 19)),
}
_SHELLY_STALK_BASES: tuple[Px, Px] = ((10, 26), (15, 26))
_SHELLY_OPEN_EYES = ("open", "wide", "up_left", "up_right")


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


#: Pupil offset inside the 5 x 5 eyeball per look; Shelly faces left.
_SHELLY_PUPILS: dict[str, Px] = {
    "open": (1, 2),
    "wide": (1, 2),
    "up_left": (0, 0),
    "up_right": (3, 0),
}


def _shelly_eye(f: Frame, style: str, tip: Px, *, right: bool) -> None:
    x, y = tip[0] - 2, tip[1] - 2
    ball = _oval(x, y, 5, 5)
    if style in _SHELLY_OPEN_EYES:
        f.part(ball, FX_WHITE)
        dx, dy = _SHELLY_PUPILS[style]
        f.paint(rect(x + dx, y + dy, x + dx + 1, y + dy + 1), OUTLINE)
        return
    f.part(ball, SHELLY_BODY)
    draw_eye(f, style, x, y, SHELLY_EYES, right=right)


def draw_shelly(f: Frame, pose: Pose) -> None:
    if pose.state == "sleeping":
        _draw_shelly_withdrawn(f, pose)
        return
    look = {"listening": "perk", "success": "perk", "error": "droop"}.get(pose.state, "rest")
    sway = (0, 1, 1, 0)[(pose.i // 2) % 4] if pose.state == "idle" else 0
    tips = tuple((x + sway, y) for x, y in _SHELLY_TIPS[look])
    phase = -pose.i * (2.0 * math.pi / 6.0) if pose.state == "thinking" else 0.0
    with f.offset(pose.dx, pose.dy):
        for base, tip in zip(_SHELLY_STALK_BASES, tips, strict=True):
            f.part(line(*base, *tip), SHELLY_BODY)
        body = ellipse(26, 41, 17, 3.2) | ellipse(13, 33, 6, 8)
        f.part(body, _shade(body, SHELLY_BODY, SHELLY_BODY_LIGHT, SHELLY_BODY_DARK, 1))
        shell = ellipse(28, 28, 11, 11)
        f.part(shell, _shade(shell, SHELLY_SHELL, SHELLY_SHELL_LIGHT, SHELLY_SHELL_DARK))
        f.paint(_spiral(28.0, 28.0, phase) & eroded(shell, 1), SHELLY_SPIRAL)
        f.paint(rect(12, 37, 13, 37), SHELLY_BLUSH)
        draw_mouth(f, pose.mouth, 8, 34, 3)
        _shelly_eye(f, pose.eyes, tips[0], right=False)
        _shelly_eye(f, pose.eyes, tips[1], right=True)


def _draw_shelly_withdrawn(f: Frame, pose: Pose) -> None:
    """Asleep: tucked into the shell, only a sliver of foot peeking out."""
    with f.offset(pose.dx, pose.dy):
        foot = ellipse(26, 43, 13, 1.6)
        f.part(foot, SHELLY_BODY_DARK)
        shell = ellipse(26, 32, 11, 10.5)
        f.part(shell, _shade(shell, SHELLY_SHELL, SHELLY_SHELL_LIGHT, SHELLY_SHELL_DARK))
        f.paint(_spiral(26.0, 32.0, 0.0, r_max=8.0) & eroded(shell, 1), SHELLY_SPIRAL)
        f.paint(ellipse(17.5, 38.5, 2, 2) & shell, SHELLY_SPIRAL)


SHELLY = PetDesign(
    id="shelly",
    name="Shelly",
    description="A patient snail whose shell spins while it thinks and who hides inside to sleep.",
    draw=draw_shelly,
    anchors=Anchors(
        arcs_left=(8, 6),
        arcs_right=(21, 7),
        dots=(27, 8),
        zzz=(35, 15),
        bang=(40, 4),
        sparkles=((3, 26, 1), (41, 10, 2), (3, 40, 3), (44, 28, 3)),
    ),
)

PETS: tuple[PetDesign, ...] = (GIGI, MISO, BREW, BOLT, MOCHI, SHELLY)


# ---------------------------------------------------------------------------
# Sheets, manifests and the template.
# ---------------------------------------------------------------------------


def render_frame(pet: PetDesign, state: str, i: int) -> Image.Image:
    pose = pet.pose(state, i)
    f = Frame()
    pet.draw(f, pose)
    own = pet.fx.get(state)
    if own is not None:
        own(f, pose)
    else:
        draw_effects(f, pose, pet.anchors)
    return f.image()


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
            state: {
                "row": row,
                "frames": counts[state],
                "fps": fps[state],
                "loop": state not in ONE_SHOT_STATES,
            }
            for row, state in enumerate(PET_STATES)
        },
    }


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
            state: {
                "row": row,
                "frames": FRAME_COUNTS[state],
                "fps": FPS[state],
                "loop": state not in ONE_SHOT_STATES,
            }
            for row, state in enumerate(PET_STATES)
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
