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

import asyncio
from pathlib import Path

import pytest

from jarvis.agentic_ide import fleet_actions
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


@pytest.mark.parametrize(
    "input_line",
    [
        "› [Pasted Content 2497 chars]",   # Codex
        "❯ [Pasted text #1 +12 lines]",    # Claude Code
        "> [Pasted 40 lines]",
        "› [pasted content 812 chars]",
        "❯ [Image #1 pasted]",
    ],
)
def test_every_paste_placeholder_wording_counts_as_still_pending(
    input_line: str,
) -> None:
    """A collapsed paste is the prompt, still sitting in the box.

    The TUI draws a summary instead of the text, so the text itself cannot be
    compared against — but it has NOT been sent. Measured against a real Codex
    (2026-07-26): a brief pasted into a fully booted pane renders as
    ``[Pasted Content 2497 chars]`` and stays there, because Codex ignores an
    Enter that arrives too soon after a paste. Recognising only Claude Code's
    ``[Pasted text …]`` reported all of those as submitted, so no retry was ever
    pressed and the user was told the prompt had gone out.

    Wording is a TUI detail that changes between releases, so the check keys on
    the shape — a bracketed summary containing "paste" — not on one vendor's
    phrasing.
    """
    needle = _submit_needle("## Task\nReview the pipeline")
    assert _input_line_holds([input_line], needle)


# ------------------------------------------------------------------ the path
@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    # Keep the arrival wait and the verification loop fast — but not zero: the
    # fake repaints through the event loop the way a real reader thread does,
    # so a poll that never yields would never see the screen change.
    monkeypatch.setattr(session_mod, "_SUBMIT_POLL_S", 0.01)
    monkeypatch.setattr(session_mod, "_SUBMIT_WINDOW_S", 0.04)
    monkeypatch.setattr(session_mod, "_SUBMIT_RETRY_AFTER_S", 0.02)
    monkeypatch.setattr(session_mod, "_ARRIVAL_POLL_S", 0.01)
    monkeypatch.setattr(session_mod, "_ARRIVAL_WINDOW_S", 0.04)
    monkeypatch.setattr(fleet_actions, "READY_POLL_S", 0.01)
    monkeypatch.setattr(fleet_actions, "READY_TIMEOUT_S", 0.08)
    return Registry(pty_manager=fake_pty)


async def _open(registry: Registry, folder: Path):
    return await registry.start(str(folder), [{"agent": "claude", "name": "Alex"}])


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _live(registry: Registry, tmp_path: Path):
    await _open(registry, tmp_path)
    return await registry.attach("Alex", 100, 30, _noop, _noop_exit)


async def _live_codex(registry: Registry, tmp_path: Path):
    session = await registry.start(
        str(tmp_path), [{"agent": "codex", "name": "Cody"}]
    )
    # A dead process's input marker must not make the replacement process look
    # ready. The attach path owns clearing this generation's screen evidence.
    session.terminals[0].transcript.feed("\u203a old prompt\r\n")
    return await registry.attach("Cody", 100, 30, _noop, _noop_exit)


