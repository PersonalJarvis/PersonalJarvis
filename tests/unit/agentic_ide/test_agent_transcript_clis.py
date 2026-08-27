"""The transcript readers of the CLIs beyond Claude Code and Codex.

Fixtures are written in the shape each CLI actually puts on disk — measured on
the maintainer's box (Grok Build, Antigravity, OpenCode, the legacy Kimi wire)
or taken from the CLI's own writer (the current Kimi wire) — for the reason the
sibling test files give: the reader's whole point is that it reads THAT file,
and a tidied-up fixture passes while the chat stage stays blank.

What the stage depends on is what is checked: a streamed message reaches the
log as one message, a call gets its result and its name, the person's words
come out of the harness's wrapping, the token report lands in the chat's own
key names, and a launch profile over another CLI's binary reads that CLI's
record.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from jarvis.agentic_ide import agent_transcript


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _kinds(events: list[dict]) -> list[str]:
    return [event["kind"] for event in events]


def _by_kind(events: list[dict], kind: str) -> list[dict]:
    return [event["payload"] for event in events if event["kind"] == kind]


# --------------------------------------------------------------------------- #
# Grok Build
# --------------------------------------------------------------------------- #


#: A real clock — the readers take a small number for seconds, as the CLIs
#: that write seconds need them to, so a fixture's milliseconds must look like
#: milliseconds.
T0 = 1_787_823_800_000


def _grok_update(update: dict, at_ms: int, method: str = "session/update") -> dict:
    at_ms += T0
    return {
        "timestamp": at_ms // 1000,
        "method": method,
        "params": {
            "sessionId": "grok-1",
            "update": update,
            "_meta": {"eventId": f"grok-1-{at_ms}", "agentTimestampMs": at_ms},
        },
    }


def _grok_session(home: Path, rows: list[dict], summary: dict | None = None) -> None:
    folder = home / "sessions" / "C%3A%5Cwork" / "grok-1"
    _write(folder / "updates.jsonl", rows)
    if summary is not None:
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _grok_rows() -> list[dict]:
    text = {"type": "text"}
    return [
        _grok_update(
            {"sessionUpdate": "hook_execution", "event_name": "session_start", "runs": []},
            1_000,
            method="_x.ai/session/update",
        ),
        _grok_update(
            {
                "sessionUpdate": "user_message_chunk",
                "content": {**text, "text": "Fix the sidebar"},
                "_meta": {"modelId": "grok-4.6", "promptIndex": 0},
            },
            2_000,
        ),
        _grok_update(
            {"sessionUpdate": "agent_thought_chunk", "content": {**text, "text": "The user "}},
            3_000,
        ),
        _grok_update(
            {"sessionUpdate": "agent_thought_chunk", "content": {**text, "text": "wants a fix."}},
            3_100,
        ),
        _grok_update(
            {"sessionUpdate": "agent_message_chunk", "content": {**text, "text": "Looking."}},
            4_000,
        ),
        _grok_update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-1",
                "title": "read_file",
                "rawInput": {"target_file": "sidebar.tsx"},
                "_meta": {"x.ai/tool": {"name": "read_file", "kind": "read"}},
            },
            5_000,
        ),
        # The refinement — a title and locations, no status — is not a result.
        _grok_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-1",
                "kind": "read",
                "title": "Read `sidebar.tsx`",
                "locations": [{"path": "sidebar.tsx"}],
                "rawInput": {"target_file": "sidebar.tsx"},
            },
            5_001,
        ),
        _grok_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-1",
                "status": "completed",
                "content": [{"type": "content", "content": {**text, "text": "export const x"}}],
                "rawOutput": {"type": "ReadFile", "Content": {"content": "export const x"}},
            },
            5_250,
        ),
        _grok_update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-2",
                "title": "list_dir",
                "rawInput": {"target_directory": "src"},
                "_meta": {"x.ai/tool": {"name": "list_dir", "kind": "read"}},
            },
            6_000,
        ),
        _grok_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-2",
                "status": "failed",
                "rawOutput": {"type": "ListDir", "NotFound": "Error: src does not exist"},
            },
            6_100,
        ),
        # A background task reports its call as completed once more when it
        # ends; the result already landed.
        _grok_update(
            {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "completed"},
            6_200,
        ),
        _grok_update(
            {"sessionUpdate": "agent_message_chunk", "content": {**text, "text": "Done."}},
            7_000,
        ),
        _grok_update(
            {
                "sessionUpdate": "turn_completed",
                "prompt_id": "p-1",
                "stop_reason": "end_turn",
                "usage": {
                    "inputTokens": 5000,
                    "outputTokens": 120,
                    "cachedReadTokens": 4000,
                    "cacheCreationTokens": 0,
                    "reasoningTokens": 80,
                    "modelCalls": 3,
                },
            },
            8_000,
            method="_x.ai/session/update",
        ),
    ]


class TestGrokBuild:
    def test_the_stream_folds_into_the_chat_events(self, tmp_path: Path) -> None:
        _grok_session(
            tmp_path, _grok_rows(), {"current_model_id": "grok-4.6", "reasoning_effort": "xhigh"}
        )
        log = agent_transcript._grok_events("grok-1", tmp_path, False)
        assert log is not None and (log.model, log.effort) == ("grok-4.6", "xhigh")
        events = agent_transcript.read_events("grok-build", "grok-1", home=tmp_path)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning",
            "assistant_text",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "assistant_text",
            "usage_delta",
            "turn_finished",
        ]
        assert _by_kind(events, "user_message")[0]["text"] == "Fix the sidebar"
        # Two chunks, one thought — timed from its first chunk to the prose.
        thought = _by_kind(events, "reasoning")[0]
        assert thought["text"] == "The user wants a fix."
        assert thought["duration_ms"] == 1_000
        calls = _by_kind(events, "tool_call")
        assert [c["name"] for c in calls] == ["read_file", "list_dir"]
        assert calls[0]["summary"] == "sidebar.tsx"
        assert calls[1]["summary"] == "src"
        results = _by_kind(events, "tool_result")
        assert results[0] == {
            "turn_id": "turn-1",
            "call_id": "call-1",
            "output": "export const x",
            "is_error": False,
            "duration_ms": 250,
        }
        assert results[1]["is_error"] is True
        assert results[1]["output"].startswith("Error: src does not exist")
        finished = _by_kind(events, "turn_finished")[0]
        assert finished["status"] == "done"
        assert finished["usage"]["output_tokens"] == 120
        assert finished["usage"]["thinking_tokens"] == 80
        assert finished["usage"]["cache_read_input_tokens"] == 4000

    def test_the_summary_and_the_opening_come_from_the_same_read(self, tmp_path: Path) -> None:
        _grok_session(tmp_path, _grok_rows())
        turns = agent_transcript.read("grok-build", "grok-1", home=tmp_path)
        assert turns is not None
        assert [(t.role, len(t.steps)) for t in turns] == [("user", 0), ("assistant", 2)]
        assert turns[1].text == "Looking.\n\nDone."
        assert turns[1].steps[0].tool == "read_file"
        assert agent_transcript.first_user_text("grok-build", "grok-1", home=tmp_path) == (
            "Fix the sidebar"
        )

    def test_a_pane_still_working_keeps_its_turn_open(self, tmp_path: Path) -> None:
        rows = _grok_rows()[:4]  # asked, and thinking
        _grok_session(tmp_path, rows)
        events = agent_transcript.read_events("grok-build", "grok-1", home=tmp_path, live=True)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning_started",
            "reasoning_delta",
        ]
        assert _by_kind(events, "reasoning_delta")[0]["text"] == "The user wants a fix."

    def test_no_file_yet_is_none_and_a_bare_hook_run_is_silence(self, tmp_path: Path) -> None:
        assert agent_transcript.read_events("grok-build", "grok-1", home=tmp_path) is None
        assert agent_transcript.read_events("grok-build", "../x", home=tmp_path) is None
        _grok_session(tmp_path, _grok_rows()[:1])
        assert agent_transcript.first_user_text("grok-build", "grok-1", home=tmp_path) == ""
        events = agent_transcript.read_events("grok-build", "grok-1", home=tmp_path)
        assert events == []


# --------------------------------------------------------------------------- #
# Antigravity
# --------------------------------------------------------------------------- #


def _agy_session(home: Path, rows: list[dict]) -> None:
    _write(
        home
        / ".gemini"
        / "antigravity-cli"
        / "brain"
        / "agy-1"
        / ".system_generated"
        / "logs"
        / "transcript.jsonl",
        rows,
    )


def _agy_step(index: int, kind: str, source: str, at: str, **fields: object) -> dict:
    return {
        "step_index": index,
        "source": source,
        "type": kind,
        "status": "DONE",
        "created_at": f"2026-08-25T17:57:{at}Z",
        **fields,
    }


class TestAntigravity:
    def test_reads_steps_in_order_and_pairs_outputs_with_calls(self, tmp_path: Path) -> None:
        _agy_session(
            tmp_path,
            [
                _agy_step(
                    0,
                    "USER_INPUT",
                    "USER_EXPLICIT",
                    "30",
                    content=(
                        "<USER_REQUEST>\nSwap the hero screenshot\n</USER_REQUEST>\n"
                        "<ADDITIONAL_METADATA>\nThe current local time is: x\n"
                        "</ADDITIONAL_METADATA>"
                    ),
                ),
                _agy_step(1, "CHECKPOINT", "SYSTEM", "30", content="{{ CHECKPOINT 0 }}"),
                _agy_step(
                    2,
                    "PLANNER_RESPONSE",
                    "MODEL",
                    "32",
                    thinking="**Examine the Image**\n\nLooking at it.",
                    tool_calls=[
                        {
                            "name": "find_by_name",
                            "args": {
                                "MaxDepth": "4",
                                "Pattern": '"*.png"',
                                "SearchDirectory": '"C:/work/src"',
                            },
                        },
                        {"name": "view_file", "args": {"AbsolutePath": '"C:/work/a.png"'}},
                    ],
                ),
                _agy_step(3, "GENERIC", "MODEL", "35", content="Found 2 results\na.png\nb.png"),
                _agy_step(4, "GENERIC", "MODEL", "36", content="The image shows a sidebar."),
                _agy_step(
                    5,
                    "ERROR_MESSAGE",
                    "SYSTEM",
                    "40",
                    content="Error: model output error, please try again",
                ),
                _agy_step(
                    6,
                    "PLANNER_RESPONSE",
                    "MODEL",
                    "45",
                    content="The screenshot was swapped.",
                ),
            ],
        )
        events = agent_transcript.read_events("antigravity", "agy-1", home=tmp_path)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning",
            "tool_call",
            "tool_call",
            "tool_result",
            "tool_result",
            "assistant_text",
            "assistant_text",
            "turn_finished",
        ]
        assert _by_kind(events, "user_message")[0]["text"] == "Swap the hero screenshot"
        calls = _by_kind(events, "tool_call")
        # JSON strings inside JSON come out as what they encode.
        assert calls[0]["input"] == {
            "MaxDepth": 4,  # "4" in the file: JSON inside JSON, decoded once
            "Pattern": "*.png",
            "SearchDirectory": "C:/work/src",
        }
        assert calls[0]["summary"] == "*.png"
        assert calls[1]["summary"] == "C:/work/a.png"
        results = _by_kind(events, "tool_result")
        assert [r["call_id"] for r in results] == [calls[0]["call_id"], calls[1]["call_id"]]
        assert results[0]["output"].startswith("Found 2 results")
        assert results[0]["duration_ms"] == 3_000
        texts = _by_kind(events, "assistant_text")
        assert texts[0]["text"].startswith("Error: model output error")
        assert texts[1]["text"] == "The screenshot was swapped."
        assert agent_transcript.first_user_text("antigravity", "agy-1", home=tmp_path) == (
            "Swap the hero screenshot"
        )

    def test_an_unknown_conversation_is_none(self, tmp_path: Path) -> None:
        assert agent_transcript.read_events("antigravity", "agy-9", home=tmp_path) is None
        assert agent_transcript.read("antigravity", "", home=tmp_path) is None


# --------------------------------------------------------------------------- #
# OpenCode
# --------------------------------------------------------------------------- #

_OPENCODE_SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT '', parent_id TEXT,
    slug TEXT NOT NULL DEFAULT '', directory TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '', time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);
CREATE TABLE message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE TABLE part (
    id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL
);
"""


