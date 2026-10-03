"""The default bar uses the authored Pet strip and keeps its complete actions."""

from __future__ import annotations

import pytest

from jarvis.ui.jarvisbar import interaction, renderer
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay
from jarvis.ui.jarvisbar.qt_overlay import QtJarvisBarOverlay
from ui.orb import controls


@pytest.fixture(autouse=True)
def geometry():
    renderer.apply_display_scale(1.0, 1.0)
    yield
    renderer.apply_display_scale(1.0, 1.0)


def center(action):
    layout = controls.pet_strip_layout(renderer.strip_scale())
    if action == "compose":
        return layout.pen[:2]
    if action == "call":
        return layout.call[:2]
    _, left, right = next(slot for slot in layout.slots if slot[0] == action)
    return (left + right) / 2, layout.height / 2


def test_default_painter_is_the_existing_component():
    expected = controls.render_pet_strip(controls.PetStripState(jarvis_bar=True))
    actual = renderer.JarvisBarRenderer().render(0, "idle")
    assert actual.tobytes() == expected.tobytes()
    assert actual.size == controls.pet_strip_size()


@pytest.mark.parametrize("screen,user", [(0.55, 0.5), (0.85, 1.35), (1.6, 2.0)])
def test_resizing_keeps_every_visible_control_clickable(screen, user):
    renderer.apply_display_scale(screen, user)
    assert renderer.JarvisBarRenderer().render(0, "idle").size == controls.pet_strip_size(
        screen * user
    )
    for action, expected in [
        ("compose", "compose"),
        ("mic_mute", "mute"),
        ("orb", "talk"),
        ("speaker", "speaker"),
        ("call", "talk"),
    ]:
        x, y = center(action)
        assert interaction.resolve_click(x, renderer.WIN_W, "idle", y=y) == expected


@pytest.mark.parametrize("mode", renderer.MODES)
def test_modes_never_hide_or_resize_controls(mode):
    frame = renderer.JarvisBarRenderer().render(0.5, mode, 0.7)
    assert frame.size == (renderer.WIN_W, renderer.WIN_H)
    assert frame.getpixel((0, 0)) == renderer.COLOR_KEY_RGB


def test_transparent_corners_and_gaps_do_not_trigger_actions():
    for x, y in [(0, 0), (renderer.WIN_W - 1, 0), (0, renderer.WIN_H - 1)]:
        assert interaction.resolve_click(x, renderer.WIN_W, "listen", y=y) == "none"


@pytest.mark.parametrize("mode", ["listen", "think", "speak"])
def test_phone_ends_session_and_writing_remains_available(mode):
    for action, expected in [
        ("compose", "compose"),
        ("call", "hangup"),
        ("speaker", "speaker"),
        ("mic_mute", "mute"),
    ]:
        x, y = center(action)
        assert interaction.resolve_click(x, renderer.WIN_W, mode, y=y) == expected


@pytest.mark.parametrize("mode", ["dictate", "dictate_transcribing"])
def test_dictation_stop_is_on_the_orb_and_cannot_start_a_call(mode):
    for action in ["orb", "call", "mic_mute", "speaker", "compose"]:
        x, y = center(action)
        assert interaction.resolve_click(x, renderer.WIN_W, mode, y=y) == (
            "dictation_stop" if action == "orb" else "none"
        )


def test_prompt_pause_keeps_write_and_call_controls():
    for action, expected in [
        ("orb", "prompt_mode_toggle"),
        ("call", "talk"),
        ("compose", "compose"),
    ]:
        x, y = center(action)
        assert (
            interaction.resolve_click(x, renderer.WIN_W, "idle", y=y, prompt_mode=True) == expected
        )


@pytest.mark.parametrize("kind", ["tk", "qt"])
def test_both_hosts_route_compose_and_audio_independently(kind):
    bar = JarvisBarOverlay() if kind == "tk" else QtJarvisBarOverlay()
    events = []
    bar.set_on_compose(lambda: events.append("compose"))
    bar.set_on_speaker_toggle(lambda: events.append("speaker"))
    bar.set_on_mute_toggle(lambda: events.append("microphone"))
    click = bar._on_click if kind == "tk" else bar._dispatch_click_ui
    for action in ["compose", "speaker", "mic_mute"]:
        x, y = center(action)
        click(x, click_y=y)
    assert events == ["compose", "speaker", "microphone"]
    assert bar._muted is True
    # Output mute only changes after the authoritative pipeline event.
    assert bar._speaker_muted is False
    bar.set_speaker_muted(True)
    assert bar._speaker_muted is True
    assert bar._muted is True


def test_audio_and_prompt_state_are_visible_without_hover():
    painter = renderer.JarvisBarRenderer()
    frames = [
        painter.render(0, "idle", **state).tobytes()
        for state in [
            {},
            {"muted": True},
            {"speaker_muted": True},
            {"prompt_mode": True},
            {"prompt_mode": True, "prompt_mode_paused": True},
        ]
    ]
    assert len(set(frames)) == len(frames)


@pytest.mark.parametrize("kind", ["tk", "qt"])
def test_hangup_guard_keeps_write_and_audio_controls_available(kind):
    bar = JarvisBarOverlay() if kind == "tk" else QtJarvisBarOverlay()
    events = []
    bar.set_on_compose(lambda: events.append("compose"))
    bar.set_on_speaker_toggle(lambda: events.append("speaker"))
    bar.set_on_mute_toggle(lambda: events.append("mic"))
    bar._hangup_click_block_until = float("inf")
    click = bar._on_click if kind == "tk" else bar._dispatch_click_ui
    for action in ["compose", "speaker", "mic_mute"]:
        x, y = center(action)
        click(x, click_y=y)
    assert events == ["compose", "speaker", "mic"]


def test_live_audio_moves_the_sphere_but_idle_stays_still():
    painter = renderer.JarvisBarRenderer()
    assert painter.render(0, "listen", 0).tobytes() != painter.render(0, "listen", 1).tobytes()
    assert painter.render(0, "idle", 0).tobytes() == painter.render(3, "idle", 1).tobytes()
    assert painter.render(0, "think").tobytes() != painter.render(0.4, "think").tobytes()


def test_pet_keeps_its_existing_notification_strip():
    state = controls.PetStripState()
    assert controls.pet_hit_test(*controls.pet_strip_layout().pen[:2]) == "bell"
    assert (
        controls.render_pet_strip(state).tobytes()
        != renderer.JarvisBarRenderer().render(0, "idle").tobytes()
    )
