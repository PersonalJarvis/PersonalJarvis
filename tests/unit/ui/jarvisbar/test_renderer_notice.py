"""A refused request must be VISIBLE on a bar that carries no text.

The bar has no bubble, no label and no toast. So when the pipeline declines
something the user asked for — pressing the dictation shortcut while a voice
conversation owns the microphone — the only place the reason can land on this
surface is the pill itself. Before the ``notice`` look existed the refusal went
to a log file the desktop app cannot open, and the shortcut was
indistinguishable from a dead key.

These pin the four properties that make the look trustworthy: it draws
something, it is unmistakably not a session, no audio signal can repaint it, and
it changes nothing about a frame that has no notice in flight.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.ui.jarvisbar import modes, renderer


@pytest.fixture(autouse=True)
def _reset_geometry() -> None:
    renderer.apply_display_scale(1.0, renderer.USER_SIZE_DEFAULT)


def _settled(mode: str, t: float = 0.0, **kw) -> np.ndarray:
    """Render ``mode`` after the pill easing has converged on its target size."""
    r = renderer.JarvisBarRenderer(accent="#e7c46e")
    for _ in range(60):
        r.render(0.0, mode, 0.0, **kw)
    return np.asarray(r.render(t, mode, 0.0, **kw))


@pytest.mark.parametrize("mode", modes.NOTICE_MODES)
def test_a_notice_draws_something_the_resting_pill_does_not(mode: str) -> None:
    """The whole point: the user can SEE that the key press was answered."""
    assert not np.array_equal(_settled(mode), _settled("idle"))


@pytest.mark.parametrize("mode", modes.NOTICE_MODES)
def test_a_notice_is_red_not_gold(mode: str) -> None:
    """Red is this surface's existing "no" (the muted rim, the rejected drop).

    Gold means activity here — a gold refusal would read as something starting.
    """
    frame = _settled(mode)
    reds = frame[:, :, 0].astype(int)
    greens = frame[:, :, 1].astype(int)
    blues = frame[:, :, 2].astype(int)
    # Ignore the magenta color key (R and B both maxed), which is the
    # transparent background rather than drawn content.
    drawn = ~((reds >= 250) & (blues >= 250))
    warm = drawn & (reds > greens + 40) & (reds > blues + 40)
    assert warm.sum() > 0, "no red content in the notice frame"


@pytest.mark.parametrize("mode", modes.NOTICE_MODES)
def test_a_notice_survives_hover(mode: str) -> None:
    """Hovering must not swap the answer for the mic and close-X controls.

    A notice can be raised while a conversation is live, so the hover branch
    would otherwise offer a hang-up X on top of a message the user has not read
    — and hide the reason they pressed the key in the first place.
    """
    assert np.array_equal(_settled(mode, hovered=False), _settled(mode, hovered=True))


@pytest.mark.parametrize("mode", modes.NOTICE_MODES)
def test_a_notice_breathes_so_it_never_reads_as_a_frozen_bar(mode: str) -> None:
    r = renderer.JarvisBarRenderer()
    for _ in range(60):
        r.render(0.0, mode, 0.0)
    early = np.asarray(r.render(0.0, mode, 0.0))
    later = np.asarray(r.render(0.45, mode, 0.0))
    assert not np.array_equal(early, later)


def test_frames_without_a_notice_are_byte_identical_to_before() -> None:
    """Adding the look may not perturb any existing mode's pixels."""
    for mode in ("idle", "listen", "speak", "think", "dictate"):
        a = _settled(mode, t=0.3)
        b = _settled(mode, t=0.3)
        assert np.array_equal(a, b)
        # And nothing in a non-notice frame turns the rim into the refusal red.
        assert not np.array_equal(a, _settled("notice", t=0.3))
