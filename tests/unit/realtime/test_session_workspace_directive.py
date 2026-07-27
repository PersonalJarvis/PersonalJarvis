"""The live model must know which coding agents are running, by name.

Live failure 2026-07-27 16:53 (Realtime): with a terminal called Dana running in
front of the user, "Was hat Dana gemacht?" was answered with "I cannot tell you,
because I do not know which person Dana is" — and the model was right not to
know. Its session instructions carried the persona, the clock, the tool
directive and the user's preferences, and nothing whatsoever about the coding
workspace two feet away. The user only got a real answer after saying the words
"agentic IDE" out loud, which is not a workflow anybody should have to learn.

The roster is therefore part of the per-turn instructions. What is pinned here
is its two halves: the model is told the NAMES (it cannot route a name it has
never heard of), and it is NOT told the transcripts (those are kilobytes the
orchestrator already holds and re-sending them every turn buys nothing).
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.agentic_ide import session as ide_session
from jarvis.realtime import session as session_module


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a roster the directive will read, without opening terminals."""

    def _install(names: tuple[str, ...]) -> None:
        monkeypatch.setattr(
            ide_session, "running_call_signs", lambda: list(names)
        )

    return _install


def _directive() -> str:
    """``_workspace_directive`` off a bare instance — it touches no other state."""
    instance = object.__new__(session_module.RealtimeVoiceSession)
    return session_module.RealtimeVoiceSession._workspace_directive(instance)


def test_the_roster_names_every_running_terminal(workspace: Any) -> None:
    workspace(("Dana", "Logan", "Casey"))
    directive = _directive()
    for name in ("Dana", "Logan", "Casey"):
        assert name in directive


def test_the_roster_tells_the_model_a_call_sign_is_not_a_person(
    workspace: Any,
) -> None:
    """The exact wrong answer of the live failure is what this must prevent."""
    workspace(("Dana",))
    directive = _directive().casefold()
    assert "not people" in directive
    assert "do not know who" in directive


def test_no_open_workspace_adds_nothing(workspace: Any) -> None:
    """With no terminals running the instructions stay byte-identical."""
    workspace(())
    assert _directive() == ""


def test_a_broken_workspace_never_breaks_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coding surface is optional; a fault in it must degrade to silence."""

    def _boom() -> list[str]:
        raise RuntimeError("registry is mid-reload")

    monkeypatch.setattr(ide_session, "running_call_signs", _boom)
    assert _directive() == ""


def test_the_directive_reaches_the_session_instructions(workspace: Any) -> None:
    workspace(("Dana",))
    rendered = session_module._session_instructions(
        "de", workspace_directive=_directive()
    )
    assert "Dana" in rendered
    # Omitting it must not smuggle a stale roster in from anywhere else.
    assert "Dana" not in session_module._session_instructions("de")


def test_the_roster_carries_names_only_not_transcripts(workspace: Any) -> None:
    """Terminal output belongs to the orchestrator, not to every turn's prompt."""
    workspace(("Dana", "Logan"))
    directive = _directive()
    # Comfortably under a kilobyte for a two-pane workspace: this block is
    # re-sent on every single final transcript of a live call.
    assert len(directive) < 1_000
