"""The user's own copy of an appshot: full resolution, lossless, colour-exact.

The picture a model sees is a downscaled JPEG (vision budget). The user's copy
comes from :attr:`ScreenContext.master` — the same capture, redacted the same
way — and is encoded here once the markings are known:

* ``png`` — 8-bit, lossless, full resolution. On an SDR monitor it carries
  the monitor's ICC profile, which is the exact description of its pixels.
  On an HDR monitor it is the SDR rendition (SDR white = sRGB white): what
  the clipboard, chat and every SDR viewer get.
* ``hdr_png`` — only from an HDR or wide-gamut monitor: 16-bit BT.2020 PQ
  with ``cICP``/``cLLi`` (:mod:`jarvis.platform.hdr_image`), which keeps the
  highlights and the gamut the monitor showed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MasterFiles:
    png: bytes = field(repr=False)
    width: int
    height: int
    hdr_png: bytes | None = field(default=None, repr=False)


def encode_master(master: Any, markup: Any = None) -> MasterFiles:
    """PNG bytes for a :class:`~jarvis.screen_context.models.MasterImage`.

    ``markup`` (:class:`jarvis.appshot.markup.Markup`) is burnt in exactly as
    on the model's picture. Runs for a second or two on a 4K HDR frame — call
    it off the event loop.
    """
    import numpy as np  # noqa: PLC0415

    from jarvis.platform import hdr_image  # noqa: PLC0415

    marked = markup is not None and not markup.empty
    if master.hdr:
        scrgb = np.asarray(master.pixels)
        if marked:
            from jarvis.appshot.markup import apply_to_scrgb  # noqa: PLC0415

            scrgb = apply_to_scrgb(scrgb, markup, master.sdr_white_nits)
        sdr = hdr_image.scrgb_to_srgb8(scrgb, master.sdr_white_nits)
        hdr_png = hdr_image.encode_png16(
            hdr_image.scrgb_to_pq16(scrgb), light=hdr_image.light_levels(scrgb)
        )
        height, width = sdr.shape[:2]
        return MasterFiles(hdr_image.encode_png8(sdr), width, height, hdr_png)
    from PIL import Image  # noqa: PLC0415

    picture = Image.fromarray(np.asarray(master.pixels, dtype=np.uint8), "RGB")
    if marked:
        from jarvis.appshot.markup import apply_to_image  # noqa: PLC0415

        picture = apply_to_image(picture, markup)
    return MasterFiles(
        hdr_image.encode_png8(picture, master.icc_profile), picture.width, picture.height
    )
