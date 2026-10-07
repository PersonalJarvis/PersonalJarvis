"""Pixels for ``FakeScreenGrab``: what a capture consumer actually receives.

``FakeScreenGrab`` (``tests/fakes/fake_tcc.py``) says whether a simulated capture
saw other apps' windows or only the wallpaper. A consumer receives pixels, so this
module turns that verdict into RGB bytes: textured content for a real capture and a
single flat colour for the wallpaper-only frame macOS returns, without an error,
for a capture it does not allow (unverified; see the fidelity ledger in
``fake_tcc.py``).

Usage::

    grab = pixels_grabber(FakeScreenGrab(tcc))     # a ``Grabber``: bbox -> ((w, h), rgb)
    capture_stable_frame(monitor, grab=grab)
"""

from __future__ import annotations

from collections.abc import Callable

from tests.fakes.fake_tcc import FakeScreenGrab

#: The flat colour of a wallpaper-only frame.
WALLPAPER_RGB: tuple[int, int, int] = (12, 40, 90)


def textured_pixels(size: tuple[int, int]) -> tuple[tuple[int, int], bytes]:
    """RGB bytes with real contrast everywhere (a diagonal gradient with stripes)."""
    width, height = size
    data = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            data[offset] = (x * 255 // max(1, width - 1)) & 0xFF
            data[offset + 1] = (y * 255 // max(1, height - 1)) & 0xFF
            data[offset + 2] = 255 if (x // 8 + y // 8) % 2 else 40
    return (width, height), bytes(data)


def wallpaper_pixels(size: tuple[int, int]) -> tuple[tuple[int, int], bytes]:
    """RGB bytes of a wallpaper-only frame: one flat colour."""
    width, height = size
    return (width, height), bytes(WALLPAPER_RGB) * (width * height)


def photo_wallpaper_pixels(size: tuple[int, int]) -> tuple[tuple[int, int], bytes]:
    """RGB bytes of a photographic wallpaper: smooth gradients, no flat colour.

    macOS ships photographic and dynamic wallpapers by default, so a wallpaper-only
    capture is usually NOT one flat colour; a sanity check that only looks for
    blankness would accept it.
    """
    width, height = size
    data = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            data[offset] = 20 + (x * 120 // max(1, width - 1))
            data[offset + 1] = 60 + (y * 100 // max(1, height - 1))
            data[offset + 2] = 140 + ((x + y) * 80 // max(1, width + height - 2))
    return (width, height), bytes(data)


def pixels_grabber(
    screen: FakeScreenGrab,
    *,
    size: tuple[int, int] = (192, 108),
    photo_wallpaper: bool = False,
) -> Callable[[dict[str, int]], tuple[tuple[int, int], bytes]]:
    """A ``Grabber`` whose pixels follow ``screen``: wallpaper unless macOS allows it.

    Every call goes through ``screen.grab()``, so the ``FakeTCC`` call log shows an
    implicit prompt if the service was still ``not_determined`` at that moment.
    ``photo_wallpaper`` makes the wallpaper-only frame a smooth gradient instead of
    one flat colour.
    """
    wallpaper = photo_wallpaper_pixels if photo_wallpaper else wallpaper_pixels

    def grab(_bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]:
        frame = screen.grab()
        return wallpaper(size) if frame.wallpaper_only else textured_pixels(size)

    return grab


__all__ = [
    "WALLPAPER_RGB",
    "photo_wallpaper_pixels",
    "pixels_grabber",
    "textured_pixels",
    "wallpaper_pixels",
]
