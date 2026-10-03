"""Route-D detail sheets are repaired deterministically with stdlib only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "figures" / "repair_sheet.py"


def _repair():
    name = "society_repair_sheet"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _palette() -> list[str]:
    return [
        "#102030",
        "#203040",
        "#304050",
        "#405060",
        "#506070",
        "#607080",
        "#708090",
        "#8090a0",
        "#90a0b0",
        "#a0b0c0",
        "#b0c0d0",
        "#c0d0e0",
        "#d0e0f0",
        "#e0f010",
        "#f01020",
        "#20f040",
    ]


def _noisy_png(width: int = 19, height: int = 23) -> bytes:
    repair = _repair()
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend(
                (
                    (x * 37 + y * 11) % 256,
                    (x * 13 + y * 41) % 256,
                    (x * 29 + y * 17) % 256,
                    90 if (x + y) % 7 == 0 else 230,
                )
            )
        rows.append(bytes(row))
    return repair.gt.encode_png(width, height, rows, channels=4)


def test_repair_resizes_quantizes_alpha_and_restamps_palette():
    repair = _repair()
    result = repair.repair_sheet(_noisy_png(), _palette())
    width, height, channels, rows = repair.gt.decode_png(result)

    assert (width, height, channels) == (128, 128, 4)
    pixels = {
        tuple(row[offset : offset + 4])
        for row in rows
        for offset in range(0, len(row), 4)
    }
    assert len(pixels) <= 32
    assert {pixel[3] for pixel in pixels} <= {0, 255}

    for cell, raw in enumerate(_palette()):
        rgb = tuple(bytes.fromhex(raw[1:]))
        for y in range(repair.CELL_HEIGHT):
            start = cell * repair.CELL_WIDTH * 4
            assert tuple(rows[y][start : start + 4]) == (*rgb, 255)


def test_repair_requires_exact_recipe_palette():
    repair = _repair()
    with pytest.raises(ValueError, match="exactly 16"):
        repair.repair_sheet(_noisy_png(), _palette()[:-1])
    with pytest.raises(ValueError, match="#rrggbb"):
        repair.repair_sheet(_noisy_png(), [*_palette()[:-1], "red"])


def test_face_contrast_gate_uses_only_explicit_template_bounds():
    repair = _repair()
    row = bytes([96, 96, 96, 255] * 128)
    source = repair.gt.encode_png(128, 128, [row] * 128, channels=4)

    # No face rectangle exists in contract.json today, so the generic repair
    # succeeds without pretending to know where the face is.
    repair.repair_sheet(source, _palette())

    with pytest.raises(ValueError, match="face region contrast"):
        repair.repair_sheet(
            source,
            _palette(),
            face_region=(16, 32, 32, 32),
            min_face_contrast=1.0,
        )


def test_face_region_cannot_overlap_the_palette_strip():
    repair = _repair()
    with pytest.raises(ValueError, match="detail area"):
        repair.repair_sheet(
            _noisy_png(128, 128),
            _palette(),
            face_region=(0, 0, 16, 16),
        )
