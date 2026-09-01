"""The app icon is drawn from the mascot's own geometry — keep the two in sync.

``scripts/make_gigi_app_icon.py`` copies the body path, the eyes and the mouth
out of ``MascotGigi.tsx`` because there is no runtime that can share them: one
side is Python drawing a PNG at build time, the other is React drawing SVG in a
browser. A copy that nothing checks drifts silently, and the drift only shows
up as a taskbar icon that no longer matches the mascot on screen.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.make_gigi_app_icon import BODY_DOME, BODY_SKIRT, EYES, MOUTH, render_tile

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


def test_eyes_and_mouth_match_the_mascot(mascot_source: str) -> None:
    """Eye sockets and the mouth are punched from the same ellipses."""
    ellipses = {
        (float(cx), float(cy), float(rx), float(ry))
        for cx, cy, rx, ry in re.findall(
            r'<ellipse cx="([\d.]+)" cy="([\d.]+)" rx="([\d.]+)" ry="([\d.]+)"',
            mascot_source,
        )
    }
    for shape in (*EYES, MOUTH):
        assert tuple(float(v) for v in shape) in ellipses, f"{shape} is not in MascotGigi.tsx"


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
