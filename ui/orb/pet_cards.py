"""The pet's done cards, drawn.

A Jarvis turn or a task Jarvis started has finished: a small green check (a
red cross when it failed) and a bold title — what was asked — over the first
line or two of the answer, on a dark, slightly see-through surface with a
hairline rim, soft rounded corners and a soft shadow.

Everything is pure PIL and returns RGBA with real alpha, ready for
:class:`ui.orb.layered_surface.AlphaWindow`, which flattens it onto a colour
key where a platform has no per-pixel alpha. The text helpers (:func:`font`,
:func:`ellipsize`, :func:`wrap`) serve the thought bubble too.
"""

from __future__ import annotations

import functools
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_Rgb = tuple[int, int, int]
_Rgba = tuple[int, int, int, int]

#: What a card shows.
CARD_KINDS: tuple[str, ...] = ("done", "error")

#: Unscaled geometry (logical px at 100 % and ``pet_scale`` 1.0).
PAD_X = 16
PAD_Y = 11
LINE_GAP = 3
ICON = 17
ICON_GAP = 7
RADIUS = 18
MIN_WIDTH = 120
#: Room around a card for its shadow.
SHADOW_PAD = 14
SHADOW_BLUR = 9
SHADOW_OFFSET_Y = 3
SHADOW_ALPHA = 110
#: Font sizes in px.
TITLE_PX = 14.5
BODY_PX = 14.0
#: Lines of the answer under a done card's title.
DETAIL_LINES = 2

#: The surface: a dark grey just see-through enough that the desktop shows
#: as a shade, a hairline rim, and the text colours.
FILL: _Rgba = (34, 34, 37, 226)
RIM: _Rgba = (255, 255, 255, 30)
#: The fill a colour-keyed window uses instead (no alpha there).
FILL_SOLID: _Rgb = (30, 30, 33)
RIM_SOLID: _Rgb = (58, 58, 63)
TITLE: _Rgb = (242, 242, 245)
BODY: _Rgb = (190, 191, 198)
ICON_FILL: dict[str, _Rgb] = {"done": (34, 197, 94), "error": (239, 68, 68)}
ICON_MARK: dict[str, _Rgb] = {"done": (10, 26, 16), "error": (40, 8, 8)}
#: Hover lifts a card a shade.
HOVER_LIFT = 10

#: The icon draws itself in this many steps (the check is ticked).
ICON_FRAMES = 10
_SS = 3  # supersampling for the card silhouette


def _px(value: float, scale: float) -> int:
    return max(1, int(round(value * max(0.25, float(scale)))))


# --- fonts -------------------------------------------------------------------


def _font_candidates(weight: str) -> list[Path]:
    """Font files to try, best first, on this OS."""
    out: list[Path] = []
    if sys.platform == "win32":
        root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        names = ["seguisb.ttf", "segoeuib.ttf"] if weight == "semibold" else ["segoeui.ttf"]
        out += [root / n for n in names]
    elif sys.platform == "darwin":
        out += [Path("/System/Library/Fonts/SFNS.ttf"), Path("/System/Library/Fonts/Helvetica.ttc")]
    else:
        bold = weight == "semibold"
        for base in ("/usr/share/fonts/truetype", "/usr/share/fonts"):
            for name in (
                ("inter/Inter-SemiBold.ttf" if bold else "inter/Inter-Regular.ttf"),
                ("dejavu/DejaVuSans-Bold.ttf" if bold else "dejavu/DejaVuSans.ttf"),
                ("TTF/DejaVuSans-Bold.ttf" if bold else "TTF/DejaVuSans.ttf"),
                ("noto/NotoSans-SemiBold.ttf" if bold else "noto/NotoSans-Regular.ttf"),
            ):
                out.append(Path(base) / name)
    return out


