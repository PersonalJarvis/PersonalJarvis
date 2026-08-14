"""Unit tests for jarvis.missions.worker_transcript.

Covers:
- Parsing a claude-shaped stream into ordered transcript items (thinking /
  text / tool_use / tool_result / result) with tool-name correlation.
- Redaction: a credential quoted in a tool result never reaches an item.
- Honest truncation: an over-long stream keeps its tail and says what it cut.
- Archive round trip: live file -> durable copy -> load after "prune".
- Tolerance: garbage lines are skipped, an empty stream yields no transcript.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.missions import worker_transcript as wt


def _assistant_line(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _tool_result_line(tool_use_id: str, content, *, is_error: bool = False) -> str:
    return json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error,
            }]
        },
    })


_STREAM = "\n".join([
    _assistant_line(
        {"type": "thinking", "thinking": "I should list the files first."},
        {"type": "tool_use", "id": "tu_1", "name": "Bash",
         "input": {"command": "ls"}},
    ),
    "not json at all {{{",
    _tool_result_line("tu_1", "file1\nfile2"),
    _assistant_line({"type": "text", "text": "Two files found."}),
    json.dumps({"type": "result", "result": "Done: listed the workspace."}),
])


def test_parse_orders_and_correlates_items() -> None:
    items = wt.parse_transcript_items(_STREAM)
    kinds = [i["kind"] for i in items]
    assert kinds == ["thinking", "tool_use", "tool_result", "text", "result"]
    tool_use = items[1]
    assert tool_use["tool_name"] == "Bash"
    assert "ls" in tool_use["args_preview"]
    tool_result = items[2]
    # tool_result frames carry no name — it must be resolved via the id.
    assert tool_result["tool_name"] == "Bash"
    assert "file1" in tool_result["preview"]
    assert tool_result["is_error"] is False
    assert items[4]["text"] == "Done: listed the workspace."


def test_previews_are_redacted() -> None:
    secret = "sk-proj-AbCdEf0123456789ghijKLmnopQRstuv"  # noqa: S105 — fake fixture key
    stream = "\n".join([
        _assistant_line({"type": "tool_use", "id": "tu_1", "name": "Read",
                         "input": {"path": ".env"}}),
        _tool_result_line("tu_1", f"OPENAI_API_KEY={secret}"),
    ])
    items = wt.parse_transcript_items(stream)
    result = next(i for i in items if i["kind"] == "tool_result")
    assert secret not in result["preview"]
    assert "<redacted" in result["preview"]


def test_truncation_keeps_the_tail_and_is_honest() -> None:
    lines = [
        _assistant_line({"type": "text", "text": f"step {n}"})
        for n in range(wt._MAX_ITEMS + 50)
    ]
    items = wt.parse_transcript_items("\n".join(lines))
    assert items[0]["kind"] == "truncated"
    assert items[0]["dropped_items"] == 50
    assert len(items) == wt._MAX_ITEMS + 1
    # The tail wins — the newest activity is what the view opens for.
    assert items[-1]["text"] == f"step {wt._MAX_ITEMS + 49}"


def test_empty_stream_yields_no_transcript(tmp_path: Path) -> None:
    assert wt.parse_transcript_items("") == []
    assert wt.load_transcript("w-1", worktree=str(tmp_path / "nope")) is None


def _mission_layout(tmp_path: Path) -> tuple[Path, Path]:
    """mission/tasks/01__t/{workspace,logs} — the real on-disk shape."""
    task_dir = tmp_path / "mission" / "tasks" / "01__t"
    worktree = task_dir / "workspace"
    worktree.mkdir(parents=True)
    log_dir = task_dir / "logs"
    log_dir.mkdir()
    (log_dir / "stream.jsonl").write_text(_STREAM, encoding="utf-8")
    return worktree, log_dir


def test_live_then_archive_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wt, "TRANSCRIPT_DIR", tmp_path / "archive")
    worktree, log_dir = _mission_layout(tmp_path)

    live = wt.load_transcript("w-abc", worktree=str(worktree))
    assert live is not None and live["source"] == "live"
    assert [i["kind"] for i in live["items"]][0] == "thinking"

    assert wt.archive_worker_stream("w-abc", "m-1", str(worktree)) is not None

    # Simulate the prune: the whole mission dir disappears.
    (log_dir / "stream.jsonl").unlink()
    archived = wt.load_transcript("w-abc", worktree=str(worktree))
    assert archived is not None and archived["source"] == "archive"
    assert len(archived["items"]) == len(live["items"])


def test_archive_without_stream_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wt, "TRANSCRIPT_DIR", tmp_path / "archive")
    worktree = tmp_path / "mission" / "tasks" / "01__t" / "workspace"
    worktree.mkdir(parents=True)
    assert wt.archive_worker_stream("w-abc", "m-1", str(worktree)) is None
