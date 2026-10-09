"""The app's neutral popover, Inter labels and compact keyboard hints in RGBA."""

from __future__ import annotations

import functools
import math
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from jarvis.ui.theme import POPOVER_COLORS

PADDING = 8
ROW_HEIGHT = 36
SEPARATOR_HEIGHT = 8


@dataclass(frozen=True)
class MenuEntry:
    label: str
    shortcut: str = ""
    enabled: bool = True
    icon: str = "open"


@functools.lru_cache(maxsize=32)
def _font(px: int, mono: bool = False, cjk: bool = False):
    assets = Path(__file__).resolve().parents[1] / "web" / "dist" / "assets"
    pattern = (
        "jetbrains-mono-latin-500-normal-*.woff2" if mono else "inter-latin-wght-normal-*.woff2"
    )
    candidates = list(assets.glob(pattern))
    if cjk:
        candidates = [
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        ] + candidates
    for path in candidates:
        try:
            font = ImageFont.truetype(str(path), px)
            if not mono and not cjk:
                try:
                    font.set_variation_by_axes([500])
                except (OSError, AttributeError):
                    # Static font builds already carry their intended weight.
                    pass
            return font
        except OSError:
            # Some FreeType builds lack WOFF2; the system UI face is the fallback.
            continue
    from ui.orb.pet_cards import font as system_font

    return system_font("regular", px)


def row_bounds(entries: list[MenuEntry | None]) -> list[tuple[int, int]]:
    y = PADDING
    bounds = []
    for entry in entries:
        height = SEPARATOR_HEIGHT if entry is None else ROW_HEIGHT
        bounds.append((y, y + height))
        y += height
    return bounds


def menu_size(entries: list[MenuEntry | None], scale: float = 1.0) -> tuple[int, int]:
    width = 240.0
    for entry in entries:
        if entry is None:
            continue
        face = _font(14, cjk=any(ord(c) > 0x2E80 for c in entry.label))
        hint = float(_font(12, mono=True).getlength(entry.shortcut)) if entry.shortcut else 0
        width = max(
            width, 40 + float(face.getlength(entry.label)) + (hint + 28 if hint else 0) + 16
        )
    width = math.ceil(width / 4) * 4
    bounds = row_bounds(entries)
    height = (bounds[-1][1] if bounds else PADDING) + PADDING
    return round(width * scale), round(height * scale)


def hit_test(entries: list[MenuEntry | None], x: float, y: float, width: float) -> int | None:
    if not PADDING <= x < width - PADDING:
        return None
    for index, (top, bottom) in enumerate(row_bounds(entries)):
        entry = entries[index]
        if top <= y < bottom and entry is not None and entry.enabled:
            return index
    return None


def _icon(draw, name, x, y, scale, color):
    def line(points):
        draw.line(
            [(round(x + a * scale), round(y + b * scale)) for a, b in points],
            fill=color,
            width=max(1, round(1.5 * scale)),
            joint="curve",
        )

    if name == "open":
        line([(9, 2), (14, 2), (14, 7)])
        line([(7, 9), (14, 2)])
        line([(6, 3), (2, 3), (2, 14), (13, 14), (13, 10)])
    elif name == "reset":
        box = [
            round(x + 3 * scale),
            round(y + 3 * scale),
            round(x + 14 * scale),
            round(y + 14 * scale),
        ]
        draw.arc(box, 210, 510, fill=color, width=max(1, round(1.5 * scale)))
        line([(2, 2), (2, 7), (7, 7)])
    else:
        line([(1, 8), (4, 4), (8, 3), (12, 4), (15, 8), (12, 12), (8, 13), (4, 12), (1, 8)])
        line([(2, 2), (14, 14)])


def render_menu(
    entries: list[MenuEntry | None],
    theme: str = "dark",
    *,
    selected: int | None = None,
    keyboard: bool = False,
    scale: float = 1.0,
) -> Image.Image:
    """Render exactly the pixels used by the desktop popup (also usable in previews)."""
    colors = POPOVER_COLORS.get(theme, POPOVER_COLORS["dark"])
    size = menu_size(entries, scale)
    zoom = scale * 3
    width, height = menu_size(entries)
    image = Image.new("RGBA", (round(width * zoom), round(height * zoom)))
    draw = ImageDraw.Draw(image)

    def rect(box, radius, fill, outline=None):
        draw.rounded_rectangle(
            tuple(round(v * zoom) for v in box),
            round(radius * zoom),
            fill=fill,
            outline=outline,
            width=max(1, round(zoom)),
        )

    rect((0.5, 0.5, width - 0.5, height - 0.5), 12, colors["background"], colors["border"])
    for index, (top, bottom) in enumerate(row_bounds(entries)):
        entry = entries[index]
        if entry is None:
            draw.line(
                (
                    round(16 * zoom),
                    round((top + 4) * zoom),
                    round((width - 16) * zoom),
                    round((top + 4) * zoom),
                ),
                fill=colors["border"],
                width=max(1, round(zoom)),
            )
            continue
        if index == selected and entry.enabled:
            fill = colors["hover"]
            if keyboard:
                bg, accent = (
                    ImageColor.getrgb(colors["background"]),
                    ImageColor.getrgb(colors["accent"]),
                )
                fill = tuple(round(a * 0.88 + b * 0.12) for a, b in zip(bg, accent, strict=True))
            rect((8, top, width - 8, bottom), 8, fill)
        ink = colors["foreground"] if entry.enabled else colors["faint"]
        middle = (top + bottom) / 2
        _icon(draw, entry.icon, 16 * zoom, (middle - 8) * zoom, zoom, colors["muted"])
        face = _font(round(14 * zoom), cjk=any(ord(c) > 0x2E80 for c in entry.label))
        draw.text((40 * zoom, middle * zoom), entry.label, font=face, fill=ink, anchor="lm")
        if entry.shortcut:
            draw.text(
                ((width - 16) * zoom, middle * zoom),
                entry.shortcut,
                font=_font(round(12 * zoom), mono=True),
                fill=colors["muted"],
                anchor="rm",
            )
    return image.resize(size, Image.Resampling.LANCZOS)
