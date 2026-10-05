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


def heart(cx, cy, size):
    pts = []
    for i in range(28):
        t = i / 28 * math.tau
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * size / 17, cy - y * size / 17 + size * 0.1))
    return pts


def mirror_x(half):
    """Close a symmetric outline from its right half (top centre to bottom centre)."""
    left = [(-x, y) for x, y in reversed(half[1:-1])]
    return list(half) + left


def leaf(x, y, length, width, angle):
    base = [(0, 0), (length * 0.35, -width), (length, 0), (length * 0.4, width * 0.8)]
    a = math.radians(angle)
    return [(x + px * math.cos(a) - py * math.sin(a), y + px * math.sin(a) + py * math.cos(a)) for px, py in base]


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

def head_items():
    items = {}
    spikes = []
    for i in range(5):
        a = math.radians(i * 72)
        x, z = 8.3 * math.sin(a), 8.3 * math.cos(a)
        spikes += [cyl((x, -6.8, z), 2.7, 0, 6.2, GOLD), sphere((x, -10.4, z), 1.05, GOLD)]
    items["crown"] = [cyl((0, -2, 0), 8, 8.6, 4.4, GOLD), *spikes,
                      sphere((0, -2, 8.7), [1.6, 1.6, 0.8], RED),
                      sphere((-5.2, -2, 6.9), [1.1, 1.1, 0.6], BLUE), sphere((5.2, -2, 6.9), [1.1, 1.1, 0.6], BLUE)]
    items["top_hat"] = place([cyl((0, -0.6, 0), 12, 12, 1.4, "#2a2a31"),
                              cyl((0, -7.8, 0), 7.6, 8, 13, "#33333b"),
                              cyl((0, -2.6, 0), 7.85, 7.9, 2.8, "body")], rot=-7)
    items["party_hat"] = place([cyl((0, -7.5, 0), 6.6, 0, 15, "#f472b6"),
                                sphere((-1.6, -3.2, 4.6), [1.1, 1.1, 0.5], "#fde047"),
                                sphere((2.4, -4.6, 3.6), [1, 1, 0.5], CYAN),
                                sphere((-0.6, -8.2, 2.6), [0.9, 0.9, 0.5], "#a78bfa"),
                                sphere((0, -15.6, 0), 2.3, "#fde047")], rot=13)
    items["cap"] = [dome((0, 1.6, 0), [9.2, 8, 9.2], BLUE), sphere((0, 1.9, 8.2), [8.8, 1.4, 6.2], "#1f4fb0"),
                    sphere((0, -6.4, 0), 1.2, "#1f4fb0")]
    items["beanie"] = [dome((0, 2.2, 0), [9.6, 9.8, 9.6], RED), cyl((0, 2.4, 0), 10, 10, 4, RED_DARK),
                       sphere((0, -8.4, 0), 3, CREAM)]
    items["cowboy_hat"] = place([sphere((0, 0.2, 0), [15, 1.3, 12], "#a8692e"),
                                 sphere((-13.8, -1.1, 0), [2.4, 1.7, 5], "#a8692e"),
                                 sphere((13.8, -1.1, 0), [2.4, 1.7, 5], "#a8692e"),
                                 cyl((0, -4.6, 0), 6.9, 5.8, 8.8, "#b8773a"),
                                 cyl((0, -1.6, 0), 6.8, 6.6, 1.8, "#3b2414")], rot=-5)
    items["wizard_hat"] = place([sphere((0, 0, 0), [13, 1.3, 11], "#4c2a96"),
                                 cyl((0, -10.2, 0), 7.8, 0, 20.4, "#5b35b8"),
                                 cyl((0, -1.6, 0), 7.3, 7.0, 1.8, GOLD),
                                 poly(star(1.4, -7.6, 2.2, 0.95), GOLD, z=4.4, d=0.6, glow=True),
                                 sphere((-1.8, -12.4, 1.8), 0.6, GOLD, glow=True),
                                 sphere((0.9, -4.4, 6.2), 0.5, GOLD, glow=True)], rot=-10)
    items["halo"] = [torus((0, -7.4, 0), 8, 1.15, "#fde68a", tilt=72, glow=True)]
    items["viking_helmet"] = [dome((0, 2, 0), [9.8, 8.8, 9.8], STEEL), cyl((0, 2.2, 0), 10.1, 10.1, 2.8, "#a77b2c"),
                              sphere((-4.6, 2.2, 9), 0.7, "#e7d39a"), sphere((0, 2.2, 10.1), 0.7, "#e7d39a"),
                              sphere((4.6, 2.2, 9), 0.7, "#e7d39a"),
                              tube([(0, -2, 0), (-0.4, -5, 0), (-0.8, -8, 0)], 0.8, "#a77b2c"),
                              *both_sides([cyl((0, -4.6, 0), 2.5, 0.3, 9, "#efe4cc")], 10.4, -0.4, 34)]
    items["chef_hat"] = [cyl((0, -1.2, 0), 8.2, 8.8, 4.6, "#fafaf8"),
                         sphere((-4.8, -7.8, 0), 5.4, "#f4f4f0"), sphere((4.8, -7.8, 0), 5.4, "#f4f4f0"),
                         sphere((0, -10.6, 0), 6.1, "#fafaf8"), cyl((0, 0.6, 0), 8.3, 8.3, 0.8, "#e4e2dc")]
    items["grad_cap"] = [cyl((0, -2.4, 0), 7.6, 7.8, 5, "#374151"), box((0, -5.6, 0), (21, 2.2, 21), "#2b3445", 0.4),
                         sphere((0, -6.5, 0), 0.9, GOLD),
                         tube([(0, -6.4, 0.6), (8, -6.4, 6), (10.4, -4.6, 7.6), (10.6, 0.2, 7.6)], 0.8, GOLD, smooth=True),
                         cyl((10.6, 1.6, 7.6), 1.3, 0.5, 2.8, GOLD)]
    items["headphones"] = [tube([(-12.6, 6, 0), (-11.8, -1.6, 0), (-6.6, -6.8, 0), (0, -8.2, 0), (6.6, -6.8, 0),
                                 (11.8, -1.6, 0), (12.6, 6, 0)], 2.2, "#26262b", smooth=True),
                           sphere((-13, 7.2, 0), [2.8, 4.5, 4.4], RED), sphere((13, 7.2, 0), [2.8, 4.5, 4.4], RED)]
    ear = [poly(mirror_x([(0, -15), (2.2, -13.2), (3.4, -7), (2.6, 1.5), (0, 1.5)]), "#f5f3f0", z=0, d=2.4,
                smooth=True),
           poly(mirror_x([(0, -12.6), (1.2, -11), (1.8, -6.4), (1.3, -1), (0, -1)]), PINK, z=1.3, d=0.6, smooth=True)]
    items["bunny_ears"] = both_sides(ear, 4.8, 0, 13)
    cat = [poly([(-5.6, 2.4), (-1.8, -9.4), (5.4, 2.4)], "body", z=0, d=2.6),
           poly([(-3, 1), (-1.6, -5.8), (2.4, 1)], PINK, z=1.5, d=0.6)]
    items["cat_ears"] = both_sides(cat, 7, 0, 18)
    items["devil_horns"] = both_sides([cyl((0, -3.4, 0), 2.4, 0, 7.4, "#d42f2f")], 5.6, 0, 22)
    petals = [sphere((2.9 * math.sin(math.radians(a)), -2.9 * math.cos(math.radians(a)), 2.6), [2.5, 2.5, 1.3], PINK)
              for a in range(0, 360, 72)]
    items["flower"] = place([*petals, sphere((0, 0, 3.4), [1.7, 1.7, 1], "#fde047"),
                             poly(leaf(1, 2.4, 6, 1.8, 30), GREEN, z=1.6, d=0.6, smooth=True)], -6.4, -0.6)
    antenna = [tube([(0, 1.5, 0), (0, -8.4, 0)], 1.1, "#3f3f46"), sphere((0, -9.8, 0), 2.3, "#a3e635", glow=True)]
    items["antennae"] = both_sides(antenna, 3.8, 0, 14)
    items["propeller_cap"] = [dome((0, 1.8, 0), [9.2, 7.8, 9.2], "#f43f5e"), cyl((0, 2, 0), 9.4, 9.4, 1.8, "#facc15"),
                              tube([(0, -5.6, 0), (0, -8.8, 0)], 1, "#3f3f46"),
                              box((0, -9.2, 0), (17.4, 1.1, 3.2), "#3b82f6", 0.5), sphere((0, -9.2, 0), 1.25, "#facc15")]
    items["santa_hat"] = [cyl((2.2, -6.2, 0), 8.6, 0.6, 13, "#d42f2f", rot=22),
                          cyl((0, 1.6, 0), 9.8, 9.8, 3.8, "#fafafa"), sphere((4.8, -12.6, 0), 2.7, "#fafafa")]
    items["bow"] = place([sphere((-3.4, 0, 0), [3.6, 2.6, 1.6], "#ec4899", rot=-18),
                          sphere((3.4, 0, 0), [3.6, 2.6, 1.6], "#ec4899", rot=18),
                          sphere((0, 0, 0.8), [1.5, 1.7, 1.2], "#be185d")], 6.4, -0.4, 16)
    items["sprout"] = [tube([(0, 1.5, 0), (0.4, -3, 0), (0, -6.4, 0)], 1.1, GREEN, smooth=True),
                       poly(leaf(0, -6.2, 7, 2.2, -150), "#84cc16", z=0, d=0.8, smooth=True),
                       poly(leaf(0, -6.2, 6, 2, -30), "#84cc16", z=0, d=0.8, smooth=True)]
    items["pirate_hat"] = place([
        poly([(-14.4, 0.6), (-12.4, -5.6), (-6.4, -9.4), (0, -8.4), (6.4, -9.4), (12.4, -5.6), (14.4, 0.6),
              (6.4, -1.4), (0, -1), (-6.4, -1.4)], "#34343e", z=0, d=8, smooth=True),
        tube([(-13.8, 0, 4.1), (-6.4, -1.8, 4.1), (0, -1.4, 4.1), (6.4, -1.8, 4.1), (13.8, 0, 4.1)], 0.9, GOLD,
             smooth=True),
        sphere((0, -5, 4.3), [2.3, 2.1, 0.6], "#f4f4f5"), box((0, -2.8, 4.3), (2, 1.2, 0.6), "#f4f4f5", 0.3),
        sphere((-0.8, -5.2, 4.9), 0.5, INK), sphere((0.8, -5.2, 4.9), 0.5, INK)], rot=-4)
    items["sweatband"] = [band(1.2, 4.4, RED), band(2.4, 3.2, "#fafafa", mode="front")]
    return items


