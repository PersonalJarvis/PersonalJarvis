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
# Written by build_agent_companions.py: body surface depth (m) under each slot anchor.
BODY_DEPTHS = ROOT / "art/studies/agent-symbol-companions/source/body_depths.json"
OUTPUT = ROOT / "jarvis/ui/web/frontend/src/components/society/companion/accessories.json"

# Draw order of the flat symbol; also the persisted slot names.
SLOTS = ["back", "outfit", "neck", "mouth", "face", "head", "held"]
# Fallback anchor depth as a fraction of frontDepthM (0 = body centre); each shape
# carries its measured surface depth per slot in "depth".
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


def shade(pts, opacity=0.16):
    """Flat-only fabric shadow on the clothing (light falls from the upper left)."""
    return region(pts, "#05070d", mode="front", opacity=opacity, only="2d")


def chin_shadow(width):
    """The head's soft shadow across the top of the shirt."""
    return front([(-width, -0.4), (width, -0.4), (width - 1.0, 1.3), (-width + 1.0, 1.3)], "#05070d",
                 opacity=0.2, only="2d")


SIDE_SHADE = [(8.0, 1.2), (60, 1.2), (60, 60), (11.5, 60)]
SHOULDER_LIGHT = [(-60, 0.0), (-9.5, 0.8), (-12.5, 60), (-60, 60)]


def lapels(v_tip, fill, reach=8.2):
    """Two lapels along a shirt V that opens at the neckline and closes at v_tip."""
    left = [(-4.6, -0.4), (0, v_tip), (-1.3, v_tip + 1.6), (-reach + 1.3, 3.4), (-reach, 0.2)]
    edge = [(-reach, 0.2, 0.3), (-reach + 1.3, 3.4, 0.3), (-1.3, v_tip + 1.6, 0.3)]
    # A fine light edge along each lapel's outer roll (flat only).
    shine = [tube(edge, 0.45, "#ffffff", opacity=0.16, only="2d"),
             tube([(-x, y, z) for x, y, z in edge], 0.45, "#ffffff", opacity=0.1, only="2d")]
    return [front(left, fill), front([(-x, y) for x, y in reversed(left)], fill), *shine]