def _opencode_db(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / "opencode.db")
    con.executescript(_OPENCODE_SCHEMA)
    con.execute(
        "INSERT INTO session (id, directory, time_created, time_updated) VALUES (?, ?, ?, ?)",
        ("ses_1", "C:/work", 1_000, 9_000),
    )
    con.execute(
        "INSERT INTO session (id, directory, time_created, time_updated) VALUES (?, ?, ?, ?)",
        ("ses_empty", "C:/work", 1_000, 1_000),
    )

    def _clocked(data: dict) -> dict:
        """Every timestamp in a row's body moved onto the real clock."""
        out: dict = {}
        for key, value in data.items():
            if key == "time" and isinstance(value, dict):
                out[key] = {k: v + T0 for k, v in value.items()}
            else:
                out[key] = value
        return out

    def message(mid: str, at: int, data: dict) -> None:
        con.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
            (mid, "ses_1", T0 + at, T0 + at, json.dumps(_clocked(data))),
        )

    def part(pid: str, mid: str, at: int, updated: int, data: dict) -> None:
        if isinstance(data.get("state"), dict):
            data = {**data, "state": _clocked(data["state"])}
        con.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
            (pid, mid, "ses_1", T0 + at, T0 + updated, json.dumps(_clocked(data))),
        )

    message(
        "msg_u",
        1_000,
        {
            "role": "user",
            "time": {"created": 1_000},
            "model": {"providerID": "opencode", "modelID": "muse-spark-1.2", "variant": "xhigh"},
        },
    )
    part("prt_u", "msg_u", 1_010, 1_010, {"type": "text", "text": "Link me to localhost"})
    message(
        "msg_a",
        2_000,
        {
            "parentID": "msg_u",
            "role": "assistant",
            "variant": "xhigh",
            "modelID": "muse-spark-1.2",
            "providerID": "opencode",
            "tokens": {
                "total": 300,
                "input": 200,
                "output": 60,
                "reasoning": 40,
                "cache": {"write": 0, "read": 150},
            },
            "time": {"created": 2_000, "completed": 6_000},
            "finish": "stop",
        },
    )
    part("prt_s", "msg_a", 2_100, 2_100, {"type": "step-start", "snapshot": "abc"})
    part(
        "prt_r",
        "msg_a",
        2_200,
        2_200,
        {
            "type": "reasoning",
            "text": "Localhost is local.",
            "time": {"start": 2_200, "end": 3_000},
            "metadata": {"openai": {"itemId": "rs_1"}},
        },
    )
    part(
        "prt_t",
        "msg_a",
        3_100,
        3_400,
        {
            "type": "tool",
            "tool": "read",
            "callID": "call_1",
            "state": {
                "status": "completed",
                "input": {"filePath": "C:/work/run.bat"},
                "output": "@echo off",
                "title": "C:/work/run.bat",
                "time": {"start": 3_100, "end": 3_350},
            },
        },
    )
    part(
        "prt_e",
        "msg_a",
        3_500,
        3_800,
        {
            "type": "tool",
            "tool": "bash",
            "callID": "call_2",
            "state": {
                "status": "error",
                "input": {"command": "curl 127.0.0.1"},
                "error": "connection refused",
            },
        },
    )
    part("prt_x", "msg_a", 4_000, 4_000, {"type": "text", "text": "Open http://127.0.0.1:47821"})
    part(
        "prt_f",
        "msg_a",
        4_100,
        4_100,
        {"type": "step-finish", "reason": "stop", "tokens": {"input": 200, "output": 60}},
    )
    con.commit()
    con.close()


