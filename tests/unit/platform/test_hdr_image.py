"""HDR masters and SDR stand-ins: the colour maths and the PNG bytes."""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest

from jarvis.platform import hdr_image


def _decode_png16(data: bytes) -> tuple[np.ndarray, dict[bytes, bytes]]:
    """Minimal reader for what encode_png16 writes (filters None and Up)."""
    pos, chunks, idat = 8, {}, b""
    while pos < len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack_from(">I", data, pos + 8 + length)
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        if kind == b"IDAT":
            idat += body
        else:
            chunks[kind] = body
        pos += 12 + length
    width, height, depth, colour = struct.unpack_from(">IIBB", chunks[b"IHDR"])
    assert (depth, colour) == (16, 2)
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, width * 6 + 1)
    rows = raw[:, 1:].copy()
    for y in range(height):
        assert raw[y, 0] in (0, 2)
        if raw[y, 0] == 2:
            rows[y] = rows[y] + rows[y - 1]
    pixels = rows.view(">u2").reshape(height, width, 3).astype(np.uint16)
    return pixels, chunks


def test_pq_hits_the_standard_reference_points() -> None:
    signal = hdr_image.pq_encode(np.array([0.0, 100.0, 1000.0, 10000.0]))
    # ST 2084 reference values: 100 nits ~ 0.508, 1000 nits ~ 0.752.
    assert signal[0] == pytest.approx(0.0, abs=1e-6)
    assert signal[1] == pytest.approx(0.5081, abs=1e-3)
    assert signal[2] == pytest.approx(0.7518, abs=1e-3)
    assert signal[3] == pytest.approx(1.0, abs=1e-6)


def test_pq_round_trips() -> None:
    nits = np.array([0.05, 1.0, 80.0, 284.0, 455.0, 4000.0], dtype=np.float32)
    back = hdr_image.pq_decode(hdr_image.pq_encode(nits))
    assert np.allclose(back, nits, rtol=2e-3)


def test_sdr_white_becomes_srgb_white_and_highlights_clip() -> None:
    white = 284.0 / hdr_image.SCRGB_NITS
    scrgb = np.array([[[white, white, white, 1.0], [2 * white, 0.0, 0.0, 1.0]]], np.float16)
    srgb = hdr_image.scrgb_to_srgb8(scrgb, 284.0)
    assert srgb[0, 0].tolist() == [255, 255, 255]
    assert srgb[0, 1].tolist() == [255, 0, 0]


def test_srgb_mid_grey_survives_the_trip_through_scrgb() -> None:
    grey = np.full((1, 1, 3), 128, np.uint8)
    scrgb = hdr_image.srgb8_to_scrgb(grey, 200.0)
    assert hdr_image.scrgb_to_srgb8(scrgb, 200.0)[0, 0].tolist() == [128, 128, 128]


def test_wide_gamut_red_stays_inside_bt2020() -> None:
    # A red beyond sRGB arrives with negative green/blue in scRGB.
    scrgb = np.array([[[1.3, -0.05, -0.02, 1.0]]], np.float32)
    nits = hdr_image.scrgb_to_bt2020_nits(scrgb)
    assert (nits >= 0).all()
    assert nits[0, 0, 0] > nits[0, 0, 1] > 0


def test_sdr_white_lands_on_pq_reference_white() -> None:
    # A white web page on a monitor with SDR white at 284 nits must reach
    # viewers at PQ reference white, not 284 nits (which they show 1.4x
    # brighter than their own SDR white).
    white = 284.0 / hdr_image.SCRGB_NITS
    scrgb = np.array([[[white, white, white, 1.0], [2 * white, 2 * white, 2 * white, 1.0]]])
    pq = hdr_image.scrgb_to_pq16(scrgb, 284.0)
    nits = hdr_image.pq_decode(pq / 65535.0)
    assert nits[0, 0].tolist() == pytest.approx([203.0] * 3, rel=2e-3)
    assert nits[0, 1].tolist() == pytest.approx([406.0] * 3, rel=2e-3)
    max_cll, max_fall = hdr_image.light_levels(scrgb, 284.0)
    assert max_cll == pytest.approx(406.0, rel=1e-3)
    assert max_fall == pytest.approx(304.5, rel=1e-3)


def test_png16_carries_cicp_light_levels_and_exact_pixels() -> None:
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 65536, size=(5, 7, 3), dtype=np.uint16)
    data = hdr_image.encode_png16(pixels, light=(455.5, 12.25))
    decoded, chunks = _decode_png16(data)
    assert np.array_equal(decoded, pixels)
    assert chunks[b"cICP"] == bytes((9, 16, 0, 1))
    assert struct.unpack(">II", chunks[b"cLLi"]) == (4555000, 122500)
    assert hdr_image.read_png_cicp(data) == (9, 16, 0, 1)


def test_markings_lay_over_hdr_at_sdr_white() -> None:
    base = np.zeros((1, 2, 4), np.float32)
    overlay = np.array([[[255, 255, 255, 255], [0, 0, 0, 0]]], np.uint8)
    out = hdr_image.composite_over_scrgb(base, overlay, 240.0)
    assert out[0, 0].tolist() == pytest.approx([3.0, 3.0, 3.0])
    assert out[0, 1].tolist() == [0.0, 0.0, 0.0]


def test_png8_embeds_the_icc_profile() -> None:
    from io import BytesIO

    from PIL import Image

    profile = b"\0" * 36 + b"acsp" + b"\0" * 200
    data = hdr_image.encode_png8(np.zeros((2, 2, 3), np.uint8), profile)
    assert Image.open(BytesIO(data)).info.get("icc_profile") == profile
    assert hdr_image.read_png_cicp(data) is None
