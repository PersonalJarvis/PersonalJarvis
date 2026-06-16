"""Guard the CU prompts that stop the model inventing shell commands.

Regression for the live bug (2026-06-15): the goal "say hello hello hello" in a
terminal was typed as "echo hello hello hello" -- the executor prompt had no rule
to type the user's literal words. These are prompt-content guards (the model's
output is non-deterministic; the prompt instruction is the testable contract).
"""
from __future__ import annotations

from jarvis.harness.screenshot_only_loop import _PLANNER_SYSTEM_PROMPT, _SYSTEM_PROMPT


def test_executor_prompt_carries_literal_dictation_rule() -> None:
    low = _SYSTEM_PROMPT.lower()
    assert "literal dictation" in low
    assert "verbatim" in low
    assert "echo" in low  # the specific failure is named so it cannot recur silently


def test_planner_prompt_carries_literal_dictation_rule() -> None:
    low = _PLANNER_SYSTEM_PROMPT.lower()
    assert "literal dictation" in low
    assert "verbatim" in low
    assert "echo" in low


def test_literal_dictation_does_not_break_judge_routing_keywords() -> None:
    # The added text must not introduce the words the test fakes / live loop use
    # to recognise the done-judge / fail-feasibility prompts.
    for prompt in (_SYSTEM_PROMPT, _PLANNER_SYSTEM_PROMPT):
        low = prompt.lower()
        assert "judge" not in low
        assert "feasibility" not in low
