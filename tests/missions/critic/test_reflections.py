"""Tests for ReflectionMemory (append + last_n + render + path-resolution)."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.missions.critic.reflections import (
    DEFAULT_LAST_N,
    REFLECTIONS_FILENAME,
    Reflection,
    ReflectionMemory,
    reflections_path_for_mission,
    reflections_path_for_worker,
)


# --- Path-Resolution ---


def test_reflections_path_for_mission_is_root_md(tmp_path: Path) -> None:
    p = reflections_path_for_mission(tmp_path)
    assert p == tmp_path / REFLECTIONS_FILENAME
    assert p.name == "reflections.md"


def test_reflections_path_for_worker_walks_up_three(tmp_path: Path) -> None:
    """Worker-cwd: <mission>/tasks/<NN>__<slug>/workspace/.

    Reflections: <mission>/reflections.md.
    Aufstieg: workspace -> task-dir -> tasks -> mission-root.
    """
    mission = tmp_path / "mission_xyz"
    workspace = mission / "tasks" / "01__refactor" / "workspace"
    workspace.mkdir(parents=True)
    expected = mission / "reflections.md"
    actual = reflections_path_for_worker(workspace)
    assert actual == expected.resolve()


# --- append + last_n Roundtrip ---


def test_append_creates_file(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "first reflection", ["src/x.py:1"])
    assert mem.path.exists()


def test_append_then_last_n_roundtrip(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "summary zero", ["src/a.py:1", "log_line:42"])
    mem.append(1, "summary one", ["test:test_x"])

    last = mem.last_n(2)
    assert len(last) == 2
    assert last[0].iteration == 0
    assert last[0].summary == "summary zero"
    assert "src/a.py:1" in last[0].evidence
    assert last[1].iteration == 1
    assert last[1].summary == "summary one"
    assert last[1].evidence == ["test:test_x"]


def test_last_n_caps_at_available(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "only one", ["e"])
    last = mem.last_n(5)
    assert len(last) == 1


def test_last_n_returns_last_n_when_more_present(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    for i in range(5):
        mem.append(i, f"summary {i}", [f"e{i}"])
    last = mem.last_n(3)
    assert len(last) == 3
    # most recent LAST in the list (chronologically ascending)
    assert [r.iteration for r in last] == [2, 3, 4]


def test_last_n_zero_returns_empty(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "x", ["e"])
    assert mem.last_n(0) == []


def test_last_n_on_missing_file_returns_empty(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path / "nonexistent_subdir")
    assert mem.last_n() == []


def test_last_n_on_empty_file_returns_empty(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.path.write_text("", encoding="utf-8")
    assert mem.last_n() == []


def test_default_last_n_is_three() -> None:
    assert DEFAULT_LAST_N == 3


# --- render_for_worker_prompt ---


def test_render_empty_memory_returns_empty(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    assert mem.render_for_worker_prompt() == ""


def test_render_includes_prior_critic_feedback_label(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "fix the empty-string edge case", ["src/p.py:7"])
    out = mem.render_for_worker_prompt()
    assert "Prior Critic Feedback" in out
    assert "fix the empty-string edge case" in out
    assert "src/p.py:7" in out


def test_render_includes_iteration_marker(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(2, "iteration 2 summary", [])
    out = mem.render_for_worker_prompt()
    assert "[Iteration 2]" in out


def test_render_caps_to_n(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    for i in range(5):
        mem.append(i, f"sum {i}", [])
    out = mem.render_for_worker_prompt(n=2)
    # nur die letzten 2 (3 + 4)
    assert "[Iteration 3]" in out
    assert "[Iteration 4]" in out
    assert "[Iteration 0]" not in out


# --- Markdown-Format Sanity ---


def test_markdown_format_has_iteration_header(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(1, "test summary", ["src/x.py:1"])
    text = mem.path.read_text(encoding="utf-8")
    assert "## Iteration 1" in text
    assert "**Summary:** test summary" in text
    assert "- src/x.py:1" in text


def test_evidence_omitted_when_empty(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "no evidence here", [])
    text = mem.path.read_text(encoding="utf-8")
    # Evidence header NOT present when the list is empty
    assert "**Evidence:**" not in text
    assert "**Summary:** no evidence here" in text


def test_iso_timestamp_included(tmp_path: Path) -> None:
    mem = ReflectionMemory(tmp_path)
    mem.append(0, "x", [])
    text = mem.path.read_text(encoding="utf-8")
    # ISO date should have a 4-digit year
    assert "20" in text  # 20XX-Jahr
    assert "T" in text   # ISO-T-Trenner


def test_reflection_model_is_frozen() -> None:
    r = Reflection(iteration=0, ts_iso="2026-01-01T00:00:00", summary="x", evidence=[])
    with pytest.raises(Exception):  # noqa: B017
        r.iteration = 99  # type: ignore[misc]
