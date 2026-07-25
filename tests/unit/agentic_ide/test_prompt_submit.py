"""Making sure a prompt is actually SUBMITTED, not just typed.

Live failure, 2026-07-25: Jarvis typed three review prompts into three agents and
only one ran. The two that stalled both ended with an ``@file`` reference — an
``@path`` at the end of the line leaves the agent's file-completion popup open, so
the Enter that follows picks a suggestion instead of submitting. Measured against a
real Claude Code: ending in ``@README.md`` never submits, the same prompt with one
trailing space always does.

Two defences are tested here: closing the completion before Enter, and verifying
afterwards that the text left the input line (retrying Enter while it has not).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import (
    PASTE_END,
    PASTE_START,
    Registry,
    SessionError,
    _input_line_holds,
    _opens_completion,
    _submit_needle,
    sanitize_prompt,
)
from tests.fakes.fake_pty_manager import FakePtyManager


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize(
    "payload",
    [
        "review this. @jarvis/ultrawiki/pipeline.py",
        "check @tests/unit/test_x.py",
        "run /effort",
    ],
)
def test_a_trailing_reference_is_recognised(payload: str) -> None:
    assert _opens_completion(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        "review the pipeline please",
        "look at @jarvis/x.py and report back",  # reference is not last
        "email me @",  # a bare @ opens nothing
        "10 / 2 is five",
    ],
)
def test_plain_endings_need_no_space(payload: str) -> None:
    assert _opens_completion(payload) is False


def test_the_needle_comes_from_the_start_of_the_prompt() -> None:
    """The input box wraps, so only the first line is reliably intact."""
    needle = _submit_needle("Führe ein gründliches Code-Review durch. @a/b.py")
    assert needle.startswith("führe ein")
    assert "@a/b.py" not in needle


def test_an_input_line_still_holding_the_prompt_is_detected() -> None:
    needle = _submit_needle("Review the pipeline")
    assert _input_line_holds(["❯ Review the pipeline", "  📁 project  🌿 main"], needle)
    assert _input_line_holds(["> Review the pipeline and tests"], needle)


def test_an_empty_input_line_means_submitted() -> None:
    needle = _submit_needle("Review the pipeline")
    assert not _input_line_holds(["❯", "  📁 project", "✽ Mulling…"], needle)


def test_an_echo_above_the_input_line_does_not_count_as_pending() -> None:
    """A submitted prompt is echoed into the history — that is success, not a
    prompt still waiting to be sent."""
    needle = _submit_needle("Review the pipeline")
    assert not _input_line_holds(["Review the pipeline", "✻ Cooked for 2s", "❯"], needle)


# ------------------------------------------------------------------ the path
@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    # Keep the verification loop fast in tests.
    monkeypatch.setattr(session_mod, "_SUBMIT_POLL_S", 0.0)
    monkeypatch.setattr(session_mod, "_SUBMIT_WINDOW_S", 0.0)
    monkeypatch.setattr(session_mod, "_SUBMIT_RETRY_AFTER_S", 0.0)
    return Registry(pty_manager=fake_pty)


async def _open(registry: Registry, folder: Path):
    return await registry.start(str(folder), [{"agent": "claude", "name": "Mika"}])


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _live(registry: Registry, tmp_path: Path):
    await _open(registry, tmp_path)
    return await registry.attach("Mika", 100, 30, _noop, _noop_exit)


async def test_a_trailing_reference_gets_a_closing_space(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    # Empty screen: the verification sees no pending input line -> submitted.
    await registry.send_prompt("Mika", "review this. @jarvis/x.py")
    assert fake_pty.typed[0] == "review this. @jarvis/x.py ", fake_pty.typed
    assert fake_pty.typed[1] == "\r"
    assert term.submitted is True


async def test_a_plain_prompt_is_typed_verbatim(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _live(registry, tmp_path)
    await registry.send_prompt("Mika", "review the pipeline")
    assert fake_pty.typed[0] == "review the pipeline", fake_pty.typed


async def test_enter_is_pressed_again_while_the_prompt_sits_in_the_box(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The safety net for cases the space alone does not fix."""
    term = await _live(registry, tmp_path)
    # Make the screen look like the prompt never left the input line.
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ review the pipeline\r\n")

    await registry.send_prompt("Mika", "review the pipeline")

    enters = [d for d in fake_pty.typed if d == "\r"]
    assert len(enters) == 2, f"one Enter plus a single retry, got {fake_pty.typed}"
    assert term.submitted is False


