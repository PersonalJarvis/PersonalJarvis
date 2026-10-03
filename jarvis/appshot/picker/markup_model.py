"""The picker's markings as plain data — no Qt, so the rules are testable anywhere.

After an area is chosen the picker stays open and the user marks it up in
place with the same tools as the full appshot editor
(``frontend/src/lib/appshotEditorModel.ts``): select and move, rectangle,
filled rectangle, ellipse, line, arrow (three looks), text (three looks),
pixelate/blur, spotlight, numbered steps, pen, highlighter and a background
frame — with the same one-letter keys. Every marking is a :class:`Shape` in
the overlay window's logical pixels; the Qt side
(:mod:`jarvis.appshot.picker.annotate`) only paints them and turns mouse and
keys into calls here.

Geometry is floats and tuples, never Qt types, so the undo history can copy
the whole list cheaply and these rules run on a headless CI box.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

Point = tuple[float, float]
#: ``(left, top, width, height)``.
Box = tuple[float, float, float, float]
#: Width of ``text`` drawn at ``size`` px — the Qt side passes a real one.
Measure = Callable[[str, float], float]

RECT = "rect"
FILLED = "filled"
ELLIPSE = "ellipse"
ARROW = "arrow"
LINE = "line"
PEN = "pen"
HIGHLIGHT = "highlight"
TEXT = "text"
COUNTER = "counter"
SPOTLIGHT = "spotlight"
REDACT = "redact"
#: Not markings: the select tool, and the background frame switch.
MOVE = "move"
BACKGROUND = "background"

#: Drawn by dragging a box.
BOX_KINDS = frozenset({RECT, FILLED, ELLIPSE, SPOTLIGHT, REDACT})
#: Drawn by dragging from a start to an end point.
SEGMENT_KINDS = frozenset({ARROW, LINE})
#: Drawn by dragging a free path.
STROKE_KINDS = frozenset({PEN, HIGHLIGHT})
#: Cover what lies in them: picked by a click inside only when nothing drawn
#: on top is hit, and only by the select tool.
AREA_KINDS = frozenset({SPOTLIGHT, REDACT})

#: Tool → key, in the full editor's toolbar order; the same keys as there.
TOOL_KEYS: dict[str, str] = {
    MOVE: "V",
    RECT: "R",
    FILLED: "F",
    ELLIPSE: "E",
    LINE: "L",
    ARROW: "A",
    TEXT: "T",
    REDACT: "P",
    SPOTLIGHT: "H",
    COUNTER: "C",
    PEN: "D",
    HIGHLIGHT: "M",
    BACKGROUND: "B",
}

ARROW_STYLES = ("tapered", "classic", "double")
TEXT_STYLES = ("plain", "label", "outline")
REDACT_MODES = ("pixelate", "blur")

#: Marking colours — the full editor's presets.
PALETTE: tuple[str, ...] = (
    "#FF3B30",
    "#FF9500",
    "#FFCC00",
    "#34C759",
    "#0A84FF",
    "#AF52DE",
    "#FFFFFF",
    "#1C1C1E",
)
#: Stroke widths (logical px): keys 1-5, the mouse wheel, the width button.
WIDTHS: tuple[float, ...] = (2.0, 3.0, 4.0, 6.0, 9.0)
DEFAULT_WIDTH_INDEX = 1

#: Background frames: the full editor's presets (id, colour stops), kept in
#: step by a parity test.
BACKGROUND_PRESETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dawn", ("#ff9a8b", "#ff6a88", "#ff99ac")),
    ("lagoon", ("#43cea2", "#185a9d")),
    ("dusk", ("#7f7fd5", "#86a8e7", "#91eae4")),
    ("ember", ("#f7971e", "#ffd200")),
    ("orchid", ("#c471f5", "#fa71cd")),
    ("slate", ("#434343", "#1c1c1e")),
    ("paper", ("#f5f5f7",)),
    ("ink", ("#111113",)),
)
DEFAULT_BACKGROUND = {"preset": "dusk", "padding": 0.08, "radius": 12, "shadow": True}

#: A drag shorter than this (logical px) draws nothing, except where a click
#: is the whole gesture (text, counter).
MIN_DRAG_PX = 4.0
#: Grab distance for the selection's resize handles and a marking's grips.
HANDLE_GRAB_PX = 8.0
#: The selection never shrinks below this while it is resized.
MIN_SELECTION_PX = 8.0
#: The smallest text and counter a grip can shrink them to (px).
MIN_TEXT_PX = 8.0


@dataclass(slots=True)
class Shape:
    """One marking. What ``points`` holds depends on ``kind``:

    box kinds and segments: ``[start, end]``; strokes: the whole path; text:
    ``[top-left]``; counter: ``[centre]``. ``size`` is the text height or the
    counter's radius; ``style`` the arrow or text look; ``mode`` pixelate or
    blur for a redaction.
    """

    kind: str
    color: str = PALETTE[0]
    width: float = WIDTHS[DEFAULT_WIDTH_INDEX]
    points: list[Point] = field(default_factory=list)
    text: str = ""
    number: int = 0
    size: float = 0.0
    style: str = ""
    mode: str = ""

    def copy(self) -> Shape:
        return replace(self, points=list(self.points))


def rough_measure(text: str, size: float) -> float:
    return len(text) * size * 0.55


# --------------------------------------------------------------------------
# Sizes
# --------------------------------------------------------------------------


def text_size(width: float) -> float:
    """Text height for a stroke width (the width step scales new text too)."""
    return max(14.0, round(width * 5.0))


def counter_size(width: float) -> float:
    """Counter badge radius for a stroke width."""
    return max(12.0, round(width * 3.0))


def grab_scope(tool: str) -> str:
    """What a press with ``tool`` may take hold of instead of drawing.

    ``any``: the select tool picks up every marking; ``selected``: a shape
    tool reshapes or moves only the marking it has selected (the one it just
    drew); ``none``: pen, highlighter and counter always draw.
    """
    if tool == MOVE:
        return "any"
    if tool in (PEN, HIGHLIGHT, COUNTER, BACKGROUND):
        return "none"
    return "selected"


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


def label_pad(shape: Shape) -> float:
    return shape.size * 0.4 if shape.kind == TEXT and shape.style == "label" else 0.0


def text_lines(shape: Shape) -> list[str]:
    return shape.text.split("\n") or [""]


def bounds(shape: Shape, measure: Measure = rough_measure) -> Box:
    """The area a marking covers, before its stroke width."""
    if not shape.points:
        return (0.0, 0.0, 0.0, 0.0)
    if shape.kind == TEXT:
        lines = text_lines(shape)
        width = max(measure(line, shape.size) for line in lines)
        width = max(width, shape.size * 0.5)
        pad = label_pad(shape)
        x, y = shape.points[0]
        return (x - pad, y - pad, width + pad * 2, len(lines) * shape.size * 1.25 + pad * 2)
    if shape.kind == COUNTER:
        r = shape.size
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


def _inside(p: Point, box: Box, slop: float) -> bool:
    x, y, w, h = box
    return x - slop <= p[0] <= x + w + slop and y - slop <= p[1] <= y + h + slop


def hit(shape: Shape, p: Point, slop: float = 6.0, measure: Measure = rough_measure) -> bool:
    """Whether a click at ``p`` lands on ``shape``.

    Lines and strokes count near their path, outlines near their edge, filled
    boxes, text, counters and areas anywhere on them.
    """
    if not shape.points:
        return False
    if shape.kind in SEGMENT_KINDS or shape.kind in STROKE_KINDS:
        reach = slop + (shape.width * 2.0 if shape.kind == HIGHLIGHT else shape.width)
        pts = shape.points
        if len(pts) == 1:
            return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= reach
        pairs = zip(pts, pts[1:], strict=False)
        return any(_distance_to_segment(p, a, b) <= reach for a, b in pairs)
    if shape.kind == RECT:
        reach = slop + shape.width
        x, y, w, h = normalized(shape.points[0], shape.points[-1])
        inner = (x + reach, y + reach, w - reach * 2, h - reach * 2)
        return _inside(p, (x, y, w, h), reach) and not (
            inner[2] > 0 and inner[3] > 0 and _inside(p, inner, 0)
        )
    if shape.kind == ELLIPSE:
        x, y, w, h = normalized(shape.points[0], shape.points[-1])
        rx, ry = max(1.0, w / 2.0), max(1.0, h / 2.0)
        d = math.hypot((p[0] - (x + rx)) / rx, (p[1] - (y + ry)) / ry)
        return abs(d - 1.0) * min(rx, ry) <= slop + shape.width
    return _inside(p, bounds(shape, measure), slop)


def shape_at(
    shapes: list[Shape],
    p: Point,
    measure: Measure = rough_measure,
    *,
    areas: bool = True,
    slop: float = 6.0,
) -> int | None:
    """Index of the top-most marking under ``p``; areas only when nothing else hits."""
    passes = (False, True) if areas else (False,)
    for want_area in passes:
        for index in range(len(shapes) - 1, -1, -1):
            shape = shapes[index]
            if (shape.kind in AREA_KINDS) != want_area:
                continue
            if hit(shape, p, slop, measure):
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
    if shape.kind in STROKE_KINDS:
        return bool(shape.points)
    if len(shape.points) < 2:
        return False
    x, y, w, h = normalized(shape.points[0], shape.points[-1])
    if shape.kind in SEGMENT_KINDS:
        return math.hypot(w, h) >= MIN_DRAG_PX
    return w >= MIN_DRAG_PX and h >= MIN_DRAG_PX


def next_counter(shapes: list[Shape]) -> int:
    """The number the next step marker shows: one past the highest so far."""
    return max((s.number for s in shapes if s.kind == COUNTER), default=0) + 1


# --------------------------------------------------------------------------
# Grips: reshaping a marking in place (the full editor's rules)
# --------------------------------------------------------------------------

_CORNERS = ("nw", "ne", "sw", "se")
_OPPOSITE = {"nw": "se", "ne": "sw", "sw": "ne", "se": "nw"}


def _corner(box: Box, name: str) -> Point:
    x, y, w, h = box
    return (x if name in ("nw", "sw") else x + w, y if name in ("nw", "ne") else y + h)


def grips(shape: Shape, measure: Measure = rough_measure) -> list[tuple[str, Point]]:
    """The grips a selected marking shows.

    Lines and arrows one at each end; boxes, strokes and text one at each
    corner (text scales with them); a counter one on its rim.
    """
    if not shape.points:
        return []
    if shape.kind in SEGMENT_KINDS:
        return [("from", shape.points[0]), ("to", shape.points[-1])]
    if shape.kind == COUNTER:
        x, y = shape.points[0]
        return [("size", (x + shape.size, y))]
    box = bounds(shape, measure)
    return [(name, _corner(box, name)) for name in _CORNERS]


def grip_at(
    shape: Shape, p: Point, radius: float = HANDLE_GRAB_PX, measure: Measure = rough_measure
) -> str | None:
    """The grip under ``p`` (within ``radius``), nearest first."""
    best, best_distance = None, radius
    for name, (gx, gy) in grips(shape, measure):
        distance = math.hypot(gx - p[0], gy - p[1])
        if distance <= best_distance:
            best, best_distance = name, distance
    return best


def reshape(original: Shape, grip: str, p: Point, measure: Measure = rough_measure) -> Shape:
    """``original`` with ``grip`` dragged to ``p``.

    Always computed from the marking as it was when the drag began, so a long
    drag never drifts. An arrow's end follows the pointer; a box keeps the
    opposite corner fixed; a stroke scales from it; text grows or shrinks
    from the opposite corner, font size and all; a counter grows with its rim.
    """
    shape = original.copy()
    if shape.kind in SEGMENT_KINDS:
        if grip == "from":
            shape.points[0] = p
        elif grip == "to":
            shape.points[-1] = p
        return shape
    if shape.kind == COUNTER:
        x, y = shape.points[0]
        shape.size = max(MIN_TEXT_PX, math.hypot(p[0] - x, p[1] - y))
        return shape
    if grip not in _OPPOSITE:
        return shape
    box = bounds(original, measure)
    anchor = _corner(box, _OPPOSITE[grip])
    if shape.kind == TEXT:
        factor = max(0.1, abs(p[1] - anchor[1]) / max(1.0, box[3]))
        shape.size = max(MIN_TEXT_PX, round(original.size * factor))
        grown = bounds(replace(shape, points=[(0.0, 0.0)]), measure)
        pad = label_pad(shape)
        # Keep the opposite corner where it was.
        left = anchor[0] if grip in ("ne", "se") else anchor[0] - grown[2]
        top = anchor[1] if grip in ("sw", "se") else anchor[1] - grown[3]
        shape.points = [(left + pad, top + pad)]
        return shape
    if shape.kind in STROKE_KINDS:
        corner = _corner(box, grip)
        span_x, span_y = corner[0] - anchor[0], corner[1] - anchor[1]
        sx = 1.0 if abs(span_x) < 1e-6 else (p[0] - anchor[0]) / span_x
        sy = 1.0 if abs(span_y) < 1e-6 else (p[1] - anchor[1]) / span_y
        shape.points = [
            (anchor[0] + (x - anchor[0]) * sx, anchor[1] + (y - anchor[1]) * sy)
            for x, y in original.points
        ]
        return shape
    shape.points = [anchor, p]
    return shape


def grip_cursor(grip: str) -> str:
    """``fdiag`` / ``bdiag`` / ``hor`` / ``grab`` — mapped to Qt cursors by the caller."""
    if grip in ("nw", "se"):
        return "fdiag"
    if grip in ("ne", "sw"):
        return "bdiag"
    if grip == "size":
        return "hor"
    return "grab"


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
    """Pixelate/blur patches as fractions of the selection, for the main process."""
    out: list[dict[str, object]] = []
    sx, sy, sw, sh = sel
    if sw <= 0 or sh <= 0:
        return out
    for shape in shapes:
        if shape.kind != REDACT or len(shape.points) < 2:
            continue
        clipped = clip_to(normalized(shape.points[0], shape.points[-1]), sel)
        if clipped is None:
            continue
        x, y, w, h = clipped
        kind = shape.mode if shape.mode in REDACT_MODES else "pixelate"
        out.append({"kind": kind, "rect": [(x - sx) / sw, (y - sy) / sh, w / sw, h / sh]})
    return out


def frame_padding(sel: Box, padding: float) -> float:
    """The background frame's margin around an area (the full editor's rule)."""
    return round(max(sel[2], sel[3]) * max(0.0, min(0.25, padding)))


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
    "AREA_KINDS",
    "ARROW",
    "ARROW_STYLES",
    "BACKGROUND",
    "BACKGROUND_PRESETS",
    "BOX_KINDS",
    "COUNTER",
    "DEFAULT_BACKGROUND",
    "ELLIPSE",
    "FILLED",
    "HANDLES",
    "HIGHLIGHT",
    "LINE",
    "MOVE",
    "PALETTE",
    "PEN",
    "RECT",
    "REDACT",
    "REDACT_MODES",
    "SEGMENT_KINDS",
    "SPOTLIGHT",
    "STROKE_KINDS",
    "TEXT",
    "TEXT_STYLES",
    "TOOL_KEYS",
    "WIDTHS",
    "History",
    "Shape",
    "bounds",
    "clip_to",
    "constrain",
    "counter_size",
    "frame_padding",
    "grab_scope",
    "grip_at",
    "grip_cursor",
    "grips",
    "handle_at",
    "handle_points",
    "hide_fractions",
    "hit",
    "is_meaningful",
    "next_counter",
    "normalized",
    "reshape",
    "resize",
    "rough_measure",
    "shape_at",
    "text_size",
    "toolbar_origin",
    "translated",
]
