"""The picker's markings as plain data — no Qt, so the rules are testable anywhere.

After an area is chosen the picker stays open and the user
marks it up in place: boxes, circles, arrows, lines, pen and highlighter
strokes, text, numbered steps, and blur or pixelate patches. Every marking is
a :class:`Shape` in the overlay window's logical pixels; the Qt side
(:mod:`jarvis.appshot.picker.annotate`) only paints them and turns mouse and
keys into calls here.

Geometry is floats and tuples, never Qt types, so the undo history can copy
the whole list cheaply and these rules run on a headless CI box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

Point = tuple[float, float]
#: ``(left, top, width, height)``.
Box = tuple[float, float, float, float]

RECT = "rect"
ELLIPSE = "ellipse"
ARROW = "arrow"
LINE = "line"
PEN = "pen"
HIGHLIGHT = "highlight"
TEXT = "text"
COUNTER = "counter"
BLUR = "blur"
PIXELATE = "pixelate"
#: Not a marking: picks, moves and deletes existing ones.
MOVE = "move"

#: Drawn by dragging a box.
BOX_KINDS = frozenset({RECT, ELLIPSE, BLUR, PIXELATE})
#: Drawn by dragging from a start to an end point.
SEGMENT_KINDS = frozenset({ARROW, LINE})
#: Drawn by dragging a free path.
STROKE_KINDS = frozenset({PEN, HIGHLIGHT})
#: Hide what is under them. They are applied to the real capture first, under
#: every other marking, so the picker paints them first as well.
HIDE_KINDS = frozenset({BLUR, PIXELATE})

#: Tool order in the toolbar, with the key that selects each one.
TOOL_KEYS: dict[str, str] = {
    MOVE: "V",
    RECT: "R",
    ELLIPSE: "E",
    ARROW: "A",
    LINE: "L",
    PEN: "P",
    HIGHLIGHT: "H",
    TEXT: "T",
    COUNTER: "N",
    BLUR: "B",
    PIXELATE: "X",
}

#: Marking colours; red is the default.
PALETTE: tuple[str, ...] = (
    "#FF3B30",
    "#FF9500",
    "#FFCC00",
    "#34C759",
    "#0A84FF",
    "#AF52DE",
    "#FFFFFF",
    "#111111",
)
#: Stroke widths the mouse wheel and the width button step through (logical px).
WIDTHS: tuple[float, ...] = (2.0, 3.0, 4.0, 6.0, 9.0)
DEFAULT_WIDTH_INDEX = 1

#: A drag shorter than this (logical px) draws nothing, except where a click
#: is the whole gesture (text, counter).
MIN_DRAG_PX = 3.0
#: Grab distance for the selection's resize handles.
HANDLE_GRAB_PX = 8.0
#: The selection never shrinks below this while it is resized.
MIN_SELECTION_PX = 8.0


@dataclass(slots=True)
class Shape:
    """One marking. What ``points`` holds depends on ``kind``:

    box kinds and segments: ``[start, end]``; strokes: the whole path; text:
    ``[anchor]`` (top-left of the first line); counter: ``[centre]``.
    """

    kind: str
    color: str = PALETTE[0]
    width: float = WIDTHS[DEFAULT_WIDTH_INDEX]
    points: list[Point] = field(default_factory=list)
    text: str = ""
    number: int = 0

    def copy(self) -> Shape:
        return replace(self, points=list(self.points))


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def normalized(a: Point, b: Point) -> Box:
    left, top = min(a[0], b[0]), min(a[1], b[1])
    return (left, top, abs(b[0] - a[0]), abs(b[1] - a[1]))


def constrain(kind: str, start: Point, end: Point) -> Point:
    """Shift held: boxes become squares/circles, lines snap to 45 degrees."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    if kind in BOX_KINDS:
        side = max(abs(dx), abs(dy))
        return (
            start[0] + math.copysign(side, dx or 1.0),
            start[1] + math.copysign(side, dy or 1.0),
        )
    if kind in SEGMENT_KINDS:
        length = math.hypot(dx, dy)
        if length == 0:
            return end
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        return (start[0] + length * math.cos(angle), start[1] + length * math.sin(angle))
    return end


def counter_radius(width: float) -> float:
    return 9.0 + width * 1.6


def text_size(width: float) -> float:
    """Text height in logical px for a stroke width — the width step scales text too."""
    return 12.0 + width * 2.5


