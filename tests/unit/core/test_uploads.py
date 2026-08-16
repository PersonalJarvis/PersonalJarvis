"""``jarvis.core.uploads`` — the one place that decides what an upload may be.

Every path in an upload comes from the browser, so the interesting cases are
the hostile ones: a crafted archive entry that walks out of the staging
folder, a zip bomb, a drop of an entire home directory. The staging call has
to refuse those on the archive's own headers, before a byte lands on disk.

The friendly cases matter just as much: dropping ``my-skill/`` and dropping
its three files must produce the same result, and the OS clutter that a
Finder or Explorer drag carries along has to disappear *visibly* rather than
silently.
"""
from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from jarvis.core import uploads


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    # Deflated, like every archive a real tool produces — and the only way the
    # zip-bomb fixture below can stay small on the wire while declaring a huge
    # uncompressed size.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            archive.writestr(path, data)
    return buffer.getvalue()


def _targz_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, data in entries.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# ----------------------------------------------------------------------
# Path normalisation
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SKILL.md", "SKILL.md"),
        ("./SKILL.md", "SKILL.md"),
        ("/SKILL.md", "SKILL.md"),
        ("my-skill\\references\\guide.md", "my-skill/references/guide.md"),
        ("my-skill//references///guide.md", "my-skill/references/guide.md"),
        ("  SKILL.md  ", "SKILL.md"),
    ],
)
def test_paths_are_normalised_to_posix_relatives(raw: str, expected: str) -> None:
    assert uploads.normalize_upload_path(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "my-skill/", "./"])
def test_entries_without_a_usable_path_are_skipped(raw: str) -> None:
    assert uploads.normalize_upload_path(raw) == ""


@pytest.mark.parametrize(
    "raw",
    [
        "../evil.md",
        "my-skill/../../evil.md",
        "..\\..\\evil.md",
        "C:/Users/Public/evil.md",
        "C:\\Windows\\system32\\evil.md",
    ],
)
def test_a_path_that_leaves_the_folder_is_refused(raw: str) -> None:
    with pytest.raises(uploads.UploadRejected):
        uploads.normalize_upload_path(raw)


# ----------------------------------------------------------------------
# Metadata filtering and root stripping
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        "my-skill/.git/HEAD",
        "__MACOSX/._SKILL.md",
        ".DS_Store",
        "references/.DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "._SKILL.md",
    ],
)
def test_os_clutter_is_recognised(path: str) -> None:
    assert uploads.is_local_metadata_path(path)


@pytest.mark.parametrize("path", ["SKILL.md", "references/guide.md", ".clawignore"])
def test_real_files_are_not_clutter(path: str) -> None:
    assert not uploads.is_local_metadata_path(path)


def test_a_shared_top_level_folder_is_stripped() -> None:
    stripped, root = uploads.strip_shared_root(
        ["my-skill/SKILL.md", "my-skill/references/guide.md"]
    )
    assert stripped == ["SKILL.md", "references/guide.md"]
    assert root == "my-skill"


def test_two_top_level_folders_are_kept_as_they_are() -> None:
    paths = ["a/SKILL.md", "b/SKILL.md"]
    stripped, root = uploads.strip_shared_root(paths)
    assert stripped == paths
    assert root is None


def test_a_file_at_the_root_stops_stripping() -> None:
    paths = ["SKILL.md", "references/guide.md"]
    stripped, root = uploads.strip_shared_root(paths)
    assert stripped == paths
    assert root is None


# ----------------------------------------------------------------------
# Staging a folder drop
# ----------------------------------------------------------------------

def test_a_folder_drop_lands_on_disk_without_its_wrapper(tmp_path: Path) -> None:
    staged = uploads.stage_upload(
        [
            ("my-skill/SKILL.md", b"---\nname: x\n---\n"),
            ("my-skill/references/guide.md", b"ref"),
        ],
        tmp_path / "staged",
    )

    assert staged.stripped_root == "my-skill"
    assert staged.files == ("SKILL.md", "references/guide.md")
    assert (staged.root / "SKILL.md").read_bytes() == b"---\nname: x\n---\n"
    assert (staged.root / "references" / "guide.md").read_bytes() == b"ref"
    assert staged.total_bytes == len(b"---\nname: x\n---\n") + len(b"ref")


def test_dropping_the_contents_gives_the_same_result(tmp_path: Path) -> None:
    """The wrapper folder must not change what gets installed."""
    wrapped = uploads.stage_upload(
        [("my-skill/SKILL.md", b"body"), ("my-skill/scripts/run.py", b"code")],
        tmp_path / "wrapped",
    )
    flat = uploads.stage_upload(
        [("SKILL.md", b"body"), ("scripts/run.py", b"code")],
        tmp_path / "flat",
    )
    assert wrapped.files == flat.files


def test_clutter_is_dropped_and_reported(tmp_path: Path) -> None:
    staged = uploads.stage_upload(
        [
            ("SKILL.md", b"body"),
            (".DS_Store", b"junk"),
            (".git/config", b"junk"),
            ("__MACOSX/._SKILL.md", b"junk"),
        ],
        tmp_path / "staged",
    )

    assert staged.files == ("SKILL.md",)
    assert set(staged.ignored) == {".DS_Store", ".git/config", "__MACOSX/._SKILL.md"}
    assert not (staged.root / ".git").exists()


