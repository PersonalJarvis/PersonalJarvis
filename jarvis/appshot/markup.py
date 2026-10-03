"""Burn the picker's markings into the real appshot.

The area picker lets the user mark the chosen area up before the shot is
taken (:mod:`jarvis.appshot.picker.annotate`). Its markings arrive here as a
transparent PNG overlay of the area plus a list of blur/pixelate rectangles.
The capture itself still runs through Screen Context — denylist, redaction
and all — and only the finished, privacy-filtered picture gets the markings:

1. blur and pixelate patches, on the capture's own pixels;
2. the overlay on top, stretched to the capture's size (the overlay is drawn
   at the overlay screen's pixel density, the capture may be downscaled);
3. the background frame, when one was chosen — the full editor's gradient
   presets, margin, rounded corners and shadow.

So the assistant sees exactly what the user pointed at. Pure Pillow, no GUI —
works on every OS and on a headless box.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

HIDE_KINDS = ("blur", "pixelate")
#: Blur radius and pixelate block as a share of the capture's longer side —
#: strong enough that text under them cannot be read back.
_BLUR_SHARE = 0.008
_PIXEL_SHARE = 0.009
#: An overlay larger than this (decoded bytes) is refused as garbage.
_MAX_OVERLAY_BYTES = 64 * 1024 * 1024

FRect = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Hide:
    kind: str
    rect: FRect


@dataclass(frozen=True, slots=True)
class Frame:
    """A background frame around the picture (the full editor's ``Background``)."""

    preset: str
    padding: float = 0.08
    radius: float = 12.0
    shadow: bool = True


@dataclass(frozen=True, slots=True)
class Markup:
    """The user's markings on an area appshot."""

    overlay_png: bytes = b""
    hides: tuple[Hide, ...] = ()
    frame: Frame | None = None

    @property
    def empty(self) -> bool:
        return not self.overlay_png and not self.hides and self.frame is None


def _parse_frame(payload: Any) -> Frame | None:
    from jarvis.appshot.picker.markup_model import BACKGROUND_PRESETS  # noqa: PLC0415

    if not isinstance(payload, dict):
        return None
    preset = payload.get("preset")
    if preset not in dict(BACKGROUND_PRESETS):
        return None
    try:
        padding = max(0.0, min(0.25, float(payload.get("padding", 0.08))))
        radius = max(0.0, min(80.0, float(payload.get("radius", 12))))
    except (TypeError, ValueError):  # Reject malformed optional frames without discarding the capture.
        return None
    return Frame(
        preset=str(preset), padding=padding, radius=radius, shadow=bool(payload.get("shadow", True))
    )


def parse_markup(payload: Any) -> Markup | None:
    """The picker's ``markup`` object → :class:`Markup`; ``None`` when absent or empty."""
    if not isinstance(payload, dict):
        return None
    overlay = b""
    raw = payload.get("overlay")
    if isinstance(raw, str) and raw:
        try:
            overlay = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            log.warning("appshot: the picker sent an unreadable markings overlay; ignored")
            overlay = b""
        if len(overlay) > _MAX_OVERLAY_BYTES or not overlay.startswith(b"\x89PNG\r\n\x1a\n"):
            overlay = b""
    hides: list[Hide] = []
    for item in payload.get("hides") or ():
        if not isinstance(item, dict) or item.get("kind") not in HIDE_KINDS:
            continue
        rect = item.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            continue
        try:
            fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in rect)
        except (TypeError, ValueError):  # Ignore malformed redaction rectangles instead of drawing unintended geometry.
            continue
        if fw > 0 and fh > 0:
            hides.append(Hide(kind=str(item["kind"]), rect=(fx, fy, fw, fh)))
    markup = Markup(
        overlay_png=overlay, hides=tuple(hides), frame=_parse_frame(payload.get("background"))
    )
    return None if markup.empty else markup


