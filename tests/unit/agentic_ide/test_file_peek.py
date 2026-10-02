"""The glance into the top files before a brief is written."""

from __future__ import annotations

from pathlib import Path

from jarvis.agentic_ide.file_peek import peek, plan_for
from jarvis.agentic_ide.task_kind import KIND_IMPLEMENT, KIND_INVESTIGATE, KIND_NEUTRAL


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_content_decides_between_files_the_names_cannot_separate(tmp_path: Path) -> None:
    """The file that actually holds the behaviour beats a better-named one."""
    _write(tmp_path, "wake/settings.py", "COLOR = 'blue'\n")
    _write(
        tmp_path,
        "wake/gate.py",
        "WAKE_TIMEOUT_S = 2.5\n\n\ndef wake_timeout_expired(now: float) -> bool:\n"
        "    return now > WAKE_TIMEOUT_S\n",
    )

    result = peek(str(tmp_path), "the wake timeout", ["wake/settings.py", "wake/gate.py"], "")

    assert result.ranked[0] == "wake/gate.py"
    assert "def wake_timeout_expired(now: float) -> bool" in result.excerpts["wake/gate.py"]
    assert "WAKE_TIMEOUT_S = 2.5" in result.excerpts["wake/gate.py"]


def test_excerpts_quote_numbered_task_lines_and_skip_imports(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "from wake import timeout\n\n\ndef run():\n    limit = timeout * 2  # wake budget\n",
    )

    excerpt = peek(str(tmp_path), "wake timeout", ["a.py"], KIND_INVESTIGATE).excerpts["a.py"]

    assert "L5: limit = timeout * 2  # wake budget" in excerpt
    assert "from wake import" not in excerpt


def test_depth_follows_the_task_kind() -> None:
    deep, build, glance = (
        plan_for(KIND_INVESTIGATE),
        plan_for(KIND_IMPLEMENT),
        plan_for(KIND_NEUTRAL),
    )
    assert deep.files > build.files > glance.files
    assert deep.total_chars > build.total_chars > glance.total_chars
    assert plan_for("unknown") == glance


def test_only_the_top_files_are_excerpted_within_the_total_budget(tmp_path: Path) -> None:
    rels = [f"m{i}.py" for i in range(8)]
    for rel in rels:
        _write(tmp_path, rel, "def ranking():\n    pass\n" * 200)

    result = peek(str(tmp_path), "ranking", rels, KIND_NEUTRAL)
    plan = plan_for(KIND_NEUTRAL)

    assert len(result.excerpts) <= plan.files
    assert sum(len(text) for text in result.excerpts.values()) <= plan.total_chars
    assert len(result.ranked) == min(len(rels), plan.pool)


def test_nothing_outside_the_workspace_and_no_crash_on_missing_files(tmp_path: Path) -> None:
    (tmp_path.parent / "secret.py").write_text("password = 'x'\n", encoding="utf-8")

    result = peek(str(tmp_path), "password", ["../secret.py", "missing.py"], KIND_INVESTIGATE)

    assert result.excerpts == {}
    assert result.ranked == ["../secret.py", "missing.py"]


def test_an_expired_deadline_costs_the_excerpts_not_the_order(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def a(): ...\n")

    result = peek(str(tmp_path), "a", ["a.py"], KIND_IMPLEMENT, deadline_s=0.0)

    assert result.excerpts == {}
    assert result.ranked == ["a.py"]
