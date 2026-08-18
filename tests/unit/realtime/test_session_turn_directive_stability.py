"""The tool directive is ONE stable role plus ONE turn-scoped line (RT-08).

The role used to be shipped again per turn with a different order glued to its
end — "CALL jarvis_action for EVERY turn that needs the user's world", then
"answer directly now, call no function", then "do not answer at all". On the
append-only steering channel all three stood side by side with nothing
retracted, and the model became inconsistent on borderline turns.

These tests pin the shape: the role never moves, the turn line always names
itself as replacing the previous one, and only the short line travels.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import jarvis.realtime.session as session_mod
from tests.unit.realtime.test_gemini_live import (
    _drive,
    _steering_session,
    _user_speaking_message,
)

_MODES = (
    {},
    {"delegate_required": True},
    {"action_pending": True},
    {"delegate_discouraged": True},
)


def _bare_session(supports_direct_tools: bool = True):
    session = session_mod.RealtimeVoiceSession.__new__(
        session_mod.RealtimeVoiceSession
    )
    session._delegate_enabled = True
    session._tool_bridge = None
    session._provider = SimpleNamespace(
        supports_direct_tools=supports_direct_tools
    )
    return session


def test_the_role_is_identical_in_every_turn_mode() -> None:
    session = _bare_session()
    roles = {
        session._tool_directive(**mode).split(session_mod._TURN_MODE_PREFIX)[0]
        for mode in _MODES
    }
    assert roles == {f"{session_mod._DELEGATE_ROLE_DIRECTIVE}\n\n"}


def test_every_turn_mode_line_retracts_the_previous_one() -> None:
    session = _bare_session()
    lines = [session._turn_mode_directive(**mode) for mode in _MODES]
    for line in lines:
        assert line.startswith(session_mod._TURN_MODE_PREFIX)
        # Short enough to read as one order, never a second role.
        assert len(line) < 800
        assert session_mod._DELEGATE_ROLE_DIRECTIVE not in line
    # Four distinct modes, four distinct lines — including a real sentence for
    # the unremarkable turn: an empty one would retract nothing.
    assert len(set(lines)) == len(_MODES)


def test_the_neutral_turn_mode_is_part_of_the_connect_instructions() -> None:
    """So the first turn of a call dedups instead of re-sending the mode."""
    session = _bare_session()
    assert session._turn_mode_directive() in session._tool_directive()


def test_a_delegate_less_session_has_no_turn_mode_line() -> None:
    session = _bare_session()
    session._delegate_enabled = False
    assert session._turn_mode_directive() == ""


@pytest.mark.asyncio
async def test_only_the_short_mode_line_travels_on_the_gemini_delta() -> None:
    session = _bare_session()
    connect_instructions = session._tool_directive()
    sent: list[dict[str, str]] = []
    live, queue = _steering_session(sent, instructions=connect_instructions)

    # Turn 1 is unremarkable: the model already has this exact line from the
    # fixed system instruction, so nothing travels at all.
    await _drive(live, queue, [_user_speaking_message()])
    await live.update_session(turn_directive=session._turn_mode_directive())
    assert sent == []

    # Turn 2 hands the turn to the orchestrator: only the short line travels.
    await _drive(live, queue, [_user_speaking_message("mach das")])
    await live.update_session(
        turn_directive=session._turn_mode_directive(delegate_required=True)
    )
    assert len(sent) == 1
    text = sent[0]["text"]
    assert session_mod._DELEGATE_REQUIRED_DIRECTIVE in text
    assert session_mod._DELEGATE_ROLE_DIRECTIVE not in text
    assert len(text) < 1_000

    # Turn 3 repeats that mode: an unchanged directive sends nothing.
    await _drive(live, queue, [_user_speaking_message("und weiter")])
    await live.update_session(
        turn_directive=session._turn_mode_directive(delegate_required=True)
    )
    assert len(sent) == 1

    # Turn 4 returns to normal — and that retraction DOES travel.
    await _drive(live, queue, [_user_speaking_message("danke dir")])
    await live.update_session(turn_directive=session._turn_mode_directive())
    assert len(sent) == 2
    assert session_mod._DELEGATE_TURN_NORMAL_DIRECTIVE in sent[1]["text"]
    assert session_mod._DELEGATE_ROLE_DIRECTIVE not in sent[1]["text"]
