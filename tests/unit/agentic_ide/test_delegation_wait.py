"""Coding completion must be fresh, correlated and recorded, not inferred from idle."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import agent_transcript, delegation_wait
from jarvis.core.delegated_work import collect_delegated_work
from jarvis.core.protocols import ChatTurn, current_chat_turn


def stamp(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def codex(kind, payload, at=101):
    return {"type": kind, "payload": payload, "timestamp": stamp(at)}


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_transcript, "_codex_file", lambda *a, **kw: path)
    monkeypatch.setattr(agent_transcript, "_claude_file", lambda *a, **kw: path)
    term = SimpleNamespace(
        agent="codex",
        account=None,
        resume=SimpleNamespace(id="s"),
        process_generation=1,
        last_submit_at=100.0,
        last_prompt="Fix the bug",
        history_id="pane",
        submitted=True,
        name="Coder",
        status="running",
    )

    def write(*rows):
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    return term, write


def user(at=101, text="Fix the bug"):
    return codex(
        "response_item",
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        at,
    )


def assistant(at=102):
    return codex(
        "response_item",
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Fixed with proof"}],
        },
        at,
    )


def test_idle_or_assistant_prose_without_end_marker_is_not_completion(recorded):
    term, write = recorded
    write(user(), assistant())
    assert delegation_wait._read(term, (1, 100))["status"] == "running"
    write(user(), assistant(), codex("event_msg", {"type": "task_complete"}, 103))
    result = delegation_wait._read(term, (1, 100))
    assert result["status"] == "completed"
    assert result["report"] == "Fixed with proof"


def test_old_answer_or_different_prompt_cannot_complete_current_job(recorded):
    term, write = recorded
    write(user(90), assistant(91), codex("event_msg", {"type": "task_complete"}, 92))
    assert delegation_wait._read(term, (1, 100)) is None
    write(
        user(text="Another request"),
        assistant(),
        codex("event_msg", {"type": "task_complete"}, 103),
    )
    assert delegation_wait._read(term, (1, 100)) is None


def test_interrupted_coding_turn_stays_cancelled(recorded):
    term, write = recorded
    write(user(), assistant(), codex("event_msg", {"type": "turn_aborted"}, 103))
    assert delegation_wait._read(term, (1, 100))["status"] == "cancelled"


@pytest.mark.parametrize("end", ["stop_reason", "turn_duration"])
def test_claude_native_completion_markers_are_honored(recorded, end):
    term, write = recorded
    term.agent = "claude"
    rows = [
        {"type": "user", "timestamp": stamp(101), "message": {"content": term.last_prompt}},
        {
            "type": "assistant",
            "timestamp": stamp(102),
            "message": {
                "content": [{"type": "text", "text": "Report"}],
                "stop_reason": "end_turn" if end == "stop_reason" else None,
            },
        },
    ]
    if end == "turn_duration":
        rows.append({"type": "system", "subtype": "turn_duration", "timestamp": stamp(103)})
    write(*rows)
    assert delegation_wait._read(term, (1, 100))["report"] == "Report"


async def test_new_submission_during_wait_is_unverified_and_closing_is_terminal(recorded):
    term, _ = recorded
    owner = SimpleNamespace(id="workspace")
    registry = SimpleNamespace(find_terminal=lambda *args: (owner, term))
    accepted = []
    token = current_chat_turn.set(ChatTurn("chat", "turn", "work", True, "trace"))
    try:
        with collect_delegated_work(accepted.append):
            delegation_wait.track_submission(registry, owner, term)
    finally:
        current_chat_turn.reset(token)
    term.last_submit_at = 200
    assert (await accepted[0].probe())["status"] == "superseded"
    registry.find_terminal = lambda *args: None
    assert (await accepted[0].probe())["status"] == "stopped"


def test_claude_interruption_is_not_completed_by_timing_record(recorded):
    term, write = recorded
    term.agent = "claude"
    write(
        {"type": "user", "timestamp": stamp(101), "message": {"content": term.last_prompt}},
        {"type": "assistant", "timestamp": stamp(102), "message": {"content": "Starting"}},
        {
            "type": "user",
            "timestamp": stamp(103),
            "message": {
                "content": "[Request interrupted by user]",
            },
        },
        {"type": "system", "subtype": "turn_duration", "timestamp": stamp(104)},
    )
    assert delegation_wait._read(term, (1, 100))["status"] == "cancelled"


def test_coding_question_is_returned_as_input_needed(recorded):
    term, write = recorded
    write(
        user(),
        codex(
            "response_item",
            {
                "type": "function_call",
                "call_id": "ask1",
                "name": "request_user_input",
                "arguments": json.dumps({"question": "Which project?"}),
            },
            102,
        ),
    )
    assert delegation_wait._read(term, (1, 100))["status"] == "needs_input"


def _claude_steered_turn(term, write):
    term.agent = "claude"
    write(
        {"type": "user", "timestamp": stamp(101), "message": {"content": "Fix the bug"}},
        {"type": "assistant", "timestamp": stamp(102), "message": {
            "content": [{"type": "tool_use", "id": "c1", "name": "Read", "input": {}}]}},
        {"type": "attachment", "timestamp": stamp(103), "attachment": {
            "type": "queued_command", "prompt": "Only the save flow",
            "commandMode": "prompt", "origin": {"kind": "human"}}},
        {"type": "assistant", "timestamp": stamp(104), "message": {
            "content": [{"type": "text", "text": "Report"}], "stop_reason": "end_turn"}},
    )


def test_a_note_typed_into_the_running_turn_does_not_orphan_the_job(recorded):
    # The note is part of the same work: the job still completes with its report.
    term, write = recorded
    _claude_steered_turn(term, write)
    result = delegation_wait._read(term, (1, 100))
    assert result["status"] == "completed" and result["report"] == "Report"


def test_a_steered_correction_completes_with_the_turn_it_joined(recorded):
    term, write = recorded
    term.last_prompt, term.last_submit_at = "Only the save flow", 102.5
    _claude_steered_turn(term, write)
    result = delegation_wait._read(term, (1, 102.5))
    assert result["status"] == "completed" and result["report"] == "Report"
