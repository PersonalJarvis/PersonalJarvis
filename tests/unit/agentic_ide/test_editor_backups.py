"""Open tabs and unsaved editor text survive an app restart."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import editor_backups
from jarvis.agentic_ide.file_editing import EditError
from jarvis.ui.web import agentic_ide_routes as routes


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "store"
    monkeypatch.setattr(editor_backups, "_store_root", lambda: root)
    return root


def test_tabs_and_backups_round_trip(tmp_path: Path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()

    editor_backups.save_tabs(
        folder,
        [{"path": "src/a.py", "mode": "edit", "preview": False}, {"path": "b.md", "mode": "diff"}],
        "b.md",
    )
    editor_backups.save_backup(folder, "src/a.py", "x = 1\r\n", base_version="v1", encoding="utf-8")

    state = editor_backups.load_state(folder)
    assert state["tabs"] == [
        {"path": "src/a.py", "mode": "edit", "preview": False},
        {"path": "b.md", "mode": "diff", "preview": False},
    ]
    assert state["active"] == "b.md"
    assert [(b["path"], b["text"], b["base_version"]) for b in state["backups"]] == [
        ("src/a.py", "x = 1\r\n", "v1")
    ]

    editor_backups.drop_backup(folder, "src/a.py")
    editor_backups.drop_backup(folder, "src/a.py")  # a second drop is harmless
    assert editor_backups.load_state(folder)["backups"] == []


def test_each_workspace_folder_keeps_its_own_state(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    editor_backups.save_tabs(one, [{"path": "a.txt"}], None)

    assert editor_backups.load_state(two) == {"tabs": [], "active": None, "backups": []}


def test_a_damaged_backup_is_skipped_not_fatal(tmp_path: Path, store: Path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    editor_backups.save_backup(folder, "a.txt", "ok", base_version=None, encoding="utf-8")
    broken = next(store.rglob("*.json")).parent / "zzz.json"
    broken.write_text("{not json", encoding="utf-8")

    assert [b["path"] for b in editor_backups.load_state(folder)["backups"]] == ["a.txt"]


def test_paths_are_validated(tmp_path: Path) -> None:
    with pytest.raises(EditError):
        editor_backups.save_backup(tmp_path, "../x", "t", base_version=None, encoding="utf-8")
    with pytest.raises(EditError):
        editor_backups.save_tabs(tmp_path, [{"path": ".git/config"}], None)


async def test_routes_store_and_return_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    session = SimpleNamespace(folder=str(folder))
    registry = SimpleNamespace(get=lambda wid: session if wid == "w1" else None)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    await routes.put_editor_tabs(
        "w1", routes.EditorTabsRequest(tabs=[routes.EditorTabState(path="a.py")], active="a.py")
    )
    await routes.put_editor_backup(
        "w1", routes.EditorBackupRequest(path="a.py", text="draft", base_version="v1")
    )
    state = await routes.get_editor_state("w1")
    assert state["active"] == "a.py"
    assert state["backups"][0]["text"] == "draft"

    await routes.delete_editor_backup("w1", "a.py")
    assert (await routes.get_editor_state("w1"))["backups"] == []

    with pytest.raises(HTTPException) as caught:
        await routes.put_editor_backup("w1", routes.EditorBackupRequest(path="../evil", text="x"))
    assert caught.value.status_code == 400