@functools.lru_cache(maxsize=32)
def font(weight: str, px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A font of ``weight`` (``regular`` / ``semibold``) at ``px`` pixels.

    The OS's UI face where one is found; Pillow's own scalable default
    otherwise, so a bare Linux server still renders readable text.
    """
    for path in _font_candidates(weight):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), px)
            except OSError:
                continue
    try:
        return ImageFont.load_default(px)
    except TypeError:  # Pillow < 10.1 has no sized default
        return ImageFont.load_default()


def _text_w(f: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> float:
    return float(f.getlength(text)) if hasattr(f, "getlength") else float(len(text) * 7)


def _line_h(f: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    try:
        ascent, descent = f.getmetrics()  # type: ignore[union-attr]
        return int(ascent + descent)
    except AttributeError:
        return 14


def ellipsize(text: str, f: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: float) -> str:
    """``text`` cut with an ellipsis to fit ``width`` pixels."""
    if _text_w(f, text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(f, text[:mid].rstrip() + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def wrap(
    text: str, f: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: float, max_lines: int
) -> list[str]:
    """Word-wrap ``text`` into at most ``max_lines`` lines, the last ellipsized."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for index, word in enumerate(words):
        trial = f"{current} {word}".strip()
        if _text_w(f, trial) <= width or not current:
            current = trial
            continue
        lines.append(current)
        if len(lines) == max_lines:
            rest = " ".join([lines.pop(), *words[index:]])
            lines.append(ellipsize(rest, f, width))
            return lines
        current = word
    if current:
        lines.append(ellipsize(current, f, width) if len(lines) < max_lines else current)
    return lines[:max_lines]


# --- shapes ------------------------------------------------------------------


@functools.lru_cache(maxsize=64)
def _rounded_mask(width: int, height: int, radius: int) -> Image.Image:
    """An antialiased rounded-rectangle mask (drawn large, scaled down)."""
    big = Image.new("L", (width * _SS, height * _SS), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, width * _SS - 1, height * _SS - 1], radius=radius * _SS, fill=255
    )
    return big.resize((width, height), Image.Resampling.LANCZOS)


