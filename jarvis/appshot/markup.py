"""Burn the picker's markings into the real appshot.

The area picker lets the user mark the chosen area up before the shot is
taken (:mod:`jarvis.appshot.picker.annotate`). Its markings arrive here as a
transparent PNG overlay of the area plus a list of blur/pixelate rectangles.
The capture itself still runs through Screen Context — denylist, redaction
and all — and only the finished, privacy-filtered picture gets the markings:

1. blur and pixelate patches, on the capture's own pixels;
2. the overlay on top, stretched to the capture's size (the overlay is drawn
   at the overlay screen's pixel density, the capture may be downscaled).

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
class Markup:
    """The user's markings on an area appshot."""

    overlay_png: bytes = b""
    hides: tuple[Hide, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.overlay_png and not self.hides


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
        except (TypeError, ValueError):
            continue
        if fw > 0 and fh > 0:
            hides.append(Hide(kind=str(item["kind"]), rect=(fx, fy, fw, fh)))
    markup = Markup(overlay_png=overlay, hides=tuple(hides))
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
    return out


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
    "Hide",
    "Markup",
    "apply_to_bytes",
    "apply_to_image",
    "apply_to_rgb",
    "parse_markup",
]
