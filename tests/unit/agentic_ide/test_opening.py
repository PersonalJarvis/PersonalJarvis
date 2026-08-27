"""The remembered opening of a pane's CLI conversation.

What is testable is the memory around the file read: that a pane is read once
and then answered from the cache, that a pane without a handle costs nothing,
that a file not there yet is retried on a slow clock rather than every poll,
and that the read happens off the event loop when one is running.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import agent_transcript, opening


@pytest.fixture(autouse=True)
def _clean():
    opening.reset_for_tests()
    yield
    opening.reset_for_tests()


def _pane(session_id: str = "abc", key: str = "mika") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        agent="claude",
        account="",
        resume=SimpleNamespace(kind="claude_session", id=session_id),
    )


def test_a_pane_without_a_handle_is_never_read(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        agent_transcript, "first_user_text", lambda *a, **k: calls.append("read") or "hello"
    )
    assert opening.opening_for(SimpleNamespace(key="k", agent="claude", resume=None)) == ""
    assert opening.opening_for(SimpleNamespace(key="k", agent="shell", resume=None)) == ""
    assert calls == []


def test_the_opening_is_read_once_and_then_remembered(monkeypatch) -> None:
    """The whole point: one file read per pane, however often the list polls."""
    calls: list[str] = []

    def _read(agent: str, session_id: str, *, home=None) -> str:
        calls.append(session_id)
        return "@.jarvis/drops/shot.png  Fix   the login\nmore"

    monkeypatch.setattr(agent_transcript, "first_user_text", _read)
    term = _pane()

    first = opening.opening_for(term)
    for _ in range(5):
        assert opening.opening_for(term) == first

    assert first == "@.jarvis/drops/shot.png Fix the login more"
    assert calls == ["abc"]


def test_a_file_not_there_yet_is_retried_on_a_slow_clock(monkeypatch) -> None:
    calls: list[str] = []
    answers = iter([None, "", "Make the wizard one screen"])

    def _read(agent: str, session_id: str, *, home=None):
        calls.append(session_id)
        return next(answers)

    monkeypatch.setattr(agent_transcript, "first_user_text", _read)
    clock = [1000.0]
    monkeypatch.setattr(opening.time, "monotonic", lambda: clock[0])
    term = _pane()

    assert opening.opening_for(term) == ""
    assert opening.opening_for(term) == ""  # within RETRY_S: not asked again
    assert calls == ["abc"]

    clock[0] += opening.RETRY_S + 1
    assert opening.opening_for(term) == ""  # the file is there, nobody spoke yet
    assert calls == ["abc", "abc"]

    clock[0] += opening.RETRY_S + 1
    assert opening.opening_for(term) == "Make the wizard one screen"
    assert opening.opening_for(term) == "Make the wizard one screen"
    assert calls == ["abc", "abc", "abc"]


def test_under_a_running_loop_the_read_happens_off_it(monkeypatch) -> None:
    """The first poll answers at once with ""; the next one has the text."""
    monkeypatch.setattr(agent_transcript, "first_user_text", lambda *a, **k: "Refactor the parser")
    term = _pane()

    async def scenario() -> tuple[str, str]:
        first = opening.opening_for(term)
        await asyncio.sleep(0.2)
        return first, opening.opening_for(term)

    assert asyncio.run(scenario()) == ("", "Refactor the parser")


def test_a_reader_that_raises_never_reaches_the_caller(monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(agent_transcript, "first_user_text", _boom)
    assert opening.opening_for(_pane()) == ""


def test_a_closed_pane_is_forgotten_and_a_new_conversation_is_its_own(monkeypatch) -> None:
    answers = {"abc": "First job", "xyz": "Second job"}
    monkeypatch.setattr(
        agent_transcript, "first_user_text", lambda agent, sid, *, home=None: answers[sid]
    )
    assert opening.opening_for(_pane("abc")) == "First job"
    # Same call-sign, different conversation: never the last one's first line.
    assert opening.opening_for(_pane("xyz")) == "Second job"

    opening.forget("mika")
    assert opening._cache == {}  # noqa: SLF001 - the memory under test


def test_a_pasted_brief_is_capped_to_a_titles_raw_material(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_transcript,
        "first_user_text",
        lambda *a, **k: "word " * opening.MAX_CHARS,
    )
    assert len(opening.opening_for(_pane())) == opening.MAX_CHARS