def face_items():
    items = {}
    bridge = lambda fill, w=0.9: tube([(-1.3, -0.6, 0.8), (0, -1.3, 0.8), (1.3, -0.6, 0.8)], w, fill, smooth=True)  # noqa: E731
    items["sunglasses"] = [*both_sides([box((0, 0.2, 0.9), (7.6, 5.6, 1.2), "#121216", 2.2),
                                        tube([(-2.2, -1, 1.6), (-0.9, -2, 1.6)], 0.7, "#e7e7ea")], 4.9),
                           bridge("#121216")]
    items["round_glasses"] = [*both_sides([torus((0, 0, 0.7), 3.6, 0.55, "#b07a2a")], 4.9), bridge("#b07a2a", 0.75)]
    items["monocle"] = [torus((4.8, 0, 0.8), 4, 0.65, GOLD),
                        tube([(8.4, 1.6, 0.7), (9.4, 5.4, 0.5), (8.2, 9.6, 0.3)], 0.45, GOLD, smooth=True)]
    items["eye_patch"] = [sphere((-4.8, 0.2, 0.7), [3.9, 3.5, 0.6], "#18181b"),
                          tube([(-14, 2.6, -1), (-8.6, 0.4, 0.5), (-4.8, -1, 1), (0, -3.4, 0.6), (7, -5.6, 0.3),
                                (14, -6.4, -1)], 0.8, "#18181b", smooth=True)]
    items["star_glasses"] = [*both_sides([poly(star(0, 0, 4.7, 2.3), "#facc15", z=0.9, d=0.9)], 5.2),
                             bridge("#facc15")]
    items["heart_glasses"] = [*both_sides([poly(heart(0, 0, 4.6), "#ef4444", z=0.9, d=0.9, smooth=True)], 5.1),
                              bridge("#ef4444")]
    items["cyber_visor"] = [box((0.2, 0, 0.9), (20.4, 5.8, 1.4), "#1e293b", 2.7),
                            box((0.2, 0, 1.7), (15, 1.1, 0.3), CYAN, 0.5, glow=True)]
    items["glasses_3d"] = [box((0, 0, 0.6), (19.4, 6.4, 0.9), WHITE, 1),
                           box((-4.8, 0, 1.2), (7, 4.4, 0.4), "#ef4444", 0.6),
                           box((4.8, 0, 1.2), (7, 4.4, 0.4), CYAN, 0.6)]
    frame = [tube([(-3.8, -3), (3.8, -3), (3.8, 3.1), (-3.8, 3.1), (-3.8, -3)], 1.3, "#18181b")]
    items["nerd_glasses"] = [*both_sides(frame, 5), box((0, -1, 1.1), (2.8, 2.2, 0.6), "#f5f5f4", 0.3)]
    items["ski_goggles"] = [box((0, 0, 0.4), (21.4, 8.6, 1), "#e5e7eb", 3.9),
                            box((0, 0, 1.1), (19.2, 6.8, 0.8), "#fb923c", 3.2),
                            tube([(-6.4, -1.6, 1.6), (-3.6, -2.6, 1.6)], 0.8, "#fff7ed"),
                            *both_sides([tube([(0, 0, 0.4), (4.6, 0.6, -2)], 2.6, "#1f2937")], 10.6)]
    items["blush"] = both_sides([sphere((0, 4.6, 0.3), [2.6, 1.5, 0.4], "#fb7185")], 7.6)
    items["angry_brows"] = both_sides([tube([(-2.6, -6.4, 0.4), (2.4, -4.4, 0.4)], 1.4, INK)], -5.2)
    items["raised_brows"] = both_sides([tube([(-2.6, -5.2, 0.4), (0, -6.8, 0.4), (2.4, -5.8, 0.4)], 1.2, INK,
                                             smooth=True)], 5.0)
    return items


