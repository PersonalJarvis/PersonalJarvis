"""SDR recordings say which matrix their encoder used, so players decode exact colours."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from jarvis.appshot import hdr_recording


def _decode_601(y: float, u: float, v: float) -> np.ndarray:
    kr, kb = 0.299, 0.114
    luma, cb, cr = (y - 16) / 219, (u - 128) / 224, (v - 128) / 224
    r = luma + 2 * (1 - kr) * cr
    b = luma + 2 * (1 - kb) * cb
    g = (luma - kr * r - kb * b) / (1 - kr - kb)
    return np.clip(np.array([r, g, b]) * 255, 0, 255)


def test_libx264_recording_is_tagged_with_the_matrix_it_was_converted_with(tmp_path) -> None:
    av = pytest.importorskip("av")
    if "libx264" not in av.codecs_available:
        pytest.skip("libx264 missing from this PyAV build")
    from jarvis.appshot import recording_codec

    colours = [(255, 0, 0), (0, 255, 0), (40, 90, 200)]
    image = np.zeros((64, 64 * len(colours), 4), np.uint8)
    image[..., 3] = 255
    for index, colour in enumerate(colours):
        image[:, index * 64 : (index + 1) * 64, :3] = colour
    candidates = [c for c in recording_codec._SDR_CANDIDATES if c[0] == "libx264"]
    path = tmp_path / "sdr.mp4"
    container, stream = recording_codec._open_first(
        av, path, (image.shape[1], image.shape[0]), 30, 4_000_000, candidates,
        recording_codec._tag_srgb,
    )
    for index in range(3):
        frame = av.VideoFrame.from_ndarray(image, format="rgba").reformat(format=stream.pix_fmt)
        frame.pts, frame.time_base = index * 2000, Fraction(1, 60000)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    with av.open(str(path)) as source:
        decoded = next(source.decode(video=0))
    assert (decoded.color_primaries, decoded.color_trc, decoded.colorspace) == (1, 13, 5)
    y = np.frombuffer(decoded.planes[0], np.uint8).reshape(decoded.height, -1)
    u = np.frombuffer(decoded.planes[1], np.uint8).reshape(decoded.height // 2, -1)
    v = np.frombuffer(decoded.planes[2], np.uint8).reshape(decoded.height // 2, -1)
    for index, colour in enumerate(colours):
        x = index * 64 + 32
        rgb = _decode_601(float(y[32, x]), float(u[16, x // 2]), float(v[16, x // 2]))
        assert np.abs(rgb - colour).max() <= 3


def test_hdr_video_round_trips_through_a_software_encoder(tmp_path) -> None:
    av = pytest.importorskip("av")
    if "libx265" not in av.codecs_available and "libsvtav1" not in av.codecs_available:
        pytest.skip("no software 10-bit encoder in this PyAV build")
    from jarvis.appshot.recording_codec import open_video

    path = tmp_path / "hdr.mp4"
    container, stream = open_video(av, path, (64, 64), 30, 4_000_000, hdr=True)
    y = np.full((64, 64), (64 + 500) << 6, np.uint16)
    uv = np.full((32, 32, 2), 512 << 6, np.uint16)
    for index in range(5):
        frame = hdr_recording.p010_frame(av, stream.pix_fmt, y, uv)
        frame.pts, frame.time_base = index * 2000, Fraction(1, 60000)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    with av.open(str(path)) as source:
        decoded = next(source.decode(video=0))
    assert (decoded.color_primaries, decoded.color_trc, decoded.colorspace) == (9, 16, 9)
    assert decoded.format.name in ("yuv420p10le", "p010le")


def test_clip_export_keeps_hdr_depth_and_every_colour_tag() -> None:
    from types import SimpleNamespace

    from jarvis.appshot import video_edit

    av = SimpleNamespace(codecs_available={"libsvtav1", "libx264"})
    hdr = SimpleNamespace(color_primaries=9, color_trc=16, colorspace=9, color_range=1)
    sdr = SimpleNamespace(color_primaries=1, color_trc=13, colorspace=5, color_range=1)
    assert video_edit._clip_encoder(av, hdr)[:2] == ("libsvtav1", "yuv420p10le")
    assert video_edit._clip_encoder(av, sdr)[:2] == ("libx264", "yuv420p")
    target = SimpleNamespace()
    video_edit._copy_colour_tags(hdr, target)
    assert (target.color_primaries, target.color_trc, target.colorspace) == (9, 16, 9)
