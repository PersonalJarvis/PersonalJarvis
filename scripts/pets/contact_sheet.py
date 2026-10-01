#!/usr/bin/env python3
"""Render every built-in pet x state x frame for a visual check.

Frames go through the REAL runtime path: :func:`jarvis.ui.pets.loader.load_pet`
decodes the committed sheet and :func:`~jarvis.ui.pets.loader.to_color_key`
scales it onto the overlay's colour key, which is then replaced by a dark
(``#1e1e1e``) and a light (``#f2f2f2``) desktop colour. One PNG per background.

Usage::

    python scripts/pets/contact_sheet.py --out DIR [--scale 4] [--pets gigi,miso] [--split]

``--split`` additionally writes one image per pet and background, which is
easier to inspect than the tall combined sheet.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Run from anywhere: import THIS checkout's jarvis, never an installed copy.
sys.path.insert(0, str(REPO_ROOT))

from PIL import Image, ImageChops, ImageDraw, ImageFont  # noqa: E402

from jarvis.ui.pets.loader import (  # noqa: E402
    COLOR_KEY,
    PetPack,
    builtin_root,
    list_pets,
    load_pet,
    to_color_key,
)
from jarvis.ui.pets.states import MAX_FRAMES_PER_STATE, PET_STATES  # noqa: E402

BACKGROUNDS: dict[str, tuple[int, int, int]] = {
    "dark": (0x1E, 0x1E, 0x1E),
    "light": (0xF2, 0xF2, 0xF2),
}
LABEL_WIDTH = 150
GAP = 6


def _keyed_to_background(keyed: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """Replace the exact colour key with ``background`` (what the overlay window does)."""
    mask = Image.new("L", keyed.size, 255)
    for channel, value in zip(keyed.split(), COLOR_KEY, strict=True):
        hit = channel.point(lambda v, value=value: 255 if v == value else 0)
        mask = ImageChops.multiply(mask, hit)
    out = keyed.copy()
    out.paste(background, mask=mask)
    return out


def render(packs: Sequence[PetPack], background: tuple[int, int, int], scale: int) -> Image.Image:
    cell = 48 * scale
    rows = len(packs) * len(PET_STATES)
    width = LABEL_WIDTH + MAX_FRAMES_PER_STATE * (cell + GAP)
    height = rows * (cell + GAP) + GAP
    sheet = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    ink = (230, 230, 230) if sum(background) < 384 else (40, 40, 40)
    row = 0
    for pack in packs:
        for state in PET_STATES:
            top = GAP + row * (cell + GAP)
            resolved, spec = pack.manifest.spec_for(state)
            label = f"{pack.manifest.id}\n{state}\n{spec.frames}f @ {spec.fps}fps"
            if resolved != state:
                label += f"\n(uses {resolved})"
            draw.multiline_text((8, top + 8), label, fill=ink, font=font)
            for col, frame in enumerate(pack.frames[state]):
                frame_size = pack.manifest.frame_size
                keyed = to_color_key(frame, scale * 48 // frame_size)
                tile = _keyed_to_background(keyed, background)
                sheet.paste(tile, (LABEL_WIDTH + col * (cell + GAP), top))
            row += 1
    return sheet


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    parser.add_argument("--scale", type=int, default=4, help="integer zoom (default 4)")
    parser.add_argument("--pets", default="", help="comma-separated ids (default: all)")
    parser.add_argument("--split", action="store_true", help="also write one image per pet")
    args = parser.parse_args(argv)

    wanted = {p for p in args.pets.split(",") if p}
    packs = [
        load_pet(builtin_root() / m.id, builtin=True)
        for m in list_pets()
        if m.builtin and (not wanted or m.id in wanted)
    ]
    if not packs:
        print("no matching built-in pets", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for name, background in BACKGROUNDS.items():
        path = args.out / f"pets-{name}.png"
        render(packs, background, args.scale).save(path)
        print(path)
        if args.split:
            for pack in packs:
                path = args.out / f"{pack.manifest.id}-{name}.png"
                render([pack], background, args.scale).save(path)
                print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