class TestOpenCode:
    def test_messages_and_parts_become_the_chat_events(self, tmp_path: Path) -> None:
        _opencode_db(tmp_path)
        log = agent_transcript._opencode_events("ses_1", tmp_path, False)
        assert log is not None and (log.model, log.effort) == ("muse-spark-1.2", "xhigh")
        events = agent_transcript.read_events("opencode", "ses_1", home=tmp_path)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "assistant_text",
            "usage_delta",
            "turn_finished",
        ]
        assert _by_kind(events, "user_message")[0]["text"] == "Link me to localhost"
        thought = _by_kind(events, "reasoning")[0]
        assert thought["text"] == "Localhost is local."
        # Timed from its own start to the call that followed it.
        assert thought["duration_ms"] == 900
        calls = _by_kind(events, "tool_call")
        assert [(c["name"], c["summary"]) for c in calls] == [
            ("read", "C:/work/run.bat"),
            ("bash", "curl 127.0.0.1"),
        ]
        results = _by_kind(events, "tool_result")
        assert results[0]["output"] == "@echo off"
        assert results[0]["duration_ms"] == 250
        assert results[1]["is_error"] is True
        assert results[1]["output"] == "connection refused"
        # The error's end is the part's last write.
        assert results[1]["duration_ms"] == 300
        usage = _by_kind(events, "turn_finished")[0]["usage"]
        assert usage == {
            "input_tokens": 200,
            "output_tokens": 60,
            "thinking_tokens": 40,
            "cache_read_input_tokens": 150,
            "cache_creation_input_tokens": 0,
        }
        turns = agent_transcript.read("opencode", "ses_1", home=tmp_path)
        assert turns is not None
        assert [(t.role, len(t.steps)) for t in turns] == [("user", 0), ("assistant", 2)]

    def test_a_session_with_nothing_said_is_available_and_empty(self, tmp_path: Path) -> None:
        _opencode_db(tmp_path)
        events = agent_transcript.read_events("opencode", "ses_empty", home=tmp_path)
        assert events == []
        assert agent_transcript.first_user_text("opencode", "ses_empty", home=tmp_path) == ""

    def test_an_unknown_session_or_database_is_none(self, tmp_path: Path) -> None:
        assert agent_transcript.read_events("opencode", "ses_1", home=tmp_path) is None
        _opencode_db(tmp_path)
        assert agent_transcript.read_events("opencode", "ses_nope", home=tmp_path) is None


