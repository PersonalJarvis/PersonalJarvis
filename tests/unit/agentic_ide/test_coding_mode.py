"""The one predicate that answers "is Jarvis an Agentic IDE right now?".

Coding mode changes how the assistant answers on EVERY screen, so more than one
layer has to agree about it: the app-wide indicator, the focus-mode context
block, and (in future) the routing gates. These tests pin the two halves of the
rule and the payload that carries it to the UI, because a surface that reports a
mode the assistant does not actually have is worse than no indicator at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import (
    Registry,
    coding_mode_active,
    coding_mode_event,
    reset_registry,
)
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Registry:
    registry = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    return registry


def test_no_workspace_is_not_coding_mode() -> None:
    """The flag without a workspace addresses nothing."""
    assert coding_mode_active() is False


async def test_workspace_alone_is_not_coding_mode(
    wired: Registry, tmp_path: Path
) -> None:
    """Terminals on a screen are not the mode — the switch has to be on."""
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    assert coding_mode_active() is False


async def test_both_halves_make_the_mode(wired: Registry, tmp_path: Path) -> None:
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    assert coding_mode_active() is True


async def test_leaving_the_mode_turns_the_predicate_off(
    wired: Registry, tmp_path: Path
) -> None:
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    wired.set_focus_mode(False)
    assert coding_mode_active() is False


async def test_closing_the_workspace_ends_the_mode(
    wired: Registry, tmp_path: Path
) -> None:
    """The mode cannot outlive the workspace it applies to."""
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    await wired.end()
    assert coding_mode_active() is False


async def test_event_carries_the_effective_mode_not_the_flag(
    wired: Registry, tmp_path: Path
) -> None:
    """The payload a client renders must agree with the predicate."""
    session = await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)

    on = coding_mode_event(wired.session, source_layer="test")
    assert on.enabled is True
    assert on.session_id == session.id
    assert on.folder == session.folder
    assert on.workspace == session.name

    wired.set_focus_mode(False)
    off = coding_mode_event(wired.session, source_layer="test")
    assert off.enabled is False
    # Nothing to name when the mode is off — a client must not render a
    # workspace label next to an "off" badge.
    assert off.folder == ""
    assert off.workspace == ""


def test_event_survives_having_no_session() -> None:
    """The close path passes None; it must produce an honest 'off', not a crash."""
    event = coding_mode_event(None, source_layer="test")
    assert event.enabled is False
    assert event.session_id == ""


async def test_event_reaches_the_ui_as_a_ws_envelope(
    wired: Registry, tmp_path: Path
) -> None:
    """The frontend keys off `event_name`; a rename would silently kill the badge."""
    from jarvis.ui.web.schema import event_to_ws_envelope

    await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    envelope = event_to_ws_envelope(coding_mode_event(wired.session, source_layer="t"))

    assert envelope["event_name"] == "AgenticIdeCodingModeChanged"
    assert envelope["payload"]["enabled"] is True


# --------------------------------------------------- the persona follows the mode
#
# Coding mode also swaps the assistant's CHARACTER (the ``coding`` mode) through
# an in-memory section override on the persona layer. That override must agree
# with ``coding_mode_active`` after EVERY transition, not only after the toggle:
# left behind by a closed or switched-away workspace it kept the assistant in
# coding character for the rest of the process, and — because the override
# outranks the user's choice — made the modes screen look as if switching modes
# did nothing at all.


def _persona_override() -> str | None:
    from jarvis.brain import modes

    return modes.section_override()


@pytest.fixture(autouse=True)
def _clear_persona_override():
    from jarvis.brain import modes

    modes.set_section_override(None)
    yield
    modes.set_section_override(None)


async def test_the_persona_override_tracks_the_toggle(
    wired: Registry, tmp_path: Path
) -> None:
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    assert _persona_override() is None
    wired.set_focus_mode(True)
    assert _persona_override() == "coding"
    wired.set_focus_mode(False)
    assert _persona_override() is None


async def test_closing_the_last_workspace_drops_the_persona_override(
    wired: Registry, tmp_path: Path
) -> None:
    """The exact bug: coding character stuck on after the workspace was closed."""
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    await wired.end()
    assert coding_mode_active() is False
    assert _persona_override() is None


async def test_clearing_the_front_drops_the_persona_override(
    wired: Registry, tmp_path: Path
) -> None:
    """The wizard for another workspace takes the front away — and the mode."""
    await wired.start(str(tmp_path), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    await wired.activate(None)
    assert coding_mode_active() is False
    assert _persona_override() is None


async def test_switching_workspaces_moves_the_persona_override_with_the_flag(
    wired: Registry, tmp_path: Path
) -> None:
    """The flag is per workspace, so the persona follows whichever is in front."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = await wired.start(str(tmp_path / "a"), [{"agent": "claude"}])
    wired.set_focus_mode(True)
    second = await wired.start(str(tmp_path / "b"), [{"agent": "claude"}])
    # A freshly opened workspace is at the front with the mode OFF.
    assert wired.session is second
    assert coding_mode_active() is False
    assert _persona_override() is None

    await wired.activate(first.id)
    assert coding_mode_active() is True
    assert _persona_override() == "coding"

    # Closing the front workspace hands the front to the survivor, whose flag
    # is off — the override must not linger from the one that just closed.
    await wired.end(first.id)
    assert wired.session is second
    assert coding_mode_active() is False
    assert _persona_override() is None

