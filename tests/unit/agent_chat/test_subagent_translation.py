"""Sub-agents in a coding agent's stream: their own conversation, never the main answer.

The Claude lines are trimmed from a real ``claude -p --output-format stream-json``
run (Claude Code 2.1) that spawned one background agent; the Codex items follow
the ``collab_tool_call`` shape of ``codex exec --json``.
"""

from __future__ import annotations

from typing import Any

from jarvis.agent_chat.events import is_transient
from jarvis.agent_chat.runner_cli import (
    _ClaudeState,
    _CodexState,
    subagent_status,
    translate_claude_line,
    translate_codex_line,
)

SPAWN = "toolu_spawn"
TASK = "a73c1e05f008ad14"


def _assistant(
    content: list[dict[str, Any]], *, mid: str, parent: str | None = None
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"id": mid, "role": "assistant", "content": content},
        "parent_tool_use_id": parent,
        "session_id": "s1",
    }


def _tool_result(call_id: str, text: str, *, parent: str | None = None) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}],
        },
        "parent_tool_use_id": parent,
        "session_id": "s1",
    }


CLAUDE_LINES: list[dict[str, Any]] = [
    _assistant(
        [
            {
                "type": "tool_use",
                "id": SPAWN,
                "name": "Agent",
                "input": {
                    "description": "Count files",
                    "prompt": "Run echo",
                    "run_in_background": True,
                },
            }
        ],
        mid="m1",
    ),
    {
        "type": "system",
        "subtype": "task_started",
        "task_id": TASK,
        "tool_use_id": SPAWN,
        "description": "Count files",
        "subagent_type": "general-purpose",
        "is_backgrounded": True,
        "task_type": "local_agent",
        "prompt": "Run echo",
    },
    _tool_result(SPAWN, "Async agent launched successfully."),
    _assistant(
        [{"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {"command": "echo hi"}}],
        mid="sub1",
        parent=SPAWN,
    ),
    {
        "type": "system",
        "subtype": "task_progress",
        "task_id": TASK,
        "tool_use_id": SPAWN,
        "description": "Running echo",
        "usage": {"total_tokens": 23416, "tool_uses": 1, "duration_ms": 2265},
        "last_tool_name": "Bash",
    },
    _tool_result("toolu_bash", "hi", parent=SPAWN),
    _assistant([{"type": "text", "text": "hi"}], mid="sub2", parent=SPAWN),
    {
        "type": "system",
        "subtype": "task_notification",
        "task_id": TASK,
        "tool_use_id": SPAWN,
        "status": "completed",
        "summary": "hi",
        "usage": {"total_tokens": 25617, "tool_uses": 1, "duration_ms": 5509},
    },
    _assistant([{"type": "text", "text": "The agent said hi."}], mid="m2"),
]


def _claude_events() -> list[dict[str, Any]]:
    st = _ClaudeState(turn_id="t1")
    return [ev for line in CLAUDE_LINES for ev in translate_claude_line(line, st)]


def test_claude_subagent_messages_are_filed_under_their_spawn_call() -> None:
    events = _claude_events()
    sub = [ev for ev in events if ev["payload"].get("agent_id") == SPAWN]
    kinds = [ev["kind"] for ev in sub]
    assert kinds == [
        "subagent_started",
        "tool_call",
        "subagent_progress",
        "tool_result",
        "assistant_text",
        "subagent_finished",
    ]
    started = sub[0]["payload"]
    assert started["description"] == "Count files"
    assert started["agent_type"] == "general-purpose"
    assert started["background"] is True
    finished = sub[-1]["payload"]
    assert finished["status"] == "done"
    assert finished["summary"] == "hi"
    assert finished["tokens"] == 25617
    assert finished["tool_uses"] == 1


def test_claude_subagent_text_never_reads_as_the_main_answer() -> None:
    main_text = [
        ev["payload"]["text"]
        for ev in _claude_events()
        if ev["kind"] == "assistant_text" and "agent_id" not in ev["payload"]
    ]
    assert main_text == ["The agent said hi."]


def test_claude_background_shell_task_gets_no_agent_card() -> None:
    st = _ClaudeState(turn_id="t1")
    line = {
        "type": "system",
        "subtype": "task_started",
        "task_id": "b1",
        "tool_use_id": "toolu_shell",
        "description": "sleep 8",
        "task_type": "local_bash",
    }
    assert translate_claude_line(line, st) == []
    done = {
        "type": "system",
        "subtype": "task_notification",
        "task_id": "b1",
        "status": "completed",
    }
    assert translate_claude_line(done, st) == []


def test_subagent_progress_is_live_only() -> None:
    progress = [ev for ev in _claude_events() if ev["kind"] == "subagent_progress"]
    assert progress and all(is_transient(ev) for ev in progress)
    assert not any(is_transient(ev) for ev in _claude_events() if ev["kind"] == "subagent_finished")


def test_subagent_status_words() -> None:
    assert subagent_status("completed") == "done"
    assert subagent_status("errored") == "failed"
    assert subagent_status("killed") == "stopped"
    assert subagent_status("interrupted") == "stopped"


def _collab(phase: str, item: dict[str, Any]) -> dict[str, Any]:
    return {"type": f"item.{phase}", "item": {"type": "collab_tool_call", **item}}


def test_codex_spawn_and_wait_become_one_agent_card() -> None:
    st = _CodexState(turn_id="t1")
    spawn = {
        "id": "item_3",
        "tool": "spawn_agent",
        "sender_thread_id": "root",
        "receiver_thread_ids": [],
        "prompt": "Reply with PONG",
        "agents_states": {},
        "status": "in_progress",
    }
    events = translate_codex_line(_collab("started", spawn), st)
    events += translate_codex_line(
        _collab("completed", {**spawn, "receiver_thread_ids": ["child-1"], "status": "completed"}),
        st,
    )
    wait = {
        "id": "item_4",
        "tool": "wait",
        "sender_thread_id": "root",
        "receiver_thread_ids": ["child-1"],
        "prompt": None,
        "agents_states": {"child-1": {"status": "completed", "message": "PONG"}},
        "status": "completed",
    }
    events += translate_codex_line(_collab("completed", wait), st)
    close = {
        "id": "item_5",
        "tool": "close_agent",
        "receiver_thread_ids": ["child-1"],
        "agents_states": {"child-1": {"status": "shutdown", "message": None}},
        "status": "completed",
    }
    events += translate_codex_line(_collab("completed", close), st)

    started = [ev["payload"] for ev in events if ev["kind"] == "subagent_started"]
    assert started == [
        {
            "turn_id": "t1",
            "agent_id": "item_3",
            "thread_id": "child-1",
            "description": "Reply with PONG",
            "agent_type": "",
            "prompt": "Reply with PONG",
            "background": True,
        }
    ]
    finished = [ev["payload"] for ev in events if ev["kind"] == "subagent_finished"]
    # The close after a finished agent does not turn it into a stopped one.
    assert [(f["agent_id"], f["status"], f["summary"]) for f in finished] == [
        ("item_3", "done", "PONG")
    ]
    calls = [ev["payload"]["name"] for ev in events if ev["kind"] == "tool_call"]
    assert calls == ["spawn_agent", "wait", "close_agent"]
