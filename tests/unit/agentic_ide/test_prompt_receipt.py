"""The delivery receipt: a pane records, and announces, that it was given a prompt.

Guards the one guarantee that makes the receipt worth having — that it does NOT
depend on the agent's screen. A prompt reaches a pane and the pane may show
nothing at all: its output parked while it was off screen, its emulator never
painted, its socket reconnecting, or the CLI redrawing its input box out of
view. Every one of those has happened live, and each time the user was told the
brief had been sent and had no way to check.

So delivery is recorded in the pane's own state (durable, survives a viewer
that was not there) and announced on a channel of its own (immediate, lossy).
The tests below pin both halves, including the case the readback most needs to
keep honest: a prompt that was typed but never submitted still produces a
receipt, because that pane looks exactly like a working one.
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide.session import Terminal, announce_prompt


def pane(**kwargs: object) -> Terminal:
    """A pane with the identity fields filled in and nothing else assumed."""
    defaults: dict = {
        "key": "t1",
        "name": "T1",
        "agent": "claude",
        "display_name": "Claude Code",
        "index": 0,
    }
    defaults.update(kwargs)
    return Terminal(**defaults)  # type: ignore[arg-type]


def test_a_pane_that_was_never_prompted_says_so() -> None:
    """No timestamp, rather than a zero that would render as 1 January 1970."""
    term = pane()

    assert term.last_prompt_at is None
    state = term.to_dict()
    assert state["last_prompt_at"] is None
    assert state["last_prompt_chars"] == 0
    assert state["history_id"] == term.history_id


def test_the_state_carries_when_a_prompt_arrived_and_how_long_it_was() -> None:
    """The durable half of the receipt: readable at any later mount or poll."""
    term = pane(last_prompt="Refactor the parser" * 40, last_prompt_at=1_700_000_000.0)

    state = term.to_dict()

    assert state["last_prompt_at"] == 1_700_000_000.0
    assert state["last_prompt_chars"] == len(term.last_prompt)
    # The excerpt stays short on purpose — a composed brief runs to thousands of
    # characters and this rides along in every state read.
    assert len(state["last_prompt"]) == 200
    assert state["last_prompt_chars"] > len(state["last_prompt"])


async def test_every_attached_viewer_is_told_about_a_delivery() -> None:
    """A pane open in two windows must not have one of them left in the dark."""
    seen: list[dict] = []
    other: list[dict] = []
    term = pane(last_prompt="Write the tests first", last_prompt_at=12.0, prompts_sent=3)
    term.prompt_viewers.extend(
        [lambda notice: _record(seen, notice), lambda notice: _record(other, notice)]
    )

    await announce_prompt(term)

    assert len(seen) == 1
    assert len(other) == 1
    assert seen[0]["name"] == "T1"
    assert seen[0]["at"] == 12.0
    assert seen[0]["chars"] == len("Write the tests first")
    assert seen[0]["prompts_sent"] == 3


async def test_the_notice_carries_an_excerpt_not_the_whole_brief() -> None:
    """The socket is otherwise keystrokes; a 6 000-character brief has a URL."""
    term = pane(last_prompt="x" * 4_000, last_prompt_at=1.0)
    seen: list[dict] = []
    term.prompt_viewers.append(lambda notice: _record(seen, notice))

    await announce_prompt(term)

    assert len(seen[0]["preview"]) == 200
    assert seen[0]["chars"] == 4_000


@pytest.mark.parametrize("submitted", [True, False, None])
async def test_a_receipt_goes_out_for_every_outcome(submitted: bool | None) -> None:
    """Including — above all — the prompt that never started.

    A brief sitting unsent in an input box is indistinguishable on screen from
    an agent hard at work, and it is the only case where the user has to do
    something about it. Suppressing the receipt there would hide the one state
    that cannot fix itself.
    """
    term = pane(last_prompt="Ship it", last_prompt_at=5.0, submitted=submitted)
    seen: list[dict] = []
    term.prompt_viewers.append(lambda notice: _record(seen, notice))

    await announce_prompt(term)

    assert seen[0]["submitted"] is submitted


async def test_a_viewer_that_raises_does_not_cost_the_other_viewers() -> None:
    """The delivery already happened; a broken channel may not unreport it."""
    seen: list[dict] = []
    term = pane(last_prompt="Carry on", last_prompt_at=7.0)

    async def explode(_notice: dict) -> None:
        raise RuntimeError("this viewer's socket died mid-send")

    term.prompt_viewers.extend([explode, lambda notice: _record(seen, notice)])

    await announce_prompt(term)  # must not raise

    assert len(seen) == 1


async def test_announcing_with_nobody_attached_is_a_no_op() -> None:
    """The state still carries it — the next viewer picks the receipt up there."""
    term = pane(last_prompt="Nobody is watching", last_prompt_at=9.0)

    await announce_prompt(term)  # must not raise

    assert term.last_prompt_at == 9.0


async def _record(sink: list[dict], notice: dict) -> None:
    sink.append(notice)
