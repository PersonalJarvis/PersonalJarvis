"""Stable-frame capture tests — injectable grabber, no real display needed."""
from __future__ import annotations

import pytest

pytest.importorskip("PIL", reason="pillow required for capture tests")

from jarvis.cu.capture import (
    Frame,
    capture_stable_frame,
    frames_differ,
    grab_region,
)
from jarvis.cu.geometry import MonitorInfo


def _solid(size: tuple[int, int], rgb: tuple[int, int, int]) -> tuple[tuple[int, int], bytes]:
    w, h = size
    return ((w, h), bytes(rgb) * (w * h))


def _monitor(**kw) -> MonitorInfo:
    defaults = dict(left=0, top=0, width=192, height=108)
    defaults.update(kw)
    return MonitorInfo(**defaults)


# ---------------------------------------------------------------------------
# frames_differ
# ---------------------------------------------------------------------------

def test_identical_frames_do_not_differ():
    a = _solid((192, 108), (30, 30, 30))
    assert not frames_differ(a, a)


def test_tiny_change_stays_below_threshold():
    # One "blinking cursor": a 2x12 white block on a dark frame.
    a = _solid((192, 108), (30, 30, 30))
    pixels = bytearray(a[1])
    for row in range(12):
        for col in range(2):
            idx = ((20 + row) * 192 + (50 + col)) * 3
            pixels[idx:idx + 3] = b"\xff\xff\xff"
    b = ((192, 108), bytes(pixels))
    assert not frames_differ(a, b)


def test_large_change_differs():
    a = _solid((192, 108), (30, 30, 30))
    b = _solid((192, 108), (200, 200, 200))
    assert frames_differ(a, b)


def test_resolution_change_always_differs():
    a = _solid((192, 108), (30, 30, 30))
    b = _solid((96, 54), (30, 30, 30))
    assert frames_differ(a, b)


# ---------------------------------------------------------------------------
# capture_stable_frame
# ---------------------------------------------------------------------------

def test_stable_screen_returns_after_one_regrab():
    frame_a = _solid((192, 108), (10, 20, 30))
    calls = {"n": 0}

    def grab(bbox):
        calls["n"] += 1
        return frame_a

    frame = capture_stable_frame(
        _monitor(), grab=grab, sleep=lambda s: None,
    )
    assert isinstance(frame, Frame)
    assert frame.stable
    assert calls["n"] == 2  # initial + one confirming re-grab
    assert frame.image_width == 192 and frame.image_height == 108
    assert frame.mapper.screen_rect == (0, 0, 192, 108)
    assert frame.jpeg[:2] == b"\xff\xd8"  # JPEG magic


def test_animating_screen_times_out_unstable():
    shade = {"v": 0}

    def grab(bbox):
        shade["v"] = (shade["v"] + 60) % 250
        return _solid((192, 108), (shade["v"],) * 3)

    frame = capture_stable_frame(
        _monitor(),
        grab=grab,
        sleep=lambda s: None,
        stability_timeout_s=0.05,
    )
    assert not frame.stable


def test_settles_after_a_few_changing_frames():
    frames = [
        _solid((192, 108), (0, 0, 0)),
        _solid((192, 108), (120, 120, 120)),
        _solid((192, 108), (240, 240, 240)),
        _solid((192, 108), (240, 240, 240)),  # settled
    ]
    seq = list(frames)

    def grab(bbox):
        return seq.pop(0) if len(seq) > 1 else frames[-1]

    frame = capture_stable_frame(_monitor(), grab=grab, sleep=lambda s: None)
    assert frame.stable


def test_downscale_builds_matching_mapper():
    big = _solid((1920, 1080), (50, 60, 70))
    frame = capture_stable_frame(
        _monitor(width=1920, height=1080),
        grab=lambda bbox: big,
        sleep=lambda s: None,
        max_dimension=960,
    )
    assert frame.image_width == 960 and frame.image_height == 540
    # Model pixel center of the image -> monitor center.
    x, y = frame.mapper.image_to_screen(480, 270)
    assert abs(x - 960) <= 2 and abs(y - 540) <= 2


def test_retina_style_grab_larger_than_monitor_rect():
    # macOS: bbox in points (1440x900), grab returns 2x pixels (2880x1800).
    raw = _solid((2880, 1800), (5, 5, 5))
    frame = capture_stable_frame(
        _monitor(width=1440, height=900),
        grab=lambda bbox: raw,
        sleep=lambda s: None,
        max_dimension=1366,
    )
    # Image was downscaled from the 2880px grab; mapper still maps into the
    # 1440x900 POINT rect the input backend consumes.
    assert frame.image_width == 1366
    sx, sy = frame.mapper.image_to_screen(frame.image_width - 1, frame.image_height - 1)
    assert sx < 1440 and sy < 900


def test_blob_written_when_dir_given(tmp_path):
    frame = capture_stable_frame(
        _monitor(),
        grab=lambda bbox: _solid((192, 108), (1, 2, 3)),
        sleep=lambda s: None,
        blob_dir=tmp_path,
    )
    assert frame.blob_path is not None
    assert (tmp_path / f"{frame.sha256}.jpg").exists()


def test_thumb_identity_ignores_caret_noise_but_sees_real_change():
    from jarvis.cu.capture import screen_thumb, thumbs_similar

    base = _solid((192, 108), (30, 30, 30))
    # A caret-sized change: a small bright block on the dark frame.
    pixels = bytearray(base[1])
    for row in range(12):
        for col in range(2):
            idx = ((20 + row) * 192 + (50 + col)) * 3
            pixels[idx:idx + 3] = b"\xff\xff\xff"
    caret = ((192, 108), bytes(pixels))
    changed = _solid((192, 108), (200, 200, 200))
    assert thumbs_similar(screen_thumb(base), screen_thumb(base))
    assert thumbs_similar(screen_thumb(base), screen_thumb(caret))
    assert not thumbs_similar(screen_thumb(base), screen_thumb(changed))
    # Opaque string keys (tests / foreign callers) compare by equality.
    assert thumbs_similar("sha1", "sha1")
    assert not thumbs_similar("sha1", "sha2")


def test_frame_carries_exact_and_perceptual_identity():
    frame = capture_stable_frame(
        _monitor(),
        grab=lambda bbox: _solid((192, 108), (1, 2, 3)),
        sleep=lambda s: None,
    )
    assert len(frame.sha256) == 64
    assert len(frame.thumb) == 96 * 54


def test_grab_region_swallows_failures():
    def broken(bbox):
        raise OSError("BitBlt failed")

    assert grab_region({"left": 0, "top": 0, "width": 10, "height": 10}, grab=broken) is None
    ok = grab_region(
        {"left": 0, "top": 0, "width": 4, "height": 4},
        grab=lambda bbox: _solid((4, 4), (9, 9, 9)),
    )
    assert ok is not None and ok[0] == (4, 4)
