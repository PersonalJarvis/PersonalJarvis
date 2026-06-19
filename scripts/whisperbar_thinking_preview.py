"""Headless preview of the whisper-bar THINKING animation (orbital core).

Renders frames straight from the pure renderer (no Tk, no app) and writes:
- ``screenshots/whisperbar-thinking-sheet.png`` — 8 frames, 4x upscaled,
  composited on a desktop-dark background for design review.
- ``screenshots/whisperbar-thinking.gif``       — ~3 s animation at 4x.

Usage:  python scripts/whisperbar_thinking_preview.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core.screenshots import screenshots_dir  # noqa: E402
from jarvis.ui.whisperbar import renderer as R  # noqa: E402

DESKTOP_BG = (32, 31, 34)  # neutral dark backdrop standing in for the desktop
UPSCALE = 4


def _frame(rnd: R.WhisperBarRenderer, t: float) -> Image.Image:
    img = rnd.render(t, "think", 0.0)
    # Swap the magenta color-key for a desktop-ish dark grey so the preview
    # shows what the keyed-out window actually looks like in place.
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            if px[x, y] == R.COLOR_KEY_RGB:
                px[x, y] = DESKTOP_BG
    return img.resize(
        (img.width * UPSCALE, img.height * UPSCALE), Image.Resampling.LANCZOS
    )


def main() -> None:
    out = screenshots_dir()
    rnd = R.WhisperBarRenderer()
    for _ in range(80):  # settle the pill ease at ACTIVE size
        rnd.render(0.0, "think", 0.0)

    # Contact sheet: 8 moments across ~2.6 s.
    times = [1.0 + k * 0.37 for k in range(8)]
    frames = [_frame(rnd, t) for t in times]
    fw, fh = frames[0].size
    pad = 8
    sheet = Image.new("RGB", (fw * 4 + pad * 5, fh * 2 + pad * 3), (18, 18, 20))
    for i, fr in enumerate(frames):
        col, row = i % 4, i // 4
        sheet.paste(fr, (pad + col * (fw + pad), pad + row * (fh + pad)))
    sheet_path = out / "whisperbar-thinking-sheet.png"
    sheet.save(sheet_path)

    # Animation: 60 frames over ~3 s (50 ms cadence in the GIF).
    anim = [_frame(rnd, 1.0 + k * 0.05) for k in range(60)]
    gif_path = out / "whisperbar-thinking.gif"
    anim[0].save(
        gif_path, save_all=True, append_images=anim[1:], duration=50, loop=0
    )

    print(f"sheet: {sheet_path}")
    print(f"gif:   {gif_path}")


if __name__ == "__main__":
    main()
