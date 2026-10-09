"""Render every Personal Jarvis app icon from the vector logo.

    python scripts/make_app_icon.py

The single source is ``assets/brand/jarvis-logo.svg``: the hooded figure as one
even-odd path in a ``0 0 256 256`` viewBox. Everything else is generated from
it — the taskbar/dock tile at every ICO size, the 1024 px master the macOS
``.icns`` is built from, the DEV-badged copy, the free-standing white mark for
dark surfaces, and ``src/components/brand/jarvisLogoPath.ts`` that the
frontend draws the same figure with. ``tests/test_app_icon.py`` fails when the
TypeScript copy drifts from the SVG.

The tile is the logo as it was drawn: black ink on white paper, cut to a
superellipse (the shape platforms use for their own icons). A faint dark
hairline inside the edge keeps the tile's outline on a light taskbar.

Sizes are rendered, not downsampled from one bitmap, and the figure grows to
fill more of the tile as the tile shrinks — at 16 px it is the silhouette, not
the margin, that a person recognises.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LOGO_SVG = ROOT / "assets" / "brand" / "jarvis-logo.svg"
FRONTEND = ROOT / "jarvis" / "ui" / "web" / "frontend"
LOGO_TS = FRONTEND / "src" / "components" / "brand" / "jarvisLogoPath.ts"
VIEWBOX = 256.0

SQUIRCLE_N = 5.0  # superellipse exponent; 5 is the shape platforms settled on
SUPERSAMPLE = 4
PAPER = (255, 255, 255)
INK = (10, 10, 10)
DEV_AMBER = (241, 180, 103)  # the interface's "degraded" amber (#F1B467)
DEV_INK = (20, 20, 20)
#: The figure's box as a fraction of the tile — bigger where the tile is smaller.
SPAN_LARGE = 0.72
SPAN_SMALL = 0.88
SPAN_FULL_AT = 64
SPAN_SMALL_AT = 16
HAIRLINE_ALPHA = 0.14
HAIRLINE_WIDTH = 0.007

MASTER = 1024
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def logo_path_data(svg: Path = LOGO_SVG) -> str:
    """The ``d`` attribute of the logo's single path."""
    match = re.search(r'<path[^>]*\sd="([^"]+)"', svg.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"{svg} has no <path d=...>")
    return match.group(1)


def logo_polygons(d: str) -> list[list[tuple[float, float]]]:
    """Split an ``M … L … Z`` path into closed polygons in viewBox units."""
    polygons = []
    for sub in d.split("Z"):
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", sub)]
        if len(numbers) >= 6:
            polygons.append(list(zip(numbers[0::2], numbers[1::2], strict=True)))
    return polygons


def figure_mask(size: int, span: float) -> Image.Image:
    """The figure as an antialiased ``L`` mask, centred in a ``size`` square.

    The path's subpaths are filled even-odd: each one is XOR-ed into the mask,
    so a hole (an eye, a fold of the cloak) cuts the shape it sits in.
    """
    big = size * SUPERSAMPLE
    scale = big * span / VIEWBOX
    offset = (big - VIEWBOX * scale) / 2.0
    acc = np.zeros((big, big), dtype=bool)
    for polygon in logo_polygons(logo_path_data()):
        layer = Image.new("1", (big, big), 0)
        points = [(x * scale + offset, y * scale + offset) for x, y in polygon]
        ImageDraw.Draw(layer).polygon(points, fill=1)
        acc ^= np.asarray(layer, dtype=bool)
    mask = Image.fromarray((acc * 255).astype(np.uint8), mode="L")
    return mask.resize((size, size), Image.Resampling.LANCZOS)


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


def mark_span(size: int) -> float:
    """Interpolate the figure size, so no two neighbouring sizes jump."""
    t = min(1.0, max(0.0, (size - SPAN_SMALL_AT) / (SPAN_FULL_AT - SPAN_SMALL_AT)))
    return SPAN_SMALL + (SPAN_LARGE - SPAN_SMALL) * t


def _hairline(size: int) -> Image.Image:
    """A ring just inside the tile edge, so the white tile survives a light taskbar."""
    inset = max(1, round(size * HAIRLINE_WIDTH))
    inner = Image.new("L", (size, size), 0)
    inner.paste(squircle_mask(size - 2 * inset), (inset, inset))
    ring = ImageChops.subtract(squircle_mask(size), inner)
    line = Image.new("RGBA", (size, size), (*INK, 255))
    line.putalpha(ring.point(lambda v: int(v * HAIRLINE_ALPHA)))
    return line


