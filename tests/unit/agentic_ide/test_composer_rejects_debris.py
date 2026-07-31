"""A CLI writes its own errors to stdout, so "it answered" is not "a brief came".

Live 2026-07-26: with the writer on a coding subscription, one CLI aborted on a
flag-validation problem and printed

    Error: invalid model selection (--model "..." --effort ""): ... requires --effort

The composer reported ``composed_by="llm"`` and would have typed that line at a
coding agent as its task. The structural check exists so debris can never again
be mistaken for a composed prompt.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import prompt_blueprint as blueprint
from jarvis.agentic_ide import prompt_composer


def test_a_cli_error_line_is_not_a_brief() -> None:
    debris = (
        'Error: invalid model selection (--model "gemini-3.5-flash" --effort ""): '
        "--model gemini-3.5-flash requires --effort (available: low, medium, high)"
    )
    assert blueprint.looks_like_brief(debris) is False


@pytest.mark.parametrize(
    "debris",
    [
        "",
        "   ",
        "command not found: claude",
        "Traceback (most recent call last):\n  File x, line 1\nRuntimeError: boom",
        "I'm sorry, I cannot help with that.",
    ],
)
def test_other_debris_is_rejected_too(debris: str) -> None:
    assert blueprint.looks_like_brief(debris) is False


@pytest.mark.parametrize(
    "brief",
    [
        "## Task\nDo the thing.",
        "# Task\nDo the thing.\n\n## Done when\nTests pass.",
        "Some lead-in line.\n\n## Task\nDo the thing.",
    ],
)
def test_a_real_brief_survives(brief: str) -> None:
    """The check rejects debris; it must not grade quality."""
    assert blueprint.looks_like_brief(brief) is True


class _Brain:
    """Brain double that answers with whatever it was given."""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def complete(self, _req: object):  # noqa: ANN202 - test double
        from jarvis.core.protocols import BrainDelta

        yield BrainDelta(content=self._answer)


def _session(tmp_path) -> object:  # noqa: ANN001 - pytest tmp_path
    profile = SimpleNamespace(
        instruction_files=[],
        summary_lines=lambda: ["Test workspace"],
    )
    return SimpleNamespace(folder=str(tmp_path), profile=profile)


async def test_composer_falls_back_when_the_writer_returns_debris(tmp_path) -> None:  # noqa: ANN001
    result = await prompt_composer.compose(
        "have a look at the wake word please",
        session=_session(tmp_path),
        terminal_name="Kai",
        brain=_Brain("Error: invalid model selection (--model x --effort '')"),
    )

    assert result.composed_by == "fallback"
    assert "not a brief" in result.note
    # The user still gets something usable, never the raw error.
    assert "Error: invalid model selection" not in result.text
    assert result.text.strip()


async def test_composer_keeps_a_real_brief(tmp_path) -> None:  # noqa: ANN001
    result = await prompt_composer.compose(
        "have a look at the wake word please",
        session=_session(tmp_path),
        terminal_name="Kai",
        brain=_Brain("## Task\nInvestigate the wake word path.\n"),
    )

    assert result.composed_by == "llm"
    assert "## Task" in result.text
