"""Transcript: turning a PTY stream into something a model can be asked about.

The transcript replays the stream onto a terminal screen and reads the rows off
it. Each test pins one property the Agentic IDE depends on — without them, "what
is Alex doing?" gets answered from control codes and repainted frames.
"""
from __future__ import annotations

from jarvis.agentic_ide.screen import ScreenBuffer
from jarvis.agentic_ide.transcript import ReplayBuffer, Transcript, is_noise, strip_ansi


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


def test_screen_tracks_the_visible_cursor_used_by_tui_input_gates() -> None:
    screen = ScreenBuffer(cols=40, rows=10)
    screen.feed("\x1b[3;1H› Ask Codex anything\x1b[3;3H\x1b[?25l")

    assert screen.visible_cursor is None

    screen.feed("\x1b[?25h")

    assert screen.visible_cursor == (2, 2)
    assert screen.row_text(2).startswith("› ")

    screen.reset()
    assert screen.visible_cursor == (0, 0)


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


# --------------------------------------------------------------- replay buffer
#
# What a pane that comes back is handed. The property under test is not "the
# bytes are kept" but "the pane can say whether they are ENOUGH" — a tail that
# lost its front cannot rebuild a TUI drawn with relative cursor moves, and the
# viewer has to know that to ask the agent for a fresh paint (2026-07-27).


def test_replay_hands_back_the_stream_verbatim() -> None:
    buffer = ReplayBuffer()
    buffer.feed("\x1b[32mbuilding…\x1b[0m")
    buffer.feed(" done\r\n")

    assert buffer.text() == "\x1b[32mbuilding…\x1b[0m done\r\n"
    assert buffer.truncated is False


def test_replay_admits_when_it_dropped_the_start() -> None:
    buffer = ReplayBuffer(limit=32)
    buffer.feed("\x1b[Hthe frame that drew the prompt box")
    assert buffer.truncated is False

    buffer.feed("\x1b[Kspinner")
    assert buffer.truncated is True, "a viewer must be able to tell it lost the start"


def test_a_truncated_replay_starts_on_an_escape_boundary() -> None:
    """PTY reads split anywhere — including mid-sequence.

    Dropping whole chunks can still leave the CONTINUATION of a sequence at the
    front, and a terminal handed ``[38;5;214m`` without its ``ESC`` prints it as
    visible text in the middle of the agent's UI.
    """
    buffer = ReplayBuffer(limit=24)
    buffer.feed("first chunk ending in \x1b")
    buffer.feed("[38;5;214mstill orange, and then some more output")

    replayed = buffer.text()
    assert "[38;5;214m" not in strip_ansi(replayed), "a half sequence would be printed"
    assert replayed.startswith("\x1b[0m"), "the starting attributes must be known"


def test_clearing_a_replay_forgets_that_it_was_truncated() -> None:
    # A fresh process draws a fresh screen: the new stream starts at its start.
    buffer = ReplayBuffer(limit=8)
    buffer.feed("aaaaaaaaaa")
    buffer.feed("bbbbbbbbbb")
    assert buffer.truncated is True

    buffer.clear()
    assert buffer.truncated is False
    assert buffer.text() == ""


