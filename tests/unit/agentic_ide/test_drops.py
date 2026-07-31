"""Guards for files dropped or pasted onto a terminal pane.

The interesting cases are all about untrusted input and about not making a mess
of the user's repository: a dropped name can come from a web page (so it can
contain ``../`` or a Windows-reserved word), a second drop of the same
screenshot must not overwrite the first while the agent is still reading it,
and none of this may show up in the user's ``git status``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import drops


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x")
    return tmp_path


def test_a_dropped_file_lands_in_the_workspace(workspace: Path) -> None:
    stored = drops.store(workspace, [("shot.png", b"\x89PNG-data")])
    assert len(stored) == 1
    item = stored[0]
    assert item.relative_path.startswith(f"{drops.DROP_DIRNAME}/")
    assert item.relative_path.endswith("shot.png")
    # The path handed to the agent must actually resolve.
    assert (workspace / item.relative_path).read_bytes() == b"\x89PNG-data"


def test_the_drop_directory_hides_itself_from_git(workspace: Path) -> None:
    """A dropped screenshot must never appear in the user's `git status`."""
    drops.store(workspace, [("shot.png", b"data")])
    ignore = workspace / ".jarvis" / ".gitignore"
    assert ignore.is_file()
    assert ignore.read_text(encoding="utf-8").strip() == "*"


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("../../etc/passwd", ".."),
        ("..\\..\\windows\\system32\\cfg", ".."),
        ("/absolute/evil.png", "/"),
        ("C:\\Windows\\evil.png", ":"),
    ],
)
def test_a_hostile_name_cannot_escape_the_drop_directory(
    workspace: Path, raw: str, forbidden: str
) -> None:
    """A dropped name is untrusted input — it names a file, never a location."""
    stored = drops.store(workspace, [(raw, b"data")])
    written = Path(stored[0].absolute_path).resolve()
    assert written.parent == (workspace / drops.DROP_DIRNAME).resolve()
    assert forbidden not in Path(stored[0].relative_path).name


def test_windows_reserved_names_are_made_writable(workspace: Path) -> None:
    """`con.txt` cannot be created on Windows regardless of extension."""
    stored = drops.store(workspace, [("con.txt", b"data")])
    assert Path(stored[0].relative_path).name.lower() != "con.txt"
    assert Path(stored[0].absolute_path).is_file()


def test_two_drops_of_the_same_name_keep_both(workspace: Path) -> None:
    """The agent may still be reading the first one."""
    first = drops.store(workspace, [("shot.png", b"one")])
    second = drops.store(workspace, [("shot.png", b"two")])
    assert first[0].absolute_path != second[0].absolute_path
    assert Path(first[0].absolute_path).read_bytes() == b"one"
    assert Path(second[0].absolute_path).read_bytes() == b"two"


def test_oversized_drops_are_refused_with_a_readable_message(
    workspace: Path,
) -> None:
    big = b"x" * (drops.MAX_FILE_BYTES + 1)
    with pytest.raises(drops.DropError, match="too large"):
        drops.store(workspace, [("huge.bin", big)])

    with pytest.raises(drops.DropError, match="Too many files"):
        drops.store(workspace, [(f"f{i}.txt", b"x") for i in range(drops.MAX_FILES + 1)])

    with pytest.raises(drops.DropError, match="no file"):
        drops.store(workspace, [])


def test_empty_files_are_skipped_not_written(workspace: Path) -> None:
    stored = drops.store(workspace, [("empty.txt", b""), ("real.txt", b"x")])
    assert len(stored) == 1
    assert stored[0].name == "real.txt"


def test_a_file_already_in_the_workspace_needs_no_copy(workspace: Path) -> None:
    inside = workspace / "src" / "main.py"
    assert drops.within_workspace(str(inside), workspace) == "src/main.py"


def test_a_file_outside_the_workspace_is_recognised_as_outside(
    workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path.parent / "elsewhere.png"
    assert drops.within_workspace(str(outside), workspace) is None


@pytest.mark.parametrize(
    ("agent", "path", "expected"),
    [
        ("claude", ".jarvis/drops/shot.png", "@.jarvis/drops/shot.png"),
        ("codex", ".jarvis/drops/shot.png", '".jarvis/drops/shot.png"'),
        # A space would end an @reference early, so it is quoted plainly instead
        # of shipping a half-reference the agent resolves to the wrong file.
        ("claude", "my docs/shot.png", '"my docs/shot.png"'),
    ],
)
def test_reference_syntax_matches_the_agent(
    agent: str, path: str, expected: str
) -> None:
    assert drops.reference(path, agent=agent) == expected


def test_sweep_removes_only_stale_copies(workspace: Path) -> None:
    import os
    import time

    stored = drops.store(workspace, [("old.png", b"x"), ("new.png", b"y")])
    old = Path(stored[0].absolute_path)
    stale = time.time() - (drops.KEEP_SECONDS + 60)
    os.utime(old, (stale, stale))

    assert drops.sweep(workspace) == 1
    assert not old.exists()
    assert Path(stored[1].absolute_path).exists()


def test_sweep_on_a_folder_with_no_drops_is_harmless(tmp_path: Path) -> None:
    assert drops.sweep(tmp_path) == 0
