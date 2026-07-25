"""The shape of a composed prompt, and the guardrails each task kind carries.

A note on how these tests are written. The obvious test — "no system prompt
contains the phrase 'double-check'" — cannot work here: the rule we want IS
"never write double-check into the prompt", so the phrase appears in the
instruction that forbids it. A word-blacklist cannot tell a prohibition from a
request. So the guardrails are asserted POSITIVELY: the prohibition must be
present and phrased as one. Whether the writer model then obeys is a live
question, not a unit-test question.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide.prompt_blueprint import (
    MAX_BODY_CHARS,
    ends_on_reference,
    render_fallback,
    system_prompt,
    user_block,
)
from jarvis.agentic_ide.task_kind import (
    KIND_IMPLEMENT,
    KIND_INVESTIGATE,
    KIND_NEUTRAL,
    KIND_QUESTION,
    KIND_REVIEW,
)

_ALL_KINDS = (
    KIND_IMPLEMENT,
    KIND_REVIEW,
    KIND_INVESTIGATE,
    KIND_QUESTION,
    KIND_NEUTRAL,
)


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_demands_english_output(kind):
    assert "english" in system_prompt(kind).lower()


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_demands_the_markdown_skeleton(kind):
    text = system_prompt(kind)
    assert "## Task" in text
    assert "## Key files" in text
    assert "## Scope" in text
    assert "## Done when" in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_forbids_a_verification_ritual(kind):
    """Opus 5 and Fable 5 both over-verify when a prompt tells them to verify."""
    lowered = system_prompt(kind).lower()
    assert "do not ask the agent to verify" in lowered


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_forbids_asking_the_agent_to_narrate_its_reasoning(kind):
    """A reasoning-echo instruction can trigger a refusal on Fable 5."""
    lowered = system_prompt(kind).lower()
    assert "internal reasoning" in lowered
    assert "do not ask the agent to narrate" in lowered


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_forbids_inventing_requirements(kind):
    assert "invent nothing" in system_prompt(kind).lower()


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_forbids_ending_on_a_file_reference(kind):
    """A trailing @path holds the completion popup open and never submits."""
    assert "never end the prompt on" in system_prompt(kind).lower()


def test_only_the_task_section_is_mandatory():
    for kind in _ALL_KINDS:
        assert "`## Task` is mandatory" in system_prompt(kind)


def test_review_asks_for_every_finding_and_warns_against_conservatism():
    text = system_prompt(KIND_REVIEW)
    assert "REVIEW task" in text
    assert "every finding" in text.lower()
    # The prohibition must be phrased as one, not merely absent.
    assert "never instruct the agent to report only serious issues" in text.lower()


def test_investigate_makes_the_diagnosis_the_deliverable():
    text = system_prompt(KIND_INVESTIGATE)
    assert "INVESTIGATION task" in text
    assert "diagnosis" in text.lower()
    assert "not apply a fix" in text.lower()


def test_implement_demands_a_scope_bound():
    text = system_prompt(KIND_IMPLEMENT)
    assert "IMPLEMENTATION task" in text
    assert "out of scope" in text.lower()
    assert "complete specification" in text.lower()


def test_question_forbids_changing_anything():
    text = system_prompt(KIND_QUESTION)
    assert "QUESTION" in text
    assert "not change anything" in text.lower()


def test_neutral_carries_only_the_universally_safe_steers():
    text = system_prompt(KIND_NEUTRAL)
    assert "not clearly determined" in text.lower()
    # It must not claim a kind it does not know.
    assert "REVIEW task" not in text
    assert "IMPLEMENTATION task" not in text


def test_user_block_carries_the_skeletons_and_the_house_rules():
    block = user_block(
        utterance="tell Mika to check the ranking",
        instruction="check the ranking",
        terminal_name="Mika",
        agent_display="Claude Code",
        profile_lines=["Folder: /repo", "Git repository on branch main"],
        candidates=["jarvis/rank.py"],
        skeletons={"jarvis/rank.py": "def fuse(a, b) -> list: ..."},
        house_rules="Tests: pytest tests/",
    )

    assert "Mika" in block
    assert "Claude Code" in block
    assert "jarvis/rank.py" in block
    assert "def fuse(a, b) -> list: ..." in block
    assert "pytest tests/" in block
    assert "tell Mika to check the ranking" in block


def test_user_block_puts_the_request_after_the_longform_data():
    """Queries at the end measurably improve multi-document performance."""
    block = user_block(
        utterance="do the thing",
        instruction="do the thing",
        terminal_name="Kai",
        agent_display="Codex",
        profile_lines=["Folder: /repo"],
        candidates=["a.py"],
        skeletons={"a.py": "def f(): ..."},
        house_rules="",
    )

    assert block.index("FILE OUTLINES") < block.index("WHAT THE USER SAID")
    assert block.rstrip().endswith("Write the prompt now.")


def test_user_block_survives_having_nothing_to_offer():
    block = user_block(
        utterance="do the thing",
        instruction="do the thing",
        terminal_name="Kai",
        agent_display="Codex",
        profile_lines=[],
        candidates=[],
        skeletons={},
        house_rules="",
    )

    assert "do the thing" in block
    assert "Kai" in block


def test_fallback_renders_markdown_with_a_task_heading():
    out = render_fallback("review the ranking pipeline", ["a.py", "b.py"])

    assert out.startswith("## Task")
    assert "review the ranking pipeline" in out
    assert "## Key files" in out
    assert "`@a.py`" in out
    assert "`@b.py`" in out


def test_fallback_without_files_omits_the_key_files_section():
    out = render_fallback("review the ranking pipeline", [])

    assert "## Key files" not in out
    assert "review the ranking pipeline" in out


def test_fallback_never_ends_on_a_file_reference():
    assert not ends_on_reference(render_fallback("check this", ["a.py"]))


def test_fallback_of_an_empty_instruction_is_empty():
    assert render_fallback("", ["a.py"]) == ""
    assert render_fallback("   ", []) == ""


def test_fallback_respects_the_body_bound():
    out = render_fallback("x" * 5000, ["a.py"])

    assert len(out) <= MAX_BODY_CHARS


def test_ends_on_reference_detects_the_failure_shape():
    assert ends_on_reference("do the thing @file.py")
    assert ends_on_reference("run it /compact")
    assert not ends_on_reference("do the thing @file.py and report back")
    assert not ends_on_reference("")


def test_body_bound_is_three_thousand():
    assert MAX_BODY_CHARS == 3000