@functools.lru_cache(maxsize=64)
def _surface(width: int, height: int, radius: int, scale: float, hovered: bool) -> Image.Image:
    """Shadow, fill and rim of one card, with ``SHADOW_PAD`` around it."""
    pad = _px(SHADOW_PAD, scale)
    full = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    mask = _rounded_mask(width, height, radius)
    shadow = Image.new("L", full.size, 0)
    shadow.paste(
        mask.point(lambda v: v * SHADOW_ALPHA // 255), (pad, pad + _px(SHADOW_OFFSET_Y, scale))
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(_px(SHADOW_BLUR, scale) / 2.0))
    full.putalpha(shadow)
    lift = HOVER_LIFT if hovered else 0
    fill = (FILL[0] + lift, FILL[1] + lift, FILL[2] + lift, FILL[3])
    body = Image.new("RGBA", (width, height), fill)
    body.putalpha(ImageChops.multiply(mask, Image.new("L", mask.size, fill[3])))
    full.alpha_composite(body, (pad, pad))
    # The rim: the silhouette minus itself shrunk by one pixel.
    inner = _rounded_mask(max(1, width - 2), max(1, height - 2), max(1, radius - 1))
    ring = mask.copy()
    hole = Image.new("L", mask.size, 0)
    hole.paste(inner, (1, 1))
    ring = ImageChops.subtract(ring, hole)
    rim = Image.new("RGBA", (width, height), RIM[:3] + (0,))
    rim.putalpha(ring.point(lambda v: v * RIM[3] // 255))
    full.alpha_composite(rim, (pad, pad))
    return full


def _ease_out_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c1 = 1.70158
    return 1.0 + (c1 + 1.0) * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


@functools.lru_cache(maxsize=64)
def render_icon(kind: str, frame: int, diameter: int) -> Image.Image:
    """The round check (or cross) at animation ``frame``, RGBA."""
    ss = 4
    size = max(2, diameter) * ss
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    t = max(0, min(ICON_FRAMES, frame)) / ICON_FRAMES
    r = size / 2.0 * _ease_out_back(min(1.0, t / 0.5))
    c = size / 2.0
    fill = ICON_FILL.get(kind, ICON_FILL["done"])
    mark = ICON_MARK.get(kind, ICON_MARK["done"])
    if r > 0.5:
        d.ellipse([c - r, c - r, c + r, c + r], fill=fill + (255,))
    share = max(0.0, min(1.0, (t - 0.3) / 0.7))
    width = max(1, int(round(size * 0.12)))
    unit = size / 24.0

    def pt(u: float, v: float) -> tuple[float, float]:
        return (c + (u - 12.0) * unit, c + (v - 12.0) * unit)

    def stroke(points: list[tuple[float, float]], amount: float) -> None:
        if amount <= 0:
            return
        pts = [pt(u, v) for u, v in points]
        lengths = [math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False)]
        want = sum(lengths) * amount
        line = [pts[0]]
        for (a, b), length in zip(zip(pts, pts[1:], strict=False), lengths, strict=False):
            if want >= length:
                line.append(b)
                want -= length
                continue
            f = want / length if length else 0.0
            line.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            break
        d.line(line, fill=mark + (255,), width=width, joint="curve")
        for x, y in (line[0], line[-1]):
            d.ellipse(
                [x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=mark + (255,)
            )

    if kind == "error":
        stroke([(8.4, 8.4), (15.6, 15.6)], min(1.0, share * 2))
        stroke([(15.6, 8.4), (8.4, 15.6)], max(0.0, share * 2 - 1))
    else:
        stroke([(7.0, 12.4), (10.6, 15.8), (17.0, 8.8)], share)
    return layer.resize((diameter, diameter), Image.Resampling.LANCZOS)


# --- cards -------------------------------------------------------------------


@functools.lru_cache(maxsize=128)
def render_card(
    kind: str,
    title: str,
    detail: str,
    *,
    scale: float = 1.0,
    max_width: int = 360,
    hovered: bool = False,
    icon_frame: int = ICON_FRAMES,
    content: bool = True,
) -> Image.Image:
    """One card as RGBA, shadow included (``SHADOW_PAD`` on every side).

    A done card is always ``max_width`` wide, so stacked cards line up edge
    to edge. ``content=False`` draws the
    surface alone — a card peeking out behind another must not show its
    text through the see-through card in front.

    Pure and cached — never mutate the result.
    """
    pad_x, pad_y = _px(PAD_X, scale), _px(PAD_Y, scale)
    max_inner = max(40, max_width - 2 * pad_x)
    title_font = font("semibold", _px(TITLE_PX, scale))
    body_font = font("regular", _px(BODY_PX, scale))
    icon_d = _px(ICON, scale)
    icon_gap = _px(ICON_GAP, scale)
    title_text = ellipsize(" ".join(title.split()), title_font, max_inner - icon_d - icon_gap)
    lines = wrap(" ".join(detail.split()), body_font, max_inner, DETAIL_LINES) if detail else []
    widest = max(
        [icon_d + icon_gap + _text_w(title_font, title_text)]
        + [_text_w(body_font, line) for line in lines]
    )
    _ = widest  # measured for the wrap; the width itself is fixed
    width = max(_px(MIN_WIDTH, scale), max_width)
    title_h = max(_line_h(title_font), icon_d)
    body_h = _line_h(body_font)
    gap = _px(LINE_GAP, scale)
    height = 2 * pad_y + title_h + (gap + body_h * len(lines) if lines else 0)
    radius = min(height // 2, _px(RADIUS, scale))
    card = _surface(width, height, radius, round(scale, 3), hovered).copy()
    if not content:
        return card
    pad = _px(SHADOW_PAD, scale)
    x0, y0 = pad + pad_x, pad + pad_y
    icon = render_icon(kind, icon_frame, icon_d)
    card.alpha_composite(icon, (x0, int(round(y0 + (title_h - icon_d) / 2.0))))
    d = ImageDraw.Draw(card)
    d.text(
        (x0 + icon_d + icon_gap, y0 + title_h / 2.0),
        title_text,
        font=title_font,
        fill=TITLE + (255,),
        anchor="lm",
    )
    y = y0 + title_h + gap
    for line in lines:
        d.text((x0, y), line, font=body_font, fill=BODY + (255,))
        y += body_h
    return card


def card_box(image: Image.Image, scale: float) -> tuple[int, int, int, int]:
    """The card's own rectangle inside its shadow-padded image."""
    pad = _px(SHADOW_PAD, scale)
    return pad, pad, image.width - pad, image.height - pad


__all__ = [
    "CARD_KINDS",
    "card_box",
    "ellipsize",
    "font",
    "render_card",
    "render_icon",
    "wrap",
]
