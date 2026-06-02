"""Regression tests for out-of-worktree deliverable verification.

Root cause of mission_019e7abd (2026-05-30, live voice failure): the worker
wrote the requested HTML file to an absolute path OUTSIDE its git worktree
(`<USER_HOME>\\Desktop\\M\\hello.html`, exactly as the task
demanded). `_capture_diff` is worktree-scoped, so the captured diff was empty;
the Critic's GROUND-TRUTH-RULE then deterministically failed the mission 3×
with `critic_loop_exhausted` even though the file existed and was correct.

`Kontrollierer._augment_diff_with_external_writes` closes the gap: it pairs the
worker's real Write/Edit tool calls (from the stream) with an on-disk existence
check and appends the verified file content to the diff the Critic reviews — so
a genuine external deliverable is no longer invisible. Hallucinated writes (no
tool call, or no file on disk) are still excluded, preserving the empty-diff
veto's anti-hallucination intent.

These tests use a real on-disk git worktree plus a sibling external directory
(outside the worktree) so the worktree-containment logic is exercised for real.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from jarvis.missions.kontrollierer.orchestrator import (
    Kontrollierer,
    _real_diff_is_empty,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=15.0,
    )


@pytest.fixture
def worktree(tmp_path: Path):
    """A fresh git worktree branched off `main`, cleaned up after the test."""
    branch = f"test/external-write-{uuid.uuid4().hex[:8]}"
    wt = tmp_path / "wt"
    add = _git("worktree", "add", "-b", branch, str(wt), "main", cwd=PROJECT_ROOT)
    if add.returncode != 0:
        pytest.skip(f"git worktree add failed: {add.stderr.strip()[:200]}")
    try:
        yield wt
    finally:
        _git("worktree", "remove", "--force", str(wt), cwd=PROJECT_ROOT)
        _git("branch", "-D", branch, cwd=PROJECT_ROOT)


@pytest.fixture
def kontrollierer() -> Kontrollierer:
    """Bare instance — the helper only needs `self` for method binding."""
    return object.__new__(Kontrollierer)


def _stream(lines: list[dict]) -> str:
    return "\n".join(json.dumps(line) for line in lines)


def _write_stream(path: str, content: str, *, errored: bool = False) -> str:
    """A minimal worker stream with one Write tool_use + its result."""
    result_block: dict = {
        "type": "tool_result",
        "tool_use_id": "tu1",
        "content": [{"type": "text", "text": (
            "<tool_use_error>nope</tool_use_error>" if errored
            else "File created successfully."
        )}],
    }
    if errored:
        result_block["is_error"] = True
    return _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": "Write",
             "input": {"file_path": path, "content": content}}]}},
        {"type": "user", "message": {"content": [result_block]}},
        {"type": "result", "result": "done", "subtype": "success"},
    ])


def test_external_file_written_outside_worktree_is_credited(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """The exact mission_019e7abd shape: file on the user's Desktop, written by
    a real Write tool call, must appear in the augmented diff with its content."""
    ext = tmp_path / "M" / "hello.html"
    ext.parent.mkdir(parents=True)
    ext.write_text("<h1>Hello, World!</h1>", encoding="utf-8")
    stream = _write_stream(str(ext), "<h1>Hello, World!</h1>")

    diff = kontrollierer._augment_diff_with_external_writes("", stream, worktree)

    assert diff.strip(), "expected a non-empty diff after crediting the external file"
    assert "hello.html" in diff
    assert "<h1>Hello, World!</h1>" in diff, "verified file content must be present"


def test_augmented_diff_is_not_seen_as_empty(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """The whole point: `_real_diff_is_empty` must return False so the Critic's
    GROUND-TRUTH-RULE no longer vetoes a genuine external deliverable."""
    ext = tmp_path / "out" / "deliverable.txt"
    ext.parent.mkdir(parents=True)
    ext.write_text("real content here\n", encoding="utf-8")
    stream = _write_stream(str(ext), "real content here\n")

    diff = kontrollierer._augment_diff_with_external_writes("", stream, worktree)

    assert _real_diff_is_empty(diff) is False


def test_write_inside_worktree_is_not_appended(
    worktree: Path, kontrollierer: Kontrollierer
) -> None:
    """In-worktree writes are already captured by `_capture_diff` — the augment
    must NOT double-report them (would duplicate content for the Critic)."""
    inside = worktree / "in.html"
    inside.write_text("<p>inside</p>", encoding="utf-8")
    stream = _write_stream(str(inside), "<p>inside</p>")

    diff = kontrollierer._augment_diff_with_external_writes("", stream, worktree)

    assert diff == "", f"in-worktree write must not be appended, got: {diff!r}"


def test_nonexistent_external_file_is_not_credited(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """Anti-hallucination: a write tool_use whose file does NOT exist on disk
    must yield nothing — the empty-diff veto must still bite."""
    ghost = tmp_path / "ghost" / "missing.html"  # never created
    stream = _write_stream(str(ghost), "<h1>ghost</h1>")

    diff = kontrollierer._augment_diff_with_external_writes("", stream, worktree)

    assert diff == "", f"nonexistent external file must not be credited: {diff!r}"


def test_errored_external_write_is_not_credited(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """A write that errored (e.g. `File has not been read yet`) is not a write
    — even if a stale file happens to exist at the path, it must not be credited
    on the strength of a failed tool call."""
    ext = tmp_path / "stale" / "old.txt"
    ext.parent.mkdir(parents=True)
    ext.write_text("pre-existing unrelated content\n", encoding="utf-8")
    stream = _write_stream(str(ext), "new", errored=True)

    diff = kontrollierer._augment_diff_with_external_writes("", stream, worktree)

    assert diff == "", f"errored write must not be credited: {diff!r}"


def test_empty_external_file_is_marked(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """A 0-byte external file (a touch-only deliverable) is still credited, but
    the block must label it `empty file` so the Critic does not mistake the
    missing `+` content for a parse glitch."""
    ext = tmp_path / "empty" / "touched.txt"
    ext.parent.mkdir(parents=True)
    ext.write_text("", encoding="utf-8")  # 0 bytes
    stream = _write_stream(str(ext), "")

    diff = kontrollierer._augment_diff_with_external_writes("", stream, worktree)

    assert "touched.txt" in diff
    assert "empty file" in diff.lower()
    assert _real_diff_is_empty(diff) is False


def test_existing_diff_is_preserved(
    worktree: Path, kontrollierer: Kontrollierer, tmp_path: Path
) -> None:
    """When the worktree diff is non-empty, the external block is appended, not
    substituted — both the in-worktree changes and the external file survive."""
    ext = tmp_path / "extra.txt"
    ext.write_text("data\n", encoding="utf-8")
    stream = _write_stream(str(ext), "data\n")
    base = "diff --git a/x b/x\nnew file mode 100644\n--- /dev/null\n+++ b/x\n+hello\n"

    diff = kontrollierer._augment_diff_with_external_writes(base, stream, worktree)

    assert "diff --git a/x b/x" in diff, "original worktree diff must be preserved"
    assert "extra.txt" in diff, "external file must be appended"
