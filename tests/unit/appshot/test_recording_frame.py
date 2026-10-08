"""Packed capture buffers are copied with stride and released before they can change."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def qt():
    gui = pytest.importorskip("PySide6.QtGui")
    multimedia = pytest.importorskip("PySide6.QtMultimedia")
    core = pytest.importorskip("PySide6.QtCore")
    return SimpleNamespace(gui=gui, media=multimedia, core=core)


def frame_for(qt, colors, *, padding=8, valid=True, mapping=True, short=False):
    """A backend-owned BGRA buffer that is deliberately overwritten on unmap."""
    height, width = len(colors), len(colors[0])
    stride = width * 4 + padding
    pixels = bytearray([91] * (stride * height))
    for y, row in enumerate(colors):
        for x, (r, g, b, a) in enumerate(row):
            pixels[y * stride + x * 4:y * stride + x * 4 + 4] = bytes((b, g, r, a))

    class Frame:
        unmapped = False
        rotation_degrees = 0
        mirror = False

        def isValid(self):
            return valid

        def pixelFormat(self):
            return qt.media.QVideoFrameFormat.PixelFormat.Format_BGRA8888

        def map(self, mode):
            assert mode == qt.media.QVideoFrame.MapMode.ReadOnly
            return mapping

        def width(self):
            return width

        def height(self):
            return height

        def bytesPerLine(self, plane):
            assert plane == 0
            return stride

        def mappedBytes(self, plane):
            return len(pixels) - int(short)

        def bits(self, plane):
            return memoryview(pixels)

        def unmap(self):
            self.unmapped = True
            pixels[:] = b"\0" * len(pixels)

        def surfaceFormat(self):
            return qt.media.QVideoFrameFormat(qt.core.QSize(width, height), self.pixelFormat())

        def rotation(self):
            return SimpleNamespace(value=self.rotation_degrees)

        def mirrored(self):
            return self.mirror

        def toImage(self):
            raise AssertionError("Packed desktop RGB must not enter GPU conversion")

    return Frame()


def test_rgb_stride_colors_and_lifetime_survive_backend_reuse(qt):
    from jarvis.appshot.recording_frame import owned_capture_image

    colors = [[(211, 17, 39, 255), (12, 207, 36, 255), (23, 43, 227, 255)],
              [(15, 18, 21, 255), (231, 225, 220, 255), (17, 65, 95, 255)]]
    frame = frame_for(qt, colors)
    image = owned_capture_image(frame)
    assert frame.unmapped
    assert (image.width(), image.height()) == (3, 2)
    for y, row in enumerate(colors):
        for x, color in enumerate(row):
            assert image.pixelColor(x, y).getRgb() == color


@pytest.mark.parametrize("alpha,empty", [(0, True), (255, False)])
def test_uninitialized_transparent_buffers_are_skipped_but_black_screens_are_kept(qt, alpha, empty):
    from jarvis.appshot.recording_frame import owned_capture_image

    frame = frame_for(qt, [[(0, 0, 0, alpha)] * 3] * 2)
    image = owned_capture_image(frame)
    assert image.isNull() == empty
    assert frame.unmapped


@pytest.mark.parametrize("kind", ["invalid", "unmappable", "short"])
def test_unusable_buffers_do_not_leak_a_mapping_or_return_pixels(qt, kind):
    from jarvis.appshot.recording_frame import owned_capture_image

    frame = frame_for(qt, [[(10, 20, 30, 255)] * 3] * 2,
                      valid=kind != "invalid", mapping=kind != "unmappable", short=kind == "short")
    assert owned_capture_image(frame).isNull()
    assert frame.unmapped == (kind == "short")


def test_rotated_and_mirrored_rgb_frames_keep_presentation_orientation(qt):
    from jarvis.appshot.recording_frame import owned_capture_image

    frame = frame_for(qt, [[(255, 0, 0, 255), (0, 255, 0, 255)],
                           [(0, 0, 255, 255), (255, 255, 255, 255)],
                           [(10, 20, 30, 255), (40, 50, 60, 255)]])
    frame.rotation_degrees = 90
    frame.mirror = True
    image = owned_capture_image(frame)
    assert (image.width(), image.height()) == (3, 2)
    assert image.pixelColor(0, 0).getRgb() == (255, 0, 0, 255)


def test_desktop_rejects_uninitialized_alpha_without_rejecting_portal_transparency(qt):
    from jarvis.appshot.recording_frame import owned_capture_image

    colors = [[(0, 43, 68, 43)] * 3] * 2
    desktop = frame_for(qt, colors)
    assert owned_capture_image(desktop, opaque_desktop=True).isNull()
    assert desktop.unmapped
    portal = frame_for(qt, colors)
    assert not owned_capture_image(portal).isNull()
