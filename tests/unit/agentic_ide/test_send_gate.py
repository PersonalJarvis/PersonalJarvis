"""The delivery gate judges a pane on fresh lifecycle evidence, not a stale stamp.

Live 2026-10-07: an idle Claude pane that had finished its turn (recap shown,
empty input) refused an approved brief as "busy", and a pane whose transcript
ended in a queued notice that never started a turn read "working" for an hour.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agentic_ide import activity, delegation_wait, task_state
from jarvis.agentic_ide.session import Registry, Session, SessionError, Terminal
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def clean_evidence():
    task_state.reset()
    yield
    task_state.reset()


def at(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat()


def user(moment: float, text: str = "Analyse PR #430", **extra) -> dict:
    return {"timestamp": at(moment), "type": "user",
            "message": {"role": "user", "content": text}, **extra}


def assistant(moment: float, stop: str = "end_turn") -> dict:
    return {"timestamp": at(moment), "type": "assistant",
            "message": {"role": "assistant", "stop_reason": stop,
                        "content": [{"type": "text", "text": "Done."}]}}


def footer(moment: float) -> list[dict]:
    return [
        {"timestamp": at(moment), "type": "system", "subtype": "stop_hook_summary"},
        {"timestamp": at(moment), "type": "system", "subtype": "turn_duration"},
        {"timestamp": at(moment + 180), "type": "system", "subtype": "away_summary"},
    ]


def notice(moment: float) -> dict:
    """The row Claude Code writes for a queued notice that starts no turn."""
    return user(
        moment,
        "<task-notification>\n<status>stopped</status>\n<summary>Background shell "
        "command didn't finish before the previous session ended</summary>\n"
        "</task-notification>",
        origin={"kind": "task-notification"}, promptSource="system",
        queueTranscriptOnly=True,
    )


@pytest.fixture
def pane(tmp_path, monkeypatch):
    record = tmp_path / "session.jsonl"
    record.write_text("", encoding="utf-8")
    monkeypatch.setattr(task_state.agent_transcript, "_claude_file", lambda *_: record)
    registry = Registry(pty_manager=FakePtyManager())
    term = Terminal("t1", "T1", "claude", "Claude Code", 0, status="live")
    term.pty_id = "pty-1"
    term.resume = SimpleNamespace(id="session")
    workspace = Session(
        id=uuid4().hex, folder=str(tmp_path), name="pull requests", profile=None,
        terminals=[term], created_at=1, project_id="project",
    )
    registry._sessions[workspace.id] = workspace
    sent: list[str] = []

    async def deliver(identity, text, **_):
        sent.append(text)
        return term

    monkeypatch.setattr(registry, "_send_prompt_locked", deliver)
    monkeypatch.setattr(delegation_wait, "track_submission", lambda *_: None)
    return registry, workspace, term, record, sent


def write(record, *rows):
    with record.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def submitted(term, moment: float) -> None:
    term.last_submit_at = moment
    term.submit_generation = term.process_generation


async def send(registry, workspace, term):
    await registry.send_prompt(
        "pane:" + term.history_id, "Updated brief", workspace_id=workspace.id,
        require_idle=True,
    )


async def test_finished_turn_is_sendable_despite_a_stale_working_stamp(pane):
    registry, workspace, term, record, sent = pane
    now = time.time()
    submitted(term, now - 1200)
    write(record, user(now - 1199), assistant(now - 60), *footer(now - 60))
    # The sweep's last word, from while the turn ran, is still "fresh".
    activity.stamp(term, "working", now=now)
    await send(registry, workspace, term)
    assert sent == ["Updated brief"]


async def test_enter_that_started_no_turn_does_not_make_the_pane_busy(pane):
    registry, workspace, term, record, sent = pane
    now = time.time()
    write(record, user(now - 1200), assistant(now - 600), *footer(now - 600))
    # An empty Enter / slash command after the turn: a submit with no record.
    submitted(term, now - 120)
    await task_state.probe(term)
    assert activity.read_activity(term) == "unknown"  # the badge stays honest
    activity.stamp(term, "unknown", now=now)
    await send(registry, workspace, term)
    assert sent == ["Updated brief"]


async def test_queued_notice_does_not_keep_a_finished_pane_working(pane):
    registry, workspace, term, record, sent = pane
    now = time.time()
    submitted(term, now - 3600)
    write(record, user(now - 3599), assistant(now - 3000), *footer(now - 3000),
          notice(now - 1800))
    await task_state.probe(term)
    assert activity.read_activity(term) == "waiting"
    await send(registry, workspace, term)
    assert sent == ["Updated brief"]


async def test_an_interrupted_turn_accepts_the_next_instruction(pane):
    registry, workspace, term, record, sent = pane
    now = time.time()
    submitted(term, now - 300)
    write(record, user(now - 299), user(now - 200, "[Request interrupted by user]"))
    await send(registry, workspace, term)
    assert sent == ["Updated brief"]


@pytest.mark.parametrize("last", ["tool_use", "question"])
async def test_a_running_turn_or_open_question_still_refuses(pane, last):
    registry, workspace, term, record, sent = pane
    now = time.time()
    submitted(term, now - 300)
    write(record, user(now - 299))
    if last == "tool_use":
        write(record, assistant(now - 5, stop="tool_use"))
    else:
        write(record, {"timestamp": at(now - 5), "type": "assistant",
                       "message": {"role": "assistant", "content": [
                           {"type": "tool_use", "name": "AskUserQuestion"}]}})
    activity.stamp(term, "waiting", now=now)  # even a stamp saying otherwise
    with pytest.raises(SessionError, match="busy"):
        await send(registry, workspace, term)
    assert sent == []


async def test_fresh_submission_is_busy_before_its_record_appears(pane):
    registry, workspace, term, record, sent = pane
    now = time.time()
    write(record, user(now - 600), assistant(now - 500), *footer(now - 500))
    submitted(term, now - 1)  # just sent; the CLI has not written its row yet
    with pytest.raises(SessionError, match="busy"):
        await send(registry, workspace, term)
    assert sent == []
