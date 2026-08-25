"""Agent chat — effort ladders, the store, the tools, the event reducers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jarvis.agent_chat import effort
from jarvis.agent_chat.catalog import PROVIDER_ROWS, provider_row
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.runner_api import messages_from_events
from jarvis.agent_chat.runner_cli import (
    _ClaudeState,
    _CodexState,
    translate_claude_line,
    translate_codex_line,
)
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.tools import execute_tool, summarize_call

# ---------------------------------------------------------------- effort


def test_effort_ladders_are_subsequences_of_the_universal_order():
    for row in PROVIDER_ROWS:
        levels = [lvl for lvl in effort.effort_levels(row.id) if lvl]
        positions = [effort.ORDER.index(lvl) for lvl in levels]
        assert positions == sorted(positions), row.id
        default = effort.default_effort(row.id)
        assert default in effort.effort_levels(row.id), row.id


@pytest.mark.parametrize(
    ("provider", "picked", "expected"),
    [
        ("claude-api", "xhigh", "xhigh"),
        ("claude-api", "none", "low"),
        ("gemini", "xhigh", "high"),
        ("gemini", "max", "high"),
        ("openai-codex", "max", "max"),
        ("openai-codex", "none", "low"),
        ("gemini", "ultra", "high"),
        ("openai", "", ""),
        ("openai", None, ""),
        ("grok", "medium", "medium"),
        ("grok", "max", "xhigh"),
        ("unknown-provider", "high", "high"),
        ("openai", "bogus", "medium"),
    ],
)
def test_normalize_effort_folds_onto_the_nearest_offered_level(provider, picked, expected):
    assert effort.normalize_effort(provider, picked) == expected


def test_catalog_rows_carry_ladders_and_curated_models_for_cli_runners():
    claude = provider_row("claude-api")
    assert claude is not None and claude.runner == "claude-cli"
    d = claude.to_dict()
    assert d["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert d["curated_models"], "CLI-backed rows need a curated list"
    assert provider_row("openai").models_source == "live"  # type: ignore[union-attr]


# ----------------------------------------------------------------- store


def test_store_round_trips_sessions_and_events(tmp_path: Path):
    store = AgentChatStore(tmp_path / "agent_chat.db")
    s = store.create_session(provider="openai", model="gpt-5.5", effort="high", cwd=str(tmp_path))
    assert s.title == "" and s.message_count == 0

    ev = store.append_event(s.session_id, make_event("user_message", {"text": "Hallo Welt, bitte"}))
    assert ev["seq"] == 1
    delta = store.append_event(s.session_id, make_event("text_delta", {"text": "x"}))
    assert delta["seq"] == 0, "transient events are never persisted"
    store.append_event(s.session_id, make_event("assistant_text", {"text": "Hi  there"}))

    again = store.get_session(s.session_id)
    assert again is not None
    assert again.title == "Hallo Welt, bitte"
    assert again.message_count == 1
    assert again.preview == "Hi there"

    events = store.list_events(s.session_id)
    assert [e["kind"] for e in events] == ["user_message", "assistant_text"]
    assert store.list_events(s.session_id, after_seq=1)[0]["kind"] == "assistant_text"

    # The store keeps whatever id the route validated against the runner's
    # ladder (jarvis/agent_chat/permissions.py); it does not judge it.
    updated = store.update_session(s.session_id, effort="low", permission_mode="accept-edits")
    assert updated is not None and updated.effort == "low"
    assert updated.permission_mode == "accept-edits"
    assert store.list_sessions()[0].session_id == s.session_id
    assert store.delete_session(s.session_id)
    assert store.get_session(s.session_id) is None
    assert store.list_events(s.session_id) == []
    store.close()


# ----------------------------------------------------------------- tools


def _run(coro):
    return asyncio.run(coro)


def test_file_tools_read_write_edit_glob_grep(tmp_path: Path):
    out, err = _run(
        execute_tool("Write", {"file_path": "a/b.txt", "content": "one\ntwo\n"}, cwd=tmp_path)
    )
    assert not err and "Created" in out
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "one\ntwo\n"

    out, err = _run(execute_tool("Read", {"file_path": "a/b.txt"}, cwd=tmp_path))
    assert not err and "1\tone" in out and "2 lines" in out

    out, err = _run(
        execute_tool(
            "Edit",
            {"file_path": "a/b.txt", "old_string": "two", "new_string": "three"},
            cwd=tmp_path,
        )
    )
    assert not err and "1 replacement" in out

    out, err = _run(
        execute_tool(
            "Edit", {"file_path": "a/b.txt", "old_string": "zzz", "new_string": "q"}, cwd=tmp_path
        )
    )
    assert err and "not found" in out

    out, err = _run(execute_tool("Glob", {"pattern": "**/*.txt"}, cwd=tmp_path))
    assert not err and "a/b.txt" in out

    out, err = _run(execute_tool("Grep", {"pattern": "thr.e", "glob": "*.txt"}, cwd=tmp_path))
    assert not err and "a/b.txt:2:three" in out

    out, err = _run(execute_tool("Ls", {}, cwd=tmp_path))
    assert not err and "a/" in out

    out, err = _run(execute_tool("Read", {"file_path": "missing.txt"}, cwd=tmp_path))
    assert err


def test_run_command_returns_output_and_exit_code(tmp_path: Path):
    out, err = _run(execute_tool("RunCommand", {"command": "echo agent-chat-ok"}, cwd=tmp_path))
    assert not err
    assert "agent-chat-ok" in out and "[exit 0" in out


def test_unknown_tool_is_an_error_not_a_crash(tmp_path: Path):
    out, err = _run(execute_tool("Nope", {}, cwd=tmp_path))
    assert err and "Unknown tool" in out


def test_summarize_call_picks_the_human_field():
    assert summarize_call("RunCommand", {"command": "git status\n--porcelain"}) == "git status"
    assert summarize_call("Edit", {"file_path": "x.py"}) == "x.py"
    assert summarize_call("Ls", {}) == "."


# -------------------------------------------------------- history replay


def test_messages_from_events_rebuilds_rounds_and_closes_dangling_calls():
    events = [
        make_event("user_message", {"text": "hi"}),
        make_event("turn_started", {"turn_id": "t1"}),
        make_event("assistant_text", {"turn_id": "t1", "text": "let me look"}),
        make_event("tool_call", {"turn_id": "t1", "call_id": "c1", "name": "Ls", "input": {}}),
        make_event(
            "tool_result", {"turn_id": "t1", "call_id": "c1", "output": "a/\nb", "is_error": False}
        ),
        make_event("assistant_text", {"turn_id": "t1", "text": "done"}),
        make_event("turn_finished", {"turn_id": "t1", "status": "done"}),
        make_event("user_message", {"text": "again"}),
        make_event("turn_started", {"turn_id": "t2"}),
        make_event("tool_call", {"turn_id": "t2", "call_id": "c2", "name": "Ls", "input": {}}),
        make_event("turn_finished", {"turn_id": "t2", "status": "cancelled"}),
    ]
    msgs = messages_from_events(events)
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant", "user", "assistant", "tool"]
    first_assistant = msgs[1]
    assert isinstance(first_assistant.content, list)
    assert [b["type"] for b in first_assistant.content] == ["text", "tool_use"]
    assert msgs[2].tool_call_id == "c1"
    # The cancelled round's tool call got a synthetic result so the next
    # request is well-formed.
    assert msgs[-1].tool_call_id == "c2"
    assert "cancelled" in json.dumps(msgs[-1].content)


# ---------------------------------------------------------- CLI shapes


def test_claude_stream_translation_streams_then_finalizes():
    st = _ClaudeState(turn_id="t")
    lines = [
        {"type": "system", "subtype": "init", "session_id": "sess-1"},
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        },
        {
            "type": "assistant",
            "message": {"id": "m1", "content": [{"type": "thinking", "thinking": "hmm"}]},
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "he"},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "llo"},
            },
        },
        {
            "type": "assistant",
            "message": {"id": "m1", "content": [{"type": "text", "text": "hello"}]},
        },
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu1",
                        "content": "a b",
                        "is_error": False,
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": 0.01,
            "usage": {"output_tokens": 5},
        },
    ]
    kinds: list[str] = []
    for obj in lines:
        for ev in translate_claude_line(obj, st):
            kinds.append(ev["kind"])
            if ev["kind"] == "assistant_text":
                assert ev["payload"]["text"] == "hello"
                assert ev["payload"]["message_id"] == "m1"
            if ev["kind"] == "tool_call":
                assert ev["payload"]["summary"] == "ls"
    assert kinds == [
        "reasoning_delta",
        "reasoning",
        "text_delta",
        "text_delta",
        "assistant_text",
        "tool_call",
        "tool_result",
    ]
    assert st.vendor_session == "sess-1"
    assert st.cost_usd == 0.01 and st.usage == {"output_tokens": 5} and st.status == "done"


def test_codex_stream_translation_maps_items_to_tools():
    st = _CodexState(turn_id="t")
    lines = [
        {"type": "thread.started", "thread_id": "th-9"},
        {"type": "turn.started"},
        {
            "type": "item.started",
            "item": {"id": "i1", "type": "command_execution", "command": "pytest -q"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "i1",
                "type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "3 passed",
                "exit_code": 0,
            },
        },
        {"type": "item.completed", "item": {"id": "i2", "type": "reasoning", "text": "thinking"}},
        {
            "type": "item.completed",
            "item": {"id": "i3", "type": "agent_message", "text": "All green."},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
    ]
    kinds = [ev["kind"] for obj in lines for ev in translate_codex_line(obj, st)]
    assert kinds == ["tool_call", "tool_result", "reasoning", "assistant_text"]
    assert st.vendor_session == "th-9"
    assert st.usage == {"input_tokens": 10, "output_tokens": 4}
    assert st.emitted_text


def test_claude_redacted_thinking_is_announced_and_tokens_count_live():
    """Claude Code redacts thinking: the UI still learns WHEN it thinks and how many tokens flow."""
    st = _ClaudeState(turn_id="t")
    lines = [
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
        },
        # Redacted: the finished block carries no text, only that it happened.
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [{"type": "thinking", "thinking": ""}],
                "usage": {"input_tokens": 3, "output_tokens": 40},
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 3, "output_tokens": 55},
            },
        },
        {
            "type": "stream_event",
            "event": {"type": "message_start", "message": {"id": "m2"}},
        },
        {
            "type": "assistant",
            "message": {
                "id": "m2",
                "content": [{"type": "text", "text": "more"}],
                "usage": {"input_tokens": 1, "output_tokens": 10},
            },
        },
    ]
    events = [ev for obj in lines for ev in translate_claude_line(obj, st)]
    kinds = [ev["kind"] for ev in events]
    assert kinds[:3] == ["reasoning_started", "usage_delta", "reasoning"]
    # The same message re-reporting its usage replaces, a new message adds.
    usage = [ev["payload"]["usage"] for ev in events if ev["kind"] == "usage_delta"]
    assert usage[0] == {"input_tokens": 3, "output_tokens": 40}
    assert usage[1] == {"input_tokens": 3, "output_tokens": 55}
    assert usage[-1] == {"input_tokens": 4, "output_tokens": 65}
    reasoning = next(ev for ev in events if ev["kind"] == "reasoning")
    assert reasoning["payload"]["text"] == ""
    assert reasoning["payload"]["duration_ms"] is not None


def test_claude_live_usage_counts_cache_and_never_walks_back():
    """The real CLI shape: the cache carries the turn, output starts as a placeholder.

    A warm session reports two new input tokens beside fifty thousand cached
    ones, and ``output_tokens: 1`` until the ``result`` line lands. Counting
    only the two uncached fields made a 50k-token turn read as "10 tokens"
    (BUG-173).
    """
    st = _ClaudeState(turn_id="t")
    lines = [
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 30420,
                    "cache_read_input_tokens": 21355,
                    "output_tokens": 1,
                },
            },
        },
        # The same message reported again with the true output count.
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 30420,
                    "cache_read_input_tokens": 21355,
                    "output_tokens": 412,
                },
            },
        },
        # …and once more as the placeholder: it must not undo the real count.
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        },
    ]
    events = [ev for obj in lines for ev in translate_claude_line(obj, st)]
    usage = [ev["payload"]["usage"] for ev in events if ev["kind"] == "usage_delta"]
    assert usage[0] == {
        "input_tokens": 2,
        "cache_creation_input_tokens": 30420,
        "cache_read_input_tokens": 21355,
        "output_tokens": 1,
    }
    assert usage[-1]["output_tokens"] == 412
    assert usage[-1]["cache_read_input_tokens"] == 21355
    assert usage[-1]["cache_creation_input_tokens"] == 30420


def test_codex_reasoning_start_is_announced():
    st = _CodexState(turn_id="t")
    lines = [
        {"type": "item.started", "item": {"id": "r1", "type": "reasoning"}},
        {"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "plan"}},
    ]
    kinds = [ev["kind"] for obj in lines for ev in translate_codex_line(obj, st)]
    assert kinds == ["reasoning_started", "reasoning"]
