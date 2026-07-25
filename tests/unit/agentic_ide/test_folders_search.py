"""Folder search, device naming, and the recents store.

These three make the wizard's first step usable: finding a folder by name
instead of clicking down five levels, labelling the machine the way its owner
named it, and getting back to yesterday's workspace in one click.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.agentic_ide import device, recents
from jarvis.agentic_ide.folders import search_folders


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small folder tree with a repo, a project, and noise to skip."""
    (tmp_path / "work" / "webshop").mkdir(parents=True)
    (tmp_path / "work" / "webshop" / ".git").mkdir()
    (tmp_path / "work" / "webshop-docs").mkdir()
    (tmp_path / "work" / "notes").mkdir()
    (tmp_path / "archive" / "old-webshop").mkdir(parents=True)
    (tmp_path / "archive" / "old-webshop" / "pyproject.toml").write_text("", encoding="utf-8")
    # Must never be walked into or offered.
    (tmp_path / "work" / "node_modules" / "webshop").mkdir(parents=True)
    (tmp_path / "work" / ".hidden-webshop").mkdir()
    return tmp_path


# ------------------------------------------------------------------- search
def test_search_finds_a_folder_by_partial_name(tree: Path) -> None:
    names = [e.name for e in search_folders("webshop", roots=[tree])]
    assert "webshop" in names
    assert "webshop-docs" in names
    assert "old-webshop" in names


def test_exact_match_ranks_first(tree: Path) -> None:
    hits = search_folders("webshop", roots=[tree])
    assert hits[0].name == "webshop", [h.name for h in hits]


def test_repos_rank_above_plain_folders_within_a_tier(tree: Path) -> None:
    """Two folders both starting with the query: the repo should come first."""
    hits = [h for h in search_folders("webshop", roots=[tree]) if h.name.startswith("webshop")]
    assert hits[0].is_repo is True


def test_search_skips_dependency_and_hidden_directories(tree: Path) -> None:
    paths = [e.path for e in search_folders("webshop", roots=[tree])]
    assert not any("node_modules" in p for p in paths)
    assert not any(".hidden-webshop" in p for p in paths)


def test_empty_query_returns_nothing(tree: Path) -> None:
    assert search_folders("   ", roots=[tree]) == []


def test_search_respects_the_limit(tree: Path) -> None:
    assert len(search_folders("webshop", roots=[tree], limit=1)) == 1


def test_depth_limit_stops_the_walk(tmp_path: Path) -> None:
    # NB: not named "target" — that is a Rust build directory and therefore on
    # the shared skip list, which would make this pass for the wrong reason.
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "buried"
    deep.mkdir(parents=True)
    assert search_folders("buried", roots=[tmp_path], max_depth=2) == []
    assert [h.name for h in search_folders("buried", roots=[tmp_path], max_depth=8)] == ["buried"]


def test_build_directories_are_never_offered(tmp_path: Path) -> None:
    """A folder called "target" / "dist" / "build" is an artefact, not a project."""
    for artefact in ("target", "dist", "build", "node_modules"):
        (tmp_path / artefact).mkdir()
    for artefact in ("target", "dist", "build", "node_modules"):
        assert search_folders(artefact, roots=[tmp_path]) == [], artefact


def test_unreadable_root_does_not_raise(tmp_path: Path) -> None:
    assert search_folders("anything", roots=[tmp_path / "missing"]) == []


# ------------------------------------------------------------- device name
def test_device_name_is_never_empty() -> None:
    device.reset_cache()
    assert device.device_name().strip()


def test_device_name_falls_back_to_the_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every per-OS lookup failing must still yield something readable."""
    device.reset_cache()
    monkeypatch.setattr(device, "_run", lambda argv: None)
    monkeypatch.setattr(device.platform, "node", lambda: "Rubens-MacBook-Pro.local")
    monkeypatch.delenv("COMPUTERNAME", raising=False)
    assert device.device_name() == "Rubens MacBook Pro"
    device.reset_cache()


def test_device_name_prefers_the_friendly_macos_name(monkeypatch: pytest.MonkeyPatch) -> None:
    device.reset_cache()
    monkeypatch.setattr(device.sys, "platform", "darwin")
    monkeypatch.setattr(device, "_run", lambda argv: "Ruben’s MacBook")
    assert device.device_name() == "Ruben’s MacBook"
    device.reset_cache()


# ----------------------------------------------------------------- recents
@pytest.fixture
def recents_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / "store" / "agentic_ide" / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)
    return store


def test_remember_then_load_round_trips(recents_store: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    recents.remember(str(project), terminals=3, agents={"claude": 2, "codex": 1})
    loaded = recents.load()
    assert len(loaded) == 1
    assert loaded[0].name == "proj"
    assert loaded[0].terminals == 3
    assert loaded[0].agents == {"claude": 2, "codex": 1}


def test_reopening_moves_a_workspace_to_the_front(recents_store: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    recents.remember(str(first), terminals=1, agents={"claude": 1})
    recents.remember(str(second), terminals=2, agents={"claude": 2})
    recents.remember(str(first), terminals=4, agents={"codex": 4})
    loaded = recents.load()
    assert [r.name for r in loaded] == ["one", "two"]
    assert loaded[0].terminals == 4, "the newest layout wins"


def test_vanished_folders_disappear_from_the_list(recents_store: Path, tmp_path: Path) -> None:
    gone = tmp_path / "deleted"
    gone.mkdir()
    recents.remember(str(gone), terminals=1, agents={"claude": 1})
    gone.rmdir()
    assert recents.load() == []


def test_the_list_is_capped(recents_store: Path, tmp_path: Path) -> None:
    for i in range(recents.MAX_RECENTS + 5):
        folder = tmp_path / f"p{i}"
        folder.mkdir()
        recents.remember(str(folder), terminals=1, agents={"claude": 1})
    assert len(recents.load()) == recents.MAX_RECENTS


def test_a_corrupt_store_degrades_to_empty(recents_store: Path) -> None:
    """A truncated or hand-edited file must not break the wizard."""
    recents_store.parent.mkdir(parents=True, exist_ok=True)
    recents_store.write_text("{not json at all", encoding="utf-8")
    assert recents.load() == []


def test_forget_removes_one_entry(recents_store: Path, tmp_path: Path) -> None:
    keep, drop = tmp_path / "keep", tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    recents.remember(str(keep), terminals=1, agents={"claude": 1})
    recents.remember(str(drop), terminals=1, agents={"claude": 1})
    assert recents.forget(str(drop)) is True
    assert [r.name for r in recents.load()] == ["keep"]
    assert recents.forget(str(drop)) is False


def test_the_store_is_valid_json_on_disk(recents_store: Path, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    recents.remember(str(project), terminals=1, agents={"claude": 1})
    payload = json.loads(recents_store.read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload[0]["path"].endswith("proj")