def mouth_items():
    items = {}
    smoke = lambda x, y: tube([(x, y, 1.4), (x + 1.6, y - 3, 1.5), (x + 0.2, y - 6.2, 1.6), (x + 2, y - 9.6, 1.7)],  # noqa: E731
                              0.85, "#d4d4d8", smooth=True)
    items["cigar"] = [tube([(0.6, 0, 0.6), (10, 2.4, 1.2)], 3, "#7a4a26"),
                      tube([(2.7, 0.55, 0.7), (3.6, 0.8, 0.8)], 3.2, GOLD),
                      tube([(10.1, 2.43, 1.2), (11.1, 2.7, 1.3)], 2.95, "#f97316", glow=True),
                      smoke(11.6, 1.2)]
    items["pipe"] = [tube([(0.6, 0, 0.6), (6.6, 1.6, 1.2)], 1.3, "#3b2617"),
                     cyl((8, 0.4, 1.2), 1.9, 2.3, 4.4, "#7a4a26"), sphere((8, -1.8, 1.2), [2.1, 0.5, 2.1], "#27272a"),
                     smoke(8, -2.6)]
    items["lollipop"] = [tube([(0.4, 0, 0.6), (6.6, 2.8, 1.4)], 0.9, "#fafafa"),
                         sphere((8.8, 3.8, 1.6), [3.4, 3.4, 0.9], "#f472b6"),
                         torus((8.8, 3.8, 2.4), 1.8, 0.45, "#fafafa")]
    items["mustache"] = [poly(mirror_x([(0, -2.2), (2.4, -3.2), (5, -2.4), (7.6, -2), (9.6, -4), (9.6, -0.8),
                                        (6.8, 0.6), (3.4, 0.2), (0, 0)]), BROWN_DARK, z=0.7, d=1, smooth=True)]
    items["beard"] = [poly(mirror_x([(0, 0.6), (5.4, -0.4), (9.4, -3.6), (11.4, -2.4), (10.6, 4.4), (6.6, 9.6),
                                     (0, 11.4)]), "#7a4a26", z=0.6, d=1.4, smooth=True),
                      poly(mirror_x([(0, -2), (3, -3), (6, -1.8), (6.6, 0.2), (3, 0), (0, 0.4)]), BROWN_DARK, z=1.6,
                           d=0.8, smooth=True)]
    items["straw"] = [tube([(0.4, 0, 0.6), (7.6, -2.4, 1.2), (12.6, -3.6, 1.4)], 0.8, "#eab308", smooth=True),
                      sphere((13.4, -3.8, 1.4), [1.7, 0.8, 0.8], "#ca8a04", rot=-12)]
    items["rose"] = [tube([(0.4, 0, 0.6), (10, 2.4, 1.2)], 0.9, "#16a34a"),
                     poly(leaf(5, 1.4, 4, 1.4, 40), "#22c55e", z=1, d=0.5, smooth=True),
                     sphere((11.4, 2.6, 1.4), 2.7, "#e11d48"), torus((11.4, 2.6, 3.8), 1.3, 0.42, "#9f1239")]
    items["bubble_gum"] = [sphere((0.4, 0.6, 3.2), [4.6, 4.6, 3.6], PINK), sphere((-1, -1, 6.4), [1, 1, 0.5], WHITE)]
    items["smile"] = [tube([(-3.2, -0.8, 0.4), (0, 1.4, 0.4), (3.2, -0.8, 0.4)], 1.2, INK, smooth=True)]
    items["tongue"] = [poly([(-1.6, 0), (1.6, 0), (1.8, 2.6), (0, 3.7), (-1.8, 2.6)], "#fb7185", z=0.3, d=0.6,
                            smooth=True),
                       tube([(-2.8, -0.4, 0.6), (0, 0.5, 0.6), (2.8, -0.4, 0.6)], 1.1, INK, smooth=True)]
    return items


