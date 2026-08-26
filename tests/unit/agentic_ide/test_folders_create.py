"""Making a folder from the picker, and finding a hidden one by name.

A new project has no folder to pick; a `.claude` folder is hidden from the
search. Both left the wizard with "go make it in Explorer first", which is the
detour the picker exists to remove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide.folders import create_folder, search_folders


# ------------------------------------------------------------------- create
def test_creates_one_folder_inside_an_existing_one(tmp_path: Path) -> None:
    entry, error = create_folder(tmp_path, "shop")

    assert error is None
    assert entry is not None
    assert entry.name == "shop"
    assert Path(entry.path) == tmp_path / "shop"
    assert (tmp_path / "shop").is_dir()
    assert entry.is_project is False


def test_surrounding_whitespace_is_not_part_of_the_name(tmp_path: Path) -> None:
    entry, error = create_folder(tmp_path, "  shop  ")

    assert error is None
    assert entry is not None and entry.name == "shop"


def test_a_folder_already_there_is_handed_back_not_refused(tmp_path: Path) -> None:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / ".git").mkdir()

    entry, error = create_folder(tmp_path, "shop")

    assert error is None
    assert entry is not None
    assert entry.is_repo is True


def test_a_file_of_that_name_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "shop").write_text("", encoding="utf-8")

    entry, error = create_folder(tmp_path, "shop")

    assert entry is None
    assert error is not None and "file" in error


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\\b"])
def test_refuses_names_that_are_not_one_plain_segment(tmp_path: Path, name: str) -> None:
    entry, error = create_folder(tmp_path, name)

    assert entry is None
    assert error
    assert not any(p.is_dir() for p in tmp_path.iterdir())


def test_a_missing_parent_is_reported_not_created(tmp_path: Path) -> None:
    entry, error = create_folder(tmp_path / "nowhere", "shop")

    assert entry is None
    assert error is not None and "nowhere" in error
    assert not (tmp_path / "nowhere").exists()


def test_no_parent_means_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    entry, error = create_folder(None, "shop")

    assert error is None
    assert entry is not None and Path(entry.path) == tmp_path / "shop"


# ------------------------------------------------------------------- hidden
def test_search_finds_a_hidden_folder_when_asked_for_by_its_dot_name(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / "claude-notes").mkdir()

    names = [e.name for e in search_folders(".claude", roots=[tmp_path])]

    assert names == [".claude"]


def test_search_still_hides_dot_folders_for_an_ordinary_query(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / "claude-notes").mkdir()

    names = [e.name for e in search_folders("claude", roots=[tmp_path])]

    assert names == ["claude-notes"]


def test_search_never_walks_into_a_hidden_folder(tmp_path: Path) -> None:
    (tmp_path / ".cache" / ".claude").mkdir(parents=True)

    assert search_folders(".claude", roots=[tmp_path]) == []