def text_box(shape: Shape, char_w: float = 0.6) -> Box:
    """A text marking's rough bounds (the Qt side measures exactly when painting)."""
    size = text_size(shape.width)
    lines = shape.text.split("\n") or [""]
    width = max(len(line) for line in lines) * size * char_w
    height = len(lines) * size * 1.25
    x, y = shape.points[0]
    return (x, y, max(width, size * 0.5), height)


def bounds(shape: Shape) -> Box:
    """The area a marking covers, before its stroke width."""
    if not shape.points:
        return (0.0, 0.0, 0.0, 0.0)
    if shape.kind == TEXT:
        return text_box(shape)
    if shape.kind == COUNTER:
        r = counter_radius(shape.width)
        x, y = shape.points[0]
        return (x - r, y - r, 2 * r, 2 * r)
    xs = [p[0] for p in shape.points]
    ys = [p[1] for p in shape.points]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _distance_to_segment(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / span))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def hit(shape: Shape, p: Point, tolerance: float = 6.0) -> bool:
    """Whether a click at ``p`` lands on ``shape`` (outlines, not empty insides)."""
    tol = tolerance + shape.width / 2.0
    if shape.kind in (TEXT, COUNTER, BLUR, PIXELATE):
        x, y, w, h = bounds(shape)
        return x - tol <= p[0] <= x + w + tol and y - tol <= p[1] <= y + h + tol
    if shape.kind in SEGMENT_KINDS or shape.kind in STROKE_KINDS:
        pts = shape.points
        if len(pts) == 1:
            return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= tol
        return any(_distance_to_segment(p, a, b) <= tol for a, b in zip(pts, pts[1:], strict=False))
    x, y, w, h = normalized(shape.points[0], shape.points[-1])
    if shape.kind == RECT:
        inside_outer = x - tol <= p[0] <= x + w + tol and y - tol <= p[1] <= y + h + tol
        inside_inner = x + tol < p[0] < x + w - tol and y + tol < p[1] < y + h - tol
        return inside_outer and not inside_inner
    if shape.kind == ELLIPSE:
        rx, ry = w / 2.0, h / 2.0
        if rx <= 0 or ry <= 0:
            return False
        cx, cy = x + rx, y + ry
        # Normalised radial distance; 1.0 is the outline.
        d = math.hypot((p[0] - cx) / rx, (p[1] - cy) / ry)
        return abs(d - 1.0) * min(rx, ry) <= tol
    return False


def shape_at(shapes: list[Shape], p: Point) -> int | None:
    """Index of the top-most marking under ``p``."""
    for index in range(len(shapes) - 1, -1, -1):
        if hit(shapes[index], p):
            return index
    return None


def translated(shape: Shape, dx: float, dy: float) -> Shape:
    moved = shape.copy()
    moved.points = [(x + dx, y + dy) for x, y in shape.points]
    return moved


def is_meaningful(shape: Shape) -> bool:
    """A finished drag that actually draws something."""
    if shape.kind == TEXT:
        return bool(shape.text.strip())
    if shape.kind == COUNTER:
        return bool(shape.points)
    if len(shape.points) < 2:
        return False
    if shape.kind in STROKE_KINDS:
        return True
    x, y, w, h = normalized(shape.points[0], shape.points[-1])
    if shape.kind in SEGMENT_KINDS:
        return math.hypot(w, h) >= MIN_DRAG_PX
    return w >= MIN_DRAG_PX and h >= MIN_DRAG_PX


def next_counter(shapes: list[Shape]) -> int:
    """The number the next step marker shows: one past the highest so far."""
    return max((s.number for s in shapes if s.kind == COUNTER), default=0) + 1


# --------------------------------------------------------------------------
# The selection frame
# --------------------------------------------------------------------------

#: Handle names: corners and edge midpoints.
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


def handle_points(sel: Box) -> dict[str, Point]:
    x, y, w, h = sel
    return {
        "nw": (x, y),
        "n": (x + w / 2, y),
        "ne": (x + w, y),
        "e": (x + w, y + h / 2),
        "se": (x + w, y + h),
        "s": (x + w / 2, y + h),
        "sw": (x, y + h),
        "w": (x, y + h / 2),
    }


def handle_at(sel: Box, p: Point, grab: float = HANDLE_GRAB_PX) -> str | None:
    for name, (hx, hy) in handle_points(sel).items():
        if abs(p[0] - hx) <= grab and abs(p[1] - hy) <= grab:
            return name
    return None