def neck_items():
    items = {}
    bowtie = lambda fill, knot, s=1.0: [  # noqa: E731
        poly([(0, -1 * s), (5 * s, -3.2 * s), (5.8 * s, 0), (5 * s, 3.2 * s), (0, 1 * s), (-5 * s, 3.2 * s),
              (-5.8 * s, 0), (-5 * s, -3.2 * s)], fill, z=0.8, d=1.2),
        sphere((0, 0, 1.4), [1.4 * s, 1.6 * s, 0.8], knot)]
    items["bow_tie"] = bowtie("#dc2626", "#991b1b")
    items["necktie"] = [poly([(-1.7, -0.8), (1.7, -0.8), (1.1, 1.9), (-1.1, 1.9)], "#2563eb", z=0.8, d=1),
                        region([(-1.1, 1.8), (1.1, 1.8), (2.5, 9.2), (0, 11.4), (-2.5, 9.2)], "#2563eb", mode="front"),
                        region([(-60, 4.6), (60, 4.6), (60, 5.6), (-60, 5.6)], "#facc15", mode="front")]
    # The stripe above is clipped to the tie only in the flat symbol; keep it subtle.
    items["necktie"][2] = region([(-1.6, 4.6), (1.6, 4.6), (1.9, 5.8), (-1.9, 5.8)], "#facc15", mode="front")
    items["scarf"] = [band(-1.6, 2.6, RED),
                      poly([(3, 1.2), (6.6, 1.2), (7.2, 8.8), (3.6, 9)], "#d0362d", z=0.9, d=1.2),
                      tube([(3.4, 6, 1.6), (6.9, 5.8, 1.6)], 0.8, "#fafafa")]
    chain = quad_curve((-8.6, -2.2), (0, 7.6), (8.6, -2.2), 9)
    items["gold_chain"] = [tube([(x, y, 0.5) for x, y in chain], 0.9, GOLD, smooth=True),
                           sphere((0, 4.6, 0.9), [2.3, 2.3, 0.6], GOLD), sphere((0, 4.6, 1.4), [1.2, 1.2, 0.4], GOLD_DARK)]
    items["medal"] = [tube([(-3.2, -2.4, 0.5), (0, 2.6, 0.6)], 1.8, "#2563eb"),
                      tube([(3.2, -2.4, 0.5), (0, 2.6, 0.6)], 1.8, "#dc2626"),
                      sphere((0, 4.4, 1), [2.6, 2.6, 0.6], GOLD), poly(star(0, 4.4, 1.6, 0.7), GOLD_DARK, z=1.6, d=0.3)]
    items["bandana"] = [band(-1, 1.4, "#dc2626"), region([(-5.6, 0), (5.6, 0), (0, 7)], "#dc2626", mode="front"),
                        sphere((-2, 1.8, 0.8), 0.6, WHITE), sphere((2, 1.8, 0.8), 0.6, WHITE),
                        sphere((0, 4.4, 0.8), 0.6, WHITE)]
    lei_colors = ["#f472b6", "#facc15", "#fb923c", "#a78bfa"]
    items["lei"] = [sphere((x, y, 0.9), [1.7, 1.7, 1], lei_colors[i % 4])
                    for i, (x, y) in enumerate(quad_curve((-9.6, -2), (0, 8.4), (9.6, -2), 9))]
    items["pearls"] = [sphere((x, y, 0.7), 0.95, "#f4f1ea") for x, y in quad_curve((-8.8, -2.2), (0, 7), (8.8, -2.2), 13)]
    items["collar_bell"] = [band(-0.8, 1.2, "#dc2626"), sphere((0, 2.8, 1.2), 2, GOLD),
                            tube([(-0.9, 3.6, 3), (0.9, 3.6, 3)], 0.5, "#6b4b12")]
    return items


