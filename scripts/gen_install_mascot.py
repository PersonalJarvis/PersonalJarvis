#!/usr/bin/env python3
"""Generate the installer banner mascot (the gold-eyed ghost) for both
Stage-1 bootstrap scripts.

Why this exists: the first mascot was machine-downscaled from the brand PNG,
which quantized the anti-aliased glow into muddy olive fringe pixels and
scattered "sparkle" specks — on a light terminal (the macOS Terminal.app
default) it rendered as a shapeless dark blob. Terminal art has to be DRAWN
for the medium: a small, deliberate pixel grid with a hard silhouette and a
flat three-color palette that reads on light AND dark backgrounds.

The sprite lives here as a human-editable pixel grid. Run this script and
paste its output into the two installers rather than hand-editing escapes:

    python scripts/gen_install_mascot.py sh    # printf lines for install.sh
    python scripts/gen_install_mascot.py ps1   # base64 blob for install.ps1
    python scripts/gen_install_mascot.py png OUT.png [--bg light|dark]
                                               # visual proof render

Rendering model: half-block art — every terminal row carries two pixel rows
via U+2580/U+2584/U+2588. Cells whose two pixels share a color use a plain
foreground block (no background escape), so the terminal's own background
shows through everywhere outside the silhouette and the art adapts to any
theme. Colors are xterm-256 (every modern terminal, incl. macOS
Terminal.app, renders them; truecolor is NOT assumed).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

# Palette: '.' transparent, K body black, G outline gold, Y bright gold.
# 233 = near-black body (crisp icon on light terminals); 178 = calm outline
# gold that defines the silhouette on dark terminals (the brand PNG works the
# same way); 220 = bright gold for eyes, mouth and the signature zigzag.
PALETTE = {"K": 233, "G": 178, "Y": 220}

# 26 columns x 28 pixel rows (= 14 terminal rows) — slightly taller than
# wide, like the brand ghost. Even row count is required by the half-block
# pairing. Features top to bottom: 1px gold dome outline, two 5x7 gold eyes
# with offset pupils, a small gold "o" mouth, the signature 3-tooth zigzag
# (2px thick so it survives half-block pairing), and the jagged feet.
GRID = [
    ".........GGGGGGGG.........",
    "......GGGKKKKKKKKGGG......",
    "....GGKKKKKKKKKKKKKKGG....",
    "...GKKKKKKKKKKKKKKKKKKG...",
    "..GKKKKKKKKKKKKKKKKKKKKG..",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    ".GKKKKYYYKKKKKKKKYYYKKKKG.",
    ".GKKKYYYYYKKKKKKYYYYYKKKG.",
    ".GKKKYYYYYKKKKKKYYYYYKKKG.",
    ".GKKKYYKKYKKKKKKYKKYYKKKG.",
    ".GKKKYYKKYKKKKKKYKKYYKKKG.",
    ".GKKKYYYYYKKKKKKYYYYYKKKG.",
    ".GKKKKYYYKKKKKKKKYYYKKKKG.",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    ".GKKKKKKKKKKYYKKKKKKKKKKG.",
    ".GKKKKKKKKKYKKYKKKKKKKKKG.",
    ".GKKKKKKKKKYKKYKKKKKKKKKG.",
    ".GKKKKKKKKKKYYKKKKKKKKKKG.",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    ".YYKKKKYYYYKKKKYYYYKKKKYY.",
    ".GYYKKYYKKYYKKYYKKYYKKYYG.",
    ".GKYYYYKKKKYYYYKKKKYYYYKG.",
    ".GKKYYKKKKKKYYKKKKKKYYKKG.",
    ".GKKKKKKKKKKKKKKKKKKKKKKG.",
    "..KKKK..KKKK..KKKK..KKKK..",
]

# Left indent that centers the 28-col sprite over the 67-col wordmark.
INDENT = 20

ESC = "\x1b"


def _validate() -> None:
    if len(GRID) % 2 != 0:
        raise SystemExit("GRID needs an even number of pixel rows")
    width = len(GRID[0])
    for i, row in enumerate(GRID):
        if len(row) != width:
            raise SystemExit(f"GRID row {i} has width {len(row)} != {width}")
        bad = set(row) - set(PALETTE) - {"."}
        if bad:
            raise SystemExit(f"GRID row {i} has unknown cells: {bad}")


def _cells(top_row: str, bot_row: str) -> list[tuple[str, int | None, int | None]]:
    """Map a pixel-row pair to (glyph, fg, bg) cells; None = terminal default."""
    out: list[tuple[str, int | None, int | None]] = []
    for t_ch, b_ch in zip(top_row, bot_row):
        t = PALETTE.get(t_ch)
        b = PALETTE.get(b_ch)
        if t is None and b is None:
            out.append((" ", None, None))
        elif t == b:
            out.append(("█", t, None))  # full block, fg only
        elif b is None:
            out.append(("▀", t, None))  # upper half, lower = terminal bg
        elif t is None:
            out.append(("▄", b, None))  # lower half, upper = terminal bg
        else:
            out.append(("▀", t, b))
    return out


def _row_ansi(cells: list[tuple[str, int | None, int | None]], esc: str) -> str:
    """One terminal row: adjacent same-color cells merged into single runs."""
    parts: list[str] = []
    i = 0
    while i < len(cells):
        glyphs = cells[i][0]
        fg, bg = cells[i][1], cells[i][2]
        j = i + 1
        while j < len(cells) and (cells[j][1], cells[j][2]) == (fg, bg):
            glyphs += cells[j][0]
            j += 1
        if fg is None:
            parts.append(glyphs)
        elif bg is None:
            parts.append(f"{esc}[38;5;{fg}m{glyphs}{esc}[0m")
        else:
            parts.append(f"{esc}[38;5;{fg};48;5;{bg}m{glyphs}{esc}[0m")
        i = j
    return " " * INDENT + "".join(parts).rstrip()


def rows(esc: str = ESC) -> list[str]:
    _validate()
    return [
        _row_ansi(_cells(GRID[i], GRID[i + 1]), esc)
        for i in range(0, len(GRID), 2)
    ]


def emit_sh() -> str:
    """printf lines for install.sh — escapes stay literal text, %b expands."""
    return "\n".join(f"    printf '%b\\n' '{row}'" for row in rows(esc="\\033"))


def emit_ps1() -> str:
    """Base64 UTF-8 blob (real ESC bytes) so install.ps1 stays pure ASCII."""
    text = "\n".join(rows())
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    wrapped = "\n".join(b64[i : i + 96] for i in range(0, len(b64), 96))
    return wrapped


def emit_png(path: Path, bg: str) -> None:
    """Proof render of the raw pixel grid (needs Pillow; dev-machine only)."""
    from PIL import Image  # noqa: PLC0415 - optional dev dependency

    rgb = {233: (18, 18, 18), 178: (215, 175, 0), 220: (255, 215, 0)}
    bg_rgb = (245, 245, 245) if bg == "light" else (12, 12, 12)
    scale = 12
    img = Image.new("RGB", (len(GRID[0]) * scale, len(GRID) * scale), bg_rgb)
    px = img.load()
    for y, row in enumerate(GRID):
        for x, ch in enumerate(row):
            color = rgb.get(PALETTE.get(ch, -1))
            if color is None:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    px[x * scale + dx, y * scale + dy] = color
    img.save(path)


def main(argv: list[str]) -> int:
    # Windows consoles default to a legacy codepage (cp1252) that cannot
    # encode the block glyphs — force UTF-8 stdout on every platform.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mode = argv[1] if len(argv) > 1 else "sh"
    if mode == "sh":
        print(emit_sh())
    elif mode == "ps1":
        print(emit_ps1())
    elif mode == "png":
        out = Path(argv[2])
        bg = argv[4] if len(argv) > 4 and argv[3] == "--bg" else "light"
        emit_png(out, bg)
        print(f"wrote {out}")
    elif mode == "ansi":
        # Direct terminal preview (run in a real terminal to eyeball it).
        print("\n".join(rows()))
    else:
        raise SystemExit(f"unknown mode: {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
