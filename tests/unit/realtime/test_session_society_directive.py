"""The live model must know which of the user's agents exist, by name.

Live failure 2026-09-03 (Realtime): with a freshly created Gmail agent on the
island, "welche Agents hast du?" was answered from the retired sub-agent
system, and "Gmail agent, check my inbox" was answered natively — the
session instructions carried the persona, the clock and the coding-terminal
roster, and nothing about the agent society. The twin of the workspace
directive: names and the routing rule go into the per-turn instructions;
the team card with each agent's hands stays with the orchestrator.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.realtime import session as session_module
from jarvis.society import lead_card


@pytest.fixture
def society(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(names: tuple[str, ...]) -> None:
        monkeypatch.setattr(lead_card, "society_agent_names", lambda: names)

    return _install


def _directive() -> str:
    instance = object.__new__(session_module.RealtimeVoiceSession)
    return session_module.RealtimeVoiceSession._society_directive(instance)


def test_the_directive_names_every_live_agent(society: Any) -> None:
    society(("Gmail agent", "Scout"))
    directive = _directive()
    assert "Gmail agent" in directive
    assert "Scout" in directive


def test_the_directive_routes_the_team_to_the_action_function(society: Any) -> None:
    society(("Gmail agent",))
    directive = _directive().casefold()
    assert "not people" in directive
    assert "call your action function" in directive
    assert "retired sub-agent" in directive
    assert "do not know who" in directive


def test_empty_snapshot_requires_verified_lookup(society: Any) -> None:
    society(())
    assert "use society_status to verify" in _directive()
    assert "read-only lookup" in _directive()


def test_the_instructions_carry_the_directive_in_both_profiles() -> None:
    line = "[Agent society — the user's own agents, live right now]"
    for compact in (False, True):
        text = session_module._session_instructions(
            "de", society_directive=line, compact=compact
        )
        assert line in text


def test_the_workspace_directive_calls_terminals_terminals(monkeypatch: pytest.MonkeyPatch):
    """Since the society exists, 'agent' is taken: a pane is a coding terminal."""
    from jarvis.agentic_ide import session as ide_session

    monkeypatch.setattr(ide_session, "running_call_signs", lambda: ["T1"])
    instance = object.__new__(session_module.RealtimeVoiceSession)
    directive = session_module.RealtimeVoiceSession._workspace_directive(instance)
    assert "coding terminals are running" in directive
    assert "not society agents" in directive