def outfit_items():
    items = {}
    jacket = lambda fill, top=-0.6: region([(-60, top), (60, top), (60, 60), (-60, 60)], fill)  # noqa: E731
    items["suit"] = [jacket("#1f2a44"), region([(-3.4, -0.7), (3.4, -0.7), (0, 7.2)], WHITE, mode="front"),
                     region([(-0.9, -0.2), (0.9, -0.2), (1.5, 5), (0, 6.4), (-1.5, 5)], "#dc2626", mode="front")]
    items["tuxedo"] = [jacket("#141418"), region([(-4, -0.7), (4, -0.7), (0, 8.2)], WHITE, mode="front"),
                       poly([(0, -0.5), (3, -1.8), (3.4, 0.6), (3, 1.9), (0, 0.6), (-3, 1.9), (-3.4, 0.6), (-3, -1.8)],
                            "#141418", z=0.9, d=0.8),
                       sphere((0, 3.4, 0.7), 0.5, INK), sphere((0, 5.2, 0.7), 0.5, INK)]
    items["lab_coat"] = [jacket("#f1f2f4"), region([(-3, -0.7), (3, -0.7), (0, 5.8)], "#93c5fd", mode="front"),
                         region([(4.2, 3), (8, 3), (8, 6.4), (4.2, 6.4)], "#dcdee3", mode="front"),
                         tube([(5.2, 2, 0.6), (5.2, 4.4, 0.6)], 0.8, BLUE)]
    items["hoodie"] = [jacket("#6b7280"), region([(-6, 4), (6, 4), (7.6, 9.4), (-7.6, 9.4)], "#4b5563", mode="front"),
                       *both_sides([tube([(0, -0.4, 0.5), (-0.2, 3.6, 0.5)], 0.7, "#f4f4f5"),
                                    sphere((-0.2, 4, 0.7), 0.6, "#f4f4f5")], 1.8)]
    items["jersey"] = [jacket("#dc2626"), band(3, 4.8, "#fafafa", mode="front"),
                       region([(-2.6, -0.7), (2.6, -0.7), (0, 2.6)], "#fafafa", mode="front"),
                       poly(star(0, 8, 2.2, 1), "#fafafa", z=0.4, d=0.4)]
    items["overalls"] = [region([(-60, 3.4), (60, 3.4), (60, 60), (-60, 60)], "#3b5b9a"),
                         region([(-5, 0.8), (5, 0.8), (5, 3.6), (-5, 3.6)], "#3b5b9a", mode="front"),
                         *both_sides([region([(-1, -1.2), (1, -1.2), (1, 1), (-1, 1)], "#3b5b9a", mode="front"),
                                      sphere((0, 1.4, 0.8), 0.75, GOLD)], 5),
                         region([(-2.2, 4.6), (2.2, 4.6), (2.2, 7), (-2.2, 7)], "#2f4a80", mode="front")]
    flower = lambda x, y: [sphere((x + 1.1 * math.sin(math.radians(a)), y - 1.1 * math.cos(math.radians(a)), 0.6),  # noqa: E731
                                  [0.9, 0.9, 0.4], PINK) for a in range(0, 360, 72)] + [
        sphere((x, y, 0.9), 0.55, "#fde047")]
    items["hawaiian_shirt"] = [jacket("#14b8a6"), region([(-3.6, -0.7), (3.6, -0.7), (0, 5)], WHITE, mode="front"),
                               *flower(-5.6, 3.2), *flower(5.4, 4.6), *flower(-1.2, 7.4)]
    items["knight_armor"] = [jacket(STEEL), region([(-6, 0.4), (6, 0.4), (5, 9), (0, 11.4), (-5, 9)], "#cbd5e1",
                                                   mode="front"),
                             region([(-0.6, 0.4), (0.6, 0.4), (0.6, 11), (-0.6, 11)], "#94a3b8", mode="front"),
                             sphere((-4.2, 1.6, 0.7), 0.6, "#64748b"), sphere((4.2, 1.6, 0.7), 0.6, "#64748b")]
    items["hero_suit"] = [jacket("#2563eb"), region([(0, 0.6), (4.6, 4.4), (0, 8.8), (-4.6, 4.4)], "#facc15",
                                                    mode="front"),
                          region([(0, 2.4), (2.4, 4.4), (0, 6.6), (-2.4, 4.4)], "#dc2626", mode="front")]
    return items


