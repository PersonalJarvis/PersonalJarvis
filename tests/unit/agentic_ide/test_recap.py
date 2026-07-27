"""Session recap: the one-line answer a pane header gives.

The recap is derived, never stored, so every test here pins a property of the
derivation: which signal wins when several are available, what a pane says when
it has nothing to report, and which rows of a coding CLI's TUI must never end up
in the header. Without the last one every busy pane in a grid reads "? for
shortcuts", which is the shape of a label that answers nothing.
"""
from __future__ import annotations

import time

from jarvis.agentic_ide.recap import HEADLINE_CHARS, summarize
from jarvis.agentic_ide.session import Terminal


def _pane(**changes: object) -> Terminal:
    term = Terminal(
        key="mika",
        name="Mika",
        agent="claude",
        display_name="Claude Code",
        index=0,
    )
    for field, value in changes.items():
        setattr(term, field, value)
    return term


def test_a_fresh_pane_says_it_has_not_started() -> None:
    recap = summarize(_pane())
    assert "Not started" in recap.headline
    assert recap.detail


def test_the_headline_reports_what_the_pane_last_printed() -> None:
    """The freshest signal wins: output beats the instruction that caused it."""
    term = _pane(status="live", last_prompt="Fix the failing login test")
    term.transcript.feed("Running pytest tests/unit/test_login.py\r\n")

    recap = summarize(term)

    assert recap.headline == "Running pytest tests/unit/test_login.py"
    # ...and the instruction is still there, in the longer form.
    assert "Fix the failing login test" in recap.detail


def test_a_live_pane_that_printed_nothing_falls_back_to_its_instruction() -> None:
    term = _pane(status="live", last_prompt="Refactor the config writer", prompts_sent=1)

    recap = summarize(term)

    assert "Refactor the config writer" in recap.headline


def test_tui_chrome_never_becomes_the_headline() -> None:
    """A status bar is drawn on every frame — it is not what the agent did."""
    term = _pane(status="live")
    term.transcript.feed("Writing jarvis/core/config_writer.py\r\n")
    term.transcript.feed("? for shortcuts\r\n")
    term.transcript.feed("Context left until auto-compact: 34%\r\n")

    assert summarize(term).headline == "Writing jarvis/core/config_writer.py"


def test_the_input_line_never_becomes_the_headline() -> None:
    """The prompt box is what the USER is typing, not what the agent is doing."""
    term = _pane(status="live")
    term.transcript.feed("Ran 42 tests, all green\r\n")
    term.transcript.feed("> and now update the docs\r\n")

    assert summarize(term).headline == "Ran 42 tests, all green"


def test_bullet_glyphs_are_stripped_from_the_headline() -> None:
    term = _pane(status="live")
    term.transcript.feed("⏺ Read jarvis/agentic_ide/session.py\r\n")

    assert summarize(term).headline == "Read jarvis/agentic_ide/session.py"


def test_a_failed_pane_leads_with_the_reason() -> None:
    term = _pane(status="error", error="Claude Code is not on PATH.")

    recap = summarize(term)

    assert "not on PATH" in recap.headline
    assert "not running" in recap.detail


def test_an_exited_pane_reports_its_exit_code() -> None:
    term = _pane(status="exited", exit_code=1)
    term.transcript.feed("Build failed: 3 type errors\r\n")

    recap = summarize(term)

    assert "code 1" in recap.headline
    assert "Build failed: 3 type errors" in recap.detail


def test_a_clean_exit_is_not_reported_as_a_failure() -> None:
    recap = summarize(_pane(status="exited", exit_code=0))
    assert "code" not in recap.headline.lower()


def test_the_detail_says_when_the_pane_last_spoke() -> None:
    term = _pane(status="live", last_output_at=time.time() - 90)
    term.transcript.feed("Compiling the frontend\r\n")

    assert "1 min ago" in summarize(term).detail


def test_the_headline_is_capped_for_transport() -> None:
    """A pane that prints a wall of JSON must not push it through every poll."""
    term = _pane(status="live")
    # A wide pane, so the row is not already clipped by the terminal's width.
    term.transcript.resize(400, 30)
    term.transcript.feed("word " * 200 + "\r\n")

    headline = summarize(term).headline

    assert len(headline) <= HEADLINE_CHARS + 1  # the ellipsis
    assert headline.endswith("…")


def test_a_pane_serializes_its_recap_for_the_ui() -> None:
    term = _pane(status="live", last_prompt="Ship the release")
    term.transcript.feed("Tagging v2.4.0\r\n")

    data = term.to_dict()

    assert data["recap"] == "Tagging v2.4.0"
    assert "Ship the release" in data["recap_detail"]


def test_summarize_survives_a_pane_without_a_transcript() -> None:
    """A recap is a convenience — it must never be what breaks a state read."""

    class Half:
        status = "live"
        last_prompt = ""
        prompts_sent = 0
        transcript = None
        last_output_at = None

    assert summarize(Half()).headline