async def test_a_booting_codex_is_not_typed_into_until_its_input_line_exists(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    fake_pty.tui_echo = True
    term = await _live_codex(registry, tmp_path)
    await fake_pty.emit(
        term.pty_id,
        "\x1b[2J\x1b[H\u203a Input disabled.\x1b[1;3H\x1b[?25l",
    )

    sending = asyncio.create_task(registry.send_prompt("Cody", "review the pipeline"))
    await asyncio.sleep(0.03)
    assert fake_pty.typed == [], "boot-time keystrokes are swallowed by Codex"

    await fake_pty.emit(
        term.pty_id,
        "\x1b[2J\x1b[H\u203a Ask Codex anything\x1b[1;3H\x1b[?25h",
    )
    delivered = await sending

    assert fake_pty.typed[:2] == ["review the pipeline", "\r"]
    assert delivered.submitted is True


async def test_a_codex_that_never_becomes_ready_receives_no_partial_prompt(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _live_codex(registry, tmp_path)

    with pytest.raises(SessionError, match="input line never appeared"):
        await registry.send_prompt("Cody", "review the pipeline")

    assert fake_pty.typed == []


async def test_a_trailing_reference_gets_a_closing_space(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    fake_pty.tui_echo = True  # a pane that draws what it is given
    term = await _live(registry, tmp_path)
    await registry.send_prompt("Alex", "review this. @jarvis/x.py")
    assert fake_pty.typed[0] == "review this. @jarvis/x.py ", fake_pty.typed
    assert fake_pty.typed[1] == "\r"
    assert term.submitted is True


async def test_a_plain_prompt_is_typed_verbatim(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await _live(registry, tmp_path)
    await registry.send_prompt("Alex", "review the pipeline")
    assert fake_pty.typed[0] == "review the pipeline", fake_pty.typed


async def test_enter_is_pressed_again_while_the_prompt_sits_in_the_box(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The safety net for cases the space alone does not fix."""
    term = await _live(registry, tmp_path)
    # Make the screen look like the prompt never left the input line.
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ review the pipeline\r\n")

    await registry.send_prompt("Alex", "review the pipeline")

    enters = [d for d in fake_pty.typed if d == "\r"]
    assert len(enters) == 2, f"one Enter plus a single retry, got {fake_pty.typed}"
    assert term.submitted is False


async def test_hand_pressed_enter_is_verified_before_an_unsent_receipt_changes(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Enter can accept a completion, so the receipt must follow the screen."""
    term = await _live(registry, tmp_path)
    term.last_prompt = "review the pipeline"
    term.submitted = False
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ review the pipeline\r\n")

    registry.write(term.key, "\r")
    assert term.submitted is None
    await asyncio.sleep(0.08)

    assert term.submitted is False
    assert term.last_submit_at is None
    assert fake_pty.typed == ["\r"], "passive verification must never press Enter again"


async def test_hand_pressed_enter_marks_the_receipt_after_the_prompt_leaves(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    term.last_prompt = "review the pipeline"
    term.submitted = False
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ review the pipeline\r\n")

    registry.write(term.key, "\r")
    await on_output("pty", "\x1b[2J\x1b[H❯\r\n✻ Working\r\n")
    await asyncio.sleep(0.04)

    assert term.submitted is True
    assert term.last_submit_at is not None


async def test_modifier_enter_adds_a_line_without_arming_or_retrying_submission(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    term.last_prompt = "review the pipeline"
    term.submitted = False

    assert registry.write(term.key, "\x1b\r") is True
    await asyncio.sleep(0.08)

    assert term.submitted is False
    assert term.manual_submit_pending is False
    assert term.last_submit_at is None
    assert fake_pty.typed == ["\x1b\r"]


async def test_failed_enter_write_does_not_change_receipt_or_activity(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    term.last_prompt = "review the pipeline"
    term.submitted = False
    fake_pty.close(term.pty_id or "")

    assert registry.write(term.key, "\r") is False
    assert term.submitted is False
    assert term.manual_submit_pending is False
    assert term.last_input_at is None
    assert term.last_submit_at is None


async def test_repeated_enter_stays_verified_until_the_prompt_leaves(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    term.last_prompt = "review the pipeline"
    term.submitted = False
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ review the pipeline\r\n")

    assert registry.write(term.key, "\r") is True
    assert registry.write(term.key, "\r") is True
    await asyncio.sleep(0.08)

    assert term.submitted is False
    assert term.manual_submit_pending is False
    assert term.last_submit_at is None
    assert fake_pty.typed == ["\r", "\r"]


async def test_multiline_bracketed_paste_never_arms_submission(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)

    assert registry.write(term.key, f"{PASTE_START}first line\n") is True
    assert registry.write(term.key, f"second line\r\n{PASTE_END}") is True

    assert term.last_submit_at is None
    assert term.manual_submit_pending is False
    assert term.bracketed_paste_active is False


async def test_edit_after_enter_invalidates_manual_submission_observation(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    term = await _live(registry, tmp_path)
    term.last_prompt = "review the pipeline"
    term.submitted = False
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[Hâ¯ review the pipeline\r\n")

    assert registry.write(term.key, "\r") is True
    assert registry.write(term.key, "\x15") is True  # Ctrl+U clears the input line.
    await on_output("pty", "\x1b[2J\x1b[Hâ¯\r\n")
    await asyncio.sleep(0.08)

    assert term.submitted is False
    assert term.manual_submit_pending is False
    assert term.last_submit_at is None


async def test_a_collapsed_paste_is_retried_and_reported_honestly(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Codex's collapsed-paste line must trigger the retry, not a success claim.

    The live failure this guards (2026-07-26): two Codex panes were handed a
    composed brief, both left it sitting in the input box as
    ``[Pasted Content 2497 chars]``, and both were logged as "submitted" — so
    neither the extra Enter nor the single-line fallback ever ran.
    """
    term = await _live(registry, tmp_path)
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H› [Pasted Content 2497 chars]\r\n")

    await registry.send_prompt("Alex", "## Task\nReview the pipeline")

    assert [d for d in fake_pty.typed if d == "\r"], "no Enter was ever pressed"
    assert len([d for d in fake_pty.typed if d == "\r"]) >= 2, (
        f"the prompt sat in the box — Enter must be pressed again: {fake_pty.typed}"
    )
    assert term.submitted is False, "a prompt still in the box is not submitted"


async def test_a_prompt_stuck_in_the_box_is_never_typed_a_second_time(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The single-line fallback must not append a duplicate of a stuck prompt.

    The fallback exists for a pane that never RECEIVED the paste. When the text
    is demonstrably still sitting in the input box, re-typing it puts a second
    copy behind the first, and the next Enter submits both — so in that case the
    only safe move is another Enter, never another paste.
    """
    term = await _live(registry, tmp_path)
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H› [Pasted Content 2497 chars]\r\n")

    await registry.send_prompt("Alex", "## Task\nReview the pipeline")

    pastes = [d for d in fake_pty.typed if "Review the pipeline" in d]
    assert len(pastes) == 1, f"the prompt was typed twice: {fake_pty.typed}"
    assert term.submitted is False


async def test_a_prompt_never_seen_to_arrive_is_reported_as_unconfirmed(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A pane that never displayed the text gets an honest "cannot say".

    Measured against a real Codex (2026-07-26): a pane still booting swallows
    the paste outright — the input box keeps showing its own idle hint and no
    part of the prompt ever appears. An empty box is ALSO exactly what a
    successful submit looks like, so the two cannot be told apart from here and
    the old check simply picked the flattering reading and claimed success.

    ``None`` is the truthful answer, and the fan-out already treats it as
    "unknown" rather than overstating. Re-typing is deliberately NOT done: if
    the text is in fact sitting there unread, a second copy lands behind the
    first and the pane runs a doubled instruction.
    """
    term = await _live(registry, tmp_path)
    on_output = fake_pty.spawns[-1]["on_output"]
    # The pane shows its own idle hint — none of our prompt.
    await on_output("pty", "\x1b[2J\x1b[H› Find and fix a bug in @filename\r\n")

    await registry.send_prompt("Alex", "## Task\nReview the pipeline")

    bodies = [d for d in fake_pty.typed if "Review the pipeline" in d]
    assert len(bodies) == 1, f"the prompt must not be re-typed: {fake_pty.typed}"
    assert term.submitted is None, "never seen to arrive is not a success claim"


async def test_a_prompt_that_lands_reports_submitted(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The whole happy path: the pane shows the text, then Enter clears it."""
    fake_pty.tui_echo = True
    term = await _live(registry, tmp_path)

    await registry.send_prompt("Alex", "review the pipeline")
    assert term.submitted is True
    assert [d for d in fake_pty.typed if d == "\r"] == ["\r"], "no needless retries"


async def test_the_submitted_flag_is_reported(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    fake_pty.tui_echo = True
    term = await _live(registry, tmp_path)
    await registry.send_prompt("Alex", "review the pipeline")
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
        await registry.send_prompt("Alex", "review the pipeline")


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
    fake_pty.tui_echo = True
    term = await _live(registry, tmp_path)

    await registry.send_prompt("Alex", _MARKDOWN)

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

    await registry.send_prompt("Alex", "review the pipeline")

    assert PASTE_START not in fake_pty.typed[0]
    assert term.sent_multiline is False


def test_a_collapsed_paste_on_the_input_line_counts_as_pending() -> None:
    """A TUI may render a paste as a placeholder. Reading that as 'submitted'
    would hide a genuine failure behind an optimistic check."""
    needle = _submit_needle(_MARKDOWN)
    assert _input_line_holds(["❯ [Pasted text #1 +12 lines]"], needle)
    assert _input_line_holds(["> [Pasted text +4 lines]", ""], needle)


async def test_a_stuck_multiline_prompt_is_left_in_the_box_not_retyped(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A prompt the pane is still holding is never typed a second time.

    This replaces an earlier single-line fallback. That fallback was written for
    a pane that never RECEIVED the paste, but "not submitted" can only be
    reached by watching the text sit in the input box — which is proof it DID
    arrive. Re-typing then appends a second copy behind the first and the next
    Enter submits both, so the pane runs a doubled instruction.

    Nothing is lost by leaving it: the prompt sits in the pane in full, and
    ``submitted`` reports plainly that it was typed but never accepted.
    """
    term = await _live(registry, tmp_path)
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", "\x1b[2J\x1b[H❯ [Pasted text #1 +4 lines]\r\n")

    await registry.send_prompt("Alex", _MARKDOWN)

    assert _ONE_LINE not in fake_pty.typed, f"prompt was re-typed: {fake_pty.typed}"
    assert len([d for d in fake_pty.typed if "Review the ranking." in d]) == 1
    assert term.submitted is False
    assert term.sent_multiline is False, "an unsubmitted prompt did not go out"
    assert term.last_prompt == _MARKDOWN, "what sits in the box is the markdown"
