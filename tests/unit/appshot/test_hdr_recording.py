"""HDR recording: P010 frames, HDR10 tags and the SDR fallback decision."""

from __future__ import annotations

import sys

import numpy as np
import pytest

from jarvis.appshot import hdr_recording


def _planes(width: int = 8, height: int = 4) -> tuple[np.ndarray, np.ndarray]:
    y = (np.arange(width * height, dtype=np.uint16).reshape(height, width) * 7 + 64) << 6
    uv = np.full((height // 2, width // 2, 2), 512 << 6, np.uint16)
    uv[..., 0] += 3 << 6
    return y, uv


@pytest.mark.parametrize("pixel_format", ["p010le", "yuv420p10le"])
def test_p010_frame_keeps_every_code_and_is_tagged_hdr10(pixel_format: str) -> None:
    av = pytest.importorskip("av")
    y, uv = _planes()
    frame = hdr_recording.p010_frame(av, pixel_format, y, uv)
    assert (frame.width, frame.height, frame.format.name) == (8, 4, pixel_format)
    assert (frame.color_primaries, frame.color_trc, frame.colorspace, frame.color_range) == (
        9, 16, 9, 1
    )
    back = frame.reformat(format="p010le")
    luma = np.frombuffer(back.planes[0], np.uint16).reshape(4, -1)[:, :8]
    assert np.array_equal(luma >> 6, y >> 6)


def test_sdr_monitors_and_other_platforms_keep_the_8_bit_path(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert hdr_recording.choose_hdr_capture(1234, (0, 0, 1, 1), "1080p", 60) is None
    assert hdr_recording.choose_hdr_capture(None, (0, 0, 1, 1), "1080p", 60) is None


def test_hdr10_tags_are_bt2020_pq_limited() -> None:
    class Context:
        pass

    context = Context()
    hdr_recording.tag_hdr10(context)
    assert (context.color_primaries, context.color_trc, context.colorspace) == (9, 16, 9)
    assert context.color_range == 1
