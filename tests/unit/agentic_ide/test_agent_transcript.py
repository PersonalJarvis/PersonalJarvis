"""The conversation reader: real records in, a readable conversation out.

The cases here are the ones that decided the design. Each fixture is written in
the shape the CLI actually writes on disk, because the whole point of this
module is that it reads THAT rather than a screen — a test against a tidied-up
shape would pass while the feature stayed broken.
"""

from __future__ import annotations

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


def _msg(role: str, content: object) -> dict:
    return {"type": role, "message": {"role": role, "content": content}}


class TestClaude:
    def test_reads_prose_and_tool_calls_as_one_turn(self, tmp_path: Path) -> None:
        _claude_session(
            tmp_path,
            "abc",
            [
                _msg("user", [{"type": "text", "text": "Fix the login bug"}]),
                _msg("assistant", [{"type": "thinking", "thinking": "hmm"}]),
                _msg("assistant", [{"type": "text", "text": "Looking at it now."}]),
                _msg(
                    "assistant",
                    [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "src/login.ts"},
                        }
                    ],
                ),
                _msg("assistant", [{"type": "text", "text": "Found it."}]),
            ],
        )
        turns = agent_transcript.read("claude", "abc", home=tmp_path)
        assert turns is not None
        assert [t.role for t in turns] == ["user", "assistant"]
        assert turns[0].text == "Fix the login bug"
        # One answer, not three bubbles — a CLI writes a reply as several records.
        assert turns[1].text == "Looking at it now.\n\nFound it."
        assert [(s.tool, s.target) for s in turns[1].steps] == [("Read", "src/login.ts")]
        # Reasoning is the model talking to itself, and it is not in the answer.
        assert "hmm" not in turns[1].text

    def test_tool_results_never_speak_as_the_user(self, tmp_path: Path) -> None:
        """A tool result is a USER record. Rendering it puts the agent's own
        output in the reader's voice, which is the confusion this replaces."""
        _claude_session(
            tmp_path,
            "abc",
            [
                _msg("user", [{"type": "text", "text": "go"}]),
                _msg(
                    "assistant",
                    [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
                ),
                _msg("user", [{"type": "tool_result", "content": "a.txt\nb.txt"}]),
            ],
        )
        turns = agent_transcript.read("claude", "abc", home=tmp_path)
        assert turns is not None
        assert [t.role for t in turns] == ["user", "assistant"]
        assert all("a.txt" not in t.text for t in turns)

    def test_harness_blocks_are_not_the_user_speaking(self, tmp_path: Path) -> None:
        """The CLI writes user records on the user's behalf all the time."""
        _claude_session(
            tmp_path,
            "abc",
            [
                _msg("user", "<task-notification><id>7</id></task-notification>"),
                _msg("user", "<system-reminder>be nice</system-reminder>"),
                _msg("user", "<caveat>ignore</caveat> But do fix the header."),
            ],
        )
        turns = agent_transcript.read("claude", "abc", home=tmp_path)
        assert turns is not None
        # Only the record with a sentence of the user's own in it survives, and
        # the machinery around that sentence is gone.
        assert len(turns) == 1
        assert turns[0].text == "But do fix the header."

    def test_missing_session_is_none_not_an_error(self, tmp_path: Path) -> None:
        assert agent_transcript.read("claude", "nope", home=tmp_path) is None

    def test_a_half_written_line_costs_only_that_line(self, tmp_path: Path) -> None:
        """The CLI writes while this reads; a torn last line must not lose the file."""
        path = tmp_path / "projects" / "p" / "abc.jsonl"
        _write(path, [_msg("user", [{"type": "text", "text": "hello"}])])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "assistant", "message": {"role": "assi')
        turns = agent_transcript.read("claude", "abc", home=tmp_path)
        assert turns is not None
        assert [t.text for t in turns] == ["hello"]


class TestCodex:
    def _session(self, home: Path, session_id: str, rows: list[dict]) -> None:
        _write(
            home / "sessions" / "2026" / "08" / "05" / f"rollout-2026-{session_id}.jsonl",
            rows,
        )

    def test_reads_messages_and_skips_the_harness_preamble(self, tmp_path: Path) -> None:
        self._session(
            tmp_path,
            "abc",
            [
                {"type": "session_meta", "payload": {}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", "text": "system rules"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "ship it"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": '{"command": "git status"}',
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done."}],
                    },
                },
                # `event_msg` repeats what `response_item` already carried.
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "Done."}},
            ],
        )
        turns = agent_transcript.read("codex", "abc", home=tmp_path)
        assert turns is not None
        assert [t.role for t in turns] == ["user", "assistant"]
        assert turns[0].text == "ship it"
        # Said once, not twice — the duplicate event stream is skipped.
        assert turns[1].text == "Done."
        assert [(s.tool, s.target) for s in turns[1].steps] == [("shell", "git status")]


class TestRegistry:
    def test_an_unknown_cli_degrades_rather_than_failing(self, tmp_path: Path) -> None:
        assert agent_transcript.can_read("claude") is True
        assert agent_transcript.can_read("some-new-cli") is False
        assert agent_transcript.read("some-new-cli", "abc", home=tmp_path) is None

    def test_a_long_block_keeps_both_ends(self) -> None:
        """A truncated result's END is where the error is."""
        text = "START" + ("x" * (agent_transcript.MAX_TEXT * 2)) + "END"
        clipped = agent_transcript._clip(text)
        assert clipped.startswith("START")
        assert clipped.endswith("END")
        assert len(clipped) < len(text)
