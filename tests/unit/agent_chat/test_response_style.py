"""Human-facing chat runners share the reporting policy; voice keeps spoken rules."""

from pathlib import Path

import pytest

from jarvis.agent_chat import jarvis_harness
from jarvis.agent_chat.runner_api import system_prompt
from jarvis.brain.manager import _WRITTEN_CHAT_STYLE
from jarvis.brain.persona_loader import load_compact_persona_prompt, load_persona_prompt
from jarvis.core.response_style import (
    CONVERSATIONAL_RESPONSE_STYLE,
    CONVERSATIONAL_TURN_REMINDER,
)


def test_api_and_main_chat_receive_the_same_reporting_policy(tmp_path: Path) -> None:
    prompt = system_prompt(cwd=tmp_path, assistant_name="Assistant")
    assert CONVERSATIONAL_RESPONSE_STYLE in prompt
    assert CONVERSATIONAL_RESPONSE_STYLE in _WRITTEN_CHAT_STYLE


def test_fresh_and_resumed_defaults_allow_detail_and_preserve_essential_information() -> None:
    assert CONVERSATIONAL_TURN_REMINDER in CONVERSATIONAL_RESPONSE_STYLE
    for policy in (CONVERSATIONAL_RESPONSE_STYLE, CONVERSATIONAL_TURN_REMINDER):
        assert "one or two short paragraphs" in policy
        assert "someone who is not technical understands it" in policy
        assert "user asks for detail or the task needs" in policy
        assert "not a hard length limit" in policy
        assert "blockers, uncertainty and essential questions" in policy
        assert "Follow explicit user preferences" in policy


def test_reply_style_keeps_the_person_oriented_without_a_persona() -> None:
    # What is happening, what is done, what the person does next — in plain
    # words, with concrete numbers and click paths instead of theory.
    for phrase in (
        "what is happening now, what is done, and what they can do next",
        "Open with the state in the first words",
        "one short sentence saying what you are doing now",
        "real numbers, times and names",
        "exact click path",
        "say sorry once",
        "secure field, never into the chat",
        "No headings, bold labels or bullet walls",
    ):
        assert phrase in CONVERSATIONAL_RESPONSE_STYLE, phrase
    # A general style: no persona, name or flirt register leaks in.
    for leak in ("Jenny", "flirt", "Discord"):
        assert leak not in CONVERSATIONAL_RESPONSE_STYLE


async def test_cli_without_brain_layers_still_receives_the_shared_default(monkeypatch) -> None:
    monkeypatch.setattr(jarvis_harness, "_brain", lambda: None)
    prompt = await jarvis_harness.identity_prompt(user_text="Hello", history=[], resume=None)
    assert CONVERSATIONAL_TURN_REMINDER in prompt
    assert "think longer and go deeper" not in prompt
    assert "Frontier-lab style" not in prompt


def test_plan_mode_keeps_its_read_only_contract(tmp_path: Path) -> None:
    prompt = system_prompt(cwd=tmp_path, assistant_name="Assistant", plan=True)
    assert CONVERSATIONAL_RESPONSE_STYLE in prompt
    assert "PLAN MODE is on" in prompt
    assert "you have no tools that change anything" in prompt


def test_both_voice_personas_include_reporting_without_losing_spoken_rules() -> None:
    for prompt in (load_persona_prompt(), load_compact_persona_prompt()):
        assert "REPORTING RESULTS" in prompt
        assert "SPOKEN" in prompt
        assert "spell every number" in prompt


@pytest.mark.parametrize("briefed", [True, False])
def test_society_api_turn_carries_the_reply_policy_exactly_once(briefed: bool) -> None:
    from types import SimpleNamespace

    from jarvis.brain.manager import _TURN_OVERRIDE, BrainManager
    from jarvis.brain.turn_override import TurnOverride

    briefing = "## How to reply to the person\n" + CONVERSATIONAL_RESPONSE_STYLE
    manager = BrainManager.__new__(BrainManager)
    manager._config = SimpleNamespace(performance=SimpleNamespace(cache_optimized_prompt=True))
    manager._render_live_tool_block = lambda: "TOOLS"
    manager._reply_language_directive = lambda: "LANGUAGE"
    token = _TURN_OVERRIDE.set(
        TurnOverride(
            provider="test",
            system_extra=briefing if briefed else "## Agent\nScout",
            tool_context={"tool_origin": "society"},
        )
    )
    try:
        prompt = manager._build_system_prompt()
    finally:
        _TURN_OVERRIDE.reset(token)
    assert prompt.count(CONVERSATIONAL_RESPONSE_STYLE) == 1
    assert "WRITTEN CHAT STYLE" in prompt