# --------------------------------------------------------------------------- #
# Kimi
# --------------------------------------------------------------------------- #


def _wire(kind: str, payload: dict, at: float) -> dict:
    return {"timestamp": at, "message": {"type": kind, "payload": payload}}


class TestKimiLegacyWire:
    def test_reads_the_wrapped_records_and_late_arguments(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "sessions" / "0123abcd" / "kimi-1" / "wire.jsonl",
            [
                {"type": "metadata", "protocol_version": "1.1"},
                _wire(
                    "TurnBegin",
                    {"user_input": [{"type": "text", "text": "Where did we stop?"}]},
                    100.0,
                ),
                _wire("StepBegin", {"n": 1}, 100.1),
                _wire(
                    "ContentPart", {"type": "think", "think": "They ask about the webhook."}, 101.0
                ),
                _wire("ContentPart", {"type": "text", "text": "Let me check the notes."}, 102.0),
                _wire(
                    "ToolCall",
                    {
                        "type": "function",
                        "id": "tool_a",
                        "function": {"name": "ReadFile", "arguments": '{"path": "NOTES.md"}'},
                    },
                    103.0,
                ),
                # A second call whose arguments stream in AFTER the first call's
                # result — measured on the legacy wire.
                _wire(
                    "ToolCall",
                    {"type": "function", "id": "tool_b", "function": {"name": "ReadFile"}},
                    103.1,
                ),
                _wire(
                    "ToolResult",
                    {
                        "tool_call_id": "tool_a",
                        "return_value": {"is_error": False, "output": "# Notes"},
                    },
                    103.5,
                ),
                _wire("ToolCallPart", {"arguments_part": '{"path": "Code.gs"}'}, 103.6),
                _wire(
                    "StatusUpdate",
                    {
                        "token_usage": {
                            "input_other": 1384,
                            "output": 184,
                            "input_cache_read": 4864,
                            "input_cache_creation": 0,
                        },
                        "message_id": "chatcmpl-1",
                    },
                    103.7,
                ),
                _wire(
                    "ToolResult",
                    {
                        "tool_call_id": "tool_b",
                        "return_value": {"is_error": True, "output": "no such file"},
                    },
                    104.0,
                ),
                _wire("ContentPart", {"type": "text", "text": "Here is the plan."}, 105.0),
            ],
        )
        events = agent_transcript.read_events("kimi", "kimi-1", home=tmp_path)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning",
            "assistant_text",
            "tool_call",
            "tool_result",
            "tool_call",
            "usage_delta",
            "tool_result",
            "assistant_text",
            "turn_finished",
        ]
        calls = _by_kind(events, "tool_call")
        assert [(c["call_id"], c["summary"]) for c in calls] == [
            ("tool_a", "NOTES.md"),
            ("tool_b", "Code.gs"),
        ]
        assert calls[0]["input"] == {"path": "NOTES.md"}
        results = _by_kind(events, "tool_result")
        assert results[0] == {
            "turn_id": "turn-1",
            "call_id": "tool_a",
            "output": "# Notes",
            "is_error": False,
            "duration_ms": 500,
        }
        assert results[1]["is_error"] is True
        assert _by_kind(events, "turn_finished")[0]["usage"] == {
            "input_tokens": 1384,
            "output_tokens": 184,
            "cache_read_input_tokens": 4864,
            "cache_creation_input_tokens": 0,
        }
        assert agent_transcript.first_user_text("kimi", "kimi-1", home=tmp_path) == (
            "Where did we stop?"
        )


