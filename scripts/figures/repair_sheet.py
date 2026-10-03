"""Deterministic validate-and-repair step for society detail sheets.

Route D in docs/agent-society/character-pipeline.md may receive a noisy PNG
from any image producer. This module turns it into the exact texture contract
without Pillow or network access:

* nearest-neighbour resize to the contract sheet size;
* 1-bit alpha;
* at most 32 RGBA colours, reserving the recipe palette colours;
* the 16 palette cells are re-stamped exactly;
* optional face-region contrast rejection when a UV template supplies bounds.

The contract currently defines the palette strip and the detail area, but not a
face rectangle. Callers therefore MUST pass face_region to enable the contrast
gate; this module never invents UV coordinates.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import glb_tools as gt  # noqa: E402

CONTRACT = json.loads((HERE / "contract.json").read_text(encoding="utf-8"))
SHEET = CONTRACT["sheet"]
SIZE = int(SHEET["size"])
CELL_WIDTH = int(SHEET["cell_width"])
CELL_HEIGHT = int(SHEET["cell_height"])
CELL_COUNT = len(SHEET["cells"])
MAX_COLORS = 32


def _parse_hex(value: str) -> tuple[int, int, int]:
    raw = value.removeprefix("#")
    if len(raw) != 6:
        raise ValueError(f"palette colour must be #rrggbb, got {value!r}")
    try:
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"palette colour must be #rrggbb, got {value!r}") from exc


def _rgba(row: bytes, x: int, channels: int) -> tuple[int, int, int, int]:
    offset = x * channels
    px = row[offset : offset + channels]
    if channels == 4:
        return px[0], px[1], px[2], px[3]
    if channels == 3:
        return px[0], px[1], px[2], 255
    if channels == 2:
        return px[0], px[0], px[0], px[1]
    if channels == 1:
        return px[0], px[0], px[0], 255
    raise ValueError(f"unsupported PNG channel count {channels}")


def _nearest_resize(
    width: int,
    height: int,
    channels: int,
    rows: list[bytes],
) -> list[list[tuple[int, int, int, int]]]:
    if width <= 0 or height <= 0:
        raise ValueError("sheet dimensions must be positive")
    resized: list[list[tuple[int, int, int, int]]] = []
    for y in range(SIZE):
        source_y = min(height - 1, (y * height) // SIZE)
        row: list[tuple[int, int, int, int]] = []
        for x in range(SIZE):
            source_x = min(width - 1, (x * width) // SIZE)
            r, g, b, a = _rgba(rows[source_y], source_x, channels)
            row.append((r, g, b, 255 if a >= 128 else 0))
        resized.append(row)
    return resized


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True))


def _quantize(
    pixels: list[list[tuple[int, int, int, int]]],
    palette_rgb: list[tuple[int, int, int]],
) -> None:
    detail = [px for row in pixels[CELL_HEIGHT:] for px in row]
    has_transparency = any(px[3] == 0 for px in detail)
    opaque_limit = MAX_COLORS - (1 if has_transparency else 0)

    targets: list[tuple[int, int, int]] = []
    for color in palette_rgb:
        if color not in targets:
            targets.append(color)
    if len(targets) > opaque_limit:
        raise ValueError("recipe palette leaves no room for the transparency colour")

    counts = Counter((r, g, b) for r, g, b, a in detail if a == 255)
    for color, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if color not in targets:
            targets.append(color)
        if len(targets) >= opaque_limit:
            break
    if not targets:
        targets.append((0, 0, 0))

    cache: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for y in range(CELL_HEIGHT, SIZE):
        for x in range(SIZE):
            r, g, b, a = pixels[y][x]
            if a == 0:
                pixels[y][x] = (0, 0, 0, 0)
                continue
            source = (r, g, b)
            mapped = cache.get(source)
            if mapped is None:
                mapped = min(targets, key=lambda target: (_distance(source, target), target))
                cache[source] = mapped
            pixels[y][x] = (*mapped, 255)


def _stamp_palette(
    pixels: list[list[tuple[int, int, int, int]]],
    palette_rgb: list[tuple[int, int, int]],
) -> None:
    if CELL_COUNT * CELL_WIDTH != SIZE:
        raise ValueError("contract palette cells do not span the sheet width")
    for cell, color in enumerate(palette_rgb):
        start = cell * CELL_WIDTH
        for y in range(CELL_HEIGHT):
            for x in range(start, start + CELL_WIDTH):
                pixels[y][x] = (*color, 255)


def _face_contrast(
    pixels: list[list[tuple[int, int, int, int]]],
    region: tuple[int, int, int, int],
) -> float:
    x, y, width, height = region
    outside = (
        width <= 0
        or height <= 0
        or x < 0
        or y < CELL_HEIGHT
        or x + width > SIZE
        or y + height > SIZE
    )
    if outside:
        raise ValueError("face region must be inside the detail area")
    levels: list[float] = []
    for py in range(y, y + height):
        for px in range(x, x + width):
            r, g, b, a = pixels[py][px]
            if a:
                levels.append((299 * r + 587 * g + 114 * b) / 1000)
    if not levels:
        return 0.0
    return max(levels) - min(levels)


def repair_sheet(
    data: bytes,
    palette: Iterable[str],
    *,
    face_region: tuple[int, int, int, int] | None = None,
    min_face_contrast: float = 24.0,
) -> bytes:
    """Return a contract-sized repaired PNG or raise ValueError."""
    if not math.isfinite(min_face_contrast) or min_face_contrast < 0:
        raise ValueError("minimum face contrast must be a finite non-negative number")

    palette_rgb = [_parse_hex(value) for value in palette]
    if len(palette_rgb) != CELL_COUNT:
        raise ValueError(f"recipe palette must contain exactly {CELL_COUNT} colours")

    width, height, channels, rows = gt.decode_png(data)
    pixels = _nearest_resize(width, height, channels, rows)
    _quantize(pixels, palette_rgb)
    _stamp_palette(pixels, palette_rgb)

    if face_region is not None:
        contrast = _face_contrast(pixels, face_region)
        if contrast < min_face_contrast:
            raise ValueError(
                f"face region contrast {contrast:.1f} is below floor {min_face_contrast:.1f}"
            )

    encoded_rows = [bytes(channel for pixel in row for channel in pixel) for row in pixels]
    return gt.encode_png(SIZE, SIZE, encoded_rows, channels=4)


def _region(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("face region must be x,y,width,height") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("face region must be x,y,width,height")
    return parts  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--palette",
        nargs=CELL_COUNT,
        required=True,
        metavar="HEX",
        help="the 16 recipe colours in contract cell order",
    )
    parser.add_argument(
        "--face-region",
        type=_region,
        help="optional x,y,width,height bounds from the UV template",
    )
    parser.add_argument("--min-face-contrast", type=float, default=24.0)
    args = parser.parse_args()

    repaired = repair_sheet(
        args.input.read_bytes(),
        args.palette,
        face_region=args.face_region,
        min_face_contrast=args.min_face_contrast,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(repaired)
    print(f"repair_sheet: {args.output} ({SIZE}x{SIZE}, <= {MAX_COLORS} colours)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
