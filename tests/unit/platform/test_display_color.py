"""Display colour detection: ICC gamut classification and the safe default."""

from __future__ import annotations

import struct
import sys

import numpy as np

from jarvis.platform import display_color as dc
from jarvis.platform import win_duplication


def _icc(primaries: dict[bytes, tuple[float, float]]) -> bytes:
    """A minimal ICC body with r/g/b colorant tags at the given xy (Y = 1)."""
    tags = []
    data = b""
    offset = 132 + 12 * len(primaries)
    for sig, (x, y) in primaries.items():
        big_x, big_z = x / y, (1 - x - y) / y
        body = b"XYZ \0\0\0\0" + struct.pack(
            ">iii", round(big_x * 65536), 65536, round(big_z * 65536)
        )
        tags.append(struct.pack(">4sII", sig, offset + len(data), len(body)))
        data += body
    header = bytearray(128)
    header[36:40] = b"acsp"
    return bytes(header) + struct.pack(">I", len(tags)) + b"".join(tags) + data


def test_srgb_profile_is_srgb() -> None:
    profile = _icc(
        {b"rXYZ": (0.6484, 0.3309), b"gXYZ": (0.3212, 0.5978), b"bXYZ": (0.1559, 0.066)}
    )
    assert dc.icc_gamut(profile) == "srgb"


def test_p3_profile_is_p3() -> None:
    profile = _icc(
        {b"rXYZ": (0.6820, 0.3190), b"gXYZ": (0.2851, 0.6740), b"bXYZ": (0.1556, 0.0661)}
    )
    assert dc.icc_gamut(profile) == "p3"


def test_bt2020_profile_is_bt2020() -> None:
    profile = _icc(
        {b"rXYZ": (0.7140, 0.2970), b"gXYZ": (0.1720, 0.7710), b"bXYZ": (0.1300, 0.0510)}
    )
    assert dc.icc_gamut(profile) == "bt2020"


def test_broken_or_lut_profiles_count_as_srgb() -> None:
    assert dc.icc_gamut(None) == "srgb"
    assert dc.icc_gamut(b"short") == "srgb"
    assert dc.icc_gamut(_icc({b"rXYZ": (0.68, 0.32)})) == "srgb"


def test_unknown_platforms_report_plain_sdr(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert dc.display_color("DP-1") == dc.SDR
    assert not dc.display_color().extended
    assert dc.device_at((0, 0)) is None


def test_duplication_is_windows_only(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert not win_duplication.available()


def test_monochrome_pointer_masks() -> None:
    # 2x1 pointer: AND row then XOR row, one byte per row (pitch 1).
    # Pixel 0: AND=0 XOR=1 -> white; pixel 1: AND=1 XOR=1 -> invert.
    data = bytes([0b01000000, 0b11000000])
    rgba = win_duplication.pointer_rgba(data, 1, 2, 2, 1)
    assert rgba.shape == (1, 2, 4)
    assert rgba[0, 0].tolist() == [255, 255, 255, 255]
    assert rgba[0, 1].tolist() == [0, 0, 0, 1]


def test_colour_pointer_reserves_alpha_one_for_inversion() -> None:
    bgra = np.array([[[10, 20, 30, 255], [1, 2, 3, 1]]], np.uint8)
    rgba = win_duplication.pointer_rgba(bgra.tobytes(), 2, 2, 1, 8)
    assert rgba[0, 0].tolist() == [30, 20, 10, 255]
    assert rgba[0, 1, 3] == 0
