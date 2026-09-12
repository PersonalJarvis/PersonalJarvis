"""Agent-specific API and CLI briefings retain conversational reply guidance."""

from jarvis.agent_chat.jarvis_harness import Identity, compact_identity, identity_prompt
from jarvis.agent_chat.runner_cli import _with_identity
from jarvis.core.response_style import (
    CONVERSATIONAL_RESPONSE_STYLE,
    CONVERSATIONAL_TURN_REMINDER,
)
from jarvis.society.roster import AgentRecord
from jarvis.society.surface import build_briefing


def _briefing(extra: str = "") -> str:
    agent = AgentRecord.from_row(
        {
            "agent_id": "reply-style-test",
            "name": "Research assistant",
            "tier": "specialist",
            "description": "Explain findings simply. Ask before publishing anything." + extra,
        }
    )
    return build_briefing(agent, [], [agent])


def test_agent_briefing_includes_reply_style_without_replacing_its_instructions() -> None:
    briefing = _briefing()
    assert CONVERSATIONAL_RESPONSE_STYLE in briefing
    assert "Ask before publishing anything." in briefing
    assert "You are Research assistant" in briefing
    assert "end with a handoff" not in briefing


async def test_cli_identity_carries_the_same_reply_policy_on_fresh_and_resumed_turns() -> None:
    for resume in (None, "existing-vendor-session"):
        identity = await identity_prompt(
            user_text="Explain the result.", history=[], resume=resume, prompt_override=_briefing()
        )
        assert CONVERSATIONAL_RESPONSE_STYLE in identity
        assert CONVERSATIONAL_RESPONSE_STYLE in compact_identity(identity)
        seat = Identity(
            session_id="society:reply-style-test", text=identity, compact=compact_identity(identity)
        )
        delivered = _with_identity("Explain the result.", seat, resume)
        expected = CONVERSATIONAL_TURN_REMINDER if resume else CONVERSATIONAL_RESPONSE_STYLE
        assert expected in delivered
        assert delivered.endswith("Explain the result.")


async def test_reply_policy_survives_long_instructions_in_a_compact_cli_identity() -> None:
    identity = await identity_prompt(
        user_text="Explain the result.",
        history=[],
        resume=None,
        prompt_override=_briefing("\nDetailed domain context." * 1000),
    )
    compact = compact_identity(identity)
    assert len(compact) < len(identity)
    assert CONVERSATIONAL_RESPONSE_STYLE in compact