def resize(sel: Box, handle: str, p: Point, limit: tuple[float, float]) -> Box:
    """Drag ``handle`` of ``sel`` to ``p``; clamped to the screen ``limit``."""
    x0, y0, w, h = sel
    x1, y1 = x0 + w, y0 + h
    px = max(0.0, min(limit[0], p[0]))
    py = max(0.0, min(limit[1], p[1]))
    if "w" in handle:
        x0 = min(px, x1 - MIN_SELECTION_PX)
    if "e" in handle:
        x1 = max(px, x0 + MIN_SELECTION_PX)
    if "n" in handle:
        y0 = min(py, y1 - MIN_SELECTION_PX)
    if "s" in handle:
        y1 = max(py, y0 + MIN_SELECTION_PX)
    return (x0, y0, x1 - x0, y1 - y0)


def toolbar_origin(
    sel: Box, bar: tuple[float, float], screen: tuple[float, float], gap: float = 10.0
) -> Point:
    """Where the toolbar goes: centred under the selection, else above, else inside.

    Always kept fully on the screen.
    """
    x, y, w, h = sel
    bw, bh = bar
    sw, sh = screen
    left = x + (w - bw) / 2.0
    if y + h + gap + bh <= sh:
        top = y + h + gap
    elif y - gap - bh >= 0:
        top = y - gap - bh
    else:
        top = y + h - gap - bh
    left = max(gap, min(sw - bw - gap, left))
    top = max(gap, min(sh - bh - gap, top))
    return (left, top)


def clip_to(box: Box, sel: Box) -> Box | None:
    """``box`` cut to the selection; ``None`` when nothing of it is inside."""
    x0 = max(box[0], sel[0])
    y0 = max(box[1], sel[1])
    x1 = min(box[0] + box[2], sel[0] + sel[2])
    y1 = min(box[1] + box[3], sel[1] + sel[3])
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def hide_fractions(shapes: list[Shape], sel: Box) -> list[dict[str, object]]:
    """Blur/pixelate patches as fractions of the selection, for the main process."""
    out: list[dict[str, object]] = []
    sx, sy, sw, sh = sel
    if sw <= 0 or sh <= 0:
        return out
    for shape in shapes:
        if shape.kind not in HIDE_KINDS or len(shape.points) < 2:
            continue
        clipped = clip_to(normalized(shape.points[0], shape.points[-1]), sel)
        if clipped is None:
            continue
        x, y, w, h = clipped
        out.append({"kind": shape.kind, "rect": [(x - sx) / sw, (y - sy) / sh, w / sw, h / sh]})
    return out


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------


class History:
    """Snapshots of the marking list; every finished action pushes one."""

    def __init__(self) -> None:
        self._undo: list[list[Shape]] = [[]]
        self._redo: list[list[Shape]] = []

    @staticmethod
    def _copy(shapes: list[Shape]) -> list[Shape]:
        return [s.copy() for s in shapes]

    def push(self, shapes: list[Shape]) -> None:
        self._undo.append(self._copy(shapes))
        self._redo.clear()

    def undo(self) -> list[Shape] | None:
        if len(self._undo) <= 1:
            return None
        self._redo.append(self._undo.pop())
        return self._copy(self._undo[-1])

    def redo(self) -> list[Shape] | None:
        if not self._redo:
            return None
        state = self._redo.pop()
        self._undo.append(state)
        return self._copy(state)

    @property
    def can_undo(self) -> bool:
        return len(self._undo) > 1

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)


__all__ = [
    "ARROW",
    "BLUR",
    "BOX_KINDS",
    "COUNTER",
    "ELLIPSE",
    "HANDLES",
    "HIDE_KINDS",
    "HIGHLIGHT",
    "LINE",
    "MOVE",
    "PALETTE",
    "PEN",
    "PIXELATE",
    "RECT",
    "SEGMENT_KINDS",
    "STROKE_KINDS",
    "TEXT",
    "TOOL_KEYS",
    "WIDTHS",
    "History",
    "Shape",
    "bounds",
    "clip_to",
    "constrain",
    "counter_radius",
    "handle_at",
    "handle_points",
    "hide_fractions",
    "hit",
    "is_meaningful",
    "next_counter",
    "normalized",
    "resize",
    "shape_at",
    "text_size",
    "toolbar_origin",
    "translated",
]
