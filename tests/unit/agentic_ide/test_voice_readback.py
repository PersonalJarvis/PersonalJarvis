"""A job the user gave a pane through Jarvis ends with Jarvis saying how it went.

The bell (``test_pane_notifications``) says THAT a pane stopped. This is the
spoken half: only for jobs handed over through Jarvis, once per job, phrased
from the pane's own report, and routed through the speech pipeline's
held-for-call gate so it never speaks into an idle room.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import notifications, voice_readback
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.activity import STILL_S
from jarvis.agentic_ide.session import Registry
from jarvis.core.events import AnnouncementRequested
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.voice.report_readback import plain_excerpt
from tests.fakes.fake_pty_manager import FakePtyManager

QUESTION_SCREEN = "\r\nDo you want to make this edit to config.py?\r\n❯ 1. Yes\r\n"


@pytest.fixture(autouse=True)
def _clean_store() -> Any:
    notifications.reset()
    yield
    notifications.reset()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=FakePtyManager())


@pytest.fixture
def no_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's real reply-language pin out of the language decision."""
    from jarvis.core import config

    def _unreadable() -> Any:
        raise OSError("no config in this test")

    monkeypatch.setattr(config, "load_config", _unreadable)


async def _noop(_data: Any) -> None:
    return None


async def _pane(registry: Registry, folder: Path) -> tuple[Any, Any]:
    session = await registry.start(str(folder), [{"agent": "claude", "name": "Alex"}])
    term = await registry.attach("Alex", 100, 30, _noop, _noop)
    return session, term


def _work_then_rest(
    watcher: Any, registry: Registry, term: Any, screen: str, *, emit: bool
) -> None:
    """Busy for three sweeps, then still until the stop is reported."""
    term.last_submit_at = 99.9
    term.submit_generation = term.process_generation
    at = 100.0
    for step in range(3):
        at = 100.0 + step * notifications.SWEEP_INTERVAL_S
        term.transcript.clear()
        term.transcript.feed(f"\r\n· working, frame {step}\r\n")
        watcher.poll(registry, now=at, emit=emit)
    rest = at + 1
    term.transcript.clear()
    term.transcript.feed(screen)
    term.last_output_at = rest
    term.last_input_at = None
    watcher.poll(registry, now=rest, emit=emit)
    watcher.poll(registry, now=rest + STILL_S + 2, emit=emit)
    watcher.poll(registry, now=rest + STILL_S + notifications.SETTLE_S + 4, emit=emit)


async def test_a_jarvis_job_that_finishes_owes_one_readback(
    registry: Registry, tmp_path: Path
) -> None:
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    term.voice_readback = True

    # The bell switch is off: the spoken readback does not depend on it.
    _work_then_rest(watcher, registry, term, "\r\n❯ \r\n", emit=False)

    assert watcher.take_readbacks() == [("completed", term)]
    assert term.voice_readback is False, "one readback per job"
    assert watcher.take_readbacks() == []


async def test_a_job_nobody_gave_through_jarvis_is_not_announced(
    registry: Registry, tmp_path: Path
) -> None:
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    _work_then_rest(watcher, registry, term, "\r\n❯ \r\n", emit=True)

    assert watcher.take_readbacks() == []


async def test_a_question_is_announced_and_keeps_the_job_open(
    registry: Registry, tmp_path: Path
) -> None:
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    term.voice_readback = True

    _work_then_rest(watcher, registry, term, QUESTION_SCREEN, emit=True)

    assert watcher.take_readbacks() == [("needs_input", term)]
    assert term.voice_readback is True, "its end is still owed"


async def test_a_job_typed_by_hand_cancels_the_readback(
    registry: Registry, tmp_path: Path
) -> None:
    session, term = await _pane(registry, tmp_path)
    term.voice_readback = True

    registry.write(term.key, "do something else\r", workspace_id=session.id)

    assert term.voice_readback is False


async def test_the_readback_is_phrased_from_the_agents_report(
    monkeypatch: pytest.MonkeyPatch, no_pin: None
) -> None:
    published: list[Any] = []

    async def publish(event: Any) -> None:
        published.append(event)

    term = _FakeTerm(request="Behebe bitte den Login-Fehler")  # i18n-allow: user's words
    monkeypatch.setattr(
        voice_readback,
        "final_report",
        lambda _term: "## Summary\n\nFixed the **login** bug in `auth.py`. All 12 tests pass.",
    )

    await voice_readback.readback("completed", term, publish)

    assert len(published) == 1
    event = published[0]
    assert isinstance(event, AnnouncementRequested)
    assert event.source_layer == voice_readback.SOURCE_LAYER
    assert event.kind == "completion"
    assert event.language == "de"
    # The live model gets the whole report and the user's own words to think about.
    assert "Fixed the **login** bug in `auth.py`." in (event.report or "")
    assert "Behebe bitte den Login-Fehler" in (event.report or "")  # i18n-allow: user's words
    # The test composer is canned-only (root conftest): pane name + the
    # report's first sentences, markdown gone.
    spoken = (
        "Alex ist fertig: "  # i18n-allow: spoken readback
        "Summary Fixed the login bug in auth.py. All 12 tests pass."
    )
    assert event.text == spoken


async def test_without_a_report_the_readback_still_names_the_pane(
    monkeypatch: pytest.MonkeyPatch, no_pin: None
) -> None:
    published: list[Any] = []

    async def publish(event: Any) -> None:
        published.append(event)

    monkeypatch.setattr(voice_readback, "final_report", lambda _term: "")

    await voice_readback.readback("completed", _FakeTerm(request="Run the tests"), publish)

    assert [e.text for e in published] == ["Alex has finished your task."]


def test_a_remote_pane_never_reads_its_stale_local_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#250: the transcript of a pane on a connected computer lives there; the
    local copy ends with the previous job's answer."""
    from jarvis.agentic_ide import agent_transcript

    def _stale(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a remote pane's local transcript is stale")

    monkeypatch.setattr(agent_transcript, "read", _stale)
    monkeypatch.setattr(agent_transcript, "can_read", lambda _agent: True)
    term = _FakeTerm(request="Fix the login bug")
    term.computer_id = "vps-1"
    term.agent = "claude"
    term.resume = type("Handle", (), {"id": "abc"})()

    assert voice_readback.final_report(term) == ""


def test_the_pipeline_holds_pane_readbacks_for_an_open_call() -> None:
    event = AnnouncementRequested(
        source_layer=voice_readback.SOURCE_LAYER, kind="completion", text="Alex is done."
    )
    assert SpeechPipeline._is_agent_reply(event)


def test_an_excerpt_is_never_cut_inside_a_word() -> None:
    long = " ".join(f"word{i}" for i in range(80))
    excerpt = plain_excerpt(long, max_words=10)
    assert excerpt.split()[:10] == [f"word{i}" for i in range(10)]
    assert excerpt.endswith("…")
    assert plain_excerpt("First one. Second one. Third.", max_words=4) == "First one. Second one."


class _FakeTerm:
    name = "Alex"

    def __init__(self, *, request: str) -> None:
        self.voice_readback_request = request
        self.transcript = _Screen()


class _Screen:
    @staticmethod
    def tail(_n: int) -> list[str]:
        return []