def test_a_truncated_replay_alone_cannot_rebuild_the_screen() -> None:
    """The reason the flag exists, spelled out as the failure it prevents.

    An Ink-based TUI paints its interface once and afterwards rewrites only the
    row that changed, addressed by relative cursor moves. Replaying a tail that
    lost its front therefore reproduces the spinner row and NOTHING it was
    drawn on top of — which is what put empty rectangles in two live panes.
    """
    opening = (
        "Running 2 shell commands\r\n"
        "\r\n"
        "- Scurrying... (0m 01s)\r\n"
        "\r\n"
        "------------------------\r\n"
        "> \r\n"
        "------------------------\r\n"
        "  auto mode on\r\n"
    )
    # Every later frame: up six rows, erase that row, rewrite it, come back.
    frames = [f"\x1b[6A\r\x1b[K- Scurrying... (0m {n:02d}s)\x1b[6B\r" for n in range(2, 60)]

    buffer = ReplayBuffer(limit=len(opening) // 2)
    buffer.feed(opening)
    for frame in frames:
        buffer.feed(frame)

    screen = ScreenBuffer(40, 12)
    screen.feed(buffer.text())
    rows = [row.rstrip() for row in screen.display() if row.strip()]

    assert any("Scurrying" in row for row in rows), "the last frame does arrive"
    assert not any(">" in row for row in rows), (
        "the prompt box is gone — which is why a truncated replay must be "
        "followed by a repaint rather than trusted on its own"
    )
    assert buffer.truncated is True


def test_a_truncated_replay_still_says_who_owns_the_screen() -> None:
    """The bug behind four rounds of "scrolling is broken in Claude Code panes".

    A CLI negotiates what kind of terminal it is talking to exactly once, in its
    first few hundred bytes. Measured against Claude Code 2.1.220: take the
    whole screen (``?1049h``) and send me the mouse (``?1000/1002/1003/1006h``).
    Those bytes leave a 128 KB tail within minutes, and every viewer that
    re-joined afterwards got a terminal that had never heard them — so it kept
    the wheel to itself and scrolled its own stale buffer, which the agent
    overwrote on its next repaint. Nothing moved, ever, in the app.
    """
    start = "\x1b[?1049h\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h\x1b[?25lhello"
    buffer = ReplayBuffer(limit=64)
    buffer.feed(start)
    buffer.feed("x" * 200)

    replay = buffer.text()

    assert buffer.truncated is True
    for mode in ("?1049h", "?1000h", "?1002h", "?1003h", "?1006h", "?25l"):
        assert f"\x1b[{mode}" in replay, f"{mode} must survive the truncation"
    # And before the content, or the screen switch would wipe what it restored.
    assert replay.index("\x1b[?1049h") < replay.index("x" * 20)


def test_private_modes_survive_every_pty_chunk_boundary() -> None:
    """The terminal negotiation may be split after any byte by a PTY read."""
    mode = "\x1b[?1000;1006h"
    for cut in range(1, len(mode)):
        buffer = ReplayBuffer(limit=16)
        buffer.feed(mode[:cut])
        buffer.feed(mode[cut:] + "screen")
        buffer.feed("x" * 64)

        replay = buffer.text()
        assert "\x1b[?1000h" in replay, f"mode 1000 lost at byte {cut}"
        assert "\x1b[?1006h" in replay, f"mode 1006 lost at byte {cut}"


def test_clearing_a_replay_forgets_an_incomplete_mode_prefix() -> None:
    buffer = ReplayBuffer(limit=16)
    buffer.feed("\x1b[?10")
    buffer.clear()
    buffer.feed("00hplain output")
    buffer.feed("x" * 64)

    assert "\x1b[?1000h" not in buffer.text()


def test_a_mode_the_agent_turned_off_comes_back_off() -> None:
    """Several private modes are ON in a terminal that was just built.

    Wraparound and the cursor among them — restoring only what was switched on
    would hand the viewer a cursor the agent had deliberately hidden.
    """
    buffer = ReplayBuffer(limit=32)
    buffer.feed("\x1b[?7h\x1b[?25h\x1b[?7l\x1b[?25l")
    buffer.feed("y" * 100)

    replay = buffer.text()

    assert "\x1b[?7l" in replay and "\x1b[?25l" in replay
    assert "\x1b[?7h" not in replay and "\x1b[?25h" not in replay


def test_an_untruncated_replay_is_still_verbatim() -> None:
    """Nothing is prepended while the original negotiation is still in there."""
    stream = "\x1b[?1049h\x1b[?1006hhello"
    buffer = ReplayBuffer(limit=1024)
    buffer.feed(stream)

    assert buffer.text() == stream


def test_rebasing_for_a_resize_drops_old_drawing_but_preserves_modes() -> None:
    """A TUI stream is geometry-bound; its negotiated terminal state is not."""
    buffer = ReplayBuffer()
    buffer.feed("\x1b[?1049h\x1b[?1006hOLD STATUS ROW")

    prologue = buffer.rebase_for_resize()

    assert "OLD STATUS ROW" not in prologue
    assert "OLD STATUS ROW" not in buffer.text()
    assert "\x1b[?1049h" in prologue
    assert "\x1b[?1006h" in prologue
    assert prologue.endswith("\x1b[0m")
    assert buffer.truncated is False

    buffer.feed("NEW SCREEN")
    assert buffer.text() == prologue + "NEW SCREEN"


def test_a_replay_forgets_the_modes_when_it_is_cleared() -> None:
    buffer = ReplayBuffer(limit=32)
    buffer.feed("\x1b[?1049h" + "z" * 100)
    buffer.clear()
    buffer.feed("fresh")

    assert buffer.text() == "fresh"
