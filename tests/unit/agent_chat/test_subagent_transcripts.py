"""Codex sub-agents read from their own rollout files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.agent_chat.subagent_transcripts import codex_subagents

PARENT = "01a0debc-8389-7101-baa5-462e6e90a3c4"
CHILD = "01a0debd-5013-7810-9557-0fe75ec805fc"
GRANDCHILD = "01a0debf-1111-7810-9557-0fe75ec805fc"
OTHER = "01a0dec0-2222-7810-9557-0fe75ec805fc"


def _meta(thread: str, parent: str, *, start: int, path: str) -> dict[str, Any]:
    return {
        "timestamp": "2026-09-26T17:22:24.088Z",
        "ordinal": 0,
        "type": "session_meta",
        "payload": {
            "id": thread,
            "parent_thread_id": parent,
            "timestamp": "2026-09-26T17:22:24.088Z",
            "thread_source": "subagent",
            "agent_nickname": "Heisenberg",
            "agent_role": "default",
            "agent_path": path,
            "subagent_history_start_ordinal": str(start),
        },
    }


def _item(ordinal: int, payload: dict[str, Any], kind: str = "response_item") -> dict[str, Any]:
    return {
        "timestamp": "2026-09-26T17:22:30.000Z",
        "ordinal": ordinal,
        "type": kind,
        "payload": payload,
    }


def _write(home: Path, thread: str, lines: list[dict[str, Any]], day: str = "2026/09/26") -> None:
    folder = home / "sessions" / Path(day)
    folder.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(line) for line in lines) + "\n"
    (folder / f"rollout-2026-09-26T19-22-24-{thread}.jsonl").write_text(body, encoding="utf-8")


def _child_lines() -> list[dict[str, Any]]:
    return [
        _meta(CHILD, PARENT, start=3, path="/root/research"),
        # Before the start ordinal: the parent's context, copied in — never shown.
        _item(
            1,
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "PARENT WORDS"}],
            },
        ),
        _item(3, {"type": "task_started"}, kind="event_msg"),
        _item(
            4,
            {
                "type": "reasoning",
                "id": "rs1",
                "summary": [{"type": "summary_text", "text": "**Reading the docs**"}],
            },
        ),
        _item(
            5,
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "c1",
                "arguments": json.dumps({"cmd": "ls -la"}),
            },
        ),
        _item(6, {"type": "function_call_output", "call_id": "c1", "output": "total 0"}),
        _item(
            7,
            {
                "type": "message",
                "id": "m1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Found nothing."}],
            },
        ),
        _item(
            8, {"type": "task_complete", "last_agent_message": "Found nothing."}, kind="event_msg"
        ),
    ]


def test_reads_a_subagents_own_steps_from_its_rollout(tmp_path: Path) -> None:
    _write(tmp_path, CHILD, _child_lines())
    agents = codex_subagents(PARENT, since_ms=1790443000000, homes=[tmp_path])
    assert len(agents) == 1
    agent = agents[0]
    assert (agent.thread_id, agent.parent_thread_id, agent.nickname, agent.path) == (
        CHILD,
        PARENT,
        "Heisenberg",
        "/root/research",
    )
    assert agent.status == "done"
    assert agent.summary == "Found nothing."
    kinds = [
        (ev["kind"], ev["payload"].get("name") or ev["payload"].get("text")) for ev in agent.events
    ]
    assert kinds == [
        ("reasoning", "**Reading the docs**"),
        ("tool_call", "RunCommand"),
        ("tool_result", None),
        ("assistant_text", "Found nothing."),
    ]
    assert agent.events[1]["payload"]["input"] == {"command": "ls -la"}
    assert "PARENT WORDS" not in json.dumps(agent.events)


def test_follows_nested_subagents_and_skips_strangers(tmp_path: Path) -> None:
    _write(tmp_path, CHILD, _child_lines())
    _write(
        tmp_path,
        GRANDCHILD,
        [
            _meta(GRANDCHILD, CHILD, start=1, path="/root/research/tests"),
            _item(1, {"type": "task_started"}, kind="event_msg"),
        ],
    )
    _write(
        tmp_path,
        OTHER,
        [_meta(OTHER, "01a0ffff-0000-7810-9557-0fe75ec805fc", start=1, path="/root/x")],
    )
    agents = codex_subagents(PARENT, since_ms=1790443000000, homes=[tmp_path])
    assert [(a.thread_id, a.status) for a in agents] == [(CHILD, "done"), (GRANDCHILD, "running")]


def test_refuses_a_thread_id_that_is_no_id(tmp_path: Path) -> None:
    assert codex_subagents("../../etc", since_ms=0, homes=[tmp_path]) == []


def test_route_lists_a_codex_threads_subagents(tmp_path: Path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.agent_chat import subagent_transcripts
    from jarvis.agent_chat.service import AgentChatService
    from jarvis.agent_chat.store import AgentChatStore
    from jarvis.ui.web.agent_chat_routes import router

    # The thread was created just now; its sub-agent's rollout is filed under today.
    _write(tmp_path, CHILD, _child_lines(), day=datetime.now(UTC).strftime("%Y/%m/%d"))
    monkeypatch.setattr(subagent_transcripts, "codex_homes", lambda account_id=None: [tmp_path])
    svc = AgentChatService(AgentChatStore(":memory:"))
    codex = svc.create_session(provider="openai-codex", cwd=str(tmp_path), surface="agent")
    svc.store.update_session(codex.session_id, vendor_session=PARENT)
    claude = svc.create_session(provider="claude-api", cwd=str(tmp_path), surface="agent")
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = svc
    with TestClient(app) as client:
        body = client.get(f"/api/agent-chat/sessions/{codex.session_id}/subagents").json()
        assert [agent["thread_id"] for agent in body["agents"]] == [CHILD]
        assert body["agents"][0]["status"] == "done"
        # Claude Code streams its sub-agents; nothing to read from files.
        other = client.get(f"/api/agent-chat/sessions/{claude.session_id}/subagents").json()
        assert other == {"agents": []}
        assert client.get("/api/agent-chat/sessions/nope/subagents").status_code == 404
