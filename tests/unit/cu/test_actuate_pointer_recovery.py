"""The Windows click paths must never strand a mouse button in the down state.

``SendInput`` fills a batch element by element and stops at the first rejection
(UIPI truncates injection the moment a higher-integrity window takes the
foreground). A partly accepted ``[move, down, up]`` therefore lands the DOWN and
drops its UP: the physical button stays pressed and the whole desktop is
unusable until the user clicks to clear it.

``drag_from_cursor`` has guarded this with a try/finally since the drag bug;
``click`` / ``click_at_cursor`` raised straight through it. These tests pin both
halves of the contract — release what the OS actually took, and never inject a
release for a press that never happened.

Pure unit tests: ``_send`` is replaced, so no real input is ever injected and
the file runs on every OS.
"""

from __future__ import annotations

import pytest

_LEFT_DOWN = 0x0002
_LEFT_UP = 0x0004


def _pointer_actuator(refuse_after: int | None = None):
    """Recording ``WindowsActuator`` whose first ``_send`` optionally reports a
    partial injection, the way SendInput does when UIPI truncates a batch."""
    from jarvis.cu.actuate.windows import SendInputRefused, WindowsActuator

    actuator = WindowsActuator.__new__(WindowsActuator)
    actuator._t = None
    actuator._abs_move_event = lambda x, y: ("move", x, y)
    actuator._mouse_input = lambda dx, dy, data, flags: ("mouse", flags)
    actuator.cursor_pos = lambda: (10, 20)
    sent: list[list[tuple]] = []

    def _send(events):
        batch = list(events)
        sent.append(batch)
        if refuse_after is not None and len(sent) == 1:
            raise SendInputRefused(
                "refused", injected=refuse_after, total=len(batch),
            )

    actuator._send = _send
    return actuator, sent


def test_click_sends_move_then_press_and_release() -> None:
    """Baseline: the happy path is one batch, positioned before pressing."""
    actuator, sent = _pointer_actuator()
    actuator.click(100, 200)

    assert len(sent) == 1
    assert sent[0] == [
        ("move", 100, 200), ("mouse", _LEFT_DOWN), ("mouse", _LEFT_UP),
    ]


def test_click_releases_the_button_a_refused_batch_left_down() -> None:
    """Move + DOWN accepted, UP rejected — the button must come back up."""
    from jarvis.cu.actuate.windows import SendInputRefused

    actuator, sent = _pointer_actuator(refuse_after=2)

    with pytest.raises(SendInputRefused):
        actuator.click(100, 200)

    assert len(sent) == 2, "expected a recovery release after the refused batch"
    assert sent[1] == [("mouse", _LEFT_UP)]


def test_click_sends_no_stray_release_when_only_the_move_landed() -> None:
    """Refused before the press: a WM_LBUTTONUP with no preceding DOWN is real
    input the caller never asked for, and applications that watch raw button
    transitions do react to it."""
    from jarvis.cu.actuate.windows import SendInputRefused

    actuator, sent = _pointer_actuator(refuse_after=1)

    with pytest.raises(SendInputRefused):
        actuator.click(100, 200)

    assert len(sent) == 1


def test_click_sends_no_stray_release_when_nothing_landed() -> None:
    from jarvis.cu.actuate.windows import SendInputRefused

    actuator, sent = _pointer_actuator(refuse_after=0)

    with pytest.raises(SendInputRefused):
        actuator.click(100, 200)

    assert len(sent) == 1


def test_double_click_recovery_tracks_the_actual_button_state() -> None:
    """The batch is [move, down, up, down, up]. Truncated after the FIRST
    press/release pair the button is already up — releasing again would be the
    stray event the previous test guards. Truncated one event later it is held,
    and the release is owed. A naive "did we press at all" check gets the first
    case wrong."""
    from jarvis.cu.actuate.windows import SendInputRefused

    actuator, sent = _pointer_actuator(refuse_after=3)
    with pytest.raises(SendInputRefused):
        actuator.click(1, 2, double=True)
    assert len(sent) == 1, "button was already released — no recovery owed"

    actuator, sent = _pointer_actuator(refuse_after=4)
    with pytest.raises(SendInputRefused):
        actuator.click(1, 2, double=True)
    assert len(sent) == 2
    assert sent[1] == [("mouse", _LEFT_UP)]


def test_click_at_cursor_also_recovers_a_held_button() -> None:
    """The no-positioning path (the visible glide animation already moved the
    cursor) shares the same hazard and the same guard."""
    from jarvis.cu.actuate.windows import SendInputRefused

    actuator, sent = _pointer_actuator(refuse_after=1)

    with pytest.raises(SendInputRefused):
        actuator.click_at_cursor(expected=(10, 20))

    # No move event here, so injected=1 IS the press.
    assert len(sent) == 2
    assert sent[1] == [("mouse", _LEFT_UP)]


def test_send_input_refused_carries_the_injected_count() -> None:
    """The recovery decision is only possible because the exception carries how
    far the batch got — a plain WinError threw that away."""
    from jarvis.cu.actuate.windows import SendInputRefused

    exc = SendInputRefused("nope", injected=2, total=5)
    assert exc.injected == 2
    assert exc.total == 5
    assert isinstance(exc, OSError), "existing except-OSError handlers must catch it"
