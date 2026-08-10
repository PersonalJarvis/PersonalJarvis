"""The Agentic-IDE explorer exposes one safe, complete workspace level at a time."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide.folders import list_workspace_dir
from jarvis.ui.web import agentic_ide_routes as routes


def test_listing_includes_hidden_and_dependency_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".github").mkdir()
    (tmp_path / ".env.example").write_text("TOKEN=\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    listing = list_workspace_dir(tmp_path)

    assert listing.path == ""
    assert [entry.name for entry in listing.entries] == [
        ".github",
        "node_modules",
        "src",
        ".env.example",
        "README.md",
    ]
    assert [entry.is_directory for entry in listing.entries[:3]] == [True, True, True]
    assert listing.entries[-1].size == 7


def test_nested_listing_keeps_every_path_workspace_relative(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "feature"
    nested.mkdir(parents=True)
    (nested / "panel.tsx").write_text("export {};\n", encoding="utf-8")

    listing = list_workspace_dir(tmp_path, "src/feature")

    assert listing.path == "src/feature"
    assert [entry.path for entry in listing.entries] == ["src/feature/panel.tsx"]
    assert str(tmp_path) not in repr(listing)


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "../outside",
        "src/../../outside",
        r"..\outside",
        "/etc",
        "C:/Windows",
        r"C:\Windows",
        r"\\server\share",
    ],
)
def test_listing_rejects_workspace_traversal(tmp_path: Path, path: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path.parent / "outside").mkdir(exist_ok=True)

    with pytest.raises(ValueError, match="outside the open workspace"):
        list_workspace_dir(tmp_path, path)


def test_listing_uses_portable_wire_paths_for_unicode_and_spaces(tmp_path: Path) -> None:
    nested = tmp_path / "folder with space" / "unicode-Δ"
    nested.mkdir(parents=True)
    (nested / "overview-π.md").write_text("ready\n", encoding="utf-8")

    listing = list_workspace_dir(tmp_path, "folder with space/unicode-Δ")

    assert listing.path == "folder with space/unicode-Δ"
    assert [entry.path for entry in listing.entries] == [
        "folder with space/unicode-Δ/overview-π.md"
    ]
    assert all("\\" not in entry.path for entry in listing.entries)


@pytest.mark.skipif(os.name == "nt", reason="These characters are Windows separators")
def test_unix_names_that_resemble_windows_paths_remain_browsable(tmp_path: Path) -> None:
    for name in ("C:notes", r"folder\name"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "entry.txt").write_text("ready\n", encoding="utf-8")

        listing = list_workspace_dir(tmp_path, name)

        assert [entry.name for entry in listing.entries] == ["entry.txt"]


def test_symlinked_directories_are_visible_but_not_expandable(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-link-target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Directory symlinks are unavailable on this host")

    listing = list_workspace_dir(tmp_path)

    entry = next(item for item in listing.entries if item.name == "linked")
    assert entry.is_symlink is True
    assert entry.is_directory is False
    with pytest.raises(ValueError, match="outside the open workspace"):
        list_workspace_dir(tmp_path, "linked")


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


async def test_workspace_route_returns_only_relative_paths(workspace: Path) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('ready')\n", encoding="utf-8")

    response = await routes.get_workspace_files("workspace-1", "src")

    assert response.workspace_id == "workspace-1"
    assert response.root_name == "project"
    assert response.path == "src"
    assert [entry.path for entry in response.entries] == ["src/main.py"]
    assert str(workspace) not in response.model_dump_json()


async def test_workspace_route_hides_unknown_and_escaping_paths(workspace: Path) -> None:
    with pytest.raises(HTTPException) as unknown:
        await routes.get_workspace_files("gone")
    assert unknown.value.status_code == 404

    with pytest.raises(HTTPException) as traversal:
        await routes.get_workspace_files("workspace-1", "..")
    assert traversal.value.status_code == 404


async def test_workspace_file_route_streams_inline_without_exposing_host_path(
    workspace: Path,
) -> None:
    document = workspace / "docs" / "guide.pdf"
    document.parent.mkdir()
    document.write_bytes(b"%PDF-1.4\npreview")

    response = await routes.get_workspace_file("workspace-1", "docs/guide.pdf")

    assert Path(response.path) == document
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert str(workspace) not in repr(response.headers)


async def test_workspace_file_preview_extracts_text_and_office_documents(
    workspace: Path,
) -> None:
    markdown = workspace / "README.md"
    markdown.write_text("# In-app preview\n", encoding="utf-8")
    docx = workspace / "proposal.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Office text</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )

    text_preview = await routes.get_workspace_file_preview("workspace-1", "README.md")
    office_preview = await routes.get_workspace_file_preview("workspace-1", "proposal.docx")

    assert text_preview.text == "# In-app preview"
    assert text_preview.path == "README.md"
    assert str(workspace) not in text_preview.model_dump_json()
    assert office_preview.text is not None
    assert "Office text" in office_preview.text


async def test_workspace_file_preview_uses_bounded_hex_for_unknown_binary(
    workspace: Path,
) -> None:
    binary = workspace / "archive.bin"
    binary.write_bytes(bytes((0, 255, 42)))

    preview = await routes.get_workspace_file_preview("workspace-1", "archive.bin")

    assert preview.text is None
    assert preview.hex_preview == "00 FF 2A"
    assert preview.size == 3


@pytest.mark.parametrize("path", ["..", "missing.txt", "."])
async def test_workspace_file_routes_hide_unavailable_targets(
    workspace: Path, path: str
) -> None:
    with pytest.raises(HTTPException) as unavailable:
        await routes.get_workspace_file_preview("workspace-1", path)
    assert unavailable.value.status_code == 404


async def test_workspace_file_route_rejects_symlink_escape(workspace: Path) -> None:
    outside = workspace.parent / "outside-secret.txt"
    outside.write_text("not workspace content", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        os.symlink(outside, link)
    except (NotImplementedError, OSError):
        pytest.skip("File symlinks are unavailable on this host")

    with pytest.raises(HTTPException) as unavailable:
        await routes.get_workspace_file_preview("workspace-1", "linked.txt")
    assert unavailable.value.status_code == 404