def apply_to_image(image: Any, markup: Markup) -> Any:
    """Return a new RGB Pillow image with ``markup`` burnt in."""
    from PIL import Image, ImageFilter  # noqa: PLC0415

    out = image.convert("RGB")
    width, height = out.size
    longer = max(width, height)
    for hide in markup.hides:
        fx, fy, fw, fh = hide.rect
        box = (
            round(fx * width),
            round(fy * height),
            min(width, round((fx + fw) * width)),
            min(height, round((fy + fh) * height)),
        )
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            continue
        patch = out.crop(box)
        if hide.kind == "pixelate":
            block = max(4, round(longer * _PIXEL_SHARE))
            small = patch.resize(
                (max(1, patch.width // block), max(1, patch.height // block)),
                Image.Resampling.BOX,
            )
            patch = small.resize(patch.size, Image.Resampling.NEAREST)
        else:
            radius = max(4.0, longer * _BLUR_SHARE)
            # Blur twice: one pass leaves large text guessable.
            patch = patch.filter(ImageFilter.GaussianBlur(radius)).filter(
                ImageFilter.GaussianBlur(radius)
            )
        out.paste(patch, box[:2])
    if markup.overlay_png:
        with Image.open(io.BytesIO(markup.overlay_png)) as overlay:
            layer = overlay.convert("RGBA")
        if layer.size != out.size:
            layer = layer.resize(out.size, Image.Resampling.LANCZOS)
        base = out.convert("RGBA")
        base.alpha_composite(layer)
        out = base.convert("RGB")
    if markup.frame is not None:
        out = _framed(out, markup.frame)
    return out


def _gradient(size: tuple[int, int], stops: tuple[str, ...]) -> Any:
    """A diagonal (top-left to bottom-right) gradient through ``stops``."""
    from PIL import Image, ImageColor  # noqa: PLC0415

    colours = [ImageColor.getrgb(stop) for stop in stops]
    if len(colours) == 1:
        return Image.new("RGB", size, colours[0])
    # Linear in x and y, so a small grid stretched up is exact enough.
    grid = 64
    small = Image.new("RGB", (grid, grid))
    width, height = size
    span = width * width + height * height
    for gy in range(grid):
        for gx in range(grid):
            x, y = gx / (grid - 1) * width, gy / (grid - 1) * height
            t = max(0.0, min(1.0, (x * width + y * height) / span))
            pos = t * (len(colours) - 1)
            i = min(int(pos), len(colours) - 2)
            f = pos - i
            a, b = colours[i], colours[i + 1]
            small.putpixel((gx, gy), tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3)))
    return small.resize(size, Image.Resampling.BILINEAR)


def _framed(picture: Any, frame: Frame) -> Any:
    """``picture`` on its background, as the full editor's ``renderResult`` frames it."""
    from PIL import Image, ImageDraw, ImageFilter  # noqa: PLC0415

    from jarvis.appshot.picker.markup_model import BACKGROUND_PRESETS  # noqa: PLC0415

    width, height = picture.size
    longer = max(width, height)
    pad = round(longer * frame.padding)
    unit = max(1.0, longer / 1400.0)
    radius = round(frame.radius * unit)
    stops = dict(BACKGROUND_PRESETS).get(frame.preset) or BACKGROUND_PRESETS[0][1]
    canvas = _gradient((width + pad * 2, height + pad * 2), stops).convert("RGBA")
    box = (pad, pad, pad + width, pad + height)
    if frame.shadow and pad > 0:
        blur = max(12.0, pad * 0.5)
        offset = round(max(4.0, pad * 0.12))
        shadow = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (box[0], box[1] + offset, box[2], box[3] + offset), radius=radius, fill=90
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur / 2.0))
        black = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
        canvas = Image.composite(black, canvas, shadow)
    mask = Image.new("L", picture.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    canvas.paste(picture.convert("RGBA"), box[:2], mask)
    return canvas.convert("RGB")


def apply_to_bytes(image: bytes, mime: str, markup: Markup) -> bytes:
    """``image`` (JPEG or PNG bytes) with the markings, in the same format."""
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as picture:
        marked = apply_to_image(picture, markup)
    buffer = io.BytesIO()
    if mime == "image/png":
        marked.save(buffer, format="PNG", optimize=True)
    else:
        marked.save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def apply_to_rgb(
    size: tuple[int, int], rgb: bytes, markup: Markup
) -> tuple[tuple[int, int], bytes]:
    """The raw frame with the markings — for the shutter thumbnail only."""
    from PIL import Image  # noqa: PLC0415

    marked = apply_to_image(Image.frombytes("RGB", size, rgb), markup)
    return marked.size, marked.tobytes()


__all__ = [
    "HIDE_KINDS",
    "Frame",
    "Hide",
    "Markup",
    "apply_to_bytes",
    "apply_to_image",
    "apply_to_rgb",
    "parse_markup",
]