def test_ignored_paths_speak_the_same_language_as_kept_ones(tmp_path: Path) -> None:
    """Both lists are shown side by side — they must use the same paths."""
    staged = uploads.stage_upload(
        [
            ("my-skill/SKILL.md", b"body"),
            ("my-skill/references/.DS_Store", b"junk"),
            # Sitting NEXT to the wrapper, not inside it: keeps its own name.
            ("__MACOSX/._my-skill", b"junk"),
        ],
        tmp_path / "staged",
    )

    assert staged.stripped_root == "my-skill"
    assert staged.files == ("SKILL.md",)
    assert set(staged.ignored) == {"references/.DS_Store", "__MACOSX/._my-skill"}


def test_an_upload_of_only_clutter_is_refused(tmp_path: Path) -> None:
    with pytest.raises(uploads.UploadRejected):
        uploads.stage_upload([(".DS_Store", b"junk")], tmp_path / "staged")


def test_an_empty_upload_is_refused(tmp_path: Path) -> None:
    with pytest.raises(uploads.UploadRejected):
        uploads.stage_upload([], tmp_path / "staged")


def test_a_traversal_entry_rejects_the_whole_upload(tmp_path: Path) -> None:
    with pytest.raises(uploads.UploadRejected):
        uploads.stage_upload(
            [("SKILL.md", b"body"), ("../escaped.md", b"evil")],
            tmp_path / "staged",
        )
    assert not (tmp_path / "escaped.md").exists()


def test_an_oversized_file_is_refused(tmp_path: Path) -> None:
    oversized = b"x" * (uploads.MAX_UPLOAD_FILE_BYTES + 1)
    with pytest.raises(uploads.UploadRejected) as exc:
        uploads.stage_upload([("big.bin", oversized)], tmp_path / "staged")
    assert "10 MB" in exc.value.message


def test_too_many_files_are_refused(tmp_path: Path) -> None:
    entries = [(f"f{i}.txt", b"x") for i in range(uploads.MAX_UPLOAD_FILE_COUNT + 1)]
    with pytest.raises(uploads.UploadRejected) as exc:
        uploads.stage_upload(entries, tmp_path / "staged")
    assert str(uploads.MAX_UPLOAD_FILE_COUNT) in exc.value.message


# ----------------------------------------------------------------------
# Staging an archive
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["bundle.zip", "bundle.tar.gz", "bundle.tgz"])
def test_archive_names_are_recognised(name: str) -> None:
    assert uploads.is_archive_name(name)
    assert uploads.is_archive_name(name.upper())


def test_a_zip_is_expanded(tmp_path: Path) -> None:
    data = _zip_bytes({"my-skill/SKILL.md": b"body", "my-skill/notes.md": b"notes"})
    staged = uploads.stage_upload([("my-skill.zip", data)], tmp_path / "staged")

    assert staged.stripped_root == "my-skill"
    assert set(staged.files) == {"SKILL.md", "notes.md"}
    assert (staged.root / "SKILL.md").read_bytes() == b"body"


def test_a_targz_is_expanded(tmp_path: Path) -> None:
    data = _targz_bytes({"SKILL.md": b"body"})
    staged = uploads.stage_upload([("bundle.tar.gz", data)], tmp_path / "staged")
    assert staged.files == ("SKILL.md",)


def test_a_zip_entry_that_walks_out_is_refused(tmp_path: Path) -> None:
    data = _zip_bytes({"../escaped.md": b"evil"})
    with pytest.raises(uploads.UploadRejected):
        uploads.stage_upload([("evil.zip", data)], tmp_path / "staged")
    assert not (tmp_path / "escaped.md").exists()


def test_a_zip_bomb_is_refused_before_it_is_unpacked(tmp_path: Path) -> None:
    """Highly compressible data: small on the wire, refused on its headers."""
    data = _zip_bytes({"bomb.bin": b"\0" * (uploads.MAX_UPLOAD_FILE_BYTES + 1)})
    assert len(data) < uploads.MAX_UPLOAD_FILE_BYTES, "fixture must stay small"

    with pytest.raises(uploads.UploadRejected) as exc:
        uploads.stage_upload([("bomb.zip", data)], tmp_path / "staged")
    assert "10 MB" in exc.value.message
    assert not (tmp_path / "staged" / "bomb.bin").exists()


def test_a_broken_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(uploads.UploadRejected) as exc:
        uploads.stage_upload([("broken.zip", b"not a zip")], tmp_path / "staged")
    assert "readable ZIP" in exc.value.message


def test_an_archive_alongside_other_files_is_stored_as_a_file(tmp_path: Path) -> None:
    """Only a LONE archive is an archive; two files are just two files."""
    data = _zip_bytes({"inner.md": b"inner"})
    staged = uploads.stage_upload(
        [("bundle.zip", data), ("SKILL.md", b"body")],
        tmp_path / "staged",
    )
    assert set(staged.files) == {"bundle.zip", "SKILL.md"}
