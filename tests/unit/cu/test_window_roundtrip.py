"""Window-centric position-translation roundtrip — the multi-monitor contract.

The product promise under test: Jarvis looks at the TARGET WINDOW, works with
window-relative positions, and translates them into global screen positions
via window origin + scale — through ONE central translation
(:class:`jarvis.cu.geometry.CoordinateMapper`), no matter which monitor the
window sits on and no matter the monitor's size or scale factor.

Every case simulates a real platform layout end-to-end through the actual
capture path (``capture_stable_frame`` with an injected grabber, real
downscale/encode, real mapper):

    known point in the window -> image position (what the model sees)
        -> CoordinateMapper -> global position == window origin + point

Also under test here:
* window targets carry the platform window handle (native per-window capture),
* ``grabber_for`` prefers the native per-window grab and falls back to the
  rect grab,
* the zoom-refine crop translation resolves through the SAME central mapper
  (``_crop_norm_to_screen``) instead of hand-rolled math,
* element snapping can never push a click outside the capture rect.
"""
from __future__ import annotations

import math

import pytest

from jarvis.cu import capture as capture_mod
from jarvis.cu.capture import capture_stable_frame, grabber_for, mss_grab
from jarvis.cu.geometry import MonitorInfo

# Each case: (id, window rect in input units, grab scale (pixels per input
# unit — 1.0 everywhere except macOS Retina), probe points relative to the
# window origin).
LAYOUTS = [
    pytest.param(
        # Windows mixed-DPI: 150%-scaled 4K left of the primary — physical-px
        # virtual desktop with a negative origin (the live 2026-06-27 bug).
        MonitorInfo(left=-2000, top=100, width=1890, height=1040,
                    name="window:chrome-on-left-4k", window_handle=7),
        1.0,
        [(1, 1), (945, 520), (1888, 1038), (378, 346)],
        id="windows-left-4k-negative-origin",
    ),
    pytest.param(
        # Windows stacked layout: monitor ABOVE the primary (negative Y).
        MonitorInfo(left=300, top=-1800, width=800, height=600,
                    name="window:stacked-above", window_handle=11),
        1.0,
        [(1, 1), (400, 300), (798, 598)],
        id="windows-stacked-above-negative-y",
    ),
    pytest.param(
        # macOS Retina: window rect in POINTS on a secondary display, capture
        # backed at 2x PIXELS (ScreenCaptureKit / CG capture convention).
        MonitorInfo(left=1728, top=-200, width=1200, height=800,
                    name="window:safari-retina", window_handle=42),
        2.0,
        [(1, 1), (600, 400), (1198, 798), (240, 267)],
        id="macos-retina-secondary-points",
    ),
    pytest.param(
        # Linux/X11: root-pixel space, window on a 4K screen right of primary.
        MonitorInfo(left=2400, top=300, width=1000, height=700,
                    name="window:firefox-x11", window_handle=123456),
        1.0,
        [(1, 1), (500, 350), (998, 698)],
        id="linux-x11-right-4k",
    ),
    pytest.param(
        # Fallback scope: no target window (desktop/taskbar action) — the
        # whole monitor runs through the SAME translation.
        MonitorInfo(left=-3840, top=0, width=3840, height=2160,
                    name="left-4k-monitor"),
        1.0,
        [(10, 10), (1920, 1080), (3830, 2150)],
        id="monitor-fallback-same-translation",
    ),
]


def _synthetic_grab(scale: float):
    """Grabber returning a black frame of ``bbox size * scale`` pixels."""

    def grab(bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]:
        gw = round(bbox["width"] * scale)
        gh = round(bbox["height"] * scale)
        return ((gw, gh), bytes(gw * gh * 3))

    return grab


@pytest.mark.parametrize(("target", "scale", "probes"), LAYOUTS)
def test_window_point_roundtrips_to_global_hit(target, scale, probes):
    frame = capture_stable_frame(
        target, grab=_synthetic_grab(scale), stability_timeout_s=0.0,
    )
    m = frame.mapper
    # One image pixel expressed in window units — the quantization floor.
    tol = math.ceil(target.width / frame.image_width)
    for wx, wy in probes:
        expected = (target.left + wx, target.top + wy)

        # Pixel convention (Claude/OpenAI): the model points at the image
        # pixel showing the window point.
        ix = wx * frame.image_width / target.width
        iy = wy * frame.image_height / target.height
        gx, gy = m.image_to_screen(ix, iy)
        assert abs(gx - expected[0]) <= tol, (target.name, (wx, wy), (gx, gy))
        assert abs(gy - expected[1]) <= tol
        assert m.contains_screen(gx, gy)

        # Normalized convention (Gemini): 0..1000 grid over the same image.
        nx = wx / target.width * 1000.0
        ny = wy / target.height * 1000.0
        gx2, gy2 = m.normalized_to_screen(nx, ny)
        assert abs(gx2 - expected[0]) <= tol + 1
        assert abs(gy2 - expected[1]) <= tol + 1
        assert m.contains_screen(gx2, gy2)

        # Inverse direction (verification crops) returns to the same pixel.
        ix2, iy2 = m.screen_to_image(gx, gy)
        assert abs(ix2 - ix) <= 1
        assert abs(iy2 - iy) <= 1


@pytest.mark.parametrize(("target", "scale", "probes"), LAYOUTS)
def test_model_coordinates_can_never_escape_the_capture_rect(
    target, scale, probes,
):
    frame = capture_stable_frame(
        target, grab=_synthetic_grab(scale), stability_timeout_s=0.0,
    )
    m = frame.mapper
    for x, y in [(-50, -50), (0, 0), (10_000, 10_000)]:
        assert m.contains_screen(*m.image_to_screen(x, y))
    for nx, ny in [(0, 0), (1000, 1000), (1400, -3)]:
        assert m.contains_screen(*m.normalized_to_screen(nx, ny))


