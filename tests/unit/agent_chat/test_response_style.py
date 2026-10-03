"""Human-facing chat runners share the reporting policy; voice keeps spoken rules."""

from pathlib import Path

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
        assert "one to three short sentences in one paragraph" in policy
        assert "user asks for detail or the task needs" in policy
        assert "not a hard length limit" in policy
        assert "blockers, uncertainty and essential questions" in policy
        assert "Follow explicit user preferences" in policy


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
