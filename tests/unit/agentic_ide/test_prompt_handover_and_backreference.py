"""The two ways a spoken order lost its task on the way to a coding agent.

Both were measured on one live composition (2026-07-29). The user said, of a
transcription-quality discussion Jarvis had just answered with four numbered
levers: "mach bitte für T2 und T3 den Prompt … vor allen Dingen Punkt zwei und
drei". What the two panes received was a brief whose ``## Task`` read "Create or
update the system prompts for terminals T2 and T3" and whose acceptance
criteria asked them to incorporate "points 2 and 3 from the current context".

So the handover became the work, and the work became a pointer to something the
recipient cannot open. Neither agent could have done what was asked.

These tests pin the two rules and the plumbing that makes the second one
possible. Whether the writer model then obeys is a live question — but a rule
that is absent cannot be obeyed at all, and the conversation it needs was not
being passed to it in ANY form.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import conversation
from jarvis.agentic_ide.prompt_blueprint import system_prompt, user_block
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


class _Message:
    """Stand-in for a ``BrainMessage``; the real history is provider-owned."""

    def __init__(self, role: str, content: object) -> None:
        self.role = role
        self.content = content


# ------------------------------------------------- the handover is not the work
@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_says_the_brief_itself_is_the_prompt(kind):
    """"Prompt T2" asks for THIS brief — it is not a task about prompts."""
    text = " ".join(system_prompt(kind).split())
    assert "THE BRIEF YOU ARE WRITING IS THAT PROMPT" in text
    assert "Never make prompt-writing the agent's task" in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_keeps_the_one_case_where_prompts_are_the_task(kind):
    """A repo whose prompt TEXT is being edited is real work, not a handover."""
    text = " ".join(system_prompt(kind).split())
    assert "prompt text that lives in the repository as a file" in text


# --------------------------------------------- back-references must be resolved
@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_demands_back_references_be_resolved(kind):
    text = " ".join(system_prompt(kind).split())
    assert "The brief is ALL the agent gets" in text
    assert "points two and three" in text
    assert "must be REPLACED by what it stands for" in text


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_every_kind_says_what_to_do_with_an_unresolvable_reference(kind):
    """Leaving it out beats forwarding a pointer that resolves to nothing."""
    text = system_prompt(kind).replace("\n", " ")
    assert "cannot be resolved from what you were given" in text


# ------------------------------------------------------ the conversation block
def test_user_block_carries_the_conversation_and_keeps_it_before_the_request():
    block = user_block(
        utterance="mach bitte für T2 und T3 den Prompt, vor allem Punkt zwei und drei",
        instruction="Punkt zwei und drei",
        terminal_name="T2",
        agent_display="Claude Code",
        profile_lines=["Folder: /repo"],
        candidates=["jarvis/stt.py"],
        skeletons={"jarvis/stt.py": "def transcribe(): ..."},
        house_rules="",
        conversation=(
            ("user", "wie können wir die Transkription schärfen?"),
            ("assistant", "Zweitens die VAD-Schwellenwerte, drittens ein Post-Processing-Modul."),
        ),
    )

    assert "VAD-Schwellenwerte" in block
    assert "Post-Processing-Modul" in block
    # It is context for the sentence, so it stands with it — after the outlines
    # and before the words it explains.
    assert block.index("FILE OUTLINES") < block.index("THE CONVERSATION THIS CAME OUT OF")
    assert block.index("THE CONVERSATION THIS CAME OUT OF") < block.index(
        "WHAT THE USER SAID"
    )


def test_user_block_says_the_agent_never_sees_the_conversation():
    """The writer has to know the block is its job to resolve, not to forward."""
    block = user_block(
        utterance="do it",
        instruction="do it",
        terminal_name="T2",
        agent_display="Codex",
        profile_lines=[],
        candidates=[],
        skeletons={},
        house_rules="",
        conversation=(("assistant", "Four levers: one, two, three, four."),),
    )

    assert "the agent will never see any of it" in block.replace("\n", " ")


def test_user_block_without_a_conversation_is_unchanged():
    """The common case — a first turn — must not gain an empty header."""
    block = user_block(
        utterance="do it",
        instruction="do it",
        terminal_name="T2",
        agent_display="Codex",
        profile_lines=[],
        candidates=[],
        skeletons={},
        house_rules="",
        conversation=(),
    )

    assert "THE CONVERSATION" not in block


# ------------------------------------------------------- history normalisation
def test_from_messages_keeps_only_plain_user_and_assistant_text():
    turns = conversation.from_messages(
        [
            _Message("user", "first"),
            _Message("tool", "a tool result"),
            _Message("assistant", ["not", "a", "string"]),
            _Message("assistant", "second"),
        ]
    )

    assert turns == (("user", "first"), ("assistant", "second"))


def test_from_messages_keeps_the_most_recent_window():
    turns = conversation.from_messages(
        [_Message("user", f"turn {i}") for i in range(conversation.MAX_MESSAGES + 3)]
    )

    assert len(turns) == conversation.MAX_MESSAGES
    assert turns[-1][1].endswith(str(conversation.MAX_MESSAGES + 2))


def test_from_messages_drops_the_instruction_being_composed():
    """The live history may already hold the sentence we are composing from."""
    turns = conversation.from_messages(
        [_Message("user", "earlier"), _Message("user", "  prompt T2 now  ")],
        exclude="prompt T2 now",
    )

    assert turns == (("user", "earlier"),)


def test_an_over_long_message_keeps_both_of_its_ends():
    """A back-reference points at an answer's conclusions as often as its start."""
    side = conversation.MAX_MESSAGE_CHARS
    body = "A" * side + "MIDDLE" + "Z" * side
    (role, text), = conversation.from_messages([_Message("assistant", body)])

    assert role == "assistant"
    assert text.startswith("A")
    assert text.endswith("Z")
    assert "MIDDLE" not in text
    assert len(text) <= conversation.MAX_MESSAGE_CHARS + 10


def test_render_labels_who_said_what():
    """"Points two and three" points into Jarvis's answer, not the question."""
    out = conversation.render(
        (("user", "how do we sharpen it?"), ("assistant", "Four levers."))
    )

    assert out.splitlines() == [
        "The user: how do we sharpen it?",
        "Jarvis: Four levers.",
    ]


def test_render_of_nothing_is_empty():
    assert conversation.render(()) == ""
    assert conversation.render((("user", "   "),)) == ""