def back_items():
    items = {}
    angel = poly([(0, 0), (4, -6), (10, -10), (16.4, -11), (15, -6.4), (17.4, -4.2), (14, -1.2), (15.4, 2.2),
                  (10, 3.2), (6, 4.6)], "#f8fafc", z=-1, d=1.6, smooth=True)
    items["angel_wings"] = both_sides([angel], 8.6, -2)
    bat = poly([(0, 0), (4, -7), (10, -11), (18.4, -9.4), (15.6, -5.4), (17.4, -1.6), (13.4, -2.8), (12.4, 1.8),
                (8.4, -0.4), (5, 3.2)], "#4c1d95", z=-1, d=1.4)
    items["bat_wings"] = both_sides([bat], 8.6, -2)
    items["cape"] = [poly([(-12, -11), (12, -11), (17.6, 6), (21, 21.4), (10, 19.6), (0, 21.8), (-10, 19.6), (-21, 21.4),
                           (-17.6, 6)], "#b91c1c", z=-1.2, d=1.2, smooth=True)]
    jet = [cyl((0, 1, -4), 3.8, 3.8, 24, "#9ca3af"), dome((0, -11, -4), [3.8, 2.8, 3.8], "#b8c0c8"),
           cyl((0, 13.8, -4), 2.3, 3, 2.6, "#52525b"),
           cyl((0, 19.6, -4), 0, 2.7, 9, "#fb923c", glow=True), cyl((0, 18.2, -3.4), 0, 1.5, 5, "#fde047", glow=True)]
    items["jetpack"] = both_sides(jet, 9.6)
    upper = poly([(0, 0), (5, -8.4), (12.4, -11.6), (15.6, -6.4), (10.6, -0.8)], "#f59e0b", z=-1, d=1, smooth=True)
    lower = poly([(0, 1), (9.4, 2.4), (11.4, 7.6), (5, 8.6)], "#fb923c", z=-1.1, d=1, smooth=True)
    dots = [sphere((10.6, -6.6, -0.2), [1.4, 1.4, 0.4], "#fff7ed"), sphere((8, 5, -0.4), [1, 1, 0.4], "#7c2d12")]
    items["butterfly_wings"] = both_sides([upper, lower, *dots], 7.6, -1)
    items["devil_tail"] = [tube([(4, 13, -1), (12, 17, -1), (19.4, 12.4, -1), (19, 5.4, -1)], 2, "#d42f2f",
                                smooth=True),
                           poly([(19, 0.6), (22.4, 6.2), (19, 5), (15.6, 6.2)], "#d42f2f", z=-1, d=1.2)]
    return items


