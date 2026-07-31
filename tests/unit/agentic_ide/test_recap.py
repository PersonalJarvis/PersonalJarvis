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


def test_the_instruction_beats_the_last_printed_row() -> None:
    """What the pane was ASKED to do leads; the row it printed is a fragment.

    This ordering is the whole lesson of the first recap that shipped: opening
    with the newest readable row put "without interrupting Claude's current
    work" in the header of a pane that had just written a design document.
    """
    term = _pane(status="live", last_prompt="Fix the failing login test")
    term.transcript.feed("Running pytest tests/unit/test_login.py\r\n")

    recap = summarize(term)

    assert "Fix the failing login test" in recap.headline
    # ...and what it printed is still there, in the longer form.
    assert "Running pytest tests/unit/test_login.py" in recap.detail


def test_the_headline_reports_the_last_printed_row_when_nothing_was_asked() -> None:
    """A pane nobody briefed still says something, from what it printed."""
    term = _pane(status="live")
    term.transcript.feed("Running pytest tests/unit/test_login.py\r\n")

    assert summarize(term).headline == "Running pytest tests/unit/test_login.py"


def test_a_bare_path_list_never_becomes_the_headline() -> None:
    """Search output names implementation debris, not the pane's user goal."""
    term = _pane(status="live")
    term.transcript.feed("Mapped the event bus to the speech pipeline\r\n")
    term.transcript.feed('"/core/bus.py", "jarvis/memory"\r\n')

    assert summarize(term).headline == "Mapped the event bus to the speech pipeline"


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


def test_the_claude_code_footer_never_becomes_the_headline() -> None:
    """The folder/branch/model footer is redrawn on every frame — pure chrome.

    Observed live: a busy pane whose newest rows were all footer captioned
    itself "Personal Jarvis 🌿 main Opus 5 (1M context)" — three facts the pane
    header already shows elsewhere, and zero about the work.
    """
    term = _pane(status="live")
    term.transcript.feed("Traced the stale process to yesterday\r\n")
    term.transcript.feed("Tip: Use /btw to ask a quick side question\r\n")
    term.transcript.feed("Personal Jarvis 🌿 main Opus 5 (1M context)\r\n")
    term.transcript.feed("auto mode on · 1 shell\r\n")

    assert summarize(term).headline == "Traced the stale process to yesterday"


def test_a_hand_driven_busy_pane_does_not_claim_it_was_never_instructed() -> None:
    """"No instruction yet" on a pane that worked for hours reads as broken."""
    term = _pane(status="live", last_output_at=time.time())
    term.transcript.feed("Ran 42 tests, all green\r\n")

    detail = summarize(term).detail

    assert "No instruction has been sent" not in detail
    assert "driven directly in the terminal" in detail


def test_a_live_pane_with_no_output_still_says_nothing_was_sent() -> None:
    """The idle wording stays for a pane that truly has nothing going on."""
    term = _pane(status="live")

    assert "No instruction has been sent" in summarize(term).detail


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

    assert len(headline) <= HEADLINE_CHARS
    assert headline.endswith("…")


def test_a_pane_serializes_its_recap_for_the_ui() -> None:
    term = _pane(status="live", last_prompt="Ship the release")
    term.transcript.feed("Tagging v2.4.0\r\n")

    data = term.to_dict()

    assert "Ship the release" in data["recap"]
    assert "Tagging v2.4.0" in data["recap_detail"]


def test_the_headline_quotes_the_job_rather_than_the_briefs_scaffolding() -> None:
    """The reported bug: headers that read "## Task" or "Context:".

    A prompt that arrives from the composer is a structured markdown brief, and
    quoting its FIRST line quoted the heading above the work instead of the
    work. Every pane the composer had written for therefore had a header that
    named the shape of the document and nothing about the job.
    """
    term = _pane(
        status="live",
        last_prompt=(
            "# Task\n"
            "\n"
            "Replace the session lookup in auth/middleware.py so the token is\n"
            "read once per request.\n"
            "\n"
            "## Context\n"
            "The login suite has been red since Tuesday.\n"
        ),
    )

    headline = summarize(term).headline

    assert headline.startswith("Replace the session lookup")
    assert "#" not in headline
    assert "Task" not in headline


def test_a_label_on_the_same_line_as_the_job_keeps_only_the_job() -> None:
    term = _pane(status="live", last_prompt="Task: fix the failing login test")

    assert summarize(term).headline == "fix the failing login test"


def test_a_brief_of_nothing_but_labels_still_says_something() -> None:
    """Better the label than a blank header — it is what the pane was sent."""
    term = _pane(status="live", last_prompt="## Instructions\n\n---\n")

    assert summarize(term).headline == "Instructions"


def test_the_headline_carries_the_job_without_framing_it() -> None:
    """A header is a few centimetres wide; ten characters of "Working on:" is
    ten characters of the answer pushed past the ellipsis."""
    term = _pane(status="live", last_prompt="Fix the failing login test")
    term.transcript.feed("Running pytest\r\n")

    recap = summarize(term)

    assert recap.headline == "Fix the failing login test"
    # The long form still says which of the two the sentence is.
    assert 'Last asked to: "Fix the failing login test"' in recap.detail


def test_the_long_form_keeps_a_long_instruction_readable() -> None:
    """The old 80-character cap cut the instruction in the CARD as well, which
    is the one place there was room to read it."""
    brief = (
        "Work out why the wake word goes deaf after a GPU hot-swap and write "
        "the finding up in docs/local-wakeword/, with the measurements that "
        "support it"
    )
    term = _pane(status="live", last_prompt=brief)

    detail = summarize(term).detail

    assert "docs/local-wakeword/" in detail


def test_summarize_survives_a_pane_without_a_transcript() -> None:
    """A recap is a convenience — it must never be what breaks a state read."""

    class Half:
        status = "live"
        last_prompt = ""
        prompts_sent = 0
        transcript = None
        last_output_at = None

    assert summarize(Half()).headline
