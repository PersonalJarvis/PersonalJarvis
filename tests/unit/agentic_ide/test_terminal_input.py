from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.agentic_ide.terminal_input import (
    DEVICE_ATTRIBUTES,
    THEME_COLOURS,
    TerminalQueryResponder,
    classify_terminal_input,
    is_newline_chord_only,
    is_terminal_report_only,
)

#: What a CLI asks its terminal while starting: device attributes, then the
#: foreground and background of the screen it is about to draw on.
DA_QUERY = "\x1b[c"
FG_QUERY = "\x1b]10;?\x1b\\"
BG_QUERY = "\x1b]11;?\x07"

#: The replies, as a browser's xterm would produce them a round trip later.
REPORTS = "\x1b]10;rgb:e8e8/e8e8/ecec\x1b\\\x1b]11;rgb:1212/1414/1a1a\x1b\\\x1b[?1;2c"


def test_a_startup_query_is_answered_from_the_pty_side() -> None:
    responder = TerminalQueryResponder(appearance="dark")

    replies = responder.feed(f"{DA_QUERY}{FG_QUERY}{BG_QUERY}")

    assert replies == (
        f"{DEVICE_ATTRIBUTES}\x1b]10;rgb:e8e8/e8e8/ecec\x1b\\\x1b]11;rgb:1212/1414/1a1a\x07"
    )


def test_the_answer_describes_the_ground_the_pane_actually_draws_on() -> None:
    """A pane on paper must not report slate, or the CLI picks the wrong palette."""
    light = TerminalQueryResponder(appearance="light").feed(BG_QUERY)

    assert light == "\x1b]11;rgb:fcfc/fbfb/f8f8\x07"


def test_an_unknown_appearance_falls_back_rather_than_failing() -> None:
    responder = TerminalQueryResponder(appearance="solarized")

    assert responder.feed(BG_QUERY) == "\x1b]11;rgb:1212/1414/1a1a\x07"


def test_the_terminator_the_query_used_is_the_one_answered_with() -> None:
    responder = TerminalQueryResponder()

    assert responder.feed("\x1b]11;?\x1b\\").endswith("\x1b\\")
    assert responder.feed("\x1b]11;?\x07").endswith("\x07")


def test_a_query_split_across_two_pty_reads_is_still_answered() -> None:
    responder = TerminalQueryResponder()

    assert responder.feed("\x1b]11;") == ""
    assert responder.feed("?\x07") == "\x1b]11;rgb:1212/1414/1a1a\x07"


def test_an_answered_query_is_never_answered_twice() -> None:
    """The retained tail must not re-match what it already replied to."""
    responder = TerminalQueryResponder()
    responder.feed(BG_QUERY)

    assert responder.feed("Welcome to Codex\r\n") == ""


def test_ordinary_output_is_not_mistaken_for_a_query() -> None:
    responder = TerminalQueryResponder()

    assert responder.feed("\x1b[32mdone\x1b[0m — 12 files changed\r\n") == ""


def test_setting_a_colour_is_not_a_question() -> None:
    """``ESC ] 11 ; <colour>`` recolours the terminal; nothing is being asked."""
    responder = TerminalQueryResponder()

    assert responder.feed("\x1b]11;#ff0000\x07") == ""


def test_replies_echoed_by_a_browser_are_recognised_as_such() -> None:
    assert is_terminal_report_only(REPORTS) is True


def test_typing_is_never_mistaken_for_a_reply() -> None:
    assert is_terminal_report_only("review this") is False
    assert is_terminal_report_only(REPORTS + "review this") is False
    assert is_terminal_report_only("") is False


def test_only_the_explicit_newline_chord_is_not_a_submit() -> None:
    assert is_newline_chord_only("\x1b\r") is True
    assert is_newline_chord_only("\r") is False
    assert is_newline_chord_only("text\r") is False


def test_chunked_bracketed_paste_newlines_are_edits_not_submissions() -> None:
    submits, edits, active = classify_terminal_input("\x1b[200~first\n", False)
    assert (submits, edits, active) == (False, True, True)

    submits, edits, active = classify_terminal_input("second\r\n\x1b[201~", active)
    assert (submits, edits, active) == (False, True, False)


def test_text_followed_by_enter_is_an_edit_and_a_submission() -> None:
    assert classify_terminal_input("new task\r", False) == (True, True, False)


def test_a_cursor_position_report_still_reaches_the_agent() -> None:
    """Only the two replies the backend produces itself are dropped."""
    assert is_terminal_report_only("\x1b[12;40R") is False


# --------------------------------------------------------------------------- #
# Drift guard                                                                  #
# --------------------------------------------------------------------------- #
_THEMES_TS = (
    Path(__file__).resolve().parents[3]
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "components"
    / "agentic"
    / "terminalThemes.ts"
)


@pytest.mark.parametrize("appearance", sorted(THEME_COLOURS))
def test_the_answered_colours_match_the_theme_the_pane_is_drawn_with(
    appearance: str,
) -> None:
    """Telling the CLI a colour the pane does not use is the same bug, quieter.

    The palette lives in TypeScript because that is what paints the pane; the
    backend answers the CLI's colour query from a copy. This pins the copy.
    """
    if not _THEMES_TS.exists():  # a source checkout without the frontend
        pytest.skip("frontend sources are not present")
    source = _THEMES_TS.read_text(encoding="utf-8")
    block = re.search(
        rf"{appearance.upper()}_TERMINAL_THEME: ITheme = \{{(.*?)\}};",
        source,
        re.DOTALL,
    )
    assert block is not None, f"no {appearance} theme found in terminalThemes.ts"
    declared = dict(re.findall(r"(\w+): \"(#[0-9a-fA-F]{6})\"", block.group(1)))
    # The theme's background stays transparent on screen but carries the pane
    # shell's RGB at alpha 0, so xterm's minimum-contrast floor measures
    # against the real ground. That RGB is the one the CLI must be told.
    ground = re.search(
        r"background: \"rgba\((\d+), (\d+), (\d+), 0\)\"", block.group(1)
    )
    assert ground is not None, f"no ground RGB in the {appearance} theme"
    background = "#" + "".join(f"{int(c):02x}" for c in ground.groups())

    assert THEME_COLOURS[appearance] == (
        declared["foreground"].lower(),
        background,
    )