def held_items():
    items = {}
    items["coffee"] = [cyl((3.2, 0, 0), 2.6, 3.1, 6, "#fafaf9"), cyl((3.2, 0.3, 0), 2.76, 2.96, 2.6, "#a8692e"),
                       cyl((3.2, -3.4, 0), 3.3, 3.2, 1, "#e7e5e4"),
                       tube([(3, -4.6, 0), (4.4, -6.8, 0), (3, -9, 0), (4.4, -11, 0)], 0.8, "#d4d4d8", smooth=True)]
    items["wrench"] = place([tube([(0, 4, 0), (0, -5, 0)], 2, "#9ca3af"),
                             poly([(-2.2, -4.4), (-2.6, -8), (-1, -9.6), (-0.6, -7), (0.6, -7), (1, -9.6), (2.6, -8),
                                   (2.2, -4.4)], "#9ca3af", z=0, d=1.6)], 4, 0, 30)
    sparkle = [poly(star(9.2, -8.6, 2.1, 0.8, 4), GOLD, z=0.4, d=0.4, glow=True),
               poly(star(11.6, -5, 1.3, 0.5, 4), GOLD, z=0.4, d=0.4, glow=True),
               poly(star(7.4, -11.4, 1.1, 0.45, 4), GOLD, z=0.4, d=0.4, glow=True)]
    items["magic_wand"] = [tube([(0.6, 3, 0), (6.4, -5.4, 0)], 1.2, "#18181b"),
                           tube([(5.7, -4.4, 0), (6.4, -5.4, 0)], 1.25, WHITE), *sparkle]
    items["sword"] = place([poly([(-1, 1), (1, 1), (1, -11.4), (0, -14), (-1, -11.4)], "#e5e7eb", z=0, d=0.8),
                            box((0, 1.7, 0), (7, 1.4, 1.6), GOLD, 0.6), tube([(0, 2.6, 0), (0, 5.6, 0)], 1.4, BROWN),
                            sphere((0, 6.2, 0), 1.1, GOLD)], 4, 0, 18)
    items["laser_sword"] = place([tube([(0, 6, 0), (0, 1.8, 0)], 1.9, "#52525b"),
                                  tube([(0, 2.6, 0), (0, 3.2, 0)], 2.1, "#27272a"),
                                  tube([(0, 1.2, 0), (0, -14, 0)], 1.9, CYAN, glow=True),
                                  tube([(0, 1.2, 0.3), (0, -13.6, 0.3)], 0.8, "#ecfeff", glow=True)], 4, 0, 15)
    items["balloon"] = [tube([(1, 2, 0), (2.4, -3, 0), (1.6, -7.6, 0), (3.2, -11.4, 0)], 0.45, "#71717a", smooth=True),
                        sphere((3.4, -11.6, 0), [0.9, 0.7, 0.9], RED),
                        sphere((3.6, -17, 0), [4.6, 5.4, 4.6], RED), sphere((2, -19, 3.6), [1, 1.5, 0.6], "#fecaca")]
    items["flag"] = [tube([(1, 4, 0), (1, -14, 0)], 0.9, "#a1a1aa"), sphere((1, -14.6, 0), 1, GOLD),
                     poly([(1.4, -13.6), (5, -14.6), (8.6, -13), (11.8, -14), (11.8, -7.4), (8.6, -6.4), (5, -8),
                           (1.4, -7)], "body", z=0, d=0.6, smooth=True)]
    items["book"] = [box((4, 0, 0), (6.2, 7.8, 2.4), "#2563eb", 0.5), box((6.9, 0, 0), (0.7, 7, 1.9), "#fafaf9", 0.1),
                     box((3.7, -1.8, 1.25), (3.4, 0.7, 0.1), GOLD, 0.2)]
    items["briefcase"] = [box((4, 1, 0), (9, 6.6, 2.6), "#7a4a26", 0.9), torus((4, -2.6, 0), 1.8, 0.5, "#3b2617"),
                          box((4, -1.2, 1.35), (1.6, 1, 0.3), GOLD, 0.2)]
    items["trophy"] = [cyl((4, -4.4, 0), 1.6, 3.8, 5.6, GOLD), torus((0.8, -4.8, 0), 1.6, 0.5, GOLD),
                       torus((7.2, -4.8, 0), 1.6, 0.5, GOLD), cyl((4, -0.4, 0), 0.8, 0.8, 2.6, GOLD_DARK),
                       box((4, 2, 0), (5.6, 2, 2.6), "#5b3a1e", 0.4), poly(star(4, -4.4, 1.3, 0.55), "#fff7d6", z=2.1, d=0.3)]
    items["rubber_duck"] = [sphere((4.4, 0, 0), [4, 3, 3.2], "#facc15"), sphere((2.6, -3.6, 0), 2.4, "#facc15"),
                            sphere((0.2, -3.4, 0.4), [1.4, 0.7, 1], "#fb923c"), sphere((2, -4.2, 2), 0.5, INK)]
    items["pizza"] = place([poly([(-4.4, -4.4), (4.4, -4.4), (0, 6)], "#fcd34d", z=0, d=1),
                            tube([(-4.6, -4.8, 0), (4.6, -4.8, 0)], 2, "#b45309"),
                            sphere((-1.4, -2, 0.6), [1.1, 1.1, 0.3], "#dc2626"),
                            sphere((1.6, -1.4, 0.6), [1.1, 1.1, 0.3], "#dc2626"),
                            sphere((0, 1.6, 0.6), [1, 1, 0.3], "#dc2626")], 5, -1, -15)
    items["microphone"] = place([tube([(0, 6, 0), (0, -1, 0)], 1.8, "#27272a"),
                                 cyl((0, -1.2, 0), 1.4, 1.8, 1, "#3f3f46"), sphere((0, -3.6, 0), 2.6, "#a1a1aa")],
                                4, 0, 12)
    items["ice_cream"] = [cyl((4, 2.4, 0), 0, 2.8, 7, "#d6a15c"), sphere((4, -2, 0), 3, PINK),
                          sphere((4, -5.4, 0), 2.6, "#86efac"), sphere((4, -8.4, 0.4), 1, "#dc2626")]
    return items


