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
    TARGET_MAX_CHARS,
    TARGET_MIN_CHARS,
    ends_on_reference,
    looks_truncated,
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
def test_every_kind_bans_the_two_leak_prone_subjects_outright(kind):
    """Verification and reasoning must not appear at all — in EITHER direction.

    The first version said "do not ask the agent to verify", and a live
    composition dutifully wrote "Do not narrate your internal reasoning or
    double-check your own work" INTO the finished prompt: the rule leaked
    instead of being followed. Banning the subject rather than one direction of
    it is what closes that.
    """
    text = system_prompt(kind)
    assert "must not appear in the prompt AT ALL" in text
    assert "not as a requirement and not as a prohibition" in text.replace("\n", " ")
    assert "Write neither." in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_names_the_leak_as_a_defect(kind):
    """Showing the exact leaked line is what makes the rule recognisable."""
    assert "being LEAKED instead of followed" in system_prompt(kind)


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_forbids_inventing_requirements(kind):
    """Still the hard rule — it just no longer suppresses description with it."""
    text = system_prompt(kind)
    assert "INVENTING is forbidden" in text
    assert "the user did not state and the workspace does not establish" in text


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


def test_implement_specifies_the_outcome_without_dictating_the_route():
    """Complete is a statement about CONTEXT, not about enumerated steps.

    The first version paired "give the COMPLETE specification" with a list of
    generic prohibitions to put in `## Scope` — no surrounding cleanup, no
    unrequested refactors, no speculative abstractions. Measured live
    2026-07-27, that combination produced a brief telling the agent to "use
    non-blocking background tasks for writer resolution": a solution the
    composer had ALREADY implemented, so the agent's task became re-doing
    finished work. Specify the outcome; leave the route to the agent, which can
    read the code the writer only saw an outline of.
    """
    text = system_prompt(KIND_IMPLEMENT)
    assert "IMPLEMENTATION task" in text
    assert "Specify the OUTCOME completely" in text
    # Complete must be defined as context, not as dictated steps.
    assert "not that you dictated the steps" in text
    # And the generic prohibition list must stay OUT of `## Scope`.
    assert "only when the user actually drew a line" in text


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
        utterance="tell Alex to check the ranking",
        instruction="check the ranking",
        terminal_name="Alex",
        agent_display="Claude Code",
        profile_lines=["Folder: /repo", "Git repository on branch main"],
        candidates=["jarvis/rank.py"],
        skeletons={"jarvis/rank.py": "def fuse(a, b) -> list: ..."},
        house_rules="Tests: pytest tests/",
    )

    assert "Alex" in block
    assert "Claude Code" in block
    assert "jarvis/rank.py" in block
    assert "def fuse(a, b) -> list: ..." in block
    assert "pytest tests/" in block
    assert "tell Alex to check the ranking" in block


def test_user_block_puts_the_request_after_the_longform_data():
    """Queries at the end measurably improve multi-document performance."""
    block = user_block(
        utterance="do the thing",
        instruction="do the thing",
        terminal_name="Dana",
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
        terminal_name="Dana",
        agent_display="Codex",
        profile_lines=[],
        candidates=[],
        skeletons={},
        house_rules="",
    )

    assert "do the thing" in block
    assert "Dana" in block


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


def test_the_target_length_sits_inside_the_hard_ceiling():
    """The ceiling says what is allowed; the target says what is good.

    Stating only the ceiling produced 549/865/904-character prompts against a
    3000 budget — a model reads "under N" as "be brief".
    """
    assert TARGET_MIN_CHARS < TARGET_MAX_CHARS < MAX_BODY_CHARS


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_states_the_target_length_not_only_the_ceiling(kind):
    """Both bounds, and a floor that names what a too-short brief dropped.

    "Be thorough" used to carry the floor and it bought length from the wrong
    place — the writer padded with dictated implementation steps rather than
    with description. The floor is now stated as the thing actually missing
    from a thin brief: context the writer was handed and did not pass on.
    """
    text = system_prompt(kind)
    assert str(TARGET_MIN_CHARS) in text
    assert str(TARGET_MAX_CHARS) in text
    assert "Under 800 you have dropped context you were given" in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_separates_describing_from_inventing(kind):
    """The anti-invention rule was suppressing legitimate description too."""
    text = system_prompt(kind)
    assert "DESCRIBING is not INVENTING" in text
    assert "INVENTING is forbidden" in text
    assert "DESCRIBING is wanted" in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_asks_for_the_goal_and_not_the_implementation(kind):
    """The rule the whole blueprint turns on, so it is pinned for every kind.

    The writer sees bounded file OUTLINES; the agent sees the code. A route
    chosen from the outline is a guess, and a guessed route gets built even
    when it is wrong or already there — measured live 2026-07-27, a brief
    asked for concurrency that the target module already had. Every current
    frontier prompting guide converges on the same instruction: state the
    goal and the bounds, leave the route to the model.
    """
    text = system_prompt(kind)
    assert "never the implementation" in text
    assert "leave the route open" in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_offers_the_current_behaviour_section(kind):
    """Describing today's code is what saves the agent a discovery round."""
    assert "## How it works today" in system_prompt(kind)


def test_no_kind_tells_the_writer_to_keep_it_short():
    """Brevity instructions are what produced the thin prompts."""
    for kind in _ALL_KINDS:
        assert "Keep it short" not in system_prompt(kind)


# ------------------------------------------------------ truncation guard
def test_a_brief_that_stops_mid_sentence_is_recognised():
    """Measured live: an investigation brief ended on "...to find" and was
    handed over as finished. Half a brief reads as a whole one."""
    assert looks_truncated("## Task\nTrace the path through the pipeline to find")


def test_a_finished_brief_is_not_flagged():
    assert not looks_truncated("## Task\nReview the ranking pipeline.")
    assert not looks_truncated("## Task\nDo it.\n\n## Key files\n- `@a.py` - here")
    assert not looks_truncated("## Task\nCheck this:")


def test_a_list_item_without_a_full_stop_is_not_truncation():
    """The common shape of a finished Key files section — must not fire."""
    assert not looks_truncated("## Task\nDo it.\n\n## Key files\n- `@a.py` - ranking")
    assert not looks_truncated("## Task\nDo it.\n\n## Done when\n- The tests pass")


def test_a_heading_left_dangling_is_not_treated_as_damage():
    """Structural lines carry no punctuation by nature; only prose is judged."""
    assert not looks_truncated("## Task\nDo it.\n\n## Scope")


def test_unformatted_prose_that_finishes_properly_is_accepted():
    """Formatting is the blueprint's job to demand, not this guard's to police."""
    assert not looks_truncated("Review the ranking pipeline and report back.")


def test_empty_output_counts_as_truncated():
    assert looks_truncated("")
    assert looks_truncated("   ")
