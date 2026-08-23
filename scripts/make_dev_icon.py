"""Render the dev-instance app icon: the mascot with a signal-yellow DEV tag.

    python scripts/make_dev_icon.py

Writes ``jarvis-dev.png`` (256 px) and ``jarvis-dev.ico`` (16…256 px) next to
the default icon in BOTH icon homes — the in-package copy that ships with the
code (``jarvis/assets/icons/``) and the build-tool copy (``assets/icons/``).
``jarvis.core.instance`` names these files; ``jarvis.assets.bundled_app_icon``
and ``jarvis.ui.icon_utils.project_icon_path`` resolve them for the dev app.

The tag is drawn at 4× and downsampled so it stays crisp at every size. At
16 px the letters are gone but the yellow corner remains — that is the whole
point: the two taskbar buttons must be told apart at a glance.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PNG = ROOT / "jarvis" / "assets" / "icons" / "jarvis.png"
TARGET_DIRS = (ROOT / "jarvis" / "assets" / "icons", ROOT / "assets" / "icons")
PNG_NAME = "jarvis-dev.png"
ICO_NAME = "jarvis-dev.ico"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

YELLOW = (255, 214, 10, 255)
INK = (10, 10, 10, 255)
LABEL = "DEV"


def _bold_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(source: Path, size: int = 256) -> Image.Image:
    scale = 4
    big = size * scale
    base = Image.open(source).convert("RGBA").resize((big, big), Image.LANCZOS)

    # Tag geometry (relative to the icon): a rounded pill in the lower-right
    # corner, wide enough for the word with comfortable padding, on a thin dark
    # outline so it still separates from a light desktop or a light taskbar.
    font = _bold_font(int(big * 0.20))
    probe = ImageDraw.Draw(base)
    tw = probe.textlength(LABEL, font=font)
    bbox = font.getbbox(LABEL)
    th = bbox[3] - bbox[1]
    pad_x = int(big * 0.045)
    pad_y = int(big * 0.035)
    w = int(tw) + 2 * pad_x
    h = th + 2 * pad_y
    x1 = big - int(big * 0.03)
    y1 = big - int(big * 0.03)
    x0 = x1 - w
    y0 = y1 - h
    radius = int(h * 0.28)
    outline = max(2, int(big * 0.008))

    tag = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(tag)
    d.rounded_rectangle(
        (x0 - outline, y0 - outline, x1 + outline, y1 + outline),
        radius=radius + outline,
        fill=INK,
    )
    d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=YELLOW)
    d.text(
        (x0 + pad_x, y0 + pad_y - bbox[1]),
        LABEL,
        font=font,
        fill=INK,
    )
    base.alpha_composite(tag)
    return base.resize((size, size), Image.LANCZOS)


def main() -> int:
    if not SOURCE_PNG.is_file():
        print(f"source icon missing: {SOURCE_PNG}", file=sys.stderr)
        return 1
    master = render(SOURCE_PNG, 256)
    for target_dir in TARGET_DIRS:
        target_dir.mkdir(parents=True, exist_ok=True)
        png = target_dir / PNG_NAME
        ico = target_dir / ICO_NAME
        master.save(png, format="PNG")
        master.save(ico, format="ICO", sizes=ICO_SIZES)
        print(f"[ok] {png}\n[ok] {ico}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
