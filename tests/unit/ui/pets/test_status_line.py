"""Condensing Jarvis's output into one bubble line, and the feed's dedupe/rate limit."""

from __future__ import annotations

import pytest

from jarvis.ui.pets.status_line import ELLIPSIS, MAX_INPUT_CHARS, StatusFeed, condense


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello there. This is the second sentence.", "Hello there. This is the second sentence."),
        (
            "I checked the calendar for today. Nothing else is planned.",
            "I checked the calendar for today.",
        ),
        ("**Done** — the *file* is __saved__.", "Done — the file is saved."),
        ("# Summary\nThe build passed.", "The build passed."),
        ("# Only a heading", "Only a heading"),
        # Each bullet is a sentence; a short first one borrows the next.
        ("- first item\n- second item", "first item. second item"),
        ("- the first bullet is long enough\n- second", "the first bullet is long enough"),
        ("Run `npm test` now.", "Run npm test now."),
        ("See https://example.com/docs for details.", "See for details."),
        ("[the docs](https://example.com) are updated.", "the docs are updated."),
        ("Saved to C:\\Users\\someone\\notes\\todo.md for you.", "Saved to todo.md for you."),
        ("Edited /home/someone/project/src/main.py just now.", "Edited main.py just now."),
        ('Result: {"ok": true, "count": 3} came back.', "Result: came back."),
    ],
)
def test_condense_cleans_markdown_and_noise(text: str, expected: str) -> None:
    assert condense(text) == expected


def test_condense_drops_fenced_code() -> None:
    text = "Here is the fix:\n```python\nprint('x')\n```\nIt works now."
    assert condense(text) == "Here is the fix. It works now."
    assert "print" not in condense(text)


def test_condense_drops_long_inline_code() -> None:
    assert condense("Try `" + "x" * 40 + "` here.") == "Try here."


def test_condense_joins_a_very_short_first_sentence() -> None:
    text = "Okay. I will open the settings page."
    assert condense(text) == text


def test_condense_clips_at_a_word_boundary() -> None:
    text = "This sentence is deliberately rather long so that it must be clipped somewhere sensible"
    out = condense(text, max_chars=40)
    assert len(out) <= 40
    assert out.endswith(ELLIPSIS)
    assert text.startswith(out[:-1])
    assert not out[:-1].endswith(" ")


@pytest.mark.parametrize("text", ["", "   ", "```\ncode only\n```", '{"a": 1}', None, 5])
def test_condense_returns_empty_for_nothing_readable(text: object) -> None:
    assert condense(text) == ""  # type: ignore[arg-type]


def test_feed_dedupes_and_rate_limits() -> None:
    clock = FakeClock()
    feed = StatusFeed(clock=clock, min_interval_s=0.3)
    first = ("Thinking …", "Opening the calendar.")
    assert feed.offer(*first) == first
    clock.now = 1.0
    assert feed.offer("Thinking …", "Opening the calendar.") is None  # duplicate
    clock.now = 1.1
    assert feed.offer("Thinking …", "Reading events.") == ("Thinking …", "Reading events.")
    clock.now = 1.2
    assert feed.offer("Thinking …", "Counting events.") is None  # too soon
    assert feed.flush() is None  # still too soon
    clock.now = 1.5
    assert feed.flush() == ("Thinking …", "Counting events.")
    assert feed.flush() is None


def test_feed_force_bypasses_interval_not_dedupe() -> None:
    clock = FakeClock()
    feed = StatusFeed(clock=clock)
    feed.offer("", "First line.")
    assert feed.offer("", "Second line.", force=True) == ("", "Second line.")
    assert feed.offer("", "Second line.", force=True) is None


def test_feed_ignores_empty_pairs_and_resets() -> None:
    clock = FakeClock()
    feed = StatusFeed(clock=clock)
    assert feed.offer("", "") is None
    assert feed.offer("", "```\nx\n```") is None
    feed.offer("", "Same line.")
    feed.reset()
    assert feed.offer("", "Same line.") == ("", "Same line.")


def test_only_the_head_of_a_long_text_is_condensed() -> None:
    # Pages of unpunctuated text (a table, a log) must not be worked through
    # on a bus handler; the gist comes from the head alone.
    text = "Here is the summary of the run " + "| cell " * 50_000
    assert len(text) > 10 * MAX_INPUT_CHARS
    line = condense(text)
    assert line.startswith("Here is the summary")
    assert len(line) <= 90
    # A sentence that starts after the cap is not part of the gist.
    assert "Tail" not in condense("x" * (MAX_INPUT_CHARS + 10) + " Tail sentence.")
