# ruff: noqa: E501  (dense hand-authored geometry tables read better unwrapped)
"""Author the agent-symbol accessory catalog as shared primitive geometry.

One JSON file feeds both renderers: the flat profile symbol projects every part
straight onto the SVG plane, and Blender (build_companion_accessories.py) builds
the same parts as meshes for the map companion. Edit this file, then run:

    python scripts/art/companion_accessories.py
    blender --background --factory-startup --python scripts/art/build_companion_accessories.py

Coordinates are AgentSymbol SVG units relative to a slot anchor: x right,
y down, z toward the viewer. Each shape scales a slot by its own factor.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTLINES = ROOT / "art/studies/agent-symbol-companions/source/outlines.json"
OUTPUT = ROOT / "jarvis/ui/web/frontend/src/components/society/companion/accessories.json"

# Draw order of the flat symbol; also the persisted slot names.
SLOTS = ["back", "outfit", "neck", "mouth", "face", "head", "held"]
# Anchor depth as a fraction of the body's front surface (0 = body centre).
SLOT_DEPTH = {"back": -1, "outfit": 1, "neck": 1, "mouth": 1, "face": 1, "head": 0, "held": 0.4}

EYE_Y = {"cloud": 23, "triangle": 23, "drop": 25}

# Anchor points [x, y, scale] in symbol coordinates, tuned per silhouette.
ANCHORS = {
    "circle": {"head": [20, 5.2, 1], "neck": [20, 27.6, 1], "held": [33.5, 29, 1], "back": [20, 20, 1]},
    "squircle": {"head": [20, 4.4, 1], "neck": [20, 27.6, 1], "held": [34, 29, 1], "back": [20, 20, 1]},
    "pill": {"head": [20, 9.6, 0.95], "neck": [20, 26.2, 0.92], "held": [35.5, 26, 0.95], "back": [20, 20, 0.95]},
    "triangle": {"head": [20, 8.6, 0.72], "neck": [20, 30.4, 0.8], "held": [32.5, 31, 0.9], "back": [20, 22, 0.9]},
    "hexagon": {"head": [20, 5.8, 0.9], "neck": [20, 27.6, 0.95], "held": [33, 29, 0.95], "back": [20, 20, 0.95]},
    "cloud": {"head": [19.2, 6.6, 0.95], "neck": [20, 30, 0.8], "held": [37, 27.5, 0.9], "back": [21, 20, 1]},
    "drop": {"head": [20, 9.6, 0.72], "neck": [20, 33.8, 0.92], "held": [32.5, 34, 0.95], "back": [20, 25, 0.95]},
}
# Neckline just below the eyes, so clothing dresses the body and leaves the face
# free. The flat symbol tilts its face; SLOT_FLAT_SHIFT recentres clothing there.
NECK = {
    "circle": [20, 22.4, 1.0], "squircle": [20, 22.4, 1.0], "pill": [20, 22.2, 0.85],
    "triangle": [20, 28.0, 0.8], "hexagon": [20, 22.4, 0.95], "cloud": [20, 27.6, 0.7],
    "drop": [20, 30.0, 0.95],
}
SLOT_FLAT_SHIFT = {"outfit": 1.0, "neck": 1.0}
# Per-item size on top of the slot anchor scale (hats read small at list size).
ITEM_SCALE = {"crown": 1.15, "top_hat": 1.12, "cap": 1.15}
FACE_SCALE = {"triangle": 0.9, "cloud": 0.9}
MOUTH_DROP = 6.8
# Held props and mouth props read too small at list sizes without a boost.
HELD_BOOST, MOUTH_BOOST = 1.35, 1.15

GOLD, GOLD_DARK = "#f2c14e", "#c8922a"
INK, WHITE, CREAM = "#101014", "#f8fafc", "#f5f0e6"
RED, RED_DARK, PINK = "#e0483e", "#a8322b", "#f9a8d4"
BLUE, CYAN, GREEN = "#2f6fe0", "#22d3ee", "#5fae3a"
BROWN, BROWN_DARK, STEEL = "#8a5a32", "#4a2f1d", "#9aa3ad"


# --- primitive builders -----------------------------------------------------

def sphere(c, r, fill, **kw):
    r = [r, r, r] if isinstance(r, (int, float)) else r
    return {"t": "sphere", "c": list(c), "r": list(r), "fill": fill, **kw}


def dome(c, r, fill, **kw):
    """Upper half of an ellipsoid; c is the centre of its flat base."""
    return {"t": "dome", "c": list(c), "r": list(r), "fill": fill, **kw}


def box(c, s, fill, bevel=0.0, **kw):
    return {"t": "box", "c": list(c), "s": list(s), "round": bevel, "fill": fill, **kw}


def cyl(c, r_bottom, r_top, h, fill, dz=1.0, **kw):
    """Upright frustum; a zero radius makes a cone."""
    return {"t": "cyl", "c": list(c), "r": [r_bottom, r_top], "h": h, "dz": dz, "fill": fill, **kw}


def torus(c, big, small, fill, tilt=0.0, **kw):
    """tilt 0 faces the viewer, 90 lies flat like a halo."""
    return {"t": "torus", "c": list(c), "R": big, "r": small, "tilt": tilt, "fill": fill, **kw}


def poly(pts, fill, z=0.0, d=1.0, smooth=False, **kw):
    return {"t": "poly", "pts": [list(p) for p in pts], "z": z, "d": d, "smooth": smooth, "fill": fill, **kw}


def tube(pts, w, fill, smooth=False, **kw):
    pts = [list(p) + [0.0] * (3 - len(p)) for p in pts]
    return {"t": "tube", "pts": pts, "w": w, "smooth": smooth, "fill": fill, **kw}


def region(pts, fill, mode="wrap", **kw):
    """A convex patch of the body surface itself: clipped to the silhouette."""
    return {"t": "region", "pts": [list(p) for p in pts], "mode": mode, "fill": fill, **kw}


def band(y0, y1, fill, mode="wrap"):
    return region([(-60, y0), (60, y0), (60, y1), (-60, y1)], fill, mode)


# --- shape helpers ----------------------------------------------------------

def star(cx, cy, outer, inner, n=5, turn=0.0):
    pts = []
    for i in range(n * 2):
        a = math.radians(turn - 90 + i * 180 / n)
        rad = outer if i % 2 == 0 else inner
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def mirror_x(half):
    """Close a symmetric outline from its right half (top centre to bottom centre)."""
    left = [(-x, y) for x, y in reversed(half[1:-1])]
    return list(half) + left


def quad_curve(p0, p1, p2, steps):
    out = []
    for i in range(steps):
        t = i / (steps - 1)
        out.append(tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t * t * c for a, b, c in zip(p0, p1, p2, strict=True)))
    return out


def rotate_xy(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def place(parts, dx=0.0, dy=0.0, rot=0.0, flip=False):
    """Move a part group: optional mirror, then rotation about the origin, then shift."""
    out = []
    for p in parts:
        q = json.loads(json.dumps(p))

        def tf(x, y):
            if flip:
                x = -x
            x, y = rotate_xy(x, y, rot)
            return x + dx, y + dy

        if "c" in q:
            q["c"][0], q["c"][1] = tf(q["c"][0], q["c"][1])
            spin = -q.get("rot", 0.0) if flip else q.get("rot", 0.0)
            if spin + rot:
                q["rot"] = spin + rot
        if "pts" in q:
            pts = [list(tf(pt[0], pt[1])) + pt[2:] for pt in q["pts"]]
            q["pts"] = list(reversed(pts)) if flip and q["t"] in ("poly", "region") else pts
        out.append(q)
    return out


def both_sides(parts, dx, dy=0.0, rot=0.0):
    """Right copy at +dx, mirrored copy at -dx (rotation mirrored too)."""
    return place(parts, dx, dy, rot) + place(place(parts, 0, 0, rot), -dx, dy, flip=True)



# --- catalog ----------------------------------------------------------------
# Ten pieces, each drawn for the flat list size first and checked on every
# silhouette. Clothing dresses the body below the eyes; the face stays free.

NAVY, NAVY_LAPEL, NAVY_DARK = "#2c3a55", "#212b42", "#18202f"
SHIRT, SHIRT_SHADE = "#f4f6f8", "#dfe4ea"
BURGUNDY, BURGUNDY_DARK = "#b3243b", "#86182b"
TUX, TUX_SATIN = "#22232b", "#3a3c48"
COAT, COAT_SHADE, COAT_BLUE = "#f2f4f7", "#dde2e9", "#a9c8ef"
TEAL = "#0f766e"
HOODIE, HOODIE_DARK, MINT = "#4a5160", "#3a404c", "#34d399"


def octagon(cx, cy, r):
    return [(cx + r * math.cos(math.radians(22.5 + 45 * i)), cy + r * math.sin(math.radians(22.5 + 45 * i)))
            for i in range(8)]


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def front(pts, fill, **kw):
    return region(pts, fill, mode="front", **kw)


def rim(width, fill, **kw):
    """The body silhouette grown outward, drawn behind it (a hood, a frame)."""
    return {"t": "rim", "w": width, "fill": fill, **kw}


def torso(top=0.0):
    """Everything below sloped shoulders: the neckline is flat only under the chin."""
    pts = [(-20, 7.0), (-15, 3.2), (-10, 1.0), (-5, -0.2), (5, -0.2), (10, 1.0), (15, 3.2), (20, 7.0), (60, 60), (-60, 60)]
    return [(x, y + top) for x, y in pts]


def lapels(v_tip, fill, reach=8.2):
    """Two lapels along a shirt V that opens at the neckline and closes at v_tip."""
    left = [(-4.6, -0.4), (0, v_tip), (-1.3, v_tip + 1.6), (-reach + 1.3, 3.4), (-reach, 0.2)]
    return [front(left, fill), front([(-x, y) for x, y in reversed(left)], fill)]


def outfit_items():
    items = {}
    jacket = lambda fill, top=0.0: region(torso(top), fill)  # noqa: E731
    collar = [front([(-4.4, -0.4), (-0.9, 0.2), (-2.6, 2.8)], SHIRT_SHADE),
              front([(4.4, -0.4), (2.6, 2.8), (0.9, 0.2)], SHIRT_SHADE)]
    pocket_square = [front(rect(5.0, 4.0, 8.8, 4.5), NAVY_DARK),
                     front([(5.4, 4.0), (6.9, 4.0), (6.0, 2.3)], SHIRT),
                     front([(6.4, 4.0), (8.2, 4.0), (7.6, 2.5)], SHIRT)]

    items["suit"] = [
        jacket(NAVY),
        front([(-4.6, -0.4), (4.6, -0.4), (0, 8.8)], SHIRT), *collar,
        front([(-1.0, 2.0), (1.0, 2.0), (2.1, 8.2), (0, 10.4), (-2.1, 8.2)], BURGUNDY),
        front([(-1.1, 3.9), (1.2, 3.1), (1.32, 3.75), (-1.2, 4.55)], BURGUNDY_DARK),
        front([(-1.6, 6.7), (1.75, 5.6), (1.85, 6.3), (-1.65, 7.4)], BURGUNDY_DARK),
        poly([(-1.55, -0.4), (1.55, -0.4), (1.0, 2.1), (-1.0, 2.1)], BURGUNDY, z=0.9, d=0.9),
        *lapels(8.8, NAVY_LAPEL),
        front(octagon(0, 11.7, 0.8), NAVY_DARK), front(octagon(0, 14.4, 0.8), NAVY_DARK),
        *pocket_square,
    ]
    items["tuxedo"] = [
        jacket(TUX),
        front([(-4.4, -0.4), (4.4, -0.4), (0, 9.8)], SHIRT),
        front(rect(-1.85, 3.0, -1.5, 7.6), SHIRT_SHADE), front(rect(1.5, 3.0, 1.85, 7.6), SHIRT_SHADE),
        front(octagon(0, 3.6, 0.5), INK), front(octagon(0, 5.8, 0.5), INK), front(octagon(0, 8.0, 0.45), INK),
        *lapels(9.8, TUX_SATIN),
        poly([(0, -0.1), (3.6, -1.5), (4.1, 0.9), (3.6, 2.7), (0, 1.3), (-3.6, 2.7), (-4.1, 0.9), (-3.6, -1.5)],
             TUX, z=0.9, d=1.0),
        sphere((0, 0.6, 1.4), [1.1, 1.3, 0.7], "#26272f"),
        front(rect(5.0, 4.0, 8.8, 4.5), "#0d0d10"),
        front([(5.4, 4.0), (6.9, 4.0), (6.0, 2.3)], SHIRT), front([(6.4, 4.0), (8.2, 4.0), (7.6, 2.5)], SHIRT),
    ]
    items["lab_coat"] = [
        jacket(COAT),
        front([(-4.0, -0.4), (4.0, -0.4), (0, 7.6)], COAT_BLUE),
        front([(-0.9, 1.8), (0.9, 1.8), (1.8, 6.6), (0, 8.4), (-1.8, 6.6)], TEAL),
        poly([(-1.4, -0.4), (1.4, -0.4), (0.9, 1.9), (-0.9, 1.9)], TEAL, z=0.9, d=0.8),
        *lapels(7.6, COAT_SHADE, reach=8.8),
        front(rect(-0.2, 9.2, 0.25, 40), COAT_SHADE),
        front(rect(5.6, 1.8, 6.3, 4.6), "#2f6fe0"), front(rect(6.9, 2.3, 7.6, 4.6), "#e0483e"),
        front(rect(4.6, 3.8, 8.8, 7.6), COAT_SHADE),
        front(rect(-8.8, 4.4, -5.0, 8.8), "#ffffff"), front(rect(-8.8, 4.4, -5.0, 5.5), TEAL),
        front(rect(-8.2, 6.4, -5.6, 6.8), "#c5ccd6"), front(rect(-8.2, 7.4, -6.4, 7.8), "#c5ccd6"),
    ]
    code = [tube([(5.2, 2.6, 0.5), (4.2, 3.5, 0.5), (5.2, 4.4, 0.5)], 0.55, MINT),
            tube([(6.6, 2.4, 0.5), (5.8, 4.6, 0.5)], 0.55, MINT),
            tube([(7.2, 2.6, 0.5), (8.2, 3.5, 0.5), (7.2, 4.4, 0.5)], 0.55, MINT)]
    strings = both_sides([tube([(0, 2.2, 0.7), (-0.3, 6.6, 0.7)], 0.7, "#e5e7eb"),
                          sphere((-0.3, 7.0, 0.8), [0.45, 0.7, 0.45], "#9ca3af")], 2.2)
    items["hoodie"] = [
        rim(1.9, HOODIE, maxY=4.0),
        region(torso(0.8), HOODIE),
        front([(-5.2, 0.8), (5.2, 0.8), (4.0, 2.7), (0, 3.3), (-4.0, 2.7)], HOODIE_DARK),
        front([(-6.4, 7.6), (6.4, 7.6), (7.8, 13.0), (-7.8, 13.0)], HOODIE_DARK),
        *strings, *code,
    ]
    return items


def head_items():
    items = {}
    spikes = []
    for i in range(5):
        a = math.radians(36 + i * 72)
        x, z = 8.2 * math.sin(a), 8.2 * math.cos(a)
        spikes += [cyl((x, -6.5, z), 2.5, 0, 5.6, GOLD), sphere((x, -9.7, z), 1.05, "#ffe08a")]
    sapphires = [sphere((8.5 * math.sin(math.radians(a)), -1.9, 8.5 * math.cos(math.radians(a))), [1.1, 1.1, 0.6],
                        "#2f6fe0") for a in (-48, 48)]
    items["crown"] = [
        dome((0, -1.6, 0), [7.2, 6.0, 7.2], "#a61e36"),
        cyl((0, -1.8, 0), 8.0, 8.5, 4.4, GOLD),
        cyl((0, 0.25, 0), 8.15, 8.15, 0.9, GOLD_DARK), cyl((0, -3.75, 0), 8.55, 8.55, 0.7, GOLD_DARK),
        *spikes,
        sphere((0, -1.9, 8.6), [1.7, 1.7, 0.8], "#d7263d"), *sapphires,
        sphere((0, -7.8, 0), 1.25, GOLD),
        tube([(0, -8.8, 0), (0, -11.8, 0)], 0.85, GOLD), tube([(-1.1, -10.7, 0), (1.1, -10.7, 0)], 0.85, GOLD),
        box((-3.2, -2.9, 8.9), (4.2, 0.6, 0.1), "#ffe39b", 0.3, only="2d"),
    ]
    items["top_hat"] = place([
        cyl((0, -0.5, 0), 12.4, 12.4, 1.4, "#1e1f26"),
        sphere((-12.2, -0.9, 0), [1.6, 1.2, 4.2], "#1e1f26", only="2d"),
        sphere((12.2, -0.9, 0), [1.6, 1.2, 4.2], "#1e1f26", only="2d"),
        cyl((0, -7.6, 0), 7.4, 7.8, 12.6, "#272832"),
        cyl((0, -2.5, 0), 7.5, 7.55, 2.8, BURGUNDY),
        box((-4.4, -8.6, 7.9), (1.3, 9.6, 0.1), "#3b3d4a", 0.6, only="2d"),
    ], dy=0.8, rot=-6)
    seams = [tube([(0, -6.2, 0), (0, 1.4, 0)], 0.45, "#2556b8", only="2d"),
             *both_sides([tube([(-0.2, -5.6, 0), (-1.6, 1.6, 0)], 0.45, "#2556b8", smooth=True, only="2d")], -4.6)]
    items["cap"] = [
        dome((0, 1.8, 0), [9.4, 8.4, 9.4], "#2f6fe0"), *seams,
        sphere((0, -6.6, 0), 1.2, "#2556b8"),
        poly(star(0, -1.4, 2.0, 0.9), "#ffffff", z=8.7, d=0.4),
        sphere((0, 2.4, 7.6), [11.6, 1.9, 7.4], "#1f4aa0"),
        box((0, 1.6, 8.9), (14, 0.5, 0.1), "#3a6fd0", 0.25, only="2d"),
    ]
    items["headphones"] = [
        tube([(-12.8, 5.6, 0), (-12.0, -1.8, 0), (-6.8, -7.2, 0), (0, -8.6, 0), (6.8, -7.2, 0), (12.0, -1.8, 0),
              (12.8, 5.6, 0)], 2.4, "#2b2d34", smooth=True),
        tube([(-5.4, -6.4, 0.9), (0, -7.6, 0.9), (5.4, -6.4, 0.9)], 1.6, "#454956", smooth=True),
        *both_sides([sphere((0, 7.4, 0), [1.4, 4.6, 4.2], "#f97316"),
                     sphere((1.7, 7.4, 0), [2.6, 5.2, 4.8], "#2b2d34"),
                     box((2.6, 7.4, 0), (1.2, 4.4, 3.2), "#3d404a", 0.5)], 11.4),
    ]
    return items


def face_items():
    lens = [(-8.6, -2.7), (-1.4, -2.7), (-1.6, 1.2), (-3.0, 2.9), (-7.0, 2.9), (-8.4, 1.2)]
    return {"sunglasses": [
        poly(lens, "#14151a", z=0.9, d=1.0, smooth=False),
        poly([(-x, y) for x, y in reversed(lens)], "#14151a", z=0.9, d=1.0),
        tube([(-9.2, -2.7, 1.2), (9.2, -2.7, 1.2)], 1.3, "#14151a"),
        tube([(-1.5, -1.4, 1.1), (0, -2.0, 1.1), (1.5, -1.4, 1.1)], 0.9, "#14151a", smooth=True),
        *both_sides([tube([(-9.0, -2.4, 0.8), (-10.6, -2.2, 0)], 0.9, "#14151a")], 0),
        tube([(-7.0, -1.4, 1.6), (-5.6, 0.9, 1.6)], 0.7, "#5b5f6e", only="2d"),
        tube([(-5.4, -1.6, 1.6), (-4.8, -0.6, 1.6)], 0.5, "#5b5f6e", only="2d"),
        tube([(2.2, -1.4, 1.6), (3.6, 0.9, 1.6)], 0.7, "#5b5f6e", only="2d"),
    ]}


def mouth_items():
    return {"cigar": [
        tube([(0.4, 0, 0.8), (9.6, 2.2, 1.4)], 2.8, "#7b4a28"),
        tube([(0.4, 0, 0.82), (1.5, 0.27, 0.86)], 2.85, "#5a3519"),
        tube([(2.6, 0.52, 0.9), (3.8, 0.82, 0.95)], 3.0, GOLD),
        tube([(3.0, 0.62, 0.92), (3.4, 0.72, 0.94)], 3.05, BURGUNDY),
        tube([(9.6, 2.2, 1.4), (10.9, 2.5, 1.45)], 2.75, "#a3a3a3"),
        tube([(10.9, 2.5, 1.45), (11.3, 2.6, 1.47)], 2.55, "#ff6a1a", glow=True),
        tube([(11.8, 1.3, 1.5), (13.4, -1.4, 1.6), (11.9, -4.4, 1.7), (13.8, -7.6, 1.8)], 0.8, "#d6d8de", smooth=True),
        tube([(12.8, -0.6, 1.5), (14.6, -2.4, 1.6), (14.0, -4.8, 1.7)], 0.55, "#d6d8de", smooth=True),
    ]}


# --- bounds -------------------------------------------------------------------

def outline_2d(part):
    """Approximate the part's front projection as points, for the symbol viewBox."""
    t = part["t"]
    if t in ("region", "rim"):
        return []
    if t in ("poly",):
        return [tuple(p) for p in part["pts"]]
    if t == "tube":
        w = part["w"] / 2
        return [(x + dx, y + dy) for x, y, _ in part["pts"] for dx, dy in ((-w, -w), (w, w))]
    cx, cy = part["c"][0], part["c"][1]
    if t in ("sphere", "dome"):
        rx, ry = part["r"][0], part["r"][1]
        pts = [(rx * math.cos(a), ry * math.sin(a)) for a in (i * math.tau / 16 for i in range(16))]
        if t == "dome":
            pts = [(x, min(y, 0)) for x, y in pts]
    elif t == "box":
        w, h = part["s"][0] / 2, part["s"][1] / 2
        pts = [(-w, -h), (w, -h), (w, h), (-w, h)]
    elif t == "cyl":
        rb, rt = part["r"]
        h = part["h"] / 2
        pts = [(-rb, h), (rb, h), (rt, -h), (-rt, -h)]
    else:  # torus
        big, small = part["R"] + part["r"], part["R"] * abs(math.cos(math.radians(part["tilt"]))) + part["r"]
        pts = [(-big, -small), (big, -small), (big, small), (-big, small)]
    rot = part.get("rot", 0)
    return [(cx + x2, cy + y2) for x2, y2 in (rotate_xy(x, y, rot) for x, y in pts)]


def bounds(parts):
    pts = [p for part in parts for p in outline_2d(part)]
    if not pts:
        return [0, 0, 0, 0]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def rounded(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, list):
        return [rounded(v) for v in value]
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    return value

def main():
    outlines = json.loads(OUTLINES.read_text(encoding="utf-8"))
    shapes = {}
    for name, anchors in ANCHORS.items():
        ys = [y for _, y in outlines[name]]
        eye = EYE_Y.get(name, 17.2)
        face_k = FACE_SCALE.get(name, 1)
        shapes[name] = {
            "top": round(min(ys), 3), "bottom": round(max(ys), 3), "eyeY": eye,
            "anchors": {**anchors, "neck": NECK[name], "outfit": NECK[name], "face": [20, eye, face_k],
                        "mouth": [20, eye + MOUTH_DROP * face_k, round(face_k * MOUTH_BOOST, 3)],
                        "held": [*anchors["held"][:2], round(anchors["held"][2] * HELD_BOOST, 3)]},
        }
    groups = {"outfit": outfit_items(), "head": head_items(), "face": face_items(), "mouth": mouth_items()}
    items = []
    for slot, group in groups.items():
        for item_id, parts in group.items():
            items.append({"id": item_id, "slot": slot, "scale": ITEM_SCALE.get(item_id, 1), "bounds": bounds(parts),
                          "regions": any(p["t"] in ("region", "rim") for p in parts), "parts": parts})
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)), "accessory ids must be unique"
    catalog = {"schema": 2, "slots": SLOTS, "slotDepth": SLOT_DEPTH, "slotFlatShift": SLOT_FLAT_SHIFT,
               "frontDepthM": 0.25, "shapes": shapes, "items": items}
    OUTPUT.write_text(json.dumps(rounded(catalog), separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(items)} accessories -> {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
