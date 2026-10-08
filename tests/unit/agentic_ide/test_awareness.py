"""Session availability and evidence must describe the actual conversation."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import agent_transcript, awareness, session, task_state
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.control import CodingSessionControl


def pane(**kwargs):
    return session.Terminal(
        "t1",
        "First",
        "codex",
        "Codex",
        0,
        status="live",
        activity="waiting",
        activity_at=time.time(),
        **kwargs,
    )


def test_allocated_native_id_does_not_mean_session_has_history():
    term = pane(resume=ResumeHandle("codex", "unused", 0.0))
    assert awareness.snapshot(term)["availability"] == "empty"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"last_submit_at": 1.0},
        {"prompts_sent": 1},
        {"resumed": True},
        {"last_prompt": "Already assigned"},
    ],
)
def test_manual_and_restored_work_cannot_be_called_empty(kwargs):
    assert awareness.snapshot(pane(**kwargs))["availability"] == "idle"


def test_native_lifecycle_overrides_stale_idle_badge(monkeypatch):
    term = pane(pty_id="live-pty")
    monkeypatch.setitem(
        task_state._readings,
        task_state._key(term),
        task_state.Evidence("working", time.time(), time.time()),
    )
    result = awareness.snapshot(term)
    assert result["activity"] == "working"
    assert result["availability"] == "busy"


def test_remote_record_is_not_read_from_local_account(monkeypatch):
    term = pane(resume=ResumeHandle("codex", "remote", 0.0), computer_id="another-device")

    def forbidden(*args, **kwargs):
        pytest.fail("Remote sessions must not read unrelated local transcripts")

    monkeypatch.setattr(agent_transcript, "read_timeline", forbidden)
    result = awareness.snapshot(term, context=True)
    assert result["availability"] == "idle"
    assert result["context_available"] is False


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_native_chat_and_tool_records_are_visible_and_bounded(tmp_path, monkeypatch, agent):
    term = pane(resume=ResumeHandle(agent, "session", 0.0))
    term.agent = agent
    monkeypatch.setattr(session, "account_home", lambda *args: tmp_path)
    timestamp = "2026-01-01T12:00:00Z"
    if agent == "codex":
        path = tmp_path / "sessions" / "2026" / "01" / "01" / "rollout-session.jsonl"
        rows = [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix queue"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "read_file",
                    "call_id": "call-1",
                    "arguments": '{"path":"queue.py"}',
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "x" * 5000,
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Reading queue"}],
                },
            },
        ]
    else:
        path = tmp_path / "projects" / "project" / "session.jsonl"
        rows = [
            {"type": "user", "message": {"role": "user", "content": "Fix queue"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "read_file",
                            "input": {"path": "queue.py"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call-1", "content": "x" * 5000},
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Reading queue"},
                    ],
                },
            },
        ]
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(json.dumps({**row, "timestamp": timestamp}) for row in rows), encoding="utf-8"
    )
    result = awareness.snapshot(term, context=True)
    assert result["last_user_message"] == "Fix queue"
    assert result["last_assistant_message"] == "Reading queue"
    assert result["has_history"] is True
    assert result["availability"] != "empty"
    assert any(event.get("name") == "read_file" for event in result["recent_events"])
    assert len(json.dumps(result)) < 4000


def test_changed_session_discards_old_context(monkeypatch):
    term = pane(resume=ResumeHandle("codex", "first", 0.0))

    def read(*args, **kwargs):
        term.resume = ResumeHandle("codex", "second", 0.0)
        return agent_transcript.TimelineRead(
            [
                {"kind": "user_message", "payload": {"text": "Old session"}},
            ]
        )

    monkeypatch.setattr(agent_transcript, "read_timeline", read)
    result = awareness.snapshot(term, context=True)
    assert result["context_available"] is False
    assert "last_user_message" not in result


async def test_context_pages_and_observe_return_exact_recorded_events(monkeypatch):
    term = pane(resume=ResumeHandle("codex", "recorded", 0.0))
    owner = SimpleNamespace(id="workspace", folder="project")
    registry = SimpleNamespace(
        find_terminal=lambda *args: (owner, term), input_token=lambda t: "token"
    )
    events = [{"kind": "assistant_text", "payload": {"text": f"Message {n}"}} for n in range(40)]
    monkeypatch.setattr(
        agent_transcript, "read_timeline", lambda *a, **kw: agent_transcript.TimelineRead(events)
    )
    gateway = CodingSessionControl(registry)
    args = {
        "action": "context",
        "workspace_id": owner.id,
        "terminal_id": "pane:" + term.history_id,
        "limit": 10,
    }
    first = await gateway.run(args)
    second = await gateway.run({**args, "cursor": first["cursor"]})
    assert first["events"] == events[:10]
    assert second["events"] == events[10:20]
    observed = await gateway.run({**args, "action": "observe", "limit": 2})
    assert observed["events"] == events[-2:]
    assert observed["earlier_events_omitted"] == 38
    assert observed["has_more"] is False
