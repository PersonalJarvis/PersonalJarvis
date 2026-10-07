"""The pet's done cards, drawn as little pixel-art letters.

A Jarvis turn or a task Jarvis started has finished, and the pet gets mail:
a sheet of cream paper in the pets' pixel-art language (``thought_bubble``
draws the thinking in the same one) — the sprites' dark one-pixel outline,
a shaded bottom row, a folded corner and a hard pixel drop shadow. On the
left a pixel envelope with a green check badge (a red cross when it failed);
on the right a bold title — what was asked — over the first line or two of
the answer.

Pixel art is drawn at art resolution (one art pixel = ``art_px`` screen
pixels, the pet sprite's own size) and scaled up nearest-neighbour, so every
edge stays a crisp step; only the text is drawn at screen resolution on top.
Everything is pure PIL and returns RGBA, ready for
:class:`ui.orb.layered_surface.AlphaWindow`. The text helpers (:func:`font`,
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

#: What a card shows.
CARD_KINDS: tuple[str, ...] = ("done", "error")

#: Art-pixel geometry of a card.
PAD_X_A = 4
PAD_Y_A = 3
ICON_W_A = 13
ICON_H_A = 10
ICON_GAP_A = 4
LINE_GAP_A = 1
#: The folded corner's size and the drop shadow's offset, in art pixels.
FOLD_A = 5
SHADOW_A = 1
#: Room around the paper for the shadow (art pixels).
MARGIN_A = 2
#: Text sizes, logical px at 100 %.
TITLE_PX = 14.0
BODY_PX = 13.5
#: Lines of the answer under the title.
DETAIL_LINES = 2

#: The pets' palette: the sprites' outline, cream paper, its shading and fold,
#: the ink, and the envelope.
OUTLINE: _Rgb = (27, 22, 34)
PAPER: _Rgb = (252, 248, 237)
PAPER_HOVER: _Rgb = (255, 253, 246)
PAPER_SHADE: _Rgb = (232, 224, 206)
FOLD: _Rgb = (219, 209, 186)
INK: _Rgb = (46, 36, 58)
INK_SOFT: _Rgb = (104, 94, 112)
ENVELOPE: _Rgb = (255, 255, 255)
ENVELOPE_SHADE: _Rgb = (214, 214, 226)
BADGE: dict[str, _Rgb] = {"done": (76, 196, 98), "error": (226, 82, 82)}
SHADOW_ALPHA = 105
#: The backdrop a colour-keyed window flattens a card onto.
FILL_SOLID: _Rgb = PAPER

#: The icon arrives in this many steps: the envelope drops in, then the badge
#: pops onto its corner.
ICON_FRAMES = 10


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


# --- pixel art -----------------------------------------------------------------


def card_pad(art_px: int) -> int:
    """Screen pixels around the paper (room for its drop shadow)."""
    return MARGIN_A * max(1, int(art_px))


def _ring(mask: Image.Image) -> Image.Image:
    """The one-pixel outline around ``mask``."""
    return ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask)


@functools.lru_cache(maxsize=32)
def _paper_mask(w: int, h: int) -> Image.Image:
    """The sheet's silhouette at art resolution: a rectangle with stepped
    corners and its top-right corner folded away."""
    mask = Image.new("L", (w, h), 255)
    px = mask.load()
    for x, y in ((0, 0), (w - 1, h - 1), (0, h - 1)):
        px[x, y] = 0
    for y in range(FOLD_A):
        for x in range(w - FOLD_A + y + 1, w):
            px[x, y] = 0
    return mask


@functools.lru_cache(maxsize=64)
def render_icon(kind: str, frame: int) -> Image.Image:
    """The pixel envelope with its badge at animation ``frame``, at art resolution.

    The envelope drops in from two pixels up, then the badge pops onto its
    corner: small first, then full size.
    """
    t = max(0, min(ICON_FRAMES, frame)) / ICON_FRAMES
    art = Image.new("RGBA", (ICON_W_A + 2, ICON_H_A + 3), (0, 0, 0, 0))
    if t <= 0.0:
        return art
    drop = 2 if t < 0.2 else (1 if t < 0.35 else 0)
    d = ImageDraw.Draw(art)
    x0, y0 = 0, 1 - drop + 1
    x1, y1 = ICON_W_A - 2, y0 + ICON_H_A - 3
    d.rectangle([x0, y0, x1, y1], fill=(*ENVELOPE, 255), outline=(*OUTLINE, 255))
    d.line([(x0 + 1, y1 - 1), (x1 - 1, y1 - 1)], fill=(*ENVELOPE_SHADE, 255))
    mid = (x0 + x1) // 2
    d.line([(x0 + 1, y0 + 1), (mid, y0 + 4), (x1 - 1, y0 + 1)], fill=(*OUTLINE, 255))
    if t >= 0.6:
        size = 4 if t < 0.8 else 6
        bx = ICON_W_A + 1 - size
        by = ICON_H_A + 2 - size
        badge = BADGE.get(kind, BADGE["done"])
        d.ellipse([bx, by, bx + size - 1, by + size - 1], fill=(*badge, 255))
        d.ellipse([bx, by, bx + size - 1, by + size - 1], outline=(*OUTLINE, 255))
        if size == 6:
            white = (255, 255, 255, 255)
            if kind == "error":
                d.point([(bx + 2, by + 2), (bx + 3, by + 3), (bx + 3, by + 2), (bx + 2, by + 3)],
                        fill=white)  # fmt: skip
            else:
                d.point([(bx + 1, by + 3), (bx + 2, by + 4), (bx + 3, by + 3), (bx + 4, by + 2)],
                        fill=white)  # fmt: skip
    return art


@functools.lru_cache(maxsize=128)
def render_card(
    kind: str,
    title: str,
    detail: str,
    *,
    scale: float = 1.0,
    max_width: int = 360,
    art_px: int = 3,
    hovered: bool = False,
    icon_frame: int = ICON_FRAMES,
    content: bool = True,
) -> Image.Image:
    """One letter as RGBA, ``card_pad(art_px)`` of shadow room on every side.

    Every card is ``max_width`` wide, so stacked letters line up edge to edge.
    ``content=False`` draws the sheet alone — a letter peeking out behind
    another shows its edge, not its text. Pure and cached — never mutate the
    result.
    """
    u = max(1, int(art_px))
    w_a = max(40, int(max_width) // u)
    title_font = font("semibold", max(9, int(round(TITLE_PX * scale))))
    body_font = font("regular", max(9, int(round(BODY_PX * scale))))
    text_x_a = PAD_X_A + ICON_W_A + ICON_GAP_A
    text_w = (w_a - text_x_a - PAD_X_A - FOLD_A // 2) * u
    title_text = ellipsize(" ".join(title.split()), title_font, text_w)
    lines = wrap(" ".join(detail.split()), body_font, text_w, DETAIL_LINES) if detail else []
    title_lh, body_lh = _line_h(title_font), _line_h(body_font)
    th_a = math.ceil(title_lh / u)
    bh_a = math.ceil(body_lh / u)
    text_h_a = th_a + (LINE_GAP_A + len(lines) * bh_a if lines else 0)
    h_a = 2 * PAD_Y_A + max(ICON_H_A + 1, text_h_a)
    m = MARGIN_A
    art = Image.new("RGBA", (w_a + 2 * m, h_a + 2 * m), (0, 0, 0, 0))
    paper = _paper_mask(w_a, h_a)
    # A hard pixel drop shadow, then the sheet, its shaded bottom row, the
    # folded corner and the outline around it all.
    shadow = Image.new("RGBA", paper.size, (*OUTLINE, SHADOW_ALPHA))
    art.paste(shadow, (m + SHADOW_A, m + SHADOW_A), paper)
    art.paste((*(PAPER_HOVER if hovered else PAPER), 255), (m, m, m + w_a, m + h_a), paper)
    lifted = Image.new("L", paper.size, 0)
    lifted.paste(paper, (0, -1))
    shade = Image.new("L", art.size, 0)
    shade.paste(ImageChops.subtract(paper, lifted), (m, m))
    art.paste((*PAPER_SHADE, 255), (0, 0), shade)
    d = ImageDraw.Draw(art)
    fx = m + w_a - FOLD_A
    d.polygon([(fx, m), (fx, m + FOLD_A - 1), (m + w_a - 1, m + FOLD_A - 1)], fill=(*FOLD, 255))
    whole = Image.new("L", art.size, 0)
    whole.paste(paper, (m, m))
    fold = Image.new("L", art.size, 0)
    ImageDraw.Draw(fold).polygon(
        [(fx, m), (fx, m + FOLD_A - 1), (m + w_a - 1, m + FOLD_A - 1)], fill=255
    )
    whole = ImageChops.lighter(whole, fold)
    art.paste((*OUTLINE, 255), (0, 0), _ring(whole))
    d.line([(fx, m), (fx, m + FOLD_A - 1), (m + w_a - 1, m + FOLD_A - 1)], fill=(*OUTLINE, 255))
    if content:
        icon = render_icon(kind, icon_frame)
        art.alpha_composite(icon, (m + PAD_X_A, m + (h_a - ICON_H_A - 1) // 2))
    image = art.resize((art.width * u, art.height * u), Image.Resampling.NEAREST)
    if not content:
        return image
    td = ImageDraw.Draw(image)
    x = (m + text_x_a) * u
    y = (m + (h_a - text_h_a) / 2.0) * u
    td.text((x, y), title_text, font=title_font, fill=(*INK, 255))
    y += th_a * u + LINE_GAP_A * u
    for line in lines:
        td.text((x, y), line, font=body_font, fill=(*INK_SOFT, 255))
        y += bh_a * u
    return image


def card_box(image: Image.Image, art_px: int) -> tuple[int, int, int, int]:
    """The sheet's own rectangle inside its padded image."""
    pad = card_pad(art_px)
    return pad, pad, image.width - pad, image.height - pad


__all__ = [
    "CARD_KINDS",
    "card_box",
    "card_pad",
    "ellipsize",
    "font",
    "render_card",
    "render_icon",
    "wrap",
]
