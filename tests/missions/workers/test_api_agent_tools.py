"""Tests for the worktree-scoped tools of the in-process API-agent worker."""
from __future__ import annotations

from pathlib import Path

from jarvis.missions.workers.api_agent_tools import (
    WORKER_TOOL_SPECS,
    execute_worker_tool,
    tool_writes_file,
)


def test_specs_cover_the_core_tools() -> None:
    names = {t["name"] for t in WORKER_TOOL_SPECS}
    assert names == {"Write", "Read", "Edit", "Bash", "Ls"}
    # every spec is OpenAI/Anthropic-translatable
    for t in WORKER_TOOL_SPECS:
        assert t["description"]
        assert t["input_schema"]["type"] == "object"


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Write", {"file_path": "a/b.txt", "content": "hi"}, worktree=tmp_path)
    assert err is False
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hi"
    out, err = execute_worker_tool("Read", {"file_path": "a/b.txt"}, worktree=tmp_path)
    assert err is False and out == "hi"


def test_edit_replaces_first_occurrence(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("foo bar foo", encoding="utf-8")
    out, err = execute_worker_tool(
        "Edit", {"file_path": "f.txt", "old_string": "foo", "new_string": "X"}, worktree=tmp_path
    )
    assert err is False
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "X bar foo"


def test_edit_missing_old_string_is_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("abc", encoding="utf-8")
    out, err = execute_worker_tool(
        "Edit", {"file_path": "f.txt", "old_string": "zzz", "new_string": "X"}, worktree=tmp_path
    )
    assert err is True
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "abc"  # unchanged


def test_read_missing_file_is_error(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Read", {"file_path": "nope.txt"}, worktree=tmp_path)
    assert err is True


def test_ls_lists_entries(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("1", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out, err = execute_worker_tool("Ls", {}, worktree=tmp_path)
    assert err is False
    assert "x.txt" in out and "sub/" in out


def test_bash_runs_in_worktree(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Bash", {"command": "echo hello-from-bash"}, worktree=tmp_path)
    assert err is False
    assert "hello-from-bash" in out


def test_bash_nonzero_exit_is_error(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Bash", {"command": "exit 3"}, worktree=tmp_path)
    assert err is True
    assert "exit 3" in out


def test_path_escape_is_rejected_on_write(tmp_path: Path) -> None:
    """The model must never write outside the worktree (../ escape)."""
    out, err = execute_worker_tool(
        "Write", {"file_path": "../escape.txt", "content": "x"}, worktree=tmp_path
    )
    assert err is True
    assert "escape" in out.lower()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_absolute_path_outside_worktree_rejected(tmp_path: Path) -> None:
    out, err = execute_worker_tool(
        "Write", {"file_path": str(tmp_path.parent / "x.txt"), "content": "x"}, worktree=tmp_path
    )
    assert err is True


def test_unknown_tool_is_error(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Frobnicate", {}, worktree=tmp_path)
    assert err is True


def test_tool_writes_file_predicate() -> None:
    assert tool_writes_file("Write") and tool_writes_file("Edit")
    assert not tool_writes_file("Read") and not tool_writes_file("Bash")