def render_tile(size: int) -> Image.Image:
    """Render one icon size from the vector — never by resampling a bigger one."""
    tile = Image.new("RGBA", (size, size), (*PAPER, 255))
    tile.alpha_composite(_hairline(size))
    ink = Image.new("RGBA", (size, size), (*INK, 255))
    ink.putalpha(figure_mask(size, mark_span(size)))
    tile.alpha_composite(ink)
    tile.putalpha(squircle_mask(size))
    return tile


def _dev_ribbon(size: int) -> Image.Image:
    """The amber DEV label the dev instance wears in its lower-right corner.

    Below 32 px the word cannot be read, so the label collapses to a plain
    amber block that still says "this is the other one" at a glance.
    """
    ribbon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ribbon)
    width = size * 0.5
    height = size * 0.2
    margin = size * 0.05
    x1 = size - margin
    y1 = size - margin
    x0 = x1 - width
    y0 = y1 - height
    draw.rounded_rectangle((x0, y0, x1, y1), radius=height * 0.28, fill=(*DEV_AMBER, 255))
    if size >= 32:
        font = ImageFont.load_default(size=int(height * 0.78))
        left, top, right, bottom = draw.textbbox((0, 0), "DEV", font=font)
        tx = x0 + (width - (right - left)) / 2 - left
        ty = y0 + (height - (bottom - top)) / 2 - top
        draw.text((tx, ty), "DEV", font=font, fill=(*DEV_INK, 255))
    return ribbon


def render_dev_tile(size: int) -> Image.Image:
    """The dev instance's icon: the same tile, wearing the DEV ribbon."""
    tile = render_tile(size)
    tile.alpha_composite(_dev_ribbon(size))
    return tile


def render_mark(size: int) -> Image.Image:
    """The free-standing figure in white, for dark surfaces with their own frame."""
    mark = Image.new("RGBA", (size, size), (*PAPER, 255))
    mark.putalpha(figure_mask(size, 1.0))
    return mark


def write_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def write_ico(path: Path, members: list[Image.Image]) -> None:
    """Write every ICO member from its own render, largest first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    largest, *rest = members
    largest.save(path, format="ICO", sizes=[m.size for m in members], append_images=rest)


def logo_ts_source(d: str) -> str:
    return (
        "// Generated from assets/brand/jarvis-logo.svg by scripts/make_app_icon.py.\n"
        '// The Personal Jarvis mark in a 0 0 256 256 viewBox; fill it with fill-rule="evenodd".\n'
        f'export const JARVIS_LOGO_PATH =\n  "{d}";\n'
    )


def main() -> None:
    LOGO_TS.write_text(logo_ts_source(logo_path_data()), encoding="utf-8", newline="\n")

    tile_256 = render_tile(256)
    members = [render_tile(s) for s in sorted(ICO_SIZES, reverse=True)]
    public = FRONTEND / "public"

    write_png(ROOT / "assets" / "icons" / "jarvis-1024.png", render_tile(MASTER))
    for path in (
        ROOT / "jarvis" / "assets" / "icons" / "jarvis.png",
        ROOT / "assets" / "icons" / "jarvis-gigi-256.png",
        public / "jarvis-gigi-256.png",
        public / "jarvis-logo.png",
        FRONTEND / "src" / "assets" / "jarvis-mark.png",
        ROOT / "video" / "public" / "jarvis-gigi.png",
        ROOT / "video" / "public" / "jarvis-mark.png",
        ROOT / "videos" / "agent-mode-launch" / "assets" / "gigi.png",
    ):
        write_png(path, tile_256)
    write_png(public / "jarvis-mark-256.png", render_mark(256))
    for path in (
        ROOT / "jarvis" / "assets" / "icons" / "jarvis.ico",
        ROOT / "assets" / "icons" / "jarvis.ico",
        public / "jarvis.ico",
        public / "jarvis-gigi.ico",
    ):
        write_ico(path, members)
    # The dev instance (``--instance dev``) shows its own DEV-badged copy on the
    # taskbar and in its shortcut, rendered from the same vector.
    dev_members = [render_dev_tile(s) for s in sorted(ICO_SIZES, reverse=True)]
    dev_png = render_dev_tile(256)
    write_png(ROOT / "assets" / "icons" / "jarvis-dev.png", dev_png)
    write_png(ROOT / "jarvis" / "assets" / "icons" / "jarvis-dev.png", dev_png)
    for path in (
        ROOT / "jarvis" / "assets" / "icons" / "jarvis-dev.ico",
        ROOT / "assets" / "icons" / "jarvis-dev.ico",
    ):
        write_ico(path, dev_members)
    print(f"wrote the app icon at {MASTER} px, 256 px, {len(ICO_SIZES)} ICO members, and DEV")


if __name__ == "__main__":
    main()
