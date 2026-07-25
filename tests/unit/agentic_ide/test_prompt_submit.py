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
    Registry,
    SessionError,
    _input_line_holds,
    _opens_completion,
    _submit_needle,
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
