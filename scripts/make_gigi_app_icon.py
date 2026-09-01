"""Render the desktop app icon: Gigi standing in a pool of light.

    python scripts/make_gigi_app_icon.py

The icon is drawn from vector geometry, not resampled from a raster. Every
shape — the body path, the trim outline, eyes, mouth, scanlines, cheek slices,
glitch pixels and arms — is copied from the canonical mascot in
``jarvis/ui/web/frontend/src/components/MascotGigi.tsx`` (viewBox ``0 0 256
256``); ``tests/test_app_icon.py`` fails if the two drift apart.

**The lighting is the whole design.** Gigi's white accents are what make him
alive, and a white tile is exactly the background that hides them — the trim
outline, the arms and the glitch pixels all vanish into it, leaving a flat
black blob. Turning the tile black is not the fix either: then his body
disappears instead. What works is a value sandwich, darkest in the middle:

* the tile is a pool of light — bright behind him, falling away to near-black
  at the corners;
* his body is a solid ink darker than any part of that pool, so he reads as a
  silhouette standing *in* the light rather than a drawing pasted onto it;
* his trim, eyes and mouth are paper white with a soft glow, and the glow has
  somewhere dark to land.

Two more details, both because a dark tile meets a dark taskbar: the tile is a
superellipse, the shape platforms use for their own icons, and a hairline of
white sits just inside the edge to draw the outline when fill and background
match.

Sizes are rendered, not downsampled, and they are not the same drawing shrunk.
Below 48 px the scanlines, cheeks, glitch pixels and arms are dropped — they
are under a pixel there and only muddy the face — and Gigi grows to fill more
of the tile, because at 16 px it is the ghost, not the frame, that a person
recognises.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

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
#: Eyes, pupils, mouth as (cx, cy, rx, ry); sparkles as (cx, cy, r).
EYES = ((102, 108, 10, 14), (154, 108, 10, 14))
PUPILS = ((104, 112, 4, 6), (156, 112, 4, 6))
SPARKLES = ((106, 105, 2), (158, 105, 2))
MOUTH = (128, 146, 7, 10)
MOUTH_INNER = (128, 146, 3, 5)
#: Body decoration as (x, y, w, h[, opacity]) — the pixel-ghost character.
SCANLINES = ((58, 132, 140, 2.4, 0.55), (58, 160, 140, 1.4, 0.30))
CHEEKS = ((64, 118, 18, 10), (170, 118, 18, 10))
GLITCH_RIGHT = (
    (200, 104, 6, 6),
    (208, 128, 4, 4),
    (202, 146, 9, 3),
    (197, 168, 3, 5),
    (206, 176, 5, 3),
)
GLITCH_LEFT = ((44, 96, 6, 4), (48, 124, 4, 6), (40, 148, 8, 3), (50, 170, 3, 5))
#: Arms, as the control points of one quadratic each.
ARM_LEFT = ((58, 140), (40, 148), (42, 162))
ARM_RIGHT = ((198, 140), (216, 148), (214, 162))
#: Horizontal extent of the drawing including arms and glitch pixels.
DRAWING_X = (40.0, 216.0)

# ── Treatment ────────────────────────────────────────────────────────────────
SQUIRCLE_N = 5.0  # superellipse exponent; 5 is the shape platforms settled on
SUPERSAMPLE = 4
#: The pool of light: bright at its centre, near-black by the corners.
POOL_INNER = (122, 122, 122)
POOL_OUTER = (11, 11, 11)
POOL_CENTRE = (0.5, 0.44)
POOL_RADIUS = 0.66
#: Ink darker than every part of the pool, so he is always the darkest thing.
BODY_INK = (10, 10, 10)
FACE_INK = (6, 6, 6)  # pupils and the mouth's core, a touch deeper still
PAPER = (255, 255, 255)
DEV_AMBER = (241, 180, 103)  # the interface's "degraded" amber (#F1B467)
DEV_INK = (20, 20, 20)
#: The mark's box as a fraction of the tile — bigger where the tile is smaller.
SPAN_LARGE = 0.62
SPAN_SMALL = 0.76
SPAN_FULL_AT = 64
SPAN_SMALL_AT = 16
#: Below this, scanlines/cheeks/glitch/arms are under a pixel and only muddy it.
DETAIL_FROM = 48
HAIRLINE_ALPHA = 0.12
HAIRLINE_WIDTH = 0.007
TRIM_WIDTH = 2.6  # stroke width in mascot units
ARM_WIDTH = 5.5

MASTER = 1024
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _quadratic(p0, p1, p2, steps: int = 64):
    """Sample a quadratic Bezier — the ``Q`` segments of the body and arms."""
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


def pool_of_light(size: int) -> Image.Image:
    """A radial field: lit where Gigi stands, falling away at the corners."""
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    cx, cy = POOL_CENTRE
    for y in range(size):
        dy = (y / size - cy) / POOL_RADIUS
        for x in range(size):
            dx = (x / size - cx) / POOL_RADIUS
            t = min(1.0, math.hypot(dx, dy))
            t = t * t * (3.0 - 2.0 * t)  # smoothstep, so the falloff has no seam
            pixels[x, y] = tuple(
                round(POOL_INNER[i] + (POOL_OUTER[i] - POOL_INNER[i]) * t) for i in range(3)
            )
    return image


def mark_span(size: int) -> float:
    """Interpolate the mark size, so no two neighbouring sizes jump."""
    t = min(1.0, max(0.0, (size - SPAN_SMALL_AT) / (SPAN_FULL_AT - SPAN_SMALL_AT)))
    return SPAN_SMALL + (SPAN_LARGE - SPAN_SMALL) * t


class _Mascot:
    """Maps mascot units onto a supersampled canvas and draws Gigi there."""

    def __init__(self, size: int, span: float, detail: bool) -> None:
        ys = [p[1] for p in body_polygon()]
        self.x0, self.x1 = DRAWING_X
        self.y0, self.y1 = min(ys), max(ys)
        self.scale = (size * SUPERSAMPLE * span) / max(self.x1 - self.x0, self.y1 - self.y0)
        self.width = round((self.x1 - self.x0) * self.scale) + 4
        self.height = round((self.y1 - self.y0) * self.scale) + 4
        self.detail = detail

    def at(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.x0) * self.scale, (y - self.y0) * self.scale)

    def _oval(self, cx: float, cy: float, rx: float, ry: float) -> tuple[float, ...]:
        return (*self.at(cx - rx, cy - ry), *self.at(cx + rx, cy + ry))

    def _rect(self, x: float, y: float, w: float, h: float) -> tuple[float, ...]:
        return (*self.at(x, y), *self.at(x + w, y + h))

    def _stroke(self, draw, points, width: float, fill) -> None:
        """Stamp a disc along a path — PIL has no round cap and frays on curves.

        The path is densified first: a polygon's straight flanks carry only
        their end points, and stamping those alone leaves a dotted line.
        """
        radius = max(1.0, width * self.scale / 2.0)
        step = max(0.5, radius / 2.0)
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            span = math.hypot(x1 - x0, y1 - y0)
            for i in range(max(1, int(span / step)) + 1):
                t = min(1.0, i * step / span) if span else 0.0
                x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

    def _layer(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        layer = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        return layer, ImageDraw.Draw(layer)

    def render(self) -> Image.Image:
        polygon = [self.at(x, y) for x, y in body_polygon()]

        # Solid ink body — darker than any part of the pool behind it.
        silhouette = Image.new("L", (self.width, self.height), 0)
        ImageDraw.Draw(silhouette).polygon(polygon, fill=255)
        body = Image.new("RGB", (self.width, self.height), BODY_INK)
        gigi = Image.merge("RGBA", (*body.split(), silhouette))

        # Trim: the paper outline that separates ink from a dark ground.
        self._stroke(ImageDraw.Draw(gigi), [*polygon, polygon[0]], TRIM_WIDTH, (*PAPER, 255))

        glowing, glow_draw = self._layer()  # gets a blurred copy underneath
        matte, matte_draw = self._layer()  # stays crisp, no glow
        if self.detail:
            for x, y, w, h, opacity in SCANLINES:
                matte_draw.rectangle(self._rect(x, y, w, h), fill=(*PAPER, round(255 * opacity)))
            for x, y, w, h in CHEEKS:
                matte_draw.rectangle(self._rect(x, y, w, h), fill=(*PAPER, 82))
            for pixels, alpha in ((GLITCH_RIGHT, 255), (GLITCH_LEFT, 179)):
                for x, y, w, h in pixels:
                    glow_draw.rectangle(self._rect(x, y, w, h), fill=(*PAPER, alpha))
            for arm in (ARM_LEFT, ARM_RIGHT):
                self._stroke(
                    glow_draw,
                    [self.at(*point) for point in _quadratic(*arm, steps=160)],
                    ARM_WIDTH,
                    (*PAPER, 255),
                )
        for eye in EYES:
            glow_draw.ellipse(self._oval(*eye), fill=(*PAPER, 255))
        glow_draw.ellipse(self._oval(*MOUTH), fill=(*PAPER, 255))

        # A wide soft halo, then the tight glow, then the crisp shapes.
        halo, halo_draw = self._layer()
        for cx, cy, rx, ry in EYES:
            halo_draw.ellipse(self._oval(cx, cy, rx + 4, ry + 4), fill=(*PAPER, 165))
        halo_draw.ellipse(
            self._oval(MOUTH[0], MOUTH[1], MOUTH[2] + 4, MOUTH[3] + 4), fill=(*PAPER, 130)
        )
        gigi.alpha_composite(halo.filter(ImageFilter.GaussianBlur(max(1.0, 8.0 * self.scale))))
        gigi.alpha_composite(glowing.filter(ImageFilter.GaussianBlur(max(1.0, 3.2 * self.scale))))
        gigi.alpha_composite(glowing)
        gigi.alpha_composite(matte)

        face, face_draw = self._layer()
        for pupil in PUPILS:
            face_draw.ellipse(self._oval(*pupil), fill=(*FACE_INK, 255))
        face_draw.ellipse(self._oval(*MOUTH_INNER), fill=(*FACE_INK, 255))
        if self.detail:
            for cx, cy, r in SPARKLES:
                face_draw.ellipse(self._oval(cx, cy, r, r), fill=(*PAPER, 255))
        gigi.alpha_composite(face)

        return gigi.resize(
            (max(1, self.width // SUPERSAMPLE), max(1, self.height // SUPERSAMPLE)),
            Image.Resampling.LANCZOS,
        )


def _hairline(size: int) -> Image.Image:
    """A ring just inside the tile edge, so the shape survives a dark taskbar."""
    inset = max(1, round(size * HAIRLINE_WIDTH))
    inner = Image.new("L", (size, size), 0)
    inner.paste(squircle_mask(size - 2 * inset), (inset, inset))
    ring = ImageChops.subtract(squircle_mask(size), inner)
    line = Image.new("RGBA", (size, size), (*PAPER, 255))
    line.putalpha(ring.point(lambda v: int(v * HAIRLINE_ALPHA)))
    return line


def render_tile(size: int) -> Image.Image:
    """Render one icon size from scratch — never by resampling a bigger one."""
    tile = pool_of_light(size).convert("RGBA")
    tile.alpha_composite(_hairline(size))
    gigi = _Mascot(size, mark_span(size), size >= DETAIL_FROM).render()
    tile.alpha_composite(gigi, ((size - gigi.width) // 2, (size - gigi.height) // 2))
    tile.putalpha(squircle_mask(size))
    return tile


def _dev_ribbon(size: int) -> Image.Image:
    """The amber DEV label the dev instance wears in its lower-right corner.

    Drawn from the same amber the interface uses for "degraded" so the two
    icons share a palette, and sized from the tile so it survives every ICO
    member: below 32 px the word cannot be read, so the label collapses to a
    plain amber block that still says "this is the other one" at a glance.
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
    radius = height * 0.28
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(*DEV_AMBER, 255))
    if size >= 32:
        font = ImageFont.load_default(size=int(height * 0.78))
        text = "DEV"
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tx = x0 + (width - (right - left)) / 2 - left
        ty = y0 + (height - (bottom - top)) / 2 - top
        draw.text((tx, ty), text, font=font, fill=(*DEV_INK, 255))
    return ribbon


