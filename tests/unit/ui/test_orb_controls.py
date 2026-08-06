"""Guards for the voice orb's desktop control row (``ui.orb.controls``).

The row exists because the desktop orb's capabilities used to be invisible
gestures. These tests pin the three properties that make it usable rather than
decorative: a click lands on the button the user aimed at, the disc says what
the system is actually doing, and the speaker toggle changes the real volume
without persisting a mute the user will not remember tomorrow.
"""

from __future__ import annotations

import pytest

from jarvis.core import runtime_refs
from ui.orb import controls


class _FakePipeline:
    """Just enough SpeechPipeline for the speaker toggle."""

    def __init__(self, volume: float = 0.8) -> None:
        self.volume = volume
        self.writes: list[float] = []

    def get_tts_volume(self) -> float:
        return self.volume

    def set_tts_volume(self, volume: float) -> None:
        self.volume = float(volume)
        self.writes.append(float(volume))


@pytest.fixture
def live_pipeline() -> _FakePipeline:
    pipeline = _FakePipeline()
    runtime_refs.set_speech_pipeline(pipeline)
    try:
        yield pipeline
    finally:
        runtime_refs.set_speech_pipeline(None)


# --- geometry ---------------------------------------------------------------


def test_every_action_is_reachable_at_its_own_centre() -> None:
    centre_y = controls.ROW_PADDING + controls.BUTTON_SIZE / 2
    hits = [controls.hit_test(cx, centre_y) for cx in controls.button_centers()]
    assert hits == list(controls.ACTIONS)


def test_the_gaps_between_discs_resolve_to_nothing() -> None:
    """A near-miss must not hang up the call. Same rule as the bar's close-X."""
    centre_y = controls.ROW_PADDING + controls.BUTTON_SIZE / 2
    first, second = controls.button_centers()[:2]
    between = (first + second) / 2
    assert controls.hit_test(between, centre_y) is None
    # Above and below the discs is the window's padding, not a button.
    assert controls.hit_test(first, 0.0) is None


def test_row_is_wide_enough_for_every_disc_and_gap() -> None:
    width, height = controls.row_size()
    expected = (
        len(controls.ACTIONS) * controls.BUTTON_SIZE
        + (len(controls.ACTIONS) - 1) * controls.BUTTON_GAP
        + 2 * controls.ROW_PADDING
    )
    assert width == expected
    assert height == controls.BUTTON_SIZE + 2 * controls.ROW_PADDING


# --- rendering --------------------------------------------------------------


def test_corners_stay_pure_key_colour_so_the_window_is_transparent() -> None:
    """A blended corner survives the colour key as a pink fleck (see the module)."""
    key = (255, 0, 255)
    frame = controls.render_row(controls.ControlState(), color_key=key)
    width, height = frame.size
    for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert frame.getpixel(point) == key


def test_disc_centres_are_painted_not_keyed_out() -> None:
    key = (255, 0, 255)
    frame = controls.render_row(controls.ControlState(), color_key=key)
    centre_y = int(controls.ROW_PADDING + controls.BUTTON_SIZE / 2)
    for cx in controls.button_centers():
        assert frame.getpixel((int(cx), centre_y)) != key


def test_a_live_conversation_lights_the_mic_disc() -> None:
    """Idle and active must not look identical — that is the whole indicator."""
    idle = controls.render_row(controls.ControlState())
    live = controls.render_row(controls.ControlState(active=True))
    mic_x = int(controls.button_centers()[controls.ACTIONS.index("mic")])
    centre_y = int(controls.ROW_PADDING + controls.BUTTON_SIZE / 2)
    box = (mic_x - 10, centre_y - 10, mic_x + 10, centre_y + 10)
    assert idle.crop(box).tobytes() != live.crop(box).tobytes()
    # The lit disc carries the product's gold, not just "some other pixels".
    fill, _border, icon = controls._disc_colors("mic", controls.ControlState(active=True))
    assert fill == controls.BTN_BG_ON
    assert icon == controls.BTN_ICON_ON


def test_a_muted_speaker_is_marked_in_the_destructive_colour() -> None:
    fill, border, icon = controls._disc_colors(
        "speaker", controls.ControlState(speaker_muted=True)
    )
    assert (fill, border, icon) == (
        controls.BTN_BG_OFF,
        controls.BTN_BORDER_OFF,
        controls.BTN_ICON_OFF,
    )


def test_attach_is_dimmed_rather_than_hidden_when_unavailable() -> None:
    """A control that vanishes teaches the user nothing about why."""
    _fill, _border, icon = controls._disc_colors(
        "attach", controls.ControlState(can_attach=False)
    )
    assert icon == controls.BTN_ICON_DISABLED


def test_hover_only_repaints_the_disc_under_the_pointer() -> None:
    plain = controls._disc_colors("close", controls.ControlState())
    hovered = controls._disc_colors("close", controls.ControlState(hovered="close"))
    neighbour = controls._disc_colors("attach", controls.ControlState(hovered="close"))
    assert hovered != plain
    assert neighbour == controls._disc_colors("attach", controls.ControlState())


# --- the speaker toggle -----------------------------------------------------


def test_speaker_toggle_is_a_no_op_without_a_live_pipeline() -> None:
    """Report nothing happened rather than painting a mute that never landed."""
    runtime_refs.set_speech_pipeline(None)
    assert controls.toggle_speaker_mute() is None
    assert controls.speaker_is_muted() is False


def test_speaker_toggle_mutes_and_restores_the_previous_volume(
    live_pipeline: _FakePipeline,
) -> None:
    live_pipeline.volume = 0.65

    assert controls.toggle_speaker_mute() is True
    assert live_pipeline.volume == 0.0
    assert controls.speaker_is_muted() is True

    assert controls.toggle_speaker_mute() is False
    assert live_pipeline.volume == pytest.approx(0.65)
    assert controls.speaker_is_muted() is False


def test_speaker_toggle_never_writes_the_config(
    live_pipeline: _FakePipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session-only by design: a forgotten mute must not survive a restart."""
    from jarvis.core import config_writer

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the orb's speaker toggle persisted the mute")

    monkeypatch.setattr(config_writer, "set_tts_volume", _fail)
    controls.toggle_speaker_mute()
    controls.toggle_speaker_mute()
    assert live_pipeline.writes == [0.0, pytest.approx(0.8)]


def test_a_pipeline_without_a_volume_getter_is_assumed_audible() -> None:
    class _Mute:
        def __init__(self) -> None:
            self.volume: float | None = None

        def set_tts_volume(self, volume: float) -> None:
            self.volume = volume

    pipeline = _Mute()
    runtime_refs.set_speech_pipeline(pipeline)
    try:
        # No getter, no attribute → treat as audible, so the first click mutes.
        assert controls.toggle_speaker_mute() is True
        assert pipeline.volume == 0.0
    finally:
        runtime_refs.set_speech_pipeline(None)