# ---------------------------------------------------------------------------
# Window identity on the capture target + native-grab fallback chain
# ---------------------------------------------------------------------------

def test_window_target_carries_the_platform_window_handle(monkeypatch):
    from jarvis.platform import window_state as ws
    from jarvis.platform.window_state import WindowInfo

    monitor = MonitorInfo(left=-2560, top=0, width=2560, height=1440,
                          name="left")
    monkeypatch.setattr(
        capture_mod, "select_monitor", lambda policy, main_monitor: monitor,
    )
    monkeypatch.setattr(
        ws, "foreground_window", lambda: WindowInfo(title="App", handle=4242),
    )
    monkeypatch.setattr(ws, "is_shell_window", lambda w: False)
    monkeypatch.setattr(
        ws, "window_frame_rect", lambda w: (-2000, 100, 800, 600),
    )
    got = capture_mod.select_capture_target("foreground", scope="window")
    assert got.window_handle == 4242
    assert got.name.startswith("window:")


def test_monitor_fallback_target_has_no_window_handle(monkeypatch):
    from jarvis.platform import window_state as ws

    monitor = MonitorInfo(left=0, top=0, width=1920, height=1080)
    monkeypatch.setattr(
        capture_mod, "select_monitor", lambda policy, main_monitor: monitor,
    )
    monkeypatch.setattr(ws, "foreground_window", lambda: None)
    got = capture_mod.select_capture_target("foreground", scope="window")
    assert got.window_handle is None


def test_grabber_for_plain_monitor_is_the_rect_grab():
    assert grabber_for(MonitorInfo(0, 0, 800, 600)) is mss_grab


def test_grabber_for_window_uses_native_grab_when_available(monkeypatch):
    from jarvis.platform import window_capture

    native = ((80, 60), bytes(80 * 60 * 3))
    monkeypatch.setattr(
        window_capture, "grab_window", lambda handle, bbox: native,
    )
    monkeypatch.setattr(
        capture_mod, "mss_grab",
        lambda bbox: (_ for _ in ()).throw(AssertionError("mss must not run")),
    )
    target = MonitorInfo(0, 0, 80, 60, name="window:x", window_handle=9)
    assert grabber_for(target)({"left": 0, "top": 0, "width": 80,
                                "height": 60}) == native


def test_grabber_for_window_falls_back_to_rect_grab(monkeypatch):
    from jarvis.platform import window_capture

    fallback = ((80, 60), b"\x01" * (80 * 60 * 3))
    monkeypatch.setattr(window_capture, "grab_window",
                        lambda handle, bbox: None)
    monkeypatch.setattr(capture_mod, "mss_grab", lambda bbox: fallback)
    target = MonitorInfo(0, 0, 80, 60, name="window:x", window_handle=9)
    grab = grabber_for(target)
    bbox = {"left": 0, "top": 0, "width": 80, "height": 60}
    assert grab(bbox) == fallback
    # The unavailable verdict sticks — the native probe is not repeated on
    # every stability re-grab.
    calls = {"n": 0}

    def count_native(handle, bbox):
        calls["n"] += 1
        return None

    monkeypatch.setattr(window_capture, "grab_window", count_native)
    grab2 = grabber_for(target)
    grab2(bbox)
    grab2(bbox)
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Zoom-refine translation goes through the central mapper
# ---------------------------------------------------------------------------

def test_refine_crop_translation_resolves_through_central_mapper():
    from jarvis.cu.engine import _crop_norm_to_screen

    bbox = {"left": -2440, "top": 200, "width": 440, "height": 440}
    assert _crop_norm_to_screen(bbox, (440, 440), 500, 500) == (-2220, 420)
    # Retina: the crop image is 2x the bbox units — the normalized grid must
    # still resolve against the bbox rect, not the pixel image.
    assert _crop_norm_to_screen(bbox, (880, 880), 500, 500) == (-2220, 420)
    # The extreme edge stays INSIDE the crop rect (the old hand-rolled math
    # produced left+width — one unit outside).
    x, y = _crop_norm_to_screen(bbox, (440, 440), 1000, 1000)
    assert (x, y) == (-2440 + 439, 200 + 439)


# ---------------------------------------------------------------------------
# Element snapping stays inside the capture rect
# ---------------------------------------------------------------------------

def test_snap_never_leaves_the_capture_rect():
    from jarvis.cu.verify import snap_point_to_element

    # Element sticks out of the window capture on the right: its center lies
    # OUTSIDE the rect. Snapping there would click outside the window.
    clickables = [("Wide toolbar button", "Button", (950, 100, 200, 30))]
    rect = (0, 0, 1000, 800)
    hit = snap_point_to_element(
        960, 110, clickables, capture_area=1000 * 800, capture_rect=rect,
    )
    assert hit is None
    # A fully inside element still snaps normally.
    clickables = [("Save", "Button", (900, 100, 60, 30))]
    hit = snap_point_to_element(
        920, 110, clickables, capture_area=1000 * 800, capture_rect=rect,
    )
    assert hit == (930, 115, "Save")


# ---------------------------------------------------------------------------
# Wayland: detect cleanly, refuse with an actionable X11/XWayland message
# ---------------------------------------------------------------------------

def test_wayland_refusal_points_to_x11_and_xwayland(monkeypatch):
    import jarvis.platform.probes as probes
    from jarvis.cu.engine import _wayland_refusal

    monkeypatch.setattr(probes, "is_wayland", lambda: True)
    msg = _wayland_refusal()
    assert msg is not None
    assert "X11" in msg
    assert "XWayland" in msg
