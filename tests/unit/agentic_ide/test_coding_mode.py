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
