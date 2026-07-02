"""CoordinateMapper property tests — the coordinate spaces CU v2 must not mix.

The legacy engine's miss-class bugs are encoded here as concrete cases:
* the live 2026-06-27 left-monitor run (negative virtual-desktop origin),
* macOS Retina (capture rect in points, image in pixels),
* mixed-DPI downscaled frames (4K monitor -> ~1366px model image).
"""
from __future__ import annotations

import pytest

from jarvis.cu.geometry import (
    CoordinateMapper,
    MonitorInfo,
    input_space,
    virtual_screen_bounds,
)


def _mapper(**kw) -> CoordinateMapper:
    defaults = dict(
        capture_left=0, capture_top=0,
        capture_width=1920, capture_height=1080,
        image_width=1920, image_height=1080,
    )
    defaults.update(kw)
    return CoordinateMapper(**defaults)


# ---------------------------------------------------------------------------
# Basic mapping
# ---------------------------------------------------------------------------

def test_identity_frame_maps_pixel_to_same_pixel():
    m = _mapper()
    assert m.image_to_screen(0, 0) == (0, 0)
    assert m.image_to_screen(959, 539) == (959, 539)
    assert m.image_to_screen(1919, 1079) == (1919, 1079)


def test_normalized_center_maps_to_screen_center():
    m = _mapper()
    assert m.normalized_to_screen(500, 500) == (960, 540)


def test_normalized_extremes_stay_inside_capture_rect():
    m = _mapper()
    assert m.normalized_to_screen(0, 0) == (0, 0)
    x, y = m.normalized_to_screen(1000, 1000)
    assert (x, y) == (1919, 1079)


def test_out_of_range_model_coords_are_clamped():
    m = _mapper()
    assert m.image_to_screen(-50, -50) == (0, 0)
    x, y = m.image_to_screen(99999, 99999)
    assert (x, y) == (1919, 1079)
    nx, ny = m.normalized_to_screen(1400, -3)
    assert 0 <= nx <= 1919 and 0 <= ny <= 1079


# ---------------------------------------------------------------------------
# The live-log left-monitor case (negative origin, BUG-CU-MULTIMON forensic)
# ---------------------------------------------------------------------------

def test_left_monitor_negative_origin_matches_live_log():
    # data/jarvis_desktop.log 2026-06-27 12:31: norm=(636,531) on the
    # 3840x2160 monitor at left=-3840 resolved to abs=(-1398,1147).
    m = _mapper(
        capture_left=-3840, capture_top=0,
        capture_width=3840, capture_height=2160,
        image_width=3840, image_height=2160,
    )
    assert m.normalized_to_screen(636, 531) == (-1398, 1147)


def test_negative_origin_roundtrip_screen_image():
    m = _mapper(
        capture_left=-3840, capture_top=-500,
        capture_width=3840, capture_height=2160,
        image_width=1366, image_height=768,
    )
    for sx, sy in [(-3840, -500), (-1920, 500), (-1, 1659)]:
        ix, iy = m.screen_to_image(sx, sy)
        rx, ry = m.image_to_screen(ix, iy)
        # One image pixel covers capture/image ~2.8 screen units here.
        assert abs(rx - sx) <= 3
        assert abs(ry - sy) <= 3


# ---------------------------------------------------------------------------
# Retina / downscale: image pixels differ from capture units
# ---------------------------------------------------------------------------

def test_retina_points_capture_with_pixel_image():
    # macOS: capture rect is 1440x900 POINTS; the model gets a 2880x1800
    # pixel image (2x backing scale). A model pixel coordinate must land on
    # the correct POINT for Quartz input.
    m = _mapper(
        capture_left=0, capture_top=0,
        capture_width=1440, capture_height=900,
        image_width=2880, image_height=1800,
    )
    # Image center -> point center.
    assert m.image_to_screen(1440, 900) == (720, 450)
    # A click at image pixel (2878, 1798) must stay on-screen in points.
    x, y = m.image_to_screen(2878, 1798)
    assert 0 <= x < 1440 and 0 <= y < 900


def test_downscaled_4k_frame_maps_back_to_full_monitor():
    # 4K monitor captured, downscaled to 1366-wide for the model.
    m = _mapper(
        capture_left=0, capture_top=0,
        capture_width=3840, capture_height=2160,
        image_width=1366, image_height=768,
    )
    # Image center -> monitor center (within one scale quantum).
    x, y = m.image_to_screen(683, 384)
    assert abs(x - 1920) <= 3 and abs(y - 1080) <= 3
    # normalized convention is image-size independent.
    assert m.normalized_to_screen(500, 500) == (1920, 1080)


def test_aspect_mismatch_is_rejected():
    with pytest.raises(ValueError):
        _mapper(
            capture_width=3840, capture_height=2160,   # 16:9
            image_width=1024, image_height=1024,        # square
        )


def test_zero_sizes_are_rejected():
    with pytest.raises(ValueError):
        _mapper(capture_width=0)
    with pytest.raises(ValueError):
        _mapper(image_height=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_contains_screen_and_region_clamping():
    m = _mapper(capture_left=-1280, capture_top=0,
                capture_width=1280, capture_height=720,
                image_width=1280, image_height=720)
    assert m.contains_screen(-1, 0)
    assert m.contains_screen(-1280, 719)
    assert not m.contains_screen(0, 0)          # first pixel of the NEXT monitor
    assert not m.contains_screen(-1281, 10)
    region = m.region_around(-1280, 0, radius=50)
    assert region["left"] == -1280 and region["top"] == 0
    assert region["width"] >= 1 and region["height"] >= 1
    # Region never leaves the capture rect.
    assert region["left"] + region["width"] <= -1280 + 1280
    assert region["top"] + region["height"] <= 0 + 720


def test_virtual_screen_bounds_spans_negative_origins():
    mons = [
        MonitorInfo(left=0, top=0, width=2560, height=1440, is_primary=True),
        MonitorInfo(left=-3840, top=0, width=3840, height=2160),
    ]
    assert virtual_screen_bounds(mons) == (-3840, 0, 6400, 2160)
    assert virtual_screen_bounds([]) == (0, 0, 0, 0)


def test_monitor_info_contains_and_bbox():
    m = MonitorInfo(left=-1280, top=100, width=1280, height=720)
    assert m.contains(-640, 400)
    assert not m.contains(1, 400)
    assert m.bbox == {"left": -1280, "top": 100, "width": 1280, "height": 720}


def test_input_space_is_reentrant_and_safe_everywhere():
    # On non-Windows a pure no-op; on Windows pins + restores. Either way it
    # must never raise and must nest.
    with input_space():
        with input_space():
            pass


def test_model_to_screen_dispatches_conventions():
    m = _mapper(
        capture_left=100, capture_top=200,
        capture_width=1000, capture_height=500,
        image_width=500, image_height=250,
    )
    assert m.model_to_screen(500, 500, "normalized_1000") == (600, 450)
    # image pixel (250,125) = image center -> capture center
    x, y = m.model_to_screen(250, 125, "image_pixels")
    assert abs(x - 600) <= 2 and abs(y - 450) <= 2
    with pytest.raises(ValueError):
        m.model_to_screen(1, 1, "polar")  # type: ignore[arg-type]