def outfit_items():
    items = {}
    jacket = lambda fill, top=0.0: region(torso(top), fill)  # noqa: E731
    collar = [front([(-4.4, -0.4), (-0.9, 0.2), (-2.6, 2.8)], SHIRT_SHADE),
              front([(4.4, -0.4), (2.6, 2.8), (0.9, 0.2)], SHIRT_SHADE)]
    pocket_square = [front(rect(5.0, 4.0, 8.8, 4.5), NAVY_DARK),
                     front([(5.4, 4.0), (6.9, 4.0), (6.0, 2.3)], SHIRT),
                     front([(6.4, 4.0), (8.2, 4.0), (7.6, 2.5)], SHIRT)]

    items["suit"] = [
        jacket(NAVY), shade(SIDE_SHADE), region(SHOULDER_LIGHT, "#ffffff", mode="front", opacity=0.05, only="2d"),
        front([(-4.6, -0.4), (4.6, -0.4), (0, 8.8)], SHIRT), *collar, chin_shadow(4.6),
        front([(-1.0, 2.0), (1.0, 2.0), (2.1, 8.2), (0, 10.4), (-2.1, 8.2)], BURGUNDY),
        front([(-1.1, 3.9), (1.2, 3.1), (1.32, 3.75), (-1.2, 4.55)], BURGUNDY_DARK),
        front([(-1.6, 6.7), (1.75, 5.6), (1.85, 6.3), (-1.65, 7.4)], BURGUNDY_DARK),
        poly([(-1.55, -0.4), (1.55, -0.4), (1.0, 2.1), (-1.0, 2.1)], BURGUNDY, z=0.9, d=0.9),
        *lapels(8.8, NAVY_LAPEL),
        front(octagon(0, 11.7, 0.8), NAVY_DARK), front(octagon(0, 14.4, 0.8), NAVY_DARK),
        *pocket_square,
    ]
    items["tuxedo"] = [
        jacket(TUX), shade(SIDE_SHADE, 0.22), region(SHOULDER_LIGHT, "#ffffff", mode="front", opacity=0.06, only="2d"),
        front([(-4.4, -0.4), (4.4, -0.4), (0, 9.8)], SHIRT), chin_shadow(4.4),
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
        jacket(COAT), shade(SIDE_SHADE, 0.08),
        front([(-4.0, -0.4), (4.0, -0.4), (0, 7.6)], COAT_BLUE), chin_shadow(4.0),
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
        rim(0.75, HOODIE_DARK, maxY=4.0, only="2d"),
        region(torso(0.8), HOODIE), shade([(x, y + 0.8) for x, y in SIDE_SHADE], 0.18),
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
        sphere((0, 0.9, 0), [8.8, 1.7, 0.1], "#05070d", opacity=0.22, only="2d"),
        dome((0, -1.6, 0), [7.2, 6.0, 7.2], "#a61e36"),
        cyl((0, -1.8, 0), 8.0, 8.5, 4.4, GOLD),
        cyl((0, 0.25, 0), 8.15, 8.15, 0.9, GOLD_DARK), cyl((0, -3.75, 0), 8.55, 8.55, 0.7, GOLD_DARK),
        *spikes,
        sphere((0, -1.9, 8.6), [1.7, 1.7, 0.8], "#d7263d", gloss=True), *sapphires,
        sphere((-0.55, -2.5, 9.5), [0.5, 0.4, 0.1], "#ffffff", opacity=0.85, only="2d"),
        sphere((0, -7.8, 0), 1.25, GOLD),
        tube([(0, -8.8, 0), (0, -11.8, 0)], 0.85, GOLD), tube([(-1.1, -10.7, 0), (1.1, -10.7, 0)], 0.85, GOLD),
        box((-3.2, -2.9, 8.9), (4.2, 0.6, 0.1), "#ffe39b", 0.3, only="2d"),
    ]
    items["top_hat"] = place([
        sphere((0, 1.0, 0), [10.8, 1.7, 0.1], "#05070d", opacity=0.24, only="2d"),
        cyl((0, -0.5, 0), 12.4, 12.4, 1.4, "#1e1f26"),
        sphere((-12.2, -0.9, 0), [1.6, 1.2, 4.2], "#1e1f26", only="2d"),
        sphere((12.2, -0.9, 0), [1.6, 1.2, 4.2], "#1e1f26", only="2d"),
        cyl((0, -7.6, 0), 7.4, 7.8, 12.6, "#272832"),
        cyl((0, -2.5, 0), 7.5, 7.55, 2.8, BURGUNDY, gloss=True),
        box((-4.4, -8.6, 7.9), (1.3, 9.6, 0.1), "#3b3d4a", 0.6, only="2d"),
    ], dy=0.8, rot=-6)
    seams = [tube([(0, -6.2, 0), (0, 1.4, 0)], 0.45, "#2556b8", only="2d"),
             *both_sides([tube([(-0.2, -5.6, 0), (-1.6, 1.6, 0)], 0.45, "#2556b8", smooth=True, only="2d")], -4.6)]
    items["cap"] = [
        sphere((0, 3.0, 0), [9.4, 1.6, 0.1], "#05070d", opacity=0.18, only="2d"),
        dome((0, 1.8, 0), [9.4, 8.4, 9.4], "#2f6fe0"), *seams,
        sphere((0, -6.6, 0), 1.2, "#2556b8"),
        poly(star(0, -1.4, 2.0, 0.9), "#ffffff", z=8.7, d=0.4),
        sphere((0, 4.7, 7.5), [10.4, 1.4, 0.1], "#05070d", opacity=0.2, only="2d"),
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


def smoke(c, size=1.0):
    """Animated smoke rising from c: drifting puffs in the flat symbol and the 3D world."""
    return {"t": "smoke", "c": list(c), "size": size, "fill": "#e4e6ec"}


def face_items():
    """Glossy wayfarers: a gradient lens, a heavy brow bar, metal rivets and a glint."""
    frame_ink, lens_top, lens_bottom = "#0b0c10", "#151925", "#3d4f78"
    lens = [(-9.0, -2.9), (-1.2, -2.9), (-1.3, 0.4), (-1.9, 2.2), (-3.2, 3.0), (-6.6, 3.1), (-8.1, 2.3),
            (-8.8, 0.6)]
    cx = sum(x for x, _ in lens) / len(lens)
    cy = sum(y for _, y in lens) / len(lens)
    rim = [(x + (x - cx) * 0.16, y + (y - cy) * 0.2) for x, y in lens]
    mirror = lambda pts: [(-x, y) for x, y in reversed(pts)]  # noqa: E731
    glass = dict(z=1.0, d=0.7, gloss=True, gradient=[lens_top, lens_bottom])
    reflections = []
    for shift in (0.0, 10.0):
        reflections += [
            poly([(-7.0 + shift, -2.5), (-5.9 + shift, -2.5), (-7.7 + shift, 1.9), (-8.4 + shift, 1.0)], "#ffffff",
                 z=1.5, d=0.1, opacity=0.16, only="2d"),
            poly([(-5.2 + shift, -2.5), (-4.75 + shift, -2.5), (-6.5 + shift, 2.7), (-7.0 + shift, 2.5)], "#ffffff",
                 z=1.5, d=0.1, opacity=0.1, only="2d"),
        ]
    return {"sunglasses": [
        poly(rim, frame_ink, z=0.7, d=1.2, gloss=True), poly(mirror(rim), frame_ink, z=0.7, d=1.2, gloss=True),
        # The 3D lens takes the gradient's middle tone; gloss gives it the sheen.
        poly(lens, "#243049", **glass), poly(mirror(lens), "#243049", **glass),
        *reflections,
        tube([(-9.7, -3.2, 1.3), (9.7, -3.2, 1.3)], 1.7, frame_ink, gloss=True),
        tube([(-8.9, -3.7, 1.6), (8.9, -3.7, 1.6)], 0.35, "#626a7e", opacity=0.9, only="2d"),
        tube([(-1.4, -1.7, 1.1), (0, -2.4, 1.1), (1.4, -1.7, 1.1)], 1.0, frame_ink, smooth=True, gloss=True),
        *both_sides([tube([(-0.2, -2.7, 0.8), (-1.9, -2.4, -1.2)], 1.0, frame_ink, gloss=True)], -9.4),
        sphere((-8.9, -2.9, 1.9), [0.5, 0.5, 0.3], "#d5d9e2", metal=True),
        sphere((8.9, -2.9, 1.9), [0.5, 0.5, 0.3], "#d5d9e2", metal=True),
        poly(star(6.9, -1.5, 1.7, 0.32, 4), "#ffffff", z=1.8, d=0.1, only="2d", anim="twinkle"),
        poly(star(-3.6, -1.9, 1.0, 0.22, 4), "#ffffff", z=1.8, d=0.1, only="2d", anim="twinkle-late"),
    ]}


def mouth_items():
    """A Churchill cigar: veined wrapper, gold band with crest, ash rings, a breathing ember."""
    wrapper, wrapper_light, wrapper_dark = "#6f3f1f", "#a8693a", "#3d2110"
    body = [
        tube([(0.8, 0, 0.8), (10.6, 0, 0.8)], 3.2, wrapper),
        tube([(1.2, -0.65, 1.4), (10.6, -0.65, 1.4)], 1.0, wrapper_light, opacity=0.55, only="2d"),
        tube([(2.0, -1.05, 1.6), (9.6, -1.05, 1.6)], 0.32, "#e6ab72", opacity=0.45, only="2d"),
        tube([(1.0, 0.95, 1.4), (10.8, 0.95, 1.4)], 0.9, wrapper_dark, opacity=0.5, only="2d"),
        # The wrapper meets the ash in a clean cut, not a rounded cap.
        tube([(10.6, 0, 0.8), (11.0, 0, 0.8)], 3.2, wrapper),
        *[tube([(x, -1.45, 1.6), (x + 0.8, 1.4, 1.6)], 0.22, "#4a2812", opacity=0.5, only="2d")
          for x in (5.0, 7.4, 9.5)],
        tube([(0.6, 0, 0.82), (1.5, 0, 0.84)], 3.25, "#5a3519"),
        # The band: a gold ring with a burgundy stripe and a small crest.
        cyl((3.4, 0, 0.8), 1.74, 1.74, 1.8, GOLD, rot=90, metal=True),
        cyl((3.4, 0, 0.8), 1.77, 1.77, 0.55, BURGUNDY, rot=90),
        sphere((3.4, 0, 2.6), [0.62, 0.62, 0.3], "#ffe08a", metal=True),
        *[tube([(x, -1.7, 1.7), (x, 1.7, 1.7)], 0.2, GOLD_DARK, opacity=0.9, only="2d") for x in (2.55, 4.25)],
        cyl((11.65, 0, 0.8), 1.55, 1.52, 1.3, "#9a9a9a", rot=90),
        *[tube([(x, -1.5, 1.6), (x, 1.5, 1.6)], 0.25, "#d0d0d0", opacity=0.75, only="2d") for x in (11.4, 11.9)],
        cyl((12.5, 0, 0.8), 1.5, 1.35, 0.45, "#ff5a14", rot=90, glow=True),
        sphere((12.8, 0, 1.0), [3.0, 3.0, 0.1], "#ff7a2a", opacity=0.2, only="2d", anim="ember"),
        sphere((12.8, 0, 1.1), [1.7, 1.9, 0.1], "#ff9a3c", opacity=0.35, only="2d", anim="ember"),
        sphere((12.85, 0, 1.2), [0.45, 1.2, 0.4], "#ffd25a", glow=True, anim="ember"),
    ]
    placed = place(body, rot=12)
    tip_x, tip_y = rotate_xy(12.9, 0, 12)
    wisps = [
        tube([(tip_x + 0.4, tip_y - 1.0, 1.4), (tip_x + 1.9, tip_y - 3.6, 1.5), (tip_x + 0.5, tip_y - 6.4, 1.6),
              (tip_x + 2.2, tip_y - 9.4, 1.7)], 0.85, "#d9dbe2", smooth=True, opacity=0.55, only="2d"),
        tube([(tip_x + 1.2, tip_y - 2.2, 1.4), (tip_x + 3.0, tip_y - 4.0, 1.5), (tip_x + 2.4, tip_y - 6.2, 1.6)],
             0.55, "#d9dbe2", smooth=True, opacity=0.4, only="2d"),
    ]
    return {"cigar": [*placed, *wisps, smoke((tip_x + 0.6, tip_y - 1.2, 1.5))]}


# --- bounds -------------------------------------------------------------------

def outline_2d(part):
    """Approximate the part's front projection as points, for the symbol viewBox."""
    t = part["t"]
    if t in ("region", "rim"):
        return []
    if t == "smoke":
        # Puffs drift up and a little right while they grow.
        cx, cy = part["c"][0], part["c"][1]
        return [(cx - 2.5, cy - 12.0), (cx + 6.0, cy + 1.0)]
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
    depths = json.loads(BODY_DEPTHS.read_text(encoding="utf-8"))
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
            "depth": depths[name],
        }
    groups = {"outfit": outfit_items(), "head": head_items(), "face": face_items(), "mouth": mouth_items()}
    for group in groups.values():
        for parts in group.values():
            for part in parts:
                if part["fill"] in (GOLD, GOLD_DARK, "#ffe08a"):
                    part["metal"] = True
                if part["fill"] in ("#14151a", TUX_SATIN):
                    part["gloss"] = True
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
