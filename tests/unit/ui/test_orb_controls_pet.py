"""The desktop pet's control strip (``ui.orb.controls``, ``PET_ACTIONS``).

Pure geometry and pixels, no Tk: a click lands on the control the user aimed
at, the strip's empty area stays free for dragging, every silhouette meets the
colour key with a hard edge (a blended pixel survives as a pink fleck), the
controls say what the system is doing, and a strip at rest costs nothing to
repaint. Plus the speaker toggle's volume read, which the pet's speaker relies
on to unmute again.
"""

from __future__ import annotations

import pytest
from PIL import Image

from jarvis.core import runtime_refs
from ui.orb import controls

KEY = (255, 0, 255)


def _centre(action: str, scale: float = 1.0) -> tuple[float, float]:
    layout = controls.pet_strip_layout(scale)
    if action == "compose":
        return layout.pen[0], layout.pen[1]
    for name, x0, x1 in layout.slots:
        if name == action:
            return (x0 + x1) / 2.0, (layout.pill[1] + layout.pill[3]) / 2.0
    raise AssertionError(action)


# --- geometry ---------------------------------------------------------------


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_every_pet_action_is_reachable_at_its_centre(scale: float) -> None:
    hits = [
        controls.pet_hit_test(*_centre(action, scale), scale) for action in controls.PET_ACTIONS
    ]
    assert hits == list(controls.PET_ACTIONS)


def test_the_voice_orb_row_keeps_its_own_actions() -> None:
    assert controls.ACTIONS == ("attach", "mic", "close", "speaker")
    assert controls.PET_ACTIONS == ("compose", "mic_mute", "orb", "speaker")


def test_the_gap_between_pen_and_pill_is_a_drag_handle_not_a_button() -> None:
    layout = controls.pet_strip_layout(1.0)
    gap_x = (layout.pen[0] + layout.pen[2] + layout.pill[0]) / 2.0
    assert controls.pet_hit_test(gap_x, layout.height / 2.0) is None
    # The padding band above the controls is empty too.
    assert controls.pet_hit_test(_centre("orb")[0], 0.5) is None


def test_a_divider_click_goes_to_a_neighbour_instead_of_nowhere() -> None:
    layout = controls.pet_strip_layout(1.0)
    divider = layout.dividers[0]
    hit = controls.pet_hit_test(divider + 0.5, layout.height / 2.0)
    assert hit in ("mic_mute", "orb")


def test_the_pill_ends_are_round_so_its_corners_are_not_clickable() -> None:
    layout = controls.pet_strip_layout(1.0)
    x0, y0, x1, _y1 = layout.pill
    assert controls.pet_hit_test(x1 - 1, y0 + 1) is None
    assert controls.pet_hit_test(x0 + 1, y0 + 1) is None


def test_the_strip_scales_with_the_display() -> None:
    small = controls.pet_strip_size(1.0)
    large = controls.pet_strip_size(2.0)
    assert large[0] == pytest.approx(small[0] * 2, abs=4)
    assert large[1] == pytest.approx(small[1] * 2, abs=2)


# --- rendering --------------------------------------------------------------


def _frame(state: controls.PetStripState, scale: float = 1.0) -> Image.Image:
    return controls.render_pet_strip(state, scale, KEY)


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_every_pixel_is_either_pure_key_or_fully_opaque_art(scale: float) -> None:
    """No pixel may blend into the key: along every silhouette edge the
    neighbour of a key pixel is never a magenta-tinted mix."""
    frame = _frame(controls.PetStripState(hovered="orb"), scale)
    width, height = frame.size
    px = frame.load()
    for x, y in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
        assert px[x, y] == KEY
    for y in range(height):
        for x in range(width):
            r, g, b = px[x, y]
            if (r, g, b) == KEY:
                continue
            # A key blend is strongly red+blue with little green; the palette
            # is grey/blue, so such a pixel can only be a fringe.
            assert not (r > 120 and b > 120 and g < 60), (x, y, px[x, y])


def test_control_centres_are_painted_not_keyed_out() -> None:
    frame = _frame(controls.PetStripState())
    for action in controls.PET_ACTIONS:
        x, y = _centre(action)
        assert frame.getpixel((int(x), int(y))) != KEY, action


def _red_pixels(frame: Image.Image, box: tuple[int, int, int, int]) -> int:
    return sum(
        1
        for (r, g, b) in frame.crop(box).getdata()
        if r > 150 and g < 110 and b < 110 and (r, g, b) != KEY
    )


def _slot_box(action: str) -> tuple[int, int, int, int]:
    layout = controls.pet_strip_layout(1.0)
    for name, x0, x1 in layout.slots:
        if name == action:
            return (x0, layout.pill[1], x1, layout.pill[3])
    raise AssertionError(action)


def test_a_muted_microphone_carries_a_red_slash() -> None:
    box = _slot_box("mic_mute")
    assert _red_pixels(_frame(controls.PetStripState()), box) == 0
    assert _red_pixels(_frame(controls.PetStripState(mic_muted=True)), box) > 5


def test_a_muted_speaker_carries_a_red_slash() -> None:
    box = _slot_box("speaker")
    assert _red_pixels(_frame(controls.PetStripState()), box) == 0
    assert _red_pixels(_frame(controls.PetStripState(speaker_muted=True)), box) > 5


def test_the_orb_is_bluish_and_swells_only_while_engaged() -> None:
    box = _slot_box("orb")

    def _blue(frame: Image.Image) -> int:
        return sum(1 for (r, g, b) in frame.crop(box).getdata() if b > 180 and r < 150)

    rest = _blue(_frame(controls.PetStripState(level=6)))  # level ignored at rest
    assert rest > 20
    assert rest == _blue(_frame(controls.PetStripState(level=0)))
    loud = _blue(_frame(controls.PetStripState(active=True, level=6)))
    assert loud > rest


def test_a_resting_strip_is_one_cached_frame() -> None:
    state = controls.PetStripState(mic_muted=True)
    assert _frame(state) is _frame(controls.PetStripState(mic_muted=True))


def test_levels_quantise_into_a_handful_of_steps() -> None:
    assert controls.quantize_level(None) == 0
    assert controls.quantize_level(0.0) == 0
    assert controls.quantize_level(1.0) == controls.PET_LEVEL_STEPS
    assert controls.quantize_level(7.0) == controls.PET_LEVEL_STEPS
    assert controls.quantize_level(float("nan")) == 0
    steps = {controls.quantize_level(i / 100) for i in range(101)}
    assert len(steps) == controls.PET_LEVEL_STEPS + 1


# --- the speaker volume the pet reads --------------------------------------------


class _Pipeline:
    def __init__(self, volume: float) -> None:
        self._volume = volume
        # A stale attribute that must NOT win over the getter.
        self._tts_volume = 1.0

    def get_tts_volume(self) -> float:
        return self._volume

    def set_tts_volume(self, volume: float) -> None:
        self._volume = float(volume)


@pytest.fixture
def pipeline() -> _Pipeline:
    live = _Pipeline(0.7)
    runtime_refs.set_speech_pipeline(live)
    try:
        yield live
    finally:
        runtime_refs.set_speech_pipeline(None)


def test_the_volume_getter_wins_over_any_attribute(pipeline: _Pipeline) -> None:
    pipeline._volume = 0.0
    assert controls.speaker_is_muted() is True


def test_the_speaker_toggle_mutes_and_then_unmutes_again(pipeline: _Pipeline) -> None:
    assert controls.toggle_speaker_mute() is True
    assert pipeline._volume == 0.0
    assert controls.toggle_speaker_mute() is False
    assert pipeline._volume == pytest.approx(0.7)
