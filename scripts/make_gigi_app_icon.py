"""Render the desktop app icon: the Gigi mark in paper on an ink squircle.

    python scripts/make_gigi_app_icon.py

The icon is drawn from vector geometry, not resampled from a raster. The body
path, the eyes and the mouth are copied verbatim from the canonical mascot in
``jarvis/ui/web/frontend/src/components/MascotGigi.tsx`` (viewBox ``0 0 256
256``); ``tests/test_app_icon.py`` fails if the two drift apart.

What the icon deliberately drops from the live mascot: scanlines, glitch
pixels, chromatic slices and arms. Those are motion decoration — below 48 px
they turn to mush, and above it they read as noise. An app icon carries a
shape, not an illustration. What is left is what Grok Bot and Hermes also show:
one silhouette, two eyes, a mouth.

Three details do the heavy lifting, and all three exist because a near-black
tile has to survive a near-black taskbar:

* a superellipse (n=5) rather than a rounded rectangle — the tile shape every
  platform's own icons use;
* a vertical ink gradient, so the tile reads as a body instead of a hole;
* a hairline of white just inside the edge, which draws the outline even when
  the background matches the fill.

Small sizes are rendered, not downsampled, and they are not the same drawing
shrunk. At 16 px on a dark taskbar the ink tile is nearly invisible, so the
paper ghost has to carry the icon on its own: it grows from 74 % of the tile at
64 px and up to 86 % at 16 px, drops the paper gradient that would only grey out
its lower half, and takes a stronger hairline.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# ── Geometry, mirrored from MascotGigi.tsx (viewBox 0 0 256 256) ──────────────
#: Body outline: a domed head over straight flanks, closed by the zigzag skirt.
BODY_DOME = ((58, 90), (58, 36), (128, 36), (198, 36), (198, 90))
BODY_SKIRT = (
    (198, 208),
    (180, 186),
    (160, 208),
    (140, 186),
    (120, 208),
    (100, 186),
    (80, 208),
    (58, 186),
)
#: Eyes and mouth as (cx, cy, rx, ry) — punched out of the body, never drawn on.
EYES = ((102, 108, 10, 14), (154, 108, 10, 14))
MOUTH = (128, 146, 7, 10)

# ── Treatment ────────────────────────────────────────────────────────────────
SQUIRCLE_N = 5.0  # superellipse exponent; 5 is the shape platforms settled on
SUPERSAMPLE = 4
#: The mark's bounding box as a fraction of the tile. Small sizes carry a
#: larger mark: at 16 px an ink tile on a dark taskbar is nearly invisible,
#: so the paper ghost has to be the thing that reads, not the frame.
MARK_SPAN_LARGE = 0.74
MARK_SPAN_SMALL = 0.86
MARK_SPAN_FULL_AT = 64  # at and above this the large span applies
MARK_SPAN_SMALL_AT = 16
MARK_RISE = 0.012  # optical centring: the zigzag skirt needs air below it
INK_TOP = (30, 30, 30)
INK_BOTTOM = (7, 7, 7)
PAPER_TOP = (255, 255, 255)
PAPER_BOTTOM = (206, 205, 201)
PAPER_FLAT = (250, 249, 246)
HAIRLINE_ALPHA = 0.11
HAIRLINE_ALPHA_SMALL = 0.16
HAIRLINE_WIDTH = 0.007
#: Below this, gradients cost more contrast than the volume they buy.
FLAT_BELOW = 40

MASTER = 1024
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _quadratic(p0, p1, p2, steps: int = 64):
    """Sample a quadratic Bezier — the ``Q`` segments of the body path."""
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        yield (
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        )


def body_polygon() -> list[tuple[float, float]]:
    """Flatten the body path into a polygon in the 256-unit mascot space."""
    points: list[tuple[float, float]] = [(58.0, 90.0)]
    points += list(_quadratic((58, 90), (58, 36), (128, 36)))[1:]
    points += list(_quadratic((128, 36), (198, 36), (198, 90)))[1:]
    points += [(float(x), float(y)) for x, y in BODY_SKIRT]
    return points


def squircle_mask(size: int) -> Image.Image:
    """A superellipse the width of ``size``, antialiased by supersampling."""
    big = size * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    radius = big / 2.0
    exponent = 2.0 / SQUIRCLE_N
    points = []
    for i in range(720):
        theta = 2.0 * math.pi * i / 720
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        x = radius * (abs(cos_t) ** exponent) * (1 if cos_t >= 0 else -1)
        y = radius * (abs(sin_t) ** exponent) * (1 if sin_t >= 0 else -1)
        points.append((radius + x, radius + y))
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def _vertical_gradient(size: int, top, bottom) -> Image.Image:
    column = Image.new("RGB", (1, size))
    draw = ImageDraw.Draw(column)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.point((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return column.resize((size, size), Image.Resampling.BILINEAR)


def _diagonal_gradient(width: int, height: int, top, bottom) -> Image.Image:
    """Light from the upper left, the way every platform lights its icons."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            t = min(1.0, x / max(1, width) * 0.42 + y / max(1, height) * 0.58)
            pixels[x, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return image


def mark_span(size: int) -> float:
    """Interpolate the mark size, so no two neighbouring sizes jump."""
    lo, hi = MARK_SPAN_SMALL_AT, MARK_SPAN_FULL_AT
    t = min(1.0, max(0.0, (size - lo) / (hi - lo)))
    return MARK_SPAN_SMALL + (MARK_SPAN_LARGE - MARK_SPAN_SMALL) * t


def mark_mask(size: int, span: float | None = None) -> Image.Image:
    """The Gigi silhouette with eyes and mouth punched out, sized for a tile."""
    big = size * SUPERSAMPLE
    polygon = body_polygon()
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    box_w, box_h = max(xs) - min(xs), max(ys) - min(ys)
    scale = (big * (mark_span(size) if span is None else span)) / max(box_w, box_h)
    off_x, off_y = -min(xs) * scale, -min(ys) * scale

    def place(x: float, y: float) -> tuple[float, float]:
        return (x * scale + off_x, y * scale + off_y)

    width, height = round(box_w * scale), round(box_h * scale)
    mask = Image.new("L", (width + 2, height + 2), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([place(x, y) for x, y in polygon], fill=255)
    for cx, cy, rx, ry in (*EYES, MOUTH):
        draw.ellipse((*place(cx - rx, cy - ry), *place(cx + rx, cy + ry)), fill=0)
    return mask.resize(
        (max(1, width // SUPERSAMPLE), max(1, height // SUPERSAMPLE)),
        Image.Resampling.LANCZOS,
    )


def _hairline(size: int, alpha: float) -> Image.Image:
    """A ring just inside the tile edge, so the shape survives a dark taskbar."""
    inset = max(1, round(size * HAIRLINE_WIDTH))
    inner = Image.new("L", (size, size), 0)
    inner.paste(squircle_mask(size - 2 * inset), (inset, inset))
    ring = ImageChops.subtract(squircle_mask(size), inner)
    line = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    line.putalpha(ring.point(lambda v: int(v * alpha)))
    return line


def render_tile(size: int) -> Image.Image:
    """Render one icon size from scratch — never by resampling a bigger one."""
    flat = size < FLAT_BELOW
    tile = _vertical_gradient(size, INK_TOP, INK_BOTTOM).convert("RGBA")
    tile.alpha_composite(_hairline(size, HAIRLINE_ALPHA_SMALL if flat else HAIRLINE_ALPHA))

    mask = mark_mask(size)
    if flat:
        paper = Image.new("RGB", mask.size, PAPER_FLAT)
    else:
        paper = _diagonal_gradient(*mask.size, PAPER_TOP, PAPER_BOTTOM)
    mark = Image.merge("RGBA", (*paper.split(), mask))
    tile.alpha_composite(
        mark,
        ((size - mask.width) // 2, (size - mask.height) // 2 - round(size * MARK_RISE)),
    )
    tile.putalpha(squircle_mask(size))
    return tile


def render_mark(size: int) -> Image.Image:
    """The free-standing mark: paper Gigi on transparency, no tile.

    Used where the surface already provides the frame — the share card's ring,
    for one. With no tile there is no edge to keep clear of, so it fills the
    canvas instead of sitting inside a tile's margin.
    """
    mask = mark_mask(size, span=1.0)
    paper = _diagonal_gradient(*mask.size, PAPER_TOP, PAPER_BOTTOM)
    mark = Image.merge("RGBA", (*paper.split(), mask))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((size - mask.width) // 2, (size - mask.height) // 2))
    return canvas


def write_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def write_ico(path: Path, members: list[Image.Image]) -> None:
    """Write every ICO member from its own render, largest first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    largest, *rest = members
    largest.save(path, format="ICO", sizes=[m.size for m in members], append_images=rest)


def main() -> None:
    master = render_tile(MASTER)
    tile_256 = render_tile(256)
    members = [render_tile(s) for s in sorted(ICO_SIZES, reverse=True)]

    write_png(ROOT / "assets" / "icons" / "jarvis-1024.png", master)
    for path in (
        ROOT / "jarvis" / "assets" / "icons" / "jarvis.png",
        ROOT / "assets" / "icons" / "jarvis-gigi-256.png",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-gigi-256.png",
    ):
        write_png(path, tile_256)
    write_png(
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-mark-256.png",
        render_mark(256),
    )
    for path in (
        ROOT / "jarvis" / "assets" / "icons" / "jarvis.ico",
        ROOT / "assets" / "icons" / "jarvis.ico",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis.ico",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-gigi.ico",
    ):
        write_ico(path, members)
    print(f"wrote the app icon at {MASTER} px, 256 px, and {len(ICO_SIZES)} ICO members")


if __name__ == "__main__":
    main()