def render_dev_tile(size: int) -> Image.Image:
    """The dev instance's icon: the same tile, wearing the DEV ribbon."""
    tile = render_tile(size)
    tile.alpha_composite(_dev_ribbon(size))
    return tile


def render_mark(size: int) -> Image.Image:
    """The free-standing mark, for surfaces that bring their own frame.

    The share card's ring, for one. With no tile there is no edge to keep clear
    of, so it fills the canvas instead of sitting inside a tile's margin.
    """
    gigi = _Mascot(size, 1.0, True).render()
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(gigi, ((size - gigi.width) // 2, (size - gigi.height) // 2))
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
        # Bundled copy: imported by GigiMark so Vite gives it a content hash.
        # A public/ file keeps its name forever, and browsers keep serving the
        # cached one — the reason a redrawn icon does not show up in the app.
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "assets" / "jarvis-mark.png",
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
    # The dev instance (``--instance dev``) shows its own DEV-badged copy on the
    # taskbar and in its shortcut. It is rendered here, from the same drawing,
    # so a redrawn Gigi can no longer leave the dev icon one draft behind.
    dev_members = [render_dev_tile(s) for s in sorted(ICO_SIZES, reverse=True)]
    write_png(ROOT / "assets" / "icons" / "jarvis-dev.png", render_dev_tile(256))
    for path in (
        ROOT / "jarvis" / "assets" / "icons" / "jarvis-dev.ico",
        ROOT / "assets" / "icons" / "jarvis-dev.ico",
    ):
        write_ico(path, dev_members)
    print(
        f"wrote the app icon at {MASTER} px, 256 px, {len(ICO_SIZES)} ICO members, "
        "and the DEV-badged copy"
    )


if __name__ == "__main__":
    main()
