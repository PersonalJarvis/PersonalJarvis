"""Lifecycle evidence outranks quiet screens, stale reports and repainting."""

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import activity, notifications, task_state


@pytest.fixture(autouse=True)
def reset():
    task_state.reset()
    yield
    task_state.reset()


def row(kind, payload, at=None):
    return {
        "timestamp": datetime.fromtimestamp(at or time.time(), UTC).isoformat(),
        "type": kind, "payload": payload,
    }


@pytest.fixture
def pane(tmp_path, monkeypatch):
    path = tmp_path / "rollout.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(task_state.agent_transcript, "_codex_file", lambda *_: path)
    term = SimpleNamespace(
        agent="codex", account=None, resume=SimpleNamespace(id="session"),
        process_generation=1, submit_generation=1, last_submit_at=time.time() - 100,
        pty_id="pty", status="live", last_output_at=0, last_input_at=None,
        transcript=SimpleNamespace(screen=SimpleNamespace(display=lambda: ["Ready"])),
        activity="", activity_since=0, activity_at=0, prompts_sent=1,
        key="pane", name="Task", resume_continuation_needed=False,
    )
    registry = SimpleNamespace(sessions=[SimpleNamespace(id="workspace", terminals=[term])])
    return term, registry, path


def append(path, *rows):
    with path.open("a", encoding="utf-8") as handle:
        for item in rows:
            handle.write(json.dumps(item) + "\n")


@pytest.mark.asyncio
async def test_silent_tool_stays_working_without_a_false_completion(pane):
    term, registry, path = pane
    append(path, row("event_msg", {"type": "task_started"}, time.time() - 90))
    await task_state.refresh(registry)
    watcher = notifications.ActivityWatcher(notifications.NotificationCenter())
    for _ in range(3):
        assert activity.read_activity(term) == "working"
        assert watcher.poll(registry) == []
    assert activity.observed(term).activity == "working"


@pytest.mark.asyncio
async def test_completion_abort_question_and_resume_are_distinct(pane):
    term, registry, path = pane
    cases = [
        ("event_msg", {"type": "task_started"}, "working"),
        ("response_item", {"type": "message", "role": "assistant",
                           "content": [{"type": "output_text", "text": "Finished?"}]}, "working"),
        ("response_item", {"type": "function_call", "name": "request_user_input"}, "asking"),
        ("response_item", {"type": "function_call_output"}, "working"),
        ("event_msg", {"type": "turn_aborted"}, "stopped"),
        ("event_msg", {"type": "task_started"}, "working"),
        ("event_msg", {"type": "task_complete"}, "waiting"),
        ("event_msg", {"type": "task_started"}, "working"),
    ]
    for kind, payload, expected in cases:
        append(path, row(kind, payload))
        term.last_output_at = time.time()  # Even a repaint cannot revive a finished task.
        await task_state.refresh(registry)
        assert activity.read_activity(term) == expected


@pytest.mark.asyncio
async def test_old_completion_missing_record_and_remote_record_are_unknown(pane):
    term, registry, path = pane
    append(path, row("event_msg", {"type": "task_started"}, term.last_submit_at - 2))
    await task_state.refresh(registry)
    # An earlier job still running when the new submit came proves nothing.
    assert activity.read_activity(term) == "unknown"
    append(path, row("event_msg", {"type": "task_complete"}, term.last_submit_at - 1))
    await task_state.refresh(registry)
    # A finished job, then a submit that never started a turn (an empty Enter,
    # a slash command): the still pane is back where that job left it, but the
    # old completion never finishes the new submit.
    assert task_state.evidence(term).state == "unknown"
    assert activity.read_activity(term) == "waiting"
    term.last_submit_at = time.time() - 1
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "unknown"  # inside the submit grace
    term.last_submit_at = time.time() - 100
    append(path, row("event_msg", {"type": "task_complete"}))
    term.computer_id = "other-machine"
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "unknown"
    term.computer_id = ""
    path.unlink()
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "unknown"


@pytest.mark.asyncio
async def test_question_outweighs_motion_and_process_exit_outweighs_history(pane):
    term, registry, path = pane
    append(path, row("event_msg", {"type": "task_started"}))
    await task_state.refresh(registry)
    term.last_output_at = time.time()
    term.transcript.screen.display = lambda: ["Select an option"]
    assert activity.read_activity(term) == "asking"
    term.status = "exited"
    assert activity.read_activity(term) == "exited"


@pytest.mark.asyncio
async def test_stale_cache_and_new_generation_cannot_reuse_completion(pane):
    term, registry, path = pane
    append(path, row("event_msg", {"type": "task_complete"}))
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "waiting"
    assert activity.read_activity(term, now=time.time() + 10) == "unknown"
    term.process_generation += 1
    assert task_state.evidence(term) is None


@pytest.mark.asyncio
async def test_partial_new_record_cannot_leave_old_completion_authoritative(pane):
    term, registry, path = pane
    append(path, row("event_msg", {"type": "task_complete"}))
    with path.open("a") as handle:
        handle.write('{"type":"event_msg","payload":')
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "unknown"


@pytest.mark.asyncio
async def test_unchanged_file_is_not_parsed_again(pane, monkeypatch):
    term, registry, path = pane
    append(path, row("event_msg", {"type": "task_started"}))
    await task_state.refresh(registry)
    def unexpected(*_):
        pytest.fail("unchanged lifecycle record was parsed again")
    monkeypatch.setattr(task_state, "transition", unexpected)
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "working"


@pytest.mark.parametrize(("data", "expected"), [
    ({"type": "assistant", "message": {"stop_reason": "tool_use"}}, "working"),
    ({"type": "assistant", "message": {"stop_reason": "end_turn"}}, "completed"),
    ({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "AskUserQuestion"}]}}, "asking"),
    ({"type": "user", "message": {"content": "[Request interrupted by user]"}}, "stopped"),
    ({"type": "user", "message": {"content": "<task-notification>"}}, "working"),
    ({"type": "user", "queueTranscriptOnly": True,
      "message": {"content": "<task-notification>"}}, None),
    ({"type": "system", "subtype": "turn_duration"}, "completed"),
    ({"type": "assistant", "isSidechain": True,
      "message": {"stop_reason": "end_turn"}}, None),
])
def test_claude_protocol_markers(data, expected):
    result = task_state.transition("claude", data)
    assert (result[0] if result else None) == expected


@pytest.mark.asyncio
async def test_claude_timing_footer_cannot_erase_an_interruption(pane, monkeypatch):
    term, registry, path = pane
    term.agent = "claude"
    monkeypatch.setattr(task_state.agent_transcript, "_claude_file", lambda *_: path)
    at = datetime.now(UTC).isoformat()
    append(path, {
        "timestamp": at, "type": "user",
        "message": {"content": "[Request interrupted by user]"},
    }, {"timestamp": at, "type": "system", "subtype": "turn_duration"})
    await task_state.refresh(registry)
    assert activity.read_activity(term) == "stopped"
