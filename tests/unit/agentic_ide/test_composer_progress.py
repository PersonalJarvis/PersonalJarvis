"""The composer says what it is doing while it does it.

Writing a brief takes 10-27 s — a quality-tier model reading file outlines,
and on a subscription CLI a cold process start before it thinks at all. For all
of that the terminal used to show nothing, which makes a working composer and a
wedged one look identical, and makes the wait feel longer than it is.

So the beats are part of the contract now, and these guards pin the three
properties that make them worth printing rather than noise: they arrive in
order, every line names the pane it belongs to (a fleet composes at once, and
an unattributed line is worse than none), and none of them can ever cost the
prompt itself.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import prompt_composer


class _Writer:
    """Stands in for a resolved quality-tier Brain."""

    name = "test-writer"


class _StreamingBrain:
    """Brain double that answers in two deltas, like a real stream."""

    name = "streaming-writer"

    async def complete(self, _req: object):  # noqa: ANN202 - test double
        from jarvis.core.protocols import BrainDelta

        yield BrainDelta(content="## Task\nLook at the wake word path.")
        yield BrainDelta(content="\n\n## Done when\nThe cause is named.")


@pytest.fixture(autouse=True)
def _writer_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: (_Writer(), "test"))


def _session(tmp_path) -> object:  # noqa: ANN001 - pytest tmp_path
    profile = SimpleNamespace(
        instruction_files=[], summary_lines=lambda: ["Test workspace"]
    )
    return SimpleNamespace(folder=str(tmp_path), profile=profile)


def _brief(**_kwargs: object) -> object:
    async def _answer() -> str:
        return "## Task\nLook at the wake word path."

    return _answer()


async def test_the_beats_arrive_in_order_and_name_the_pane(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    seen: list[prompt_composer.ComposeNotice] = []
    monkeypatch.setattr(prompt_composer, "_llm_compose", _brief)

    result = await prompt_composer.compose(
        "schau dir mal den wake word pfad an",  # i18n-allow: German speech input under test
        session=_session(tmp_path),
        terminal_name="Kate",
        on_progress=seen.append,
    )

    assert result.composed_by == "llm"
    assert [n.stage for n in seen] == [
        prompt_composer.STAGE_START,
        prompt_composer.STAGE_THINKING,
        prompt_composer.STAGE_READY,
    ]
    assert all("Kate" in n.message for n in seen)
    assert all(n.terminal == "Kate" for n in seen)
    # The closing line reports what was actually written, not that something was.
    assert "characters" in seen[-1].message


async def test_the_first_token_says_the_model_started_writing(
    tmp_path,
) -> None:  # noqa: ANN001
    """The gap before the first token IS the wait on a cold subscription CLI."""
    seen: list[prompt_composer.ComposeNotice] = []

    await prompt_composer.compose(
        "why does the wake word not fire",
        session=_session(tmp_path),
        terminal_name="Kate",
        brain=_StreamingBrain(),
        on_progress=seen.append,
    )

    stages = [n.stage for n in seen]
    assert prompt_composer.STAGE_DRAFTING in stages
    # It lands between "thinking" and "ready", and only once however many
    # deltas arrive.
    assert stages.count(prompt_composer.STAGE_DRAFTING) == 1
    assert stages.index(prompt_composer.STAGE_THINKING) < stages.index(
        prompt_composer.STAGE_DRAFTING
    ) < stages.index(prompt_composer.STAGE_READY)


async def test_the_opening_line_follows_the_instruction(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """Context-aware, not one canned sentence read past after the second time."""
    monkeypatch.setattr(prompt_composer, "_llm_compose", _brief)
    session = _session(tmp_path)

    lines: list[str] = []
    for spoken in (
        "review the wake word detection",
        "build a retry into the uploader",
        "why does the bar freeze when I hang up",
    ):
        seen: list[prompt_composer.ComposeNotice] = []
        await prompt_composer.compose(
            spoken, session=session, terminal_name="Kate", on_progress=seen.append
        )
        opening = next(
            n.message for n in seen if n.stage == prompt_composer.STAGE_START
        )
        lines.append(opening)
        # The user's own words come back, so a misheard instruction is visible
        # before the model has spent 20 s on it.
        assert spoken.split()[-1] in opening

    assert len(set(lines)) == len(lines), "three instructions, three openings"


async def test_the_same_instruction_always_reads_the_same(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """Varied per instruction, never per run — a random line cannot be pinned."""
    monkeypatch.setattr(prompt_composer, "_llm_compose", _brief)
    session = _session(tmp_path)

    async def opening() -> str:
        seen: list[prompt_composer.ComposeNotice] = []
        await prompt_composer.compose(
            "review the wake word detection",
            session=session,
            terminal_name="Kate",
            on_progress=seen.append,
        )
        return next(n.message for n in seen if n.stage == prompt_composer.STAGE_START)

    assert await opening() == await opening()


async def test_a_broken_sink_never_costs_the_prompt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    monkeypatch.setattr(prompt_composer, "_llm_compose", _brief)

    def _explode(_notice: prompt_composer.ComposeNotice) -> None:
        raise RuntimeError("the progress view is gone")

    result = await prompt_composer.compose(
        "review the wake word detection",
        session=_session(tmp_path),
        terminal_name="Kate",
        on_progress=_explode,
    )

    assert result.composed_by == "llm"
    assert result.text.startswith("## Task")


async def test_the_typed_prompt_bar_stays_silent(tmp_path) -> None:  # noqa: ANN001
    """Nothing is being written there — a progress line would be noise."""
    seen: list[prompt_composer.ComposeNotice] = []

    result = await prompt_composer.compose(
        "run the tests",
        session=_session(tmp_path),
        terminal_name="Kate",
        use_llm=False,
        on_progress=seen.append,
    )

    assert result.composed_by == "raw"
    assert seen == []


async def test_degrading_is_announced_rather_than_hidden(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """A plain brief is a fine outcome; a plain brief passed off as a written
    one is not — the readback afterwards would claim more than happened."""
    seen: list[prompt_composer.ComposeNotice] = []
    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: (None, ""))

    result = await prompt_composer.compose(
        "review the wake word detection",
        session=_session(tmp_path),
        terminal_name="Kate",
        on_progress=seen.append,
    )

    assert result.composed_by == "fallback"
    closing = seen[-1]
    assert closing.stage == prompt_composer.STAGE_FALLBACK
    assert "Kate" in closing.message
    assert "quality-tier" in closing.message
    assert prompt_composer.STAGE_READY not in [n.stage for n in seen]


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        (True, "working on it"),
        (False, "never started"),
        (None, "could not confirm"),
    ],
)
async def test_the_closing_line_keeps_typed_and_started_apart(
    submitted: bool | None, expected: str
) -> None:
    """A prompt sitting in an input box looks exactly like a running task."""
    seen: list[prompt_composer.ComposeNotice] = []

    prompt_composer.announce_delivery(
        "Kate", delivered=True, submitted=submitted, sink=seen.append
    )

    assert len(seen) == 1
    assert seen[0].stage == prompt_composer.STAGE_SENT
    assert "Kate" in seen[0].message
    assert expected in seen[0].message


async def test_an_undelivered_pane_says_why() -> None:
    seen: list[prompt_composer.ComposeNotice] = []

    prompt_composer.announce_delivery(
        "Kate", delivered=False, reason="its agent is exited, not running",
        sink=seen.append,
    )

    assert "Kate did not get it" in seen[0].message
    assert "exited" in seen[0].message
