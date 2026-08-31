"""Render the desktop app icon: original Gigi on a white rounded tile.

    python scripts/make_gigi_app_icon.py

Reads ``jarvis/ui/web/frontend/public/jarvis-logo.png`` (the original
black-and-white ghost, transparent around the body), composites it onto a
white squircle without recoloring, and writes PNG + ICO copies that every
surface shares: in-package icons, repo-root icons, and the web favicon.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-logo.png"
MASTER = 1024
GHOST_RATIO = 0.78
CORNER = 0.22
WHITE = (255, 255, 255, 255)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def render_tile(size: int) -> Image.Image:
    ghost = Image.open(SOURCE).convert("RGBA")
    ghost = ghost.resize(
        (max(1, int(size * GHOST_RATIO)), max(1, int(size * GHOST_RATIO))),
        Image.Resampling.LANCZOS,
    )
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    inset = 0
    draw.rounded_rectangle(
        (inset, inset, size - 1 - inset, size - 1 - inset),
        radius=int(size * CORNER),
        fill=WHITE,
    )
    x = (size - ghost.width) // 2
    y = (size - ghost.height) // 2
    tile.alpha_composite(ghost, (x, y))
    return tile


def write_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def write_ico(path: Path, master: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    master.save(path, format="ICO", sizes=ICO_SIZES)


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing source logo: {SOURCE}")
    master = render_tile(MASTER)
    png256 = master.resize((256, 256), Image.Resampling.LANCZOS)
    write_png(ROOT / "assets" / "icons" / "jarvis-1024.png", master)
    targets_png = [
        ROOT / "jarvis" / "assets" / "icons" / "jarvis.png",
        ROOT / "assets" / "icons" / "jarvis-gigi-256.png",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-gigi-256.png",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-mark-256.png",
    ]
    targets_ico = [
        ROOT / "jarvis" / "assets" / "icons" / "jarvis.ico",
        ROOT / "assets" / "icons" / "jarvis.ico",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis.ico",
        ROOT / "jarvis" / "ui" / "web" / "frontend" / "public" / "jarvis-gigi.ico",
    ]
    for path in targets_png:
        write_png(path, png256)
    for path in targets_ico:
        write_ico(path, master)
    print(f"wrote {len(targets_png)} png and {len(targets_ico)} ico from {SOURCE.name}")


if __name__ == "__main__":
    main()
