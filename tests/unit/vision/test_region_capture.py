"""Tests for the region-of-interest crop around the cursor (AI Pointer step 4)."""

from __future__ import annotations

from jarvis.vision import screenshot as screenshot_module
from jarvis.vision.screenshot import capture_region, region_bbox_around


def test_region_bbox_around_centers_and_sizes() -> None:
    bb = region_bbox_around(100, 200, 50)
    assert bb == {"left": 50, "top": 150, "width": 100, "height": 100}


def test_region_bbox_handles_negative_coords() -> None:
    # Multi-monitor: a secondary screen to the left has negative X.
    bb = region_bbox_around(-1663, 696, 64)
    assert bb == {"left": -1727, "top": 632, "width": 128, "height": 128}


def test_region_bbox_clamps_to_virtual_bounds() -> None:
    bb = region_bbox_around(5, 5, 50, virtual_bounds=(0, 0, 800, 600))
    assert bb["left"] >= 0
    assert bb["top"] >= 0
    assert bb["left"] + bb["width"] <= 800
    assert bb["top"] + bb["height"] <= 600
    assert bb["width"] >= 1 and bb["height"] >= 1


def test_capture_region_returns_jpeg_bytes(monkeypatch) -> None:
    import pytest

    pytest.importorskip("PIL")  # Pillow lives in the [desktop] extra (cloud-first)

    def fake_grab(bbox: dict) -> tuple[tuple[int, int], bytes]:
        w, h = bbox["width"], bbox["height"]
        return ((w, h), b"\x10\x20\x30" * (w * h))  # solid RGB fill

    monkeypatch.setattr(
        screenshot_module, "warn_if_screen_recording_denied", lambda: False
    )
    data = capture_region(
        {"left": 0, "top": 0, "width": 8, "height": 8}, grab=fake_grab
    )
    assert data[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert len(data) > 10


def test_capture_region_png_format(monkeypatch) -> None:
    import pytest

    pytest.importorskip("PIL")

    def fake_grab(bbox: dict) -> tuple[tuple[int, int], bytes]:
        w, h = bbox["width"], bbox["height"]
        return ((w, h), b"\xaa\xbb\xcc" * (w * h))

    monkeypatch.setattr(
        screenshot_module, "warn_if_screen_recording_denied", lambda: False
    )
    data = capture_region(
        {"left": 0, "top": 0, "width": 4, "height": 4},
        image_format="png",
        grab=fake_grab,
    )
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature
