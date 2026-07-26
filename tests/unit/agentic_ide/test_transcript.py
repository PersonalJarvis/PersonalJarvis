"""Transcript: turning a PTY stream into something a model can be asked about.

The transcript replays the stream onto a terminal screen and reads the rows off
it. Each test pins one property the Agentic IDE depends on — without them, "what
is Alex doing?" gets answered from control codes and repainted frames.
"""
from __future__ import annotations

from jarvis.agentic_ide.transcript import Transcript, is_noise, strip_ansi


def test_strip_ansi_removes_colour_and_cursor_sequences() -> None:
    coloured = "\x1b[32mgreen\x1b[0m and \x1b[1;31mred\x1b[0m"
    assert strip_ansi(coloured) == "green and red"


def test_strip_ansi_removes_osc_window_title() -> None:
    assert strip_ansi("\x1b]0;a title\x07text") == "text"


def test_colour_codes_do_not_reach_the_transcript() -> None:
    t = Transcript()
    t.feed("\x1b[32mtests passed\x1b[0m\r\n")
    assert t.tail() == ["tests passed"]


def test_carriage_return_overwrites_in_place() -> None:
    """A progress bar leaves one row behind, not one per repaint."""
    t = Transcript()
    t.feed("build 10%\rbuild 50%\rbuild 100%\r\n")
    assert t.tail() == ["build 100%"]


def test_absolute_cursor_positioning_is_honoured() -> None:
    """The core reason a plain filter fails: a TUI writes rows out of order."""
    t = Transcript()
    # Paint row 3 first, then jump back up to row 1.
    t.feed("\x1b[3;1Hthird line\x1b[1;1Hfirst line")
    assert t.tail() == ["first line", "third line"]


def test_erase_line_removes_stale_content() -> None:
    """A repainted row must not leave the longer previous text behind."""
    t = Transcript()
    t.feed("Thinking about a very long thing\r\x1b[KDone")
    assert t.tail() == ["Done"]


def test_erase_display_keeps_the_erased_rows_as_history() -> None:
    """A TUI that clears the screen has not undone what already happened."""
    t = Transcript()
    t.feed("step one complete\r\n")
    t.feed("\x1b[2J\x1b[H")
    t.feed("step two starting\r\n")
    assert t.tail() == ["step one complete", "step two starting"]


def test_repeated_identical_rows_are_folded() -> None:
    t = Transcript()
    t.feed("thinking\r\nthinking\r\nthinking\r\ndone\r\n")
    assert t.tail() == ["thinking", "done"]


def test_decoration_only_rows_are_dropped() -> None:
    t = Transcript()
    t.feed("╭──────────╮\r\nreal content\r\n╰──────────╯\r\n")
    assert t.tail() == ["real content"]


def test_spinner_frames_are_dropped_but_activity_is_still_visible() -> None:
    """Byte volume survives the filter, so "is it doing anything?" stays
    answerable for an agent that only animates a spinner."""
    t = Transcript()
    t.feed("\x1b[2m⠋\x1b[0m\r\x1b[2m⠙\x1b[0m\r\x1b[2m⠹\x1b[0m\r")
    assert t.tail() == []
    assert t.raw_chars > 0


def test_the_current_row_is_visible_before_its_newline() -> None:
    """An agent waiting at a prompt has written no newline yet — that row is
    usually the most current signal there is."""
    t = Transcript()
    t.feed("Do you want to proceed? (y/n) ")
    assert t.tail() == ["Do you want to proceed? (y/n)"]


def test_escape_sequence_split_across_two_reads() -> None:
    """A PTY read can end in the middle of an escape sequence."""
    t = Transcript()
    t.feed("first\r\n\x1b[3")
    t.feed(";1Hthird\r\n")
    assert t.tail() == ["first", "third"]


def test_output_beyond_the_screen_scrolls_into_history() -> None:
    t = Transcript(cols=40, rows=6)
    for i in range(20):
        t.feed(f"line {i}\r\n")
    tail = t.tail(5)
    assert tail == ["line 15", "line 16", "line 17", "line 18", "line 19"]


def test_history_is_bounded() -> None:
    t = Transcript(cols=40, rows=6, max_lines=25)
    for i in range(500):
        t.feed(f"line {i}\r\n")
    assert len(t.lines()) <= 31  # scrollback cap + the visible rows


def test_resize_reflows_without_losing_content() -> None:
    t = Transcript(cols=40, rows=10)
    t.feed("hello from the agent\r\n")
    t.resize(100, 30)
    assert "hello from the agent" in t.lines()


def test_is_noise_classifies_blank_and_decoration() -> None:
    assert is_noise("")
    assert is_noise("   ")
    assert is_noise("────────")
    assert not is_noise("Running tests…")
