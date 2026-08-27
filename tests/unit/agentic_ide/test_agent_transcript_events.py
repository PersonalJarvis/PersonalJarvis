"""The transcript as agent-chat events: what the chat stage folds.

Fixtures are written in the shape the CLIs actually put on disk, for the same
reason as in ``test_agent_transcript.py`` — the reader's whole point is that it
reads THAT file, so a tidied-up fixture would pass while the stage stayed blank.
The cases here are the ones the stage depends on: where a turn is cut, where
the thinking's duration comes from, that a tool result lands on its call, and
how the last turn ends depending on whether the pane is still working.
"""

from __future__ import annotations

import calendar
import json
from pathlib import Path

from jarvis.agentic_ide import agent_transcript


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _claude_session(home: Path, session_id: str, rows: list[dict]) -> None:
    _write(home / "projects" / "C--some--folder" / f"{session_id}.jsonl", rows)


def _at(seconds: float) -> str:
    """An ISO timestamp ``seconds`` into a fixed minute — what Claude Code writes."""
    whole = int(seconds)
    frac = int(round((seconds - whole) * 1000))
    return f"2026-08-26T17:15:{whole:02d}.{frac:03d}Z"


def _user(text: str, at: float, **extra: object) -> dict:
    return {
        "type": "user",
        "timestamp": _at(at),
        "uuid": f"u-{at}",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        **extra,
    }


def _assistant(block: dict, at: float, mid: str = "msg_1", **extra: object) -> dict:
    return {
        "type": "assistant",
        "timestamp": _at(at),
        "uuid": f"a-{at}",
        "message": {
            "id": mid,
            "model": "claude-opus-5",
            "role": "assistant",
            "content": [block],
            "usage": {"input_tokens": 2, "output_tokens": 40},
        },
        "effort": "xhigh",
        **extra,
    }


def _tool_result(call_id: str, text: str, at: float, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "timestamp": _at(at),
        "uuid": f"r-{at}",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": text,
                    "is_error": is_error,
                }
            ],
        },
    }


def _kinds(events: list[dict]) -> list[str]:
    return [ev["kind"] for ev in events]