# --- bounds -------------------------------------------------------------------

def outline_2d(part):
    """Approximate the part's front projection as points, for the symbol viewBox."""
    t = part["t"]
    if t == "region":
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
            "anchors": {**anchors, "face": [20, eye, face_k], "mouth": [20, eye + MOUTH_DROP * face_k, round(face_k * MOUTH_BOOST, 3)],
                        "held": [*anchors["held"][:2], round(anchors["held"][2] * HELD_BOOST, 3)],
                        "outfit": anchors["neck"]},
        }
    groups = {"head": head_items(), "face": face_items(), "mouth": mouth_items(), "neck": neck_items(),
              "outfit": outfit_items(), "back": back_items(), "held": held_items()}
    items = []
    for slot in ["head", "face", "mouth", "neck", "outfit", "back", "held"]:
        for item_id, parts in groups[slot].items():
            items.append({"id": item_id, "slot": slot, "bounds": bounds(parts),
                          "regions": any(p["t"] == "region" for p in parts), "parts": parts})
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)), "accessory ids must be unique"
    catalog = {"schema": 1, "slots": SLOTS, "slotDepth": SLOT_DEPTH, "frontDepthM": 0.25, "shapes": shapes,
               "items": items}
    OUTPUT.write_text(json.dumps(rounded(catalog), separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(items)} accessories -> {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
