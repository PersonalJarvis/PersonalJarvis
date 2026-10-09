"""The app icon and the in-app mark are drawn from one vector logo.

``scripts/make_app_icon.py`` renders every native icon from
``assets/brand/jarvis-logo.svg`` and writes the same path into
``src/components/brand/jarvisLogoPath.ts`` for the frontend. There is no
runtime that can share the two — one side is Python drawing a PNG at build
time, the other is React drawing SVG in a browser — so a copy nothing checks
would drift and show up as a taskbar icon that no longer matches the app.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scripts.make_app_icon import (
    LOGO_TS,
    figure_mask,
    logo_path_data,
    logo_polygons,
    logo_ts_source,
    render_tile,
)

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_path_matches_the_logo_svg() -> None:
    """The TypeScript copy is exactly what the generator would write."""
    assert LOGO_TS.read_text(encoding="utf-8") == logo_ts_source(logo_path_data())


def test_logo_fits_its_viewbox() -> None:
    points = [p for polygon in logo_polygons(logo_path_data()) for p in polygon]
    assert points, "the logo path is empty"
    assert all(0.0 <= x <= 256.0 and 0.0 <= y <= 256.0 for x, y in points)


def test_eyes_are_cut_out_of_the_figure() -> None:
    """Even-odd filling keeps the holes: the figure is not one solid blob."""
    mask = figure_mask(256, 1.0)
    histogram = mask.histogram()
    ink, paper = sum(histogram[200:]), sum(histogram[:56])
    assert 0.25 < ink / (256 * 256) < 0.6, "the figure covers an implausible share of its box"
    assert paper > ink, "the background is missing"


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


def test_committed_icons_are_the_current_render() -> None:
    """Re-run ``python scripts/make_app_icon.py`` after editing the logo."""
    committed = Image.open(ROOT / "jarvis" / "assets" / "icons" / "jarvis.png").convert("RGBA")
    assert committed.tobytes() == render_tile(256).tobytes()
