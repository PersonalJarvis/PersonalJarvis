"""The explorer's git view: which files changed, and how, inside one workspace."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.agentic_ide import git_changes

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "app" / "gone.py").write_text("bye\n", encoding="utf-8")
    (tmp_path / "top.txt").write_text("root\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_reports_modified_deleted_and_untracked_with_line_counts(repo: Path) -> None:
    (repo / "app" / "main.py").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
    (repo / "app" / "gone.py").unlink()
    (repo / "app" / "new.py").write_text("a\nb\n", encoding="utf-8")

    changes = git_changes.workspace_changes(repo)

    assert changes.available
    by_path = {item.path: item for item in changes.files}
    assert by_path["app/main.py"].status == "modified"
    assert (by_path["app/main.py"].added, by_path["app/main.py"].removed) == (2, 1)
    assert by_path["app/gone.py"].status == "deleted"
    assert by_path["app/new.py"].status == "untracked"
    assert by_path["app/new.py"].added == 2


def test_a_sub_folder_workspace_sees_only_its_own_changes_relative_to_itself(repo: Path) -> None:
    (repo / "app" / "main.py").write_text("changed\n", encoding="utf-8")
    (repo / "top.txt").write_text("changed\n", encoding="utf-8")

    changes = git_changes.workspace_changes(repo / "app")

    assert [item.path for item in changes.files] == ["main.py"]


def test_the_diff_marks_removed_and_added_lines_with_line_numbers(repo: Path) -> None:
    (repo / "app" / "main.py").write_text("one\nTWO\nthree\n", encoding="utf-8")

    diff = git_changes.file_diff(repo, "app/main.py")

    assert diff.status == "modified"
    assert (diff.added, diff.removed) == (1, 1)
    lines = [(line.kind, line.old_no, line.new_no, line.text) for line in diff.hunks[0].lines]
    assert ("del", 2, None, "two") in lines
    assert ("add", None, 2, "TWO") in lines
    assert ("ctx", 1, 1, "one") in lines


def test_an_untracked_file_is_all_new_and_an_absolute_path_is_accepted(repo: Path) -> None:
    (repo / "fresh.md").write_text("# Title\nbody\n", encoding="utf-8")

    diff = git_changes.file_diff(repo, str(repo / "fresh.md"))

    assert diff.status == "untracked"
    assert diff.added == 2
    assert [line.kind for line in diff.hunks[0].lines] == ["add", "add"]


def test_a_deleted_file_still_has_a_diff(repo: Path) -> None:
    (repo / "app" / "gone.py").unlink()

    diff = git_changes.file_diff(repo, "app/gone.py")

    assert diff.status == "deleted"
    assert diff.removed == 1


def test_paths_outside_the_workspace_are_refused(repo: Path) -> None:
    with pytest.raises(ValueError):
        git_changes.file_diff(repo / "app", "../top.txt")
    with pytest.raises(ValueError):
        git_changes.normalize_workspace_path(repo / "app", str(repo / "top.txt"))


def test_a_dotfile_keeps_its_leading_dot(repo: Path) -> None:
    assert git_changes.normalize_workspace_path(repo, "./.env.example") == ".env.example"


def test_a_plain_folder_is_reported_unavailable_not_raised(tmp_path: Path) -> None:
    changes = git_changes.workspace_changes(tmp_path)

    assert not changes.available
    assert changes.reason
