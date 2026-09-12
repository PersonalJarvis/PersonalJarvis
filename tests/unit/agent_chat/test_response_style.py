"""Human-facing chat runners share the reporting policy; voice keeps spoken rules."""

from pathlib import Path

from jarvis.agent_chat.runner_api import system_prompt
from jarvis.brain.manager import _WRITTEN_CHAT_STYLE
from jarvis.brain.persona_loader import load_compact_persona_prompt, load_persona_prompt
from jarvis.core.response_style import CONVERSATIONAL_RESPONSE_STYLE


def test_api_and_main_chat_receive_the_same_reporting_policy(tmp_path: Path) -> None:
    prompt = system_prompt(cwd=tmp_path, assistant_name="Assistant")
    assert CONVERSATIONAL_RESPONSE_STYLE in prompt
    assert CONVERSATIONAL_RESPONSE_STYLE in _WRITTEN_CHAT_STYLE


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
