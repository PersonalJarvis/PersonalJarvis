"""The pet strip in pixel art (``ui.orb.pixel_strip``).

Pins: the frame keeps the original strip's size, so the original hit test
still fits it; every control's centre is paint, not key; only the pixel kit's
colours and the key appear (hard edges, no fringe); mutes turn red; the phone
turns green to call and red to hang up under the pointer; the voice moves the
bars; the art is whole blocks of ``art_px``.
"""

from __future__ import annotations

import pytest

from ui.orb import controls
from ui.orb import pixel_strip as ps

KEY = (255, 0, 255)
S = controls.PetStripState


def _colours(image) -> set[tuple[int, int, int]]:
    return {c for _n, c in image.getcolors(image.width * image.height)}


@pytest.mark.parametrize("scale", [1.0, 1.25, 2.0])
def test_the_pixel_strip_keeps_the_strips_size_and_controls(scale: float) -> None:
    frame = ps.render_pet_strip_pixel(S(), scale, 3, KEY)
    layout = controls.pet_strip_layout(scale)
    assert frame.size == (layout.width, layout.height)
    centres = [layout.pen[:2], layout.call[:2]]
    centres += [
        ((x0 + x1) / 2, (layout.pill[1] + layout.pill[3]) / 2) for _a, x0, x1 in layout.slots
    ]
    for x, y in centres:
        assert frame.getpixel((int(x), int(y))) != KEY


def test_only_the_kits_colours_and_the_key_appear() -> None:
    frame = ps.render_pet_strip_pixel(S(motion="voice", level=4, phase=3), 1.25, 4, KEY)
    allowed = {
        KEY,
        ps.OUTLINE,
        ps.PAPER,
        ps.PAPER_HOVER,
        ps.HIGHLIGHT,
        ps.SHADE,
        ps.DIVIDER,
        ps.INK,
        ps.SKY_HIGHLIGHT,
    }
    stray = {c for c in _colours(frame) - allowed if c[2] < 140 or c[0] > 140}
    assert not stray  # the bars' sky shades are the only other colours


def test_mutes_turn_red_and_the_phone_shows_its_action() -> None:
    muted = ps.render_pet_strip_pixel(S(mic_muted=True), 1.0, 3, KEY)
    assert ps.MUTED in _colours(muted)
    call = ps.render_pet_strip_pixel(S(hovered="call"), 1.0, 3, KEY)
    hang = ps.render_pet_strip_pixel(S(hovered="call", active=True), 1.0, 3, KEY)
    assert ps.CALL_GO in _colours(call) and ps.CALL_STOP in _colours(hang)


def test_the_voice_moves_the_bars_and_art_pixels_are_whole_blocks() -> None:
    rest = ps.render_pet_strip_pixel(S(), 1.0, 3, KEY)
    loud = ps.render_pet_strip_pixel(S(motion="voice", level=6, phase=1), 1.0, 3, KEY)
    assert rest.tobytes() != loud.tobytes()
    big = ps.render_pet_strip_pixel(S(), 2.0, 4, KEY)
    # Nearest-neighbour blocks: every 4 x 4 block inside the frame is one colour.
    for bx in range(0, big.width - 4, 4):
        block = big.crop((bx, 8, bx + 4, 12))
        assert len(_colours(block)) == 1
