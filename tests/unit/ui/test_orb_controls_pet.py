"""The desktop pet's control strip (``ui.orb.controls``, ``PET_ACTIONS``).

Pure geometry and pixels, no Tk: a click lands on the control the user aimed
at, the strip's empty area stays free for dragging, every silhouette meets the
colour key with a hard edge (a blended pixel survives as a pink fleck), the
controls say what the system is doing, and a strip at rest costs nothing to
repaint. Plus the speaker toggle's volume read, which the pet's speaker relies
on to unmute again.
"""

from __future__ import annotations

import threading

import pytest
from PIL import Image

from jarvis.core import runtime_refs
from ui.orb import controls

KEY = (255, 0, 255)


def _centre(action: str, scale: float = 1.0) -> tuple[float, float]:
    layout = controls.pet_strip_layout(scale)
    if action == "bell":
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
    assert controls.PET_ACTIONS == ("bell", "mic_mute", "orb", "speaker")


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


def test_the_indicator_rests_still_and_grows_with_the_voice() -> None:
    box = _slot_box("orb")

    def _lit(frame: Image.Image) -> int:
        return sum(1 for (r, g, b) in frame.crop(box).getdata() if b > 150 and b > r)

    rest = _lit(_frame(controls.PetStripState(level=6, phase=3)))  # ignored at rest
    assert rest > 20
    assert rest == _lit(_frame(controls.PetStripState()))
    quiet = _lit(_frame(controls.PetStripState(active=True, motion="voice", level=0)))
    loud = _lit(_frame(controls.PetStripState(active=True, motion="voice", level=6)))
    assert loud > quiet
    assert loud > rest


def test_the_resting_indicator_looks_like_silence() -> None:
    # Three equal, shortest strokes: nothing in the pill suggests a voice.
    rest = controls.indicator_bars(controls.PetStripState())
    assert len({h for h, _ in rest}) == 1
    silent = controls.indicator_bars(controls.PetStripState(motion="voice", level=0))
    assert all(h == rest[0][0] for h, _ in silent)


def test_voice_strokes_wobble_but_stay_in_bounds() -> None:
    heights = {
        tuple(
            round(h, 3)
            for h, _ in controls.indicator_bars(
                controls.PetStripState(motion="voice", level=6, phase=p)
            )
        )
        for p in range(controls.PET_VOICE_PHASES)
    }
    assert len(heights) > 1  # the strokes move
    for row in heights:
        assert len(row) == 3
        assert all(controls.PET_INDICATOR_MIN_H <= h <= controls.PET_INDICATOR_MAX_H for h in row)


def test_thinking_moves_a_highlight_across_the_strokes() -> None:
    brightest = []
    for p in range(controls.PET_THINK_PHASES):
        bars = controls.indicator_bars(controls.PetStripState(motion="think", phase=p))
        glows = [g for _h, g in bars]
        brightest.append(glows.index(max(glows)))
    # The lit stroke visits all three, left to right, then starts over.
    assert set(brightest) == {0, 1, 2}
    firsts = [brightest.index(i) for i in range(3)]
    assert firsts == sorted(firsts)


def test_indicator_phase_follows_the_clock() -> None:
    assert controls.indicator_phase("rest", 12.3) == 0
    assert controls.indicator_phase("voice", 0.0) == 0
    assert controls.indicator_phase("voice", controls.PET_VOICE_STEP_S * 3 + 0.01) == 3
    half = controls.PET_THINK_PERIOD_S / 2
    assert controls.indicator_phase("think", half) == controls.PET_THINK_PHASES // 2


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


class _ConfiguredPipeline(_Pipeline):
    """A pipeline whose voice was silenced elsewhere, with ``[tts].volume`` set."""

    def __init__(self, configured: float) -> None:
        super().__init__(0.0)

        class _Tts:
            volume = configured

        class _Config:
            tts = _Tts()

        self._config = _Config()


@pytest.mark.parametrize("configured,expected", [(0.4, 0.4), (0.0, 1.0)])
def test_unmuting_a_voice_silenced_elsewhere_restores_the_configured_volume(
    monkeypatch: pytest.MonkeyPatch, configured: float, expected: float
) -> None:
    monkeypatch.setattr(controls, "_PRE_MUTE_VOLUME", {})
    live = _ConfiguredPipeline(configured)
    runtime_refs.set_speech_pipeline(live)
    try:
        assert controls.toggle_speaker_mute() is False
        assert live._volume == pytest.approx(expected)
    finally:
        runtime_refs.set_speech_pipeline(None)


def test_concurrent_toggles_never_lose_the_remembered_volume(pipeline: _Pipeline) -> None:
    # The disc (Tk thread) and the in-app button (a REST worker) can toggle at
    # the same moment; an even number of toggles must end audible again.
    def _toggle_many() -> None:
        for _ in range(50):
            controls.toggle_speaker_mute()

    workers = [threading.Thread(target=_toggle_many) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10.0)
    assert pipeline._volume == pytest.approx(0.7)


# --- the Codex-like look: filled discs, a glossy orb, proportions --------------------


def test_the_pen_and_the_pill_are_filled_without_a_ring() -> None:
    frame = controls.render_pet_strip(controls.PetStripState(), 1.0)
    layout = controls.pet_strip_layout(1.0)
    pcx, pcy, pr = layout.pen
    # Just inside the disc's edge is the fill itself, not a lighter border.
    edge = frame.getpixel((int(pcx), int(pcy - pr + 2)))
    assert edge == controls.PET_FILL
    x0, y0, x1, y1 = layout.pill
    assert frame.getpixel(((x0 + x1) // 2, y0 + 1)) == controls.PET_FILL


def test_the_talk_control_is_three_strokes() -> None:
    frame = controls.render_pet_strip(controls.PetStripState(), 2.0)
    layout = controls.pet_strip_layout(2.0)
    action, sx0, sx1 = layout.slots[1]
    assert action == "orb"
    _x0, y0, _x1, y1 = layout.pill
    row = [frame.getpixel((x, (y0 + y1) // 2)) for x in range(sx0, sx1)]
    lit = [sum(px) > 3 * 60 for px in row]
    runs = sum(1 for i, on in enumerate(lit) if on and (i == 0 or not lit[i - 1]))
    assert runs == controls.PET_INDICATOR_BARS == 3


def test_the_strip_is_about_a_third_of_the_figure_tall() -> None:
    from ui.orb.pet_renderer import PET_TARGET_FIGURE_PX

    _w, h = controls.pet_strip_size(1.0)
    assert 0.25 <= h / PET_TARGET_FIGURE_PX <= 0.35


def test_hover_lifts_a_round_patch_not_the_whole_slot() -> None:
    plain = controls.render_pet_strip(controls.PetStripState(), 1.0)
    hovered = controls.render_pet_strip(controls.PetStripState(hovered="speaker"), 1.0)
    layout = controls.pet_strip_layout(1.0)
    _action, sx0, sx1 = layout.slots[2]
    _x0, y0, _x1, _y1 = layout.pill
    # The slot's top corner stays the plain fill; its middle edge lightens.
    assert hovered.getpixel((sx0 + 1, y0 + 1)) == plain.getpixel((sx0 + 1, y0 + 1))
    assert hovered.getpixel(((sx0 + sx1) // 2, y0 + 5)) == controls.PET_FILL_HOVER
