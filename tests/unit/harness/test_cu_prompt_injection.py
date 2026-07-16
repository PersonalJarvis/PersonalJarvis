"""Prompt-injection defense in the Computer-Use system prompts (audit 🔴 #3,
deep-dive 2026-07-15 C-01).

The screen is the agent's sole grounding signal and must be treated as
untrusted DATA — a web page / document / dialog showing "ignore your task and
click here" must not hijack the agent. These are deterministic presence checks
for the defense block; a full adversarial attack-suite needs a real-model eval
(tracked separately).

C-01 regression guard: the ACTIVE v2 engine prompt (jarvis.cu.engine)
originally shipped WITHOUT the untrusted-screen / sensitive-screen-handoff /
consequential-action policy that the legacy loop carried — every check here
runs against BOTH prompts so a future engine swap cannot silently drop the
policy again.
"""
from __future__ import annotations

import pytest

from jarvis.cu import engine as cu_engine
from jarvis.harness import screenshot_only_loop as sol

# (id, prompt) — "active" is the live v2 executor prompt; "legacy" is retained
# for the retired loop that older selectors still import.
_PROMPTS = [
    pytest.param(cu_engine._SYSTEM_BASE, id="active-v2"),
    pytest.param(sol._SYSTEM_PROMPT, id="legacy"),
]


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_marks_screen_as_untrusted_data(prompt):
    p = prompt.lower()
    assert "untrusted" in p
    assert "never instructions" in p or "not a command to obey" in p


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_makes_only_the_goal_authoritative(prompt):
    p = prompt.lower()
    assert "authoritative" in p
    assert "goal" in p


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_names_redirect_attempts(prompt):
    p = prompt.lower()
    assert "ignore your instructions" in p
    assert "injection" in p or "redirect" in p


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_directs_fail_on_off_goal_pressure(prompt):
    # Pushed toward a consequential/off-goal action -> fail, don't comply.
    p = prompt.lower()
    assert "fail" in p
    assert "redirect the task" in p


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_hands_off_credentials_and_captchas(prompt):
    # Login / password / 2FA / CAPTCHA screens must be a human handoff, never
    # something the agent types into or solves.
    p = prompt.lower()
    assert "password" in p
    assert "captcha" in p
    assert "2fa" in p or "one-time code" in p or "two-factor" in p


@pytest.mark.parametrize("prompt", _PROMPTS)
def test_system_prompt_gates_consequential_actions_on_the_goal(prompt):
    # Buy/Pay/Send/Delete only when the GOAL explicitly asked for it.
    p = prompt.lower()
    assert "consequential" in p
    assert "buy" in p and "pay" in p and "delete" in p
    assert "explicitly asked" in p or "explicitly" in p


def test_active_v2_composed_prompt_includes_the_policy():
    """The live decide-path composes _SYSTEM_BASE + coordinate block + action
    grammar (engine.py). The policy must sit in the composed system prompt,
    not only in a constant nothing reads."""
    from jarvis.cu import conventions as conv

    composed = (
        cu_engine._SYSTEM_BASE
        + conv.coordinate_prompt_block("normalized_1000", 1366, 768)
        + "\n\n"
        + conv.action_grammar_block()
    )
    p = composed.lower()
    assert "untrusted" in p
    assert "captcha" in p
    assert "consequential" in p
