"""The code editor round-trips workspace files byte for byte and never clobbers an agent's edit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import file_editing
from jarvis.agentic_ide.file_editing import (
    EditConflict,
    EditError,
    TrashUnavailable,
    create_entry,
    delete_entry,
    list_files,
    read_text_file,
    rename_entry,
    write_text_file,
)
from jarvis.ui.web import agentic_ide_routes as routes


def test_read_keeps_indentation_crlf_and_bom(tmp_path: Path) -> None:
    raw = b"\xef\xbb\xbfdef f():\r\n\t\treturn  1\r\n\r\n\r\n"
    (tmp_path / "a.py").write_bytes(raw)

    loaded = read_text_file(tmp_path, "a.py")

    assert loaded.text == "def f():\r\n\t\treturn  1\r\n\r\n\r\n"
    assert loaded.encoding == "utf-8-sig"
    assert loaded.eol == "\r\n"
    assert not loaded.binary


def test_save_round_trips_bytes_and_returns_new_version(tmp_path: Path) -> None:
    raw = b"\xef\xbb\xbfline one\r\nline two\r\n"
    (tmp_path / "a.txt").write_bytes(raw)
    loaded = read_text_file(tmp_path, "a.txt")

    saved = write_text_file(
        tmp_path,
        "a.txt",
        loaded.text + "line three\r\n",
        expected_version=loaded.version,
        encoding=loaded.encoding,
    )

    assert (tmp_path / "a.txt").read_bytes() == raw + b"line three\r\n"
    assert saved.version == read_text_file(tmp_path, "a.txt").version
    assert saved.version != loaded.version


def test_save_refuses_when_the_file_changed_on_disk(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("mine\n", encoding="utf-8")
    loaded = read_text_file(tmp_path, "a.txt")
    (tmp_path / "a.txt").write_text("the agent's\n", encoding="utf-8")

    with pytest.raises(EditConflict) as caught:
        write_text_file(tmp_path, "a.txt", "mine, edited\n", expected_version=loaded.version)

    assert caught.value.current_version == read_text_file(tmp_path, "a.txt").version
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "the agent's\n"


def test_save_without_version_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(EditConflict):
        write_text_file(tmp_path, "a.txt", "gone\n", expected_version=None, create=True)

    created = write_text_file(tmp_path, "b.txt", "new\n", expected_version=None, create=True)
    assert created.path == "b.txt"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "new\n"


def test_binary_and_large_files_are_not_loaded_as_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "x.bin").write_bytes(b"\x89PNG\x00\x00data")
    (tmp_path / "big.txt").write_text("a" * 64, encoding="utf-8")
    monkeypatch.setattr(file_editing, "MAX_EDITABLE_BYTES", 32)

    assert read_text_file(tmp_path, "x.bin").binary is True
    assert read_text_file(tmp_path, "x.bin").text is None
    big = read_text_file(tmp_path, "big.txt")
    assert big.too_large is True and big.text is None


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "C:/x.txt", ".git/config", ""])
def test_paths_outside_the_workspace_or_inside_git_are_refused(tmp_path: Path, path: str) -> None:
    with pytest.raises(EditError):
        write_text_file(tmp_path, path, "x", expected_version=None, create=True)


def test_symlink_escape_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")

    with pytest.raises(EditError):
        create_entry(root, "link/evil.txt", directory=False)
    assert not (outside / "evil.txt").exists()


def test_create_rename_and_permanent_delete(tmp_path: Path) -> None:
    assert create_entry(tmp_path, "src/pkg", directory=True) == "src/pkg"
    assert create_entry(tmp_path, "src/pkg/mod.py", directory=False) == "src/pkg/mod.py"
    with pytest.raises(EditError):
        create_entry(tmp_path, "src/pkg/mod.py", directory=False)

    assert rename_entry(tmp_path, "src/pkg/mod.py", "src/pkg/main.py") == "src/pkg/main.py"
    assert (tmp_path / "src/pkg/main.py").is_file()
    with pytest.raises(EditError):
        rename_entry(tmp_path, "src", "src/pkg/inner")

    assert delete_entry(tmp_path, "src", permanent=True) is False
    assert not (tmp_path / "src").exists()


def test_delete_without_trash_asks_for_a_permanent_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(file_editing, "_move_to_trash", lambda target: False)

    with pytest.raises(TrashUnavailable):
        delete_entry(tmp_path, "a.txt")
    assert (tmp_path / "a.txt").exists()


def test_case_only_rename_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")

    assert rename_entry(tmp_path, "readme.md", "README.md") == "README.md"
    assert [entry.name for entry in tmp_path.iterdir()] == ["README.md"]


def test_list_files_walks_outside_git_and_skips_dependency_folders(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("", encoding="utf-8")

    paths, truncated = list_files(tmp_path)

    assert "src/a.py" in paths
    assert not any(path.startswith("node_modules/") for path in paths)
    assert truncated is False


def test_list_files_in_git_honours_gitignore(tmp_path: Path) -> None:
    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.js").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")

    paths, _ = list_files(tmp_path)

    assert paths == [".gitignore", "main.py"]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    session = SimpleNamespace(folder=str(root))
    registry = SimpleNamespace(
        get=lambda workspace_id: session if workspace_id == "workspace-1" else None
    )
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    return root


async def test_routes_load_save_and_report_conflicts(workspace: Path) -> None:
    (workspace / "main.py").write_bytes(b"x = 1\r\n")

    loaded = await routes.get_workspace_text_file("workspace-1", "main.py")
    assert loaded["text"] == "x = 1\r\n"
    assert loaded["eol"] == "\r\n"
    routes.WorkspaceTextFileResponse(**loaded)

    saved = await routes.save_workspace_text_file(
        "workspace-1",
        routes.SaveWorkspaceFileRequest(
            path="main.py", text="x = 2\r\n", expected_version=loaded["version"]
        ),
    )
    assert (workspace / "main.py").read_bytes() == b"x = 2\r\n"

    with pytest.raises(HTTPException) as caught:
        await routes.save_workspace_text_file(
            "workspace-1",
            routes.SaveWorkspaceFileRequest(
                path="main.py", text="stale\n", expected_version=loaded["version"]
            ),
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["current_version"] == saved["version"]


async def test_routes_reject_unknown_workspace_and_escapes(workspace: Path) -> None:
    with pytest.raises(HTTPException) as unknown:
        await routes.get_workspace_text_file("nope", "main.py")
    assert unknown.value.status_code == 404

    with pytest.raises(HTTPException) as escape:
        await routes.create_workspace_entry(
            "workspace-1", routes.WorkspaceEntryRequest(path="../evil.txt")
        )
    assert escape.value.status_code == 409
    assert not (workspace.parent / "evil.txt").exists()


async def test_delete_route_reports_missing_trash(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(file_editing, "_move_to_trash", lambda target: False)

    with pytest.raises(HTTPException) as caught:
        await routes.delete_workspace_entry(
            "workspace-1", routes.DeleteWorkspaceEntryRequest(path="a.txt")
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["trash_unavailable"] is True

    answer = await routes.delete_workspace_entry(
        "workspace-1", routes.DeleteWorkspaceEntryRequest(path="a.txt", permanent=True)
    )
    assert answer["trashed"] is False
    assert not (workspace / "a.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_save_keeps_the_file_mode(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    script.chmod(0o755)
    loaded = read_text_file(tmp_path, "run.sh")

    write_text_file(tmp_path, "run.sh", "echo bye\n", expected_version=loaded.version)

    assert script.stat().st_mode & 0o777 == 0o755