async def test_a_prompt_that_lands_reports_submitted(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    on_output = fake_pty.spawns[-1]["on_output"]
    # Screen after a successful submit: the prompt is echoed in the history and
    # the input line is empty again.
    await on_output("pty", "\x1b[2J\x1b[Hreview the pipeline\r\n✽ Mulling…\r\n❯\r\n")

    await registry.send_prompt("Mika", "review the pipeline")
    assert term.submitted is True
    assert [d for d in fake_pty.typed if d == "\r"] == ["\r"], "no needless retries"


async def test_the_submitted_flag_is_reported(
    registry: Registry, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    await registry.send_prompt("Mika", "review the pipeline")
    assert term.to_dict()["submitted"] is True


async def test_a_fresh_terminal_has_no_submitted_verdict(
    registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path)
    assert registry.session.terminals[0].to_dict()["submitted"] is None


async def test_a_dead_terminal_still_refuses_outright(
    registry: Registry, tmp_path: Path
) -> None:
    """Verification never softens the hard refusal: no live agent, nothing typed."""
    await _open(registry, tmp_path)
    with pytest.raises(SessionError, match="not running"):
        await registry.send_prompt("Mika", "review the pipeline")


# --------------------------------------------------- multi-line transport
# A structured prompt only reaches the pane if its line breaks survive, and a
# bare "\n" written to a PTY IS the Enter key — an unwrapped markdown prompt
# would submit after its first line. Bracketed paste is the terminal-level
# convention that delivers the whole block as one paste instead.
_MARKDOWN = "## Task\nReview the ranking.\n\n## Scope\nRanking only."
_ONE_LINE = "## Task Review the ranking. ## Scope Ranking only."


def test_sanitize_keeps_newlines_when_asked() -> None:
    out = sanitize_prompt(_MARKDOWN, keep_newlines=True)
    assert out == _MARKDOWN


def test_sanitize_default_still_collapses_newlines() -> None:
    assert "\n" not in sanitize_prompt("a\nb\nc")
    assert sanitize_prompt(_MARKDOWN) == _ONE_LINE


def test_sanitize_strips_control_characters_even_in_multiline_mode() -> None:
    out = sanitize_prompt("## Task\n\x1b[31mred\x1b[0m\rsubmit\x03now", keep_newlines=True)
    assert "\x1b" not in out
    assert "\r" not in out
    assert "\x03" not in out
    assert "red" in out and "submit" in out and "now" in out


def test_sanitize_collapses_runs_of_blank_lines() -> None:
    assert sanitize_prompt("a\n\n\n\n\nb", keep_newlines=True) == "a\n\nb"


def test_the_needle_uses_only_the_first_line() -> None:
    """The needle must not span the line break, or it can never be found."""
    needle = _submit_needle(_MARKDOWN)
    assert needle == "## task"


async def test_a_multiline_prompt_is_sent_as_one_bracketed_paste(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)

    await registry.send_prompt("Mika", _MARKDOWN)

    body = fake_pty.typed[0]
    assert body.startswith(PASTE_START)
    assert body.endswith(PASTE_END)
    assert "## Task\nReview the ranking." in body
    assert fake_pty.typed[1] == "\r"
    assert term.submitted is True
    assert term.sent_multiline is True


async def test_a_single_line_prompt_is_not_wrapped(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)

    await registry.send_prompt("Mika", "review the pipeline")

    assert PASTE_START not in fake_pty.typed[0]
    assert term.sent_multiline is False


def test_a_collapsed_paste_on_the_input_line_counts_as_pending() -> None:
    """A TUI may render a paste as a placeholder. Reading that as 'submitted'
    would hide a genuine failure behind an optimistic check."""
    needle = _submit_needle(_MARKDOWN)
    assert _input_line_holds(["❯ [Pasted text #1 +12 lines]"], needle)
    assert _input_line_holds(["> [Pasted text +4 lines]", ""], needle)


async def test_a_rejected_multiline_prompt_falls_back_to_one_line(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Worst case is the old behaviour, never a lost instruction."""
    term = await _live(registry, tmp_path)
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ [Pasted text #1 +4 lines]\r\n")

    await registry.send_prompt("Mika", _MARKDOWN)

    assert any(d == _ONE_LINE for d in fake_pty.typed), fake_pty.typed
    assert term.sent_multiline is False
    assert term.last_prompt == _ONE_LINE
