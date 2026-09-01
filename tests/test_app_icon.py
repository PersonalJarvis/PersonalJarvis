"""The app icon is drawn from the mascot's own geometry — keep the two in sync.

``scripts/make_gigi_app_icon.py`` copies every shape out of ``MascotGigi.tsx``
because there is no runtime that can share them: one side is Python drawing a
PNG at build time, the other is React drawing SVG in a browser. A copy that
nothing checks drifts silently, and the drift only shows up as a taskbar icon
that no longer matches the mascot on screen.

The last test is the one that would have caught the icon this replaced: a body
no darker than its background is a drawing pasted onto a tile, not a ghost
standing in light, and it is exactly what makes an icon look dead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.make_gigi_app_icon import (
    ARM_LEFT,
    ARM_RIGHT,
    BODY_DOME,
    BODY_INK,
    BODY_SKIRT,
    CHEEKS,
    EYES,
    GLITCH_LEFT,
    GLITCH_RIGHT,
    MOUTH,
    MOUTH_INNER,
    POOL_INNER,
    POOL_OUTER,
    PUPILS,
    SCANLINES,
    render_tile,
)

MASCOT = (
    Path(__file__).resolve().parents[1]
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "components"
    / "MascotGigi.tsx"
)


@pytest.fixture(scope="module")
def mascot_source() -> str:
    return MASCOT.read_text(encoding="utf-8")


def test_body_path_matches_the_mascot(mascot_source: str) -> None:
    """The dome control points and the zigzag skirt come from the SVG path."""
    match = re.search(r'className="gigi-body"\s*\n\s*d="([^"]+)"', mascot_source)
    assert match, "MascotGigi.tsx no longer exposes a gigi-body path"
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", match.group(1))]
    points = list(zip(numbers[0::2], numbers[1::2], strict=True))
    assert points[: len(BODY_DOME)] == [(float(x), float(y)) for x, y in BODY_DOME]
    assert points[len(BODY_DOME) :] == [(float(x), float(y)) for x, y in BODY_SKIRT]


def test_ellipses_match_the_mascot(mascot_source: str) -> None:
    """Eyes, pupils and both mouth ellipses are the mascot's own."""
    drawn = {
        (float(cx), float(cy), float(rx), float(ry))
        for cx, cy, rx, ry in re.findall(
            r'<ellipse[^>]*?cx="([\d.]+)" cy="([\d.]+)" rx="([\d.]+)" ry="([\d.]+)"',
            mascot_source,
        )
    }
    for shape in (*EYES, *PUPILS, MOUTH, MOUTH_INNER):
        assert tuple(float(v) for v in shape) in drawn, f"{shape} is not in MascotGigi.tsx"


def test_rectangles_match_the_mascot(mascot_source: str) -> None:
    """Scanlines, cheek slices and glitch pixels come from the same rects."""
    drawn = {
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"',
            mascot_source,
        )
    }
    scanlines = [shape[:4] for shape in SCANLINES]
    for shape in (*scanlines, *CHEEKS, *GLITCH_LEFT, *GLITCH_RIGHT):
        assert tuple(float(v) for v in shape) in drawn, f"{shape} is not in MascotGigi.tsx"


def test_arms_match_the_mascot(mascot_source: str) -> None:
    """Both arms are the quadratics the mascot waves with."""
    paths = re.findall(r'className="gigi-arm[^"]*"\s*\n\s*d="([^"]+)"', mascot_source)
    assert len(paths) == 2, "MascotGigi.tsx no longer draws two arms"
    drawn = set()
    for path in paths:
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", path)]
        drawn.add(tuple(zip(numbers[0::2], numbers[1::2], strict=True)))
    for arm in (ARM_LEFT, ARM_RIGHT):
        assert tuple((float(x), float(y)) for x, y in arm) in drawn, f"{arm} is not an arm"


def test_gigi_is_darker_than_every_part_of_his_ground() -> None:
    """He must read as a silhouette standing in light, not a shape on a tile.

    Equal values are what made the previous icon look dead: the outline was the
    only thing separating him from the background, so he read as a wireframe.
    """
    assert max(BODY_INK) < min(POOL_OUTER), "the body is not darker than the tile's darkest point"
    assert min(POOL_INNER) > max(POOL_OUTER), "the pool of light has no falloff"


@pytest.mark.parametrize("size", [16, 32, 256])
def test_every_size_renders_a_filled_tile(size: int) -> None:
    """A rendered tile is opaque in the middle and cut away in the corner.

    The corner is not asserted at exactly zero: the squircle is antialiased by
    supersampling, so at 16 px a trace of edge coverage lands in that pixel.
    """
    tile = render_tile(size)
    assert tile.size == (size, size)
    assert tile.getpixel((size // 2, size // 2))[3] == 255
    assert tile.getpixel((0, 0))[3] < 16