class TestKimiCurrentWire:
    def test_reads_the_flat_records_without_doubling_the_prompt(self, tmp_path: Path) -> None:
        _write(
            tmp_path
            / "sessions"
            / "wd_work_1fce0659c7a0"
            / "session_k2"
            / "agents"
            / "main"
            / "wire.jsonl",
            [
                {"type": "metadata", "protocol_version": "1.4", "created_at": 1_000},
                {"type": "config.update", "profileName": "agent", "time": T0 + 1_000},
                {"type": "tools.set_active_tools", "names": ["Read"], "time": T0 + 1_000},
                # The prompt is logged twice: as the turn's input and as the
                # message it becomes. Once is enough.
                {
                    "type": "turn.prompt",
                    "input": [{"type": "text", "text": "Rename the helper"}],
                    "origin": {"kind": "user"},
                    "time": T0 + 2_000,
                },
                {
                    "type": "context.append_message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Rename the helper"}],
                        "toolCalls": [],
                        "origin": {"kind": "user"},
                    },
                    "time": T0 + 2_000,
                },
                # A reminder the harness appends in the user's name.
                {
                    "type": "context.append_message",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "<system-reminder>\nbe brief\n</system-reminder>",
                            }
                        ],
                        "toolCalls": [],
                        "origin": {"kind": "injection", "variant": "reminder"},
                    },
                    "time": T0 + 2_001,
                },
                {
                    "type": "context.append_loop_event",
                    "event": {"type": "step.begin", "uuid": "s1", "turnId": "1", "step": 1},
                    "time": T0 + 2_100,
                },
                {
                    "type": "context.append_loop_event",
                    "event": {
                        "type": "content.part",
                        "uuid": "p1",
                        "turnId": "1",
                        "step": 1,
                        "part": {"type": "think", "think": "Find its callers first."},
                    },
                    "time": T0 + 2_200,
                },
                {
                    "type": "context.append_loop_event",
                    "event": {
                        "type": "tool.call",
                        "uuid": "c1",
                        "toolCallId": "c1",
                        "name": "Grep",
                        "args": {"pattern": "helper("},
                    },
                    "time": T0 + 3_000,
                },
                {
                    "type": "context.append_loop_event",
                    "event": {
                        "type": "tool.result",
                        "parentUuid": "c1",
                        "toolCallId": "c1",
                        "result": {"output": "a.py:3", "isError": False},
                    },
                    "time": T0 + 3_200,
                },
                {
                    "type": "context.append_loop_event",
                    "event": {
                        "type": "step.end",
                        "uuid": "s1",
                        "usage": {"inputOther": 900, "output": 40, "inputCacheRead": 100},
                        "finishReason": "completed",
                    },
                    "time": T0 + 3_300,
                },
                {"type": "usage.record", "model": "kimi-k2.5", "usage": {}, "time": T0 + 3_300},
                {
                    "type": "context.append_loop_event",
                    "event": {
                        "type": "content.part",
                        "uuid": "p2",
                        "part": {"type": "text", "text": "Two callers, both renamed."},
                    },
                    "time": T0 + 4_000,
                },
            ],
        )
        log = agent_transcript._kimi_events("session_k2", tmp_path, False)
        assert log is not None and log.model == "kimi-k2.5"
        events = agent_transcript.read_events("kimi", "session_k2", home=tmp_path)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning",
            "tool_call",
            "tool_result",
            "usage_delta",
            "assistant_text",
            "turn_finished",
        ]
        assert _by_kind(events, "user_message")[0]["text"] == "Rename the helper"
        assert _by_kind(events, "reasoning")[0]["duration_ms"] == 800
        assert _by_kind(events, "tool_call")[0]["summary"] == "helper("
        assert _by_kind(events, "tool_result")[0]["duration_ms"] == 200
        assert _by_kind(events, "turn_finished")[0]["usage"] == {
            "input_tokens": 900,
            "output_tokens": 40,
            "cache_read_input_tokens": 100,
        }

    def test_a_session_that_only_opened_is_silence_not_absence(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "sessions" / "wd_work_1" / "session_k3" / "agents" / "main" / "wire.jsonl",
            [
                {"type": "metadata", "protocol_version": "1.4", "created_at": 1_000},
                {"type": "config.update", "profileName": "agent", "time": T0 + 1_000},
            ],
        )
        assert agent_transcript.first_user_text("kimi", "session_k3", home=tmp_path) == ""
        assert agent_transcript.first_user_text("kimi", "session_k4", home=tmp_path) is None


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_every_cli_with_a_record_is_readable_and_the_rest_are_not(self) -> None:
        for agent in ("claude", "codex", "grok-build", "antigravity", "opencode", "kimi"):
            assert agent_transcript.can_read(agent) is True, agent
        # DeepSeek Harness runs its chat in the browser — the pane holds no
        # conversation — and a plain shell has none.
        assert agent_transcript.can_read("deepseek-harness") is False
        assert agent_transcript.can_read("plain") is False
        assert agent_transcript.can_read("") is False

    def test_a_launch_profile_reads_through_the_binary_it_runs(self, tmp_path: Path) -> None:
        """GLM Coding Plan IS Claude Code pointed elsewhere — so is its record."""
        assert agent_transcript._agent_key("GLM ") == "claude"
        assert agent_transcript._agent_key("some-new-cli") == "some-new-cli"
        assert agent_transcript.can_read("glm") is True
        _write(
            tmp_path / "projects" / "C--work" / "abc.jsonl",
            [
                {
                    "type": "user",
                    "timestamp": "2026-08-27T10:00:00.000Z",
                    "uuid": "u1",
                    "message": {"role": "user", "content": "Port the parser"},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-08-27T10:00:05.000Z",
                    "uuid": "a1",
                    "message": {
                        "id": "m1",
                        "model": "glm-5",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "On it."}],
                    },
                },
            ],
        )
        events = agent_transcript.read_events("glm", "abc", home=tmp_path)
        assert events is not None
        assert _by_kind(events, "user_message")[0]["text"] == "Port the parser"
        assert agent_transcript.first_user_text("glm", "abc", home=tmp_path) == "Port the parser"

    def test_the_opening_is_unknown_once_the_window_is_full(self, tmp_path: Path) -> None:
        """A window on the recent past cannot name a session's first message."""
        rows: list[dict] = []
        for index in range(agent_transcript.MAX_TURNS + 1):
            rows.append(
                _grok_update(
                    {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": f"question {index}"},
                        "_meta": {"modelId": "grok-4.6", "promptIndex": index},
                    },
                    1_000 + index * 10,
                )
            )
        _grok_session(tmp_path, rows)
        assert agent_transcript.first_user_text("grok-build", "grok-1", home=tmp_path) == ""