class TestClaudeEvents:
    def test_one_exchange_becomes_one_turn_in_the_chat_vocabulary(self, tmp_path: Path) -> None:
        _claude_session(
            tmp_path,
            "abc",
            [
                _user("Fix the login bug", 0),
                _assistant({"type": "thinking", "thinking": "", "signature": "x"}, 1),
                _assistant({"type": "text", "text": "Looking at it now."}, 4.5),
                _assistant(
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "Read",
                        "input": {"file_path": "src/login.ts"},
                    },
                    5,
                ),
                _tool_result("call-1", "export const login = …", 7),
                _assistant({"type": "text", "text": "Found it."}, 9, mid="msg_2"),
            ],
        )
        events = agent_transcript.read_events("claude", "abc", home=tmp_path, live=False)
        assert events is not None
        # The token reports ride alongside (a transient kind — the reducer only
        # keeps the latest), so the order that matters is everything else's.
        assert [k for k in _kinds(events) if k != "usage_delta"] == [
            "user_message",
            "turn_started",
            "reasoning",
            "assistant_text",
            "tool_call",
            "tool_result",
            "assistant_text",
            "turn_finished",
        ]
        # One report per MESSAGE, not per record: msg_1 spans three records.
        assert _kinds(events).count("usage_delta") == 2
        by_kind = {ev["kind"]: ev for ev in events}
        assert by_kind["user_message"]["payload"]["text"] == "Fix the login bug"
        started = by_kind["turn_started"]["payload"]
        assert started == {
            "turn_id": "turn-1",
            "provider": "claude",
            "model": "claude-opus-5",
            "effort": "xhigh",
            "runner": "cli",
        }
        # Redacted thinking still says how long it took: the gap to the next
        # record, which is the only clock the file carries.
        reasoning = by_kind["reasoning"]["payload"]
        assert reasoning["text"] == ""
        assert reasoning["duration_ms"] == 3500
        call = by_kind["tool_call"]["payload"]
        assert (call["call_id"], call["name"], call["summary"]) == (
            "call-1",
            "Read",
            "src/login.ts",
        )
        result = by_kind["tool_result"]["payload"]
        assert result["call_id"] == "call-1"
        assert result["output"] == "export const login = …"
        assert result["duration_ms"] == 2000
        finished = by_kind["turn_finished"]["payload"]
        assert finished["status"] == "done"
        # Two messages, counted once each — not once per record of a message.
        assert finished["usage"]["output_tokens"] == 80
        assert finished["duration_ms"] == 8000
        # Timestamps are the file's, not the clock's: seq ascends, ts follows the rows.
        assert [ev["seq"] for ev in events] == list(range(1, len(events) + 1))
        assert events[0]["ts_ms"] < events[-1]["ts_ms"]

    def test_thinking_with_words_keeps_them(self, tmp_path: Path) -> None:
        _claude_session(
            tmp_path,
            "abc",
            [
                _user("go", 0),
                _assistant({"type": "thinking", "thinking": "Check the tests first."}, 1),
                _assistant({"type": "text", "text": "On it."}, 2),
            ],
        )
        events = agent_transcript.read_events("claude", "abc", home=tmp_path)
        assert events is not None
        reasoning = next(ev for ev in events if ev["kind"] == "reasoning")
        assert reasoning["payload"]["text"] == "Check the tests first."

    def test_a_working_pane_leaves_its_last_turn_running(self, tmp_path: Path) -> None:
        """Same file, two endings: the pane's state decides, not the file."""
        rows = [
            _user("go", 0),
            _assistant({"type": "text", "text": "Reading."}, 1),
            _assistant({"type": "thinking", "thinking": "", "signature": "x"}, 2),
        ]
        _claude_session(tmp_path, "abc", rows)
        idle = agent_transcript.read_events("claude", "abc", home=tmp_path, live=False)
        live = agent_transcript.read_events("claude", "abc", home=tmp_path, live=True)
        assert idle is not None and live is not None
        assert _kinds(idle)[-2:] == ["reasoning", "turn_finished"]
        # Still thinking: announced as a live thought, and the turn stays open.
        assert _kinds(live)[-1] == "reasoning_started"
        assert "turn_finished" not in _kinds(live)

    def test_a_question_not_yet_answered_opens_a_turn_only_while_working(
        self, tmp_path: Path
    ) -> None:
        _claude_session(tmp_path, "abc", [_user("go", 0)])
        idle = agent_transcript.read_events("claude", "abc", home=tmp_path, live=False)
        live = agent_transcript.read_events("claude", "abc", home=tmp_path, live=True)
        assert idle is not None and _kinds(idle) == ["user_message"]
        assert live is not None and _kinds(live) == ["user_message", "turn_started"]

    def test_turns_are_cut_at_the_persons_messages(self, tmp_path: Path) -> None:
        _claude_session(
            tmp_path,
            "abc",
            [
                _user("first", 0),
                _assistant({"type": "text", "text": "one"}, 1),
                _user("second", 2),
                _assistant({"type": "text", "text": "two"}, 3, mid="msg_2"),
            ],
        )
        events = agent_transcript.read_events("claude", "abc", home=tmp_path)
        assert events is not None
        turn_ids = [ev["payload"]["turn_id"] for ev in events if ev["kind"] == "turn_started"]
        assert turn_ids == ["turn-1", "turn-2"]
        kinds = _kinds(events)
        # The first turn is closed BEFORE the second question is recorded.
        assert kinds.index("turn_finished") < kinds.index("user_message", 1)

    def test_harness_and_sidechain_records_are_not_the_conversation(self, tmp_path: Path) -> None:
        _claude_session(
            tmp_path,
            "abc",
            [
                _user("<local-command-caveat>ignore</local-command-caveat>", 0, isMeta=True),
                _user("<task-notification>done</task-notification>", 0.5),
                _user("real question", 1),
                _assistant({"type": "text", "text": "sub-agent chatter"}, 2, isSidechain=True),
                _assistant({"type": "text", "text": "answer"}, 3),
            ],
        )
        events = agent_transcript.read_events("claude", "abc", home=tmp_path)
        assert events is not None
        users = [ev["payload"]["text"] for ev in events if ev["kind"] == "user_message"]
        assert users == ["real question"]
        texts = [ev["payload"]["text"] for ev in events if ev["kind"] == "assistant_text"]
        assert texts == ["answer"]

    def test_only_the_last_turns_are_answered(self, tmp_path: Path) -> None:
        rows: list[dict] = []
        for n in range(agent_transcript.MAX_TURNS + 5):
            rows.append(_user(f"q{n}", n * 2))
            rows.append(_assistant({"type": "text", "text": f"a{n}"}, n * 2 + 1, mid=f"m{n}"))
        _claude_session(tmp_path, "abc", rows)
        events = agent_transcript.read_events("claude", "abc", home=tmp_path)
        assert events is not None
        users = [ev["payload"]["text"] for ev in events if ev["kind"] == "user_message"]
        assert len(users) == agent_transcript.MAX_TURNS
        assert users[-1] == f"q{agent_transcript.MAX_TURNS + 4}"
        # The cut is at a question, so the first event is one — never half a turn.
        assert events[0]["kind"] == "user_message"

    def test_one_long_task_still_opens_with_its_question(self, tmp_path: Path) -> None:
        """BUG-196: megabytes of tool output after the prompt never cut the prompt off.

        A pane given one task writes its question at the head of the file and
        everything after it is the answer; a read that took a fixed tail came
        back with the answer's torso and the chat stage had nothing above it to
        scroll to. Three megabytes of tool result here — more than the tail
        that was read — and the first event is still what the person said.
        """
        haystack = "x" * 3_000_000
        _claude_session(
            tmp_path,
            "abc",
            [
                _user("fix the scroll", 0),
                _assistant({"type": "tool_use", "id": "c1", "name": "Read", "input": {}}, 1),
                _tool_result("c1", haystack, 2),
                _assistant({"type": "text", "text": "done"}, 3),
            ],
        )
        events = agent_transcript.read_events("claude", "abc", home=tmp_path)
        assert events is not None
        assert events[0]["kind"] == "user_message"
        assert events[0]["payload"]["text"] == "fix the scroll"
        texts = [ev["payload"]["text"] for ev in events if ev["kind"] == "assistant_text"]
        assert texts == ["done"]

    def test_a_file_past_the_read_bound_is_read_from_its_tail(self, tmp_path: Path) -> None:
        """The bound is the one thing that still cuts — and it cuts on a whole line.

        The seek lands mid-object; that fragment is dropped rather than parsed
        as garbage, and every line after it comes back intact.
        """
        rows = [_user(f"q{n}", n) for n in range(5)]
        _claude_session(tmp_path, "abc", rows)
        path = tmp_path / "projects" / "C--some--folder" / "abc.jsonl"
        one = len(json.dumps(rows[-1], ensure_ascii=False)) + 1
        kept = list(agent_transcript._rows(path, max_bytes=one * 2 + one // 2))
        assert [row["uuid"] for row in kept] == ["u-3", "u-4"]
        whole = list(agent_transcript._rows(path))
        assert [row["uuid"] for row in whole] == [f"u-{n}" for n in range(5)]

    def test_no_file_is_none_not_an_error(self, tmp_path: Path) -> None:
        assert agent_transcript.read_events("claude", "nope", home=tmp_path) is None
        assert agent_transcript.read_events("shell", "abc", home=tmp_path) is None


def _codex_session(home: Path, session_id: str, rows: list[dict]) -> None:
    _write(
        home
        / "sessions"
        / "2026"
        / "08"
        / "25"
        / f"rollout-2026-08-25T16-50-19-{session_id}.jsonl",
        rows,
    )


def _item(payload: dict, at: float) -> dict:
    return {"timestamp": _at(at), "type": "response_item", "payload": payload}


class TestCodexEvents:
    def test_rollout_records_become_the_same_vocabulary(self, tmp_path: Path) -> None:
        _codex_session(
            tmp_path,
            "sess-1",
            [
                {
                    "timestamp": _at(0),
                    "type": "turn_context",
                    "payload": {"turn_id": "t", "model": "gpt-5.6-terra"},
                },
                _item(
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "<skills>…</skills>"}],
                    },
                    0.1,
                ),
                _item(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "list the files"}],
                    },
                    1,
                ),
                _item(
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [{"type": "summary_text", "text": "Need a listing."}],
                    },
                    2,
                ),
                _item(
                    {
                        "type": "function_call",
                        "call_id": "fc_1",
                        "name": "shell",
                        "arguments": json.dumps({"command": ["ls"]}),
                    },
                    3,
                ),
                _item(
                    {"type": "function_call_output", "call_id": "fc_1", "output": "a.txt\nb.txt"},
                    4,
                ),
                _item(
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Two files."}],
                    },
                    5,
                ),
                {
                    "timestamp": _at(6),
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "t", "error": None},
                },
            ],
        )
        events = agent_transcript.read_events("codex", "sess-1", home=tmp_path, live=True)
        assert events is not None
        assert _kinds(events) == [
            "user_message",
            "turn_started",
            "reasoning",
            "tool_call",
            "tool_result",
            "assistant_text",
            "turn_finished",
        ]
        by_kind = {ev["kind"]: ev for ev in events}
        assert by_kind["turn_started"]["payload"]["model"] == "gpt-5.6-terra"
        assert by_kind["reasoning"]["payload"]["text"] == "Need a listing."
        assert by_kind["reasoning"]["payload"]["duration_ms"] == 1000
        assert by_kind["tool_call"]["payload"]["input"] == {"command": ["ls"]}
        assert by_kind["tool_result"]["payload"]["output"] == "a.txt\nb.txt"
        # `task_complete` ends the turn even on a pane that is still "working":
        # the CLI itself said it was done.
        assert by_kind["turn_finished"]["payload"]["status"] == "done"

    def test_a_failed_task_ends_the_turn_in_error(self, tmp_path: Path) -> None:
        _codex_session(
            tmp_path,
            "sess-2",
            [
                _item(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hi"}],
                    },
                    1,
                ),
                _item(
                    {
                        "type": "message",
                        "id": "m",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "…"}],
                    },
                    2,
                ),
                {
                    "timestamp": _at(3),
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "error": {"message": "You've hit your usage limit."},
                    },
                },
            ],
        )
        events = agent_transcript.read_events("codex", "sess-2", home=tmp_path)
        assert events is not None
        finished = events[-1]
        assert finished["kind"] == "turn_finished"
        assert finished["payload"]["status"] == "error"
        assert finished["payload"]["error"] == "You've hit your usage limit."


class TestTimestamps:
    def test_iso_and_epoch_shapes(self) -> None:
        expected = calendar.timegm((2026, 8, 26, 17, 15, 42)) * 1000 + 847
        assert agent_transcript._ts_ms("2026-08-26T17:15:42.847Z") == expected
        assert agent_transcript._ts_ms(expected / 1000) == expected
        assert agent_transcript._ts_ms(expected) == expected
        assert agent_transcript._ts_ms("not a time") is None
        assert agent_transcript._ts_ms(None) is None
        assert agent_transcript._ts_ms(True) is None
